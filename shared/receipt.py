"""Operation receipt log — append_receipt / read_receipt_log.

Receipt files are stored alongside the target file as
{filename}.mcp_receipt.json  (sibling, not hidden).
All functions silently drop errors — receipts are best-effort.

**Why this file knows about a header.**

Every server in this fleet writes `{file}.mcp_receipt.json` beside the file it
touched, and the shared output directory means one CSV can be written by
MCP_Data_Analyst and then moved or renamed here. When MCP_Data_Analyst grew a
scope header at index 0 -- to answer a user review that read two entries after
twenty calls and concluded eighteen operations had vanished -- every other
reader in the fleet started returning that header as an entry. Reproduced
before fixing, between DA and MCP_Machine_Learning: DA writes one receipt, the
sibling reads two, and the extra has no `tool` field.

`carry_receipt` mattered more than the reader. Merging two v2 logs by
concatenating the lists produces a file with a header buried in the middle,
which is not a header and not an entry -- so the merge is header-aware and the
combined log has exactly one.

A v1 file (a bare list, no header) still reads exactly as it was written.
Existing receipts on disk do not get to become unreadable because the format
grew.

**Order.** Entries here are oldest-first, unlike DA and ML which reverse.
`fs_index action=receipt` takes `history[-n:]` to get the newest, so the two
agree about which end is recent; they disagree only about which end is printed
first. Left as it is deliberately: changing it silently reverses every existing
caller's output, and the ordering is not what any review complained about.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# What the log holds. Identical wording to the siblings: they write the same
# file, and a caller reading the scope should not be able to tell which server
# wrote it.
RECEIPT_SCOPE = (
    "mutations only: operations that wrote to this file. Reads, inspections, "
    "correlations and chart generation are not recorded here."
)

# Above this, a content hash costs more than the operation it describes.
_MAX_HASH_BYTES = 64 * 1024 * 1024


def _receipt_path(file_path: str) -> Path:
    p = Path(file_path)
    return p.with_name(p.name + ".mcp_receipt.json")


def fingerprint(file_path: str | Path) -> str:
    """Identify a file's contents, or say honestly that this is not a hash.

    Returns `sha256:<16 hex>` for a file small enough to read, and
    `size-mtime:<...>` for one that is not. The prefix is the point: a caller
    comparing two fingerprints must be able to tell a content hash from a
    cheaper stand-in, because only one of them proves the bytes are the same.
    """
    p = Path(file_path)
    try:
        stat = p.stat()
    except OSError:
        return ""
    if stat.st_size > _MAX_HASH_BYTES:
        return f"size-mtime:{stat.st_size}-{int(stat.st_mtime)}"
    try:
        digest = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return f"size-mtime:{stat.st_size}-{int(stat.st_mtime)}"
    return f"sha256:{digest}"


def _split_header(loaded: Any) -> tuple[list[dict], dict | None]:
    """Separate the scope header from the entries, for either file format."""
    if isinstance(loaded, dict):
        # MCP_Microsoft_Office wrote `{"file": ..., "entries": [...]}` until the
        # formats were converged. Files in that shape still exist on disk, and
        # every reader in the fleet returned [] for them.
        entries = loaded.get("entries", [])
        return [e for e in entries if isinstance(e, dict)], None
    if not isinstance(loaded, list) or not loaded:
        return [], None
    first = loaded[0]
    if isinstance(first, dict) and "_scope" in first:
        return [e for e in loaded[1:] if isinstance(e, dict)], first
    return [e for e in loaded if isinstance(e, dict)], None


def _load(rp: Path) -> tuple[list[dict], dict | None]:
    try:
        return _split_header(json.loads(rp.read_text(encoding="utf-8")))
    except Exception:
        return [], None


def append_receipt(
    file_path: str,
    tool: str,
    op: str,
    result: str,
    backup: str | None,
    input_fingerprint: str = "",
    duration_ms: float | None = None,
) -> None:
    """Append one operation record to the receipt log. Never raises.

    `input_fingerprint` is what `fingerprint()` returned BEFORE the write; the
    output side is measured here, after it. Pass it and the entry says what the
    operation turned into what. Omit it and the entry is still valid -- one side
    of a lineage is better than none, and no call site is obliged to change.
    """
    try:
        rp = _receipt_path(file_path)
        history, header = _load(rp) if rp.exists() else ([], None)
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool": tool,
            "op": op,
            "result": result,
            "backup": backup,
        }
        if input_fingerprint:
            entry["input"] = input_fingerprint
        after = fingerprint(file_path)
        if after:
            entry["output"] = after
        if duration_ms is not None:
            entry["duration_ms"] = round(float(duration_ms), 1)
        history.append(entry)
        head = header or {"_scope": RECEIPT_SCOPE, "_format": 2}
        rp.write_text(
            json.dumps([head, *history], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def read_receipt_log(file_path: str) -> list[dict]:
    """Return operation history list, oldest first. Returns [] on any error."""
    entries, _ = read_receipt(file_path)
    return entries


def read_receipt(file_path: str) -> tuple[list[dict], str]:
    """Entries oldest first, and the scope sentence that belongs beside them.

    Two return values rather than one because the count alone is what misled a
    caller: twenty operations, two entries, and no way to learn from the log
    that eighteen of them were never eligible for it.
    """
    try:
        rp = _receipt_path(file_path)
        if not rp.exists():
            return [], RECEIPT_SCOPE
    except Exception:
        return [], RECEIPT_SCOPE
    entries, header = _load(rp)
    return entries, str(header.get("_scope")) if header else RECEIPT_SCOPE


def carry_receipt(src_path: str, dst_path: str) -> bool:
    """Move a file's receipt log to follow it to a new name or directory.

    The log is a sibling named after the file, so a rename or a move orphaned
    it under the old name: the destination started a fresh history whose first
    and only entry was the move itself, and everything the file had recorded
    before that became unreachable. Best-effort, and never raises.

    The merge is header-aware. Concatenating two v2 logs as raw lists buries a
    header in the middle of the entries, where it is neither a header nor an
    entry, and every reader downstream then reports it as an operation that
    never happened.
    """
    try:
        old = _receipt_path(src_path)
        if not old.exists():
            return False
        new = _receipt_path(dst_path)
        if new.exists():
            # A destination with its own history keeps it; merge oldest-first
            # so the combined log still reads in chronological order.
            old_entries, old_header = _load(old)
            new_entries, new_header = _load(new)
            head = old_header or new_header or {"_scope": RECEIPT_SCOPE, "_format": 2}
            merged = [head, *old_entries, *new_entries]
            new.write_text(json.dumps(merged, indent=2), encoding="utf-8")
            old.unlink()
            return True
        old.rename(new)
        return True
    except Exception:
        return False
