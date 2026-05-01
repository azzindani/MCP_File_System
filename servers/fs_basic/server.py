"""FastMCP server — 6 thin tool wrappers. All logic lives in engine.py."""

import logging
import sys
from pathlib import Path

# Add project root and this directory to sys.path before any local imports
_this_dir = Path(__file__).resolve().parent
_root_dir = _this_dir.parent.parent
for _p in (str(_root_dir), str(_this_dir)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

import engine  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

mcp = FastMCP("fs_basic")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
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
    """Locate files by name/content. grep_mode returns matching lines."""
    return engine.fs_query(
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


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
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
    """Read file content, tree, metadata, or diff. Bounded always."""
    return engine.fs_read(
        path=path,
        mode=mode,
        start_line=start_line,
        end_line=end_line,
        depth=depth,
        compare_to=compare_to,
        changed_since=changed_since,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def fs_write(ops: list[dict], dry_run: bool = False) -> dict:
    """Write, edit, move, copy files. Delete requires confirmation token."""
    return engine.fs_write(ops=ops, dry_run=dry_run)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def fs_index(
    action: str = "query",
    path: str = "",
    pattern: str = "",
    max_results: int = 50,
) -> dict:
    """Build/query/list file index or read operation receipt history."""
    return engine.fs_index(
        action=action,
        path=path,
        pattern=pattern,
        max_results=max_results,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def fs_manage(action: str, path: str = "") -> dict:
    """Disk usage, permissions, symlink info, or snapshot version list."""
    return engine.fs_manage(action=action, path=path)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def fs_archive(
    action: str,
    path: str,
    target: str = "",
    format_: str = "zip",
    dry_run: bool = False,
) -> dict:
    """Create or extract zip/tar.gz archives. Uses Python stdlib only."""
    return engine.fs_archive(
        action=action,
        path=path,
        target=target,
        format_=format_,
        dry_run=dry_run,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
