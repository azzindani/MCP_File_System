"""`fs_index action=list` said it truncated and never said out of how many.

    {"entries": [...50...], "returned": 50, "truncated": true,
     "hint": "Results capped at 50. Use action=query with a pattern to narrow"}

Fifty of fifty-one and fifty of seven hundred thousand are the same response,
and the hint says narrow either way. `stats` in the same module has always
reported `entry_count`, and `_action_query` already runs the `COUNT(*)` this
needed -- `list` was the sibling nobody went back for.

This is the same family as `test_a_total_that_was_not_a_total.py`, which
covers `fs_query(content=)`. That one is about a total that lied; this one is
about a total that was never there. Both are the reason `shared/counts.py`
derives `truncated` from the two numbers instead of accepting it as an
argument: a flag computed separately is a flag that can disagree.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from shared.counts import count_violations, counted

# --------------------------------------------------------------------------
# the contract itself
# --------------------------------------------------------------------------


def test_truncated_is_derived_not_asserted():
    assert counted(50, 51) == {"returned": 50, "total": 51, "truncated": True}
    assert counted(20, 20) == {"returned": 20, "total": 20, "truncated": False}


def test_a_returned_above_its_total_is_loud():
    # The two counts were taken over different sets. Clamping this quietly is
    # how 20-of-25 shipped as complete somewhere else.
    with pytest.raises(ValueError, match="different sets"):
        counted(25, 20)


def test_an_unknown_total_keeps_the_denominator_and_marks_it_a_floor():
    out = counted(50, 50, exact=False)
    assert out["total"] == 50
    assert out["total_is_lower_bound"] is True


@pytest.mark.parametrize(
    "payload, expect",
    [
        ({"returned": 50, "total": 700000, "truncated": True}, []),
        ({"returned": 50, "truncated": True}, ["no `total`"]),
        ({"returned": 20, "total": 25, "truncated": False}, ["should be True"]),
        ({"entries": []}, []),  # no `returned` -> contract does not apply
    ],
)
def test_count_violations_names_the_breach(payload, expect):
    problems = " ".join(count_violations(payload))
    for fragment in expect:
        assert fragment in problems
    if not expect:
        assert problems == ""


# --------------------------------------------------------------------------
# the tool that earned it
# --------------------------------------------------------------------------


@pytest.fixture()
def indexed_home(tmp_path, monkeypatch):
    """A home with more files than the list cap, and an index over it.

    The mode is pinned, not inherited. `get_max_results()` is 50 unconstrained
    and 10 constrained, so a test that asserts a cap and reads the mode from
    the environment passes under one CI job and fails under the other -- which
    is what the constrained-mode job exists to catch, and it caught this file.
    """
    monkeypatch.delenv("MCP_CONSTRAINED_MODE", raising=False)
    home = tmp_path / "home"
    (home / "docs").mkdir(parents=True)
    for i in range(12):
        (home / "docs" / f"f{i:02d}.txt").write_text("x")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def _list_action(max_results: int) -> dict:
    import servers.fs_basic._basic_index as idx

    return idx._action_list("", max_results)


def test_list_reports_the_total_it_capped_against(indexed_home):
    import servers.fs_basic._basic_index as idx

    idx._action_build(str(indexed_home))
    result = _list_action(5)

    assert result["returned"] == 5
    assert result["truncated"] is True
    # The number the old response could not give: what 5 is 5 *of*.
    assert result["total"] >= 12
    assert count_violations(result) == []
    assert str(result["total"]) in result["hint"]


def test_list_that_returned_everything_is_not_marked_truncated(indexed_home):
    import servers.fs_basic._basic_index as idx

    idx._action_build(str(indexed_home))
    result = _list_action(500)

    assert result["truncated"] is False
    assert result["returned"] == result["total"]
    assert count_violations(result) == []
    assert "hint" not in result


def test_the_total_is_scoped_to_the_root_the_rows_came_from(indexed_home, tmp_path):
    """A denominator taken over a different WHERE than the rows is the bug."""
    import servers.fs_basic._basic_index as idx

    other = indexed_home / "other"
    other.mkdir()
    for i in range(4):
        (other / f"o{i}.txt").write_text("y")

    idx._action_build(str(indexed_home))
    scoped = idx._action_list(str(indexed_home / "docs"), 500)
    whole = idx._action_list("", 500)

    assert count_violations(scoped) == []
    # The subtree holds its own directory row plus the twelve files in it.
    assert scoped["total"] == scoped["returned"] == 13
    # And that is the point: the denominator follows the WHERE the rows came
    # from. A total taken over the whole index would report the other four
    # files too, and `truncated` derived from it would then be wrong.
    assert whole["total"] > scoped["total"]


def test_constrained_mode_shrinks_the_page_and_not_the_total(indexed_home, monkeypatch):
    """The cap is what changed; the denominator is a property of the data.

    This is the case the old response could not express at all: a tight cap is
    exactly when a caller most needs to know how much it is not seeing.
    """
    import servers.fs_basic._basic_index as idx

    idx._action_build(str(indexed_home))
    unconstrained_total = _list_action(500)["total"]

    monkeypatch.setenv("MCP_CONSTRAINED_MODE", "1")
    tight = _list_action(500)

    assert tight["returned"] == 10  # get_max_results() under constraint
    assert tight["total"] == unconstrained_total
    assert tight["truncated"] is True
    assert count_violations(tight) == []


def test_the_db_is_only_opened_once(indexed_home, monkeypatch):
    """The COUNT(*) rides the connection the rows came from, not a new one."""
    import servers.fs_basic._basic_index as idx

    idx._action_build(str(indexed_home))
    opened = []
    real = idx._get_conn

    def counting_conn():
        opened.append(1)
        return real()

    monkeypatch.setattr(idx, "_get_conn", counting_conn)
    idx._action_list("", 5)
    assert len(opened) == 1


def test_sqlite_count_matches_what_the_tool_reports(indexed_home):
    import servers.fs_basic._basic_index as idx

    idx._action_build(str(indexed_home))
    result = _list_action(3)

    conn = sqlite3.connect(idx._db_path())
    try:
        truth = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    finally:
        conn.close()
    assert result["total"] == truth
