"""fs_index implementation — VERIFY via SQLite FTS5 index and receipts.

action=query answers from the SQLite index, which knows only what action=build
put into it. A query against a directory that has never been built therefore
returns `success: true, returned: 0` -- exactly what a directory containing
nothing returns. Round 11's sweep hit this twice and wrote the tool off as
unreliable ("fs_index kept returning 0 matches even after files existed on
disk -- ls confirms real contents"), which is the reasonable reading of a
zero with nothing else in it:

    query  pattern=* path=/workspace/data/ml_advanced_2   -> returned 0
    query  pattern=*                                      -> returned 6
    build  path=/workspace/data/ml_advanced_2             -> indexed 8
    query  pattern=* path=/workspace/data/ml_advanced_2   -> returned 8

Eight files, present the whole time. The empty answer only ever meant "not in
the index", and the response said `returned: 0` with no root, no build time,
and no hint. The stale-index warning that does exist fires at 24 hours, so it
had nothing to say about an index minutes old that simply never covered the
path.

A zero-result query now reports what the index actually holds under the root
it filtered on, so "never indexed" and "nothing matches" stop looking alike.
"""

import sqlite3
import stat as stat_mod
import time
from datetime import UTC, datetime
from pathlib import Path

from _basic_helpers import (
    _error,
    get_max_results,
    info,
    ok,
    read_receipt_log,
    resolve_path,
)

_META_TABLE = "index_meta"
_FILES_TABLE = "files"


def _index_dir() -> Path:
    """Compute index directory at call time for test isolation."""
    return Path.home() / ".mcp_fs_index"


def _db_path() -> Path:
    return _index_dir() / "index.db"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_fs_index(
    action: str = "query",
    path: str = "",
    pattern: str = "",
    max_results: int = 50,
) -> dict:
    try:
        return _fs_index(action, path, pattern, max_results)
    except ValueError as e:
        return _error("fs_index", str(e), "Ensure path is within your home directory.")
    except Exception as e:
        return _error("fs_index", str(e), "Use fs_index with action=stats to check index health.")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _fs_index(action: str, path: str, pattern: str, max_results: int) -> dict:
    if action not in ("build", "query", "list", "stats", "clear", "receipt"):
        return _error(
            "fs_index",
            f"Unknown action '{action}'",
            "Use one of: build, query, list, stats, clear, receipt.",
        )

    if action == "receipt":
        return _action_receipt(path, max_results)
    if action == "build":
        return _action_build(path)
    if action == "query":
        return _action_query(pattern, path, max_results)
    if action == "list":
        return _action_list(path, max_results)
    if action == "stats":
        return _action_stats()
    # clear
    return _action_clear(path)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _get_conn() -> sqlite3.Connection:
    _index_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path()))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_FILES_TABLE} (
            path TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            size INTEGER DEFAULT 0,
            mtime REAL DEFAULT 0,
            type TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_META_TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()
    return conn


def _action_build(path: str) -> dict:
    root = resolve_path(path or str(Path.home()), must_exist=True)
    if not root.is_dir():
        return _error(
            "fs_index",
            f"Not a directory: {root.name}",
            "Provide a directory path to build an index.",
        )

    progress = []
    conn = _get_conn()
    cur = conn.cursor()
    count = 0
    started = time.time()

    progress.append(info(f"Indexing {root.name}"))

    # stat() follows symlinks, so a link was stored as type "file" carrying the
    # size and mtime of whatever it pointed at -- while fs_manage
    # action=symlink_info, in this same server, answers is_symlink: true for the
    # same path. Two tools describing one path two ways, and the one that says
    # "file" is the one a caller reads first. A broken link was worse: stat()
    # raises, and the `continue` dropped it from the index without a word, so a
    # dangling symlink was indistinguishable from a path that is not there.
    #
    # lstat() describes the link itself, and the type says which kind of thing
    # each row is.
    by_type = {"file": 0, "dir": 0, "symlink": 0}
    for p in root.rglob("*"):
        try:
            st = p.lstat()
            if p.is_symlink():
                ftype = "symlink"
            elif stat_mod.S_ISDIR(st.st_mode):
                ftype = "dir"
            else:
                ftype = "file"
            cur.execute(
                f"INSERT OR REPLACE INTO {_FILES_TABLE} (path, name, size, mtime, type) "
                f"VALUES (?, ?, ?, ?, ?)",
                (str(p), p.name, st.st_size, st.st_mtime, ftype),
            )
            by_type[ftype] += 1
            count += 1
        except OSError:
            continue

    ts_now = datetime.now(UTC).isoformat()
    cur.execute(
        f"INSERT OR REPLACE INTO {_META_TABLE} (key, value) VALUES (?, ?)",
        ("last_built", ts_now),
    )
    cur.execute(
        f"INSERT OR REPLACE INTO {_META_TABLE} (key, value) VALUES (?, ?)",
        ("root", str(root)),
    )
    conn.commit()
    conn.close()

    elapsed = round(time.time() - started, 2)
    progress.append(ok(f"Indexed {count} entries", f"{elapsed}s"))

    result: dict = {
        "success": True,
        "op": "fs_index",
        "action": "build",
        "root": str(root),
        "indexed": count,
        # "indexed: 18" was every entry under the root, and stats called the
        # same number file_count. Say what the 18 are made of, once, here.
        "files": by_type["file"],
        "dirs": by_type["dir"],
        "symlinks": by_type["symlink"],
        "last_built": ts_now,
        "elapsed_seconds": elapsed,
        "db_path": str(_db_path()),
        "progress": progress,
    }
    result["token_estimate"] = len(str(result)) // 4
    return result


def _action_list(path: str, max_results: int) -> dict:
    if not _db_path().exists():
        return _error(
            "fs_index",
            "Index not built yet",
            "Run fs_index with action=build to create the index first.",
        )

    effective_max = min(max_results, get_max_results())
    root_filter = ""
    if path:
        try:
            root_filter = str(resolve_path(path))
        except ValueError as e:
            return _error("fs_index", str(e), "Ensure path is within your home directory.")

    conn = _get_conn()
    try:
        if root_filter:
            rows = conn.execute(
                f"SELECT path, name, size, mtime, type FROM {_FILES_TABLE} "
                f"WHERE path LIKE ? ORDER BY path LIMIT ?",
                (root_filter + "%", effective_max + 1),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT path, name, size, mtime, type FROM {_FILES_TABLE} ORDER BY path LIMIT ?",
                (effective_max + 1,),
            ).fetchall()
    finally:
        conn.close()

    truncated = len(rows) > effective_max
    entries = [
        {"path": r[0], "name": r[1], "size": r[2], "mtime": r[3], "type": r[4]}
        for r in rows[:effective_max]
    ]

    result: dict = {
        "success": True,
        "op": "fs_index",
        "action": "list",
        "root": root_filter or str(Path.home()),
        "entries": entries,
        "returned": len(entries),
        "truncated": truncated,
        "progress": [ok(f"Listed {len(entries)} indexed entries")],
    }
    if truncated:
        result["hint"] = (
            f"Results capped at {effective_max}. Use action=query with a pattern to narrow results."
        )
    result["token_estimate"] = len(str(result)) // 4
    return result


def _action_query(pattern: str, path: str, max_results: int) -> dict:
    if not pattern:
        return _error(
            "fs_index",
            "pattern must not be empty for action=query",
            "Provide a filename pattern like '*.py'.",
        )

    effective_max = min(max_results, get_max_results())

    if not _db_path().exists():
        return _error(
            "fs_index",
            "Index not built yet",
            "Run fs_index with action=build to create the index first.",
        )

    root_filter = ""
    if path:
        try:
            root_path = resolve_path(path)
            root_filter = str(root_path)
        except ValueError:
            pass

    conn = _get_conn()
    progress = []
    try:
        # Convert glob pattern to SQL LIKE
        like_pattern = pattern.replace("*", "%").replace("?", "_")
        if root_filter:
            rows = conn.execute(
                f"SELECT path, name, size, mtime, type FROM {_FILES_TABLE} "
                f"WHERE name LIKE ? AND path LIKE ? LIMIT ?",
                (like_pattern, root_filter + "%", effective_max + 1),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT path, name, size, mtime, type FROM {_FILES_TABLE} "
                f"WHERE name LIKE ? LIMIT ?",
                (like_pattern, effective_max + 1),
            ).fetchall()

        # Check index age
        meta_row = conn.execute(
            f"SELECT value FROM {_META_TABLE} WHERE key='last_built'"
        ).fetchone()

        # How much of this root the index covers at all. Fetched here, in the
        # same connection, because it is the number that tells a zero-result
        # query apart from an unindexed one.
        if root_filter:
            covered_row = conn.execute(
                f"SELECT COUNT(*) FROM {_FILES_TABLE} WHERE path LIKE ?",
                (root_filter + "%",),
            ).fetchone()
        else:
            covered_row = conn.execute(f"SELECT COUNT(*) FROM {_FILES_TABLE}").fetchone()
        indexed_under_root = covered_row[0] if covered_row else 0
    finally:
        conn.close()

    truncated = len(rows) > effective_max
    matches = rows[:effective_max]
    index_age_warning = None

    if meta_row:
        try:
            last_built = datetime.fromisoformat(meta_row[0])
            age_hours = (datetime.now(UTC) - last_built).total_seconds() / 3600
            if age_hours > 24:
                index_age_warning = (
                    f"Index is {age_hours:.0f}h old. Run fs_index with action=build to refresh."
                )
        except Exception:
            pass

    match_list = [
        {"path": r[0], "name": r[1], "size": r[2], "mtime": r[3], "type": r[4]} for r in matches
    ]

    progress.append(ok(f"Query matched {len(match_list)} result(s)"))

    result: dict = {
        "success": True,
        "op": "fs_index",
        "action": "query",
        "pattern": pattern,
        # Named for the same reason action=list names it: a filtered answer that
        # does not say what it filtered on cannot be read.
        "root": root_filter or str(Path.home()),
        "matches": match_list,
        "returned": len(match_list),
        "indexed_under_root": indexed_under_root,
        "truncated": truncated,
        "progress": progress,
    }
    if index_age_warning:
        result["index_age_warning"] = index_age_warning
    if truncated:
        result["hint"] = (
            f"Results capped at {effective_max}. Use a narrower pattern or increase max_results."
        )
    elif not match_list:
        built = meta_row[0] if meta_row else "never"
        where = root_filter or str(Path.home())
        if indexed_under_root == 0:
            # The whole point of the fix: this is not "no such file", it is
            # "this subtree was never indexed", and only action=build fixes it.
            result["hint"] = (
                f"Nothing under {where} is in the index, so this is not the same as no such file. "
                f"Run fs_index action=build path={where} to index it, or fs_query to search the "
                f"disk directly. Index last built: {built}."
            )
        else:
            result["hint"] = (
                f"The index holds {indexed_under_root} entr(y/ies) under {where} and none match "
                f"'{pattern}'. Anything written since {built} is not in it — run fs_index "
                f"action=build to refresh."
            )
    result["token_estimate"] = len(str(result)) // 4
    return result


def _action_stats() -> dict:
    if not _db_path().exists():
        result: dict = {
            "success": True,
            "op": "fs_index",
            "action": "stats",
            "built": False,
            "hint": "Run fs_index with action=build to create the index.",
            "progress": [info("Index not yet built")],
        }
        result["token_estimate"] = len(str(result)) // 4
        return result

    conn = _get_conn()
    try:
        count_row = conn.execute(f"SELECT COUNT(*) FROM {_FILES_TABLE}").fetchone()
        type_rows = conn.execute(
            f"SELECT type, COUNT(*) FROM {_FILES_TABLE} GROUP BY type"
        ).fetchall()
        meta_rows = conn.execute(f"SELECT key, value FROM {_META_TABLE}").fetchall()
    finally:
        conn.close()

    meta = {r[0]: r[1] for r in meta_rows}
    entry_count = count_row[0] if count_row else 0
    by_type = {r[0]: r[1] for r in type_rows}
    db_size = _db_path().stat().st_size if _db_path().exists() else 0

    # A cleared index reported built: true beside file_count: 0 and the
    # timestamp of the build whose entries had just been removed -- three
    # answers describing an index that no longer holds anything.
    if not entry_count and not meta.get("last_built"):
        result = {
            "success": True,
            "op": "fs_index",
            "action": "stats",
            "built": False,
            "entry_count": 0,
            "file_count": 0,
            "db_path": str(_db_path()),
            "db_size_bytes": db_size,
            "hint": "Run fs_index with action=build to create the index.",
            "progress": [info("Index is empty")],
        }
        result["token_estimate"] = len(str(result)) // 4
        return result

    result = {
        "success": True,
        "op": "fs_index",
        "action": "stats",
        "built": True,
        # file_count counted every row, directories and symlinks included, under
        # a name that says otherwise. entry_count is that total; file_count now
        # counts files.
        "entry_count": entry_count,
        "file_count": by_type.get("file", 0),
        "dir_count": by_type.get("dir", 0),
        "symlink_count": by_type.get("symlink", 0),
        "last_built": meta.get("last_built"),
        "indexed_root": meta.get("root"),
        "db_path": str(_db_path()),
        "db_size_bytes": db_size,
        "progress": [ok(f"Index has {entry_count} entries")],
    }
    result["token_estimate"] = len(str(result)) // 4
    return result


def _action_clear(path: str) -> dict:
    if not path:
        return _error(
            "fs_index",
            "path required for action=clear",
            "Provide the directory whose index entries should be removed.",
        )

    root = resolve_path(path)

    if not _db_path().exists():
        result: dict = {
            "success": True,
            "op": "fs_index",
            "action": "clear",
            "cleared": 0,
            "note": "Index did not exist",
            "progress": [info("Index not built, nothing to clear")],
        }
        result["token_estimate"] = len(str(result)) // 4
        return result

    conn = _get_conn()
    try:
        cur = conn.execute(
            f"DELETE FROM {_FILES_TABLE} WHERE path LIKE ?",
            (str(root) + "%",),
        )
        removed = cur.rowcount
        left_row = conn.execute(f"SELECT COUNT(*) FROM {_FILES_TABLE}").fetchone()
        remaining = left_row[0] if left_row else 0
        # Clearing the last entries leaves nothing an index could be said to
        # hold, so the build stamp goes with them. Left behind, it made stats
        # answer built: true and last_built: <that build> for an empty table.
        if not remaining:
            conn.execute(f"DELETE FROM {_META_TABLE} WHERE key IN ('last_built', 'root')")
        conn.commit()
    finally:
        conn.close()

    result = {
        "success": True,
        "op": "fs_index",
        "action": "clear",
        "root": str(root),
        "cleared": removed,
        "remaining": remaining,
        "progress": [ok(f"Cleared {removed} entries for {root.name}", f"{remaining} left")],
    }
    result["token_estimate"] = len(str(result)) // 4
    return result


def _action_receipt(path: str, max_results: int) -> dict:
    if not path:
        return _error(
            "fs_index",
            "path required for action=receipt",
            "Provide the file path whose receipt history you want.",
        )

    file_path = resolve_path(path)
    history = read_receipt_log(str(file_path))

    # query and list have always honoured max_results; receipt was the one
    # action that ignored it, so a request for 5 came back with all 14 -- a
    # response contradicting the argument that produced it, and unbounded for a
    # file with a long history.
    effective_max = max(0, min(max_results, get_max_results()))
    shown = history[-effective_max:] if effective_max else []
    truncated = len(shown) < len(history)

    result: dict = {
        "success": True,
        "op": "fs_index",
        "action": "receipt",
        "file": str(file_path),
        "history": shown,
        "count": len(shown),
        "total": len(history),
        "truncated": truncated,
        "progress": [
            ok(
                f"Receipt for {file_path.name}",
                f"{len(shown)} of {len(history)} entries"
                if truncated
                else f"{len(history)} entries",
            )
        ],
    }
    if truncated:
        result["hint"] = (
            f"Showing the {len(shown)} most recent of {len(history)} entries. Raise max_results for more."
        )
    if not history:
        result["hint"] = "No receipt found. Operations via fs_write automatically create receipts."
    result["token_estimate"] = len(str(result)) // 4
    return result
