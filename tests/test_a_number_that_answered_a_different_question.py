"""Counts and sizes reported under names that describe something else.

Round 14's axis, applied to the three read-only tools in phase 4. None of these
lost data; each one answered confidently in the caller's own vocabulary, about
a different thing.

**fs_manage action=disk_usage** takes a path, echoes it back, and reported
`shutil.disk_usage` -- the numbers for the *volume*, identical for every path on
the machine -- under the progress line "Disk usage for fsr2". `size`, `storage`
and `space` are all aliases for this action, so "how big is this folder" is the
likeliest question being asked, and 206.9 GB was the answer to it. Both numbers
are worth having; each now says which it is, and the path walk is bounded
because a path can be the whole volume.

**fs_index action=build** called `stat()`, which follows symlinks, so a link was
stored as `type: "file"` carrying the target's size -- while `fs_manage
action=symlink_info`, in the same server, answers `is_symlink: true` for that
same path. A *broken* link raised inside the loop and hit `continue`, dropping
it from the index with no mention, so a dangling symlink and a path that does
not exist looked identical.

**fs_index action=stats** reported `file_count`, counting directories and
symlinks in it (18 entries on a tree of 12 files), and answered `built: true`
with the previous `last_built` after `action=clear` had removed every row.

**fs_archive action=extract** said "Extracted 3 files" for an archive holding
two files and the directory above them -- the same number the info line one
call earlier correctly calls "entries".
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402


@pytest.fixture
def sandbox(tmp_home):
    """A tree with two files, a directory, a live symlink and a broken one.

    tmp_home also relocates the index db, which lives under Path.home().
    """
    d = tmp_home / "work"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("a" * 30)
    (d / "sub" / "c.txt").write_text("c" * 13)
    try:
        os.symlink("a.txt", d / "live_link")
        os.symlink("gone.txt", d / "broken_link")
    except OSError, NotImplementedError:  # pragma: no cover - Windows without privilege
        pytest.skip("symlinks not creatable here")
    return d


# --- disk_usage answers about the path it was given -------------------------


class TestDiskUsageDistinguishesPathFromVolume:
    def test_it_reports_the_size_of_the_path(self, sandbox):
        r = engine.fs_manage(action="disk_usage", path=str(sandbox))
        assert r["success"] is True
        assert r["path_bytes"] == 43, "30 + 13 bytes of real files"
        assert r["path_files"] == 2
        assert r["path_walk_complete"] is True

    def test_the_volume_numbers_are_still_there_and_named(self, sandbox):
        r = engine.fs_manage(action="disk_usage", path=str(sandbox))
        assert r["volume_total_bytes"] == r["total_bytes"] > r["path_bytes"]
        assert r["volume_free_bytes"] == r["free_bytes"]

    def test_the_message_does_not_pass_volume_size_off_as_the_folder(self, sandbox):
        r = engine.fs_manage(action="disk_usage", path=str(sandbox))
        msg = r["progress"][0]
        assert "2 file(s)" in msg["msg"]
        assert "volume:" in msg["detail"], "the two figures have to be told apart"

    def test_a_symlink_is_not_counted_as_its_target(self, sandbox):
        """live_link points at a.txt; counting both double-counts 30 bytes."""
        r = engine.fs_manage(action="disk_usage", path=str(sandbox))
        assert r["path_bytes"] == 43

    def test_a_single_file_reports_its_own_size(self, sandbox):
        r = engine.fs_manage(action="disk_usage", path=str(sandbox / "a.txt"))
        assert r["path_bytes"] == 30
        assert r["path_files"] == 1

    def test_a_walk_that_stops_early_says_so(self, sandbox, monkeypatch):
        import _basic_manage

        monkeypatch.setattr(_basic_manage, "get_max_usage_walk", lambda: 1)
        r = engine.fs_manage(action="disk_usage", path=str(sandbox))
        assert r["path_walk_complete"] is False
        assert "Stopped after 1 entries" in r["hint"]
        assert "volume figures are complete" in r["hint"]

    def test_the_size_alias_reaches_the_same_answer(self, sandbox):
        r = engine.fs_manage(action="size", path=str(sandbox))
        assert r["path_bytes"] == 43


# --- the index describes the thing at the path, not what it points at -------


class TestTheIndexKnowsASymlinkWhenItSeesOne:
    def test_a_symlink_is_typed_as_one(self, sandbox):
        engine.fs_index(action="build", path=str(sandbox))
        rows = {m["name"]: m for m in engine.fs_index(action="list", path=str(sandbox))["entries"]}
        assert rows["live_link"]["type"] == "symlink"
        assert rows["a.txt"]["type"] == "file"
        assert rows["sub"]["type"] == "dir"

    def test_it_does_not_carry_the_targets_size(self, sandbox):
        engine.fs_index(action="build", path=str(sandbox))
        rows = {m["name"]: m for m in engine.fs_index(action="list", path=str(sandbox))["entries"]}
        assert rows["a.txt"]["size"] == 30
        assert rows["live_link"]["size"] != 30, "the link's own size, not what it points at"

    def test_a_broken_link_is_indexed_rather_than_dropped(self, sandbox):
        b = engine.fs_index(action="build", path=str(sandbox))
        names = {m["name"] for m in engine.fs_index(action="list", path=str(sandbox))["entries"]}
        assert "broken_link" in names, "a dangling link looked exactly like a missing path"
        assert b["symlinks"] == 2

    def test_the_agreement_with_fs_manage(self, sandbox):
        """One server answering two ways about one path was the whole defect."""
        engine.fs_index(action="build", path=str(sandbox))
        rows = {m["name"]: m for m in engine.fs_index(action="list", path=str(sandbox))["entries"]}
        info = engine.fs_manage(action="symlink_info", path=str(sandbox / "live_link"))
        assert info["is_symlink"] is True
        assert rows["live_link"]["type"] == "symlink"


class TestTheIndexCountsSayWhatTheyCount:
    def test_build_breaks_its_total_down(self, sandbox):
        b = engine.fs_index(action="build", path=str(sandbox))
        assert b["indexed"] == b["files"] + b["dirs"] + b["symlinks"]
        assert (b["files"], b["dirs"], b["symlinks"]) == (2, 1, 2)

    def test_file_count_counts_files(self, sandbox):
        engine.fs_index(action="build", path=str(sandbox))
        s = engine.fs_index(action="stats")
        assert s["entry_count"] == 5
        assert s["file_count"] == 2, "directories and symlinks are not files"
        assert s["dir_count"] == 1
        assert s["symlink_count"] == 2

    def test_stats_and_build_agree(self, sandbox):
        b = engine.fs_index(action="build", path=str(sandbox))
        s = engine.fs_index(action="stats")
        assert s["entry_count"] == b["indexed"]
        assert s["file_count"] == b["files"]


class TestAClearedIndexSaysItIsEmpty:
    def test_clear_reports_what_is_left(self, sandbox):
        engine.fs_index(action="build", path=str(sandbox))
        c = engine.fs_index(action="clear", path=str(sandbox))
        assert c["cleared"] == 5
        assert c["remaining"] == 0

    def test_stats_after_a_full_clear(self, sandbox):
        engine.fs_index(action="build", path=str(sandbox))
        engine.fs_index(action="clear", path=str(sandbox))
        s = engine.fs_index(action="stats")
        assert s["built"] is False, "an index holding nothing has not been built"
        assert s["entry_count"] == 0
        assert s.get("last_built") is None, "the stamp of a build whose rows are gone"
        assert "action=build" in s["hint"]

    def test_a_partial_clear_leaves_the_index_built(self, sandbox):
        engine.fs_index(action="build", path=str(sandbox))
        c = engine.fs_index(action="clear", path=str(sandbox / "sub"))
        assert c["cleared"] >= 1
        assert c["remaining"] > 0
        s = engine.fs_index(action="stats")
        assert s["built"] is True
        assert s["last_built"] is not None

    def test_a_rebuild_after_clearing_works(self, sandbox):
        engine.fs_index(action="build", path=str(sandbox))
        engine.fs_index(action="clear", path=str(sandbox))
        again = engine.fs_index(action="build", path=str(sandbox))
        assert again["indexed"] == 5
        assert engine.fs_index(action="stats")["built"] is True


# --- extract counts entries, and says how many were files -------------------


class TestExtractDoesNotCallDirectoriesFiles:
    def _archive(self, sandbox, suffix, fmt):
        arc = sandbox.parent / f"arc{suffix}"
        r = engine.fs_archive(
            action="create", path=str(arc), target=str(sandbox / "sub"), format_=fmt
        )
        assert r["success"] is True, r.get("error")
        return arc

    @pytest.mark.parametrize(("suffix", "fmt"), [(".zip", "zip"), (".tar.gz", "tar.gz")])
    def test_the_breakdown_adds_up(self, sandbox, suffix, fmt):
        arc = self._archive(sandbox, suffix, fmt)
        out = sandbox.parent / f"out{suffix}"
        r = engine.fs_archive(action="extract", path=str(arc), target=str(out))
        assert r["success"] is True, r.get("error")
        assert r["extracted"] == r["extracted_files"] + r["extracted_dirs"]
        assert r["extracted_files"] >= 1

    @pytest.mark.parametrize(("suffix", "fmt"), [(".zip", "zip"), (".tar.gz", "tar.gz")])
    def test_the_message_stops_calling_entries_files(self, sandbox, suffix, fmt):
        arc = self._archive(sandbox, suffix, fmt)
        out = sandbox.parent / f"out2{suffix}"
        r = engine.fs_archive(action="extract", path=str(arc), target=str(out))
        last = r["progress"][-1]
        assert "entries" in last["msg"]
        assert "file(s)" in last["detail"] and "dir(s)" in last["detail"]

    def test_the_files_really_landed(self, sandbox):
        arc = self._archive(sandbox, ".zip", "zip")
        out = sandbox.parent / "out_real"
        engine.fs_archive(action="extract", path=str(arc), target=str(out))
        assert (out / "sub" / "c.txt").read_text() == "c" * 13
