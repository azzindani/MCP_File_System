"""Core path utilities: resolve, atomic write, default output dir."""

import shutil
import sys
import tempfile
from pathlib import Path


def resolve_path(file_path: str, must_exist: bool = False) -> Path:
    """Resolve path; raise ValueError if outside home directory.

    Handles ~ expansion and relative paths (relative to home).
    Rejects path traversal sequences and paths outside home.
    Applies Windows long-path prefix when needed.
    """
    home = Path.home()
    raw = Path(file_path).expanduser()
    if not raw.is_absolute():
        raw = home / raw
    path = raw.resolve()

    # Normalise home for comparison (resolve symlinks in home too)
    home_resolved = home.resolve()
    try:
        path.relative_to(home_resolved)
    except ValueError:
        raise ValueError(
            f"Path '{path}' is outside your home directory. "
            "Only paths within your home directory are allowed."
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
