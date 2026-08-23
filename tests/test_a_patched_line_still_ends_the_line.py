"""Replacing a line without a trailing newline welded it to the next one.

    before:  line one\\nline two\\nline three\\n
    op:      patch_lines start_line=1 end_line=2 content="PATCHED"
    after:   line one\\nPATCHEDline three\\n
    success: True

Three lines became two and nothing said so. `content` is the text of a line, and
a caller writing the text of a line does not type its terminator -- fs_read does
not show one, and no editor makes you add one. The op is named patch_*lines*, so
producing something that is not a line is the tool's mistake, not the caller's.

insert_after has always terminated its own content:

    to_insert = insert_content if insert_content.endswith("\\n") else insert_content + "\\n"

patch_lines was the sibling that did not. The terminator is copied from the
region being replaced rather than added unconditionally, so patching the final
line of a file that ends without a newline does not quietly give it one, and
patching the final line of a file that does ends the file the same way it began.

Found in the notes column of a round-8 sweep report that recorded the op as PASS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from servers.fs_basic import engine

THREE_LINES = "line one\nline two\nline three\n"
NO_FINAL_NEWLINE = "line one\nline two\nline three"


def patch(path: Path, start: int, end: int, content: str) -> dict:
    return engine.fs_write(
        ops=[
            {
                "op": "patch_lines",
                "path": str(path),
                "start_line": start,
                "end_line": end,
                "content": content,
            }
        ]
    )


@pytest.fixture()
def three_lines(work_dir: Path) -> Path:
    p = work_dir / "a.txt"
    p.write_text(THREE_LINES, encoding="utf-8")
    return p


class TestTheSweepsCall:
    def test_the_lines_are_not_welded_together(self, three_lines: Path):
        r = patch(three_lines, 1, 2, "PATCHED")
        assert r["success"] is True, r.get("error")
        assert "PATCHEDline" not in three_lines.read_text(encoding="utf-8")

    def test_the_file_still_has_three_lines(self, three_lines: Path):
        patch(three_lines, 1, 2, "PATCHED")
        assert three_lines.read_text(encoding="utf-8").count("\n") == 3

    def test_the_replacement_is_a_line_of_its_own(self, three_lines: Path):
        patch(three_lines, 1, 2, "PATCHED")
        assert three_lines.read_text(encoding="utf-8") == "line one\nPATCHED\nline three\n"

    def test_a_caller_who_did_add_the_newline_gets_the_same_file(self, three_lines: Path):
        patch(three_lines, 1, 2, "PATCHED\n")
        assert three_lines.read_text(encoding="utf-8") == "line one\nPATCHED\nline three\n"


class TestTheFileEndingIsPreserved:
    def test_patching_the_last_line_keeps_the_final_newline(self, three_lines: Path):
        patch(three_lines, 2, 3, "LAST")
        assert three_lines.read_text(encoding="utf-8") == "line one\nline two\nLAST\n"

    def test_a_file_without_a_final_newline_does_not_gain_one(self, work_dir: Path):
        p = work_dir / "b.txt"
        p.write_text(NO_FINAL_NEWLINE, encoding="utf-8")
        patch(p, 2, 3, "LAST")
        assert p.read_text(encoding="utf-8") == "line one\nline two\nLAST"

    def test_that_file_is_not_given_a_newline_mid_patch_either(self, work_dir: Path):
        p = work_dir / "c.txt"
        p.write_text(NO_FINAL_NEWLINE, encoding="utf-8")
        patch(p, 0, 1, "FIRST")
        assert p.read_text(encoding="utf-8") == "FIRST\nline two\nline three"


class TestOtherShapesOfReplacement:
    def test_a_multi_line_replacement_terminates_its_last_line(self, three_lines: Path):
        patch(three_lines, 1, 2, "A\nB")
        assert three_lines.read_text(encoding="utf-8") == "line one\nA\nB\nline three\n"

    def test_an_empty_replacement_deletes_the_range(self, three_lines: Path):
        patch(three_lines, 1, 2, "")
        assert three_lines.read_text(encoding="utf-8") == "line one\nline three\n"

    def test_replacing_every_line_leaves_one_line(self, three_lines: Path):
        patch(three_lines, 0, 3, "ONLY")
        assert three_lines.read_text(encoding="utf-8") == "ONLY\n"

    def test_a_replacement_ending_in_crlf_is_left_alone(self, work_dir: Path):
        """Read and write as bytes -- Path.read_text() would translate the
        newlines it is being asked to check."""
        p = work_dir / "d.txt"
        p.write_bytes(b"line one\r\nline two\r\n")
        patch(p, 0, 1, "FIRST\r\n")
        assert p.read_bytes() == b"FIRST\r\nline two\r\n"


class TestTheSiblingStillBehaves:
    def test_insert_after_still_terminates_its_content(self, three_lines: Path):
        engine.fs_write(
            ops=[
                {
                    "op": "insert_after",
                    "path": str(three_lines),
                    "after_pattern": "line one",
                    "content": "INSERTED",
                }
            ]
        )
        assert (
            three_lines.read_text(encoding="utf-8") == "line one\nINSERTED\nline two\nline three\n"
        )

    def test_delete_lines_is_unaffected(self, three_lines: Path):
        engine.fs_write(
            ops=[{"op": "delete_lines", "path": str(three_lines), "start_line": 1, "end_line": 2}]
        )
        assert three_lines.read_text(encoding="utf-8") == "line one\nline three\n"

    def test_a_dry_run_changes_nothing(self, three_lines: Path):
        r = engine.fs_write(
            ops=[
                {
                    "op": "patch_lines",
                    "path": str(three_lines),
                    "start_line": 1,
                    "end_line": 2,
                    "content": "PATCHED",
                }
            ],
            dry_run=True,
        )
        assert r["success"] is True, r.get("error")
        assert three_lines.read_text(encoding="utf-8") == THREE_LINES
