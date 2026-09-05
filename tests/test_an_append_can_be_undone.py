"""Appending to a file must leave a way back, like every other write here.

An AST census of the thirteen `_op_*` functions in `_basic_write.py`:

    snapshot   write_file  download  copy  move  rename  replace_text
               insert_after  delete_lines  patch_lines  delete_confirm
       --      append_file
       --      create_dir        (no file content)
       --      set_permissions   (no file content)

`append_file` was the one content op with no snapshot, and it declared a
`backup` field in its response that was always None. It is also the least
reversible op to leave without one: append is the canonical non-idempotent
write, so a client retrying a call whose first attempt timed out doubles the
text, and nothing recorded how long the file had been before either call.

Found by round 11's axis -- call every tool twice with identical arguments.
Four concurrent identical `append_file` calls against the live endpoint all
succeeded and all four landed, 289 bytes to 305, with `.mcp_versions` empty.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402

from shared import version_control as vc  # noqa: E402


def write(op: str, **kw) -> dict:
    outer = engine.fs_write([dict(op=op, **kw)])
    assert outer["success"] is True, outer
    return outer["results"][0]


class TestAnAppendIsSnapshotted:
    def test_the_file_as_it_was_is_recoverable(self, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("first\n", encoding="utf-8")
        r = write("append_file", path=str(f), content="second\n")
        assert r["backup"], "append_file reported no backup"
        assert Path(r["backup"]).read_text(encoding="utf-8") == "first\n"
        assert f.read_text(encoding="utf-8") == "first\nsecond\n"

    def test_a_repeated_append_can_be_rolled_back(self, tmp_path):
        # The retry case: the same call twice doubles the text, and the second
        # call's snapshot is the state to come back to.
        f = tmp_path / "log.txt"
        f.write_text("first\n", encoding="utf-8")
        write("append_file", path=str(f), content="second\n")
        write("append_file", path=str(f), content="second\n")
        assert f.read_text(encoding="utf-8") == "first\nsecond\nsecond\n"

        newest = vc.list_versions(str(f))[-1]
        assert vc.restore_version(str(f), newest["timestamp"])["success"] is True
        assert f.read_text(encoding="utf-8") == "first\nsecond\n"

    def test_appending_to_a_new_file_snapshots_nothing(self, tmp_path):
        f = tmp_path / "fresh.txt"
        r = write("append_file", path=str(f), content="only\n")
        assert r["backup"] is None
        assert not (tmp_path / ".mcp_versions").exists()
        assert f.read_text(encoding="utf-8") == "only\n"

    def test_the_receipt_names_the_backup(self, tmp_path):

        f = tmp_path / "log.txt"
        f.write_text("first\n", encoding="utf-8")
        write("append_file", path=str(f), content="second\n")
        # Through the reader: parsing the file here made the test a second
        # implementation of the storage format, and it broke when that format
        # grew a scope header.
        from shared.receipt import read_receipt_log

        entries = read_receipt_log(str(f))
        assert entries[-1].get("backup"), entries[-1]

    def test_a_dry_run_still_writes_nothing(self, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("first\n", encoding="utf-8")
        engine.fs_write([{"op": "append_file", "path": str(f), "content": "no\n"}], dry_run=True)
        assert f.read_text(encoding="utf-8") == "first\n"
        assert not (tmp_path / ".mcp_versions").exists()


class TestASnapshotCanBePutBack:
    """The snapshots were taken, listed, and unusable.

    Every destructive op takes one, `fs_manage action=versions` lists them, and
    every empty listing ends "Snapshots are created automatically on destructive
    writes" -- but `restore_version` in shared/version_control.py had no caller
    anywhere outside the tests. All three sibling repos expose a restore; this
    server took the snapshots and offered no way back.

    It goes on fs_write, not fs_manage: fs_manage declares readOnlyHint, and a
    restore is a write.
    """

    def test_the_newest_snapshot_comes_back_with_no_timestamp(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("keep me\n", encoding="utf-8")
        write("replace_text", path=str(f), find="keep me", replace="oops")
        assert f.read_text(encoding="utf-8") == "oops\n"

        r = write("restore", path=str(f))
        assert r["success"] is True, r.get("error")
        assert f.read_text(encoding="utf-8") == "keep me\n"

    def test_a_listed_timestamp_can_be_sent_straight_back(self, tmp_path):
        # The listing must carry the key the restore matches on: without it a
        # caller has to parse the stamp back out of a filename.
        f = tmp_path / "notes.txt"
        f.write_text("one\n", encoding="utf-8")
        write("replace_text", path=str(f), find="one", replace="two")
        write("replace_text", path=str(f), find="two", replace="three")

        listed = engine.fs_manage("versions", str(f))["versions"]
        assert listed and all("timestamp" in v for v in listed), listed
        r = write("restore", path=str(f), timestamp=listed[0]["timestamp"])
        assert r["success"] is True, r.get("error")
        assert f.read_text(encoding="utf-8") == "one\n"

    def test_the_restore_is_itself_undoable(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("first\n", encoding="utf-8")
        write("replace_text", path=str(f), find="first", replace="second")
        r = write("restore", path=str(f))
        assert r["backup"], "no counter-snapshot before the restore"
        assert Path(r["backup"]).read_text(encoding="utf-8") == "second\n"

    def test_a_dry_run_restores_nothing(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("first\n", encoding="utf-8")
        write("replace_text", path=str(f), find="first", replace="second")
        engine.fs_write([{"op": "restore", "path": str(f)}], dry_run=True)
        assert f.read_text(encoding="utf-8") == "second\n"

    def test_a_file_with_no_snapshots_says_where_to_look(self, tmp_path):
        f = tmp_path / "fresh.txt"
        f.write_text("x\n", encoding="utf-8")
        # A failing op short-circuits the batch and answers as itself.
        r = engine.fs_write([{"op": "restore", "path": str(f)}])
        assert r["success"] is False
        assert "versions" in r["hint"], r["hint"]

    def test_an_unknown_timestamp_names_the_ones_that_exist(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("first\n", encoding="utf-8")
        write("replace_text", path=str(f), find="first", replace="second")
        got = engine.fs_write(
            [{"op": "restore", "path": str(f), "timestamp": "1999-01-01T00-00-00Z"}]
        )
        assert got["success"] is False
        assert "Available timestamps" in got["hint"], got["hint"]

    def test_the_restore_is_recorded(self, tmp_path):

        f = tmp_path / "notes.txt"
        f.write_text("first\n", encoding="utf-8")
        write("replace_text", path=str(f), find="first", replace="second")
        write("restore", path=str(f))
        from shared.receipt import read_receipt_log

        entries = read_receipt_log(str(f))
        assert [e.get("op") or e.get("action") for e in entries][-1] == "restore"

    def test_the_vocabulary_names_it(self):
        from shared.patch_validator import validate_ops

        errors = validate_ops([{"op": "rollback", "path": "/tmp/x"}])
        assert "restore" in errors[0], errors
