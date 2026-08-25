"""A content search for text that was in the file returned nothing, twice over.

    fs_query(path="/workspace/data", content="Desktop,99+,7.77")
      -> success: true, matches: [], total_found: 0

    grep -rl "Desktop,99+,7.77" /workspace/data
      -> /workspace/data/fsr1/ad_copy_diff.csv

The line was a literal row out of a CSV. `_looks_like_regex()` saw the `+`,
decided the needle was a pattern, and `re.search` read `9+` as "one or more
nines" -- so the search asked for something the file does not contain and said
so under `success: true`. Fragments of the *same line* with no metacharacter in
them (`Desktop,99`, `7.77`) matched perfectly.

The heuristic cannot be fixed, because the question it asks cannot be answered
from the string. "Is `report.md` a filename or a pattern matching `reportXmd`?"
has no answer without the caller, and `fs_query` gave the caller nowhere to say:
there was no regex parameter, and the docstring said "name/content", never
mentioning that content was sometimes a regular expression. Every needle holding
a `.` -- a filename, a version, a decimal -- was quietly a pattern too, matching
*more* than asked rather than less, which is the same defect facing the other
way and much harder to notice.

So content is literal, `regex=True` opts in, and both paths say which reading
they used in `content_is_regex`.

The second silent zero is the same shape one level down: both matchers wrap
their body in `except Exception: return no-match`, so an unparseable pattern was
searched for in every file, failed to compile in each one, and reported nothing
found. It now fails once, up front, with the reason.

Found in a round-15 sweep report, reproduced three times by the model before it
wrote the row.
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

# The literal row from the sweep, and the needles it was probed with.
ROW = "Desktop,99+,7.77,9,9,9"


@pytest.fixture()
def csv_file(work_dir: Path) -> Path:
    f = work_dir / "ad_copy.csv"
    f.write_text(f"device,age,spends,impressions,clicks,link_clicks\n{ROW}\n", encoding="utf-8")
    return f


class TestALiteralNeedleIsFoundLiterally:
    @pytest.mark.parametrize(
        "needle",
        [
            "Desktop,99+,7.77",  # the one from the report
            "99+,7.77",
            "99+",
            "7.77",  # a `.` -- was a pattern too, and matched too much
            "Desktop,99",  # no metacharacter; worked before and must keep working
        ],
    )
    def test_it_finds_the_row(self, csv_file: Path, work_dir: Path, needle: str) -> None:
        r = engine.fs_query(path=str(work_dir), content=needle)
        assert r["success"] is True, r.get("error")
        assert [Path(m).name for m in r["matches"]] == [csv_file.name], (needle, r["matches"])

    def test_grep_mode_finds_the_line(self, csv_file: Path, work_dir: Path) -> None:
        r = engine.fs_query(path=str(work_dir), content="Desktop,99+,7.77", grep_mode=True)
        assert r["success"] is True, r.get("error")
        assert r["returned"] == 1, r
        assert r["matches"][0]["hits"][0]["text"] == ROW

    def test_a_dot_no_longer_matches_any_character(self, work_dir: Path) -> None:
        """The quieter half: as a pattern, `7.77` matched `7X77` as well."""
        (work_dir / "decoy.txt").write_text("7X77\n", encoding="utf-8")
        r = engine.fs_query(path=str(work_dir), content="7.77")
        assert [Path(m).name for m in r["matches"]] == [], r["matches"]


class TestTheResponseSaysHowItRead:
    def test_a_literal_search_says_so(self, csv_file: Path, work_dir: Path) -> None:
        r = engine.fs_query(path=str(work_dir), content="Desktop,99+,7.77")
        assert r["content_is_regex"] is False

    def test_a_regex_search_says_so(self, csv_file: Path, work_dir: Path) -> None:
        r = engine.fs_query(path=str(work_dir), content="Desktop,9+", regex=True)
        assert r["content_is_regex"] is True

    def test_grep_mode_says_so_too(self, csv_file: Path, work_dir: Path) -> None:
        r = engine.fs_query(path=str(work_dir), content=ROW, grep_mode=True)
        assert r["content_is_regex"] is False

    def test_a_name_only_search_does_not_claim_either(self, csv_file: Path, work_dir: Path) -> None:
        assert "content_is_regex" not in engine.fs_query(path=str(work_dir), pattern="*.csv")


class TestRegexIsStillAvailable:
    def test_it_matches_as_a_pattern_when_asked(self, csv_file: Path, work_dir: Path) -> None:
        r = engine.fs_query(path=str(work_dir), content=r"Desktop,\d+", regex=True)
        assert [Path(m).name for m in r["matches"]] == [csv_file.name], r["matches"]

    def test_a_quantifier_means_a_quantifier(self, csv_file: Path, work_dir: Path) -> None:
        """`9+9` as a pattern matches the `99` in the row; as text it is absent.

        Not `9+`: the row really does contain the characters `9` and `+` side by
        side, so a literal search for it correctly succeeds and the two readings
        agree. A test needs a needle they disagree about.
        """
        assert engine.fs_query(path=str(work_dir), content="9+9", regex=True)["matches"]
        assert not engine.fs_query(path=str(work_dir), content="9+9")["matches"]

    def test_grep_mode_takes_the_flag(self, csv_file: Path, work_dir: Path) -> None:
        r = engine.fs_query(path=str(work_dir), content=r"7\.77", regex=True, grep_mode=True)
        assert r["returned"] == 1, r


class TestAnUnusablePatternIsAnError:
    def test_it_is_refused_rather_than_returning_nothing(self, csv_file, work_dir: Path) -> None:
        r = engine.fs_query(path=str(work_dir), content="foo(bar", regex=True)
        assert r["success"] is False, r
        assert "regular expression" in r["error"], r["error"]

    def test_the_hint_names_the_way_out(self, csv_file, work_dir: Path) -> None:
        r = engine.fs_query(path=str(work_dir), content="foo(bar", regex=True)
        assert "regex=True" in r["hint"], r["hint"]

    def test_the_same_string_is_fine_as_text(self, work_dir: Path) -> None:
        (work_dir / "odd.txt").write_text("foo(bar\n", encoding="utf-8")
        r = engine.fs_query(path=str(work_dir), content="foo(bar")
        assert r["success"] is True
        assert [Path(m).name for m in r["matches"]] == ["odd.txt"]

    def test_grep_mode_is_refused_the_same_way(self, csv_file, work_dir: Path) -> None:
        r = engine.fs_query(path=str(work_dir), content="foo(bar", regex=True, grep_mode=True)
        assert r["success"] is False, r


class TestAnEmptyResultExplainsItself:
    def test_it_says_the_needle_was_read_literally(self, csv_file: Path, work_dir: Path) -> None:
        r = engine.fs_query(path=str(work_dir), content="nothing.here+at+all")
        assert r["total_found"] == 0
        assert "literal text" in r["hint"], r["hint"]
        assert "regex=True" in r["hint"], r["hint"]

    def test_a_plain_needle_keeps_the_older_hint(self, csv_file: Path, work_dir: Path) -> None:
        """No metacharacters, so the regex advice would be noise."""
        r = engine.fs_query(path=str(work_dir), content="absent")
        assert r["total_found"] == 0
        assert "regex=True" not in r["hint"], r["hint"]

    def test_grep_mode_explains_it_too(self, csv_file: Path, work_dir: Path) -> None:
        r = engine.fs_query(path=str(work_dir), content="nothing.here+at+all", grep_mode=True)
        assert r["returned"] == 0
        assert "regex=True" in r["hint"], r["hint"]
