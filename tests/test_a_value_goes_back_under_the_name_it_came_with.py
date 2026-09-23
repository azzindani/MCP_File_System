"""A value goes back under the name it came with; an empty query says why.

Three sweep findings on this server:

- delete_request answers `confirmation_token`, and delete_confirm took only
  `token`: passing the value back under the name it was handed out with was
  refused as an unknown field.
- fs_index query with a bare word ('invoice') found nothing, and the hint blamed
  a stale index -- "run fs_index action=build to refresh" -- when the cause was
  the pattern: it matches whole file names, so only a file named exactly
  'invoice' qualifies. Following the hint changed nothing.
- With no `path`, query and list read the whole index and reported the root as
  home -- /home/app on a deployed server, which is not even a served folder.
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


def write(op: str, **kw) -> dict:
    outer = engine.fs_write([dict(op=op, **kw)])
    return outer.get("results", [outer])[0] if isinstance(outer, dict) else outer


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


class TestTheTokenGoesBackAsItCame:
    def test_a_file(self, tmp_path):
        target = tmp_path / "gone.txt"
        target.write_text("x\n", encoding="utf-8")
        handed = write("delete_request", path=str(target))
        result = write("delete_confirm", confirmation_token=handed["confirmation_token"])
        assert result["success"] is True, result.get("error")
        assert not target.exists()

    def test_a_tree(self, tmp_path):
        folder = tmp_path / "tree"
        folder.mkdir()
        (folder / "a.txt").write_text("a\n", encoding="utf-8")
        handed = write("delete_tree_request", path=str(folder))
        result = write("delete_tree_confirm", confirmation_token=handed["confirmation_token"])
        assert result["success"] is True, result.get("error")
        assert not folder.exists()

    def test_the_grammar_names_both_spellings(self):
        entry = next(
            e for e in engine.list_fs_ops("delete_confirm")["ops"] if e["op"] == "delete_confirm"
        )
        assert "confirmation_token" in str(entry)


@pytest.fixture
def indexed(home):
    docs = home / "docs"
    docs.mkdir()
    for name in ("invoice_007.pdf", "invoice_008.pdf", "notes.md"):
        (docs / name).write_bytes(b"x")
    built = engine.fs_index(action="build", path=str(docs))
    assert built["success"] is True, built.get("error")
    return docs


class TestAnEmptyQuerySaysWhy:
    def test_a_bare_word_is_pointed_at_the_glob_that_finds_it(self, indexed):
        result = engine.fs_index(action="query", pattern="invoice", path=str(indexed))
        assert result["returned"] == 0
        assert "pattern='*invoice*'" in result["hint"] and "2 indexed name(s)" in result["hint"]
        assert "refresh" not in result["hint"]

    def test_following_the_hint_finds_them(self, indexed):
        result = engine.fs_index(action="query", pattern="*invoice*", path=str(indexed))
        assert sorted(m["name"] for m in result["matches"]) == [
            "invoice_007.pdf",
            "invoice_008.pdf",
        ]

    def test_a_glob_that_finds_nothing_still_suggests_a_rebuild(self, indexed):
        result = engine.fs_index(action="query", pattern="*.xlsx", path=str(indexed))
        assert "action=build" in result["hint"]


class TestNoPathMeansTheWholeIndex:
    @pytest.mark.parametrize("action", ["query", "list"])
    def test_the_root_says_so(self, indexed, home, action):
        kwargs = {"pattern": "*"} if action == "query" else {}
        result = engine.fs_index(action=action, **kwargs)
        assert result["root"] == "(the whole index)" and str(home) not in result["root"]
