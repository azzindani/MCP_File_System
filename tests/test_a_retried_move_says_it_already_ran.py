"""A retry of a move that already succeeded must not be told to retry again.

move, rename and copy resolve their source with must_exist=True, so a client
re-sending one whose first attempt timed out gets FileNotFoundError, which fell
into the catch-all handler:

    error: "Path does not exist: /workspace/data/fsw1/mv_src.txt"
    hint:  "Retry op=move with corrected parameters."

That advice is wrong twice over. Retrying will not help -- the source is gone
because the move worked -- and there are no parameters to correct. The caller
cannot tell "already done" from "never valid", and the hint pushes it toward the
one action guaranteed to fail again.

Found by round 11's axis: the sweep called every op twice with identical
arguments, and move, rename and replace_text all failed the second call.

The op still fails -- it genuinely cannot move a file that is not there, and
inventing success would be worse -- but the hint now says which of the two
situations the caller is in, by looking at whether the destination holds the
file.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402


def write(op: str, **kw) -> dict:
    outer = engine.fs_write([dict(op=op, **kw)])
    return outer.get("results", [outer])[0] if isinstance(outer, dict) else outer


class TestARetriedMove:
    def test_the_first_call_succeeds(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("move me\n", encoding="utf-8")
        dst = tmp_path / "b.txt"
        assert write("move", src=str(src), dst=str(dst))["success"] is True
        assert dst.read_text(encoding="utf-8") == "move me\n"

    def test_the_second_call_says_it_looks_already_applied(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("move me\n", encoding="utf-8")
        dst = tmp_path / "b.txt"
        write("move", src=str(src), dst=str(dst))

        r = write("move", src=str(src), dst=str(dst))
        assert r["success"] is False
        assert "already" in r["hint"].lower(), r["hint"]
        assert "b.txt" in r["hint"], r["hint"]

    def test_it_no_longer_tells_the_caller_to_retry(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("move me\n", encoding="utf-8")
        dst = tmp_path / "b.txt"
        write("move", src=str(src), dst=str(dst))
        assert (
            "Retry op=move with corrected parameters"
            not in write("move", src=str(src), dst=str(dst))["hint"]
        )

    def test_a_genuinely_wrong_path_is_told_so(self, tmp_path):
        # Nothing at the source and nothing at the destination: this really is a
        # bad call, and the hint must not claim it already ran.
        r = write("move", src=str(tmp_path / "never.txt"), dst=str(tmp_path / "nor.txt"))
        assert r["success"] is False
        assert "already" not in r["hint"].lower(), r["hint"]
        assert "fs_read" in r["hint"], r["hint"]

    def test_the_moved_file_is_not_lost(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("move me\n", encoding="utf-8")
        dst = tmp_path / "b.txt"
        write("move", src=str(src), dst=str(dst))
        write("move", src=str(src), dst=str(dst))
        assert dst.read_text(encoding="utf-8") == "move me\n"


class TestARetriedRename:
    def test_the_second_call_says_it_looks_already_applied(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("rename me\n", encoding="utf-8")
        write("rename", path=str(f), name="b.txt")

        r = write("rename", path=str(f), name="b.txt")
        assert r["success"] is False
        assert "already" in r["hint"].lower(), r["hint"]
        assert "b.txt" in r["hint"], r["hint"]

    def test_a_genuinely_wrong_path_is_told_so(self, tmp_path):
        r = write("rename", path=str(tmp_path / "never.txt"), name="b.txt")
        assert r["success"] is False
        assert "already" not in r["hint"].lower(), r["hint"]


class TestARetriedCopy:
    def test_a_missing_source_with_the_copy_in_place_is_named(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("copy me\n", encoding="utf-8")
        dst = tmp_path / "b.txt"
        write("copy", src=str(src), dst=str(dst))
        src.unlink()

        r = write("copy", src=str(src), dst=str(dst))
        assert r["success"] is False
        assert "already" in r["hint"].lower(), r["hint"]


class TestARetryIsVisibleInTheAnswer:
    """A mutating op must report the state it produced, not only its delta.

    The sweep's sharpest observation in phase 2: `delete_lines` run twice with
    identical arguments removes a *second* line, and both calls answer
    `lines_removed: 1`. The responses are byte-identical while the file is not,
    so a client re-sending a call that timed out has no way to see it destroyed
    different content. `append_file` was the same -- "Appended to af_test.txt"
    both times, with the text landed twice.

    These ops cannot be made idempotent; appending twice is what appending twice
    means. What they can do is answer with the resulting count, which differs
    between the calls and is what a caller can check against its expectation.
    """

    def test_delete_lines_reports_the_resulting_count(self, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
        first = write("delete_lines", path=str(f), start_line=1, end_line=2)
        second = write("delete_lines", path=str(f), start_line=1, end_line=2)
        assert first["total_lines"] == 3, first
        assert second["total_lines"] == 2, second
        assert first != second, "a retry answered identically while removing another line"

    def test_delete_lines_matches_the_file(self, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        r = write("delete_lines", path=str(f), start_line=0, end_line=1)
        assert r["total_lines"] == len(f.read_text(encoding="utf-8").splitlines())

    def test_append_reports_the_resulting_size(self, tmp_path):
        # Sizes come from the file rather than from counted characters: on
        # Windows write_text turns each "\n" into two bytes, so the literals
        # 11 and 16 were asserting the platform, not the behaviour.
        f = tmp_path / "log.txt"
        f.write_bytes(b"start\n")
        first = write("append_file", path=str(f), content="more\n")
        after_first = f.stat().st_size
        second = write("append_file", path=str(f), content="more\n")
        assert first["size_bytes"] == after_first, first
        assert second["size_bytes"] == f.stat().st_size, second
        assert first["size_bytes"] != second["size_bytes"]

    def test_append_size_matches_the_file(self, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("start\n", encoding="utf-8")
        r = write("append_file", path=str(f), content="more\n")
        assert r["size_bytes"] == f.stat().st_size

    def test_insert_after_reports_the_resulting_count(self, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("alpha\nbeta\n", encoding="utf-8")
        first = write("insert_after", path=str(f), after_pattern="alpha", content="new\n")
        second = write("insert_after", path=str(f), after_pattern="alpha", content="new\n")
        assert first["total_lines"] == 3, first
        assert second["total_lines"] == 4, second

    def test_patch_lines_reports_the_resulting_count(self, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        r = write("patch_lines", path=str(f), start_line=0, end_line=1, content="one\ntwo\n")
        assert r["total_lines"] == 4, r
        assert r["total_lines"] == len(f.read_text(encoding="utf-8").splitlines())
