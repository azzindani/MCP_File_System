"""Four names for two handlers, and a line that stopped being a line.

Round 14's axis is a tool advertising a vocabulary and then not honouring it.
`fs_write` publishes seventeen op names in every refusal it writes, and four of
them describe the two-phase delete gate:

    delete_request        delete_confirm
    delete_tree_request   delete_tree_confirm

Two of the four were aliases. `delete_request` -- the op named for a single
file -- resolved a *directory*, issued a token, and `delete_confirm` spent it on
`shutil.rmtree`, erasing a tree recursively under success: true. The confirm
half was one function registered under both names, so `delete_tree_confirm`
answered `"op": "delete_confirm"`, an op the caller had not called; and a tree
request's own `next_step` said to call `delete_confirm`, teaching the wrong
name to anyone who followed the response. A reader of the op table concludes
the file op cannot destroy a directory. It could.

Nothing here removes a capability: every delete still has an op that performs
it, and the refusals name it. What changes is that the name has to agree with
what it is pointed at, and a token records which confirm op it was issued for
-- checked *before* the token is consumed, so the retry the hint names still
has a token to spend.

`insert_after` is the same rule one level down. It has always terminated its own
content, but not the line it inserts after:

    file:    "line one\\nappended line two"      (no final newline)
    op:      insert_after after_pattern="appended" content="inserted after anchor"
    disk:    "line one\\nappended line twoinserted after anchor\\n"
    result:  success: true, total_lines: 3       (the file has 2)

Two lines welded into one, a count that matched neither, and the file gained a
trailing newline it never had. patch_lines was taught in round 8 that an op
named for lines must produce lines; this is the anchor side of the same rule.

Also pinned: patch_lines said "Patched lines 1–2" for an end-exclusive range
that replaced one line -- the exact message `delete_lines`, twenty lines above
it in the same file, was already fixed to stop printing -- and never reported
how many lines it wrote. And the validator type-checked `path` and nothing
else, so `content` as a list of lines (a natural reading of patch_*lines*, and
what a sweep model actually sent) reached the handler and came back as "'list'
object has no attribute 'splitlines'".
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

from shared import confirm_store  # noqa: E402


def write(op: str, **kw) -> dict:
    outer = engine.fs_write([dict(op=op, **kw)])
    return outer.get("results", [outer])[0] if isinstance(outer, dict) else outer


@pytest.fixture(autouse=True)
def _clean_tokens():
    confirm_store._store.clear()
    yield
    confirm_store._store.clear()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    (d / "nested").mkdir(parents=True)
    (d / "a.txt").write_bytes(b"alpha\n")
    (d / "nested" / "b.txt").write_bytes(b"beta\n")
    return d


@pytest.fixture
def victim(tmp_path: Path) -> Path:
    f = tmp_path / "victim.txt"
    f.write_bytes(b"delete me\n")
    return f


# --- the op name has to match what it is pointed at -------------------------


class TestTheRequestNameMatchesTheTarget:
    def test_delete_request_refuses_a_directory(self, tree):
        r = write("delete_request", path=str(tree))
        assert r["success"] is False
        assert "directory" in r["error"]
        assert "delete_tree_request" in r["hint"], "the refusal must name the op that works"
        assert tree.exists()

    def test_the_refusal_hands_out_no_token(self, tree):
        r = write("delete_request", path=str(tree))
        assert "confirmation_token" not in r
        assert confirm_store._store == {}

    def test_delete_tree_request_refuses_a_plain_file(self, victim):
        r = write("delete_tree_request", path=str(victim))
        assert r["success"] is False
        assert "file" in r["error"]
        assert "delete_request" in r["hint"]
        assert victim.exists()

    def test_the_matching_pairs_still_work(self, tree, victim):
        t = write("delete_tree_request", path=str(tree))["confirmation_token"]
        assert write("delete_tree_confirm", token=t)["success"] is True
        assert not tree.exists()

        f = write("delete_request", path=str(victim))["confirmation_token"]
        assert write("delete_confirm", token=f)["success"] is True
        assert not victim.exists()


class TestTheConfirmNameMatchesTheToken:
    def test_the_file_op_cannot_spend_a_tree_token(self, tree):
        token = write("delete_tree_request", path=str(tree))["confirmation_token"]
        r = write("delete_confirm", token=token)
        assert r["success"] is False
        assert "delete_tree_confirm" in r["error"]
        assert tree.exists(), "a tree was deleted through the single-file op"

    def test_the_refused_token_is_not_burned(self, tree):
        """The hint says to retry with the other op, so that has to be possible."""
        token = write("delete_tree_request", path=str(tree))["confirmation_token"]
        write("delete_confirm", token=token)
        assert token in write("delete_confirm", token=token)["hint"]
        assert write("delete_tree_confirm", token=token)["success"] is True
        assert not tree.exists()

    def test_the_tree_op_cannot_spend_a_file_token(self, victim):
        token = write("delete_request", path=str(victim))["confirmation_token"]
        r = write("delete_tree_confirm", token=token)
        assert r["success"] is False
        assert "delete_confirm" in r["error"]
        assert victim.exists()

    def test_an_expired_or_unknown_token_is_still_refused(self):
        r = write("delete_confirm", token="del_nosuchtoken")
        assert r["success"] is False
        assert "Invalid or expired" in r["error"]

    def test_a_spent_token_is_still_refused(self, victim):
        token = write("delete_request", path=str(victim))["confirmation_token"]
        assert write("delete_confirm", token=token)["success"] is True
        assert write("delete_confirm", token=token)["success"] is False


class TestTheResponseNamesTheOpThatWasCalled:
    def test_tree_confirm_does_not_answer_as_delete_confirm(self, tree):
        token = write("delete_tree_request", path=str(tree))["confirmation_token"]
        assert write("delete_tree_confirm", token=token)["op"] == "delete_tree_confirm"

    def test_file_confirm_still_answers_as_itself(self, victim):
        token = write("delete_request", path=str(victim))["confirmation_token"]
        assert write("delete_confirm", token=token)["op"] == "delete_confirm"

    def test_the_next_step_names_the_op_that_will_work(self, tree, victim):
        t = write("delete_tree_request", path=str(tree))
        assert t["confirm_op"] == "delete_tree_confirm"
        assert "op=delete_tree_confirm" in t["next_step"]

        f = write("delete_request", path=str(victim))
        assert f["confirm_op"] == "delete_confirm"
        assert "op=delete_confirm" in f["next_step"]

    def test_the_warning_counts_the_files_not_the_arguments(self, tree):
        r = write("delete_tree_request", path=str(tree))
        assert r["targets"][0]["files"] == 2
        assert "2 file(s)" in r["warning"], r["warning"]

    def test_a_single_file_warning_stays_plain(self, victim):
        # What this is about is the *scope* clause: one file must not pick up
        # the "holding N file(s)" wording a tree needs. It used to assert the
        # whole opening of the sentence, which pinned "Permanently ... Cannot
        # be undone" -- wording that was false, and that a later fix had to
        # change. See test_the_warning_said_it_could_not_be_undone.
        r = write("delete_request", path=str(victim))
        assert "1 item(s) (" in r["warning"], r["warning"]
        assert "holding" not in r["warning"], r["warning"]
        assert "files" not in r["targets"][0]


# --- an insertion has to start a line, so the anchor has to end one ----------


class TestInsertAfterLeavesLines:
    def test_it_does_not_weld_onto_an_unterminated_anchor(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("line one\nappended line two")
        r = write("insert_after", path=str(f), after_pattern="appended", content="inserted")
        assert r["success"] is True
        assert f.read_text() == "line one\nappended line two\ninserted"

    def test_the_reported_count_is_the_count_on_disk(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("line one\nappended line two")
        r = write("insert_after", path=str(f), after_pattern="appended", content="inserted")
        assert r["total_lines"] == len(f.read_text().splitlines()) == 3

    def test_a_file_that_ended_without_a_newline_still_does(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("only line")
        write("insert_after", path=str(f), after_pattern="only", content="after")
        assert not f.read_text().endswith("\n")

    def test_a_file_that_ended_with_one_keeps_it(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("one\ntwo\n")
        write("insert_after", path=str(f), after_pattern="one", content="mid")
        assert f.read_text() == "one\nmid\ntwo\n"

    def test_multi_line_content_at_an_unterminated_anchor(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("head\ntail")
        write("insert_after", path=str(f), after_pattern="tail", content="a\nb\n")
        assert f.read_text() == "head\ntail\na\nb"

    def test_every_match_gets_a_line_of_its_own(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("x\nx\nx")
        r = write("insert_after", path=str(f), after_pattern="x", content="-", count=0)
        assert r["insertions"] == 3
        assert f.read_text().splitlines() == ["x", "-", "x", "-", "x", "-"]
        assert r["total_lines"] == 6


# --- patch_lines says how many lines it wrote, in the convention it uses -----


class TestPatchLinesReportsBothCounts:
    def test_it_reports_the_lines_it_wrote(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("a\nb\nc\n")
        r = write("patch_lines", path=str(f), start_line=0, end_line=1, content="X\nY")
        assert r["lines_replaced"] == 1
        assert r["lines_written"] == 2
        assert r["total_lines"] == len(f.read_text().splitlines()) == 4

    def test_the_message_stops_reading_as_an_inclusive_range(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("a\nb\nc\n")
        outer = engine.fs_write(
            [{"op": "patch_lines", "path": str(f), "start_line": 1, "end_line": 2, "content": "B"}]
        )
        msg = outer["progress"][0]
        assert "1–2" not in msg["msg"], "end_line is exclusive; 'lines 1-2' replaced one line"
        assert "Replaced 1 line(s) with 1" in msg["msg"]
        assert msg["detail"] == "lines [1, 2)"

    def test_the_dry_run_says_the_same_thing(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("a\nb\nc\n")
        outer = engine.fs_write(
            [{"op": "patch_lines", "path": str(f), "start_line": 0, "end_line": 2, "content": "X"}],
            dry_run=True,
        )
        assert outer["would_change"][0]["lines_written"] == 1
        assert "Would replace 2 line(s) with 1" in outer["progress"][0]["msg"]
        assert f.read_text() == "a\nb\nc\n"


# --- the validator checks types, not only names -----------------------------


class TestAFieldOfTheWrongTypeIsNamed:
    def test_content_as_a_list_of_lines(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("a\nb\n")
        r = engine.fs_write(
            [
                {
                    "op": "patch_lines",
                    "path": str(f),
                    "start_line": 0,
                    "end_line": 1,
                    "content": ["X", "Y"],
                }
            ]
        )
        assert r["success"] is False
        assert "'content' must be a string, got list" in r["error"]
        assert "join" in r["error"], "say how to turn the list into what the op wants"
        assert "splitlines" not in r["error"], "a Python attribute error is not a message"

    def test_an_octal_mode_sent_as_a_number(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("x\n")
        r = engine.fs_write([{"op": "set_permissions", "path": str(f), "mode": 644}])
        assert r["success"] is False
        assert "'mode' must be a string" in r["error"]
        assert "octal" in r["error"]

    def test_a_line_number_sent_as_text(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("a\nb\n")
        r = engine.fs_write(
            [{"op": "delete_lines", "path": str(f), "start_line": "0", "end_line": 1}]
        )
        assert r["success"] is False
        assert "'start_line' must be an integer, got str" in r["error"]

    def test_a_flag_sent_as_text(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("aaa\n")
        r = engine.fs_write(
            [{"op": "replace_text", "path": str(f), "find": "a+", "replace": "-", "regex": "true"}]
        )
        assert r["success"] is False
        assert "'regex' must be true or false" in r["error"]

    def test_the_hint_stops_listing_ops_when_the_op_was_fine(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("x\n")
        r = engine.fs_write([{"op": "set_permissions", "path": str(f), "mode": 644}])
        assert "Valid ops:" not in r["hint"], "the op name was not the problem"
        assert "field" in r["hint"]

    def test_an_unknown_op_still_gets_the_op_list(self):
        r = engine.fs_write([{"op": "nope", "path": "x"}])
        assert r["success"] is False
        assert "Valid ops:" in r["hint"]
        assert "delete_tree_confirm" in r["hint"]

    def test_a_correctly_typed_call_is_untouched(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("aaa\n")
        r = write("replace_text", path=str(f), find="a+", replace="-", regex=True, count=1)
        assert r["success"] is True
        assert f.read_text() == "-\n"


# --- max_results names one of the two things grep mode bounds ---------------


class TestGrepModeSaysWhichLimitApplied:
    """`max_results=5` returned 200 matching lines.

    The cap was honoured -- for files. In grep mode the count a caller reads is
    hits, and those ran to a separate budget that nothing in the response named,
    so the number that came back could not be reconciled with the number asked
    for. Both limits are right; only one of them was visible.
    """

    def _haystack(self, tmp_path: Path) -> Path:
        d = tmp_path / "hay"
        d.mkdir()
        (d / "a.txt").write_text("needle\n" * 400)
        return d

    def test_the_response_names_both_limits(self, tmp_path):
        d = self._haystack(tmp_path)
        r = engine.fs_query(
            path=str(d), pattern="*.txt", content="needle", grep_mode=True, max_results=5
        )
        assert r["success"] is True
        assert r["limits"]["max_results"] == 5
        assert r["limits"]["max_hits"] >= 1

    def test_the_hint_explains_what_max_results_bounded(self, tmp_path):
        d = self._haystack(tmp_path)
        r = engine.fs_query(
            path=str(d), pattern="*.txt", content="needle", grep_mode=True, max_results=5
        )
        assert r["truncated"] is True
        assert r["hits_returned"] < r["hits_found"]
        assert "max_results bounds the files searched" in r["hint"]
        assert "not the lines matched inside them" in r["hint"]

    def test_a_search_that_fits_needs_no_explaining(self, tmp_path):
        d = tmp_path / "small"
        d.mkdir()
        (d / "a.txt").write_text("needle\n")
        r = engine.fs_query(path=str(d), pattern="*.txt", content="needle", grep_mode=True)
        assert r["truncated"] is False
        assert r["hits_returned"] == r["hits_found"] == 1
        assert "hint" not in r
