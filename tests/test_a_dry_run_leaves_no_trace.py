"""A dry run must not write, and a snapshot is a write.

Seven of the sixteen fs_write ops took their snapshot *before* checking
`dry_run`, so previewing an op copied the whole file into `.mcp_versions` and
then reported that it would not change anything. Against the live endpoint,
three dry runs on one 12-byte file:

    dry_run replace_text  -> would_change: true, backup: .../t_...Z.txt.bak
    dry_run write_file    -> would_change: true, backup: .../t_...Z_1.txt.bak
    dry_run delete_lines  -> would_change: true, backup: .../t_...Z_2.txt.bak

    .mcp_versions/  3 files, each a full copy

Two consequences. Previewing a batch of ops on a large file writes a full copy
per op, which is the opposite of what a caller asks a dry run for. And the
version history fills with snapshots of writes that never happened -- all
identical to the live file -- which `fs_manage action=versions` lists and the
new `restore` op will happily offer.

The dry-run response also reported a `backup` path, which was true and
misleading: it named a file the caller never asked to have created. It now
reports None, because a dry run has nothing to back up.
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
def target(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("hello world\nsecond line\n", encoding="utf-8")
    return f


def ops_for(target: Path, tmp_path: Path) -> list[tuple[str, dict]]:
    """One op of every kind that has a dry-run branch."""
    return [
        ("write_file", {"path": str(target), "content": "different\n"}),
        ("append_file", {"path": str(target), "content": "more\n"}),
        ("copy", {"src": str(target), "dst": str(tmp_path / "copy.txt")}),
        ("replace_text", {"path": str(target), "find": "hello", "replace": "HI"}),
        ("insert_after", {"path": str(target), "after_pattern": "hello", "content": "new\n"}),
        ("delete_lines", {"path": str(target), "start_line": 0, "end_line": 1}),
        ("patch_lines", {"path": str(target), "start_line": 0, "end_line": 1, "content": "x\n"}),
        ("restore", {"path": str(target)}),
    ]


def run(op: str, args: dict) -> dict:
    outer = engine.fs_write([dict(op=op, **args)], dry_run=True)
    return outer.get("results", [outer])[0] if isinstance(outer, dict) else outer


class TestADryRunWritesNothing:
    @pytest.mark.parametrize("op", [o for o, _ in ops_for(Path("t"), Path("d"))])
    def test_no_snapshot_directory_appears(self, target, tmp_path, op):
        args = dict(ops_for(target, tmp_path))[op]
        run(op, args)
        versions = tmp_path / ".mcp_versions"
        assert not versions.exists() or not list(versions.iterdir()), (
            f"dry_run {op} wrote {[p.name for p in versions.iterdir()]}"
        )

    @pytest.mark.parametrize("op", [o for o, _ in ops_for(Path("t"), Path("d"))])
    def test_the_file_is_untouched(self, target, tmp_path, op):
        args = dict(ops_for(target, tmp_path))[op]
        before = target.read_bytes()
        run(op, args)
        assert target.read_bytes() == before

    @pytest.mark.parametrize("op", [o for o, _ in ops_for(Path("t"), Path("d"))])
    def test_it_does_not_claim_a_backup(self, target, tmp_path, op):
        args = dict(ops_for(target, tmp_path))[op]
        r = run(op, args)
        if r.get("success"):
            assert r.get("backup") is None, f"dry_run {op} named a backup it should not have made"

    def test_a_batch_of_dry_runs_leaves_the_directory_clean(self, target, tmp_path):
        for op, args in ops_for(target, tmp_path):
            engine.fs_write([dict(op=op, **args)], dry_run=True)
        versions = tmp_path / ".mcp_versions"
        assert not versions.exists() or not list(versions.iterdir())
        assert target.read_text(encoding="utf-8") == "hello world\nsecond line\n"


class TestARealRunStillSnapshots:
    """The guard above must not have been bought by dropping the snapshot."""

    @pytest.mark.parametrize(
        "op",
        [
            "write_file",
            "append_file",
            "replace_text",
            "insert_after",
            "delete_lines",
            "patch_lines",
        ],
    )
    def test_a_wet_run_still_leaves_a_backup(self, target, tmp_path, op):
        args = dict(ops_for(target, tmp_path))[op]
        outer = engine.fs_write([dict(op=op, **args)])
        r = outer.get("results", [outer])[0]
        assert r["success"] is True, r.get("error")
        assert r.get("backup"), f"{op} took no snapshot on a real run"
        assert Path(r["backup"]).read_text(encoding="utf-8") == "hello world\nsecond line\n"
