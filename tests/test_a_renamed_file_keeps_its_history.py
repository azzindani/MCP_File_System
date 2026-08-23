"""Renaming or moving a file must not detach its history from it.

Snapshots are named `{stem}_{ts}{ext}.bak` inside the `.mcp_versions` beside
the file, and the receipt log is a sibling named `{filename}.mcp_receipt.json`.
Both are keyed on the name. So a rename left every one of them behind:

    before rename:  versions=1  receipts=[append_file, replace_text]
    after  rename:  versions=0  receipts=[]

with `success: true` on the rename. The file's entire recovery history was
gone — `fs_manage action=versions` reported nothing to restore for a file that
had a snapshot a moment earlier — and no receipt anywhere recorded that a
rename had happened at all.

`move` looked better and was the same defect: the destination started a fresh
log whose only entry was the move itself, while the real history stayed at the
source path.

`set_permissions` recorded nothing either. Who can read a file is the change
most worth being able to look up later, and it was the one write op that left
no trace.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if p not in sys.path:
        sys.path.insert(0, p)

import engine  # noqa: E402

from shared import version_control as vc  # noqa: E402


def write(op: str, **kw) -> dict:
    """One op through fs_write, returning that op's own result."""
    outer = engine.fs_write([dict(op=op, **kw)])
    assert outer["success"] is True, outer
    return outer["results"][0]


def receipts(p: Path) -> list[str]:
    log = Path(str(p) + ".mcp_receipt.json")
    if not log.exists():
        return []
    data = json.loads(log.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    return [e.get("op") or e.get("action") for e in entries]


def versions(p: Path) -> int:
    return engine.fs_manage("versions", str(p)).get("count", 0)


@pytest.fixture
def edited(tmp_path):
    """A file with a real history: one snapshot and two receipt entries."""
    f = tmp_path / "a.txt"
    f.write_text("hello\n", encoding="utf-8")
    write("append_file", path=str(f), content="1\n")
    write("replace_text", path=str(f), find="hello", replace="HI")
    assert versions(f) == 1, "fixture needs a snapshot to lose"
    assert receipts(f) == ["append_file", "replace_text"]
    return f


class TestRenameCarriesTheHistory:
    def test_the_snapshot_is_still_restorable(self, edited, tmp_path):
        write("rename", path=str(edited), name="b.txt")
        assert versions(tmp_path / "b.txt") == 1

    def test_the_receipt_log_follows_and_records_the_rename(self, edited, tmp_path):
        write("rename", path=str(edited), name="b.txt")
        assert receipts(tmp_path / "b.txt") == ["append_file", "replace_text", "rename"]

    def test_nothing_is_left_behind_under_the_old_name(self, edited, tmp_path):
        write("rename", path=str(edited), name="b.txt")
        assert receipts(edited) == []
        assert not list((tmp_path / ".mcp_versions").glob("a_*")), (
            "snapshots left under the old stem"
        )

    def test_the_count_carried_is_reported(self, edited):
        assert write("rename", path=str(edited), name="b.txt")["snapshots_carried"] == 1


class TestMoveCarriesTheHistory:
    def test_the_snapshot_and_log_arrive_at_the_destination(self, edited, tmp_path):
        dst = tmp_path / "sub" / "c.txt"
        assert write("move", path=str(edited), dst=str(dst))["snapshots_carried"] == 1
        assert versions(dst) == 1
        assert receipts(dst) == ["append_file", "replace_text", "move"]

    def test_a_destination_with_its_own_history_keeps_it(self, edited, tmp_path):
        other = tmp_path / "sub" / "c.txt"
        other.parent.mkdir()
        other.write_text("other\n", encoding="utf-8")
        write("append_file", path=str(other), content="x\n")
        gone = tmp_path / "sub" / "d.txt"
        write("move", path=str(edited), dst=str(gone))
        # Different destination name, so nothing to merge -- but the source's
        # entries must all be there, in order, ahead of the move.
        assert receipts(gone)[:2] == ["append_file", "replace_text"]
        assert receipts(other) == ["append_file"]


class TestCopyIsANewFile:
    def test_the_copy_does_not_claim_the_original_history(self, edited, tmp_path):
        dst = tmp_path / "copy.txt"
        write("copy", path=str(edited), dst=str(dst))
        # A copy is a new artifact: it starts its own log, and crucially the
        # source keeps everything it had.
        assert receipts(dst) == ["copy"]
        assert receipts(edited) == ["append_file", "replace_text"]
        assert versions(edited) == 1


class TestSetPermissionsIsRecorded:
    @pytest.mark.skipif(sys.platform == "win32", reason="chmod is a no-op on Windows")
    def test_it_writes_a_receipt_naming_both_modes(self, edited):
        r = write("set_permissions", path=str(edited), mode="600")
        assert r["mode_before"], r
        assert receipts(edited)[-1] == "set_permissions"

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod is a no-op on Windows")
    def test_the_previous_mode_is_reported(self, edited):
        edited.chmod(0o644)
        assert write("set_permissions", path=str(edited), mode="600")["mode_before"] == "0o644"


class TestALegacyNameCannotBeAnotherFilesSnapshot:
    """Reading the siblings' extension-less name reopened the hole on this side.

    This repo writes `{stem}_{ts}{ext}.bak` precisely so that report.csv and
    report.docx do not share a history. But `_patterns` also matched the
    siblings' `{stem}_{ts}.bak` unconditionally, so a snapshot written by
    another server for the .docx was still offered as a version of the .csv --
    and restore_version takes the newest candidate. The three siblings now write
    the extension too; the compatibility match survives only where the stem is
    unambiguous.
    """

    def test_a_legacy_snapshot_is_offered_when_nothing_shares_the_stem(self, tmp_path):
        f = tmp_path / "solo.csv"
        f.write_text("current\n", encoding="utf-8")
        vdir = tmp_path / ".mcp_versions"
        vdir.mkdir()
        (vdir / "solo_2026-08-01T00-00-00Z.bak").write_text("older\n", encoding="utf-8")
        assert len(vc.list_versions(str(f))) == 1
        assert vc.restore_version(str(f), "2026-08-01T00-00-00Z")["success"] is True
        assert f.read_text(encoding="utf-8") == "older\n"

    def test_a_legacy_snapshot_is_withheld_when_a_namesake_exists(self, tmp_path):
        csv = tmp_path / "report.csv"
        csv.write_text("a,b\n1,2\n", encoding="utf-8")
        (tmp_path / "report.docx").write_bytes(b"PK\x03\x04")
        vdir = tmp_path / ".mcp_versions"
        vdir.mkdir()
        (vdir / "report_2026-08-01T00-00-00Z.bak").write_bytes(b"PK\x03\x04elsewhere")
        assert vc.list_versions(str(csv)) == []
        assert vc.restore_version(str(csv), "2026-08-01T00-00-00Z")["success"] is False
        assert csv.read_text(encoding="utf-8") == "a,b\n1,2\n"

    def test_this_repos_own_snapshots_are_unaffected(self, tmp_path):
        csv = tmp_path / "report.csv"
        csv.write_text("a,b\n1,2\n", encoding="utf-8")
        (tmp_path / "report.docx").write_bytes(b"PK\x03\x04")
        vc.snapshot(str(csv))
        assert len(vc.list_versions(str(csv))) == 1
