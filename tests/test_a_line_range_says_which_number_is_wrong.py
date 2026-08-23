"""One sentence blamed the file's length for every bad line range.

A coverage sweep ran patch_lines against a six-line file asking for line 5:

    op=patch_lines  start_line=5  end_line=5
      error: "Invalid line range [5, 5) for file with 6 lines"
      hint : "Use fs_read to inspect line numbers."

Nothing was out of range. start_line 5 is a real line of a six-line file; the
range was empty because both bounds were the same. The message named the file's
length -- the one number that was fine -- and the hint sent the caller to go and
read line numbers they had just read. It also printed the *clamped* bounds
rather than the ones the caller sent, so start_line=-5 was quoted back as 0,
the same way delete_paragraph used to quote back an index nobody sent.

The bracket notation was carrying the whole contract: fs_write's docstring is
66 characters and its `ops` schema is an opaque list[dict], so `[5, 5)` was the
only statement anywhere that line numbers are 0-based and end_line is
exclusive -- and a caller could only reach it by getting the call wrong.

Both delete_lines and patch_lines had the identical five lines.

Found by giving fs_write a phase of its own and running all sixteen ops.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from servers.fs_basic import engine

# The sweep's file: six lines, and it asked for the sixth.
SIX_LINES = "alpha one\nbravo two\nCHARLIE three\ndelta four\necho five\nfoxtrot six\n"

RANGE_OPS = ["delete_lines", "patch_lines"]


def _op(name: str, path: Path, start: int, end: int) -> dict:
    op: dict = {"op": name, "path": str(path), "start_line": start, "end_line": end}
    if name == "patch_lines":
        op["content"] = "replacement\n"
    return op


@pytest.fixture()
def six_line_file(work_dir: Path) -> Path:
    p = work_dir / "a.txt"
    p.write_text(SIX_LINES, encoding="utf-8")
    return p


class TestTheCallTheSweepMade:
    @pytest.mark.parametrize("name", RANGE_OPS)
    def test_it_no_longer_blames_the_file_length(self, six_line_file: Path, name: str):
        r = engine.fs_write(ops=[_op(name, six_line_file, 5, 5)])
        assert r["success"] is False
        assert "6 lines" not in r["error"], r["error"]

    @pytest.mark.parametrize("name", RANGE_OPS)
    def test_it_says_the_range_is_empty(self, six_line_file: Path, name: str):
        r = engine.fs_write(ops=[_op(name, six_line_file, 5, 5)])
        assert "empty" in r["error"].lower(), r["error"]

    @pytest.mark.parametrize("name", RANGE_OPS)
    def test_it_names_both_arguments_the_caller_sent(self, six_line_file: Path, name: str):
        r = engine.fs_write(ops=[_op(name, six_line_file, 5, 5)])
        blob = f"{r['error']} {r['hint']}"
        assert "start_line" in blob and "end_line" in blob, blob

    @pytest.mark.parametrize("name", RANGE_OPS)
    def test_the_hint_gives_the_call_that_would_have_worked(self, six_line_file: Path, name: str):
        r = engine.fs_write(ops=[_op(name, six_line_file, 5, 5)])
        assert "start_line=5" in r["hint"] and "end_line=6" in r["hint"], r["hint"]

    @pytest.mark.parametrize("name", RANGE_OPS)
    def test_that_suggested_call_actually_works(self, six_line_file: Path, name: str):
        r = engine.fs_write(ops=[_op(name, six_line_file, 5, 6)])
        assert r["success"] is True, r.get("error")
        assert "foxtrot six" not in six_line_file.read_text(encoding="utf-8")


class TestTheConventionIsStated:
    @pytest.mark.parametrize("name", RANGE_OPS)
    @pytest.mark.parametrize("start,end", [(5, 5), (4, 2), (-5, 2), (99, 100)])
    def test_every_rejection_explains_the_indexing(
        self, six_line_file: Path, name: str, start: int, end: int
    ):
        r = engine.fs_write(ops=[_op(name, six_line_file, start, end)])
        assert r["success"] is False
        assert "0-based" in r["hint"], r["hint"]
        assert "exclusive" in r["hint"], r["hint"]


class TestEachWrongNumberIsNamed:
    @pytest.mark.parametrize("name", RANGE_OPS)
    def test_an_inverted_range_says_so(self, six_line_file: Path, name: str):
        r = engine.fs_write(ops=[_op(name, six_line_file, 4, 2)])
        assert r["success"] is False
        assert "end_line 2" in r["error"] and "start_line 4" in r["error"], r["error"]

    @pytest.mark.parametrize("name", RANGE_OPS)
    def test_a_start_past_the_end_says_where_the_end_is(self, six_line_file: Path, name: str):
        r = engine.fs_write(ops=[_op(name, six_line_file, 99, 100)])
        assert r["success"] is False
        assert "99" in r["error"], r["error"]
        assert "start_line=5" in r["hint"], r["hint"]

    @pytest.mark.parametrize("name", RANGE_OPS)
    def test_a_negative_start_is_quoted_back_as_sent(self, six_line_file: Path, name: str):
        """It used to be clamped to 0 and reported as 0 -- a value nobody sent."""
        r = engine.fs_write(ops=[_op(name, six_line_file, -5, 2)])
        assert r["success"] is False
        assert "-5" in r["error"], r["error"]

    @pytest.mark.parametrize("name", RANGE_OPS)
    def test_an_empty_file_does_not_offer_line_minus_one(self, work_dir: Path, name: str):
        empty = work_dir / "empty.txt"
        empty.write_text("", encoding="utf-8")
        r = engine.fs_write(ops=[_op(name, empty, 0, 1)])
        assert r["success"] is False
        assert "-1" not in r["hint"], r["hint"]


class TestTheOrdinaryCallsStillWork:
    def test_delete_lines_removes_the_range_it_names(self, six_line_file: Path):
        r = engine.fs_write(ops=[_op("delete_lines", six_line_file, 1, 3)])
        assert r["success"] is True, r.get("error")
        text = six_line_file.read_text(encoding="utf-8")
        assert "bravo two" not in text and "CHARLIE three" not in text
        assert "alpha one" in text and "delta four" in text

    def test_patch_lines_replaces_the_range_it_names(self, six_line_file: Path):
        r = engine.fs_write(ops=[_op("patch_lines", six_line_file, 0, 1)])
        assert r["success"] is True, r.get("error")
        text = six_line_file.read_text(encoding="utf-8")
        assert text.startswith("replacement\n")
        assert "alpha one" not in text

    @pytest.mark.parametrize("name", RANGE_OPS)
    def test_an_end_past_the_file_still_clamps_to_the_last_line(
        self, six_line_file: Path, name: str
    ):
        """end_line is allowed to overshoot -- only start_line has to be real."""
        r = engine.fs_write(ops=[_op(name, six_line_file, 4, 999)])
        assert r["success"] is True, r.get("error")
        assert "echo five" not in six_line_file.read_text(encoding="utf-8")

    @pytest.mark.parametrize("name", RANGE_OPS)
    def test_a_dry_run_of_a_bad_range_is_still_refused(self, six_line_file: Path, name: str):
        r = engine.fs_write(ops=[_op(name, six_line_file, 5, 5)], dry_run=True)
        assert r["success"] is False
        assert six_line_file.read_text(encoding="utf-8") == SIX_LINES
