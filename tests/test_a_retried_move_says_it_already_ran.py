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
