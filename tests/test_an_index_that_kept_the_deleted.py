"""One build of one directory answered with two different sizes.

    fs_index action=build  path=/workspace/data -> indexed 41, files 29, dirs 12
    fs_index action=stats                       -> 53 entries, 37 files, 15 dirs
    fs_index action=list                        -> /workspace/data/vocab_verify/...
    ls /workspace/data/vocab_verify             -> No such file or directory

Nothing was written between the build and the stats. The extra twelve rows were
a directory tree deleted that morning, still carrying its real old sizes and
mtimes, and `list` served them beside live ones with nothing to tell them apart.

`_action_build` only ever ran INSERT OR REPLACE. A path that disappeared since
the last build kept its row forever, because nothing in a "build" ever removed
anything. So the index grew monotonically and every reader -- list, query,
stats -- reported the accumulated total while build reported the walk. A stale
row is worse than a missing one: a caller can act on it, and the sweep did,
reading a file count that had not been true for hours.

A build now drops its own subtree first, in the same transaction as the inserts
that follow, so a walk that dies partway rolls back to the previous index rather
than emptying it. `dropped_stale` says how many rows went, because a silent
correction is how the two numbers diverged unnoticed in the first place.

The neighbouring bug, found while fixing this one: every root filter here was
`path LIKE root || '%'` -- no separator -- so clearing or listing
/workspace/data also reached /workspace/database. And SQLite's LIKE reads `_`
as a single-character wildcard, which ordinary names like Ad_Data carry. Four
sites, one helper.

Found in a round-15 sweep report.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _basic_index  # noqa: E402
import engine  # noqa: E402


@pytest.fixture()
def indexed_dir(work_dir: Path) -> Path:
    d = work_dir / "site"
    (d / "keep").mkdir(parents=True)
    (d / "keep" / "a.txt").write_text("a\n", encoding="utf-8")
    (d / "doomed").mkdir()
    (d / "doomed" / "b.txt").write_text("b\n", encoding="utf-8")
    (d / "doomed" / "c.txt").write_text("c\n", encoding="utf-8")
    return d


def entries_under(root: Path) -> set[str]:
    r = engine.fs_index(action="list", path=str(root), max_results=500)
    assert r["success"] is True, r
    return {e["path"] if isinstance(e, dict) else e for e in r["entries"]}


class TestARebuildForgetsWhatIsGone:
    def test_the_deleted_tree_leaves_the_index(self, indexed_dir: Path) -> None:
        engine.fs_index(action="build", path=str(indexed_dir))
        assert any("doomed" in p for p in entries_under(indexed_dir))

        import shutil

        shutil.rmtree(indexed_dir / "doomed")
        engine.fs_index(action="build", path=str(indexed_dir))

        left = entries_under(indexed_dir)
        assert not [p for p in left if "doomed" in p], sorted(left)

    def test_build_and_stats_agree_afterwards(self, indexed_dir: Path) -> None:
        """The symptom as the sweep saw it: one build, two totals."""
        import shutil

        engine.fs_index(action="build", path=str(indexed_dir))
        shutil.rmtree(indexed_dir / "doomed")
        built = engine.fs_index(action="build", path=str(indexed_dir))
        stats = engine.fs_index(action="stats")

        assert stats["entry_count"] == built["indexed"], (stats, built)
        assert stats["file_count"] == built["files"], (stats, built)

    def test_it_says_how_many_it_dropped(self, indexed_dir: Path) -> None:
        import shutil

        engine.fs_index(action="build", path=str(indexed_dir))
        shutil.rmtree(indexed_dir / "doomed")
        built = engine.fs_index(action="build", path=str(indexed_dir))
        # doomed/ itself plus its two files.
        assert built["dropped_stale"] == 3, built

    def test_an_unchanged_rebuild_drops_nothing(self, indexed_dir: Path) -> None:
        engine.fs_index(action="build", path=str(indexed_dir))
        again = engine.fs_index(action="build", path=str(indexed_dir))
        assert again["dropped_stale"] == 0, again
        assert again["indexed"] == 5, again  # keep, keep/a, doomed, doomed/b, doomed/c

    def test_a_query_cannot_return_a_path_that_is_gone(self, indexed_dir: Path) -> None:
        import shutil

        engine.fs_index(action="build", path=str(indexed_dir))
        shutil.rmtree(indexed_dir / "doomed")
        engine.fs_index(action="build", path=str(indexed_dir))

        r = engine.fs_index(action="query", pattern="b.txt", path=str(indexed_dir))
        assert r["returned"] == 0, r["matches"]

    def test_live_files_are_still_there(self, indexed_dir: Path) -> None:
        """The rebuild must not take the survivors with it."""
        import shutil

        engine.fs_index(action="build", path=str(indexed_dir))
        shutil.rmtree(indexed_dir / "doomed")
        engine.fs_index(action="build", path=str(indexed_dir))
        assert any(p.endswith("a.txt") for p in entries_under(indexed_dir))


class TestARootIsNotAStringPrefix:
    """/workspace/data must not reach /workspace/database."""

    @pytest.fixture()
    def two_trees(self, work_dir: Path) -> tuple[Path, Path]:
        data = work_dir / "data"
        data.mkdir()
        (data / "one.txt").write_text("1\n", encoding="utf-8")
        database = work_dir / "database"
        database.mkdir()
        (database / "two.txt").write_text("2\n", encoding="utf-8")
        return data, database

    def test_clear_leaves_the_neighbour_alone(self, two_trees) -> None:
        data, database = two_trees
        engine.fs_index(action="build", path=str(data))
        engine.fs_index(action="build", path=str(database))
        assert entries_under(database)

        engine.fs_index(action="clear", path=str(data))
        assert not entries_under(data)
        assert entries_under(database), "clearing data/ took database/ with it"

    def test_build_leaves_the_neighbour_alone(self, two_trees) -> None:
        data, database = two_trees
        engine.fs_index(action="build", path=str(database))
        engine.fs_index(action="build", path=str(data))
        assert entries_under(database), "rebuilding data/ dropped database/'s rows"

    def test_list_does_not_bleed_across(self, two_trees) -> None:
        data, database = two_trees
        engine.fs_index(action="build", path=str(data))
        engine.fs_index(action="build", path=str(database))
        assert not [p for p in entries_under(data) if "two.txt" in p]


class TestUnderscoresAreNotWildcards:
    """SQLite LIKE reads `_` as any-one-character, and real names carry it."""

    def test_an_underscored_root_does_not_match_its_lookalike(self, work_dir: Path) -> None:
        real = work_dir / "Ad_Data"
        real.mkdir()
        (real / "real.txt").write_text("r\n", encoding="utf-8")
        lookalike = work_dir / "AdXData"
        lookalike.mkdir()
        (lookalike / "other.txt").write_text("o\n", encoding="utf-8")

        engine.fs_index(action="build", path=str(real))
        engine.fs_index(action="build", path=str(lookalike))

        found = entries_under(real)
        assert not [p for p in found if "other.txt" in p], sorted(found)

    def test_the_pattern_escapes_what_it_should(self) -> None:
        like, esc = _basic_index._subtree_like(Path("/tmp/Ad_Data"))
        assert esc == "\\"
        assert r"Ad\_Data" in like, like
        assert like.endswith("%")
