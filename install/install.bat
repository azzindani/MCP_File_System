@echo off
REM install.bat — Install or update mcp-filesystem on Windows
REM Requires: git, uv

SET REPO_URL=https://github.com/azzindani/mcp_file_system.git
SET INSTALL_DIR=%USERPROFILE%\.mcp_servers\mcp-filesystem

echo =^> Installing mcp-filesystem to %INSTALL_DIR%

IF EXIST "%INSTALL_DIR%\.git" (
    echo =^> Updating existing installation...
    cd /d "%INSTALL_DIR%"
    git fetch origin --quiet
    git reset --hard FETCH_HEAD --quiet
) ELSE (
    echo =^> Cloning repository...
    IF EXIST "%INSTALL_DIR%" (
        rmdir /s /q "%INSTALL_DIR%"
    )
    git clone "%REPO_URL%" "%INSTALL_DIR%" --quiet
)

echo =^> Syncing dependencies...
cd /d "%INSTALL_DIR%\servers\fs_basic"
uv sync --quiet

echo =^> Writing MCP config...
cd /d "%INSTALL_DIR%"
uv run python install\mcp_config_writer.py

echo.
echo Installation complete.
echo Restart LM Studio / Claude Desktop to load the fs_basic MCP server.
