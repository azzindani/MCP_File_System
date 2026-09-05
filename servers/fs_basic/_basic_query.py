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
    get_max_grep_hits,
    get_max_results,
    get_max_scan_files,
    get_name_backend,
    info,
    ok,
    resolve_path,
    warn,
)

from shared.counts import counted

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_fs_query(
    pattern: str = "",
    path: str = "",
    type_: str = "any",
    content: str = "",
    grep_mode: bool = False,
    context_lines: int = 0,
    include_meta: bool = False,
    follow_symlinks: bool = False,
    max_results: int = 50,
    regex: bool = False,
) -> dict:
    try:
        return _fs_query(
            pattern,
            path,
            type_,
            content,
            grep_mode,
            context_lines,
            include_meta,
            follow_symlinks,
            max_results,
            regex,
        )
    except ValueError as e:
        return _error(
            "fs_query",
            str(e),
            "Ensure path is absolute and within your home directory.",
        )
    except PermissionError as e:
        return _error(
            "fs_query",
            f"Permission denied: {e}",
            "Check directory permissions or choose a path you own.",
        )
    except Exception as e:
        return _error(
            "fs_query",
            str(e),
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
    regex: bool = False,
) -> dict:
    progress = []

    # --- input validation ---
    # The docstring offers name *or* content search, so a content-only call is a
    # documented call: content="needle", grep_mode=True, no pattern. It used to
    # be refused by the schema before reaching any of this, as a raw pydantic
    # "pattern Field required" naming an argument the caller had deliberately
    # left out. Searching every name is the right default once the caller has
    # said what they want found inside.
    if not pattern and content:
        pattern = "*"
    if not pattern:
        return _error(
            "fs_query",
            "give a name pattern, a content string, or both",
            "Use pattern='*.py' to search by name, or content='needle' to search inside files.",
        )
    _type_aliases = {
        "directory": "dir",
        "folder": "dir",
        "folders": "dir",
        "dirs": "dir",
        "files": "file",
    }
    type_ = _type_aliases.get(type_, type_)
    if type_ not in ("file", "dir", "any"):
        return _error(
            "fs_query",
            f"type_ must be 'file', 'dir', or 'any', got '{type_}'",
            "Use one of: file, dir, any.",
        )
    context_lines = max(0, min(context_lines, get_max_context_lines()))

    # --- resolve root ---
    root_str = path or str(Path.home())
    root = resolve_path(root_str)
    if not root.exists():
        return _error(
            "fs_query",
            f"Search root does not exist: {root.name}",
            "Use fs_read with mode=tree to inspect the directory structure.",
        )
    if not root.is_dir():
        return _error(
            "fs_query",
            f"Search root is not a directory: {root.name}",
            "Provide a directory path for the 'path' parameter.",
        )

    # --- respect constrained mode ---
    effective_max = min(max_results, get_max_results())

    progress.append(info(f"Searching {root.name}", f"pattern={pattern}"))

    # --- name search ---
    # The scan budget is its OWN limit, for every search. It used to be
    # `max_results * 10`, which gathered 500 paths for a 50-result request,
    # filtered those by content, and reported the survivors as `total_found` --
    # 97 against grep's 489 over the same tree.
    #
    # The first fix left `else effective_max * 10` here for the name-only path,
    # on the grounds that name matching had always been exact. It is exact only
    # while the multiplier happens to exceed the tree: under
    # MCP_CONSTRAINED_MODE=1 get_max_results() is 10, so a name search walked
    # 100 paths and reported `total_found: 100` for a directory of 120 files --
    # the same defect, in the same field, one branch over. get_max_scan_files()
    # says in its own docstring that a results cap must not decide how much of
    # the tree is searched; this line was the last place still doing it.
    #
    # Caught by CI's constrained job, which exists because a sibling repo set
    # the flag on its only matrix and so never tested the other branch.
    scan_limit = get_max_scan_files()
    name_matches, scan_complete = _name_search(
        root,
        pattern,
        type_,
        follow_symlinks,
        scan_limit,
    )
    backend = get_name_backend()

    # --- content filter ---
    if content:
        is_regex = regex
        if is_regex:
            # Compile once, here, so an unusable pattern is an error rather than
            # a silent zero. Both matchers wrap their body in `except Exception`
            # and return "no match", so `content="foo(bar"` used to search every
            # file, fail to compile in each one, and report success with nothing
            # found -- indistinguishable from a needle that is genuinely absent.
            try:
                re.compile(content)
            except re.error as exc:
                return _error(
                    "fs_query",
                    f"content is not a valid regular expression: {exc}",
                    "Drop regex=True to search for it as literal text, or fix the pattern.",
                )
        if grep_mode:
            cb = get_content_backend()
            return _build_grep_response(
                name_matches,
                content,
                is_regex,
                context_lines,
                effective_max,
                include_meta,
                root,
                pattern,
                backend,
                cb,
                progress,
                scan_complete,
                scan_limit,
            )
        else:
            name_matches = [
                p for p in name_matches if p.is_file() and _file_contains(p, content, is_regex)
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
        "total_found": total_found,
        # `truncated` has only ever meant "more matched than were returned".
        # It says nothing about whether the search LOOKED everywhere, and the
        # two were being read as one: a caller seeing total_found 97 with
        # truncated true concluded there were exactly 97 matches, when the
        # walk had stopped after 500 of 1,843 files. `scan_complete` answers the
        # second question, and an incomplete walk makes the total a floor --
        # which `counted()` marks in the same response that carries it.
        **counted(len(matches), total_found, exact=scan_complete),
        "scan_complete": scan_complete,
        "backend_used": backend,
        "progress": progress,
    }
    if not scan_complete:
        # total_found is a LOWER BOUND here, and must say so in the same
        # response that carries it rather than in documentation nobody reads.
        result["total_found_is_lower_bound"] = True
        result["files_scanned"] = scan_limit
        progress.append(
            warn(
                f"Stopped after scanning {scan_limit} path(s)",
                "total_found counts only what was scanned",
            )
        )
    if content:
        result["content_is_regex"] = is_regex
    if not scan_complete:
        result["hint"] = (
            f"The search stopped after {scan_limit} path(s), so total_found is a lower bound. "
            f"Search a subdirectory, narrow `pattern` so fewer files are read, "
            f"or raise MCP_MAX_SCAN_FILES."
        )
    elif truncated:
        result["hint"] = (
            f"Use fs_query with a narrower pattern or increase max_results "
            f"(current: {effective_max})."
        )
    elif not total_found:
        # `pattern` is a glob matched against the whole filename, so the substring
        # a caller naturally reaches for ("report") matches nothing while the file
        # sits right there. Zero matches plus success:true is a silent dead end.
        if not any(ch in pattern for ch in "*?["):
            result["hint"] = (
                f"No file is named exactly '{pattern}'. pattern is a glob, not a substring — "
                f"try '*{pattern}*' to match anywhere in the name."
            )
        elif content and not is_regex and _looks_like_regex(content):
            # The needle holds characters a pattern would read specially, and it
            # was searched for literally. Say which way it was read, because the
            # opposite reading is the one that used to happen silently.
            result["hint"] = (
                f"'{content}' was searched for as literal text. If it was meant as a "
                "pattern, pass regex=True; otherwise search without the content filter "
                "first to confirm what is there."
            )
        elif content:
            result["hint"] = (
                f"Files matching '{pattern}' exist only if none contain '{content}'. "
                "Search without the content filter first to confirm what is there."
            )
        else:
            result["hint"] = (
                f"Nothing under {root} matches '{pattern}'. Use fs_index to list what is there."
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
) -> tuple[list[Path], bool]:
    """Matching paths, and whether the whole tree was walked.

    The second value is the point. This used to return the list alone, so a
    walk that stopped at its cap was indistinguishable from one that finished
    -- and every count derived from it was reported as exact.
    """
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
                            return matches, False
            if type_ in ("file", "any"):
                for f in filenames:
                    if fnmatch.fnmatch(f, pattern):
                        matches.append(dp / f)
                        if len(matches) >= limit:
                            return matches, False
    except PermissionError:
        pass
    return matches, True


# ---------------------------------------------------------------------------
# Content search helpers
# ---------------------------------------------------------------------------


_METACHARACTERS = re.compile(r"[\\.*+?^${}()\[\]|]")


def _looks_like_regex(pattern: str) -> bool:
    """Whether this needle contains characters a regex would read specially.

    This used to *decide* whether `content` was a regex, and that question
    cannot be answered from the string. "Desktop,99+,7.77" is a literal line out
    of a CSV; read as a pattern, `9+` means "one or more nines" and the search
    returned nothing, under success:true, for text that was demonstrably in the
    file. Every needle holding a `.` -- a filename, a version, a number -- was
    silently a pattern too, matching more than asked rather than less.

    The caller is the only one who knows which they meant, and `fs_query` had no
    parameter for them to say. Now it does, and this is only used to explain an
    empty result: if a literal search found nothing and the needle reads like a
    pattern, that is worth saying once rather than leaving the caller to wonder.
    """
    return bool(_METACHARACTERS.search(pattern))


def _file_contains(file_path: Path, pattern: str, is_regex: bool) -> bool:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        if is_regex:
            return bool(re.search(pattern, text))
        return pattern in text
    except Exception:
        return False


def _python_grep(file_path: Path, pattern: str, context_lines: int, is_regex: bool) -> list[dict]:
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
    except FileNotFoundError, subprocess.TimeoutExpired:
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
    scan_complete: bool = True,
    scan_limit: int = 0,
) -> dict:
    """Build grep_mode=True response.

    Takes scan_complete for the same reason the plain path reports it: this
    branch counts hits across the files the walk happened to reach, and a walk
    that stopped early makes every count here a lower bound.
    """
    if content_backend == "ripgrep":
        rg_results = _rg_grep(root, content, context_lines, is_regex, name_matches)
        all_entries: list[dict] = []
        for file_path, hits in rg_results.items():
            if hits:
                entry: dict = {"path": file_path, "hits": hits}
                if include_meta:
                    try:
                        entry.update(_with_meta(Path(file_path)))
                    except Exception:
                        pass
                all_entries.append(entry)
        truncated = len(all_entries) > effective_max
        # Every candidate was processed here, so this count is exact.
        files_matched, files_exact = len(all_entries), True
        matches_out = all_entries[:effective_max]
    else:
        content_backend = "python"
        matches_out = []
        truncated = False
        files_matched, files_exact = 0, True
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
                # Collect one past the cap, then report on what was actually
                # found. The old form stopped *at* the cap and set the flag from
                # `idx < len(name_matches) - 1` -- whether any unscanned file
                # remained, not whether any further file matched. With exactly
                # max_results matches among a larger set of candidates that is a
                # false positive, and which way it fell depended on directory
                # iteration order: the same five files with five receipts beside
                # them reported truncated in CI and not in production, off the
                # same commit. The name-pattern branch above already compares
                # counts this way.
                if len(matches_out) > effective_max:
                    truncated = True
                    # Stopped one past the cap, so all that is known about the
                    # real number of matching files is that it is at least this.
                    files_matched, files_exact = len(matches_out), False
                    matches_out = matches_out[:effective_max]
                    break
                files_matched = len(matches_out)

    # The file list was bounded above; the lines inside each file were not. A
    # term appearing on 15,101 lines of two CSVs produced a 5.5 MB response that
    # said truncated: false, because `truncated` only ever described the file
    # list. Bound the lines too, and let either kind of trimming set the flag.
    hit_budget = get_max_grep_hits()
    hits_found = sum(len(e.get("hits", [])) for e in matches_out)
    hits_dropped = False
    if hits_found > hit_budget:
        remaining = hit_budget
        for entry in matches_out:
            entry_hits = entry.get("hits", [])
            entry["hits_total"] = len(entry_hits)
            if len(entry_hits) > remaining:
                entry["hits"] = entry_hits[:remaining]
                hits_dropped = True
            remaining = max(0, remaining - len(entry_hits))
        matches_out = [e for e in matches_out if e.get("hits")]

    total = len(matches_out)
    hits_returned = sum(len(e.get("hits", [])) for e in matches_out)
    files_truncated = truncated
    truncated = truncated or hits_dropped
    progress.append(
        ok(f"grep found {total} file(s) with matches", f"{hits_returned} line(s) returned")
    )

    result: dict = {
        "success": True,
        "op": "fs_query",
        "grep_mode": True,
        "pattern": pattern,
        "root": str(root),
        "content_pattern": content,
        # Which way the needle was read. A zero that does not say this is the
        # defect this field exists to close.
        "content_is_regex": is_regex,
        "matches": matches_out,
        # Two payloads, cut by two different budgets: the file list by
        # max_results, the lines inside each file by the hit budget. One flag
        # cannot describe both, and when it tried, a 5.5 MB response said
        # truncated: false because the flag only ever meant the file list. The
        # canonical triple describes the files; the lines carry their own three
        # numbers beside it. `truncated` below stays the "anything was withheld"
        # answer a caller reads first, which is why it is computed from both.
        **counted(total, max(total, files_matched), exact=files_exact and scan_complete),
        "files_matched": files_matched,
        "files_truncated": files_truncated,
        "hits_returned": hits_returned,
        "hits_found": hits_found,
        "hits_truncated": hits_dropped,
        # counts-contract: composite -- deliberately overrides the flag that
        # counted() derived for the file dimension immediately above, because
        # here it has to answer "was anything withheld at all" across both
        # payloads. A search that returned every matching file but clipped
        # 8,000 of its lines is not a complete result, and this is the field a
        # caller reads first. The per-payload numbers are all still present, so
        # nothing is hidden by the override; `files_truncated` and
        # `hits_truncated` say which of the two fired.
        "truncated": truncated,
        "scan_complete": scan_complete,
        "backend_used": content_backend,
        "progress": progress,
    }
    if not scan_complete:
        result["total_found_is_lower_bound"] = True
        result["files_scanned"] = scan_limit
        progress.append(
            warn(
                f"Stopped after scanning {scan_limit} path(s)",
                "the counts below cover only what was scanned",
            )
        )
    # grep mode bounds two different things and the caller only names one of
    # them. max_results=5 came back with 200 matching lines, honouring the cap
    # on files while the number the caller actually reads -- hits -- ran to a
    # separate budget nothing in the response mentioned. Say which limit bound
    # what, so the count that arrives can be reconciled with the count asked for.
    result["limits"] = {"max_results": effective_max, "max_hits": hit_budget}
    if hits_dropped:
        result["hint"] = (
            f"{hits_found} matching line(s) found, {hits_returned} returned "
            f"(line budget {hit_budget}). max_results bounds the files searched "
            f"({effective_max} here), not the lines matched inside them. Narrow the "
            "content pattern, or use fs_read on one file to page through its matches."
        )
    elif truncated:
        result["hint"] = (
            f"Results capped at {effective_max}. "
            "Narrow the content pattern or directory to see all matches."
        )
    elif not total and not is_regex and _looks_like_regex(content):
        result["hint"] = (
            f"'{content}' was searched for as literal text. If it was meant as a "
            "pattern, pass regex=True."
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
