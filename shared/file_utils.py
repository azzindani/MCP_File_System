"""Core path utilities: resolve, atomic write, default output dir."""

import shutil
import sys
import tempfile
from pathlib import Path


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
    """Write content to path atomically (temp-file rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=path.parent,
        suffix=path.suffix,
        mode="w",
        encoding="utf-8",
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    shutil.move(tmp_path, path)


def get_default_output_dir(input_path: str | None = None) -> Path:
    """Return input file's parent dir, or ~/Downloads as fallback."""
    if input_path:
        return Path(input_path).parent
    downloads = Path.home() / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads
