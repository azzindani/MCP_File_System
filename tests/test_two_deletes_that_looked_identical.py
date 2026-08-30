"""`delete_request` and `delete_tree_request` both answered `op: delete_pending`.

Found by round 22's sweep, judging the response as data: the first field the
contract says a model checks was byte-identical whether the caller had asked to
delete one file or an entire directory tree. Two operations with very different
blast radii, one name.

It matters because the pending response is the thing a caller reads to decide
whether to confirm. Everything else in it -- the warning, the target list, the
`next_step` -- was already correct and tree-aware; only `op` was not, and `op`
is what a dispatcher branches on.

The confirm side already distinguished (`delete_confirm` /
`delete_tree_confirm`), so this was a name that had simply not been kept in step
with its own pair.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402


def request(op: str, path: Path, dry_run: bool = False, **kw) -> dict:
    # dry_run belongs to fs_write, not to the op dict -- passing it inside the
    # op makes fs_write reject the batch and answer with its own envelope.
    outer = engine.fs_write([dict(op=op, path=str(path), **kw)], dry_run=dry_run)
    return outer.get("results", [outer])[0] if isinstance(outer, dict) else outer


def make_tree(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    (d / "nested").mkdir(parents=True)
    (d / "a.txt").write_bytes(b"alpha\n")
    (d / "nested" / "b.txt").write_bytes(b"beta\n")
    return d


class TestTheOpNamesTheOperation:
    def test_a_file_request_is_delete_pending(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_ALLOWED_ROOT", str(tmp_path))
        victim = tmp_path / "one.txt"
        victim.write_bytes(b"x\n")
        assert request("delete_request", victim)["op"] == "delete_pending"

    def test_a_tree_request_is_delete_tree_pending(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_ALLOWED_ROOT", str(tmp_path))
        assert request("delete_tree_request", make_tree(tmp_path))["op"] == "delete_tree_pending"

    def test_the_two_are_not_the_same_string(self, tmp_path, monkeypatch):
        """The assertion the old code would have failed.

        Written as a comparison rather than two literals so it keeps failing if
        someone unifies the names again for tidiness.
        """
        monkeypatch.setenv("MCP_ALLOWED_ROOT", str(tmp_path))
        victim = tmp_path / "one.txt"
        victim.write_bytes(b"x\n")
        file_op = request("delete_request", victim)["op"]
        tree_op = request("delete_tree_request", make_tree(tmp_path))["op"]
        assert file_op != tree_op

    def test_dry_run_names_the_operation_too(self, tmp_path, monkeypatch):
        """dry_run is the branch a cautious caller uses first."""
        monkeypatch.setenv("MCP_ALLOWED_ROOT", str(tmp_path))
        result = request("delete_tree_request", make_tree(tmp_path), dry_run=True)
        assert result["op"] == "delete_tree_pending"
        assert result["dry_run"] is True

    def test_the_pending_op_matches_the_confirm_it_hands_back(self, tmp_path, monkeypatch):
        """A pending op and its confirm op must describe the same operation.

        This is the property the names exist to carry: `delete_tree_pending`
        leads to `delete_tree_confirm`, never to the file op.
        """
        monkeypatch.setenv("MCP_ALLOWED_ROOT", str(tmp_path))
        result = request("delete_tree_request", make_tree(tmp_path))
        assert result["op"].replace("_pending", "_confirm") in str(result.get("next_step", ""))
