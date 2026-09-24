"""A caller's regular expression is stopped when it runs away, and answers as before when it doesn't.

fs_query(content=..., regex=True) and fs_write's replace_text matched with
Python's `re`, which has no timeout. `(a+)+$` over a 29-byte file was still
running when killed at 10 seconds, in the content filter, in grep mode and in
replace_text alike -- and the container has no ripgrep, so grep mode always
took the Python path. They now match in a worker process with a budget
(shared/regex_guard.py, MCP_REGEX_SECONDS):

- a runaway pattern is refused inside its budget, naming the pattern, and the
  file replace_text was pointed at is untouched;
- every other pattern finds and replaces exactly what `re` does;
- the guard module is the same file in every repo that ships it.
"""

from __future__ import annotations

import hashlib
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _basic_query  # noqa: E402
import engine  # noqa: E402

RUNAWAY = r"(a+)+$"
STUCK = "a" * 40 + "!\n"
TEXT = "order 12-A\nnone here\nref 7-bb and 9-c\n"


@pytest.fixture
def files(work_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MCP_REGEX_SECONDS", "1")
    monkeypatch.setattr(
        _basic_query, "get_content_backend", lambda: "python"
    )  # what the container has
    (work_dir / "stuck.txt").write_text(STUCK, encoding="utf-8")
    (work_dir / "orders.txt").write_text(TEXT, encoding="utf-8")
    (work_dir / "empty.txt").write_text("", encoding="utf-8")
    return work_dir


def _timed(fn):
    began = time.monotonic()
    out = fn()
    return out, time.monotonic() - began


class TestARunawayPatternIsStopped:
    @pytest.mark.parametrize("grep_mode", [False, True], ids=["filter", "grep"])
    def test_a_content_search(self, files, grep_mode):
        r, took = _timed(
            lambda: engine.fs_query(
                path=str(files), content=RUNAWAY, regex=True, grep_mode=grep_mode
            )
        )
        assert r["success"] is False
        assert RUNAWAY in r["error"] and "was still matching after 1s" in r["error"]
        assert "literal" in r["hint"]
        assert took < 8, f"stopped after {took:.1f}s against a 1s budget"

    def test_replace_text_leaves_the_file_untouched(self, files):
        f = files / "stuck.txt"
        op = {"op": "replace_text", "path": str(f), "find": RUNAWAY, "replace": "x", "regex": True}
        r, took = _timed(lambda: engine.fs_write([op]))
        assert r["success"] is False and RUNAWAY in str(r)
        assert took < 8
        assert f.read_text(encoding="utf-8") == STUCK


class TestEveryOtherPatternAnswersAsReDoes:
    @pytest.mark.parametrize("pattern", [r"\d+-\w+", r"^none", r"zzz", r"(?i)ORDER"])
    def test_the_filter_finds_the_same_files(self, files, pattern):
        r = engine.fs_query(path=str(files), content=pattern, regex=True)
        assert r["success"] is True, r
        want = sorted(
            p.name for p in files.iterdir() if re.search(pattern, p.read_text(encoding="utf-8"))
        )
        assert (
            sorted(Path(m if isinstance(m, str) else m["path"]).name for m in r["matches"]) == want
        )

    @pytest.mark.parametrize("pattern", [r"\d+-\w+", r"^none", r"zzz"])
    def test_grep_finds_the_same_lines(self, files, pattern):
        r = engine.fs_query(path=str(files), content=pattern, regex=True, grep_mode=True)
        assert r["success"] is True, r
        got = sorted((Path(e["path"]).name, h["line"]) for e in r["matches"] for h in e["hits"])
        want = sorted(
            (p.name, i + 1)
            for p in files.iterdir()
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines())
            if re.search(pattern, line)
        )
        assert got == want

    @pytest.mark.parametrize(
        ("find", "replace", "count"),
        [(r"(\d+)-(\w+)", r"\2:\1", 0), (r"(\d+)-(\w+)", r"\2:\1", 1), (r"zzz", "y", 0)],
    )
    def test_replace_text_writes_what_re_writes(self, files, find, replace, count):
        f = files / "orders.txt"
        op = {
            "op": "replace_text",
            "path": str(f),
            "find": find,
            "replace": replace,
            "regex": True,
            "count": count,
        }
        r = engine.fs_write([op])
        want = re.sub(find, replace, TEXT, count=count)
        if want == TEXT:
            assert f.read_text(encoding="utf-8") == TEXT
        else:
            assert r["success"] is True, r
            assert f.read_text(encoding="utf-8") == want

    def test_a_bad_group_in_the_replacement_is_refused(self, files):
        f = files / "orders.txt"
        op = {
            "op": "replace_text",
            "path": str(f),
            "find": r"(\d+)",
            "replace": r"\2",
            "regex": True,
        }
        r = engine.fs_write([op])
        assert r["success"] is False and "invalid group reference 2" in str(r)
        assert f.read_text(encoding="utf-8") == TEXT


def test_the_guard_is_one_file_across_the_fleet():
    mine = hashlib.sha256((ROOT / "shared" / "regex_guard.py").read_bytes()).hexdigest()
    siblings = [
        ROOT.parent / repo / "shared" / "regex_guard.py"
        for repo in ("MCP_Data_Analyst", "MCP_Documents", "MCP_Web_Browser")
    ]
    present = [p for p in siblings if p.exists()]
    if not present:
        pytest.skip("no sibling repo checked out beside this one")
    for p in present:
        assert hashlib.sha256(p.read_bytes()).hexdigest() == mine, p
