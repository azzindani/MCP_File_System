"""An unknown fs_write op must say which ops exist.

A coverage sweep called fs_write with `{"op": "write", ...}` -- the obvious
guess, since the tool's own docstring is "Write, edit, move, copy files" and its
schema declares `ops` as a bare `list[dict]` with additionalProperties, so
nothing in the MCP handshake names a single valid op. The reply was:

    error: "Op 0: unknown op 'write'"
    hint:  "Fix the op array and retry."

Neither field names `write_file`, and there is no list_ops tool on this server
to ask. The caller is told it is wrong and given no way to become right, which
is the exact shape CLAUDE.md forbids ("Never 'Invalid input.' or 'Try again.'";
a hint must name a specific tool or fix).

MCP_Data_Analyst's validator for the same op-array pattern has always appended
"Valid ops: ..." to this error. This pins the two repos to that one behaviour so
they cannot drift apart again.
"""

from __future__ import annotations

import pytest

from servers.fs_basic.engine import fs_write
from shared.patch_validator import ALLOWED_OPS, validate_ops


class TestTheErrorNamesTheVocabulary:
    def test_unknown_op_lists_the_valid_ops(self):
        errors = validate_ops([{"op": "write", "path": "/tmp/x", "content": "hi"}])
        assert len(errors) == 1
        assert "unknown op 'write'" in errors[0]
        assert "Valid ops:" in errors[0], "the caller has no other way to discover the vocabulary"

    def test_the_op_it_actually_wanted_is_in_the_list(self):
        """'write' is a wrong guess for 'write_file' -- name the real one."""
        errors = validate_ops([{"op": "write", "path": "/tmp/x", "content": "hi"}])
        assert "write_file" in errors[0]

    @pytest.mark.parametrize("op", sorted(ALLOWED_OPS))
    def test_every_allowed_op_is_named(self, op: str):
        errors = validate_ops([{"op": "nope"}])
        assert op in errors[0]


class TestTheHintIsActionable:
    def _reply(self) -> dict:
        return fs_write(ops=[{"op": "write", "path": "/tmp/x", "content": "hi"}])

    def test_it_fails(self):
        assert self._reply()["success"] is False

    def test_the_hint_names_the_valid_ops(self):
        assert "Valid ops:" in self._reply()["hint"]

    def test_the_hint_is_not_the_forbidden_shape(self):
        """The old hint, "Fix the op array and retry.", told the caller nothing
        it did not already know."""
        hint = self._reply()["hint"]
        assert "and retry" not in hint
        assert hint.strip() not in {"Invalid input.", "Try again."}

    def test_write_file_is_reachable_from_the_hint_alone(self):
        assert "write_file" in self._reply()["hint"]


class TestEveryBadOpIsReportedAtOnce:
    """It used to return errors[0] only, so a three-op batch with three
    mistakes cost three round trips to discover."""

    def test_all_errors_come_back_together(self):
        reply = fs_write(
            ops=[
                {"op": "write", "path": "/tmp/a", "content": "x"},
                {"op": "nonsense", "path": "/tmp/b"},
                {"op": "create_dir"},
            ]
        )
        assert reply["success"] is False
        assert "Op 0" in reply["error"]
        assert "Op 1" in reply["error"]
        assert "Op 2" in reply["error"]

    def test_the_missing_field_error_still_names_the_field(self):
        reply = fs_write(ops=[{"op": "create_dir"}])
        assert "'path'" in reply["error"]
