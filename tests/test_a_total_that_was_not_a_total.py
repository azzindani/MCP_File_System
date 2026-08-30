"""fs_query reported `total_found` for a search that had not finished looking.

Found by round 22's sweep, cross-checking a tool's answer against a second
method rather than trusting its success flag:

    fs_query(path=..., content="campaign_platform")  ->  total_found: 97
    grep -R -I -l "campaign_platform" | wc -l        ->  489

Both numbers were about the same tree. The tool gathered `max_results * 10`
paths -- 500 of 1,843 -- filtered THOSE by content, and reported the survivors
in a field called `total_found`. Nothing in the response said the walk had
stopped early. `truncated: true` was present but means "more matched than were
returned", which a caller reads as "there are exactly 97, you got 50".

Two separate faults, and the second is the one worth remembering:

  the cap        a scan budget was derived from a RESULTS budget, so asking for
                 fewer results silently searched less of the tree.
  the silence    a walk that hit its cap was indistinguishable from one that
                 finished, so a lower bound was reported as an exact count.

The name-only search was correct throughout (99 files, 99 reported) -- which is
why nothing looked wrong until someone recomputed the content search.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "servers" / "fs_basic"))

from _basic_query import run_fs_query  # noqa: E402


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    """A tree with more files than a small scan budget will reach.

    Every file contains the needle, so the correct total is knowable exactly
    and any shortfall is the scan, not the matching.
    """
    monkeypatch.setenv("MCP_ALLOWED_ROOT", str(tmp_path))
    for i in range(120):
        (tmp_path / f"file_{i:03d}.txt").write_text("needle here\n")
    return tmp_path


def test_a_complete_scan_says_so_and_counts_exactly(tree, monkeypatch):
    monkeypatch.setenv("MCP_MAX_SCAN_FILES", "10000")
    result = run_fs_query(pattern="*.txt", path=str(tree), content="needle")
    assert result["success"], result
    assert result["scan_complete"] is True
    assert result["total_found"] == 120
    # No lower-bound marker on a search that finished.
    assert "total_found_is_lower_bound" not in result


def test_a_capped_scan_admits_the_count_is_a_lower_bound(tree, monkeypatch):
    """The whole defect in one assertion: an incomplete search must say so."""
    monkeypatch.setenv("MCP_MAX_SCAN_FILES", "30")
    result = run_fs_query(pattern="*.txt", path=str(tree), content="needle")
    assert result["success"], result
    assert result["scan_complete"] is False
    assert result["total_found_is_lower_bound"] is True
    assert result["files_scanned"] == 30
    assert result["total_found"] < 120
    # And the hint names what to do about it, not just that it happened.
    assert "lower bound" in result["hint"]
    assert "MCP_MAX_SCAN_FILES" in result["hint"] or "subdirectory" in result["hint"]


def test_the_scan_budget_is_not_derived_from_max_results(tree, monkeypatch):
    """Asking for fewer results must not search less of the tree.

    This is the fault that produced the wrong number: the walk gathered
    `max_results * 10` paths, so max_results=5 searched 50 files and max_results
    =50 searched 500, and `total_found` moved with a parameter that is supposed
    to bound only how much comes back.
    """
    monkeypatch.setenv("MCP_MAX_SCAN_FILES", "10000")
    few = run_fs_query(pattern="*.txt", path=str(tree), content="needle", max_results=5)
    many = run_fs_query(pattern="*.txt", path=str(tree), content="needle", max_results=50)
    assert few["total_found"] == many["total_found"] == 120
    assert few["returned"] == 5
    assert few["scan_complete"] is True


def test_grep_mode_reports_scan_completeness_too(tree, monkeypatch):
    """The other return path. It builds its own response and was missed."""
    monkeypatch.setenv("MCP_MAX_SCAN_FILES", "30")
    result = run_fs_query(pattern="*.txt", path=str(tree), content="needle", grep_mode=True)
    assert result["success"], result
    assert result["scan_complete"] is False
    assert result["files_scanned"] == 30


def test_a_name_only_search_is_unaffected(tree, monkeypatch):
    """Name matching was always exact; the fix must not change it."""
    monkeypatch.setenv("MCP_MAX_SCAN_FILES", "10000")
    result = run_fs_query(pattern="*.txt", path=str(tree))
    assert result["total_found"] == 120
    assert result["scan_complete"] is True
