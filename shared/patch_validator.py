"""Validate fs_write op arrays before execution.

validate_ops() returns a list of error strings.
Empty list means the array is structurally valid.
"""

ALLOWED_OPS: frozenset[str] = frozenset(
    {
        "write_file",
        "append_file",
        "create_dir",
        "move",
        "copy",
        "rename",
        "replace_text",
        "insert_after",
        "delete_lines",
        "patch_lines",
        "delete_request",
        "delete_confirm",
        "delete_tree_request",
        "delete_tree_confirm",
        "set_permissions",
    }
)

_REQUIRED: dict[str, list[str]] = {
    "write_file": ["path", "content"],
    "append_file": ["path", "content"],
    "create_dir": ["path"],
    "move": ["src", "dst"],
    "copy": ["src", "dst"],
    "rename": ["path", "name"],
    "replace_text": ["path", "find", "replace"],
    "insert_after": ["path", "after_pattern", "content"],
    "delete_lines": ["path", "start_line", "end_line"],
    "patch_lines": ["path", "start_line", "end_line", "content"],
    "delete_request": ["path"],
    "delete_confirm": ["token"],
    "delete_tree_request": ["path"],
    "delete_tree_confirm": ["token"],
    "set_permissions": ["path", "mode"],
}

_PATH_OPS: frozenset[str] = frozenset(
    {
        "write_file",
        "append_file",
        "create_dir",
        "rename",
        "replace_text",
        "insert_after",
        "delete_lines",
        "patch_lines",
        "delete_request",
        "delete_tree_request",
        "set_permissions",
    }
)

_MAX_OPS = 50


def validate_ops(ops: list[dict]) -> list[str]:
    """Return list of error strings; empty means valid."""
    if not isinstance(ops, list):
        return ["'ops' must be a list of operation dicts"]
    if len(ops) == 0:
        return ["'ops' list must not be empty"]
    if len(ops) > _MAX_OPS:
        return [f"Too many ops: {len(ops)} (max {_MAX_OPS})"]

    errors: list[str] = []
    for i, op_dict in enumerate(ops):
        prefix = f"Op {i}"
        if not isinstance(op_dict, dict):
            errors.append(f"{prefix}: must be a dict, got {type(op_dict).__name__}")
            continue

        op_name = op_dict.get("op")
        if not op_name:
            errors.append(f"{prefix}: missing required key 'op'")
            continue
        if not isinstance(op_name, str):
            errors.append(f"{prefix}: 'op' must be a string")
            continue
        if op_name not in ALLOWED_OPS:
            errors.append(f"{prefix}: unknown op '{op_name}'")
            continue

        for field in _REQUIRED.get(op_name, []):
            if field not in op_dict:
                errors.append(f"{prefix} ({op_name}): missing required field '{field}'")
            elif op_name in _PATH_OPS and field == "path":
                val = op_dict[field]
                if not isinstance(val, str) or not val.strip():
                    errors.append(f"{prefix} ({op_name}): 'path' must be a non-empty string")

    return errors
