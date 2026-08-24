"""fs_read returned lines the file does not contain.

`Path.read_text()` defaults to universal-newline mode, which rewrites every
"\\r\\n" to "\\n" as it reads. So on a CRLF file -- which is most CSVs that have
ever been near Excel, including the reference dataset -- fs_read mode=content
returned lines with endings the file does not have, reported success, and said
nothing about the substitution.

The check that found it: md5 the bytes on disk, md5 the bytes fs_read returned.
They matched only after `tr -d '\\r'` on the disk side.

That matters beyond tidiness. A caller reading a file to hash it, to compare two
copies, or to write the lines somewhere else is asking a question about bytes,
and was getting an answer about different bytes. A round-trip through fs_read
silently converted a CRLF file to LF.

Reading with newline="" turns the translation off. The endings are now the
file's own, and `line_ending` says which they are so a caller can tell whether
a "\\r" it is looking at came from the file or from itself.
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
def crlf(tmp_path) -> Path:
    p = tmp_path / "excel.csv"
    p.write_bytes(b"a,b\r\n1,2\r\n3,4\r\n")
    return p


@pytest.fixture
def lf(tmp_path) -> Path:
    p = tmp_path / "unix.csv"
    p.write_bytes(b"a,b\n1,2\n3,4\n")
    return p


class TestTheBytesComeBackUnchanged:
    def test_crlf_survives_the_read(self, crlf):
        r = engine.fs_read(path=str(crlf), mode="content")
        assert r["success"] is True, r.get("error")
        assert "".join(r["lines"]).encode() == crlf.read_bytes()

    def test_lf_survives_the_read(self, lf):
        r = engine.fs_read(path=str(lf), mode="content")
        assert "".join(r["lines"]).encode() == lf.read_bytes()

    def test_a_crlf_file_does_not_come_back_as_lf(self, crlf):
        r = engine.fs_read(path=str(crlf), mode="content")
        joined = "".join(r["lines"])
        assert "\r\n" in joined, "the CR was stripped on the way out"

    def test_a_round_trip_does_not_convert_the_file(self, crlf, tmp_path):
        r = engine.fs_read(path=str(crlf), mode="content")
        copy = tmp_path / "written_back.csv"
        copy.write_bytes("".join(r["lines"]).encode())
        assert copy.read_bytes() == crlf.read_bytes()


class TestItSaysWhichEndingsTheseAre:
    def test_crlf_is_named(self, crlf):
        assert engine.fs_read(path=str(crlf), mode="content")["line_ending"] == "crlf"

    def test_lf_is_named(self, lf):
        assert engine.fs_read(path=str(lf), mode="content")["line_ending"] == "lf"

    def test_a_mixed_file_is_not_rounded_to_one_kind(self, tmp_path):
        p = tmp_path / "half_converted.txt"
        p.write_bytes(b"one\r\ntwo\nthree\r\n")
        assert engine.fs_read(path=str(p), mode="content")["line_ending"] == "mixed"

    def test_a_file_with_no_newline_at_all(self, tmp_path):
        p = tmp_path / "bare.txt"
        p.write_bytes(b"no ending here")
        assert engine.fs_read(path=str(p), mode="content")["line_ending"] == "none"


class TestTheRestOfContentModeIsUnchanged:
    def test_line_count_is_right_for_crlf(self, crlf):
        r = engine.fs_read(path=str(crlf), mode="content")
        assert r["total_lines"] == 3, r["total_lines"]

    def test_a_range_still_slices_by_line(self, crlf):
        r = engine.fs_read(path=str(crlf), mode="content", start_line=1, end_line=2)
        assert r["lines"] == ["1,2\r\n"], r["lines"]

    def test_an_empty_file_is_still_readable(self, tmp_path):
        p = tmp_path / "empty.txt"
        p.write_bytes(b"")
        r = engine.fs_read(path=str(p), mode="content")
        assert r["success"] is True
        assert r["total_lines"] == 0
        assert r["lines"] == []

    def test_token_estimate_is_present(self, crlf):
        r = engine.fs_read(path=str(crlf), mode="content")
        assert isinstance(r["token_estimate"], int) and r["token_estimate"] > 0
