"""fs_write answers its own op grammar: six names in tools/list, not seven.

A model reads every tool name on every turn. `list_fs_ops` existed only to
tell a caller what fs_write's `ops` could hold -- the items schema is an open
object, so tools/list cannot show it -- and nothing else needs it. An empty
`ops` now asks fs_write for that grammar, which it answers without writing
anything. `list_fs_ops` leaves the listing and still answers, naming where the
grammar lives now, so a client that learned it keeps working.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from servers.fs_basic import server as fs  # noqa: E402


def _listed() -> dict:
    return {t.name: t for t in asyncio.run(fs.mcp.list_tools())}


def _call(tool: str, arguments: dict) -> dict:
    return asyncio.run(fs.mcp._tool_manager.call_tool(tool, arguments, convert_result=False))


class TestSixNotSeven:
    def test_list_fs_ops_is_not_listed(self):
        assert sorted(_listed()) == [
            "fs_archive",
            "fs_index",
            "fs_manage",
            "fs_query",
            "fs_read",
            "fs_write",
        ]

    def test_fs_write_says_where_its_grammar_is(self):
        text = _listed()["fs_write"].description
        assert "ops=[]" in text and "list_fs_ops" not in text


class TestAnEmptyOpsIsTheGrammar:
    def test_it_is_the_whole_catalogue(self):
        answer = _call("fs_write", {"ops": []})
        catalogue = _call("list_fs_ops", {})
        assert answer["success"] is True and answer["op"] == "fs_write"
        assert answer["ops"] == catalogue["ops"]
        assert answer["total_ops"] == len(catalogue["ops"]) >= 17

    def test_it_says_nothing_was_written(self):
        answer = _call("fs_write", {"ops": []})
        assert answer["ops_applied"] == 0
        assert answer["hint"].startswith("Nothing was written")

    def test_a_real_op_list_is_still_applied_or_refused_as_before(self):
        answer = _call("fs_write", {"ops": [{"op": "frobnicate"}], "dry_run": True})
        assert answer["success"] is False
        assert "unknown op 'frobnicate'" in answer["error"]


class TestTheOldNameStillAnswers:
    def test_it_answers_and_names_its_successor(self):
        answer = _call("list_fs_ops", {"op": "copy"})
        assert answer["success"] is True
        assert [e["op"] for e in answer["ops"]] == ["copy"]
        assert "fs_write(ops=[])" in answer["retired"]
