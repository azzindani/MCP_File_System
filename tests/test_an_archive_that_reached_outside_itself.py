"""Archiving a folder copied in the contents of a file outside it.

    fs_archive(action="create", target=<tree>, format_="zip")
      -> files_archived: 4

`tree` held three ordinary files and one symlink. zipfile.write() opens the
path it is given and reads it, so the symlink was followed and its target's
bytes were stored under the link's name. When the target lived outside `tree`
-- which is the ordinary reason to have a symlink at all -- the zip came out
holding data from a directory the caller never named. Hand that zip to someone
and you have handed them the file it pointed at.

tar.gz, the same call with one argument changed, stored the link as a link and
leaked nothing. Both replies said files_archived: 4.

Round 16 found the asymmetry and called it out; the probe here found the part
that made it more than cosmetic, which is that the dereference crosses the
boundary of the directory being archived. It also turned up a third case: a
symlink to a *directory* answers False to is_file() and pathlib does not
recurse through it, so it fell out of the zip entirely and was not counted
either -- a silent omission sitting next to a silent inclusion.

Now both formats store links as links, and the response carries
symlinks_archived and symlinks_pointing_outside beside files_archived, so one
number can no longer stand for two different archives.

Making zip store links exposed the other half: extractall() writes a link entry
out as a plain file holding the target path, so a stored link did not survive
the round trip. The zip side now recreates them, refusing any that resolve
outside the destination -- the rule tarfile's data filter already applied. And
that filter *raised*, so one link to an absolute path cost the caller the whole
archive and left nothing on disk; it now skips that member and extracts the
rest, which is what the zip side does.
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from servers.fs_basic import engine

SECRET = "TOP-SECRET-CONTENTS-abc123"


@pytest.fixture()
def tree_with_links(work_dir: Path) -> Path:
    """Three ordinary files, plus links pointing in and out of the tree."""
    outside_file = work_dir / "OUTSIDE_secret.txt"
    outside_file.write_text(SECRET + "\n", encoding="utf-8")
    outside_dir = work_dir / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "deep.txt").write_text("DEEP-" + SECRET + "\n", encoding="utf-8")

    tree = work_dir / "tree"
    tree.mkdir()
    (tree / "normal.txt").write_text("ordinary\n", encoding="utf-8")
    (tree / "second.txt").write_text("also ordinary\n", encoding="utf-8")
    os.symlink(outside_file, tree / "escaping_link.txt")
    os.symlink("normal.txt", tree / "inside_link.txt")
    os.symlink(outside_dir, tree / "escaping_dirlink")
    return tree


def _archive(tree: Path, dest: Path, fmt: str) -> dict:
    r = engine.fs_archive(action="create", path=str(dest), target=str(tree), format_=fmt)
    assert r["success"] is True, r.get("error")
    return r


def _stored_contents(arc: Path, fmt: str) -> dict[str, bytes]:
    """What each entry actually holds, decompressed.

    Scanning the archive file's raw bytes for the secret proves nothing: both
    formats compress, so the string is absent from the container even when the
    entry inside carries it. That check passed against the broken code.
    """
    out: dict[str, bytes] = {}
    if fmt == "zip":
        with zipfile.ZipFile(arc) as zf:
            for zi in zf.infolist():
                out[zi.filename] = zf.read(zi)
    else:
        with tarfile.open(arc) as tf:
            for m in tf.getmembers():
                # extractfile() follows a link *within* the archive and raises
                # KeyError when the target is not a member. A link carries no
                # data of its own, which is the whole point.
                if m.issym() or m.islnk() or not m.isfile():
                    continue
                fh = tf.extractfile(m)
                out[m.name] = fh.read() if fh is not None else b""
    return out


class TestNoDataCrossesTheBoundary:
    """The reason this is more than a formatting difference."""

    @pytest.mark.parametrize("fmt,ext", [("zip", ".zip"), ("tar.gz", ".tar.gz")])
    def test_the_probe_would_notice_a_leak(
        self, tree_with_links: Path, work_dir: Path, fmt: str, ext: str
    ) -> None:
        """Dereference one link by hand and confirm _stored_contents sees it."""
        bait = work_dir / "bait"
        bait.mkdir()
        (bait / "copied.txt").write_bytes((work_dir / "OUTSIDE_secret.txt").read_bytes())
        arc = work_dir / ("bait" + ext)
        _archive(bait, arc, fmt)
        assert any(SECRET.encode() in v for v in _stored_contents(arc, fmt).values())

    @pytest.mark.parametrize("fmt,ext", [("zip", ".zip"), ("tar.gz", ".tar.gz")])
    def test_no_entry_holds_a_byte_from_outside_the_tree(
        self, tree_with_links: Path, work_dir: Path, fmt: str, ext: str
    ) -> None:
        arc = work_dir / ("out" + ext)
        _archive(tree_with_links, arc, fmt)
        for name, blob in _stored_contents(arc, fmt).items():
            assert SECRET.encode() not in blob, f"{fmt} stored outside data under {name}"

    def test_the_zip_stores_the_link_rather_than_its_target(
        self, tree_with_links: Path, work_dir: Path
    ) -> None:
        arc = work_dir / "out.zip"
        _archive(tree_with_links, arc, "zip")
        with zipfile.ZipFile(arc) as zf:
            by_name = {i.filename: i for i in zf.infolist()}
            link = by_name["tree/escaping_link.txt"]
            assert (link.external_attr >> 16) & 0o170000 == 0o120000, "not stored as a symlink"
            assert zf.read(link).decode().endswith("OUTSIDE_secret.txt")

    def test_tar_gz_is_unchanged_and_still_stores_links(
        self, tree_with_links: Path, work_dir: Path
    ) -> None:
        arc = work_dir / "out.tar.gz"
        _archive(tree_with_links, arc, "tar.gz")
        with tarfile.open(arc) as tf:
            syms = {m.name for m in tf.getmembers() if m.issym()}
        assert "tree/escaping_link.txt" in syms
        assert "tree/inside_link.txt" in syms


class TestTheCountsNoLongerHideIt:
    def test_files_archived_counts_only_regular_files(
        self, tree_with_links: Path, work_dir: Path
    ) -> None:
        r = _archive(tree_with_links, work_dir / "out.zip", "zip")
        assert r["files_archived"] == 2, r

    def test_symlinks_are_counted_separately(self, tree_with_links: Path, work_dir: Path) -> None:
        r = _archive(tree_with_links, work_dir / "out.zip", "zip")
        assert r["symlinks_archived"] == 3, r

    def test_the_escaping_ones_are_named(self, tree_with_links: Path, work_dir: Path) -> None:
        r = _archive(tree_with_links, work_dir / "out.zip", "zip")
        assert set(r["symlinks_pointing_outside"]) == {"escaping_link.txt", "escaping_dirlink"}, r

    def test_the_hint_says_they_will_dangle(self, tree_with_links: Path, work_dir: Path) -> None:
        r = _archive(tree_with_links, work_dir / "out.zip", "zip")
        assert "dangle" in r["hint"], r["hint"]

    def test_both_formats_report_the_same_counts(
        self, tree_with_links: Path, work_dir: Path
    ) -> None:
        """One call, one argument changed, must not mean two different archives."""
        z = _archive(tree_with_links, work_dir / "a.zip", "zip")
        t = _archive(tree_with_links, work_dir / "a.tar.gz", "tar.gz")
        keys = ("files_archived", "symlinks_archived", "symlinks_pointing_outside")
        assert {k: z[k] for k in keys} == {k: t[k] for k in keys}

    def test_a_tree_with_no_links_says_nothing_about_them(self, work_dir: Path) -> None:
        plain = work_dir / "plain"
        plain.mkdir()
        (plain / "a.txt").write_text("a", encoding="utf-8")
        r = _archive(plain, work_dir / "plain.zip", "zip")
        assert r["symlinks_archived"] == 0
        assert r["symlinks_pointing_outside"] == []
        assert "hint" not in r


class TestTheSymlinkedDirectoryNoLongerVanishes:
    """It answered False to is_file() and pathlib would not recurse it."""

    def test_it_is_present_in_the_zip(self, tree_with_links: Path, work_dir: Path) -> None:
        arc = work_dir / "out.zip"
        _archive(tree_with_links, arc, "zip")
        with zipfile.ZipFile(arc) as zf:
            assert "tree/escaping_dirlink" in zf.namelist()

    def test_its_contents_did_not_come_along(self, tree_with_links: Path, work_dir: Path) -> None:
        arc = work_dir / "out.zip"
        _archive(tree_with_links, arc, "zip")
        with zipfile.ZipFile(arc) as zf:
            assert not [n for n in zf.namelist() if "deep.txt" in n]


class TestTheRoundTrip:
    @pytest.mark.parametrize("fmt,ext", [("zip", ".zip"), ("tar.gz", ".tar.gz")])
    def test_an_inside_link_comes_back_as_a_link(
        self, tree_with_links: Path, work_dir: Path, fmt: str, ext: str
    ) -> None:
        arc = work_dir / ("rt" + ext)
        _archive(tree_with_links, arc, fmt)
        out = work_dir / ("unpacked_" + fmt)
        e = engine.fs_archive(action="extract", path=str(arc), target=str(out))
        assert e["success"] is True, e.get("error")
        link = out / "tree" / "inside_link.txt"
        assert link.is_symlink(), f"{fmt} did not restore the link"
        assert os.readlink(link) == "normal.txt"

    @pytest.mark.parametrize("fmt,ext", [("zip", ".zip"), ("tar.gz", ".tar.gz")])
    def test_an_escaping_link_is_skipped_not_recreated(
        self, tree_with_links: Path, work_dir: Path, fmt: str, ext: str
    ) -> None:
        arc = work_dir / ("rt" + ext)
        _archive(tree_with_links, arc, fmt)
        out = work_dir / ("unpacked_" + fmt)
        e = engine.fs_archive(action="extract", path=str(arc), target=str(out))
        assert not (out / "tree" / "escaping_link.txt").exists()
        assert any("escaping_link.txt" in s for s in e["symlinks_skipped"]), e

    @pytest.mark.parametrize("fmt,ext", [("zip", ".zip"), ("tar.gz", ".tar.gz")])
    def test_one_bad_link_does_not_cost_the_whole_archive(
        self, tree_with_links: Path, work_dir: Path, fmt: str, ext: str
    ) -> None:
        """filter="data" raised, so tar.gz used to extract nothing at all."""
        arc = work_dir / ("rt" + ext)
        _archive(tree_with_links, arc, fmt)
        out = work_dir / ("unpacked_" + fmt)
        e = engine.fs_archive(action="extract", path=str(arc), target=str(out))
        assert e["success"] is True, e.get("error")
        assert (out / "tree" / "normal.txt").read_text() == "ordinary\n"
        assert (out / "tree" / "second.txt").read_text() == "also ordinary\n"
        assert e["extracted_files"] == 2, e

    def test_nothing_lands_outside_the_destination(
        self, tree_with_links: Path, work_dir: Path
    ) -> None:
        arc = work_dir / "rt.zip"
        _archive(tree_with_links, arc, "zip")
        out = work_dir / "unpacked"
        engine.fs_archive(action="extract", path=str(arc), target=str(out))
        for p in out.rglob("*"):
            if p.is_symlink():
                resolved = Path(os.path.join(p.parent, os.readlink(p))).resolve()
                assert resolved.is_relative_to(out.resolve()), p

    def test_both_formats_extract_to_the_same_tree(
        self, tree_with_links: Path, work_dir: Path
    ) -> None:
        results = {}
        for fmt, ext in (("zip", ".zip"), ("tar.gz", ".tar.gz")):
            arc = work_dir / ("cmp" + ext)
            _archive(tree_with_links, arc, fmt)
            out = work_dir / ("cmp_" + fmt)
            engine.fs_archive(action="extract", path=str(arc), target=str(out))
            results[fmt] = sorted(
                (p.relative_to(out).as_posix(), p.is_symlink()) for p in out.rglob("*")
            )
        assert results["zip"] == results["tar.gz"], results
