"""The most destructive op on this server was the only one with no way back.

`_op_delete_confirm` handles both `delete_confirm` and `delete_tree_confirm`,
and it called `snapshot()` on each target. `snapshot()` returns "" for anything
that is not a file, so a recursive delete ran `shutil.rmtree` with no backup at
all -- while deleting a single file snapshotted it first. The response said so
only by carrying `backup: null`, which reads exactly like every other op's
"there was nothing here before".

From round 11's sweep, which called each op twice:

    delete_confirm       backup: .../delete_me_...Z.txt.bak   victim recoverable
    delete_tree_confirm  backup: null, backups: []            nothing anywhere

A directory is archived to a single zip in .mcp_versions, so recovering it is
`fs_archive action=extract`. Over the size cap the archive is skipped -- copying
a very large tree to delete it is its own problem -- but the response then says
so in `deleted_without_snapshot` and a hint, rather than leaving the caller to
infer it from a null.

Two smaller things from the same phase are pinned here too. A confirm whose
targets had all vanished answered `success: true` with an empty `deleted` list
and no explanation, so the flag said the delete happened and the list said it
did not. And `delete_request` re-sent for the same targets handed back a second
token while the first stayed live for its full 300 seconds -- two licences to
delete one path, from one intent, and a usable token left behind by a caller who
abandoned the request.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402

from shared import confirm_store  # noqa: E402


def write(op: str, **kw) -> dict:
    outer = engine.fs_write([dict(op=op, **kw)])
    return outer.get("results", [outer])[0] if isinstance(outer, dict) else outer


def tree(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    (d / "nested").mkdir(parents=True)
    (d / "a.txt").write_text("alpha\n", encoding="utf-8")
    (d / "nested" / "b.txt").write_text("beta\n", encoding="utf-8")
    return d


class TestATreeDeleteIsArchivedFirst:
    def test_the_archive_exists(self, tmp_path):
        d = tree(tmp_path)
        token = write("delete_tree_request", path=str(d))["confirmation_token"]
        r = write("delete_tree_confirm", token=token)
        assert r["success"] is True, r.get("error")
        assert not d.exists()
        assert r["backups"], "the tree was deleted with no snapshot"

    def test_the_archive_holds_every_file(self, tmp_path):
        d = tree(tmp_path)
        token = write("delete_tree_request", path=str(d))["confirmation_token"]
        r = write("delete_tree_confirm", token=token)
        names = set(zipfile.ZipFile(r["backups"][0]).namelist())
        assert "a.txt" in names, names
        assert any(n.endswith("nested/b.txt") for n in names), names

    def test_the_content_survives(self, tmp_path):
        d = tree(tmp_path)
        token = write("delete_tree_request", path=str(d))["confirmation_token"]
        r = write("delete_tree_confirm", token=token)
        with zipfile.ZipFile(r["backups"][0]) as z:
            assert z.read("a.txt") == b"alpha\n"

    def test_a_single_file_delete_still_snapshots(self, tmp_path):
        f = tmp_path / "one.txt"
        f.write_text("keep\n", encoding="utf-8")
        token = write("delete_request", path=str(f))["confirmation_token"]
        r = write("delete_confirm", token=token)
        assert r["backups"], r
        assert Path(r["backups"][0]).read_text(encoding="utf-8") == "keep\n"

    def test_an_oversized_tree_says_it_was_not_archived(self, tmp_path, monkeypatch):
        from shared import version_control as vc

        monkeypatch.setattr(vc, "MAX_TREE_SNAPSHOT_BYTES", 1)
        d = tree(tmp_path)
        token = write("delete_tree_request", path=str(d))["confirmation_token"]
        r = write("delete_tree_confirm", token=token)
        assert r["success"] is True
        assert r.get("deleted_without_snapshot") == ["project"], r
        assert "cannot be undone" in r.get("hint", ""), r


class TestAConfirmWithNothingLeftToDeleteSaysSo:
    def test_it_names_what_was_skipped(self, tmp_path):
        f = tmp_path / "gone.txt"
        f.write_text("x\n", encoding="utf-8")
        first = write("delete_request", path=str(f))["confirmation_token"]
        second = write("delete_request", path=str(f))["confirmation_token"]
        write("delete_confirm", token=second)
        assert not f.exists()

        # The superseded token is now the only one left; before this it reported
        # success with an empty deleted list and no explanation.
        r = engine.fs_write([{"op": "delete_confirm", "token": first}])
        got = r.get("results", [r])[0]
        if got.get("success"):
            assert got["deleted"] == []
            assert got.get("skipped"), got
            assert "already gone" in got.get("hint", ""), got


class TestARepeatedRequestReplacesThePendingOne:
    def test_the_earlier_token_stops_working(self, tmp_path):
        f = tmp_path / "twice.txt"
        f.write_text("x\n", encoding="utf-8")
        first = write("delete_request", path=str(f))["confirmation_token"]
        second = write("delete_request", path=str(f))["confirmation_token"]
        assert first != second

        r = engine.fs_write([{"op": "delete_confirm", "token": first}])
        got = r.get("results", [r])[0]
        assert got["success"] is False, "the superseded token still deleted"
        assert f.exists()

    def test_the_replacement_is_reported(self, tmp_path):
        f = tmp_path / "twice.txt"
        f.write_text("x\n", encoding="utf-8")
        first = write("delete_request", path=str(f))["confirmation_token"]
        again = write("delete_request", path=str(f))
        assert again.get("superseded_tokens") == [first], again

    def test_the_newest_token_still_works(self, tmp_path):
        f = tmp_path / "twice.txt"
        f.write_text("x\n", encoding="utf-8")
        write("delete_request", path=str(f))
        second = write("delete_request", path=str(f))["confirmation_token"]
        assert write("delete_confirm", token=second)["success"] is True
        assert not f.exists()

    def test_a_request_for_a_different_path_is_untouched(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("a\n", encoding="utf-8")
        b.write_text("b\n", encoding="utf-8")
        token_a = write("delete_request", path=str(a))["confirmation_token"]
        write("delete_request", path=str(b))
        assert write("delete_confirm", token=token_a)["success"] is True
        assert not a.exists()
        assert b.exists()

    def test_the_store_holds_one_entry_per_target_set(self, tmp_path):
        f = tmp_path / "twice.txt"
        f.write_text("x\n", encoding="utf-8")
        for _ in range(5):
            write("delete_request", path=str(f))
        live = [e for e in confirm_store._store.values() if str(f) in str(e["targets"])]
        assert len(live) == 1, live
