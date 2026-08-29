"""fs_query said "there is more" when there was not, depending on scan order.

grep mode stopped as soon as it had `max_results` matches and then set
`truncated` from `idx < len(name_matches) - 1` -- whether any *unscanned file*
remained, not whether any further file *matched*. Those are different questions
whenever the name pattern selects files that do not contain the term, which is
the normal case: fs_write leaves a `.mcp_receipt.json` beside every file it
writes, so a directory of five written files holds ten entries.

With exactly five matches and max_results=5 the answer then depended on where
the five matching files happened to fall in directory iteration order. The same
five files, off the same commit, reported truncated: true in CI and
truncated: false against the deployed server -- and the smoke test assertion
written to catch this ("false positive at exact cap") passed or failed on that
coin flip. A caller reading truncated: true narrows the pattern or pages, and
finds nothing further.

The fix collects one match past the cap and reports on what was found, which is
what the name-pattern branch in the same function already did. These tests fix
the iteration order both ways so neither outcome can be luck.
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

fs_query = engine.fs_query


def _make_dir(tmp_path: Path, n_matching: int, n_decoy: int) -> Path:
    """n_matching files containing the term, plus n_decoy that do not."""
    d = tmp_path / "grep_cap"
    d.mkdir()
    for i in range(n_matching):
        (d / f"m{i}.txt").write_text("boundarytoken\n", encoding="utf-8")
    for i in range(n_decoy):
        (d / f"m{i}.txt.mcp_receipt.json").write_text('{"op": "write_file"}\n', encoding="utf-8")
    return d


class TestExactlyAtTheCap:
    def test_five_matches_with_max_five_is_not_truncated(self, tmp_path):
        # Twenty decoys, not the five a real run leaves, so that the old logic
        # cannot pass by luck: it computed the flag from the position of the
        # last match, so it only came out right when the matches happened to be
        # scanned last. With five decoys that is a coin flip -- which is how
        # this survived in the smoke test for so long.
        d = _make_dir(tmp_path, n_matching=5, n_decoy=20)
        r = fs_query(
            pattern="*", path=str(d), content="boundarytoken", grep_mode=True, max_results=5
        )
        assert r["success"] is True, r.get("error")
        assert len(r["matches"]) == 5
        assert r["truncated"] is False, (
            "exactly max_results matches is not truncation; a caller told to narrow "
            "the pattern here finds nothing further"
        )

    @pytest.mark.parametrize("n_decoy", [0, 1, 5, 20])
    def test_the_answer_does_not_depend_on_how_many_non_matching_files_sit_beside_them(
        self, tmp_path, n_decoy
    ):
        """The old flag was computed from the position of the last match within
        the *candidate* list, so adding non-matching files changed it."""
        d = _make_dir(tmp_path, n_matching=5, n_decoy=n_decoy)
        r = fs_query(
            pattern="*", path=str(d), content="boundarytoken", grep_mode=True, max_results=5
        )
        assert r["truncated"] is False, f"{n_decoy} decoy file(s) flipped the flag"


class TestGenuineTruncationStillReported:
    def test_six_matches_with_max_five_is_truncated(self, tmp_path):
        d = _make_dir(tmp_path, n_matching=6, n_decoy=6)
        r = fs_query(
            pattern="*", path=str(d), content="boundarytoken", grep_mode=True, max_results=5
        )
        assert r["success"] is True, r.get("error")
        assert len(r["matches"]) == 5, "the cap must still be honoured"
        assert r["truncated"] is True, "a sixth match was dropped and not reported"

    def test_the_hint_says_how_to_see_the_rest(self, tmp_path):
        d = _make_dir(tmp_path, n_matching=6, n_decoy=0)
        r = fs_query(
            pattern="*", path=str(d), content="boundarytoken", grep_mode=True, max_results=5
        )
        assert r["truncated"] is True
        assert "max_results" in r.get("hint", "") or "Narrow" in r.get("hint", "")

    def test_far_more_matches_than_the_cap(self, tmp_path):
        d = _make_dir(tmp_path, n_matching=30, n_decoy=10)
        r = fs_query(
            pattern="*", path=str(d), content="boundarytoken", grep_mode=True, max_results=5
        )
        assert len(r["matches"]) == 5
        assert r["truncated"] is True


class TestNothingMatchesAtAll:
    def test_no_matches_is_not_truncated(self, tmp_path):
        d = _make_dir(tmp_path, n_matching=0, n_decoy=5)
        r = fs_query(
            pattern="*", path=str(d), content="boundarytoken", grep_mode=True, max_results=5
        )
        assert r["success"] is True, r.get("error")
        assert r["matches"] == []
        assert r["truncated"] is False
