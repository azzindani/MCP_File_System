"""Shared imports, constants, and _error helper for fs_basic engine.

This module also ensures the project root is in sys.path so that
'shared.*' imports work regardless of working directory.
"""
import logging
import sys
from pathlib import Path

# Ensure project root and fs_basic dir are importable
_this_dir = Path(__file__).resolve().parent
_root_dir = _this_dir.parent.parent
for _p in (str(_root_dir), str(_this_dir)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.confirm_store import cleanup_expired, create_token, validate_token  # noqa: E402
from shared.file_utils import atomic_write, get_default_output_dir, resolve_path  # noqa: E402
from shared.patch_validator import ALLOWED_OPS, validate_ops  # noqa: E402
from shared.platform_utils import (  # noqa: E402
    get_content_backend,
    get_max_context_lines,
    get_max_depth,
    get_max_lines,
    get_max_results,
    get_max_tree_entries,
    get_name_backend,
    get_platform,
    is_constrained_mode,
)
from shared.progress import fail, info, ok, undo, warn  # noqa: E402
from shared.receipt import append_receipt, read_receipt_log  # noqa: E402
from shared.version_control import list_versions, restore_version, snapshot  # noqa: E402

logger = logging.getLogger(__name__)


def _error(op: str, error: str, hint: str = "", extra: dict | None = None) -> dict:
    """Return a structured error dict with required fields."""
    result: dict = {
        "success": False,
        "op": op,
        "error": error,
        "hint": hint,
        "progress": [fail(error)],
        "token_estimate": 0,
    }
    if extra:
        result.update(extra)
    result["token_estimate"] = len(str(result)) // 4
    return result


__all__ = [
    "_error",
    "logger",
    # shared helpers
    "cleanup_expired",
    "create_token",
    "validate_token",
    "atomic_write",
    "get_default_output_dir",
    "resolve_path",
    "ALLOWED_OPS",
    "validate_ops",
    "get_content_backend",
    "get_max_context_lines",
    "get_max_depth",
    "get_max_lines",
    "get_max_results",
    "get_max_tree_entries",
    "get_name_backend",
    "get_platform",
    "is_constrained_mode",
    "fail",
    "info",
    "ok",
    "undo",
    "warn",
    "append_receipt",
    "read_receipt_log",
    "list_versions",
    "restore_version",
    "snapshot",
]
