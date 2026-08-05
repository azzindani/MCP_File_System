#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# mcp-filesystem — remote bootstrap (Google Colab / any fresh Linux VM, no
# Docker needed). Installs uv, clones/updates this repo, syncs Python deps.
#
# Companion to remote_launch.sh. Same idea as azzindani/Folio's install.sh,
# adapted for a uv/Python project instead of npm.
#
# Usage:
#   REPO_DIR=/content/MCP_File_System ./remote_install.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_DIR="${REPO_DIR:-/content/MCP_File_System}"
REPO_URL="${REPO_URL:-https://github.com/azzindani/MCP_File_System.git}"

if ! command -v uv &>/dev/null; then
  echo "[remote_install] installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="${HOME}/.local/bin:${PATH}"

if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR" && git pull -q && echo "Updated: $(git log -1 --oneline)"
else
  git clone -q "$REPO_URL" "$REPO_DIR"
  echo "Cloned: $(cd "$REPO_DIR" && git log -1 --oneline)"
fi

cd "$REPO_DIR"
uv sync --all-packages 2>&1 | tail -5
echo "✓ mcp-filesystem installed"
