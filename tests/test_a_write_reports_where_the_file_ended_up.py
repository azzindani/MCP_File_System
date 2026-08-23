"""What fs_write says it did must match what a caller can go and check.

Both cases here came out of a coverage sweep that was told to verify each op on
disk rather than read its success flag -- neither is visible from the response
alone.

`rename` returns the old name as `path` and the new one as `new_path`, but the
dispatcher built the caller-facing URL from `dst or path`. `move` and `copy` set
`dst`, so they were right; `rename` fell through to `path` and handed back a URL
for a file that no longer existed, under success: true.

`delete_lines` counts correctly and described itself wrongly: start_line and
end_line are 0-based and end-exclusive, so start=2 end=3 removes exactly one
line while the message read "Deleted lines 2-3" -- which anyone would read as
two. The count in the payload and the sentence next to it disagreed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "servers" / "fs_basic"
for _p in (str(ROOT), str(SERVER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A directory that is publicly served, so public_url is actually attached."""
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://example.invalid/files")
    return tmp_path


class TestRenameReportsTheNewName:
    def test_the_public_url_names_the_file_that_now_exists(self, served):
        src = served / "before.txt"
        src.write_text("hello\n", encoding="utf-8")
        r = engine.fs_write(ops=[{"op": "rename", "path": str(src), "name": "after.txt"}])
        assert r["success"] is True, r.get("error")
        inner = r["results"][0]
        assert inner["new_path"].endswith("after.txt")
        url = inner.get("public_url")
        if url is not None:
            assert "after.txt" in url, f"public_url still points at the old name: {url}"
            assert "before.txt" not in url

    def test_the_renamed_file_is_the_one_on_disk(self, served):
        src = served / "before.txt"
        src.write_text("hello\n", encoding="utf-8")
        engine.fs_write(ops=[{"op": "rename", "path": str(src), "name": "after.txt"}])
        assert not src.exists()
        assert (served / "after.txt").read_text(encoding="utf-8") == "hello\n"

    def test_move_and_copy_still_report_their_destination(self, served):
        src = served / "a.txt"
        src.write_text("x\n", encoding="utf-8")
        r = engine.fs_write(ops=[{"op": "copy", "src": str(src), "dst": str(served / "b.txt")}])
        assert r["success"] is True, r.get("error")
        inner = r["results"][0]
        assert inner["dst"].endswith("b.txt")
        if inner.get("public_url"):
            assert "b.txt" in inner["public_url"]


class TestDeleteLinesSaysHowManyItDeleted:
    @pytest.fixture
    def five_lines(self, tmp_path) -> Path:
        p = tmp_path / "lines.txt"
        p.write_text("".join(f"line {i}\n" for i in range(5)), encoding="utf-8")
        return p

    def test_one_line_removed_is_described_as_one(self, five_lines):
        r = engine.fs_write(
            ops=[{"op": "delete_lines", "path": str(five_lines), "start_line": 2, "end_line": 3}]
        )
        assert r["success"] is True, r.get("error")
        inner = r["results"][0]
        assert inner["lines_removed"] == 1
        text = " ".join(str(m) for m in r["progress"])
        assert "1 line" in text, text
        assert "lines 2–3" not in text

    def test_the_count_matches_the_file(self, five_lines):
        before = five_lines.read_text(encoding="utf-8").splitlines()
        r = engine.fs_write(
            ops=[{"op": "delete_lines", "path": str(five_lines), "start_line": 1, "end_line": 4}]
        )
        after = five_lines.read_text(encoding="utf-8").splitlines()
        assert r["results"][0]["lines_removed"] == len(before) - len(after) == 3

    def test_the_message_states_the_half_open_range(self, five_lines):
        r = engine.fs_write(
            ops=[{"op": "delete_lines", "path": str(five_lines), "start_line": 1, "end_line": 4}]
        )
        text = " ".join(str(m) for m in r["progress"])
        assert "[1, 4)" in text, text

    def test_a_dry_run_describes_the_same_count_and_writes_nothing(self, five_lines):
        original = five_lines.read_text(encoding="utf-8")
        r = engine.fs_write(
            ops=[{"op": "delete_lines", "path": str(five_lines), "start_line": 0, "end_line": 2}],
            dry_run=True,
        )
        assert r["success"] is True
        text = " ".join(str(m) for m in r["progress"])
        assert "2 line" in text, text
        assert five_lines.read_text(encoding="utf-8") == original


class TestAnOpTakesTheNameItsSiblingsUse:
    """Ten of the sixteen ops call the file `path`; move and copy call it `src`.

    Nothing advertises the difference -- the tool's schema is `ops: list[dict]`
    -- so the first a caller learns of it is a refusal. A sweep model and this
    file's own author independently wrote copy(path=..., dst=...).
    """

    def test_copy_takes_path_as_well_as_src(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("x\n", encoding="utf-8")
        r = engine.fs_write(ops=[{"op": "copy", "path": str(src), "dst": str(tmp_path / "b.txt")}])
        assert r["success"] is True, r.get("error")
        assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "x\n"
        assert src.exists(), "copy must not remove the source"

    def test_move_takes_path_as_well_as_src(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("x\n", encoding="utf-8")
        r = engine.fs_write(ops=[{"op": "move", "path": str(src), "dst": str(tmp_path / "b.txt")}])
        assert r["success"] is True, r.get("error")
        assert not src.exists()
        assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "x\n"

    def test_src_still_works(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("x\n", encoding="utf-8")
        r = engine.fs_write(ops=[{"op": "copy", "src": str(src), "dst": str(tmp_path / "b.txt")}])
        assert r["success"] is True, r.get("error")

    def test_src_wins_when_both_are_given(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("real\n", encoding="utf-8")
        (tmp_path / "other.txt").write_text("other\n", encoding="utf-8")
        r = engine.fs_write(
            ops=[
                {
                    "op": "copy",
                    "src": str(real),
                    "path": str(tmp_path / "other.txt"),
                    "dst": str(tmp_path / "out.txt"),
                }
            ]
        )
        assert r["success"] is True, r.get("error")
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "real\n"

    def test_rename_takes_new_name_and_dst(self, tmp_path):
        for given, target in (("new_name", "one.txt"), ("dst", "two.txt")):
            src = tmp_path / f"src_{given}.txt"
            src.write_text("x\n", encoding="utf-8")
            r = engine.fs_write(ops=[{"op": "rename", "path": str(src), given: target}])
            assert r["success"] is True, r.get("error")
            assert (tmp_path / target).exists()

    def test_a_missing_source_names_both_spellings(self, tmp_path):
        r = engine.fs_write(ops=[{"op": "copy", "dst": str(tmp_path / "b.txt")}])
        assert r["success"] is False
        assert "src" in r["error"] and "path" in r["error"], r["error"]


class TestASizeThatExistsIsNotReportedAsZero:
    """A 900-byte file read as "0 KB" in the delete confirmation.

    _get_size_kb used integer division, so everything under 1024 bytes became 0
    -- and that number appears in "Permanently deletes 1 item(s) (0 KB). Cannot
    be undone." A sweep hit the same shape on the ML side, where two real
    34-byte snapshots listed as size_kb 0.0, which is what an empty backup looks
    like and is the number someone decides a restore on.
    """

    @pytest.mark.parametrize(
        "n_bytes,expected_nonzero",
        [(0, False), (1, True), (34, True), (900, True), (1024, True), (5000, True)],
    )
    def test_only_an_empty_file_reports_zero(self, n_bytes, expected_nonzero):
        from shared.file_utils import size_kb

        assert (size_kb(n_bytes) > 0) is expected_nonzero, n_bytes

    def test_a_kilobyte_still_reads_as_one(self):
        from shared.file_utils import size_kb

        assert size_kb(1024) == 1.0
        assert size_kb(1536) == 1.5

    def test_the_delete_warning_does_not_say_zero_for_a_real_file(self, tmp_path):
        small = tmp_path / "small.txt"
        small.write_text("x" * 900, encoding="utf-8")
        r = engine.fs_write(ops=[{"op": "delete_request", "path": str(small)}])
        assert r["success"] is True, r.get("error")
        assert r["targets"][0]["size_kb"] > 0, r["targets"][0]
        assert "(0 KB)" not in r["warning"], r["warning"]
