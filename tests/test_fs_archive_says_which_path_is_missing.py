"""fs_archive blamed the file it was asked to create.

A coverage sweep called `create` with the two paths the other way round --
`path` = the file to archive, `target` = the .zip to write -- which is the
natural reading of "path" and "target" and the mistake the swap guard in
_basic_archive.py was written to catch. It never fired:

    action=create  path=.../scratch.txt  target=.../scratch.zip
      error: "Path does not exist: /workspace/data/.sweep01_scratch.zip"
      hint : "Check archive path, target, and format then retry."

`source` was resolved with must_exist=True *before* the guard, so the missing
.zip raised FileNotFoundError first and the caller was told the archive it had
asked the tool to create did not exist -- under a hint naming all three
arguments and identifying none. The sweep retried with a different `path`, got
the byte-identical message, and gave up.

The guard now runs on the two names before the filesystem is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from servers.fs_basic import engine

SWAP_HINT_MARKERS = ["swapped", "'path'", "'target'"]


@pytest.fixture()
def a_file(work_dir: Path) -> Path:
    p = work_dir / "scratch.txt"
    p.write_text("hello\n", encoding="utf-8")
    return p


class TestTheSwappedCallTheSweepMade:
    def test_it_does_not_blame_the_archive_it_was_asked_to_create(
        self, work_dir: Path, a_file: Path
    ):
        r = engine.fs_archive(
            action="create", path=str(a_file), target=str(work_dir / "scratch.zip")
        )
        assert r["success"] is False
        assert "Path does not exist" not in r["error"], r["error"]

    def test_the_error_names_target_as_the_missing_one(self, work_dir: Path, a_file: Path):
        r = engine.fs_archive(
            action="create", path=str(a_file), target=str(work_dir / "scratch.zip")
        )
        assert "'target'" in r["error"], r["error"]

    def test_the_hint_says_they_look_swapped(self, work_dir: Path, a_file: Path):
        r = engine.fs_archive(
            action="create", path=str(a_file), target=str(work_dir / "scratch.zip")
        )
        for marker in SWAP_HINT_MARKERS:
            assert marker in r["hint"], f"missing {marker!r}: {r['hint']}"

    def test_the_hint_names_both_actual_paths(self, work_dir: Path, a_file: Path):
        r = engine.fs_archive(
            action="create", path=str(a_file), target=str(work_dir / "scratch.zip")
        )
        assert "scratch.zip" in r["hint"] and "scratch.txt" in r["hint"], r["hint"]

    def test_the_retry_with_a_directory_gets_the_same_help(self, work_dir: Path):
        d = work_dir / "folder"
        d.mkdir()
        (d / "a.txt").write_text("a", encoding="utf-8")
        r = engine.fs_archive(action="create", path=str(d), target=str(work_dir / "scratch.zip"))
        assert r["success"] is False
        assert "swapped" in r["hint"], r["hint"]
        assert "folder" in r["hint"], r["hint"]

    def test_nothing_is_written(self, work_dir: Path, a_file: Path):
        engine.fs_archive(action="create", path=str(a_file), target=str(work_dir / "scratch.zip"))
        assert not (work_dir / "scratch.zip").exists()
        assert a_file.read_text(encoding="utf-8") == "hello\n"


class TestAMissingSourceThatIsNotASwap:
    def test_it_still_says_target_is_the_missing_one(self, work_dir: Path):
        r = engine.fs_archive(
            action="create", path=str(work_dir / "out.zip"), target=str(work_dir / "nope.txt")
        )
        assert r["success"] is False
        assert "'target'" in r["error"], r["error"]

    def test_it_does_not_claim_the_arguments_are_swapped(self, work_dir: Path):
        r = engine.fs_archive(
            action="create", path=str(work_dir / "out.zip"), target=str(work_dir / "nope.txt")
        )
        assert "swapped" not in r["hint"], r["hint"]

    def test_the_hint_points_at_a_tool_that_finds_files(self, work_dir: Path):
        r = engine.fs_archive(
            action="create", path=str(work_dir / "out.zip"), target=str(work_dir / "nope.txt")
        )
        assert "fs_query" in r["hint"], r["hint"]

    def test_two_archive_names_are_not_read_as_a_swap(self, work_dir: Path):
        r = engine.fs_archive(
            action="create", path=str(work_dir / "a.zip"), target=str(work_dir / "b.zip")
        )
        assert r["success"] is False
        assert "swapped" not in r["hint"], r["hint"]


class TestAMissingArchiveOnTheReadingActions:
    @pytest.mark.parametrize("action", ["extract", "list"])
    def test_the_hint_names_path(self, work_dir: Path, action: str):
        r = engine.fs_archive(action=action, path=str(work_dir / "ghost.zip"))
        assert r["success"] is False
        assert "'path'" in r["hint"], r["hint"]

    @pytest.mark.parametrize("action", ["extract", "list"])
    def test_the_hint_names_the_action_asked_for(self, work_dir: Path, action: str):
        r = engine.fs_archive(action=action, path=str(work_dir / "ghost.zip"))
        assert action in r["hint"], r["hint"]


class TestTheGuardThatAlreadyWorkedStillWorks:
    def test_an_existing_non_archive_destination_is_refused(self, work_dir: Path, a_file: Path):
        victim = work_dir / "notes.txt"
        victim.write_text("precious\n", encoding="utf-8")
        r = engine.fs_archive(action="create", path=str(victim), target=str(a_file))
        assert r["success"] is False
        assert "Refusing to overwrite non-archive file" in r["error"], r["error"]
        assert victim.read_text(encoding="utf-8") == "precious\n"

    def test_an_existing_directory_destination_is_refused(self, work_dir: Path, a_file: Path):
        d = work_dir / "dest"
        d.mkdir()
        r = engine.fs_archive(action="create", path=str(d), target=str(a_file))
        assert r["success"] is False
        assert "Destination is a directory" in r["error"], r["error"]

    def test_a_missing_target_argument_is_still_reported(self, work_dir: Path):
        r = engine.fs_archive(action="create", path=str(work_dir / "out.zip"))
        assert r["success"] is False
        assert "target" in r["error"], r["error"]


class TestTheRightWayRoundIsUnaffected:
    def test_a_file_is_archived(self, work_dir: Path, a_file: Path):
        arc = work_dir / "ok.zip"
        r = engine.fs_archive(action="create", path=str(arc), target=str(a_file))
        assert r["success"] is True, r.get("error")
        assert arc.exists()
        assert r["files_archived"] == 1

    def test_a_directory_is_archived(self, work_dir: Path):
        d = work_dir / "tree"
        d.mkdir()
        (d / "a.txt").write_text("a", encoding="utf-8")
        (d / "b.txt").write_text("b", encoding="utf-8")
        arc = work_dir / "tree.tar.gz"
        r = engine.fs_archive(action="create", path=str(arc), target=str(d), format_="tar.gz")
        assert r["success"] is True, r.get("error")
        assert r["files_archived"] == 2

    def test_dry_run_still_reports_without_writing(self, work_dir: Path, a_file: Path):
        arc = work_dir / "dry.zip"
        r = engine.fs_archive(action="create", path=str(arc), target=str(a_file), dry_run=True)
        assert r["success"] is True, r.get("error")
        assert r["dry_run"] is True
        assert not arc.exists()

    def test_the_archive_can_be_listed_back(self, work_dir: Path, a_file: Path):
        arc = work_dir / "roundtrip.zip"
        engine.fs_archive(action="create", path=str(arc), target=str(a_file))
        r = engine.fs_archive(action="list", path=str(arc))
        assert r["success"] is True, r.get("error")
        assert r["count"] == 1
