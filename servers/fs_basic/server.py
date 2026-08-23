"""FastMCP server — 6 thin tool wrappers. All logic lives in engine.py."""

import argparse
import logging
import os
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
from deploy_auth import build_auth, build_oauth_bridge  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from shared.strict_args import enforce_known_arguments  # noqa: E402

_VERSION = "0.1.1"  # keep in sync with pyproject.toml [project].version
_HOST = os.environ.get("FS_HOST", "127.0.0.1")
_PORT = int(os.environ.get("FS_PORT", "8801"))
_oauth_bridge = build_oauth_bridge("FS")
_token_verifier, _auth_settings = build_auth("FS", _HOST, _PORT, _oauth_bridge)

mcp = FastMCP(
    "fs_basic",
    host=_HOST,
    port=_PORT,
    token_verifier=_token_verifier,
    auth=_auth_settings,
)
if _oauth_bridge is not None:
    _oauth_bridge.register_routes(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness check. Unauthenticated."""
    return JSONResponse({"status": "ok", "version": _VERSION})


@mcp.custom_route("/version", methods=["GET"])
async def version(request: Request) -> JSONResponse:
    """Report running version. Unauthenticated."""
    return JSONResponse({"current": _VERSION})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
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
    type: str = "",
) -> dict:
    """Locate files by name/content. grep_mode returns matching lines."""
    return engine.fs_query(
        pattern=pattern,
        path=path,
        type_=type or type_,
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
    format: str = "",
) -> dict:
    # Three sweeps running, the first call to this tool passed the archive as
    # `target` and the payload as `path` -- the natural reading of the two
    # names. The swap message added earlier explains it, but a caller should not
    # have to fail once to learn the contract, and half the 80 characters was
    # being spent on an implementation note nobody can act on.
    """Create or extract zip/tar.gz. path=archive, target=what goes in it."""
    return engine.fs_archive(
        action=action,
        path=path,
        target=target,
        format_=format or format_,
        dry_run=dry_run,
    )


# The bundled FastMCP ignores an argument a tool does not declare, so a wrong
# name yields a plausible answer with the argument silently dropped. Refuse it,
# and name the ones that would have worked.
enforce_known_arguments(mcp)


def main() -> None:
    parser = argparse.ArgumentParser(description="File System MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.environ.get("FS_TRANSPORT", "stdio"),
    )
    args = parser.parse_args()

    if args.transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
