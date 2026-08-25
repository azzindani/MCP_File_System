"""Validate fs_write op arrays before execution.

validate_ops() returns a list of error strings.
Empty list means the array is structurally valid.

`shared/strict_args.py` makes every tool refuse an argument it does not declare,
but it can only see the tool's own parameters -- and `fs_write` declares two,
`ops` and `dry_run`. Everything that actually varies a write lives inside the op
dicts, where nothing looked at the key names at all: only the *required* fields
were checked, so any other key was dropped without a word. The optional ones are
undiscoverable to begin with, because the schema for `ops` is `list[dict]`:

    replace_text   regex, count
    insert_after   count
    write_file     content_encoding
    restore        timestamp

Round 11 measured both halves against the live server:

    replace_text find="X+" replace="-" use_regex=True
        -> success: false, "Pattern not found in t.txt"
    replace_text find="X+" replace="-" regex=True
        -> success: true, replacements: 2

The flag was dropped, "X+" was matched literally, and the caller was told the
pattern is not in the file -- which is false, and sends it to re-read a file
that was never the problem.

    write_file content="aGVsbG8=" encoding="base64"
        -> success: true, 8 bytes on disk: 61 47 56 73 62 47 38 3d   ("aGVsbG8=")
    write_file content="aGVsbG8=" content_encoding="base64"
        -> success: true, 5 bytes on disk: 68 65 6c 6c 6f            ("hello")

One word apart, both `success: true`, and the first wrote the base64 text into
the file instead of the bytes it stands for.
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
        "download",
        # Every destructive op above takes a snapshot, `fs_manage
        # action=versions` lists them, and every empty listing says "Snapshots
        # are created automatically on destructive writes" -- but nothing could
        # put one back. `restore_version` existed in shared/version_control.py
        # with no caller outside the tests. The three sibling repos all expose a
        # restore; this server took the snapshots and offered no way to use
        # them.
        "restore",
    }
)

_REQUIRED: dict[str, list[str]] = {
    "write_file": ["path", "content"],
    "download": ["url", "path"],
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
    # timestamp is optional: with none given, restore takes the newest snapshot.
    "restore": ["path"],
}

# The fields an op reads beyond its required ones. Kept next to _REQUIRED so a
# new optional field is one line away from the list that makes it discoverable;
# adding a handler that reads op_dict.get("thing") without adding it here now
# makes "thing" a refusal, which is the failure mode that gets noticed.
_OPTIONAL: dict[str, list[str]] = {
    "write_file": ["content_encoding"],
    "replace_text": ["regex", "count"],
    "insert_after": ["count"],
    "restore": ["timestamp"],
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
        "download",
        "restore",
    }
)

# A census of the sixteen ops: ten of them call the file they act on `path`.
# Only move and copy call it `src`, so `path` is the name a caller reaches for,
# and two independent callers -- a sweep model and this repo's own test author
# -- wrote copy(path=..., dst=...) and were refused. The tool schema is an
# opaque list[dict], so nothing advertises the difference until the call fails.
#
# Accepting the majority spelling costs nothing: `src` stays documented and both
# work. The alias resolves to the canonical field before validation, so no op
# handler changes.
_FIELD_ALIASES: dict[str, dict[str, str]] = {
    "move": {"path": "src"},
    "copy": {"path": "src"},
    "rename": {"new_name": "name", "dst": "name"},
}

# Only `path` was ever type-checked, so every other field reached its handler as
# whatever the caller sent and failed there as a Python attribute error. An op
# called patch_*lines* invites `content` as a list of lines -- a sweep model
# wrote exactly that -- and got back "'list' object has no attribute
# 'splitlines'" under the hint "Retry op=patch_lines with corrected
# parameters", which names neither the field nor what was wrong with it.
_STR_FIELDS: frozenset[str] = frozenset(
    {
        "content",
        "content_encoding",
        "find",
        "replace",
        "after_pattern",
        "name",
        "src",
        "dst",
        "mode",
        "token",
        "url",
        "timestamp",
    }
)
_INT_FIELDS: frozenset[str] = frozenset({"start_line", "end_line", "count"})
_BOOL_FIELDS: frozenset[str] = frozenset({"regex"})

_MAX_OPS = 50


def _type_errors(prefix: str, op_name: str, op_dict: dict) -> list[str]:
    """Name the field and the type it wants, before the handler trips over it."""
    out: list[str] = []
    for field, value in op_dict.items():
        if field == "op":
            continue
        if field in _STR_FIELDS and not isinstance(value, str):
            got = type(value).__name__
            extra = ""
            if field == "content" and isinstance(value, list):
                extra = ' Pass the lines joined into one string, e.g. "\\n".join(lines).'
            elif field == "mode":
                # mode=644 as an int is the natural way to type it, and it is
                # base-8 text: reaching the handler it raised "int() can't
                # convert non-string with explicit base".
                extra = " Modes are octal strings, e.g. '644' or '755'."
            out.append(f"{prefix} ({op_name}): '{field}' must be a string, got {got}.{extra}")
        elif field in _INT_FIELDS and (isinstance(value, bool) or not isinstance(value, int)):
            out.append(
                f"{prefix} ({op_name}): '{field}' must be an integer, got {type(value).__name__}"
            )
        elif field in _BOOL_FIELDS and not isinstance(value, bool):
            out.append(
                f"{prefix} ({op_name}): '{field}' must be true or false, got {type(value).__name__}"
            )
    return out


def apply_field_aliases(op_dict: dict) -> dict:
    """Fill a canonical field from the spelling the other ops use."""
    aliases = _FIELD_ALIASES.get(op_dict.get("op", ""), {})
    for given, canonical in aliases.items():
        if canonical not in op_dict and given in op_dict:
            op_dict[canonical] = op_dict[given]
    return op_dict


def known_fields(op_name: str) -> list[str]:
    """Every key this op reads, including the alias spellings it accepts."""
    fields = {"op"}
    fields.update(_REQUIRED.get(op_name, []))
    fields.update(_OPTIONAL.get(op_name, []))
    fields.update(_FIELD_ALIASES.get(op_name, {}))
    return sorted(fields)


def _did_you_mean(unknown: str, known: list[str]) -> str:
    """The closest accepted name, when one is obviously close."""
    import difflib

    # Prefix- and suffix-insensitive first: the real misses are use_regex for
    # regex and encoding for content_encoding, where one name contains the
    # other. difflib alone rates encoding/content_encoding below its cutoff.
    for k in known:
        if k in unknown or unknown in k:
            return k
    close = difflib.get_close_matches(unknown, known, n=1, cutoff=0.75)
    return close[0] if close else ""


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
            errors.append(
                f"{prefix}: unknown op '{op_name}'. Valid ops: {', '.join(sorted(ALLOWED_OPS))}"
            )
            continue

        apply_field_aliases(op_dict)

        # Names first: a missing required field reported against a call whose
        # real problem is a typo'd optional one sends the caller after the
        # wrong thing. The accepted list goes in the message because the `ops`
        # schema is list[dict] and this is the only place it is discoverable.
        known = known_fields(op_name)
        unknown = [k for k in op_dict if k not in known]
        if unknown:
            suggestion = _did_you_mean(unknown[0], known)
            lead = f"did you mean {suggestion}? " if suggestion else ""
            errors.append(
                f"{prefix} ({op_name}): unknown field(s) {', '.join(sorted(unknown))} -- "
                f"{lead}{op_name} accepts: {', '.join(known)}"
            )
            continue

        errors.extend(_type_errors(prefix, op_name, op_dict))

        accepted = _FIELD_ALIASES.get(op_name, {})
        for field in _REQUIRED.get(op_name, []):
            if field not in op_dict:
                also = [g for g, c in accepted.items() if c == field]
                extra = f" (also accepted: {', '.join(sorted(also))})" if also else ""
                errors.append(f"{prefix} ({op_name}): missing required field '{field}'{extra}")
            elif op_name in _PATH_OPS and field == "path":
                val = op_dict[field]
                if not isinstance(val, str) or not val.strip():
                    errors.append(f"{prefix} ({op_name}): 'path' must be a non-empty string")

    return errors
