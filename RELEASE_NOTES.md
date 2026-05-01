# Release Notes

## v0.1.0 — Initial Release

A self-hosted MCP server that gives local LLMs structured access to file management tools.
Designed for low-VRAM machines running 9B local models in LM Studio — 6 tools, all schemas
under 1,200 tokens total.

---

### What's Included

**6 tools covering the full file management loop:**

| Tool | Role | Description |
|---|---|---|
| `fs_query` | LOCATE | Find files by name/glob/content; grep mode returns matching lines with context |
| `fs_read` | INSPECT | Read file content, directory trees, metadata, or unified diffs — always bounded |
| `fs_write` | PATCH | Write, edit, move, copy, rename; in-place text editing without full rewrites |
| `fs_index` | VERIFY | SQLite FTS5 filename index for instant lookups; per-file operation receipts |
| `fs_manage` | METADATA | Disk usage, permissions, symlink info, snapshot version list |
| `fs_archive` | ARCHIVE | Create and extract zip / tar.gz using Python stdlib only — zero extra dependencies |

---

### Safety

- **Two-phase deletion** — every delete goes through a pending token (Phase 1) that must be
  explicitly confirmed (Phase 2). Auto-approve in LM Studio cannot bypass it because Phase 1
  never deletes — it only returns a token the LLM cannot fabricate.
- **Automatic snapshots** — every destructive write is snapshotted to `~/.mcp_versions/`
  before executing. Full restore via `fs_manage action=versions` +
  `fs_write op=delete_confirm`.
- **Operation receipts** — every `fs_write` op is logged to a per-file
  `.mcp_receipt.json`. Full audit trail via `fs_index action=receipt`.

---

### Cross-Platform Search Backends

| Search type | Backend chain |
|---|---|
| Name / path | Everything (Windows) → mdfind (macOS) → locate (Linux) → pure Python |
| Content | ripgrep → pure Python `re` |

Every response includes `backend_used`. Behaviour is identical regardless of backend.

---

### Works Anywhere on Your Filesystem

No home directory restriction. Pass any absolute path — local drives, second drives
(`D:\`), mounted volumes — and the server handles it. UNC network paths are rejected
(local-only by design).

---

### Model-Friendly Aliases

The server accepts common synonyms and normalises them automatically so local models
don't need to memorise exact parameter values:

| Tool | Alias | Resolves to |
|---|---|---|
| `fs_query` | `directory`, `folder`, `folders`, `dirs` | `dir` |
| `fs_query` | `files` | `file` |
| `fs_read` | `list`, `ls` | `tree` |
| `fs_read` | `stat`, `info`, `metadata` | `meta` |
| `fs_read` | `read`, `text` | `content` |
| `fs_manage` | `symlink`, `link` | `symlink_info` |
| `fs_manage` | `snapshot`, `snapshots`, `history` | `versions` |
| `fs_manage` | `perms`, `perm`, `chmod` | `permissions` |
| `fs_manage` | `size`, `storage`, `space` | `disk_usage` |
| `fs_archive` | `tar`, `tgz`, `gz`, `gzip` | `tar.gz` |
| `fs_index` | `list` | new action — returns all indexed entries under a path |

---

### Installation

Paste into LM Studio `mcp.json` (Developer tab → Edit mcp.json):

**Windows (PowerShell)**

```json
{
  "mcpServers": {
    "fs_basic": {
      "command": "powershell",
      "args": [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "$d = Join-Path $env:USERPROFILE '.mcp_servers\\mcp-filesystem'; $g = Join-Path $d '.git'; if (!(Test-Path $g)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d }; git clone https://github.com/azzindani/MCP_File_System.git $d --quiet } else { Set-Location $d; git fetch origin --quiet; git reset --hard FETCH_HEAD --quiet }; Set-Location (Join-Path $d 'servers\\fs_basic'); uv sync --quiet; uv run python server.py"
      ],
      "env": { "MCP_CONSTRAINED_MODE": "0" },
      "timeout": 600000
    }
  }
}
```

**macOS / Linux (bash)**

```json
{
  "mcpServers": {
    "fs_basic": {
      "command": "bash",
      "args": [
        "-c",
        "d=\"$HOME/.mcp_servers/mcp-filesystem\"; if [ ! -d \"$d/.git\" ]; then rm -rf \"$d\"; git clone https://github.com/azzindani/MCP_File_System.git \"$d\" --quiet; else cd \"$d\" && git fetch origin --quiet && git reset --hard FETCH_HEAD --quiet; fi; cd \"$d/servers/fs_basic\"; uv sync --quiet; uv run python server.py"
      ],
      "env": { "MCP_CONSTRAINED_MODE": "0" },
      "timeout": 600000
    }
  }
}
```

Set `MCP_CONSTRAINED_MODE` to `"1"` on machines with limited RAM to reduce result/line limits.

---

### Requirements

- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Git
- LM Studio with a tool-calling model (Gemma 4, Qwen 3, etc.)

---

### CI

Passes on `ubuntu-22.04`, `macos-latest`, and `windows-latest` with
`MCP_CONSTRAINED_MODE=1` and `PYTHONPATH="."`.
