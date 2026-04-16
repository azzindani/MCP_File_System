"""Operation receipt log — append_receipt / read_receipt_log.

Receipt files are stored alongside the target file as
{filename}.mcp_receipt.json  (sibling, not hidden).
All functions silently drop errors — receipts are best-effort.
"""
import json
from datetime import UTC, datetime
from pathlib import Path


def _receipt_path(file_path: str) -> Path:
    p = Path(file_path)
    return p.with_name(p.name + ".mcp_receipt.json")


def append_receipt(
    file_path: str,
    tool: str,
    op: str,
    result: str,
    backup: str | None,
) -> None:
    """Append one operation record to the receipt log. Never raises."""
    try:
        rp = _receipt_path(file_path)
        history: list[dict] = []
        if rp.exists():
            try:
                history = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:
                history = []
        history.append(
            {
                "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tool": tool,
                "op": op,
                "result": result,
                "backup": backup,
            }
        )
        rp.write_text(
            json.dumps(history, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def read_receipt_log(file_path: str) -> list[dict]:
    """Return operation history list. Returns [] on any error."""
    try:
        rp = _receipt_path(file_path)
        if not rp.exists():
            return []
        return json.loads(rp.read_text(encoding="utf-8"))
    except Exception:
        return []
