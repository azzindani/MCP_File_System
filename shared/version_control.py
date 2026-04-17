"""Snapshot / restore / list_versions — defense-in-depth for writes.

Snapshots go to ~/.mcp_versions/{stem}_{UTC_ts}{ext}.bak
snapshot() never raises — returns "" on any error.
All Path.home() calls are deferred to call time for test isolation.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path


def _versions_dir() -> Path:
    """Return snapshot dir. Called at runtime so monkeypatching works."""
    return Path.home() / ".mcp_versions"


def snapshot(file_path: str) -> str:
    """Copy file to ~/.mcp_versions backup. Returns backup path or ''."""
    try:
        src = Path(file_path)
        if not src.exists() or not src.is_file():
            return ""
        vdir = _versions_dir()
        vdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        backup_name = f"{src.stem}_{ts}{src.suffix}.bak"
        backup_path = vdir / backup_name
        shutil.copy2(src, backup_path)
        return str(backup_path)
    except Exception:
        return ""


def restore_version(file_path: str, timestamp: str) -> dict:
    """Restore a snapshot identified by its UTC timestamp string."""
    try:
        src = Path(file_path)
        pattern = f"{src.stem}_{timestamp}{src.suffix}.bak"
        vdir = _versions_dir()
        candidates = sorted(vdir.glob(pattern))
        if not candidates:
            return {
                "success": False,
                "error": f"No snapshot found matching timestamp '{timestamp}'",
                "hint": "Use fs_manage with action=versions to list available snapshots.",
            }
        backup = candidates[-1]
        shutil.copy2(backup, src)
        return {"success": True, "restored": str(src), "from_backup": str(backup)}
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "hint": "Check that the backup file exists and you have write access.",
        }


def list_versions(file_path: str) -> list[dict]:
    """Return sorted list of available snapshots for file_path."""
    try:
        src = Path(file_path)
        pattern = f"{src.stem}_*{src.suffix}.bak"
        vdir = _versions_dir()
        versions = []
        for bak in sorted(vdir.glob(pattern)):
            try:
                stat = bak.stat()
                versions.append(
                    {
                        "backup": str(bak),
                        "size_bytes": stat.st_size,
                        "created": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                    }
                )
            except OSError:
                continue
        return versions
    except Exception:
        return []
