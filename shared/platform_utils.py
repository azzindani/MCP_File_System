"""Platform detection and constrained-mode limits.

All limit functions read MCP_CONSTRAINED_MODE env var at call time
so tests can override it without restarting.
"""
import os
import shutil
import sys


def is_constrained_mode() -> bool:
    return os.environ.get("MCP_CONSTRAINED_MODE", "0") == "1"


def get_max_results() -> int:
    return 10 if is_constrained_mode() else 50


def get_max_lines() -> int:
    return 20 if is_constrained_mode() else 100


def get_max_tree_entries() -> int:
    return 100 if is_constrained_mode() else 500


def get_max_depth() -> int:
    return 3 if is_constrained_mode() else 5


def get_max_context_lines() -> int:
    return 2 if is_constrained_mode() else 5


def get_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def get_name_backend() -> str:
    """Return fastest available name-search backend for current platform."""
    platform = get_platform()
    if platform == "windows":
        everything_path = os.environ.get("EVERYTHING_PATH", "")
        if everything_path or shutil.which("es.exe"):
            return "everything"
    elif platform == "macos":
        if shutil.which("mdfind"):
            return "mdfind"
    elif platform == "linux":
        if shutil.which("locate") or shutil.which("plocate"):
            return "locate"
    return "python"


def get_content_backend() -> str:
    """Return fastest available content-search backend."""
    if shutil.which("rg"):
        return "ripgrep"
    return "python"
