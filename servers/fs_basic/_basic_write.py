"""fs_write implementation — PATCH files with two-phase deletion gate."""

import base64
import binascii
import re
import shutil
import sys
from pathlib import Path

from _basic_helpers import (
    ALLOWED_OPS,
    MAX_TREE_SNAPSHOT_BYTES,
    _error,
    append_receipt,
    atomic_write,
    atomic_write_bytes,
    attach_public_url,
    carry_receipt,
    carry_snapshots,
    cleanup_expired,
    create_token,
    discard_snapshot_if_unchanged,
    fetch_url,
    info,
    is_url,
    list_versions,
    ok,
    peek_token,
    resolve_path,
    restore_version,
    size_kb,
    snapshot,
    snapshot_tree,
    tree_size,
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
        return _error("fs_write", str(e), "Ensure all paths are within your home directory.")
    except Exception as e:
        return _error(
            "fs_write", str(e), "Check op parameters and retry with a single op to isolate."
        )


# ---------------------------------------------------------------------------
# Core dispatcher
# ---------------------------------------------------------------------------


def _fs_write(ops: list[dict], dry_run: bool) -> dict:
    cleanup_expired()

    # Step 1: structural validation. Report every error, and name the ops the
    # caller may use -- the tool schema is an opaque list[dict], so this message
    # is the only place the vocabulary is discoverable.
    errors = validate_ops(ops)
    if errors:
        # Every validation failure used to be answered with the list of valid op
        # names, including the ones where the op name was fine and a field was
        # the wrong type -- pointing the caller at the one part of the call that
        # was already correct.
        joined = "; ".join(errors)
        if "unknown op" in joined or "missing required key 'op'" in joined:
            hint = f"Valid ops: {', '.join(sorted(ALLOWED_OPS))}"
        else:
            hint = (
                "Correct the field(s) named above and resend. The `ops` schema is "
                "list[dict], so these messages are where each op's fields are described."
            )
        return _error("fs_write", joined, hint)

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


def _wrong_delete_op(op_name: str, path: Path) -> dict | None:
    """Refuse a delete op whose name does not match what is at the path.

    The server advertises four delete ops and ran two handlers. Both request
    names reached the same code, so `delete_request` -- the op named for a
    single file -- resolved a directory, issued a token, and `delete_confirm`
    spent it on `shutil.rmtree`. A caller reading the op table sees a separate
    `delete_tree_request` and reasonably concludes the file op cannot erase a
    tree. It could, recursively, under `success: true`.

    Nothing is being taken away: every deletion still has an op that performs
    it. What changes is that the op name the caller typed has to agree with what
    it is pointed at, and the refusal names the one that does.
    """
    is_dir = path.is_dir()
    if op_name == "delete_request" and is_dir:
        return _error(
            "delete_request",
            f"{path.name} is a directory, and delete_request deletes a single file",
            f"Use op=delete_tree_request to delete {path.name} and everything under it, "
            f"then confirm with op=delete_tree_confirm.",
        )
    if op_name == "delete_tree_request" and not is_dir:
        return _error(
            "delete_tree_request",
            f"{path.name} is a file, and delete_tree_request deletes a directory tree",
            f"Use op=delete_request to delete {path.name}, then confirm with op=delete_confirm.",
        )
    return None


def _file_count(path: Path) -> int:
    """How many files a tree delete would actually destroy."""
    try:
        return sum(1 for p in path.rglob("*") if p.is_file())
    except OSError:
        return 0


def _no_snapshot_reason(path: Path) -> str:
    """Why the confirm step will not be able to keep a copy of this target, or "".

    Asked at request time, because that is when the caller decides. The confirm
    step already takes the snapshot and reports it; by then the answer is no
    longer useful for choosing whether to go ahead.

    Mirrors snapshot()/snapshot_tree(): a file is always copied, a directory is
    zipped unless it is over the tree-snapshot cap. Both can still fail on the
    day for a reason no inspection predicts -- a full disk, a read-only parent --
    so the confirm response stays the record of what was actually kept, and this
    is only ever consulted for the sentence shown beforehand.
    """
    if not path.is_dir():
        return ""
    try:
        size = tree_size(path)
    except OSError as exc:  # unreadable subtree; do not promise a copy
        return str(exc)
    if size > MAX_TREE_SNAPSHOT_BYTES:
        return (
            f"{size // (1024 * 1024)} MB exceeds the "
            f"{MAX_TREE_SNAPSHOT_BYTES // (1024 * 1024)} MB tree-snapshot limit"
        )
    return ""


def _delete_warning(scope: str, total_size_kb: float, n: int, unbacked: list[str]) -> str:
    """The sentence the caller decides on. It has to be true.

    It used to read "Permanently deletes N item(s) (X KB). Cannot be undone."
    for every delete on this server -- while the confirm step snapshotted each
    file and zipped each tree into .mcp_versions/ first, which an earlier round
    added precisely so a recursive delete had a way back.

    So the most consequential sentence the server prints was false, and false in
    both directions at once: a caller deleting something sensitive was told the
    bytes were gone when a full copy remained beside them, and a caller who
    needed the file was scared off an operation that was reversible all along.
    Neither is a caller reading it wrong.
    """
    head = f"Deletes {scope} ({total_size_kb} KB)."
    if not unbacked:
        kept = "A copy is kept" if n == 1 else "A copy of each is kept"
        return (
            f"{head} {kept} under .mcp_versions/ first, so this can be undone -- "
            "fs_manage action=versions lists them."
        )
    if len(unbacked) == 1:
        which = f"No copy is kept of {unbacked[0]}"
    else:
        which = f"No copy is kept of {len(unbacked)} of them ({', '.join(unbacked)})"
    # Only mention the others when there are some: "anything else is copied"
    # beside a single unrecoverable target reads as though a copy exists.
    rest = "" if len(unbacked) == n else " Anything else is copied under .mcp_versions/ first."
    return f"{head} {which}, so that part cannot be undone.{rest}"


def _handle_delete_request(delete_ops: list[dict], dry_run: bool) -> dict:
    targets: list[dict] = []
    progress: list[dict] = []
    total_size_kb = 0
    total_files = 0
    tree_requested = False
    unbacked: list[str] = []

    for op_dict in delete_ops:
        path_str = op_dict["path"]
        op_name = str(op_dict.get("op", "delete_request"))
        try:
            path = resolve_path(path_str, must_exist=True)
        except (ValueError, FileNotFoundError) as e:
            return _error(
                "fs_write", str(e), "Verify the path exists and is within your home directory."
            )
        mismatch = _wrong_delete_op(op_name, path)
        if mismatch:
            return mismatch
        size_kb = _get_size_kb(path)
        total_size_kb += size_kb
        is_dir = path.is_dir()
        tree_requested = tree_requested or is_dir
        files = _file_count(path) if is_dir else 1
        total_files += files
        t = {
            "path": str(path),
            "size_kb": size_kb,
            "type": "directory" if is_dir else "file",
        }
        if is_dir:
            # "Permanently deletes 1 item(s)" for a directory counted the
            # argument, not the damage: a tree of forty files read as one.
            t["files"] = files
        why = _no_snapshot_reason(path)
        t["recoverable"] = not why
        if why:
            t["no_snapshot_reason"] = why
            unbacked.append(path.name)
            progress.append(warn(f"No copy will be kept of {path.name}", why))
        targets.append(t)
        detail = f"{size_kb} KB, {files} file(s)" if is_dir else f"{size_kb} KB"
        progress.append(info(f"Located {path.name}", detail))

    confirm_op = "delete_tree_confirm" if tree_requested else "delete_confirm"
    n = len(targets)
    scope = f"{n} item(s)"
    if total_files != n:
        scope = f"{n} item(s) holding {total_files} file(s)"
    warning = _delete_warning(scope, total_size_kb, n, unbacked)

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

    token, superseded = create_token(targets, confirm_op)
    result = {
        "success": True,
        "op": "delete_pending",
        "pending": True,
        "confirmation_token": token,
        "expires_in_seconds": 300,
        "targets": targets,
        "total_size_kb": total_size_kb,
        "warning": warning,
        # A tree request's next_step named delete_confirm -- the file op --
        # so following the instructions in the response taught the wrong half
        # of the vocabulary the tool advertises.
        "confirm_op": confirm_op,
        "next_step": (f"Call fs_write with op={confirm_op} and token={token} to proceed."),
        "progress": progress,
    }
    # Asking twice for the same targets is one intent, not two. Say which token
    # stopped working, so a caller holding the earlier one is not left to
    # discover it at confirm time.
    if superseded:
        result["superseded_tokens"] = superseded
        progress.append(warn(f"Replaced {len(superseded)} earlier request(s) for the same targets"))
    result["token_estimate"] = len(str(result)) // 4
    return result


def _op_delete_confirm(op_dict: dict, dry_run: bool) -> dict:
    token = op_dict["token"]
    # The op the caller actually typed. Both confirm names route here, and this
    # function used to answer "op": "delete_confirm" whichever was called, so
    # delete_tree_confirm reported itself as an op the caller had not used.
    called_as = str(op_dict.get("op", "delete_confirm"))

    pending = peek_token(token)
    if pending is None:
        return _error(
            called_as,
            "Invalid or expired confirmation token",
            "Use fs_write with op=delete_request to request a new confirmation token.",
        )

    wants = str(pending.get("confirm_op", "delete_confirm"))
    if wants != called_as:
        # Refused before validate_token consumes it, so the retry this hint
        # names still has a token to spend.
        kind = "a directory tree" if wants == "delete_tree_confirm" else "a single file"
        return _error(
            called_as,
            f"Token {token} was issued for {kind}, which {wants} confirms",
            f"Call fs_write with op={wants} and token={token}. The token is still valid.",
        )

    entry = validate_token(token)
    if entry is None:  # pragma: no cover - expired between peek and validate
        return _error(
            called_as,
            "Invalid or expired confirmation token",
            "Use fs_write with op=delete_request to request a new confirmation token.",
        )

    targets = entry["targets"]
    deleted: list[str] = []
    backups: list[str] = []
    progress: list[dict] = []

    skipped: list[str] = []
    unbacked: list[str] = []

    for t in targets:
        p = Path(t["path"])
        if not p.exists():
            skipped.append(str(p))
            progress.append(warn(f"{p.name} already gone, skipping"))
            continue
        # `snapshot()` returns "" for anything that is not a file, so a tree
        # delete used to run rmtree with no backup at all while a single-file
        # delete snapshotted its victim first -- the most destructive op on the
        # server was the only one with no way back.
        if p.is_dir():
            backup, why = snapshot_tree(str(p))
            if not backup:
                unbacked.append(p.name)
                progress.append(warn(f"No snapshot of {p.name}: {why}"))
        else:
            backup = snapshot(str(p))
        if backup:
            backups.append(backup)
        if not dry_run:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            deleted.append(str(p))
            append_receipt(str(p), "fs_write", called_as, "deleted", backup)
            progress.append(ok(f"Deleted {p.name}", f"backup={backup}"))

    result: dict = {
        "success": True,
        "op": called_as,
        "deleted": deleted,
        "backup": backups[0] if backups else None,
        "backups": backups,
        "progress": progress,
    }
    # A confirm whose targets had all vanished answered success: true with an
    # empty `deleted` list and nothing else -- the flag said the delete happened
    # and the list said it did not. Name what was skipped, and what went without
    # a snapshot, in the response rather than only in the progress log.
    if skipped:
        result["skipped"] = skipped
    if unbacked:
        result["deleted_without_snapshot"] = unbacked
        result["hint"] = (
            f"No snapshot was taken of {', '.join(unbacked)} — that delete cannot be undone."
        )
    if not deleted and skipped:
        result["hint"] = "Nothing was deleted: every target was already gone."
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
        "download": _op_download,
        "restore": _op_restore,
    }
    handler = handlers.get(name)
    if not handler:
        return _error(
            "fs_write", f"Unhandled op: {name}", "Use a supported op from the fs_write op table."
        )
    try:
        result = handler(op_dict, dry_run)
        # A remote caller shares no filesystem with this server, so the path it
        # just got back means nothing to it. When the file landed under a
        # publicly served MCP_OUTPUT_DIR, hand back a URL it can actually use.
        # move and copy report their destination as `dst`; rename reports it as
        # `new_path` and keeps `path` as the name the file had *before*. Reading
        # `path` there hands back a URL for a file that no longer exists.
        target = result.get("dst") or result.get("new_path") or result.get("path")

        # The snapshot is taken before the write, because nothing knows yet
        # whether the write will change anything. Three identical write_file
        # calls left two backups, both byte-identical to the live file. Checked
        # here rather than in each handler so every op gets it, and checked
        # after the fact so it is exact: a backup equal to the file now on disk
        # cannot restore anything the file does not already hold. A delete's
        # backup survives -- its file is gone, so there is nothing to compare
        # it against and the helper keeps it.
        if result.get("success") and not dry_run and target and result.get("backup"):
            kept = discard_snapshot_if_unchanged(result["backup"], target)
            if not kept:
                result["backup"] = None
                result.setdefault("progress", []).append(
                    info("Snapshot discarded", "the file is unchanged")
                )
                result["token_estimate"] = len(str(result)) // 4

        if result.get("success") and target:
            before = len(result)
            attach_public_url(result, Path(target))
            if len(result) != before:
                result["token_estimate"] = len(str(result)) // 4
        return result
    except ValueError as e:
        return _error(name, str(e), "Ensure path is within your home directory.")
    except PermissionError as e:
        return _error(name, f"Permission denied: {e}", "Check file/directory permissions.")
    except FileNotFoundError as e:
        return _error(name, str(e), _missing_source_hint(name, op_dict))
    except Exception as e:
        return _error(name, str(e), f"Retry op={name} with corrected parameters.")


def _already_applied(name: str, op_dict: dict) -> Path | None:
    """Where this op would have put the file, if it already ran."""
    try:
        if name in ("move", "copy"):
            return resolve_path(str(op_dict.get("dst", "")))
        if name == "rename":
            src = Path(str(op_dict.get("path", ""))).expanduser()
            new_name = str(op_dict.get("name", ""))
            if not new_name or "/" in new_name or "\\" in new_name:
                return None
            return resolve_path(str(src.parent / new_name))
    except Exception:
        return None
    return None


def _missing_source_hint(name: str, op_dict: dict) -> str:
    """Say whether the op looks already-applied rather than simply wrong.

    move, rename and copy resolve their source with must_exist=True, so a client
    retrying one whose first attempt timed out gets "Path does not exist" and the
    generic hint "Retry op=move with corrected parameters." That advice is wrong
    twice: retrying will not help, and the move already succeeded. The caller
    cannot tell "already done" from "never valid" and the hint pushes it toward
    the one action guaranteed to fail again.

    Round 11's sweep hit this on the second identical call to move, rename and
    replace_text within one phase.
    """
    landed = _already_applied(name, op_dict)
    if landed is not None and landed.exists():
        return (
            f"{landed.name} already exists at the destination, so this {name} looks like it "
            f"already ran — a retry of a call that timed out. Read it with fs_read before "
            f"repeating the op."
        )
    return f"Nothing exists at the source. Check it with fs_read mode=meta before retrying {name}."


# ---------------------------------------------------------------------------
# Individual op implementations
# ---------------------------------------------------------------------------


def _op_write_file(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"])
    content: str = op_dict["content"]
    encoding = op_dict.get("content_encoding", "text")
    if encoding not in ("text", "base64"):
        raise ValueError(f"content_encoding must be 'text' or 'base64', got {encoding!r}")
    binary: bytes | None = None
    if encoding == "base64":
        try:
            binary = base64.b64decode(content, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"content is not valid base64: {exc}") from exc
    # The dry-run answer comes before the snapshot, not after. A dry run's whole
    # contract is that it changes nothing, and seven of these ops snapshotted
    # first -- so previewing three ops on one file left three full copies of it
    # in .mcp_versions, and the version list filled with snapshots of writes that
    # never happened.
    if dry_run:
        r: dict = {
            "success": True,
            "op": "write_file",
            "path": str(path),
            "would_change": True,
            "backup": None,
            "progress": [info(f"Would write {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    backup: str | None = None
    if path.exists():
        backup = snapshot(str(path))

    if binary is not None:
        atomic_write_bytes(path, binary)
    else:
        atomic_write(path, content)
    append_receipt(
        str(path), "fs_write", "write_file", "created" if not backup else "overwritten", backup
    )
    r = {
        "success": True,
        "op": "write_file",
        "path": str(path),
        "backup": backup,
        "progress": [ok(f"Wrote {path.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_download(op_dict: dict, dry_run: bool) -> dict:
    """Fetch an http(s) URL and save it at `path`.

    The one place this server treats a URL as an input. It is deliberately an
    explicit op rather than URL support inside resolve_path(): every other
    fs_write op takes `path` as a *destination*, and silently downloading a
    destination would be nonsense. Requires MCP_FETCH_URLS=1 on the server.
    """
    url = str(op_dict["url"]).strip()
    if not is_url(url):
        raise ValueError(f"'url' must be an http:// or https:// URL, got {url!r}")
    path = resolve_path(op_dict["path"])

    if dry_run:
        r: dict = {
            "success": True,
            "op": "download",
            "path": str(path),
            "url": url,
            "would_change": True,
            "backup": None,
            "progress": [info(f"Would download {url} to {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    backup: str | None = None
    if path.exists():
        backup = snapshot(str(path))

    payload = fetch_url(url).read_bytes()
    atomic_write_bytes(path, payload)
    append_receipt(str(path), "fs_write", "download", f"downloaded {len(payload)} bytes", backup)
    r = {
        "success": True,
        "op": "download",
        "path": str(path),
        "url": url,
        "bytes": len(payload),
        "backup": backup,
        "progress": [ok(f"Downloaded {path.name}", f"{len(payload):,} bytes")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_append_file(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"])
    content: str = op_dict["content"]

    if dry_run:
        r: dict = {
            "success": True,
            "op": "append_file",
            "path": str(path),
            "would_change": True,
            "progress": [info(f"Would append to {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    # Every other content op here snapshots first -- write_file, replace_text,
    # insert_after, delete_lines, patch_lines, download -- and append_file was
    # the one that did not, while declaring a `backup` field that was always
    # None. Append is the least reversible op to leave without one: it is the
    # canonical non-idempotent write, so a client retrying a call that timed out
    # doubles the text, and nothing records how long the file was before.
    # Appending to a file that does not exist yet has nothing to preserve.
    backup = snapshot(str(path)) if path.is_file() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(content)
    append_receipt(str(path), "fs_write", "append_file", "appended", backup or None)
    r = {
        "success": True,
        "op": "append_file",
        "path": str(path),
        # Same reason as delete_lines: the response was identical on a retry
        # ("Appended to af_test.txt" both times) while the text landed twice.
        # The resulting size is what tells the two calls apart.
        "size_bytes": path.stat().st_size,
        "backup": backup or None,
        "progress": [ok(f"Appended to {path.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_restore(op_dict: dict, dry_run: bool) -> dict:
    """Put a snapshot back over the live file.

    Every destructive op here snapshots first, `fs_manage action=versions` lists
    what it took, and every empty listing ends "Snapshots are created
    automatically on destructive writes" -- but nothing could use one.
    `restore_version` sat in shared/version_control.py with no caller outside
    the tests, so this server took the snapshots and offered no way back. All
    three sibling repos expose a restore.

    With no timestamp, the newest snapshot is used, which is what a caller
    undoing the write they just made wants. The live file is snapshotted first,
    so a restore is itself undoable -- the same counter-snapshot the sibling
    repos take.
    """
    path = resolve_path(op_dict["path"])
    timestamp = str(op_dict.get("timestamp", "")).strip()

    if not path.is_file():
        return _error(
            "restore",
            f"File not found: {path.name}",
            "Restore writes over a file that exists. Check the path.",
        )

    available = list_versions(str(path))
    if not available:
        return _error(
            "restore",
            f"No snapshots found for {path.name}",
            "Use fs_manage with action=versions to see what a file has.",
        )
    if not timestamp:
        timestamp = available[-1]["timestamp"]

    if dry_run:
        r: dict = {
            "success": True,
            "op": "restore",
            "path": str(path),
            "timestamp": timestamp,
            "would_change": True,
            "progress": [info(f"Would restore {path.name} from {timestamp}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    counter = snapshot(str(path))
    result = restore_version(str(path), timestamp)
    if not result.get("success"):
        return _error(
            "restore",
            str(result.get("error", "restore failed")),
            f"Available timestamps: {', '.join(v['timestamp'] for v in available[-5:])}",
        )

    append_receipt(str(path), "fs_write", "restore", f"restored from {timestamp}", counter or None)
    r = {
        "success": True,
        "op": "restore",
        "path": str(path),
        "timestamp": timestamp,
        "restored_from": result.get("from_backup", ""),
        "backup": counter or None,
        "progress": [
            info("Counter-snapshot taken", Path(counter).name if counter else ""),
            ok(f"Restored {path.name}", timestamp),
        ],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_create_dir(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"])

    if dry_run:
        r: dict = {
            "success": True,
            "op": "create_dir",
            "path": str(path),
            "would_change": not path.exists(),
            "progress": [info(f"Would create dir {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    path.mkdir(parents=True, exist_ok=True)
    r = {
        "success": True,
        "op": "create_dir",
        "path": str(path),
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
            "success": True,
            "op": "move",
            "src": str(src),
            "dst": str(dst),
            "would_change": True,
            "progress": [info(f"Would move {src.name} → {dst.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dst)
    # Same detachment as rename: the destination started a fresh history whose
    # only entry was the move itself, while every snapshot stayed behind in the
    # source directory's .mcp_versions.
    carried = carry_snapshots(str(src), str(dst))
    carry_receipt(str(src), str(dst))
    append_receipt(str(dst), "fs_write", "move", f"moved from {src}", None)
    progress = [ok(f"Moved {src.name} → {dst.name}")]
    if carried:
        progress.append(info(f"Carried {carried} snapshot(s) to the new location"))
    r = {
        "success": True,
        "op": "move",
        "src": str(src),
        "dst": str(dst),
        "backup": None,
        "snapshots_carried": carried,
        "progress": progress,
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_copy(op_dict: dict, dry_run: bool) -> dict:
    src = resolve_path(op_dict["src"], must_exist=True)
    dst = resolve_path(op_dict["dst"])
    if dry_run:
        r: dict = {
            "success": True,
            "op": "copy",
            "src": str(src),
            "dst": str(dst),
            "would_change": True,
            "backup": None,
            "progress": [info(f"Would copy {src.name} → {dst.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    backup: str | None = None
    if dst.exists():
        backup = snapshot(str(dst))

    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(str(src), dst)
    else:
        shutil.copy2(src, dst)
    append_receipt(str(dst), "fs_write", "copy", f"copied from {src}", backup)
    r = {
        "success": True,
        "op": "copy",
        "src": str(src),
        "dst": str(dst),
        "backup": backup,
        "progress": [ok(f"Copied {src.name} → {dst.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_rename(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"], must_exist=True)
    new_name: str = op_dict["name"]
    if "/" in new_name or "\\" in new_name:
        return _error(
            "rename",
            "name must not contain path separators",
            "Use op=move to move across directories.",
        )
    dst = path.parent / new_name

    if dry_run:
        r: dict = {
            "success": True,
            "op": "rename",
            "path": str(path),
            "new_path": str(dst),
            "would_change": True,
            "progress": [info(f"Would rename {path.name} → {new_name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    path.rename(dst)
    # Snapshots and the receipt log are both named after the file, so without
    # this a rename silently detaches the file's whole history: fs_manage
    # action=versions went from 1 to 0 with success:true, and no receipt
    # recorded the rename either.
    carried = carry_snapshots(str(path), str(dst))
    carry_receipt(str(path), str(dst))
    append_receipt(str(dst), "fs_write", "rename", f"renamed from {path.name}", None)
    progress = [ok(f"Renamed {path.name} → {new_name}")]
    if carried:
        progress.append(info(f"Carried {carried} snapshot(s) to the new name"))
    r = {
        "success": True,
        "op": "rename",
        "path": str(path),
        "new_path": str(dst),
        "snapshots_carried": carried,
        "progress": progress,
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
        content = path.open(encoding="utf-8", errors="replace", newline="").read()
    except Exception as e:
        return _error("replace_text", str(e), "Check file permissions.")

    if use_regex:
        try:
            new_content, n = re.subn(find, replace, content, count=count)
        except re.error as e:
            return _error(
                "replace_text", f"Invalid regex: {e}", "Fix the regex in the 'find' parameter."
            )
    else:
        occurrences = content.count(find)
        n = min(occurrences, count) if count else occurrences
        new_content = content.replace(find, replace, count if count else -1)

    if n == 0:
        return _error(
            "replace_text",
            f"Pattern not found in {path.name}",
            "Use fs_read to verify the file content and pattern.",
        )

    if dry_run:
        r: dict = {
            "success": True,
            "op": "replace_text",
            "path": str(path),
            "would_replace": n,
            "would_change": True,
            "backup": None,
            "progress": [info(f"Would replace {n} occurrence(s) in {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    backup = snapshot(str(path))

    atomic_write(path, new_content)
    append_receipt(str(path), "fs_write", "replace_text", f"replaced {n} occurrences", backup)
    r = {
        "success": True,
        "op": "replace_text",
        "path": str(path),
        "replacements": n,
        "backup": backup,
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
        text = path.open(encoding="utf-8", errors="replace", newline="").read()
    except Exception as e:
        return _error("insert_after", str(e), "Check file permissions.")

    lines = text.splitlines(keepends=True)
    new_lines: list[str] = []
    inserted = 0
    for line in lines:
        hit = (count == 0 or inserted < count) and after_pattern in line
        # Inserting after a line means that line now has something below it, so
        # it has to end. Only the final line of a file that ends without a
        # newline can be unterminated, and anchoring there welded the two
        # together -- "appended line twoinserted after anchor", two lines where
        # the response reported three, under success: true. patch_lines was
        # taught to produce something that is a line; this is the same rule for
        # the line it is inserted after.
        anchor_ended_the_file = hit and not line.endswith(("\n", "\r"))
        if anchor_ended_the_file:
            line += "\n"
        new_lines.append(line)
        if hit:
            to_insert = insert_content
            if anchor_ended_the_file:
                # The file's missing final newline moves down to the block that
                # is now last, so a file that ended without one still does.
                to_insert = to_insert[:-1] if to_insert.endswith("\n") else to_insert
            elif not to_insert.endswith("\n"):
                to_insert += "\n"
            new_lines.append(to_insert)
            inserted += 1

    if inserted == 0:
        return _error(
            "insert_after",
            f"Pattern not found: '{after_pattern}'",
            "Use fs_read to verify file contents before inserting.",
        )

    if dry_run:
        r: dict = {
            "success": True,
            "op": "insert_after",
            "path": str(path),
            "insertions": inserted,
            "would_change": True,
            "backup": None,
            "progress": [info(f"Would insert after {inserted} match(es)")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    backup = snapshot(str(path))

    atomic_write(path, "".join(new_lines))
    append_receipt(str(path), "fs_write", "insert_after", f"inserted {inserted} block(s)", backup)
    r = {
        "success": True,
        "op": "insert_after",
        "path": str(path),
        "insertions": inserted,
        "total_lines": len(new_lines),
        "backup": backup,
        "progress": [ok(f"Inserted after {inserted} match(es) in {path.name}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


_LINE_RANGE_CONVENTION = (
    "Line numbers are 0-based and end_line is exclusive, so start_line=4, "
    "end_line=5 is the fifth line on its own."
)


def _line_range_error(op: str, start: int, end: int, total: int) -> dict | None:
    """Reject an unusable [start_line, end_line) range, naming the part that is wrong.

    The previous message clamped both bounds before printing them and then blamed
    the file's length for every case: start_line=5, end_line=5 on a six-line file
    came back as "Invalid line range [5, 5) for file with 6 lines", pointing the
    caller at the one number that was fine, under a hint telling them to go and
    read the line numbers they already had. It also printed the clamped values
    rather than the ones the caller sent, so start_line=-5 was quoted back as 0.

    The end-exclusive convention had no other home: fs_write's docstring is 66
    characters and its `ops` schema is an opaque list[dict], so this message is
    where a caller learns it — which meant learning it by failing first.
    """
    if start < 0:
        return _error(
            op,
            f"start_line {start} is negative",
            f"{_LINE_RANGE_CONVENTION} The first line is start_line=0.",
        )
    if start >= total:
        last = total - 1 if total else 0
        return _error(
            op,
            f"start_line {start} is past the end of a {total}-line file",
            f"{_LINE_RANGE_CONVENTION} The last line is start_line={last}. "
            "Use fs_read to see the current line numbers.",
        )
    if end <= start:
        detail = (
            f"start_line and end_line are both {start}, which selects nothing"
            if end == start
            else f"end_line {end} is not greater than start_line {start}"
        )
        return _error(
            op,
            f"Empty line range: {detail}",
            f"{_LINE_RANGE_CONVENTION} For line {start} alone, pass "
            f"start_line={start}, end_line={start + 1}.",
        )
    return None


def _op_delete_lines(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"], must_exist=True)
    start: int = int(op_dict["start_line"])
    end: int = int(op_dict["end_line"])

    try:
        text = path.open(encoding="utf-8", errors="replace", newline="").read()
    except Exception as e:
        return _error("delete_lines", str(e), "Check file permissions.")

    lines = text.splitlines(keepends=True)
    total = len(lines)
    bad = _line_range_error("delete_lines", start, end, total)
    if bad:
        return bad
    s = start
    e = min(end, total)

    new_lines = lines[:s] + lines[e:]

    if dry_run:
        r: dict = {
            "success": True,
            "op": "delete_lines",
            "path": str(path),
            "lines_removed": e - s,
            "would_change": True,
            "backup": None,
            # start_line/end_line are 0-based and end-exclusive, so "lines 2-3"
            # reads as two lines and removes one. Say the count, which is what
            # the caller can actually check against the file.
            "progress": [
                info(f"Would delete {e - s} line(s) from {path.name}", f"lines [{s}, {e})")
            ],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    backup = snapshot(str(path))
    atomic_write(path, "".join(new_lines))
    append_receipt(
        str(path), "fs_write", "delete_lines", f"removed {e - s} line(s) at [{s}, {e})", backup
    )
    r = {
        "success": True,
        "op": "delete_lines",
        "path": str(path),
        "lines_removed": e - s,
        # The delta alone cannot tell two calls apart. A retried delete_lines
        # removes a *second* line and answers lines_removed: 1 both times, so a
        # client re-sending a call that timed out has no way to see that it
        # destroyed different content. The resulting count differs, and is what
        # a caller can check against what it expected.
        "total_lines": len(new_lines),
        "backup": backup,
        "progress": [ok(f"Deleted {e - s} line(s) from {path.name}", f"lines [{s}, {e})")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_patch_lines(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"], must_exist=True)
    start: int = int(op_dict["start_line"])
    end: int = int(op_dict["end_line"])
    patch: str = op_dict["content"]

    try:
        text = path.open(encoding="utf-8", errors="replace", newline="").read()
    except Exception as e:
        return _error("patch_lines", str(e), "Check file permissions.")

    lines = text.splitlines(keepends=True)
    total = len(lines)
    bad = _line_range_error("patch_lines", start, end, total)
    if bad:
        return bad
    s = start
    e = min(end, total)

    # patch_lines replaces lines, so its replacement has to end a line. Without
    # this, content given as "PATCHED" merged into whatever followed --
    # "line one\nPATCHEDline three\n", three lines silently becoming two under
    # success: true. insert_after has always terminated its own content this
    # way; this was the sibling that did not.
    #
    # The terminator is copied from the region being replaced rather than added
    # unconditionally, so patching the final line of a file that ends without a
    # newline does not quietly give it one.
    patch_lines = patch.splitlines(keepends=True)
    replaced_ends_line = bool(lines[e - 1].endswith(("\n", "\r"))) if e > s else False
    if patch_lines and replaced_ends_line and not patch_lines[-1].endswith(("\n", "\r")):
        patch_lines[-1] += "\n"
    new_lines = lines[:s] + patch_lines + lines[e:]

    if dry_run:
        r: dict = {
            "success": True,
            "op": "patch_lines",
            "path": str(path),
            "lines_replaced": e - s,
            "lines_written": len(patch_lines),
            "would_change": True,
            "backup": None,
            "progress": [
                info(
                    f"Would replace {e - s} line(s) with {len(patch_lines)} in {path.name}",
                    f"lines [{s}, {e})",
                )
            ],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    backup = snapshot(str(path))
    atomic_write(path, "".join(new_lines))
    append_receipt(
        str(path),
        "fs_write",
        "patch_lines",
        f"replaced {e - s} line(s) at [{s}, {e}) with {len(patch_lines)}",
        backup,
    )
    r = {
        "success": True,
        "op": "patch_lines",
        "path": str(path),
        "lines_replaced": e - s,
        # end_line is exclusive, so "Patched lines 1–2" reads as two lines and
        # replaced one -- the same message delete_lines was already fixed to
        # stop printing. lines_replaced counts what was removed; the caller
        # wrote a different number of lines and nothing said how many.
        "lines_written": len(patch_lines),
        "total_lines": len(new_lines),
        "backup": backup,
        "progress": [
            ok(
                f"Replaced {e - s} line(s) with {len(patch_lines)} in {path.name}",
                f"lines [{s}, {e})",
            )
        ],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


def _op_set_permissions(op_dict: dict, dry_run: bool) -> dict:
    path = resolve_path(op_dict["path"], must_exist=True)
    mode_str: str = op_dict["mode"]

    if sys.platform == "win32":
        r: dict = {
            "success": True,
            "op": "set_permissions",
            "path": str(path),
            "note": "set_permissions is a no-op on Windows",
            "progress": [warn("set_permissions no-op on Windows")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    try:
        mode_int = int(mode_str, 8)
    except ValueError:
        return _error(
            "set_permissions",
            f"Invalid octal mode '{mode_str}'",
            "Provide mode as octal string e.g. '755' or '644'.",
        )

    if dry_run:
        r = {
            "success": True,
            "op": "set_permissions",
            "path": str(path),
            "mode": oct(mode_int),
            "would_change": True,
            "progress": [info(f"Would chmod {mode_str} {path.name}")],
        }
        r["token_estimate"] = len(str(r)) // 4
        return r

    before = oct(path.stat().st_mode & 0o777)
    path.chmod(mode_int)
    # Who can read a file is the change most worth being able to look up later,
    # and it was the one write op that recorded nothing at all.
    append_receipt(
        str(path), "fs_write", "set_permissions", f"mode {before} → {oct(mode_int)}", None
    )
    r = {
        "success": True,
        "op": "set_permissions",
        "path": str(path),
        "mode": oct(mode_int),
        "mode_before": before,
        "progress": [ok(f"Set permissions {mode_str} on {path.name}", f"was {before}")],
    }
    r["token_estimate"] = len(str(r)) // 4
    return r


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_size_kb(path: Path) -> float:
    try:
        if path.is_file():
            return size_kb(path.stat().st_size)
        total = 0
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
        return size_kb(total)
    except Exception:
        return 0.0
