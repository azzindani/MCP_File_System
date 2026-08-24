"""max_results bounded the files, and nothing bounded the lines inside them.

fs_query with grep_mode=True returns one entry per matching file, each carrying
a `hits` list of matching lines. `max_results` was applied to the entry list and
never to the hits, so a term that matches few files but many lines came back
whole:

    content='Search Keywords', pattern='*.csv', max_results=50
      -> files 2, hits 30,202, 5,462,648 bytes, truncated: false

`truncated` was not lying about the file list -- both matching files were
returned, so by its own definition nothing was cut. It was describing the wrong
thing. A caller reading it sees a complete result and a response that will not
fit in any context window.

The tell is that the same tool looks correct on a term that matches many files:
max_results=5 returns 5 files, one hit each, truncated: true. Whether the cap
appeared to work depended entirely on how the matches were distributed.

There is now a budget on lines as well, `get_max_grep_hits()`, and either kind
of trimming sets `truncated`. The response also reports `hits_found` beside
`hits_returned`, so a caller can tell how much it is not seeing.
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

from shared.platform_utils import get_max_grep_hits  # noqa: E402


@pytest.fixture
def haystack(tmp_path) -> Path:
    """Two files, one term, far more matching lines than any budget."""
    for name in ("a.csv", "b.csv"):
        rows = ["header"] + [f"row {i} Search Keywords" for i in range(4000)]
        (tmp_path / name).write_text("\n".join(rows) + "\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def scattered(tmp_path) -> Path:
    """Many files, one matching line each -- the case that always looked fine.

    Eight, not thirty: constrained mode caps max_results at 10, and a fixture
    above that cap makes the "nothing was trimmed" test depend on which mode
    the suite is running in.
    """
    for i in range(8):
        (tmp_path / f"f{i:02d}.txt").write_text(
            f"line\nSearch Keywords {i}\nline\n", encoding="utf-8"
        )
    return tmp_path


def grep(root: Path, **kw) -> dict:
    args = {
        "pattern": "*",
        "path": str(root),
        "content": "Search Keywords",
        "grep_mode": True,
        "max_results": 50,
    }
    args.update(kw)
    return engine.fs_query(**args)


def hits_in(result: dict) -> int:
    return sum(len(m.get("hits", [])) for m in result.get("matches", []))


class TestTheLinesAreBounded:
    def test_a_few_files_with_many_lines_does_not_dump_them_all(self, haystack):
        r = grep(haystack)
        assert r["success"] is True, r.get("error")
        assert hits_in(r) <= get_max_grep_hits(), hits_in(r)

    def test_the_response_stays_small(self, haystack):
        r = grep(haystack)
        # 8,000 matching lines were 5.5 MB. Anything in that region is unusable.
        assert len(str(r)) < 200_000, len(str(r))

    def test_it_says_it_trimmed(self, haystack):
        r = grep(haystack)
        assert r["truncated"] is True, "8,000 matching lines came back as a complete result"

    def test_the_hint_names_the_line_budget(self, haystack):
        r = grep(haystack)
        assert "line" in r.get("hint", "").lower(), r.get("hint")

    def test_constrained_mode_is_tighter(self, haystack, monkeypatch):
        monkeypatch.setenv("MCP_CONSTRAINED_MODE", "1")
        r = grep(haystack)
        assert hits_in(r) <= get_max_grep_hits()


class TestItSaysHowMuchItLeftOut:
    def test_hits_found_counts_everything(self, haystack):
        r = grep(haystack)
        assert r["hits_found"] == 8000, r["hits_found"]

    def test_hits_returned_matches_the_payload(self, haystack):
        r = grep(haystack)
        assert r["hits_returned"] == hits_in(r)

    def test_returned_is_still_the_file_count(self, haystack):
        r = grep(haystack)
        assert r["returned"] == len(r["matches"])

    def test_a_trimmed_file_reports_its_real_total(self, haystack):
        r = grep(haystack)
        trimmed = [m for m in r["matches"] if "hits_total" in m]
        assert trimmed, "no file recorded how many hits it really had"
        assert trimmed[0]["hits_total"] == 4000, trimmed[0]["hits_total"]


class TestTheCaseThatAlwaysWorkedStillWorks:
    def test_many_files_one_hit_each_is_capped_by_files(self, scattered):
        r = grep(scattered, max_results=5)
        assert r["returned"] == 5, r["returned"]
        assert r["truncated"] is True

    def test_an_uncapped_search_is_not_marked_truncated(self, scattered):
        r = grep(scattered, max_results=50)
        assert r["returned"] == 8, r["returned"]
        assert r["truncated"] is False
        assert "hint" not in r or "line" not in r["hint"].lower()

    def test_the_matches_themselves_are_still_right(self, scattered):
        r = grep(scattered, max_results=50)
        for m in r["matches"]:
            for h in m["hits"]:
                assert "Search Keywords" in h["text"]

    def test_a_term_that_matches_nothing_is_unaffected(self, scattered):
        r = grep(scattered, content="zzz_not_here")
        assert r["success"] is True
        assert r["returned"] == 0
        assert r["truncated"] is False


class TestTheResponseContract:
    def test_token_estimate_is_present(self, haystack):
        r = grep(haystack)
        assert isinstance(r["token_estimate"], int) and r["token_estimate"] > 0

    def test_token_estimate_reflects_the_trimmed_body(self, haystack):
        r = grep(haystack)
        # It was computed from the full 5.5 MB body before; it should now be
        # small enough that a caller budgeting on it is not misled.
        assert r["token_estimate"] < 60_000, r["token_estimate"]

    def test_progress_reports_the_line_count(self, haystack):
        r = grep(haystack)
        assert any("line" in str(p).lower() for p in r["progress"]), r["progress"]
