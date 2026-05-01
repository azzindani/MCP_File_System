"""Comprehensive tests for fs_basic engine — all 6 tools, all modes."""

import sys
from pathlib import Path

# conftest.py adds the right paths; just import engine directly
import engine  # noqa: E402
import pytest

# ===========================================================================
# fs_query
# ===========================================================================


class TestFsQuery:
    def test_finds_files_by_glob(self, simple_dir):
        r = engine.fs_query("*.txt", path=str(simple_dir))
        assert r["success"] is True
        assert r["op"] == "fs_query"
        assert len(r["matches"]) == 10
        assert all(m.endswith(".txt") for m in r["matches"])

    def test_returns_paths_only_by_default(self, simple_dir):
        r = engine.fs_query("*.txt", path=str(simple_dir))
        assert isinstance(r["matches"], list)
        assert isinstance(r["matches"][0], str)

    def test_type_file_excludes_dirs(self, messy_dir):
        r = engine.fs_query("*", path=str(messy_dir), type_="file")
        assert r["success"] is True
        for m in r["matches"]:
            assert Path(m).is_file()

    def test_type_dir_excludes_files(self, messy_dir):
        r = engine.fs_query("*", path=str(messy_dir), type_="dir")
        assert r["success"] is True
        for m in r["matches"]:
            assert Path(m).is_dir()

    def test_grep_mode_returns_line_hits(self, simple_dir):
        r = engine.fs_query("*.txt", path=str(simple_dir), content="line 1", grep_mode=True)
        assert r["success"] is True
        assert r["grep_mode"] is True
        assert len(r["matches"]) > 0
        hit = r["matches"][0]
        assert "path" in hit
        assert "hits" in hit
        assert hit["hits"][0]["line"] >= 1

    def test_grep_mode_context_lines(self, simple_dir):
        r = engine.fs_query(
            "*.txt",
            path=str(simple_dir),
            content="Second line",
            grep_mode=True,
            context_lines=1,
        )
        assert r["success"] is True
        assert len(r["matches"]) > 0
        hit0 = r["matches"][0]["hits"][0]
        assert "context_before" in hit0
        assert "context_after" in hit0

    def test_include_meta_adds_size_mtime_mime(self, simple_dir):
        r = engine.fs_query("*.txt", path=str(simple_dir), include_meta=True)
        assert r["success"] is True
        entry = r["matches"][0]
        assert isinstance(entry, dict)
        assert "size" in entry
        assert "mtime" in entry
        assert "mime" in entry

    def test_follow_symlinks_false_no_loop(self, messy_dir):
        # Should complete without infinite loop
        r = engine.fs_query("*", path=str(messy_dir), follow_symlinks=False)
        assert r["success"] is True

    def test_max_results_cap(self, simple_dir):
        r = engine.fs_query("*.txt", path=str(simple_dir), max_results=3)
        assert r["success"] is True
        assert len(r["matches"]) <= 3
        assert r["truncated"] is True

    def test_constrained_mode_reduces_cap(self, simple_dir, monkeypatch):
        monkeypatch.setenv("MCP_CONSTRAINED_MODE", "1")
        r = engine.fs_query("*.txt", path=str(simple_dir))
        assert r["success"] is True
        assert len(r["matches"]) <= 10

    def test_no_matches_returns_empty_list(self, simple_dir):
        r = engine.fs_query("*.nonexistent_ext", path=str(simple_dir))
        assert r["success"] is True
        assert r["matches"] == []
        assert r["returned"] == 0

    def test_path_outside_home_error(self):
        r = engine.fs_query("*.py", path="/etc")
        assert r["success"] is False
        assert "error" in r
        assert "hint" in r

    def test_invalid_root_error(self, tmp_home):
        r = engine.fs_query("*.py", path=str(tmp_home / "no_such_dir"))
        assert r["success"] is False

    def test_returns_backend_used(self, simple_dir):
        r = engine.fs_query("*.txt", path=str(simple_dir))
        assert "backend_used" in r

    def test_has_token_estimate(self, simple_dir):
        r = engine.fs_query("*.txt", path=str(simple_dir))
        assert isinstance(r.get("token_estimate"), int)

    def test_has_progress(self, simple_dir):
        r = engine.fs_query("*.txt", path=str(simple_dir))
        assert isinstance(r.get("progress"), list)


# ===========================================================================
# fs_read
# ===========================================================================


class TestFsRead:
    def test_content_mode_correct_lines(self, sample_file):
        r = engine.fs_read(str(sample_file), mode="content", start_line=0, end_line=3)
        assert r["success"] is True
        assert r["mode"] == "content"
        assert len(r["lines"]) == 3

    def test_content_mode_truncated_with_hint(self, sample_file, monkeypatch):
        monkeypatch.setenv("MCP_CONSTRAINED_MODE", "1")
        # Write a file with 50 lines
        big = sample_file.parent / "big.txt"
        big.write_text("\n".join(f"line {i}" for i in range(50)) + "\n")
        r = engine.fs_read(str(big), mode="content", start_line=0, end_line=50)
        assert r["success"] is True
        assert r["truncated"] is True
        assert "hint" in r

    def test_tree_mode_respects_depth(self, messy_dir):
        r = engine.fs_read(str(messy_dir), mode="tree", depth=1)
        assert r["success"] is True
        assert r["mode"] == "tree"
        # depth=1 entries should all be at depth 1
        assert all(e["depth"] <= 1 for e in r["entries"])

    def test_tree_mode_truncates(self, work_dir, monkeypatch):
        monkeypatch.setenv("MCP_CONSTRAINED_MODE", "1")
        # Create enough entries to exceed constrained limit (100 entries)
        for i in range(20):
            subdir = work_dir / f"sub_{i:02d}"
            subdir.mkdir(exist_ok=True)
            for j in range(7):
                (subdir / f"file_{j}.txt").write_text(f"content {i} {j}")
        r = engine.fs_read(str(work_dir), mode="tree", depth=3)
        assert r["success"] is True
        assert r["truncated"] is True
        assert "hint" in r

    def test_meta_mode_returns_metadata(self, sample_file):
        r = engine.fs_read(str(sample_file), mode="meta")
        assert r["success"] is True
        assert r["mode"] == "meta"
        assert "size" in r
        assert "mtime" in r
        assert "permissions" in r
        assert "mime" in r
        assert "is_symlink" in r

    def test_meta_mode_changed_since_true(self, sample_file):
        r = engine.fs_read(str(sample_file), mode="meta", changed_since="2000-01-01T00:00:00Z")
        assert r["success"] is True
        assert r["changed"] is True

    def test_meta_mode_changed_since_false(self, sample_file):
        r = engine.fs_read(str(sample_file), mode="meta", changed_since="2099-01-01T00:00:00Z")
        assert r["success"] is True
        assert r["changed"] is False

    def test_diff_mode_detects_changes(self, work_dir):
        a = work_dir / "file_a.txt"
        b = work_dir / "file_b.txt"
        a.write_text("hello\nworld\n")
        b.write_text("hello\nearth\n")
        r = engine.fs_read(str(a), mode="diff", compare_to=str(b))
        assert r["success"] is True
        assert r["mode"] == "diff"
        assert r["changed"] is True
        assert len(r["diff"]) > 0

    def test_diff_mode_identical_files(self, work_dir):
        a = work_dir / "same_a.txt"
        b = work_dir / "same_b.txt"
        a.write_text("same content\n")
        b.write_text("same content\n")
        r = engine.fs_read(str(a), mode="diff", compare_to=str(b))
        assert r["success"] is True
        assert r["changed"] is False

    def test_auto_mode_file_gives_content(self, sample_file):
        r = engine.fs_read(str(sample_file))
        assert r["success"] is True
        assert r["mode"] == "content"

    def test_auto_mode_dir_gives_tree(self, messy_dir):
        r = engine.fs_read(str(messy_dir))
        assert r["success"] is True
        assert r["mode"] == "tree"

    def test_binary_file_returns_hex_preview(self, work_dir):
        p = work_dir / "binary.bin"
        p.write_bytes(b"\x00\x01\x02\x03" * 100)
        r = engine.fs_read(str(p), mode="content")
        assert r["success"] is True
        assert r.get("binary") is True
        assert "hex_preview" in r
        assert "lines" not in r

    def test_file_not_found_error(self, tmp_home):
        r = engine.fs_read(str(tmp_home / "nonexistent.txt"))
        assert r["success"] is False
        assert "hint" in r

    def test_path_outside_home_error(self):
        r = engine.fs_read("/etc/passwd")
        assert r["success"] is False

    def test_has_token_estimate(self, sample_file):
        r = engine.fs_read(str(sample_file))
        assert isinstance(r.get("token_estimate"), int)


# ===========================================================================
# fs_write — non-delete ops
# ===========================================================================


class TestFsWriteBasicOps:
    def test_write_file_creates_new(self, work_dir):
        p = work_dir / "new.txt"
        r = engine.fs_write([{"op": "write_file", "path": str(p), "content": "hello\n"}])
        assert r["success"] is True
        assert p.read_text() == "hello\n"

    def test_write_file_overwrites_with_snapshot(self, work_dir):
        p = work_dir / "overwrite.txt"
        p.write_text("old\n")
        r = engine.fs_write([{"op": "write_file", "path": str(p), "content": "new\n"}])
        assert r["success"] is True
        assert p.read_text() == "new\n"
        # backup created
        res0 = r["results"][0]
        assert res0["backup"] is not None

    def test_append_file_content_appended(self, sample_file):
        original = sample_file.read_text()
        r = engine.fs_write(
            [{"op": "append_file", "path": str(sample_file), "content": "appended\n"}]
        )
        assert r["success"] is True
        assert sample_file.read_text() == original + "appended\n"

    def test_create_dir_nested(self, work_dir):
        target = work_dir / "a" / "b" / "c"
        r = engine.fs_write([{"op": "create_dir", "path": str(target)}])
        assert r["success"] is True
        assert target.is_dir()

    def test_move_src_gone_dst_correct(self, work_dir):
        src = work_dir / "src.txt"
        dst = work_dir / "dst.txt"
        src.write_text("content")
        r = engine.fs_write([{"op": "move", "src": str(src), "dst": str(dst)}])
        assert r["success"] is True
        assert not src.exists()
        assert dst.read_text() == "content"

    def test_move_dst_exists_returns_error(self, work_dir):
        src = work_dir / "mv_src.txt"
        dst = work_dir / "mv_dst.txt"
        src.write_text("src")
        dst.write_text("dst")
        r = engine.fs_write([{"op": "move", "src": str(src), "dst": str(dst)}])
        assert r["success"] is False
        assert src.exists()  # not moved

    def test_copy_src_preserved_dst_correct(self, work_dir):
        src = work_dir / "cp_src.txt"
        dst = work_dir / "cp_dst.txt"
        src.write_text("hello")
        r = engine.fs_write([{"op": "copy", "src": str(src), "dst": str(dst)}])
        assert r["success"] is True
        assert src.exists()
        assert dst.read_text() == "hello"

    def test_rename_file_in_same_dir(self, work_dir):
        p = work_dir / "old_name.txt"
        p.write_text("rename me")
        r = engine.fs_write([{"op": "rename", "path": str(p), "name": "new_name.txt"}])
        assert r["success"] is True
        assert not p.exists()
        assert (work_dir / "new_name.txt").exists()

    def test_replace_text_occurrences(self, work_dir):
        p = work_dir / "rep.txt"
        p.write_text("foo bar foo baz foo\n")
        r = engine.fs_write(
            [{"op": "replace_text", "path": str(p), "find": "foo", "replace": "qux"}]
        )
        assert r["success"] is True
        assert "qux" in p.read_text()
        assert "foo" not in p.read_text()
        assert r["results"][0]["replacements"] == 3

    def test_replace_text_snapshot_created(self, work_dir, tmp_home):
        p = work_dir / "snap.txt"
        p.write_text("original\n")
        r = engine.fs_write(
            [{"op": "replace_text", "path": str(p), "find": "original", "replace": "changed"}]
        )
        assert r["success"] is True
        assert r["results"][0]["backup"] is not None

    def test_insert_after_pattern(self, work_dir):
        p = work_dir / "ins.txt"
        p.write_text("line1\ninsert_here\nline3\n")
        r = engine.fs_write(
            [
                {
                    "op": "insert_after",
                    "path": str(p),
                    "after_pattern": "insert_here",
                    "content": "NEW LINE",
                }
            ]
        )
        assert r["success"] is True
        lines = p.read_text().splitlines()
        assert lines[2] == "NEW LINE"

    def test_delete_lines_removes_range(self, work_dir):
        p = work_dir / "del_lines.txt"
        p.write_text("a\nb\nc\nd\ne\n")
        r = engine.fs_write(
            [{"op": "delete_lines", "path": str(p), "start_line": 1, "end_line": 3}]
        )
        assert r["success"] is True
        remaining = p.read_text().splitlines()
        assert remaining == ["a", "d", "e"]

    def test_patch_lines_replaces_range(self, work_dir):
        p = work_dir / "patch.txt"
        p.write_text("a\nb\nc\nd\n")
        r = engine.fs_write(
            [
                {
                    "op": "patch_lines",
                    "path": str(p),
                    "start_line": 1,
                    "end_line": 3,
                    "content": "X\nY\n",
                }
            ]
        )
        assert r["success"] is True
        assert p.read_text().splitlines() == ["a", "X", "Y", "d"]

    @pytest.mark.skipif(sys.platform == "win32", reason="no POSIX chmod on Windows")
    def test_set_permissions_posix(self, work_dir):
        p = work_dir / "perms.txt"
        p.write_text("content")
        r = engine.fs_write([{"op": "set_permissions", "path": str(p), "mode": "644"}])
        assert r["success"] is True

    def test_dry_run_disk_unchanged(self, work_dir):
        p = work_dir / "dry.txt"
        r = engine.fs_write(
            [{"op": "write_file", "path": str(p), "content": "should not appear"}],
            dry_run=True,
        )
        assert r["success"] is True
        assert r.get("dry_run") is True
        assert not p.exists()

    def test_invalid_op_name_rejected(self, work_dir):
        p = work_dir / "x.txt"
        r = engine.fs_write([{"op": "do_magic", "path": str(p)}])
        assert r["success"] is False

    def test_partial_invalid_batch_nothing_applied(self, work_dir):
        good = work_dir / "good.txt"
        r = engine.fs_write(
            [
                {"op": "write_file", "path": str(good), "content": "good"},
                {"op": "unknown_op", "path": str(good)},
            ]
        )
        # Validation fails before any op runs
        assert r["success"] is False
        assert not good.exists()

    def test_path_outside_home_error(self):
        r = engine.fs_write([{"op": "write_file", "path": "/etc/hosts", "content": "x"}])
        assert r["success"] is False


# ===========================================================================
# fs_write — deletion protocol
# ===========================================================================


class TestFsWriteDeletion:
    def test_delete_request_returns_pending_no_delete(self, work_dir):
        p = work_dir / "to_del.txt"
        p.write_text("delete me")
        r = engine.fs_write([{"op": "delete_request", "path": str(p)}])
        assert r["success"] is True
        assert r["op"] == "delete_pending"
        assert r["pending"] is True
        assert "confirmation_token" in r
        assert r["confirmation_token"].startswith("del_")
        assert p.exists()  # not yet deleted

    def test_delete_request_targets_in_response(self, work_dir):
        p = work_dir / "targets.txt"
        p.write_text("x")
        r = engine.fs_write([{"op": "delete_request", "path": str(p)}])
        assert r["success"] is True
        assert len(r["targets"]) == 1
        assert r["targets"][0]["path"] == str(p)
        assert "size_kb" in r["targets"][0]
        assert "total_size_kb" in r

    def test_delete_confirm_valid_token(self, work_dir):
        p = work_dir / "confirm_del.txt"
        p.write_text("goodbye")
        r1 = engine.fs_write([{"op": "delete_request", "path": str(p)}])
        token = r1["confirmation_token"]
        r2 = engine.fs_write([{"op": "delete_confirm", "token": token}])
        assert r2["success"] is True
        # delete_confirm is a regular op inside a batch wrapper
        del_result = r2["results"][0]
        assert del_result["op"] == "delete_confirm"
        assert str(p) in del_result["deleted"]
        assert not p.exists()
        assert del_result["backup"] is not None

    def test_delete_confirm_expired_token_error(self, work_dir, monkeypatch):
        import shared.confirm_store as cs

        p = work_dir / "exp.txt"
        p.write_text("x")
        r1 = engine.fs_write([{"op": "delete_request", "path": str(p)}])
        token = r1["confirmation_token"]
        # Expire the token manually
        cs._store[token]["expires_at"] = 0
        r2 = engine.fs_write([{"op": "delete_confirm", "token": token}])
        assert r2["success"] is False
        assert "hint" in r2

    def test_delete_confirm_invalid_token_error(self, work_dir):
        r = engine.fs_write([{"op": "delete_confirm", "token": "del_00000000"}])
        assert r["success"] is False
        assert "hint" in r

    def test_delete_confirm_consumed_token(self, work_dir):
        p = work_dir / "consume.txt"
        p.write_text("x")
        r1 = engine.fs_write([{"op": "delete_request", "path": str(p)}])
        token = r1["confirmation_token"]
        engine.fs_write([{"op": "delete_confirm", "token": token}])
        # Second use of same token should fail
        r3 = engine.fs_write([{"op": "delete_confirm", "token": token}])
        assert r3["success"] is False

    def test_delete_tree_request_returns_combined_token(self, work_dir):
        d = work_dir / "tree_to_del"
        d.mkdir()
        (d / "a.txt").write_text("a")
        (d / "b.txt").write_text("b")
        r = engine.fs_write([{"op": "delete_tree_request", "path": str(d)}])
        assert r["success"] is True
        assert r["pending"] is True
        assert "confirmation_token" in r
        assert d.exists()

    def test_delete_tree_confirm_deletes_all(self, work_dir):
        d = work_dir / "tree_del_confirm"
        d.mkdir()
        (d / "x.txt").write_text("x")
        r1 = engine.fs_write([{"op": "delete_tree_request", "path": str(d)}])
        token = r1["confirmation_token"]
        r2 = engine.fs_write([{"op": "delete_tree_confirm", "token": token}])
        assert r2["success"] is True
        assert not d.exists()

    def test_bulk_delete_one_combined_token(self, work_dir):
        p1 = work_dir / "bulk1.txt"
        p2 = work_dir / "bulk2.txt"
        p1.write_text("1")
        p2.write_text("2")
        r = engine.fs_write(
            [
                {"op": "delete_request", "path": str(p1)},
                {"op": "delete_request", "path": str(p2)},
            ]
        )
        assert r["success"] is True
        assert r["pending"] is True
        assert len(r["targets"]) == 2
        # One combined token
        assert "confirmation_token" in r

    def test_dry_run_delete_request_no_token(self, work_dir):
        p = work_dir / "dry_del.txt"
        p.write_text("x")
        r = engine.fs_write([{"op": "delete_request", "path": str(p)}], dry_run=True)
        assert r["success"] is True
        assert r.get("dry_run") is True
        assert "confirmation_token" not in r
        assert p.exists()


# ===========================================================================
# fs_index
# ===========================================================================


class TestFsIndex:
    def test_build_creates_db(self, work_dir, tmp_home):
        r = engine.fs_index(action="build", path=str(work_dir))
        assert r["success"] is True
        assert r["action"] == "build"
        assert r["indexed"] >= 0
        assert "last_built" in r

    def test_query_returns_matches(self, work_dir, tmp_home):
        (work_dir / "query_test.py").write_text("# test")
        engine.fs_index(action="build", path=str(work_dir))
        r = engine.fs_index(action="query", pattern="*.py")
        assert r["success"] is True
        assert any("query_test.py" in m["name"] for m in r["matches"])

    def test_stats_returns_count_and_last_built(self, work_dir, tmp_home):
        engine.fs_index(action="build", path=str(work_dir))
        r = engine.fs_index(action="stats")
        assert r["success"] is True
        assert r["built"] is True
        assert isinstance(r["file_count"], int)
        assert r["last_built"] is not None

    def test_clear_removes_entries(self, work_dir, tmp_home):
        (work_dir / "clear_test.txt").write_text("x")
        engine.fs_index(action="build", path=str(work_dir))
        r = engine.fs_index(action="clear", path=str(work_dir))
        assert r["success"] is True
        assert r["cleared"] >= 0

    def test_receipt_returns_history(self, work_dir, tmp_home):
        p = work_dir / "receipt_test.txt"
        p.write_text("original")
        engine.fs_write([{"op": "write_file", "path": str(p), "content": "new"}])
        r = engine.fs_index(action="receipt", path=str(p))
        assert r["success"] is True
        assert r["action"] == "receipt"
        assert isinstance(r["history"], list)

    def test_list_returns_entries(self, work_dir, tmp_home):
        (work_dir / "list_a.txt").write_text("a")
        (work_dir / "list_b.py").write_text("b")
        engine.fs_index(action="build", path=str(work_dir))
        r = engine.fs_index(action="list", path=str(work_dir))
        assert r["success"] is True
        assert r["action"] == "list"
        assert isinstance(r["entries"], list)
        assert r["returned"] >= 2
        assert "truncated" in r

    def test_list_no_path_uses_all_entries(self, work_dir, tmp_home):
        (work_dir / "all_a.txt").write_text("a")
        engine.fs_index(action="build", path=str(work_dir))
        r = engine.fs_index(action="list")
        assert r["success"] is True
        assert r["returned"] >= 1

    def test_list_no_index_returns_error(self, work_dir, tmp_home):
        db = Path.home() / ".mcp_fs_index" / "index.db"
        if db.exists():
            db.unlink()
        r = engine.fs_index(action="list", path=str(work_dir))
        assert r["success"] is False
        assert "hint" in r


# ===========================================================================
# fs_manage
# ===========================================================================


class TestFsManage:
    def test_disk_usage_returns_values(self, tmp_home):
        r = engine.fs_manage(action="disk_usage", path=str(tmp_home))
        assert r["success"] is True
        assert r["action"] == "disk_usage"
        assert "total_bytes" in r
        assert "used_bytes" in r
        assert "free_bytes" in r

    def test_permissions_returns_mode(self, sample_file):
        r = engine.fs_manage(action="permissions", path=str(sample_file))
        assert r["success"] is True
        assert "mode_string" in r
        assert "mode_octal" in r

    def test_symlink_info_on_regular_file(self, sample_file):
        r = engine.fs_manage(action="symlink_info", path=str(sample_file))
        assert r["success"] is True
        assert r["is_symlink"] is False

    def test_symlink_info_on_symlink(self, messy_dir):
        link = messy_dir / "link_to_readme.txt"
        if not link.exists() and not link.is_symlink():
            pytest.skip("symlink fixture not available")
        r = engine.fs_manage(action="symlink_info", path=str(link))
        assert r["success"] is True
        assert r["is_symlink"] is True

    def test_versions_returns_list(self, sample_file, tmp_home):
        # Create a snapshot by writing
        engine.fs_write([{"op": "write_file", "path": str(sample_file), "content": "v2\n"}])
        r = engine.fs_manage(action="versions", path=str(sample_file))
        assert r["success"] is True
        assert isinstance(r["versions"], list)


# ===========================================================================
# fs_archive
# ===========================================================================


class TestFsArchive:
    def test_create_zip(self, work_dir):
        src = work_dir / "to_zip"
        src.mkdir()
        (src / "a.txt").write_text("hello")
        arc = work_dir / "archive.zip"
        r = engine.fs_archive(action="create", path=str(arc), target=str(src))
        assert r["success"] is True
        assert arc.exists()
        assert r["format"] == "zip"

    def test_create_targz(self, work_dir):
        src = work_dir / "to_tar"
        src.mkdir()
        (src / "b.txt").write_text("world")
        arc = work_dir / "archive.tar.gz"
        r = engine.fs_archive(action="create", path=str(arc), target=str(src), format_="tar.gz")
        assert r["success"] is True
        assert arc.exists()

    def test_extract_zip(self, work_dir):
        src = work_dir / "extract_src"
        src.mkdir()
        (src / "c.txt").write_text("extract me")
        arc = work_dir / "extract.zip"
        engine.fs_archive(action="create", path=str(arc), target=str(src))
        out = work_dir / "extracted"
        r = engine.fs_archive(action="extract", path=str(arc), target=str(out))
        assert r["success"] is True
        assert out.is_dir()

    def test_extract_targz(self, work_dir):
        src = work_dir / "tar_src"
        src.mkdir()
        (src / "d.txt").write_text("tar content")
        arc = work_dir / "test.tar.gz"
        engine.fs_archive(action="create", path=str(arc), target=str(src), format_="tar.gz")
        out = work_dir / "tar_out"
        r = engine.fs_archive(action="extract", path=str(arc), target=str(out))
        assert r["success"] is True

    def test_list_zip(self, work_dir):
        src = work_dir / "list_src.txt"
        src.write_text("list me")
        arc = work_dir / "list.zip"
        engine.fs_archive(action="create", path=str(arc), target=str(src))
        r = engine.fs_archive(action="list", path=str(arc))
        assert r["success"] is True
        assert r["count"] >= 1
        assert isinstance(r["entries"], list)

    def test_extract_conflict_no_overwrite_error(self, work_dir):
        src = work_dir / "conflict_src"
        src.mkdir()
        (src / "conflict.txt").write_text("original")
        arc = work_dir / "conflict.zip"
        engine.fs_archive(action="create", path=str(arc), target=str(src))
        out = work_dir / "conflict_out"
        out.mkdir()
        (out / "conflict_src" / "conflict.txt").parent.mkdir(parents=True, exist_ok=True)
        (out / "conflict_src" / "conflict.txt").write_text("existing")
        r = engine.fs_archive(action="extract", path=str(arc), target=str(out))
        assert r["success"] is False
        assert "conflicts" in r

    def test_dry_run_create_no_file(self, work_dir):
        src = work_dir / "dry_zip_src"
        src.mkdir()
        (src / "e.txt").write_text("dry")
        arc = work_dir / "dry.zip"
        r = engine.fs_archive(action="create", path=str(arc), target=str(src), dry_run=True)
        assert r["success"] is True
        assert r.get("dry_run") is True
        assert not arc.exists()


# ===========================================================================
# Alias normalisation — intuitive synonyms must resolve correctly
# ===========================================================================


class TestAliases:
    def test_fs_query_type_directory(self, work_dir):
        (work_dir / "sub_a").mkdir()
        (work_dir / "sub_b").mkdir()
        (work_dir / "file.txt").write_text("x")
        r = engine.fs_query("*", path=str(work_dir), type_="directory")
        assert r["success"] is True
        for m in r["matches"]:
            assert Path(m).is_dir()

    def test_fs_query_type_folder(self, work_dir):
        (work_dir / "fold_a").mkdir()
        (work_dir / "fold_b").mkdir()
        r = engine.fs_query("fold_*", path=str(work_dir), type_="folder")
        assert r["success"] is True
        assert len(r["matches"]) == 2
        for m in r["matches"]:
            assert Path(m).is_dir()

    def test_fs_query_type_files(self, simple_dir):
        r = engine.fs_query("*", path=str(simple_dir), type_="files")
        assert r["success"] is True
        for m in r["matches"]:
            assert Path(m).is_file()

    def test_fs_read_mode_list_gives_tree(self, work_dir):
        (work_dir / "a.txt").write_text("a")
        (work_dir / "sub").mkdir()
        r = engine.fs_read(str(work_dir), mode="list")
        assert r["success"] is True
        assert r["mode"] == "tree"

    def test_fs_read_mode_stat_gives_meta(self, sample_file):
        r = engine.fs_read(str(sample_file), mode="stat")
        assert r["success"] is True
        assert r["mode"] == "meta"
        assert "size" in r

    def test_fs_read_mode_info_gives_meta(self, sample_file):
        r = engine.fs_read(str(sample_file), mode="info")
        assert r["success"] is True
        assert r["mode"] == "meta"

    def test_fs_manage_action_symlink(self, sample_file):
        r = engine.fs_manage(action="symlink", path=str(sample_file))
        assert r["success"] is True
        assert r["action"] == "symlink_info"

    def test_fs_manage_action_snapshot(self, sample_file, tmp_home):
        engine.fs_write([{"op": "write_file", "path": str(sample_file), "content": "v2\n"}])
        r = engine.fs_manage(action="snapshot", path=str(sample_file))
        assert r["success"] is True
        assert r["action"] == "versions"

    def test_fs_manage_action_perms(self, sample_file):
        r = engine.fs_manage(action="perms", path=str(sample_file))
        assert r["success"] is True
        assert r["action"] == "permissions"

    def test_fs_manage_action_space(self, tmp_home):
        r = engine.fs_manage(action="space", path=str(tmp_home))
        assert r["success"] is True
        assert r["action"] == "disk_usage"

    def test_fs_archive_format_tar(self, work_dir):
        src = work_dir / "alias_src"
        src.mkdir()
        (src / "f.txt").write_text("x")
        arc = work_dir / "alias.tar.gz"
        r = engine.fs_archive(action="create", path=str(arc), target=str(src), format_="tar")
        assert r["success"] is True
        assert arc.exists()

    def test_fs_archive_format_tgz(self, work_dir):
        src = work_dir / "tgz_src"
        src.mkdir()
        (src / "g.txt").write_text("y")
        arc = work_dir / "alias.tgz.tar.gz"
        r = engine.fs_archive(action="create", path=str(arc), target=str(src), format_="tgz")
        assert r["success"] is True


# ===========================================================================
# Return value contract — every response must have required fields
# ===========================================================================


class TestReturnValueContract:
    def _check_required(self, r: dict) -> None:
        assert isinstance(r, dict), "response must be a dict"
        assert "success" in r, "missing 'success'"
        assert "op" in r, "missing 'op'"
        assert "progress" in r, "missing 'progress'"
        assert isinstance(r["progress"], list), "'progress' must be a list"
        assert "token_estimate" in r, "missing 'token_estimate'"
        assert isinstance(r["token_estimate"], int), "'token_estimate' must be int"
        if not r["success"]:
            assert "error" in r, "failed response missing 'error'"
            assert "hint" in r, "failed response missing 'hint'"

    def test_fs_query_contract(self, simple_dir):
        self._check_required(engine.fs_query("*.txt", path=str(simple_dir)))

    def test_fs_query_error_contract(self):
        self._check_required(engine.fs_query("*.py", path="/etc"))

    def test_fs_read_contract(self, sample_file):
        self._check_required(engine.fs_read(str(sample_file)))

    def test_fs_read_error_contract(self, tmp_home):
        self._check_required(engine.fs_read(str(tmp_home / "ghost.txt")))

    def test_fs_write_contract(self, work_dir):
        p = work_dir / "contract.txt"
        self._check_required(
            engine.fs_write([{"op": "write_file", "path": str(p), "content": "x"}])
        )

    def test_fs_write_error_contract(self):
        self._check_required(engine.fs_write([{"op": "bad_op", "path": "/tmp/x"}]))

    def test_fs_index_contract(self, work_dir, tmp_home):
        self._check_required(engine.fs_index(action="stats"))

    def test_fs_manage_contract(self, tmp_home):
        self._check_required(engine.fs_manage(action="disk_usage"))

    def test_fs_archive_contract(self, work_dir):
        arc = work_dir / "contract.zip"
        src = work_dir / "ctr_src.txt"
        src.write_text("x")
        self._check_required(engine.fs_archive(action="create", path=str(arc), target=str(src)))
