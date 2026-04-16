"""Write fs_basic MCP server entry to LM Studio and Claude Desktop configs.

Run from the repo root: uv run python install/mcp_config_writer.py
"""
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Server entry definitions
# ---------------------------------------------------------------------------

_INSTALL_DIR_POSIX = "$HOME/.mcp_servers/mcp-filesystem"
_INSTALL_DIR_WIN = "%USERPROFILE%\\.mcp_servers\\mcp-filesystem"


def _posix_entry(constrained: bool = False) -> dict:
    return {
        "command": "bash",
        "args": [
            "-c",
            (
                'd="$HOME/.mcp_servers/mcp-filesystem"; '
                'if [ ! -d "$d/.git" ]; then rm -rf "$d"; '
                'git clone https://github.com/azzindani/mcp_file_system.git "$d" --quiet; '
                'else cd "$d" && git fetch origin --quiet '
                '&& git reset --hard FETCH_HEAD --quiet; fi; '
                'cd "$d/servers/fs_basic"; uv sync --quiet; uv run python server.py'
            ),
        ],
        "env": {"MCP_CONSTRAINED_MODE": "1" if constrained else "0"},
        "timeout": 600000,
    }


def _windows_entry(constrained: bool = False) -> dict:
    return {
        "command": "powershell",
        "args": [
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$d = Join-Path $env:USERPROFILE '.mcp_servers\\mcp-filesystem'; "
                "$g = Join-Path $d '.git'; "
                "if (!(Test-Path $g)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d }; "
                "git clone https://github.com/azzindani/mcp_file_system.git $d --quiet } "
                "else { Set-Location $d; git fetch origin --quiet; "
                "git reset --hard FETCH_HEAD --quiet }; "
                "Set-Location (Join-Path $d 'servers\\fs_basic'); "
                "uv sync --quiet; uv run python server.py"
            ),
        ],
        "env": {"MCP_CONSTRAINED_MODE": "1" if constrained else "0"},
        "timeout": 600000,
    }


# ---------------------------------------------------------------------------
# Config file locations
# ---------------------------------------------------------------------------

def _lm_studio_config_path() -> Path | None:
    platform = sys.platform
    if platform == "win32":
        base = Path(os.environ.get("APPDATA", "~")).expanduser()
        return base / "LM Studio" / "mcp_config.json"
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "LM Studio" / "mcp_config.json"
    return Path.home() / ".lmstudio" / "mcp_config.json"


def _claude_desktop_config_path() -> Path | None:
    platform = sys.platform
    if platform == "win32":
        base = Path(os.environ.get("APPDATA", "~")).expanduser()
        return base / "Claude" / "claude_desktop_config.json"
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "claude" / "claude_desktop_config.json"


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _write_config(config_path: Path, server_entry: dict, label: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    existing.setdefault("mcpServers", {})
    existing["mcpServers"]["fs_basic"] = server_entry

    config_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  Written: {config_path}")


def main() -> None:
    platform = sys.platform
    constrained = os.environ.get("MCP_CONSTRAINED_MODE", "0") == "1"

    if platform == "win32":
        entry = _windows_entry(constrained)
    else:
        entry = _posix_entry(constrained)

    targets = [
        (_lm_studio_config_path(), "LM Studio"),
        (_claude_desktop_config_path(), "Claude Desktop"),
    ]

    print("Writing MCP config entries for fs_basic...")
    for path, label in targets:
        if path is None:
            continue
        try:
            _write_config(path, entry, label)
        except Exception as e:
            print(f"  WARN: Could not write {label} config: {e}", file=sys.stderr)

    print("\nDone. Example JSON entry:")
    print(json.dumps({"mcpServers": {"fs_basic": entry}}, indent=2))


if __name__ == "__main__":
    main()
