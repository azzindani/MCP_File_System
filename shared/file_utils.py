"""Core path utilities: resolve, atomic write, default output dir."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

from shared.exchange import (
    apply_default_mode,
    attach_public_url,
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
    "public_url_for",
    "resolve_path",
    "url_fetch_enabled",
]


def resolve_path(file_path: str, must_exist: bool = False) -> Path:
    """Resolve and normalise a path. Rejects UNC network paths on Windows.

    Handles ~ expansion and relative paths (resolved from home).
    Applies Windows long-path prefix for paths > 200 chars.
    No directory restriction — the tool is designed to work anywhere.
    """
    home = Path.home()
    raw = Path(file_path).expanduser()
    if not raw.is_absolute():
        raw = home / raw
    path = raw.resolve()

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
