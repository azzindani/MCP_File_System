"""fs_read implementation — INSPECT files, trees, metadata, diffs."""
import difflib
import mimetypes
import stat as stat_mod
from datetime import UTC, datetime
from pathlib import Path

from _basic_helpers import (
    _error,
    get_max_depth,
    get_max_lines,
    get_max_tree_entries,
    info,
    ok,
    resolve_path,
)

_BINARY_CHUNK = 8192


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_fs_read(
    path: str,
    mode: str = "auto",
    start_line: int = 0,
    end_line: int = 100,
    depth: int = 2,
    compare_to: str = "",
    changed_since: str = "",
) -> dict:
    try:
        return _fs_read(path, mode, start_line, end_line, depth, compare_to, changed_since)
    except ValueError as e:
        return _error("fs_read", str(e),
                      "Ensure path is absolute and within your home directory.")
    except FileNotFoundError:
        return _error("fs_read", f"Path does not exist: {Path(path).name}",
                      "Use fs_query to locate the file first.")
    except PermissionError:
        return _error("fs_read", f"Permission denied: {Path(path).name}",
                      "Check permissions or choose a path you own.")
    except Exception as e:
        return _error("fs_read", str(e),
                      "Use fs_read with a specific mode to narrow the operation.")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _fs_read(
    path: str,
    mode: str,
    start_line: int,
    end_line: int,
    depth: int,
    compare_to: str,
    changed_since: str,
) -> dict:
    resolved = resolve_path(path, must_exist=True)

    # Auto-detect mode
    if mode == "auto":
        mode = "content" if resolved.is_file() else "tree"

    if mode not in ("content", "tree", "meta", "diff"):
        return _error("fs_read", f"Unknown mode '{mode}'",
                      "Use one of: content, tree, meta, diff, auto.")

    if mode == "content":
        return _read_content(resolved, start_line, end_line)
    if mode == "tree":
        return _read_tree(resolved, depth)
    if mode == "meta":
        return _read_meta(resolved, changed_since)
    # diff
    return _read_diff(resolved, compare_to)


# ---------------------------------------------------------------------------
# content mode
# ---------------------------------------------------------------------------

def _is_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:_BINARY_CHUNK]
        return b"\x00" in chunk
    except Exception:
        return False


def _read_content(path: Path, start_line: int, end_line: int) -> dict:
    if not path.is_file():
        return _error("fs_read", f"Not a file: {path.name}",
                      "Use mode=tree for directories.")

    if _is_binary(path):
        hex_preview = path.read_bytes()[:32].hex()
        st = path.stat()
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        result = {
            "success": True,
            "op": "fs_read",
            "path": str(path),
            "mode": "content",
            "binary": True,
            "size": st.st_size,
            "mime": mime,
            "hex_preview": hex_preview,
            "progress": [info(f"Binary file: {path.name}")],
        }
        result["token_estimate"] = len(str(result)) // 4
        return result

    max_lines = get_max_lines()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _error("fs_read", f"Cannot read file: {e}",
                      "Check file encoding or permissions.")
    all_lines = text.splitlines(keepends=True)
    total_lines = len(all_lines)

    # Clamp to valid range and max_lines
    s = max(0, start_line)
    e = min(end_line, s + max_lines, total_lines)
    sliced = all_lines[s:e]
    truncated = e < total_lines or s > 0 and end_line > s + max_lines

    result: dict = {
        "success": True,
        "op": "fs_read",
        "path": str(path),
        "mode": "content",
        "lines": sliced,
        "start_line": s,
        "end_line": e,
        "total_lines": total_lines,
        "truncated": truncated,
        "progress": [ok(f"Read {len(sliced)} lines from {path.name}")],
    }
    if truncated:
        result["hint"] = (
            f"Use fs_read with start_line/end_line to read other ranges "
            f"(total_lines={total_lines})."
        )
    result["token_estimate"] = len(str(result)) // 4
    return result


# ---------------------------------------------------------------------------
# tree mode
# ---------------------------------------------------------------------------

def _read_tree(path: Path, depth: int) -> dict:
    if not path.is_dir():
        return _error("fs_read", f"Not a directory: {path.name}",
                      "Use mode=content to read a file.")

    max_depth = max(1, min(depth, get_max_depth()))
    max_entries = get_max_tree_entries()
    entries: list[dict] = []
    truncated = False
    _collect_tree(path, path, 0, max_depth, entries, max_entries)
    truncated = len(entries) >= max_entries

    result: dict = {
        "success": True,
        "op": "fs_read",
        "path": str(path),
        "mode": "tree",
        "entries": entries,
        "returned": len(entries),
        "truncated": truncated,
        "progress": [ok(f"Tree of {path.name}", f"{len(entries)} entries")],
    }
    if truncated:
        result["hint"] = (
            f"Tree truncated at {max_entries} entries. "
            "Use fs_read with a deeper path or smaller depth."
        )
    result["token_estimate"] = len(str(result)) // 4
    return result


def _collect_tree(
    base: Path,
    current: Path,
    current_depth: int,
    max_depth: int,
    entries: list[dict],
    max_entries: int,
) -> None:
    if len(entries) >= max_entries:
        return
    try:
        for child in sorted(current.iterdir()):
            if len(entries) >= max_entries:
                break
            rel = str(child.relative_to(base))
            entry: dict = {
                "path": rel,
                "type": "dir" if child.is_dir() else "file",
                "depth": current_depth + 1,
            }
            try:
                st = child.stat()
                entry["size"] = st.st_size
            except OSError:
                pass
            entries.append(entry)
            if child.is_dir() and current_depth + 1 < max_depth:
                _collect_tree(base, child, current_depth + 1, max_depth, entries, max_entries)
    except PermissionError:
        pass


# ---------------------------------------------------------------------------
# meta mode
# ---------------------------------------------------------------------------

def _read_meta(path: Path, changed_since: str) -> dict:
    try:
        lstat = path.lstat()
    except OSError as e:
        return _error("fs_read", str(e), "Check that the file exists.")

    is_symlink = path.is_symlink()
    symlink_target = str(path.readlink()) if is_symlink else None
    is_broken = is_symlink and not path.exists()

    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    perms = stat_mod.filemode(lstat.st_mode)
    mtime_dt = datetime.fromtimestamp(lstat.st_mtime, tz=UTC)

    result: dict = {
        "success": True,
        "op": "fs_read",
        "path": str(path),
        "mode": "meta",
        "size": lstat.st_size,
        "mtime": mtime_dt.isoformat(),
        "permissions": perms,
        "mime": mime,
        "is_symlink": is_symlink,
        "symlink_target": symlink_target,
        "is_broken_symlink": is_broken,
        "progress": [ok(f"Metadata for {path.name}")],
    }

    if changed_since:
        try:
            since_dt = datetime.fromisoformat(changed_since.replace("Z", "+00:00"))
            result["changed"] = mtime_dt > since_dt
            result["changed_since"] = changed_since
        except ValueError:
            result["changed_since_error"] = (
                f"Cannot parse timestamp '{changed_since}'; use ISO 8601 format."
            )

    result["token_estimate"] = len(str(result)) // 4
    return result


# ---------------------------------------------------------------------------
# diff mode
# ---------------------------------------------------------------------------

def _read_diff(path: Path, compare_to: str) -> dict:
    if not compare_to:
        return _error("fs_read", "diff mode requires 'compare_to' parameter",
                      "Provide a file path or snapshot timestamp in compare_to.")

    # Resolve compare_to: could be a file path or a snapshot timestamp
    cmp_path: Path | None = None
    try:
        cmp_resolved = resolve_path(compare_to)
        if cmp_resolved.exists():
            cmp_path = cmp_resolved
    except ValueError:
        pass

    if cmp_path is None:
        # Treat as snapshot timestamp pattern
        vdir = Path.home() / ".mcp_versions"
        pattern = f"{path.stem}_{compare_to}{path.suffix}.bak"
        candidates = sorted(vdir.glob(pattern))
        if not candidates:
            return _error(
                "fs_read",
                f"No snapshot found for timestamp '{compare_to}'",
                "Use fs_manage with action=versions to list snapshots.",
            )
        cmp_path = candidates[-1]

    try:
        a_lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        b_lines = cmp_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as e:
        return _error("fs_read", f"Cannot read files for diff: {e}",
                      "Ensure both files are readable text files.")

    diff = list(
        difflib.unified_diff(
            b_lines, a_lines,
            fromfile=str(cmp_path.name),
            tofile=str(path.name),
        )
    )

    result: dict = {
        "success": True,
        "op": "fs_read",
        "path": str(path),
        "mode": "diff",
        "compare_to": str(cmp_path),
        "diff": diff,
        "changed": len(diff) > 0,
        "progress": [ok(f"Diff {path.name} vs {cmp_path.name}")],
    }
    result["token_estimate"] = len(str(result)) // 4
    return result
