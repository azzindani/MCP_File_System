"""The sentence a caller decides on said the opposite of what the server does.

    delete_request  -> warning: "Permanently deletes 1 item(s) (0.1 KB).
                                 Cannot be undone."
    delete_confirm  -> backup:  ".../victim_2026-08-25T08-31-04Z.txt.bak"

Every delete on this server is snapshotted first -- a file copied into
`.mcp_versions/`, a directory zipped into it -- which an earlier round added
precisely so that a recursive delete had a way back
([[test_a_tree_delete_can_be_undone]]). The warning was never updated, so the
most consequential line the server prints was false.

False in both directions at once, which is what makes it worth a test rather
than a one-word edit:

* someone deleting something sensitive is told the bytes are gone, while a
  complete copy sits in a directory beside them;
* someone who needs the file is scared off an operation that was reversible
  the whole time, and the truth is only in the *confirm* response -- after the
  decision has been made.

The warning is now derived rather than asserted: `_no_snapshot_reason()` asks,
per target, whether the confirm step will actually be able to keep a copy, and
the sentence is built from the answers. A tree over the snapshot cap is the one
case that really cannot be undone, and that is the one case that now says so --
naming which target, because a mixed request where one of four is unrecoverable
is exactly where a summary sentence misleads.

Found in a round-15 sweep report, in the notes column of a row marked PASS:
"CONTRADICTION: request warning said 'Cannot be undone' but tool quietly kept
.mcp_versions/victim_*.txt.bak".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _basic_write  # noqa: E402
import engine  # noqa: E402


def write(op: str, **kw) -> dict:
    outer = engine.fs_write([dict(op=op, **kw)])
    return outer.get("results", [outer])[0] if isinstance(outer, dict) else outer


@pytest.fixture()
def victim(work_dir: Path) -> Path:
    f = work_dir / "victim.txt"
    f.write_bytes(b"something worth keeping\n")
    return f


@pytest.fixture()
def victim_tree(work_dir: Path) -> Path:
    d = work_dir / "project"
    (d / "nested").mkdir(parents=True)
    (d / "a.txt").write_bytes(b"alpha\n")
    (d / "nested" / "b.txt").write_bytes(b"beta\n")
    return d


class TestTheWarningMatchesWhatHappens:
    def test_a_file_delete_no_longer_claims_to_be_permanent(self, victim: Path) -> None:
        r = write("delete_request", path=str(victim))
        assert "Cannot be undone" not in r["warning"], r["warning"]
        assert "can be undone" in r["warning"], r["warning"]

    def test_it_says_where_the_copy_goes(self, victim: Path) -> None:
        r = write("delete_request", path=str(victim))
        assert ".mcp_versions" in r["warning"], r["warning"]

    def test_it_names_the_tool_that_lists_them(self, victim: Path) -> None:
        """A promise of recoverability is only useful with the way back attached."""
        assert "fs_manage" in write("delete_request", path=str(victim))["warning"]

    def test_a_tree_delete_says_the_same(self, victim_tree: Path) -> None:
        r = write("delete_tree_request", path=str(victim_tree))
        assert "Cannot be undone" not in r["warning"], r["warning"]
        assert "can be undone" in r["warning"], r["warning"]

    def test_the_promise_is_kept(self, victim: Path) -> None:
        """The whole point: what the warning said, checked against what happened."""
        request = write("delete_request", path=str(victim))
        assert "can be undone" in request["warning"]
        confirmed = write("delete_confirm", token=request["confirmation_token"])
        assert confirmed["success"] is True, confirmed.get("error")
        assert not victim.exists()
        assert confirmed["backups"], "warning promised a copy and none was kept"
        assert Path(confirmed["backups"][0]).exists()

    def test_the_tree_promise_is_kept(self, victim_tree: Path) -> None:
        request = write("delete_tree_request", path=str(victim_tree))
        assert "can be undone" in request["warning"]
        confirmed = write("delete_tree_confirm", token=request["confirmation_token"])
        assert confirmed["success"] is True, confirmed.get("error")
        assert confirmed["backups"], "warning promised a copy and none was kept"
        assert Path(confirmed["backups"][0]).exists()

    def test_each_target_says_whether_it_is_recoverable(self, victim: Path) -> None:
        target = write("delete_request", path=str(victim))["targets"][0]
        assert target["recoverable"] is True
        assert "no_snapshot_reason" not in target

    def test_the_size_is_still_reported(self, victim: Path) -> None:
        """The rest of the sentence has to survive the rewrite."""
        r = write("delete_request", path=str(victim))
        assert "1 item(s)" in r["warning"], r["warning"]
        assert "KB" in r["warning"], r["warning"]


class TestTheOneCaseThatReallyIsPermanent:
    """A tree over the snapshot cap. The warning has to change back for it."""

    def test_an_oversized_tree_says_it_cannot_be_undone(
        self, victim_tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_basic_write, "MAX_TREE_SNAPSHOT_BYTES", 1)
        r = write("delete_tree_request", path=str(victim_tree))
        assert "cannot be undone" in r["warning"], r["warning"]
        assert victim_tree.name in r["warning"], r["warning"]

    def test_it_says_why_on_the_target(
        self, victim_tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_basic_write, "MAX_TREE_SNAPSHOT_BYTES", 1)
        target = write("delete_tree_request", path=str(victim_tree))["targets"][0]
        assert target["recoverable"] is False
        assert "tree-snapshot limit" in target["no_snapshot_reason"]

    def test_that_prediction_is_what_actually_happens(
        self, victim_tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The warning is a forecast; this is the only test that checks it came true."""
        import shared.version_control as vc

        monkeypatch.setattr(_basic_write, "MAX_TREE_SNAPSHOT_BYTES", 1)
        monkeypatch.setattr(vc, "MAX_TREE_SNAPSHOT_BYTES", 1)
        request = write("delete_tree_request", path=str(victim_tree))
        assert "cannot be undone" in request["warning"]
        confirmed = write("delete_tree_confirm", token=request["confirmation_token"])
        assert confirmed["success"] is True, confirmed.get("error")
        assert not confirmed["backups"], "warning said no copy would be kept, and one was"

    def test_a_lone_unrecoverable_target_does_not_mention_others(
        self, victim_tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "Anything else is copied" beside one target implies a copy exists."""
        monkeypatch.setattr(_basic_write, "MAX_TREE_SNAPSHOT_BYTES", 1)
        assert "Anything else" not in write("delete_tree_request", path=str(victim_tree))["warning"]


class TestAMixedRequestNamesWhichOne:
    """Where a one-sentence summary misleads most: some recoverable, some not."""

    def test_it_names_the_unrecoverable_one(
        self, work_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        small = work_dir / "small"
        (small).mkdir()
        (small / "s.txt").write_bytes(b"s\n")
        big = work_dir / "big"
        big.mkdir()
        (big / "b.txt").write_bytes(b"b" * 4096)

        # Only `big` is over the cap, so only `big` loses its copy.
        monkeypatch.setattr(_basic_write, "MAX_TREE_SNAPSHOT_BYTES", 100)
        r = engine.fs_write(
            [
                {"op": "delete_tree_request", "path": str(small)},
                {"op": "delete_tree_request", "path": str(big)},
            ]
        )
        r = r.get("results", [r])[0]
        assert "big" in r["warning"], r["warning"]
        assert "small" not in r["warning"], r["warning"]
        assert "cannot be undone" in r["warning"], r["warning"]
        assert "Anything else is copied" in r["warning"], r["warning"]

    def test_each_target_carries_its_own_answer(
        self, work_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        small = work_dir / "small"
        small.mkdir()
        (small / "s.txt").write_bytes(b"s\n")
        big = work_dir / "big"
        big.mkdir()
        (big / "b.txt").write_bytes(b"b" * 4096)

        monkeypatch.setattr(_basic_write, "MAX_TREE_SNAPSHOT_BYTES", 100)
        r = engine.fs_write(
            [
                {"op": "delete_tree_request", "path": str(small)},
                {"op": "delete_tree_request", "path": str(big)},
            ]
        )
        r = r.get("results", [r])[0]
        by_name = {Path(t["path"]).name: t for t in r["targets"]}
        assert by_name["small"]["recoverable"] is True
        assert by_name["big"]["recoverable"] is False
