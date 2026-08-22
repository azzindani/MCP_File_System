"""fs_manage rejected the first word of its own description.

The tool description is "Disk usage, permissions, symlink info, or snapshot
version list." The schema carries no enum and no parameter descriptions, so that
sentence is the whole vocabulary a caller can see, and the alias table already
recognised a word from three of its four phrases -- "symlink" for symlink_info,
"snapshot" for versions, "perms" for permissions. "usage" was the gap:

    action='usage'  ->  Unknown action 'usage'

found by calling every tool with only the arguments its schema marks required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from servers.fs_basic.engine import fs_manage
from servers.fs_basic.server import fs_manage as tool

CANONICAL = ["disk_usage", "permissions", "symlink_info", "versions"]


def described_words() -> list[str]:
    """Single words a caller can lift straight out of the description."""
    doc = getattr(tool, "description", None) or tool.__doc__ or ""
    return [w.lower() for w in re.findall(r"[A-Za-z]+", doc)]


class TestEveryWordInTheDescriptionIsUnderstood:
    @pytest.mark.parametrize(
        "action", ["usage", "disk_usage", "permissions", "symlink", "snapshot"]
    )
    def test_it_is_not_rejected_as_unknown(self, action: str, tmp_path: Path):
        r = fs_manage(action, str(tmp_path))
        error = str(r.get("error", ""))
        assert "Unknown action" not in error, f"{action}: {error}"

    def test_usage_means_disk_usage(self, tmp_path: Path):
        by_word = fs_manage("usage", str(tmp_path))
        by_name = fs_manage("disk_usage", str(tmp_path))
        assert by_word.get("success") == by_name.get("success")
        assert by_word.get("op") == by_name.get("op")

    def test_the_description_still_says_disk_usage(self):
        assert "usage" in described_words()


class TestTheCanonicalNamesStillWork:
    @pytest.mark.parametrize("action", CANONICAL)
    def test_they_are_accepted(self, action: str, tmp_path: Path):
        assert "Unknown action" not in str(fs_manage(action, str(tmp_path)).get("error", ""))


class TestSomethingThatIsNotAnActionIsStillRejected:
    @pytest.mark.parametrize("action", ["chmod777", "", "delete"])
    def test_it_fails(self, action: str, tmp_path: Path):
        r = fs_manage(action, str(tmp_path))
        assert r.get("success") is False
        assert "Unknown action" in str(r.get("error", "")), r

    def test_the_hint_names_the_canonical_actions(self, tmp_path: Path):
        hint = str(fs_manage("delete", str(tmp_path)).get("hint", ""))
        assert all(name in hint for name in CANONICAL), hint
