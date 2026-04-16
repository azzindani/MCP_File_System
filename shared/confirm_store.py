"""In-memory deletion confirmation token store.

Tokens expire after 300 seconds and are consumed on first use.
Server restart clears all pending tokens.
"""
import secrets
import time

_store: dict[str, dict] = {}
_EXPIRY_SECONDS = 300


def create_token(targets: list[dict]) -> str:
    """Generate token, store with expiry. Returns token string."""
    token = "del_" + secrets.token_hex(4)
    _store[token] = {
        "targets": targets,
        "expires_at": time.time() + _EXPIRY_SECONDS,
    }
    return token


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
