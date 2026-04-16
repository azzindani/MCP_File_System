#!/bin/sh
# install.sh — Install or update mcp-filesystem on Linux / macOS
# POSIX sh only. No bash-isms.
set -e

REPO_URL="https://github.com/azzindani/mcp_file_system.git"
INSTALL_DIR="${HOME}/.mcp_servers/mcp-filesystem"

echo "==> Installing mcp-filesystem to ${INSTALL_DIR}"

if [ -d "${INSTALL_DIR}/.git" ]; then
    echo "==> Updating existing installation..."
    cd "${INSTALL_DIR}"
    git fetch origin --quiet
    git reset --hard FETCH_HEAD --quiet
else
    echo "==> Cloning repository..."
    rm -rf "${INSTALL_DIR}"
    git clone "${REPO_URL}" "${INSTALL_DIR}" --quiet
fi

echo "==> Syncing dependencies..."
cd "${INSTALL_DIR}/servers/fs_basic"
uv sync --quiet

echo "==> Writing MCP config..."
cd "${INSTALL_DIR}"
uv run python install/mcp_config_writer.py

echo ""
echo "Installation complete."
echo "Restart LM Studio / Claude Desktop to load the fs_basic MCP server."
