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


def fs_query(
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
    format_: str = "zip",
    dry_run: bool = False,
) -> dict:
    return run_fs_archive(
        action=action,
        path=path,
        target=target,
        format_=format_,
        dry_run=dry_run,
    )
