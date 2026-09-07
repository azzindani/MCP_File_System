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


class TestAnUnknownFormatIsNotAContradiction:
    """The test above passed while the defect was live, because it used the one
    path shape that hides it: `out`, with no extension, so nothing was inferred
    and the contradiction branch could not fire.

    Give the archive a name and the order of the two checks starts to matter.
    Round 29 sent `format='zzqq_no_such_choice'` at a path called `z2.zip` and
    got back

        format 'zzqq_no_such_choice' contradicts the extension of 'z2.zip'.
        That writes zzqq_no_such_choice bytes into a name meaning zip.

    -- a refusal describing a value that is not a format at all as a real one
    competing with the extension, and offering the caller "rename the archive"
    as a way out. Renaming it would not have helped. Validity is the earlier
    question, and its refusal is the one that names the two legal values.
    """

    @pytest.mark.parametrize("name", ["out.zip", "out.tar.gz", "out.tgz"])
    def test_an_unknown_format_is_called_unknown(self, tmp_path, payload, name):
        result = _fs_archive(
            "create", str(tmp_path / name), str(payload), "zzqq_no_such_choice", False
        )
        assert result["success"] is False
        assert "Unknown format" in result["error"], result["error"]
        assert "contradicts" not in result["error"], (
            "an unrecognised value was reported as a competing format"
        )

    @pytest.mark.parametrize("name", ["out.zip", "out.tar.gz"])
    def test_and_the_refusal_names_both_legal_values(self, tmp_path, payload, name):
        blob = " ".join(
            str(_fs_archive("create", str(tmp_path / name), str(payload), "rar", False).get(k, ""))
            for k in ("error", "hint")
        )
        assert "zip" in blob and "tar.gz" in blob, blob

    def test_and_nothing_is_written(self, tmp_path, payload):
        out = tmp_path / "out.zip"
        _fs_archive("create", str(out), str(payload), "rar", False)
        assert not out.exists()

    def test_a_real_format_that_contradicts_still_says_contradicts(self, tmp_path, payload):
        """The reorder must not swallow the case it was put in front of."""
        result = _fs_archive("create", str(tmp_path / "out.tar.gz"), str(payload), "zip", False)
        assert result["success"] is False
        assert "contradicts" in result["error"]
        assert "Unknown format" not in result["error"]

    @pytest.mark.parametrize("action", ["extract", "list"])
    def test_the_other_actions_refuse_it_too(self, tmp_path, payload, action):
        """`format` is inferred for every action, so every action can be handed
        a bad one."""
        archive = tmp_path / "real.zip"
        _create(archive, payload)
        result = _fs_archive(
            action, str(archive), str(tmp_path / "dest"), "zzqq_no_such_choice", False
        )
        assert result["success"] is False
        assert "Unknown format" in result["error"], result["error"]
