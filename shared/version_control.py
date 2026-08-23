"""Snapshot / restore / list_versions — defense-in-depth for writes.

Snapshots go to `<the file's own directory>/.mcp_versions/{stem}_{UTC_ts}{ext}.bak`,
which is where the three sibling MCP_* servers put theirs. They used to go to
`~/.mcp_versions`, and that had two consequences worth writing down:

* In the deployed configuration only the shared exchange directory is mounted,
  so a snapshot written under the container's home was stranded there — gone on
  the next rebuild, exactly the way generated outputs were before they were
  moved to the shared directory. The safety net behind every destructive
  fs_write op could not survive a redeploy.
* `fs_manage action=versions` could not see a snapshot a sibling server had
  taken of the same file. A sweep pointed it at Ad_Data.csv with seven visible
  `.bak` files sitting in `/workspace/data/.mcp_versions/`, and got
  "Found 0 snapshot(s)" with a hint explaining that snapshots are created
  automatically on destructive writes.

Reading is deliberately more forgiving than writing: both locations are
searched, and both filename conventions are matched — this repo's
`{stem}_{ts}{ext}.bak` and the siblings' `{stem}_{ts}.bak` — so snapshots taken
before this change, or by another server, are still listed and still restorable.

snapshot() never raises — returns "" on any error.
All Path.home() calls are deferred to call time for test isolation.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

VERSIONS_DIRNAME = ".mcp_versions"

# A snapshot name is the stem, an underscore, then a UTC timestamp that always
# begins with a four-digit year. Globbing `{stem}_*` alone would let a snapshot
# of `Ad_Data_test.csv` answer a query about `Ad_Data.csv`.
_TS_GLOB = "[0-9][0-9][0-9][0-9]-*"


def _legacy_versions_dir() -> Path:
    """Where snapshots used to be written. Still read, never written."""
    return Path.home() / VERSIONS_DIRNAME


def _versions_dir_for(file_path: Path) -> Path:
    """Where a snapshot of this file belongs — beside the file, as siblings do."""
    return file_path.parent / VERSIONS_DIRNAME


def _search_dirs(src: Path) -> list[Path]:
    seen: list[Path] = []
    for candidate in (_versions_dir_for(src), _legacy_versions_dir()):
        if candidate not in seen:
            seen.append(candidate)
    return seen


def _legacy_is_unambiguous(src: Path) -> bool:
    """True when no other file beside this one shares its stem.

    `report_{ts}.bak` could be a snapshot of report.csv or of report.docx --
    the siblings' extension-less name says nothing about which. Reading it is
    only safe where there is nothing to confuse it with. Restoring a Word
    document over a dataset under success: true is what the extension-bearing
    name exists to prevent, and matching the old one unconditionally reopened
    the hole on the read side.
    """
    try:
        siblings = list(src.parent.iterdir())
    except OSError:
        return False
    return not any(p.is_file() and p.stem == src.stem and p.suffix != src.suffix for p in siblings)


def _patterns(src: Path, timestamp: str) -> list[str]:
    """This repo's naming, plus the siblings' where it cannot be ambiguous."""
    patterns = [f"{src.stem}_{timestamp}{src.suffix}.bak"]
    if _legacy_is_unambiguous(src):
        patterns.append(f"{src.stem}_{timestamp}.bak")
    return patterns


def _find_snapshots(src: Path, timestamp: str) -> list[Path]:
    hits: list[Path] = []
    for vdir in _search_dirs(src):
        for pattern in _patterns(src, timestamp):
            hits.extend(vdir.glob(pattern))
    # Oldest first, so callers taking the last element get the newest.
    return sorted(set(hits), key=lambda p: (p.stat().st_mtime if p.exists() else 0, str(p)))


def snapshot(file_path: str) -> str:
    """Copy file to its directory's .mcp_versions. Returns backup path or ''."""
    try:
        src = Path(file_path)
        if not src.exists() or not src.is_file():
            return ""
        vdir = _versions_dir_for(src)
        vdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        backup_path = vdir / f"{src.stem}_{ts}{src.suffix}.bak"
        # A second write inside the same second would otherwise overwrite the
        # snapshot taken by the first one.
        counter = 1
        while backup_path.exists():
            backup_path = vdir / f"{src.stem}_{ts}_{counter}{src.suffix}.bak"
            counter += 1
        shutil.copy2(src, backup_path)
        return str(backup_path)
    except Exception:
        return ""


def restore_version(file_path: str, timestamp: str) -> dict:
    """Restore a snapshot identified by its UTC timestamp string."""
    try:
        src = Path(file_path)
        candidates = _find_snapshots(src, timestamp)
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
        versions = []
        for bak in _find_snapshots(src, _TS_GLOB):
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


def carry_snapshots(src_path: str, dst_path: str) -> int:
    """Move a file's snapshots to follow it to a new name or directory.

    Snapshots are named from the file's stem and live in the .mcp_versions
    beside it, so a rename or a move left every one of them behind under the
    old name. `fs_manage action=versions` then reported zero versions for a
    file that had them a moment earlier, and reported success doing it -- the
    file's whole recovery history was detached with nothing to say so.

    Returns the number of snapshots carried across. Best-effort, like the rest
    of this module: a snapshot that cannot be moved is left where it is rather
    than failing the rename that triggered this.
    """
    moved = 0
    try:
        src = Path(src_path)
        dst = Path(dst_path)
        if src.stem == dst.stem and src.parent == dst.parent:
            return 0
        target_dir = _versions_dir_for(dst)
        for bak in _find_snapshots(src, _TS_GLOB):
            # `{stem}_{ts}{ext}.bak` and the legacy `{stem}_{ts}.bak` both keep
            # everything after the stem, so the tail transplants unchanged.
            tail = bak.name[len(src.stem) :]
            new = target_dir / f"{dst.stem}{tail}"
            if new.exists():
                continue
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(bak), new)
                moved += 1
            except OSError:
                continue
    except Exception:
        return moved
    return moved
