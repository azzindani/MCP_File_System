"""Pytest configuration and shared fixtures for fs_basic tests."""

import os
import shutil
import sys
from pathlib import Path

import pytest

# Ensure project root and fs_basic dir are in path for imports
_repo_root = Path(__file__).parent.parent
_fs_basic_dir = _repo_root / "servers" / "fs_basic"
for _p in (str(_repo_root), str(_fs_basic_dir)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Run tests with constrained mode OFF unless a test overrides it
os.environ.setdefault("MCP_CONSTRAINED_MODE", "0")

_SIMPLE_SRC = _repo_root / "tests" / "fixtures" / "simple"
_MESSY_SRC = _repo_root / "tests" / "fixtures" / "messy"


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temporary home directory — isolates resolve_path() checks.

    All tests that touch file paths must use this fixture so that
    resolve_path() accepts the test paths (it rejects paths outside home).
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def work_dir(tmp_home: Path) -> Path:
    """A writable work directory inside tmp_home."""
    d = tmp_home / "work"
    d.mkdir()
    return d


@pytest.fixture
def simple_dir(work_dir: Path) -> Path:
    """Flat directory with 10 clean text files, inside tmp_home."""
    dst = work_dir / "simple"
    shutil.copytree(_SIMPLE_SRC, dst)
    return dst


@pytest.fixture
def messy_dir(work_dir: Path) -> Path:
    """Nested dir with unicode names and a symlink, inside tmp_home."""
    dst = work_dir / "messy"
    shutil.copytree(_MESSY_SRC, dst, symlinks=True)
    # Ensure link_to_readme.txt is a working symlink to readme.txt in dst.
    link = dst / "link_to_readme.txt"
    readme = dst / "readme.txt"
    if link.is_symlink() or link.exists():
        link.unlink()
    try:
        link.symlink_to(readme)
    except OSError:
        pass  # symlinks require elevated privileges on Windows without Developer Mode
    return dst


@pytest.fixture(scope="session")
def large_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """5 000+ files — created fresh each session in /tmp (not home-checked).

    Tests using large_dir must NOT call resolve_path; use it only with
    engine functions that receive a pre-resolved work_dir copy, or skip
    the home-check by patching home via a separate tmp_home fixture.
    """
    base = tmp_path_factory.mktemp("large")
    for i in range(5000):
        subdir = base / f"dir_{i // 100:02d}"
        subdir.mkdir(exist_ok=True)
        (subdir / f"file_{i:04d}.txt").write_text(f"file {i}\ncontent line\n", encoding="utf-8")
    return base


@pytest.fixture
def sample_file(work_dir: Path) -> Path:
    """A sample text file for read/write tests."""
    p = work_dir / "sample.txt"
    p.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n", encoding="utf-8")
    return p
