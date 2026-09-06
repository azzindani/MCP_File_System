"""Thin router — imports and re-exports from _basic_*.py sub-modules.

Zero MCP imports. This module is the sole entry point for all tool logic.
Tests import this module directly without spinning up an MCP server.
"""

from _basic_archive import run_fs_archive
from _basic_index import run_fs_index
from _basic_manage import run_fs_manage
from _basic_query import run_fs_query
from _basic_read import run_fs_read
from _basic_write import run_fs_write

from shared.patch_validator import ALLOWED_OPS, _did_you_mean, catalogue


def list_fs_ops(op: str = "") -> dict:
    """The fs_write op grammar, which tools/list cannot show.

    `ops` is declared `list[dict]` with an items schema of
    `{"additionalProperties": true}`, so every field of every op is invisible
    to a caller reading the schema, and fs_write's one-line description names
    none of them. This is the same gap `list_patch_ops` and `list_derive_ops`
    close in the Data_Analyst repo; filesystem had the refusal half -- which
    does name the accepted fields -- and not the discovery half, so the grammar
    could only be learned by getting it wrong first.
    """
    if op and op not in ALLOWED_OPS:
        near = _did_you_mean(op, sorted(ALLOWED_OPS))
        lead = f"Did you mean '{near}'? " if near else ""
        return {
            "success": False,
            "op": "list_fs_ops",
            "error": f"Unknown fs_write op: '{op}'.",
            "hint": f"{lead}Valid ops: {', '.join(sorted(ALLOWED_OPS))}.",
            "progress": [],
            "token_estimate": 60,
        }

    ops = catalogue(op)
    result: dict = {
        "success": True,
        "op": "list_fs_ops",
        "requested": op or "all",
        "total_ops": len(ops),
        "ops": ops,
        "hint": (
            "Send these to fs_write as ops=[{...}]. Every op takes its `op` name plus the "
            "fields listed; `destructive: true` means fs_write snapshots the file first and "
            "fs_manage(action='versions') lists the snapshots."
        ),
        "progress": [],
    }
    result["token_estimate"] = len(str(result)) // 4
    return result


def fs_query(
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
    return run_fs_query(
        pattern=pattern,
        path=path,
        type_=type_,
        content=content,
        grep_mode=grep_mode,
        context_lines=context_lines,
        include_meta=include_meta,
        follow_symlinks=follow_symlinks,
        max_results=max_results,
        regex=regex,
    )


def fs_read(
    path: str,
    mode: str = "auto",
    start_line: int = 0,
    end_line: int = 100,
    depth: int = 2,
    compare_to: str = "",
    changed_since: str = "",
) -> dict:
    return run_fs_read(
        path=path,
        mode=mode,
        start_line=start_line,
        end_line=end_line,
        depth=depth,
        compare_to=compare_to,
        changed_since=changed_since,
    )


def fs_write(ops: list[dict], dry_run: bool = False) -> dict:
    return run_fs_write(ops=ops, dry_run=dry_run)


def fs_index(
    action: str = "query",
    path: str = "",
    pattern: str = "",
    max_results: int = 50,
) -> dict:
    return run_fs_index(
        action=action,
        path=path,
        pattern=pattern,
        max_results=max_results,
    )


def fs_manage(action: str, path: str = "") -> dict:
    return run_fs_manage(action=action, path=path)


def fs_archive(
    action: str,
    path: str,
    target: str = "",
    # "" means "read it off the archive's extension". The server has always
    # passed "" and this said "zip", so the engine default was dead for every
    # MCP caller and live for every direct one -- two behaviours behind one
    # signature. `create` still falls back to zip when the name says nothing.
    format_: str = "",
    dry_run: bool = False,
) -> dict:
    return run_fs_archive(
        action=action,
        path=path,
        target=target,
        format_=format_,
        dry_run=dry_run,
    )
