"""Core path utilities: resolve, atomic write, default output dir."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

from shared.exchange import (
    apply_default_mode,
    attach_public_url,
    client_side_refusal,
    fetch_url,
    get_inbox_dir,
    get_output_dir,
    is_url,
    public_url_for,
    url_fetch_enabled,
)

__all__ = [
    "apply_default_mode",
    "atomic_write",
    "atomic_write_bytes",
    "attach_public_url",
    "fetch_url",
    "get_default_output_dir",
    "get_inbox_dir",
    "get_output_dir",
    "is_url",
    "PathOutsideRootError",
    "anchor",
    "default_dir",
    "path_hint",
    "paths_confined",
    "public_url_for",
    "resolve_path",
    "served_roots",
    "url_fetch_enabled",
]


class PathOutsideRootError(ValueError):
    """A confined server was handed a path outside every folder it serves.

    A ValueError because every tool here answers ValueError first; a
    PermissionError would be swallowed by the walkers' `except PermissionError`
    and read as an empty folder.
    """

    hint = (
        "Pass a path inside the data folder (a relative path is read from it). "
        "The server's operator can add folders with MCP_ALLOWED_ROOTS."
    )


def paths_confined() -> bool:
    """True when paths are held to the served folders (every HTTP deployment).

    A local install works anywhere, by design: it is the caller's own machine.
    A remote caller shares no filesystem with this server, and unconfined any
    authenticated caller could read, overwrite or delete any file the container
    could -- /proc/self/environ holds the API keys.
    """
    return os.environ.get("MCP_CONFINE_PATHS", "").strip().lower() in ("1", "true", "yes", "on")


def served_roots() -> list[Path]:
    """The folders a confined server serves: MCP_OUTPUT_DIR, MCP_DATA_ROOT, MCP_ALLOWED_ROOTS."""
    raws = [os.environ.get("MCP_OUTPUT_DIR", ""), os.environ.get("MCP_DATA_ROOT", "")]
    raws += os.environ.get("MCP_ALLOWED_ROOTS", "").split(os.pathsep)
    return [Path(r).expanduser().resolve() for r in raws if r.strip()]


def default_dir() -> Path:
    """Where a relative path, or no path at all, points: home locally, the data folder when confined."""
    if paths_confined():
        for name in ("MCP_DATA_ROOT", "MCP_OUTPUT_DIR"):
            if os.environ.get(name, "").strip():
                return Path(os.environ[name]).expanduser()
    return Path.home()


def anchor(file_path: str) -> Path:
    """`file_path` with ~ expanded and a relative path placed under default_dir(), unresolved."""
    raw = Path(file_path).expanduser()
    return raw if raw.is_absolute() else default_dir() / raw


def path_hint(exc: Exception, fallback: str) -> str:
    """The recovery for this ValueError: the refusal's own hint, else the caller's."""
    return exc.hint if isinstance(exc, PathOutsideRootError) else fallback


# How this server takes a file's bytes: fs_write's write_file, which is text by
# default and binary with content_encoding="base64".
_FS_INLINE_ROUTE = (
    "write its contents with fs_write: a write_file op, with content_encoding='base64' for a binary file"
)


def _confine(path: Path, file_path: str) -> None:
    """Refuse `path` (already resolved) when confined and outside every served folder.

    Judged after symlinks resolve and before existence is checked, so the
    refusal says nothing about what exists out there.
    """
    if not paths_confined():
        return
    roots = served_roots()
    if any(path == root or path.is_relative_to(root) for root in roots):
        return
    shown = ", ".join(str(r) for r in roots[:3]) or "none configured"
    elsewhere = client_side_refusal(file_path, _FS_INLINE_ROUTE)
    if elsewhere:
        refusal = PathOutsideRootError(elsewhere)
        refusal.hint = "The file is on the caller's side; bring it here by one of the routes named."
        raise refusal
    raise PathOutsideRootError(f"'{file_path}' is outside the folders this server can use ({shown}).")


def resolve_path(file_path: str, must_exist: bool = False) -> Path:
    """Resolve and normalise a path. Rejects UNC network paths on Windows.

    Handles ~ expansion and relative paths (resolved from home, or from the
    data folder when confined). Applies Windows long-path prefix for paths
    > 200 chars. No directory restriction on a local install — the tool is
    designed to work anywhere. With MCP_CONFINE_PATHS on, the resolved path
    must lie inside a served folder (see served_roots).
    """
    if "\x00" in file_path:
        raise ValueError("A path cannot contain a null byte.")
    path = anchor(file_path).resolve()
    _confine(path, file_path)

    # Reject UNC network paths — this server is local-only
    if sys.platform == "win32" and str(path).startswith("\\\\"):
        raise ValueError(
            f"UNC network paths are not supported: '{path}'. "
            "This server operates on local files only."
        )

    if must_exist and not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")

    # Windows long-path prefix
    if sys.platform == "win32" and len(str(path)) > 200:
        path = Path("\\\\?\\" + str(path))

    return path


def atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically (temp-file rename).

    NamedTemporaryFile creates 0600 and the rename preserves it, which would
    leave every written file unreadable to anything but this process — wrong
    for a shared directory, and inconsistent with a plain open() anywhere.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=path.parent,
        suffix=path.suffix,
        mode="w",
        encoding="utf-8",
        # Verbatim: the line ops hand back text they read from the file, so
        # translating "\n" to os.linesep here would rewrite every line ending
        # in a file the caller only asked to edit one line of.
        newline="",
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    apply_default_mode(tmp_path)
    shutil.move(tmp_path, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write binary data to path atomically (temp-file rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=path.parent,
        suffix=path.suffix,
        mode="wb",
    ) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    apply_default_mode(tmp_path)
    shutil.move(tmp_path, path)


def get_default_output_dir(input_path: str | None = None) -> Path:
    """Return MCP_OUTPUT_DIR, else the input file's parent, else ~/Downloads.

    MCP_OUTPUT_DIR outranks the input file's directory: a remote deployment
    sets it precisely so generated files land somewhere the caller can reach,
    which an input file's own directory is not guaranteed to be.
    """
    if os.environ.get("MCP_OUTPUT_DIR", "").strip():
        return get_output_dir()
    if input_path:
        return Path(input_path).parent
    downloads = Path.home() / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads


def size_kb(n_bytes: int) -> float:
    """Size in KB, rounded so a file that exists never reports as 0.

    Integer division sends everything under 1024 bytes to 0, which is what an
    empty file looks like. That number carries real weight here: it is the size
    quoted in the delete confirmation ("Deletes 1 item(s) (0 KB) ...") -- and a
    900-byte file reads as nothing worth keeping. Small files keep enough
    decimals to stay non-zero; only a genuinely empty one returns 0.0.
    """
    if n_bytes <= 0:
        return 0.0
    kb = n_bytes / 1024
    return round(kb, 1) if kb >= 0.1 else round(kb, 3)
