"""Tests for shared/exchange.py and the fs_write `download` op.

Every env var the exchange layer reads is unset by default, so each test
establishes the exact environment it needs via monkeypatch. The one HTTP
server used here binds to 127.0.0.1 — no external network.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import engine  # noqa: E402 — conftest puts fs_basic on sys.path
import pytest

from shared import exchange
from shared.exchange import (
    assert_fetchable,
    attach_public_url,
    fetch_url,
    get_inbox_dir,
    get_output_dir,
    is_url,
    public_url_for,
    url_fetch_enabled,
)
from shared.file_utils import get_default_output_dir
from shared.patch_validator import validate_ops

CSV_BODY = b"a,b\n1,2\n3,4\n"


class _Handler(BaseHTTPRequestHandler):
    """Serves a CSV at /data.csv, a no-extension export, and a big payload."""

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        if self.path.startswith("/data.csv") or self.path.startswith("/export"):
            body, ctype = CSV_BODY, "text/csv"
        elif self.path.startswith("/big"):
            body, ctype = b"x" * (3 * 1024 * 1024), "application/octet-stream"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Silence the default stderr access log."""


class _QuietServer(ThreadingHTTPServer):
    """Silences the traceback the size-cap test provokes by hanging up early."""

    def handle_error(self, request: object, client_address: object) -> None:
        """Expected mid-body disconnect — nothing to report."""


@pytest.fixture(scope="module")
def http_url():
    server = _QuietServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def clear_fetch_cache():
    exchange._fetch_cache.clear()
    yield
    exchange._fetch_cache.clear()


@pytest.fixture
def remote_mode(monkeypatch, tmp_home):
    """Server configured the way a container deployment configures it."""
    shared_dir = tmp_home / "shared"
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(shared_dir))
    monkeypatch.setenv("MCP_PUBLIC_BASE_URL", "https://files.example.test/data")
    monkeypatch.setenv("MCP_FETCH_URLS", "1")
    monkeypatch.setenv("MCP_FETCH_ALLOW_PRIVATE", "1")
    shared_dir.mkdir(parents=True, exist_ok=True)
    return shared_dir


# ---------------------------------------------------------------------------
# output directory
# ---------------------------------------------------------------------------


def test_output_dir_defaults_to_downloads(monkeypatch, tmp_home):
    monkeypatch.delenv("MCP_OUTPUT_DIR", raising=False)
    assert get_output_dir() == tmp_home / "Downloads"


def test_output_dir_honours_env_and_creates_it(monkeypatch, tmp_path):
    target = tmp_path / "not-yet-there"
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(target))
    assert get_output_dir() == target
    assert target.is_dir()


def test_default_output_dir_prefers_env_over_input_parent(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path / "shared"))
    assert get_default_output_dir(str(tmp_path / "elsewhere" / "in.csv")) == tmp_path / "shared"


def test_default_output_dir_uses_input_parent_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_OUTPUT_DIR", raising=False)
    assert get_default_output_dir(str(tmp_path / "in.csv")) == tmp_path


# ---------------------------------------------------------------------------
# public URLs
# ---------------------------------------------------------------------------


def test_public_url_for_file_under_output_dir(remote_mode):
    target = remote_mode / "notes.md"
    target.write_text("hi")
    assert public_url_for(target) == "https://files.example.test/data/notes.md"


def test_public_url_encodes_and_keeps_subdirectories(remote_mode):
    nested = remote_mode / "inbox" / "my file.csv"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(CSV_BODY)
    assert public_url_for(nested) == "https://files.example.test/data/inbox/my%20file.csv"


def test_public_url_empty_for_file_outside_output_dir(remote_mode, tmp_home):
    outside = tmp_home / "private.md"
    outside.write_text("x")
    assert public_url_for(outside) == ""


def test_public_url_empty_when_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path))
    assert public_url_for(tmp_path / "x.md") == ""


def test_attach_public_url_only_sets_key_when_resolvable(remote_mode, tmp_home):
    inside = remote_mode / "a.md"
    inside.write_text("x")
    assert attach_public_url({"success": True}, inside)["public_url"].endswith("/a.md")
    assert "public_url" not in attach_public_url({"success": True}, tmp_home / "b.md")


# ---------------------------------------------------------------------------
# URL detection and the fetch gate
# ---------------------------------------------------------------------------


def test_is_url_only_matches_http_schemes():
    assert is_url("https://example.test/a.csv")
    assert is_url("  HTTP://example.test/a.csv ")
    assert not is_url("/home/app/a.csv")
    assert not is_url("file:///etc/passwd")


def test_fetch_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MCP_FETCH_URLS", raising=False)
    assert not url_fetch_enabled()
    with pytest.raises(ValueError, match="MCP_FETCH_URLS=1"):
        fetch_url("https://example.test/a.csv")


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


def test_assert_fetchable_rejects_non_http_scheme(monkeypatch):
    monkeypatch.delenv("MCP_FETCH_ALLOW_PRIVATE", raising=False)
    with pytest.raises(ValueError, match="Only http and https"):
        assert_fetchable("file:///etc/passwd")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8801/health",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
        "http://[::1]:8000/x",
    ],
)
def test_assert_fetchable_rejects_non_public_addresses(monkeypatch, url):
    monkeypatch.delenv("MCP_FETCH_ALLOW_PRIVATE", raising=False)
    with pytest.raises(ValueError, match="non-public address"):
        assert_fetchable(url)


def test_assert_fetchable_allows_private_when_opted_in(monkeypatch):
    monkeypatch.setenv("MCP_FETCH_ALLOW_PRIVATE", "1")
    assert_fetchable("http://127.0.0.1:8801/health")


def test_assert_fetchable_reports_unresolvable_host(monkeypatch):
    monkeypatch.delenv("MCP_FETCH_ALLOW_PRIVATE", raising=False)
    with pytest.raises(ValueError, match="Cannot resolve host"):
        assert_fetchable("https://no-such-host.invalid/a.csv")


# ---------------------------------------------------------------------------
# real downloads
# ---------------------------------------------------------------------------


def test_fetch_url_downloads_real_bytes_into_inbox(remote_mode, http_url):
    path = fetch_url(f"{http_url}/data.csv")
    assert path.read_bytes() == CSV_BODY
    assert path == get_inbox_dir() / "data.csv"


def test_fetch_url_adds_suffix_from_content_type(remote_mode, http_url):
    assert fetch_url(f"{http_url}/export?id=7").suffix == ".csv"


def test_fetch_url_enforces_the_size_cap(remote_mode, http_url, monkeypatch):
    monkeypatch.setenv("MCP_MAX_FETCH_MB", "1")
    with pytest.raises(ValueError, match="larger than the 1 MB limit"):
        fetch_url(f"{http_url}/big")


# ---------------------------------------------------------------------------
# fs_write download op
# ---------------------------------------------------------------------------


class TestFsWriteDownloadOp:
    def test_validator_accepts_download_op(self):
        assert (
            validate_ops([{"op": "download", "url": "https://x.test/a.csv", "path": "/tmp/a.csv"}])
            == []
        )

    def test_validator_reports_missing_url(self):
        errors = validate_ops([{"op": "download", "path": "/tmp/a.csv"}])
        assert any("missing required field 'url'" in e for e in errors)

    def test_downloads_real_bytes_to_the_requested_path(self, remote_mode, http_url):
        target = remote_mode / "sales.csv"
        r = engine.fs_write(
            [{"op": "download", "url": f"{http_url}/data.csv", "path": str(target)}]
        )
        assert r["success"] is True
        assert target.read_bytes() == CSV_BODY
        result = r["results"][0]
        assert result["bytes"] == len(CSV_BODY)
        assert result["public_url"] == "https://files.example.test/data/sales.csv"

    def test_dry_run_touches_nothing(self, remote_mode, http_url):
        target = remote_mode / "sales.csv"
        r = engine.fs_write(
            [{"op": "download", "url": f"{http_url}/data.csv", "path": str(target)}],
            dry_run=True,
        )
        assert r["success"] is True
        assert r["results"][0]["would_change"] is True
        assert not target.exists()

    def test_overwrite_snapshots_the_previous_file(self, remote_mode, http_url):
        target = remote_mode / "sales.csv"
        target.write_text("old contents")
        r = engine.fs_write(
            [{"op": "download", "url": f"{http_url}/data.csv", "path": str(target)}]
        )
        assert r["success"] is True
        assert r["results"][0]["backup"]
        assert Path(r["results"][0]["backup"]).exists()
        assert target.read_bytes() == CSV_BODY

    def test_rejects_a_non_url(self, remote_mode, tmp_home):
        r = engine.fs_write(
            [{"op": "download", "url": "/etc/passwd", "path": str(tmp_home / "x.csv")}]
        )
        assert r["success"] is False
        assert "http" in r["error"]

    def test_refuses_private_hosts_by_default(self, monkeypatch, tmp_home, http_url):
        monkeypatch.setenv("MCP_FETCH_URLS", "1")
        monkeypatch.delenv("MCP_FETCH_ALLOW_PRIVATE", raising=False)
        r = engine.fs_write(
            [{"op": "download", "url": f"{http_url}/data.csv", "path": str(tmp_home / "x.csv")}]
        )
        assert r["success"] is False
        assert "non-public address" in r["error"]

    def test_refuses_when_fetching_is_disabled(self, monkeypatch, tmp_home):
        monkeypatch.delenv("MCP_FETCH_URLS", raising=False)
        r = engine.fs_write(
            [
                {
                    "op": "download",
                    "url": "https://example.test/a.csv",
                    "path": str(tmp_home / "x.csv"),
                }
            ]
        )
        assert r["success"] is False
        assert "MCP_FETCH_URLS=1" in r["error"]


class TestPublicUrlOnWrites:
    def test_write_file_result_carries_public_url(self, remote_mode):
        target = remote_mode / "notes.md"
        r = engine.fs_write([{"op": "write_file", "path": str(target), "content": "hello"}])
        assert r["results"][0]["public_url"] == "https://files.example.test/data/notes.md"

    def test_no_public_url_outside_the_shared_dir(self, remote_mode, tmp_home):
        target = tmp_home / "private.md"
        r = engine.fs_write([{"op": "write_file", "path": str(target), "content": "hello"}])
        assert "public_url" not in r["results"][0]

    def test_no_public_url_when_unconfigured(self, monkeypatch, tmp_home):
        monkeypatch.delenv("MCP_PUBLIC_BASE_URL", raising=False)
        monkeypatch.delenv("MCP_OUTPUT_DIR", raising=False)
        target = tmp_home / "notes.md"
        r = engine.fs_write([{"op": "write_file", "path": str(target), "content": "hello"}])
        assert "public_url" not in r["results"][0]
