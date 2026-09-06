"""`fs_archive` wrote ZIP bytes into a file named `.tar.gz`.

    Create or extract zip/tar.gz. path=archive, target=what goes in it.

tar.gz is advertised there, so naming the archive is a reasonable way to ask
for one. It was not enough: `format` defaulted to `"zip"` and the archive's own
extension was never consulted, so

    fs_archive(action="create", path="out.tar.gz", target="file.txt")

returned `success: true` and `format: "zip"` next to a file that `file(1)`
identifies as `Zip archive data`. The response was honest; the filename was
not. That is the wrong way round -- the extension is what the next tool reads,
and `tar -xzf out.tar.gz` fails on it.

Two halves, and both matter:

* An unstated format is now read off the extension, so the obvious call works.
* An explicit format that contradicts the extension is refused, because that is
  the original bug requested on purpose and there is no good reason to write
  it.

`.zip` and no extension at all still behave exactly as before.
"""

from __future__ import annotations

import pathlib
import sys
import tarfile
import zipfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "servers" / "fs_basic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _basic_archive import _format_from_path, _fs_archive  # noqa: E402


@pytest.fixture()
def payload(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello\n", encoding="utf-8")
    return f


def _create(archive, target):
    return _fs_archive("create", str(archive), str(target), "", False)


class TestTheExtensionDecidesWhenNobodySaysOtherwise:
    def test_a_tar_gz_name_produces_a_real_tarball(self, tmp_path, payload):
        out = tmp_path / "out.tar.gz"
        result = _create(out, payload)
        assert result["success"] is True, result.get("error")
        assert result["format"] == "tar.gz"
        assert tarfile.is_tarfile(out), "named .tar.gz and is not a tarball"
        assert not zipfile.is_zipfile(out)

    def test_and_tar_can_actually_open_it(self, tmp_path, payload):
        """The check the caller's next command performs."""
        out = tmp_path / "out.tar.gz"
        _create(out, payload)
        with tarfile.open(out, "r:gz") as tf:
            assert any(m.name.endswith("file.txt") for m in tf.getmembers())

    @pytest.mark.parametrize("name", ["out.tgz", "out.tar"])
    def test_the_other_tar_spellings_too(self, tmp_path, payload, name):
        out = tmp_path / name
        result = _create(out, payload)
        assert result["format"] == "tar.gz"
        assert tarfile.is_tarfile(out)

    def test_a_zip_name_still_produces_a_zip(self, tmp_path, payload):
        out = tmp_path / "out.zip"
        result = _create(out, payload)
        assert result["success"] is True, result.get("error")
        assert result["format"] == "zip"
        assert zipfile.is_zipfile(out)

    def test_no_extension_still_defaults_to_zip(self, tmp_path, payload):
        """Unchanged behaviour: nothing in the name asks for anything."""
        out = tmp_path / "out"
        result = _create(out, payload)
        assert result["success"] is True, result.get("error")
        assert result["format"] == "zip"


class TestAContradictionIsRefusedRatherThanWritten:
    def test_zip_into_a_tar_gz_name_is_refused(self, tmp_path, payload):
        out = tmp_path / "out.tar.gz"
        result = _fs_archive("create", str(out), str(payload), "zip", False)
        assert result["success"] is False
        assert "contradicts" in result["error"]

    def test_and_nothing_is_written(self, tmp_path, payload):
        out = tmp_path / "out.tar.gz"
        _fs_archive("create", str(out), str(payload), "zip", False)
        assert not out.exists()

    def test_the_hint_offers_both_ways_out(self, tmp_path, payload):
        out = tmp_path / "out.tar.gz"
        hint = _fs_archive("create", str(out), str(payload), "zip", False)["hint"]
        assert "format" in hint and "rename" in hint

    def test_tar_gz_into_a_zip_name_is_refused_the_same_way(self, tmp_path, payload):
        out = tmp_path / "out.zip"
        result = _fs_archive("create", str(out), str(payload), "tar.gz", False)
        assert result["success"] is False
        assert "contradicts" in result["error"]

    def test_an_explicit_format_matching_the_name_is_fine(self, tmp_path, payload):
        out = tmp_path / "out.tar.gz"
        result = _fs_archive("create", str(out), str(payload), "tar.gz", False)
        assert result["success"] is True, result.get("error")
        assert tarfile.is_tarfile(out)

    def test_an_explicit_format_on_a_nameless_archive_is_obeyed(self, tmp_path, payload):
        out = tmp_path / "bundle"
        result = _fs_archive("create", str(out), str(payload), "tar.gz", False)
        assert result["success"] is True, result.get("error")
        assert tarfile.is_tarfile(out)


class TestTheHelper:
    @pytest.mark.parametrize(
        "name, want",
        [
            ("a.tar.gz", "tar.gz"),
            ("a.TAR.GZ", "tar.gz"),
            ("a.tgz", "tar.gz"),
            ("a.tar", "tar.gz"),
            ("a.zip", "zip"),
            ("a.ZIP", "zip"),
            ("a.bin", ""),
            ("a", ""),
            ("/long/path/to/a.tar.gz", "tar.gz"),
        ],
    )
    def test_it_reads_the_double_extension(self, name, want):
        """Path.suffix alone says '.gz', which is why this is not Path.suffix."""
        assert _format_from_path(name) == want


class TestAnUnknownFormatIsStillRefused:
    def test_nonsense_format(self, tmp_path, payload):
        result = _fs_archive("create", str(tmp_path / "out"), str(payload), "rar", False)
        assert result["success"] is False
        assert "rar" in result["error"]
