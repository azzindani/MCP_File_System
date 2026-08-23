"""In-memory deletion confirmation token store.

Tokens expire after 300 seconds and are consumed on first use.
Server restart clears all pending tokens.
"""

import secrets
import time

_store: dict[str, dict] = {}
_EXPIRY_SECONDS = 300


def _paths_of(targets: list[dict]) -> frozenset[str]:
    return frozenset(str(t.get("path", "")) for t in targets)


def create_token(targets: list[dict]) -> tuple[str, list[str]]:
    """Generate token, store with expiry. Returns (token, superseded tokens).

    Asking again for the same targets replaces the pending request rather than
    adding a second one. A client whose delete_request timed out and re-sent it
    was handed a fresh token while the first stayed live for the full 300
    seconds -- two independent licences to delete one path, from one intent.
    Worse for the caller who then decides *not* to delete: abandoning the
    request left a usable token behind, so the confirmation gate was weaker than
    it looks.

    Superseding is scoped to the exact target set, so a pending request for a
    different path is untouched.
    """
    cleanup_expired()
    wanted = _paths_of(targets)
    superseded = [t for t, entry in list(_store.items()) if _paths_of(entry["targets"]) == wanted]
    for t in superseded:
        _store.pop(t, None)

    token = "del_" + secrets.token_hex(4)
    _store[token] = {
        "targets": targets,
        "expires_at": time.time() + _EXPIRY_SECONDS,
    }
    return token, superseded


def validate_token(token: str) -> dict | None:
    """Return token data if valid and unexpired. Consumes on use."""
    cleanup_expired()
    entry = _store.get(token)
    if entry is None:
        return None
    if time.time() > entry["expires_at"]:
        _store.pop(token, None)
        return None
    del _store[token]
    return entry


def cleanup_expired() -> None:
    """Remove expired tokens. Call on every fs_write invocation."""
    now = time.time()
    expired = [k for k, v in list(_store.items()) if now > v["expires_at"]]
    for k in expired:
        _store.pop(k, None)
