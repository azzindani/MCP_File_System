"""fs_write dropped every op field it did not require, without a word.

`shared/strict_args.py` makes each tool refuse an argument it does not declare,
but it can only see the tool's own parameters, and `fs_write` declares two:
`ops` and `dry_run`. Everything that varies a write lives inside the op dicts,
one level below where that guard can reach. `validate_ops` checked only the
*required* fields, so any other key was silently discarded -- and the optional
ones are undiscoverable to begin with, because the schema for `ops` is
`list[dict]`.

Round 11 measured both halves against the live server. Renaming one flag:

    replace_text find="X+" replace="-" use_regex=True
        -> success: false, "Pattern not found in t.txt"
    replace_text find="X+" replace="-" regex=True
        -> success: true, replacements: 2

`use_regex` is the name of the handler's own local variable. Dropping it made
"X+" a literal search, and the caller was told the pattern is not in the file --
which is false, and points it at a file that was never the problem.

Renaming one other:

    write_file content="aGVsbG8=" encoding="base64"
        -> success: true, 8 bytes on disk: 61 47 56 73 62 47 38 3d   ("aGVsbG8=")
    write_file content="aGVsbG8=" content_encoding="base64"
        -> success: true, 5 bytes on disk: 68 65 6c 6c 6f            ("hello")

Both said success. The first wrote the base64 text into the file instead of the
bytes it stands for, and nothing in the response distinguishes them.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402

from shared import patch_validator as pv  # noqa: E402


def write(op: str, **kw) -> dict:
    outer = engine.fs_write([dict(op=op, **kw)])
    return outer.get("results", [outer])[0] if isinstance(outer, dict) else outer


class TestTheDroppedFlagIsRefused:
    def test_a_misspelled_regex_flag_does_not_report_a_missing_pattern(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_bytes(b"aXXbXXc\n")
        r = write("replace_text", path=str(f), find="X+", replace="-", use_regex=True)
        assert r["success"] is False
        assert "Pattern not found" not in r["error"], r["error"]
        assert "use_regex" in r["error"], r["error"]

    def test_the_refusal_names_the_field_that_was_meant(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_bytes(b"aXXbXXc\n")
        r = write("replace_text", path=str(f), find="X+", replace="-", use_regex=True)
        assert "regex" in r["error"], r["error"]

    def test_the_file_is_left_alone(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_bytes(b"aXXbXXc\n")
        write("replace_text", path=str(f), find="X+", replace="-", use_regex=True)
        assert f.read_bytes() == b"aXXbXXc\n"

    def test_the_correct_spelling_still_works(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_bytes(b"aXXbXXc\n")
        r = write("replace_text", path=str(f), find="X+", replace="-", regex=True)
        assert r["success"] is True, r.get("error")
        assert f.read_bytes() == b"a-b-c\n"


class TestTheDroppedEncodingIsRefused:
    def test_a_misspelled_encoding_is_not_written_as_text(self, tmp_path):
        f = tmp_path / "b.bin"
        r = write("write_file", path=str(f), content="aGVsbG8=", encoding="base64")
        assert r["success"] is False, "the base64 text was written as literal characters"
        assert not f.exists(), f.read_bytes()

    def test_the_refusal_names_content_encoding(self, tmp_path):
        f = tmp_path / "b.bin"
        r = write("write_file", path=str(f), content="aGVsbG8=", encoding="base64")
        assert "content_encoding" in r["error"], r["error"]

    def test_the_correct_spelling_still_decodes(self, tmp_path):
        f = tmp_path / "c.bin"
        r = write(
            "write_file",
            path=str(f),
            content=base64.b64encode(b"hello").decode(),
            content_encoding="base64",
        )
        assert r["success"] is True, r.get("error")
        assert f.read_bytes() == b"hello"


class TestEveryOptionalFieldIsAccepted:
    """A field a handler reads must be a field the validator allows.

    This is the check that keeps the two lists together: the failure mode of
    adding an optional field and forgetting _OPTIONAL is that the field becomes
    a refusal, and this test is where that shows up rather than in production.
    """

    @pytest.mark.parametrize(
        ("op", "field"),
        [(op, f) for op, fields in pv._OPTIONAL.items() for f in fields],
    )
    def test_the_handler_reads_it(self, op, field):
        import ast

        src = (ROOT / "servers" / "fs_basic" / "_basic_write.py").read_text(encoding="utf-8")
        fn = next(
            n
            for n in ast.parse(src).body
            if isinstance(n, ast.FunctionDef) and n.name == f"_op_{op}"
        )
        read = {
            n.args[0].value
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "op_dict"
            and n.args
            and isinstance(n.args[0], ast.Constant)
        }
        assert field in read, f"_OPTIONAL lists {op}.{field} but the handler never reads it"

    def test_no_handler_reads_a_field_the_validator_rejects(self):
        import ast

        src = (ROOT / "servers" / "fs_basic" / "_basic_write.py").read_text(encoding="utf-8")
        for fn in ast.parse(src).body:
            if not (isinstance(fn, ast.FunctionDef) and fn.name.startswith("_op_")):
                continue
            op = fn.name[len("_op_") :]
            if op not in pv.ALLOWED_OPS:
                continue
            known = set(pv.known_fields(op))
            for n in ast.walk(fn):
                key = None
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "get"
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "op_dict"
                    and n.args
                    and isinstance(n.args[0], ast.Constant)
                ):
                    key = n.args[0].value
                elif (
                    isinstance(n, ast.Subscript)
                    and isinstance(n.value, ast.Name)
                    and n.value.id == "op_dict"
                    and isinstance(n.slice, ast.Constant)
                ):
                    key = n.slice.value
                if key is not None:
                    assert key in known, f"_op_{op} reads {key!r}, which validate_ops refuses"


class TestARefusalIsNotAPartialWrite:
    def test_a_bad_field_late_in_the_batch_stops_the_whole_batch(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        r = engine.fs_write(
            [
                {"op": "write_file", "path": str(a), "content": "one\n"},
                {"op": "write_file", "path": str(b), "content": "two\n", "encoding": "base64"},
            ]
        )
        assert r["success"] is False, r
        assert not a.exists(), "the first op ran even though the batch was invalid"
        assert not b.exists()
