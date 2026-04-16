"""fs_query implementation — LOCATE files by name or content."""
import fnmatch
import json
import mimetypes
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from _basic_helpers import (
    _error,
    get_content_backend,
    get_max_context_lines,
    get_max_results,
    get_name_backend,
    info,
    ok,
    resolve_path,
)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_fs_query(
    pattern: str,
    path: str = "",
    type_: str = "any",
    content: str = "",
    grep_mode: bool = False,
    context_lines: int = 0,
    include_meta: bool = False,
    follow_symlinks: bool = False,
    max_results: int = 50,
) -> dict:
    try:
        return _fs_query(
            pattern, path, type_, content, grep_mode,
            context_lines, include_meta, follow_symlinks, max_results,
        )
    except ValueError as e:
        return _error(
            "fs_query", str(e),
            "Ensure path is absolute and within your home directory.",
        )
    except PermissionError as e:
        return _error(
            "fs_query", f"Permission denied: {e}",
            "Check directory permissions or choose a path you own.",
        )
    except Exception as e:
        return _error(
            "fs_query", str(e),
            "Use fs_query with a simpler pattern to narrow the scope.",
        )


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _fs_query(
    pattern: str,
    path: str,
    type_: str,
    content: str,
    grep_mode: bool,
    context_lines: int,
    include_meta: bool,
    follow_symlinks: bool,
    max_results: int,
) -> dict:
    progress = []

    # --- input validation ---
    if not pattern:
        return _error("fs_query", "pattern must not be empty",
                      "Provide a glob pattern such as '*.py' or 'report_*'.")
    if type_ not in ("file", "dir", "any"):
        return _error("fs_query", f"type_ must be 'file', 'dir', or 'any', got '{type_}'",
                      "Use one of: file, dir, any.")
    context_lines = max(0, min(context_lines, get_max_context_lines()))

    # --- resolve root ---
    root_str = path or str(Path.home())
    root = resolve_path(root_str)
    if not root.exists():
        return _error("fs_query", f"Search root does not exist: {root.name}",
                      "Use fs_read with mode=tree to inspect the directory structure.")
    if not root.is_dir():
        return _error("fs_query", f"Search root is not a directory: {root.name}",
                      "Provide a directory path for the 'path' parameter.")

    # --- respect constrained mode ---
    effective_max = min(max_results, get_max_results())

    progress.append(info(f"Searching {root.name}", f"pattern={pattern}"))

    # --- name search ---
    name_matches: list[Path] = _name_search(
        root, pattern, type_, follow_symlinks, effective_max * 10  # gather extra to filter
    )
    backend = get_name_backend()

    # --- content filter ---
    if content:
        is_regex = _looks_like_regex(content)
        if grep_mode:
            cb = get_content_backend()
            return _build_grep_response(
                name_matches, content, is_regex, context_lines,
                effective_max, include_meta, root, pattern, backend, cb, progress,
            )
        else:
            name_matches = [
                p for p in name_matches
                if p.is_file() and _file_contains(p, content, is_regex)
            ]

    # --- truncate ---
    total_found = len(name_matches)
    truncated = total_found > effective_max
    matches = name_matches[:effective_max]

    # --- build match entries ---
    if include_meta:
        match_entries: list = [_with_meta(p) for p in matches]
    else:
        match_entries = [str(p) for p in matches]

    progress.append(ok(f"Found {total_found} match(es)", f"returned {len(matches)}"))

    result: dict = {
        "success": True,
        "op": "fs_query",
        "pattern": pattern,
        "root": str(root),
        "matches": match_entries,
        "returned": len(matches),
        "total_found": total_found,
        "truncated": truncated,
        "backend_used": backend,
        "progress": progress,
    }
    if truncated:
        result["hint"] = (
            f"Use fs_query with a narrower pattern or increase max_results "
            f"(current: {effective_max})."
        )
    result["token_estimate"] = len(str(result)) // 4
    return result


# ---------------------------------------------------------------------------
# Name search
# ---------------------------------------------------------------------------

def _name_search(
    root: Path,
    pattern: str,
    type_: str,
    follow_symlinks: bool,
    limit: int,
) -> list[Path]:
    matches: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(
            root, followlinks=follow_symlinks, onerror=None
        ):
            dp = Path(dirpath)
            if type_ in ("dir", "any"):
                for d in dirnames:
                    if fnmatch.fnmatch(d, pattern):
                        matches.append(dp / d)
                        if len(matches) >= limit:
                            return matches
            if type_ in ("file", "any"):
                for f in filenames:
                    if fnmatch.fnmatch(f, pattern):
                        matches.append(dp / f)
                        if len(matches) >= limit:
                            return matches
    except PermissionError:
        pass
    return matches


# ---------------------------------------------------------------------------
# Content search helpers
# ---------------------------------------------------------------------------

def _looks_like_regex(pattern: str) -> bool:
    """Heuristic: if pattern contains regex metacharacters, treat as regex."""
    return bool(re.search(r"[\\.*+?^${}()\[\]|]", pattern))


def _file_contains(file_path: Path, pattern: str, is_regex: bool) -> bool:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        if is_regex:
            return bool(re.search(pattern, text))
        return pattern in text
    except Exception:
        return False


def _python_grep(
    file_path: Path, pattern: str, context_lines: int, is_regex: bool
) -> list[dict]:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        compiled = re.compile(pattern) if is_regex else None
        hits: list[dict] = []
        for i, line in enumerate(lines):
            matched = bool(compiled.search(line)) if compiled else (pattern in line)
            if matched:
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                hits.append(
                    {
                        "line": i + 1,
                        "text": line,
                        "context_before": lines[start:i],
                        "context_after": lines[i + 1 : end],
                    }
                )
        return hits
    except Exception:
        return []


def _rg_grep(
    root: Path,
    pattern: str,
    context_lines: int,
    is_regex: bool,
    name_matches: list[Path],
) -> dict[str, list[dict]]:
    """Run ripgrep in JSON mode; return {path: [hit, ...]} mapping."""
    args = ["rg", "--json"]
    if context_lines > 0:
        args.extend(["--context", str(context_lines)])
    if not is_regex:
        args.extend(["--fixed-strings"])
    args.append("--")
    args.append(pattern)
    # Limit search to name-matched files
    for p in name_matches[:500]:
        if p.is_file():
            args.append(str(p))

    try:
        proc = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            timeout=30,
            text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}

    result: dict[str, list[dict]] = {}
    pending: dict[str, dict] = {}  # path → {"match": ..., "before": [...]}

    for raw_line in proc.stdout.splitlines():
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        mtype = msg.get("type")
        data = msg.get("data", {})
        path_text = (data.get("path") or {}).get("text", "")
        line_num = data.get("line_number")
        line_text = ((data.get("lines") or {}).get("text") or "").rstrip("\n")

        if mtype == "match":
            result.setdefault(path_text, [])
            entry: dict = {
                "line": line_num,
                "text": line_text,
                "context_before": [],
                "context_after": [],
            }
            pending[path_text] = {"entry": entry, "idx": len(result[path_text])}
            result[path_text].append(entry)
        elif mtype == "context":
            # assign to nearest pending match
            for ppath, pdata in list(pending.items()):
                if ppath == path_text and line_num is not None:
                    entry = result[path_text][pdata["idx"]]
                    match_ln = entry["line"]
                    if match_ln is not None and line_num < match_ln:
                        entry["context_before"].append(line_text)
                    else:
                        entry["context_after"].append(line_text)
                    break

    return result


def _build_grep_response(
    name_matches: list[Path],
    content: str,
    is_regex: bool,
    context_lines: int,
    effective_max: int,
    include_meta: bool,
    root: Path,
    pattern: str,
    name_backend: str,
    content_backend: str,
    progress: list,
) -> dict:
    """Build grep_mode=True response."""
    if content_backend == "ripgrep":
        rg_results = _rg_grep(root, content, context_lines, is_regex, name_matches)
        matches_out: list[dict] = []
        for file_path, hits in rg_results.items():
            if hits:
                entry: dict = {"path": file_path, "hits": hits}
                if include_meta:
                    try:
                        entry.update(_with_meta(Path(file_path)))
                    except Exception:
                        pass
                matches_out.append(entry)
                if len(matches_out) >= effective_max:
                    break
    else:
        content_backend = "python"
        matches_out = []
        for file_path in name_matches:
            if not file_path.is_file():
                continue
            hits = _python_grep(file_path, content, context_lines, is_regex)
            if hits:
                entry = {"path": str(file_path), "hits": hits}
                if include_meta:
                    try:
                        entry.update(_with_meta(file_path))
                    except Exception:
                        pass
                matches_out.append(entry)
                if len(matches_out) >= effective_max:
                    break

    total = len(matches_out)
    truncated = total >= effective_max
    progress.append(ok(f"grep found {total} file(s) with matches"))

    result: dict = {
        "success": True,
        "op": "fs_query",
        "grep_mode": True,
        "pattern": pattern,
        "root": str(root),
        "content_pattern": content,
        "matches": matches_out,
        "returned": total,
        "truncated": truncated,
        "backend_used": content_backend,
        "progress": progress,
    }
    if truncated:
        result["hint"] = (
            f"Results capped at {effective_max}. "
            "Narrow the content pattern or directory to see all matches."
        )
    result["token_estimate"] = len(str(result)) // 4
    return result


# ---------------------------------------------------------------------------
# Metadata helper
# ---------------------------------------------------------------------------

def _with_meta(p: Path) -> dict:
    try:
        st = p.stat()
        mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        return {
            "path": str(p),
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
            "mime": mime,
        }
    except OSError:
        return {"path": str(p)}
