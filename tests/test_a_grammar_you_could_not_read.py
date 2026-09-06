"""Seventeen ops, and no way to learn any of them without getting one wrong.

`fs_write` declares its whole surface as:

    "ops": {"items": {"additionalProperties": true, "type": "object"},
            "type": "array"}

-- an opaque object -- and its description is one line that names no field. So
`tools/list` shows a caller nothing, and the grammar of every op is reachable
only by sending a wrong call and reading the refusal.

The refusal is excellent. Round 28's first call this round was

    {"op": "copy", "source": ..., "destination": ...}

and it came back

    Op 0 (copy): unknown field(s) destination, source -- copy accepts: dst, op, path, src

which is exactly right, and one wasted call too late. The Data_Analyst repo
solved this twice -- `list_patch_ops` for apply_patch's 52 ops, `list_derive_ops`
for feature_engineering's 5 -- and filesystem had the refusal half without the
discovery half.

`catalogue()` renders from ALLOWED_OPS, _REQUIRED, _OPTIONAL and _FIELD_ALIASES
rather than restating them, because this repo's own filter-operator vocabulary
once drifted across three hand-written copies.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from servers.fs_basic import engine  # noqa: E402
from shared.patch_validator import ALLOWED_OPS  # noqa: E402


class TestEveryOpIsDiscoverable:
    def test_it_lists_them_all(self):
        r = engine.list_fs_ops()
        assert r["success"] is True
        assert {e["op"] for e in r["ops"]} == set(ALLOWED_OPS)

    def test_copy_names_the_fields_that_actually_work(self):
        """src/dst, not source/destination -- the exact guess that failed."""
        entry = next(e for e in engine.list_fs_ops("copy")["ops"] if e["op"] == "copy")
        assert set(entry["required"]) == {"src", "dst"}

    def test_every_op_carries_a_worked_example(self):
        missing = [e["op"] for e in engine.list_fs_ops()["ops"] if "example" not in e]
        assert not missing, f"no example for: {missing}"

    def test_each_example_is_a_call_the_validator_accepts(self):
        """A catalogue whose examples do not validate is a second wrong answer."""
        from shared.patch_validator import validate_ops

        for entry in engine.list_fs_ops()["ops"]:
            errors = validate_ops([entry["example"]])
            assert not errors, f"{entry['op']}: {errors}"

    def test_the_destructive_ops_are_marked(self):
        by_name = {e["op"]: e for e in engine.list_fs_ops()["ops"]}
        assert by_name["delete_tree_confirm"]["destructive"] is True
        assert by_name["create_dir"]["destructive"] is False

    def test_asking_for_one_op_returns_only_that_op(self):
        r = engine.list_fs_ops("rename")
        assert [e["op"] for e in r["ops"]] == ["rename"]

    def test_an_unknown_op_is_refused_with_the_list(self):
        r = engine.list_fs_ops("coppy")
        assert r["success"] is False
        assert "copy" in r["hint"]


class TestAnErrorNamesWhatWasLookedFor:
    def test_fs_read_reports_the_path_it_was_given(self, tmp_path):
        """Reporting the basename made an absolute path look like a relative one."""
        missing = tmp_path / "nested" / "ads.csv"
        r = engine.fs_read(str(missing))
        assert r["success"] is False
        assert str(missing) in r["error"], r["error"]

    def test_fs_manage_does_the_same(self, tmp_path):
        missing = tmp_path / "nested" / "ads.csv"
        r = engine.fs_manage("disk_usage", str(missing))
        assert r["success"] is False
        assert str(missing) in r["error"], r["error"]


class TestTheArchiveFormatIsInferredForEveryAction:
    def test_listing_a_zip_does_not_need_the_format_spelled_out(self, tmp_path):
        """`create` read the extension and `list` refused to, on the same filename."""
        target = tmp_path / "stuff"
        target.mkdir()
        (target / "a.txt").write_text("hello", encoding="utf-8")
        archive = tmp_path / "out.zip"

        made = engine.fs_archive("create", str(archive), target=str(target))
        assert made["success"] is True, made.get("error")

        listed = engine.fs_archive("list", str(archive))
        assert listed["success"] is True, listed.get("error")
        assert listed["entries"]

    def test_a_name_that_says_nothing_asks_for_the_format_by_name(self, tmp_path):
        """ "Unknown format ''" named no parameter, on a tool that declares two."""
        blank = tmp_path / "archive_without_a_suffix"
        blank.write_bytes(b"not really an archive")
        r = engine.fs_archive("list", str(blank))
        assert r["success"] is False
        assert "format" in r["hint"], r["hint"]
