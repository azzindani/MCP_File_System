"""Two tools whose contract was unreachable from the schema.

**fs_query** is documented "Locate files by name/content", so a content-only
search is a documented call. It was refused before reaching any code that could
explain itself:

    fs_query(path="/workspace/data", content="coverage-sweep", grep_mode=True)
      1 validation error for fs_queryArguments
      pattern
        Field required [type=missing, ...]

A raw pydantic error naming an argument the caller had deliberately left out,
for the half of the tool's own description they were trying to use. `pattern`
now defaults to matching every name once `content` says what to look for
inside, and a call with neither gets a sentence instead of a schema error.

**fs_archive** takes `path` = the archive and `target` = what goes into it,
which is the opposite of how the two names read. Three sweeps running, the first
call passed them the other way round. The swap message added earlier makes it
recoverable in one retry, but a caller should not have to fail once to learn the
contract -- and half of the docstring's 80 characters was spent on "Uses Python
stdlib only", which no caller can act on. The docstring now carries the roles.

Found by giving the filesystem read tools a phase of their own.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from servers.fs_basic import engine

SERVER = Path(__file__).parent.parent / "servers" / "fs_basic" / "server.py"


def docstring_of(name: str) -> str:
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"{name} not found in server.py")


@pytest.fixture()
def haystack(work_dir: Path) -> Path:
    (work_dir / "a.txt").write_text("nothing here\n", encoding="utf-8")
    (work_dir / "b.txt").write_text("coverage-sweep needle\n", encoding="utf-8")
    (work_dir / "c.log").write_text("needle again\n", encoding="utf-8")
    return work_dir


class TestAContentOnlySearchIsAccepted:
    def test_grep_mode_with_no_pattern_works(self, haystack: Path):
        r = engine.fs_query(path=str(haystack), content="needle", grep_mode=True)
        assert r["success"] is True, r.get("error")

    def test_it_finds_the_files_that_contain_the_string(self, haystack: Path):
        r = engine.fs_query(path=str(haystack), content="needle")
        assert r["total_found"] == 2, r

    def test_it_does_not_find_the_file_that_does_not(self, haystack: Path):
        r = engine.fs_query(path=str(haystack), content="needle")
        assert all("a.txt" not in str(m) for m in r["matches"]), r["matches"]

    def test_a_name_pattern_still_narrows_a_content_search(self, haystack: Path):
        r = engine.fs_query(path=str(haystack), pattern="*.log", content="needle")
        assert r["total_found"] == 1, r

    def test_a_name_only_search_is_unchanged(self, haystack: Path):
        r = engine.fs_query(path=str(haystack), pattern="*.txt")
        assert r["success"] is True and r["total_found"] == 2, r


class TestNeitherIsStillRefused:
    def test_it_fails(self, haystack: Path):
        r = engine.fs_query(path=str(haystack))
        assert r["success"] is False

    def test_the_error_is_a_sentence_not_a_schema_dump(self, haystack: Path):
        r = engine.fs_query(path=str(haystack))
        assert "validation error" not in r["error"], r["error"]
        assert "Field required" not in r["error"], r["error"]

    def test_the_hint_names_both_ways_to_search(self, haystack: Path):
        r = engine.fs_query(path=str(haystack))
        assert "pattern" in r["hint"] and "content" in r["hint"], r["hint"]


class TestTheArchiveDocstringCarriesTheContract:
    def test_it_names_both_parameters(self):
        doc = docstring_of("fs_archive")
        assert "path=" in doc and "target=" in doc, doc

    def test_it_says_which_one_is_the_archive(self):
        assert "path=archive" in docstring_of("fs_archive"), docstring_of("fs_archive")

    def test_it_is_within_the_eighty_char_limit(self):
        assert len(docstring_of("fs_archive")) <= 80, len(docstring_of("fs_archive"))

    def test_it_still_says_what_the_tool_does(self):
        doc = docstring_of("fs_archive").lower()
        assert "create" in doc and "extract" in doc and "zip" in doc

    def test_the_implementation_note_is_gone(self):
        """ "Uses Python stdlib only" is not something a caller can act on."""
        assert "stdlib" not in docstring_of("fs_archive")


class TestTheArchiveCallStillBehaves:
    def test_the_documented_order_works(self, work_dir: Path):
        payload = work_dir / "scratch.txt"
        payload.write_text("hello\n", encoding="utf-8")
        r = engine.fs_archive(action="create", path=str(work_dir / "out.zip"), target=str(payload))
        assert r["success"] is True, r.get("error")
        assert (work_dir / "out.zip").exists()

    def test_the_swapped_order_still_explains_itself(self, work_dir: Path):
        payload = work_dir / "scratch.txt"
        payload.write_text("hello\n", encoding="utf-8")
        r = engine.fs_archive(action="create", path=str(payload), target=str(work_dir / "out.zip"))
        assert r["success"] is False
        assert "swapped" in r["hint"], r["hint"]
