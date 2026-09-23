"""A remote server reads and writes only inside the folders it serves.

`resolve_path` promised "no directory restriction -- the tool is designed to
work anywhere", which is right for a local install and wrong for the deployed
container: any authenticated caller could read, overwrite or delete any file
the process could (`fs_read("/etc/hostname")`; /proc/self/environ holds the API
keys). The README claimed every path was "validated against the user's home
directory"; nothing checked.

With MCP_CONFINE_PATHS on (the default for every HTTP deployment) a path must
lie inside MCP_OUTPUT_DIR, MCP_DATA_ROOT or MCP_ALLOWED_ROOTS, judged after
symlinks resolve and before anything is looked for. A relative path, or no
path, means the data folder. A local stdio install is unchanged.

Also here: three places that met a refused path and carried on as if none had
been given. `fs_index(action="query")` searched the whole index; `fs_read`'s
`compare_to` went looking for a snapshot; `symlink_info` reported whether the
path existed before it checked whether it was allowed to look.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402

from shared.file_utils import PathOutsideRootError, resolve_path  # noqa: E402

OUTSIDE = "/etc/hostname" if Path("/etc/hostname").exists() else str(Path(__file__).resolve())


@pytest.fixture
def served(tmp_home, monkeypatch):
    data = tmp_home / "data"
    data.mkdir()
    monkeypatch.setenv("MCP_CONFINE_PATHS", "1")
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(data))
    monkeypatch.delenv("MCP_DATA_ROOT", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ROOTS", raising=False)
    (data / "note.txt").write_text("hello\n", encoding="utf-8")
    return data


@pytest.fixture
def outside_file(tmp_home):
    """A real file in the caller's home, which a confined server does not serve."""
    f = tmp_home / "private.txt"
    f.write_text("secret\n", encoding="utf-8")
    return f


class TestResolve:
    def test_a_file_outside_is_refused(self, served):
        with pytest.raises(PathOutsideRootError, match="outside the folders"):
            resolve_path(OUTSIDE)

    def test_a_missing_file_outside_is_refused_not_reported_missing(self, served):
        with pytest.raises(PathOutsideRootError):
            resolve_path("/nonexistent-dir/secret.txt", must_exist=True)

    def test_a_relative_path_is_read_from_the_data_folder(self, served):
        assert resolve_path("note.txt") == (served / "note.txt").resolve()

    def test_climbing_out_is_refused(self, served):
        with pytest.raises(PathOutsideRootError):
            resolve_path("../private.txt")

    def test_home_is_not_served(self, served):
        with pytest.raises(PathOutsideRootError):
            resolve_path("~/private.txt")

    def test_a_symlink_is_judged_by_where_it_leads(self, served, outside_file):
        link = served / "innocent.txt"
        try:
            link.symlink_to(outside_file)
        except OSError, NotImplementedError:
            pytest.skip("cannot create a symlink here")
        with pytest.raises(PathOutsideRootError):
            resolve_path(str(link))

    def test_an_extra_root_can_be_served(self, served, outside_file, monkeypatch):
        monkeypatch.setenv("MCP_ALLOWED_ROOTS", str(outside_file.parent))
        assert resolve_path(str(outside_file)) == outside_file.resolve()

    def test_a_null_byte_is_refused(self, served):
        with pytest.raises(ValueError, match="null byte"):
            resolve_path("note.txt\x00.png")


class TestTools:
    def test_a_read_outside_is_refused_with_the_refusals_hint(self, served, outside_file):
        r = engine.fs_read(str(outside_file))
        assert r["success"] is False
        assert "outside the folders" in r["error"]
        assert "MCP_ALLOWED_ROOTS" in r["hint"], "the hint must say how to recover, not 'home'"

    def test_a_write_outside_is_refused_and_writes_nothing(self, served, tmp_home):
        target = tmp_home / "elsewhere" / "planted.txt"
        r = engine.fs_write([{"op": "write_file", "path": str(target), "content": "x"}])
        assert r["success"] is False
        assert "outside the folders" in str(r)
        assert not target.parent.exists()

    def test_a_delete_outside_is_refused_and_the_file_survives(self, served, outside_file):
        r = engine.fs_write([{"op": "delete", "path": str(outside_file)}])
        assert r["success"] is False
        assert outside_file.read_text(encoding="utf-8") == "secret\n"

    def test_a_copy_out_of_the_served_folder_is_refused(self, served, tmp_home):
        dst = tmp_home / "exfil.txt"
        r = engine.fs_write([{"op": "copy", "src": "note.txt", "dst": str(dst)}])
        assert r["success"] is False
        assert not dst.exists()

    def test_a_search_with_no_path_searches_the_data_folder(self, served, outside_file):
        r = engine.fs_query(pattern="*.txt")
        assert r["success"] is True, r
        found = str(r)
        assert "note.txt" in found
        assert "private.txt" not in found

    def test_an_index_query_on_a_refused_path_is_refused_not_widened(self, served):
        assert engine.fs_index(action="build", path=str(served))["success"] is True
        r = engine.fs_index(action="query", path=OUTSIDE, pattern="note")
        assert r["success"] is False, "a refused path searched the whole index instead"
        assert "outside the folders" in r["error"]

    def test_compare_to_outside_is_refused_not_read_as_a_timestamp(self, served, outside_file):
        r = engine.fs_read("note.txt", mode="diff", compare_to=str(outside_file))
        assert r["success"] is False
        assert "outside the folders" in r["error"]

    def test_symlink_info_refuses_before_it_looks(self, served):
        r = engine.fs_manage(action="symlink_info", path="/nonexistent-dir/x")
        assert r["success"] is False
        assert "outside the folders" in r["error"], "said whether it exists before checking"

    def test_disk_usage_with_no_path_measures_the_data_folder(self, served):
        r = engine.fs_manage(action="disk_usage")
        assert r["success"] is True, r


class TestLocal:
    def test_a_local_install_works_anywhere(self, tmp_home, monkeypatch, outside_file):
        monkeypatch.delenv("MCP_CONFINE_PATHS", raising=False)
        assert resolve_path(str(outside_file)) == outside_file.resolve()
        assert engine.fs_read(str(outside_file))["success"] is True

    def test_a_relative_path_is_still_read_from_home(self, tmp_home, monkeypatch, outside_file):
        monkeypatch.delenv("MCP_CONFINE_PATHS", raising=False)
        assert resolve_path("private.txt") == outside_file.resolve()
