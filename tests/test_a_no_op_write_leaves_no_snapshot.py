"""A write that changed nothing kept a full copy of the file anyway.

The snapshot has to be taken before the write -- nothing knows yet whether the
write will change anything. What was missing was the other half: throwing it
away when the answer turns out to be "nothing".

    fs_write write_file path=f.txt content="same"   x3
    -> 2 snapshots, both byte-identical to the live file

That is the retry case. A client re-sending a write_file or a download whose
first attempt timed out pays a full copy of the file per attempt, and nothing
in this fleet prunes .mcp_versions. The sibling repo measured the same shape on
a 1.9 MB CSV: four calls, ~7.5 MB of backups, half of them duplicates.

The check sits in the dispatcher rather than in each handler, so every op gets
it, and it runs after the write rather than guessing before it -- which is what
makes it exact. A backup byte-identical to the file now on disk cannot restore
anything the file does not already hold.

A delete's backup is never touched: its file is gone, so there is nothing to
compare against and it is kept. That is the case where losing the snapshot
would actually cost something.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402

from shared.version_control import discard_snapshot_if_unchanged  # noqa: E402


def write(op: str, **kw) -> dict:
    outer = engine.fs_write([dict(op=op, **kw)])
    return outer.get("results", [outer])[0] if isinstance(outer, dict) else outer


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def snaps(home: Path) -> list[Path]:
    d = home / ".mcp_versions"
    return sorted(d.glob("*.bak")) if d.is_dir() else []


class TestARewriteWithTheSameContentKeepsNoSnapshot:
    def test_three_identical_writes_leave_none(self, home):
        f = home / "f.txt"
        for _ in range(3):
            write("write_file", path=str(f), content="same\n")
        assert snaps(home) == [], [p.name for p in snaps(home)]

    def test_the_retry_reports_no_backup(self, home):
        f = home / "f.txt"
        write("write_file", path=str(f), content="same\n")
        r = write("write_file", path=str(f), content="same\n")
        assert r["success"] is True, r.get("error")
        assert r["backup"] is None, r["backup"]

    def test_the_file_is_still_right(self, home):
        f = home / "f.txt"
        write("write_file", path=str(f), content="same\n")
        write("write_file", path=str(f), content="same\n")
        assert f.read_bytes() == b"same\n"


class TestARealChangeStillSnapshots:
    def test_a_different_content_keeps_its_backup(self, home):
        f = home / "f.txt"
        write("write_file", path=str(f), content="one\n")
        r = write("write_file", path=str(f), content="two\n")
        assert r["backup"], r
        assert len(snaps(home)) == 1

    def test_the_backup_holds_the_previous_content(self, home):
        f = home / "f.txt"
        write("write_file", path=str(f), content="one\n")
        r = write("write_file", path=str(f), content="two\n")
        assert Path(r["backup"]).read_bytes() == b"one\n"

    def test_an_append_always_changes_and_always_keeps_one(self, home):
        f = home / "log.txt"
        write("write_file", path=str(f), content="start\n")
        write("append_file", path=str(f), content="more\n")
        r = write("append_file", path=str(f), content="more\n")
        assert r["backup"], "appending twice is a real change and needs its backup"


class TestADeletesBackupIsNeverDiscarded:
    def test_the_victim_is_still_recoverable(self, home):
        f = home / "gone.txt"
        f.write_bytes(b"keep\n")
        token = write("delete_request", path=str(f))["confirmation_token"]
        r = write("delete_confirm", token=token)
        assert r["backups"], r
        assert Path(r["backups"][0]).read_bytes() == b"keep\n"
        assert not f.exists()


class TestTheHelperIsExact:
    def test_it_keeps_a_backup_that_differs(self, tmp_path):
        live, back = tmp_path / "live", tmp_path / "back.bak"
        live.write_bytes(b"new")
        back.write_bytes(b"old")
        assert discard_snapshot_if_unchanged(str(back), live) == str(back)
        assert back.exists()

    def test_it_drops_a_backup_that_matches(self, tmp_path):
        live, back = tmp_path / "live", tmp_path / "back.bak"
        live.write_bytes(b"same")
        back.write_bytes(b"same")
        assert discard_snapshot_if_unchanged(str(back), live) == ""
        assert not back.exists()

    def test_a_same_size_difference_is_still_a_difference(self, tmp_path):
        live, back = tmp_path / "live", tmp_path / "back.bak"
        live.write_bytes(b"abc")
        back.write_bytes(b"abd")
        assert discard_snapshot_if_unchanged(str(back), live) == str(back)

    def test_a_vanished_live_file_keeps_the_backup(self, tmp_path):
        back = tmp_path / "back.bak"
        back.write_bytes(b"x")
        gone = tmp_path / "gone"
        assert discard_snapshot_if_unchanged(str(back), gone) == str(back)
        assert back.exists()

    def test_an_empty_backup_path_is_passed_through(self, tmp_path):
        assert discard_snapshot_if_unchanged("", tmp_path / "x") == ""
