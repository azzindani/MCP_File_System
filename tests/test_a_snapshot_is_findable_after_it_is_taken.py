"""The safety net behind every destructive write was invisible and not durable.

A sweep pointed fs_manage at a file with seven .bak snapshots plainly visible
beside it and got:

    action=versions  path=/workspace/data/Ad_Data.csv
      success: true
      count: 0
      progress: "Found 0 snapshot(s) for Ad_Data.csv"
      hint: "No snapshots found. Snapshots are created automatically on
             destructive writes."

Two causes, and the second is worse than the first.

* This repo wrote its snapshots to `~/.mcp_versions`, the three sibling MCP_*
  servers write theirs to `<the file's directory>/.mcp_versions`. So `versions`
  could not see a snapshot a sibling had taken of the same file, and said none
  existed rather than that it had looked somewhere else.
* In the deployed configuration only the shared exchange directory is mounted.
  A snapshot under the container's home was therefore stranded there and gone
  on the next rebuild -- the same defect that put generated outputs in
  ~/Downloads before the shared output directory existed. The rollback path for
  every destructive fs_write op could not survive a redeploy.

Writing now follows the siblings. Reading is deliberately more forgiving than
writing: both directories are searched and both filename conventions matched,
so snapshots taken before this change, or by another server, still list and
still restore.

Found by giving the filesystem read tools a phase of their own and running
every action each one accepts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from servers.fs_basic import engine
from shared.version_control import list_versions, restore_version, snapshot

SIBLING_STAMP = "2026-08-23T02-05-46-288468Z"


@pytest.fixture()
def a_file(work_dir: Path) -> Path:
    p = work_dir / "Ad_Data.csv"
    p.write_text("Date,spends\n2019-10-16,0\n", encoding="utf-8")
    return p


class TestASnapshotLandsBesideItsFile:
    def test_it_is_written_into_the_files_own_directory(self, a_file: Path):
        backup = snapshot(str(a_file))
        assert backup, "snapshot returned nothing"
        assert Path(backup).parent == a_file.parent / ".mcp_versions", backup

    def test_it_is_not_written_under_home(self, a_file: Path, tmp_home: Path):
        backup = snapshot(str(a_file))
        assert not str(Path(backup)).startswith(str(tmp_home / ".mcp_versions")), backup

    def test_the_tool_can_then_find_it(self, a_file: Path):
        snapshot(str(a_file))
        r = engine.fs_manage(action="versions", path=str(a_file))
        assert r["success"] is True, r.get("error")
        assert r["count"] == 1, r

    def test_two_snapshots_in_the_same_second_do_not_overwrite(self, a_file: Path):
        first = snapshot(str(a_file))
        second = snapshot(str(a_file))
        assert first != second
        assert len(list_versions(str(a_file))) == 2


class TestASiblingsSnapshotIsFound:
    """The sweep's case: .bak files written by another MCP_* server."""

    def test_it_is_listed(self, a_file: Path):
        vdir = a_file.parent / ".mcp_versions"
        vdir.mkdir()
        (vdir / f"Ad_Data_{SIBLING_STAMP}.bak").write_text("old\n", encoding="utf-8")
        assert len(list_versions(str(a_file))) == 1

    def test_the_tool_no_longer_reports_none(self, a_file: Path):
        vdir = a_file.parent / ".mcp_versions"
        vdir.mkdir()
        (vdir / f"Ad_Data_{SIBLING_STAMP}.bak").write_text("old\n", encoding="utf-8")
        r = engine.fs_manage(action="versions", path=str(a_file))
        assert r["count"] == 1, r
        assert "hint" not in r or "No snapshots" not in r["hint"], r.get("hint")

    def test_it_can_be_restored(self, a_file: Path):
        vdir = a_file.parent / ".mcp_versions"
        vdir.mkdir()
        (vdir / f"Ad_Data_{SIBLING_STAMP}.bak").write_text("restored content\n", encoding="utf-8")
        r = restore_version(str(a_file), SIBLING_STAMP)
        assert r["success"] is True, r.get("error")
        assert a_file.read_text(encoding="utf-8") == "restored content\n"


class TestOlderSnapshotsUnderHomeStillWork:
    def test_they_are_still_listed(self, a_file: Path, tmp_home: Path):
        legacy = tmp_home / ".mcp_versions"
        legacy.mkdir(parents=True)
        (legacy / "Ad_Data_2026-08-01T00-00-00Z.csv.bak").write_text("legacy\n", encoding="utf-8")
        assert len(list_versions(str(a_file))) == 1

    def test_they_are_still_restorable(self, a_file: Path, tmp_home: Path):
        legacy = tmp_home / ".mcp_versions"
        legacy.mkdir(parents=True)
        (legacy / "Ad_Data_2026-08-01T00-00-00Z.csv.bak").write_text("legacy\n", encoding="utf-8")
        r = restore_version(str(a_file), "2026-08-01T00-00-00Z")
        assert r["success"] is True, r.get("error")
        assert a_file.read_text(encoding="utf-8") == "legacy\n"

    def test_both_locations_are_listed_together(self, a_file: Path, tmp_home: Path):
        legacy = tmp_home / ".mcp_versions"
        legacy.mkdir(parents=True)
        (legacy / "Ad_Data_2026-08-01T00-00-00Z.csv.bak").write_text("legacy\n", encoding="utf-8")
        snapshot(str(a_file))
        assert len(list_versions(str(a_file))) == 2


class TestAnotherFilesSnapshotsAreNotClaimed:
    def test_a_longer_stem_does_not_answer_for_a_shorter_one(self, work_dir: Path):
        short = work_dir / "Ad_Data.csv"
        short.write_text("a\n", encoding="utf-8")
        longer = work_dir / "Ad_Data_test.csv"
        longer.write_text("b\n", encoding="utf-8")
        snapshot(str(longer))
        assert list_versions(str(short)) == []

    def test_each_file_sees_only_its_own(self, work_dir: Path):
        for name in ("Ad_Data.csv", "Ad_Data_test.csv"):
            p = work_dir / name
            p.write_text("x\n", encoding="utf-8")
            snapshot(str(p))
        assert len(list_versions(str(work_dir / "Ad_Data.csv"))) == 1
        assert len(list_versions(str(work_dir / "Ad_Data_test.csv"))) == 1

    def test_a_file_with_no_snapshots_still_says_so(self, a_file: Path):
        r = engine.fs_manage(action="versions", path=str(a_file))
        assert r["count"] == 0
        assert "hint" in r


class TestReceiptHonoursMaxResults:
    """The other half of the same phase: an argument the response ignored."""

    def _with_history(self, work_dir: Path, entries: int) -> Path:
        p = work_dir / "log.txt"
        p.write_text("start\n", encoding="utf-8")
        for i in range(entries):
            engine.fs_write(ops=[{"op": "append_file", "path": str(p), "content": f"line {i}\n"}])
        return p

    def test_it_returns_no_more_than_asked_for(self, work_dir: Path):
        p = self._with_history(work_dir, 8)
        r = engine.fs_index(action="receipt", path=str(p), max_results=5)
        assert r["success"] is True, r.get("error")
        assert len(r["history"]) <= 5, len(r["history"])

    def test_it_reports_the_full_total(self, work_dir: Path):
        p = self._with_history(work_dir, 8)
        r = engine.fs_index(action="receipt", path=str(p), max_results=5)
        assert r["total"] >= 8, r["total"]

    def test_it_flags_that_it_truncated(self, work_dir: Path):
        p = self._with_history(work_dir, 8)
        r = engine.fs_index(action="receipt", path=str(p), max_results=5)
        assert r["truncated"] is True
        assert "max_results" in r["hint"], r["hint"]

    def test_the_entries_kept_are_the_most_recent(self, work_dir: Path):
        p = self._with_history(work_dir, 8)
        full = engine.fs_index(action="receipt", path=str(p), max_results=100)
        capped = engine.fs_index(action="receipt", path=str(p), max_results=3)
        assert capped["history"] == full["history"][-3:]

    def test_a_short_history_is_not_marked_truncated(self, work_dir: Path):
        p = self._with_history(work_dir, 2)
        r = engine.fs_index(action="receipt", path=str(p), max_results=50)
        assert r["truncated"] is False
        assert r["count"] == r["total"]
