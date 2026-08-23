"""A query against an unindexed directory looked exactly like an empty one.

`fs_index action=query` answers from SQLite, which knows only what
`action=build` put there. Ask it about a directory nobody has built and it
returns `success: true, returned: 0` -- byte-identical to the answer for a
directory that really is empty.

Round 11's sweep hit this twice and concluded the tool was broken, which is the
reasonable reading of a bare zero. Measured against the live endpoint:

    query  pattern=* path=/workspace/data/ml_advanced_2   -> returned 0
    query  pattern=*                                      -> returned 6
    build  path=/workspace/data/ml_advanced_2             -> indexed 8
    query  pattern=* path=/workspace/data/ml_advanced_2   -> returned 8

Eight files, on disk the whole time. The response carried no root, no build
time and no hint, and the one staleness warning that exists fires at 24 hours,
so it had nothing to say about an index minutes old that had simply never
covered that path.
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


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private HOME, so the index under test is this test's own."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def indexed_tree(home):
    """One directory in the index, one beside it that is not."""
    inside = home / "indexed"
    inside.mkdir()
    (inside / "one.txt").write_bytes(b"a\n")
    (inside / "two.txt").write_bytes(b"b\n")

    outside = home / "unindexed"
    outside.mkdir()
    (outside / "three.txt").write_bytes(b"c\n")
    (outside / "four.txt").write_bytes(b"d\n")

    r = engine.fs_index(action="build", path=str(inside))
    assert r["success"] is True, r.get("error")
    return inside, outside


class TestAnUnindexedPathSaysSo:
    def test_the_query_still_succeeds(self, indexed_tree):
        _, outside = indexed_tree
        r = engine.fs_index(action="query", pattern="*", path=str(outside))
        assert r["success"] is True
        assert r["returned"] == 0

    def test_it_reports_that_the_root_holds_nothing(self, indexed_tree):
        _, outside = indexed_tree
        r = engine.fs_index(action="query", pattern="*", path=str(outside))
        assert r["indexed_under_root"] == 0, r

    def test_the_hint_distinguishes_it_from_no_such_file(self, indexed_tree):
        _, outside = indexed_tree
        r = engine.fs_index(action="query", pattern="*", path=str(outside))
        hint = r.get("hint", "")
        assert "not the same as no such file" in hint, hint
        assert "action=build" in hint, hint

    def test_the_hint_names_the_path_to_build(self, indexed_tree):
        _, outside = indexed_tree
        r = engine.fs_index(action="query", pattern="*", path=str(outside))
        assert str(outside) in r.get("hint", ""), r.get("hint")

    def test_building_it_makes_the_files_appear(self, indexed_tree):
        _, outside = indexed_tree
        engine.fs_index(action="build", path=str(outside))
        r = engine.fs_index(action="query", pattern="*", path=str(outside))
        assert r["returned"] == 2, r
        assert r["indexed_under_root"] == 2, r


class TestAnIndexedPathWithNoMatchSaysSomethingElse:
    def test_the_root_is_reported_as_covered(self, indexed_tree):
        inside, _ = indexed_tree
        r = engine.fs_index(action="query", pattern="*.nope", path=str(inside))
        assert r["returned"] == 0
        assert r["indexed_under_root"] == 2, r

    def test_the_hint_does_not_claim_the_root_is_unindexed(self, indexed_tree):
        inside, _ = indexed_tree
        r = engine.fs_index(action="query", pattern="*.nope", path=str(inside))
        hint = r.get("hint", "")
        assert "not the same as no such file" not in hint, hint
        assert "none match" in hint, hint


class TestTheAnswerSaysWhatItFilteredOn:
    def test_query_reports_its_root_like_list_does(self, indexed_tree):
        inside, _ = indexed_tree
        r = engine.fs_index(action="query", pattern="*", path=str(inside))
        assert r["root"] == str(inside), r.get("root")

    def test_an_unfiltered_query_reports_home(self, indexed_tree, home):
        r = engine.fs_index(action="query", pattern="*")
        assert r["root"] == str(home), r.get("root")

    def test_a_matching_query_carries_no_hint(self, indexed_tree):
        inside, _ = indexed_tree
        r = engine.fs_index(action="query", pattern="one.txt", path=str(inside))
        assert r["returned"] == 1, r
        assert "hint" not in r, r["hint"]
