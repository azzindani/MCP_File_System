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

from shared.confirm_store import (  # noqa: E402
    cleanup_expired,
    create_token,
    peek_token,
    validate_token,
)
from shared.file_utils import (  # noqa: E402
    atomic_write,
    atomic_write_bytes,
    attach_public_url,
    fetch_url,
    get_default_output_dir,
    is_url,
    resolve_path,
    size_kb,
)
from shared.patch_validator import ALLOWED_OPS, validate_ops  # noqa: E402
from shared.platform_utils import (  # noqa: E402
    get_content_backend,
    get_max_context_lines,
    get_max_depth,
    get_max_grep_hits,
    get_max_lines,
    get_max_results,
    get_max_tree_entries,
    get_name_backend,
    get_platform,
    is_constrained_mode,
)
from shared.progress import fail, info, ok, undo, warn  # noqa: E402
from shared.receipt import append_receipt, carry_receipt, read_receipt_log  # noqa: E402
from shared.version_control import (  # noqa: E402
    carry_snapshots,
    discard_snapshot_if_unchanged,
    list_versions,
    restore_version,
    snapshot,
    snapshot_tree,
)

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
    "size_kb",
    "_error",
    "logger",
    # shared helpers
    "cleanup_expired",
    "create_token",
    "peek_token",
    "validate_token",
    "atomic_write",
    "atomic_write_bytes",
    "attach_public_url",
    "fetch_url",
    "get_default_output_dir",
    "is_url",
    "resolve_path",
    "ALLOWED_OPS",
    "validate_ops",
    "get_content_backend",
    "get_max_context_lines",
    "get_max_grep_hits",
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
    "carry_receipt",
    "carry_snapshots",
    "read_receipt_log",
    "list_versions",
    "restore_version",
    "snapshot",
    "discard_snapshot_if_unchanged",
    "snapshot_tree",
]
