"""fs_write implementation — PATCH files with two-phase deletion gate."""
import re
import shutil
import sys
from pathlib import Path

from _basic_helpers import (
    _error,
    append_receipt,
    atomic_write,
    cleanup_expired,
    create_token,
    info,
    ok,
    resolve_path,
    snapshot,
    validate_ops,
    validate_token,
    warn,
)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_fs_write(ops: list[dict], dry_run: bool = False) -> dict:
    try:
        return _fs_write(ops, dry_run)
    except ValueError as e:
        return _error("fs_write", str(e),
                      "Ensure all paths are within your home directory.")
    except Exception as e:
        return _error("fs_write", str(e),
                      "Check op parameters and retry with a single op to isolate.")


# ---------------------------------------------------------------------------
# Core dispatcher
# ---------------------------------------------------------------------------


def _fs_write(ops: list[dict], dry_run: bool) -> dict:
    cleanup_expired()

    # Step 1: structural validation
    errors = validate_ops(ops)
    if errors:
        return _error("fs_write", errors[0],
                      "Fix the op array and retry.")

    # Step 2: detect delete ops — they stop the batch
    delete_op_names = ("delete_request", "delete_tree_request")
    delete_ops = [op for op in ops if op.get("op") in delete_op_names]
    if delete_ops:
        return _handle_delete_request(delete_ops, dry_run)

    # Step 3: execute ops in order; stop on first failure
    progress: list[dict] = []
    results: list[dict] = []
    would_change: list[dict] = []

    for op_dict in ops:
        r = _dispatch_op(op_dict, dry_run)
        if not r.get("success", False):
            return r  # stop batch; already-applied ops have snapshots
        results.append(r)
        progress.extend(r.pop("progress", []))
        if dry_run and r.get("would_change"):
            would_change.append(r)

    response: dict = {
        "success": True,
        "op": "fs_write",
        "ops_applied": 0 if dry_run else len(results),
        "results": results,
        "progress": progress,
    }
    if dry_run:
        response["dry_run"] = True
        response["would_change"] = would_change
    response["token_estimate"] = len(str(response)) // 4
    return response


# ---------------------------------------------------------------------------
# Delete protocol
# ---------------------------------------------------------------------------


def _handle_delete_request(delete_ops: list[dict], dry_run: bool) -> dict:
    targets: list[dict] = []
    progress: list[dict] = []
    total_size_kb = 0

    for op_dict in delete_ops:
        path_str = op_dict["path"]
        try:
            path = resolve_path(path_str, must_exist=True)
        except (ValueError, FileNotFoundError) as e:
            return _error("fs_write", str(e),
                          "Verify the path exists and is within your home directory.")
        size_kb = _get_size_kb(path)
        total_size_kb += size_kb
        t = {
            "path": str(path),
            "size_kb": size_kb,
            "type": "directory" if path.is_dir() else "file",
        }
        targets.append(t)
        progress.append(info(f"Located {path.name}", f"{size_kb} KB"))

    n = len(targets)
    warning = f"Permanently deletes {n} item(s) ({total_size_kb} KB). Cannot be undone."

    if dry_run:
        result: dict = {
            "success": True,
            "op": "delete_pending",
            "pending": True,
            "dry_run": True,
            "targets": targets,
            "total_size_kb": total_size_kb,
            "warning": warning,
            "progress": progress,
        }
        result["token_estimate"] = len(str(result)) // 4
        return result

    token = create_token(targets)
    result = {
        "success": True,
        "op": "delete_pending",
        "pending": True,
        "confirmation_token": token,
        "expires_in_seconds": 300,
        "targets": targets,
        "total_size_kb": total_size_kb,
        "warning": warning,
        "next_step": (
            f"Call fs_write with op=delete_confirm and token={token} to proceed."
        ),
        "progress": progress,
    }
    result["token_estimate"] = len(str(result)) // 4
    return result


def _op_delete_confirm(op_dict: dict, dry_run: bool) -> dict:
    token = op_dict["token"]
    entry = validate_token(token)
    if entry is None:
        return _error(
            "delete_confirm",
            "Invalid or expired confirmation token",
            "Use fs_write with op=delete_request to request a new confirmation token.",
        )

    targets = entry["targets"]
    deleted: list[str] = []
    backups: list[str] = []
    progress: list[dict] = []

    for t in targets:
        p = Path(t["path"])
        if not p.exists():
            progress.append(warn(f"{p.name} already gone, skipping"))
            continue
        backup = snapshot(str(p))
        if backup:
            backups.append(backup)
        if not dry_run:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            deleted.append(str(p))
            append_receipt(str(p), "fs_write", "delete_confirm", "deleted", backup)
            progress.append(ok(f"Deleted {p.name}", f"backup={backup}"))

    result: dict = {
        "success": True,
        "op": "delete_confirm",
        "deleted": deleted,
        "backup": backups[0] if backups else None,
        "backups": backups,
        "progress": progress,
    }
    result["token_estimate"] = len(str(result)) // 4
    return result


# ---------------------------------------------------------------------------
# Op dispatcher
# ---------------------------------------------------------------------------


def _dispatch_op(op_dict: dict, dry_run: bool) -> dict:
    name = op_dict["op"]
    handlers = {
        "write_file": _op_write_file,
        "append_file": _op_append_file,
        "create_dir": _op_create_dir,
        "move": _op_move,
        "copy": _op_copy,
        "rename": _op_rename,
        "replace_text": _op_replace_text,
        "insert_after": _op_insert_after,
        "delete_lines": _op_delete_lines,
        "patch_lines": _op_patch_lines,
        "delete_confirm": _op_delete_confirm,
        "delete_tree_confirm": _op_delete_confirm,
        "set_permissions": _op_set_permissions,
    }
    handler = handlers.get(name)
    if not handler:
        return _error("fs_write", f"Unhandled op: {name}",
                      "Use a supported op from the fs_write op table.")
    try:
        return handler(op_dict, dry_run)
    except ValueError as e:
        return _error(name, str(e),
                      "Ensure path is within your home directory.")
    except PermissionError as e:
        return _error(name, f"Permission denied: {e}",
                      "Check file/directory permissions.")
    except Exception as e:
        return _error(name, str(e),
                      f"Retry op={name} with corrected parameters.")


# ---------------------------------------------------------------------------
# Individual op implementations
# ---------------------------------------------------------------------------


def _op_write_file(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"])
    content: str = op_dict["content"]
    backup: str | None = None

    if path.exists():
        backup = snapshot(str(path))

    if dry_run:
        r: dict = {
            "success": True, "op": "write_file", "path": str(path),
            "would_change": True, "backup": backup,
            "progress": [info(f"Would write {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    atomic_write(path, content)
    append_receipt(str(path), "fs_write", "write_file",
                   "created" if not backup else "overwritten", backup)
    r = {
        "success": True, "op": "write_file", "path": str(path),
        "backup": backup,
        "progress": [ok(f"Wrote {path.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_append_file(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"])
    content: str = op_dict["content"]

    if dry_run:
        r: dict = {
            "success": True, "op": "append_file", "path": str(path),
            "would_change": True,
            "progress": [info(f"Would append to {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(content)
    append_receipt(str(path), "fs_write", "append_file", "appended", None)
    r = {
        "success": True, "op": "append_file", "path": str(path),
        "backup": None,
        "progress": [ok(f"Appended to {path.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_create_dir(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"])

    if dry_run:
        r: dict = {
            "success": True, "op": "create_dir", "path": str(path),
            "would_change": not path.exists(),
            "progress": [info(f"Would create dir {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    path.mkdir(parents=True, exist_ok=True)
    r = {
        "success": True, "op": "create_dir", "path": str(path),
        "progress": [ok(f"Created dir {path.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_move(op_dict: dict, dry_run: bool) -> dict:
    src = resolve_path(op_dict["src"], must_exist=True)
    dst = resolve_path(op_dict["dst"])

    if dst.exists():
        return _error(
            "move",
            f"Destination already exists: {dst.name}",
            "Rename the destination first, or use op=copy if you want to overwrite.",
        )

    if dry_run:
        r: dict = {
            "success": True, "op": "move", "src": str(src), "dst": str(dst),
            "would_change": True,
            "progress": [info(f"Would move {src.name} → {dst.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dst)
    append_receipt(str(dst), "fs_write", "move", f"moved from {src}", None)
    r = {
        "success": True, "op": "move", "src": str(src), "dst": str(dst),
        "backup": None,
        "progress": [ok(f"Moved {src.name} → {dst.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_copy(op_dict: dict, dry_run: bool) -> dict:
    src = resolve_path(op_dict["src"], must_exist=True)
    dst = resolve_path(op_dict["dst"])
    backup: str | None = None

    if dst.exists():
        backup = snapshot(str(dst))

    if dry_run:
        r: dict = {
            "success": True, "op": "copy", "src": str(src), "dst": str(dst),
            "would_change": True, "backup": backup,
            "progress": [info(f"Would copy {src.name} → {dst.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(str(src), dst)
    else:
        shutil.copy2(src, dst)
    append_receipt(str(dst), "fs_write", "copy", f"copied from {src}", backup)
    r = {
        "success": True, "op": "copy", "src": str(src), "dst": str(dst),
        "backup": backup,
        "progress": [ok(f"Copied {src.name} → {dst.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_rename(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"], must_exist=True)
    new_name: str = op_dict["name"]
    if "/" in new_name or "\\" in new_name:
        return _error("rename", "name must not contain path separators",
                      "Use op=move to move across directories.")
    dst = path.parent / new_name

    if dry_run:
        r: dict = {
            "success": True, "op": "rename",
            "path": str(path), "new_path": str(dst), "would_change": True,
            "progress": [info(f"Would rename {path.name} → {new_name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    path.rename(dst)
    r = {
        "success": True, "op": "rename",
        "path": str(path), "new_path": str(dst),
        "progress": [ok(f"Renamed {path.name} → {new_name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_replace_text(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"], must_exist=True)
    find: str = op_dict["find"]
    replace: str = op_dict["replace"]
    use_regex: bool = bool(op_dict.get("regex", False))
    count: int = int(op_dict.get("count", 0))  # 0 = all

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _error("replace_text", str(e), "Check file permissions.")

    if use_regex:
        try:
            new_content, n = re.subn(find, replace, content, count=count)
        except re.error as e:
            return _error("replace_text", f"Invalid regex: {e}",
                          "Fix the regex in the 'find' parameter.")
    else:
        occurrences = content.count(find)
        n = min(occurrences, count) if count else occurrences
        new_content = content.replace(find, replace, count if count else -1)

    if n == 0:
        return _error("replace_text", f"Pattern not found in {path.name}",
                      "Use fs_read to verify the file content and pattern.")

    backup = snapshot(str(path))

    if dry_run:
        r: dict = {
            "success": True, "op": "replace_text", "path": str(path),
            "would_replace": n, "would_change": True, "backup": backup,
            "progress": [info(f"Would replace {n} occurrence(s) in {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    atomic_write(path, new_content)
    append_receipt(str(path), "fs_write", "replace_text",
                   f"replaced {n} occurrences", backup)
    r = {
        "success": True, "op": "replace_text", "path": str(path),
        "replacements": n, "backup": backup,
        "progress": [ok(f"Replaced {n} occurrence(s) in {path.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_insert_after(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"], must_exist=True)
    after_pattern: str = op_dict["after_pattern"]
    insert_content: str = op_dict["content"]
    count: int = int(op_dict.get("count", 1))

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _error("insert_after", str(e), "Check file permissions.")

    lines = text.splitlines(keepends=True)
    new_lines: list[str] = []
    inserted = 0
    for line in lines:
        new_lines.append(line)
        if (count == 0 or inserted < count) and after_pattern in line:
            # Preserve line ending of insert
            to_insert = insert_content if insert_content.endswith("\n") else insert_content + "\n"
            new_lines.append(to_insert)
            inserted += 1

    if inserted == 0:
        return _error("insert_after", f"Pattern not found: '{after_pattern}'",
                      "Use fs_read to verify file contents before inserting.")

    backup = snapshot(str(path))

    if dry_run:
        r: dict = {
            "success": True, "op": "insert_after", "path": str(path),
            "insertions": inserted, "would_change": True, "backup": backup,
            "progress": [info(f"Would insert after {inserted} match(es)")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    atomic_write(path, "".join(new_lines))
    append_receipt(str(path), "fs_write", "insert_after",
                   f"inserted {inserted} block(s)", backup)
    r = {
        "success": True, "op": "insert_after", "path": str(path),
        "insertions": inserted, "backup": backup,
        "progress": [ok(f"Inserted after {inserted} match(es) in {path.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_delete_lines(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"], must_exist=True)
    start: int = int(op_dict["start_line"])
    end: int = int(op_dict["end_line"])

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _error("delete_lines", str(e), "Check file permissions.")

    lines = text.splitlines(keepends=True)
    total = len(lines)
    s = max(0, start)
    e = min(end, total)
    if s >= e:
        return _error("delete_lines",
                      f"Invalid line range [{s}, {e}) for file with {total} lines",
                      "Use fs_read to inspect line numbers before deleting.")

    new_lines = lines[:s] + lines[e:]
    backup = snapshot(str(path))

    if dry_run:
        r: dict = {
            "success": True, "op": "delete_lines", "path": str(path),
            "lines_removed": e - s, "would_change": True, "backup": backup,
            "progress": [info(f"Would delete lines {s}–{e} from {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    atomic_write(path, "".join(new_lines))
    append_receipt(str(path), "fs_write", "delete_lines",
                   f"removed lines {s}–{e}", backup)
    r = {
        "success": True, "op": "delete_lines", "path": str(path),
        "lines_removed": e - s, "backup": backup,
        "progress": [ok(f"Deleted lines {s}–{e} from {path.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_patch_lines(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"], must_exist=True)
    start: int = int(op_dict["start_line"])
    end: int = int(op_dict["end_line"])
    patch: str = op_dict["content"]

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _error("patch_lines", str(e), "Check file permissions.")

    lines = text.splitlines(keepends=True)
    total = len(lines)
    s = max(0, start)
    e = min(end, total)
    if s >= e:
        return _error("patch_lines",
                      f"Invalid line range [{s}, {e}) for file with {total} lines",
                      "Use fs_read to inspect line numbers.")

    patch_lines = patch.splitlines(keepends=True)
    new_lines = lines[:s] + patch_lines + lines[e:]
    backup = snapshot(str(path))

    if dry_run:
        r: dict = {
            "success": True, "op": "patch_lines", "path": str(path),
            "lines_replaced": e - s, "would_change": True, "backup": backup,
            "progress": [info(f"Would patch lines {s}–{e} in {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    atomic_write(path, "".join(new_lines))
    append_receipt(str(path), "fs_write", "patch_lines",
                   f"patched lines {s}–{e}", backup)
    r = {
        "success": True, "op": "patch_lines", "path": str(path),
        "lines_replaced": e - s, "backup": backup,
        "progress": [ok(f"Patched lines {s}–{e} in {path.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_set_permissions(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"], must_exist=True)
    mode_str: str = op_dict["mode"]

    if sys.platform == "win32":
        r: dict = {
            "success": True, "op": "set_permissions", "path": str(path),
            "note": "set_permissions is a no-op on Windows",
            "progress": [warn("set_permissions no-op on Windows")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    try:
        mode_int = int(mode_str, 8)
    except ValueError:
        return _error("set_permissions",
                      f"Invalid octal mode '{mode_str}'",
                      "Provide mode as octal string e.g. '755' or '644'.")

    if dry_run:
        r = {
            "success": True, "op": "set_permissions", "path": str(path),
            "mode": oct(mode_int), "would_change": True,
            "progress": [info(f"Would chmod {mode_str} {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    path.chmod(mode_int)
    r = {
        "success": True, "op": "set_permissions", "path": str(path),
        "mode": oct(mode_int),
        "progress": [ok(f"Set permissions {mode_str} on {path.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_size_kb(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size // 1024
        total = 0
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
        return total // 1024
    except Exception:
        return 0
