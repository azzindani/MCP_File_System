"""A name this server does not take must be refused, not quietly dropped.

This server runs on the FastMCP bundled in the `mcp` SDK, whose argument model
uses pydantic's default `extra="ignore"`. Against the deployed endpoint:

    fs_query(path=..., pattern="*", type_="dir")   ->  0 results   filter applied
    fs_query(path=..., pattern="*", type="dir")    ->  6 results   filter dropped
    fs_query(path=..., pattern="*", nonsense=1)    ->  success: true

The three sibling repos run standalone fastmcp 2.x, which answers "Unexpected
keyword argument". So a wrong name was reported on three servers out of four and
silently ignored on this one -- and `type` is exactly the name a caller reaches
for, because `type_` is a trailing-underscore spelling that appears nowhere else
in the fleet.

Two fixes, and both are needed. `type` and `format` now work, so the natural
guess is right rather than merely diagnosable; and any *other* unknown name is
refused with the accepted names listed, so the silent class is closed rather
than one instance of it patched.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp.types import CallToolResult

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "servers" / "fs_basic"
for _p in (str(ROOT), str(SERVER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import server as fs_server  # noqa: E402


def call(name: str, arguments: dict, convert_result: bool = True) -> dict:
    """Dispatch the way the server does, conversion included.

    Two layers are easy to test past and both have burned this fleet. Calling
    the wrapper's `.fn` skips argument validation entirely -- that let a
    too-narrow annotation pass six of seven tests in a sibling repo. And calling
    `call_tool` with its *default* convert_result=False skips the result
    conversion, which is where the first version of this refusal broke: it
    returned a JSON string, and the SDK iterated it one character at a time into
    1900 validation errors. Every assertion here goes through the converted
    path, because that is the one a client sees.
    """
    result = asyncio.run(
        fs_server.mcp._tool_manager.call_tool(name, arguments, convert_result=convert_result)
    )
    if not convert_result:
        return result if isinstance(result, dict) else {"raw": result}
    # Converted, this is the content list the SDK puts straight into a
    # CallToolResult. Validating it here is the whole point: a bare string
    # passes json.loads happily and then explodes at the protocol boundary.
    CallToolResult(content=list(result))
    assert result and hasattr(result[0], "text"), f"not renderable content: {result!r}"
    return json.loads(result[0].text)


@pytest.fixture
def tree(tmp_path: Path) -> str:
    (tmp_path / "one.txt").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("beta\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "three.txt").write_text("gamma\n", encoding="utf-8")
    return str(tmp_path)


class TestTheNaturalSpellingWorks:
    def test_fs_query_takes_type(self, tree):
        both = call("fs_query", {"path": tree, "pattern": "*"})
        dirs = call("fs_query", {"path": tree, "pattern": "*", "type": "dir"})
        assert both["success"] is True and dirs["success"] is True
        assert dirs["returned"] < both["returned"], "type= was accepted and then ignored"

    def test_fs_query_still_takes_type_underscore(self, tree):
        both = call("fs_query", {"path": tree, "pattern": "*"})
        dirs = call("fs_query", {"path": tree, "pattern": "*", "type_": "dir"})
        assert dirs["success"] is True
        assert dirs["returned"] < both["returned"]

    def test_the_two_spellings_agree(self, tree):
        a = call("fs_query", {"path": tree, "pattern": "*", "type": "file"})
        b = call("fs_query", {"path": tree, "pattern": "*", "type_": "file"})
        assert a["returned"] == b["returned"]

    def test_fs_archive_takes_format(self, tmp_path, tree):
        out = tmp_path / "made.tar.gz"
        r = call(
            "fs_archive",
            {"action": "create", "path": str(out), "target": tree, "format": "tar.gz"},
        )
        assert r["success"] is True, r.get("error")
        assert out.exists()

    def test_fs_archive_still_takes_format_underscore(self, tmp_path, tree):
        out = tmp_path / "made.zip"
        r = call(
            "fs_archive",
            {"action": "create", "path": str(out), "target": tree, "format_": "zip"},
        )
        assert r["success"] is True, r.get("error")
        assert out.exists()


class TestAnUnknownNameIsRefused:
    def test_an_invented_argument_does_not_pass_silently(self, tree):
        r = call("fs_query", {"path": tree, "pattern": "*", "nonsense_argument": 1})
        assert r["success"] is False
        assert "nonsense_argument" in r["error"]

    def test_the_refusal_lists_the_names_that_work(self, tree):
        r = call("fs_query", {"path": tree, "pattern": "*", "nonsense_argument": 1})
        for expected in ("pattern", "path", "content", "max_results"):
            assert expected in r["hint"], r["hint"]

    def test_a_near_miss_is_named(self, tree):
        r = call("fs_query", {"path": tree, "patern": "*"})
        assert r["success"] is False
        assert "pattern" in r["hint"]

    @pytest.mark.parametrize(
        "tool,args",
        [
            ("fs_read", {"mode": "content", "filepath": "/tmp"}),
            ("fs_index", {"action": "list", "directory": "/tmp"}),
            ("fs_manage", {"action": "disk_usage", "folder": "/tmp"}),
            ("fs_archive", {"action": "create", "path": "/tmp/x.zip", "kind": "zip"}),
        ],
    )
    def test_every_tool_reports_a_name_it_does_not_take(self, tool, args):
        r = call(tool, args)
        assert r["success"] is False, f"{tool} accepted an argument it does not declare"
        wrong = [k for k in args if k in r["error"]]
        assert wrong, r

    def test_a_correct_call_is_untouched(self, tree):
        r = call("fs_read", {"path": str(Path(tree) / "one.txt"), "mode": "content"})
        assert r["success"] is True, r.get("error")
        assert "alpha" in json.dumps(r)


class TestTheRefusalSaysItOnce:
    """The refusal listed every accepted name twice and mis-stated its size.

    `hint` was built as `f"{hint} Accepted: {names}."` where `hint` had already
    spelled the same list out when there was no near-miss to suggest. Against
    the live endpoint, fs_query's refusal:

        fs_query accepts: content, context_lines, follow_symlinks, grep_mode,
        include_meta, max_results, path, pattern, type, type_. Accepted:
        content, context_lines, follow_symlinks, grep_mode, include_meta,
        max_results, path, pattern, type, type_.

    241 characters where 120 say the same thing. And `token_estimate` was the
    literal 40 whatever the response held -- under half the real size here, on
    a server whose entire design is a 12,000-token client budget.
    """

    def test_the_accepted_names_appear_once(self, tree):
        r = call("fs_query", {"path": tree, "pattern": "*", "nonsense_argument": 1})
        assert r["hint"].count("max_results") == 1, r["hint"]

    def test_a_near_miss_still_names_the_alternatives(self, tree):
        r = call("fs_query", {"path": tree, "patern": "*"})
        assert "pattern" in r["hint"], r["hint"]
        assert "Did you mean" in r["hint"], r["hint"]

    def test_the_token_estimate_is_measured(self, tree):
        r = call("fs_query", {"path": tree, "pattern": "*", "nonsense_argument": 1})
        assert r["token_estimate"] >= len(str(r)) // 8, r["token_estimate"]
