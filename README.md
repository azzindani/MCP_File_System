# MCP File System

A self-hosted MCP server that gives local LLMs structured access to file management tools. No cloud APIs, no API keys — everything runs on your machine.

## Features

- **6 tools** in a single server: `fs_query`, `fs_read`, `fs_write`, `fs_index`, `fs_manage`, `fs_archive`
- **LOCATE → INSPECT → PATCH → VERIFY** workflow for surgical file edits
- **Automatic version control** — every destructive write is snapshotted and fully restorable
- **Operation receipt logging** — full audit trail of all modifications per file
- **Two-phase deletion protocol** — deletions always require an explicit confirmation token; auto-approve cannot bypass it
- **Constrained mode** — reduces result/line limits for lower-memory machines
- **In-place editing ops** — `replace_text`, `insert_after`, `delete_lines`, `patch_lines` without rewriting whole files
- **SQLite FTS5 index** — fast filename lookup without scanning disk on every query
- **Archive support** — create and extract zip / tar.gz using Python stdlib only (zero extra deps)
- **Cross-platform name search** — Everything (Windows), mdfind (macOS), locate (Linux), pure Python fallback
- **Cross-platform content search** — ripgrep if available, pure Python `re` fallback
- **Symlink safety** — `follow_symlinks=False` by default, prevents infinite loops
- **Path traversal prevention** — every path resolved and validated against the user's home directory
- **Modular architecture** — engine split into focused sub-modules, all under 1 000 lines

## Quick Install (LM Studio)

> **Tested on Windows 11** with LM Studio 0.4.x and uv 0.5+.

### Requirements

- **Git** — `git --version`
- **Python 3.14 or higher** — `python --version`
- **uv** — `uv --version` ([install guide](https://docs.astral.sh/uv/getting-started/installation/))
- **LM Studio** with a model that supports tool calling (Gemma 4, Qwen 3, etc.)

### Platform Support

| Platform | Status |
|---|---|
| Windows | Tested — real-world verified (Windows 11) |
| macOS | Untested — CI/CD pipeline passes |
| Linux | Untested — CI/CD pipeline passes |

> Real-world usage has only been verified on Windows. macOS and Linux are supported by design and pass the automated CI pipeline, but have not been tested by hand. Reports from non-Windows users are welcome.

### First Run

The first launch clones the repo and installs dependencies (~1-3 minutes). Subsequent launches are instant.

> **Pre-install recommended:** To avoid the 60-second LM Studio connection timeout on first launch, run this once in PowerShell before connecting:
> ```powershell
> $d = Join-Path $env:USERPROFILE '.mcp_servers\mcp-filesystem'
> $g = Join-Path $d '.git'
> if (!(Test-Path $g)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d }; git clone https://github.com/azzindani/MCP_File_System.git $d --quiet }
> Set-Location "$d\servers\fs_basic"; uv sync
> ```
> If you skip this step and LM Studio times out, press **Restart** in the MCP Servers panel — it will reconnect and complete the install immediately.

### Steps

1. Open LM Studio → **Developer** tab (`</>` icon) or you can find via **Integrations**
2. Find **mcp.json** or **Edit mcp.json** → click to open
3. Paste this config:

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

4. Wait for the blue dot next to the server
5. Start chatting — the model will see all 6 tools

### macOS / Linux

Replace the `"command"` and `"args"` with the bash equivalent:

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

## Available Tools

### fs_query — LOCATE

Find files and directories by name, type, size, date, or content. Optionally returns matching lines (grep mode) instead of just paths.

| Parameter | Default | Description |
|---|---|---|
| `pattern` | required | Glob pattern (`*.py`, `report_*`) or literal filename |
| `path` | `""` (home dir) | Root directory to search |
| `type_` | `"any"` | `"file"` \| `"dir"` \| `"any"` |
| `content` | `""` | Substring or regex to match inside files |
| `grep_mode` | `False` | Return line-level matches instead of paths |
| `context_lines` | `0` | Lines before/after each grep match (0–5) |
| `include_meta` | `False` | Add size, mtime, and MIME type per result |
| `follow_symlinks` | `False` | Follow symlinks (risk: loops) |
| `max_results` | `50` | Cap on returned results |

**Backends (name search):** Everything → mdfind → locate → pure Python `os.walk`
**Backends (content search):** ripgrep → pure Python `re`

Every response includes `backend_used`.

---

### fs_read — INSPECT / VERIFY

Read file content, directory trees, metadata, diffs, or change detection. Always bounded — never returns unbounded data.

| Parameter | Default | Description |
|---|---|---|
| `path` | required | Absolute path to file or directory |
| `mode` | `"auto"` | `"content"` \| `"tree"` \| `"meta"` \| `"diff"` \| `"auto"` |
| `start_line` | `0` | First line to return (content mode) |
| `end_line` | `100` | Last line exclusive (content mode) |
| `depth` | `2` | Max directory depth (tree mode) |
| `compare_to` | `""` | Path or snapshot timestamp to diff against |
| `changed_since` | `""` | ISO timestamp — returns `changed: true/false` (meta mode) |

**Modes:**
- `auto` — file → content, directory → tree
- `content` — lines `[start_line:end_line]`, max 100 lines (20 constrained)
- `tree` — directory structure up to `depth`, max 500 entries (100 constrained)
- `meta` — size, mtime, permissions, MIME type, symlink info
- `diff` — unified diff between `path` and `compare_to`

Binary files return metadata + 32-byte hex preview only — never raw bytes.

---

### fs_write — PATCH

All write, edit, move, copy, rename, and delete operations. Delete always requires a two-phase confirmation token.

| Op | Required fields | Snapshot | Notes |
|---|---|---|---|
| `write_file` | `path`, `content` | if overwrite | Creates or overwrites. Optional `content_encoding`: `text` (default) or `base64` for binary files |
| `append_file` | `path`, `content` | No | Non-destructive |
| `create_dir` | `path` | No | Auto-creates parents |
| `move` | `src`, `dst` | if dst exists | Errors if dst exists |
| `copy` | `src`, `dst` | if dst exists | Snapshots dst before overwrite |
| `rename` | `path`, `name` | No | Same directory only |
| `replace_text` | `path`, `find`, `replace` | Yes | Optional `regex`, `count` |
| `insert_after` | `path`, `after_pattern`, `content` | Yes | Insert lines after match |
| `delete_lines` | `path`, `start_line`, `end_line` | Yes | Remove line range |
| `patch_lines` | `path`, `start_line`, `end_line`, `content` | Yes | Replace line range |
| `delete_request` | `path` | No | Phase 1 — returns confirmation token |
| `delete_confirm` | `token` | Yes | Phase 2 — executes after token validated |
| `delete_tree_request` | `path` | No | Phase 1 for directory tree |
| `delete_tree_confirm` | `token` | Yes | Phase 2 for directory tree |
| `set_permissions` | `path`, `mode` | No | Linux/macOS only, no-op on Windows |
| `download` | `url`, `path` | if overwrite | Fetches an http(s) URL to `path`. Requires `MCP_FETCH_URLS=1` |

**Rules:**
- Max 50 ops per call
- Entire array validated before any op executes — fails atomically
- `dry_run=True` returns `would_change` list without touching disk
- A `delete_request` op always stops the batch and returns a pending token

#### Two-Phase Deletion Protocol

```
Phase 1 — delete_request (safe under auto-approve)
  Server returns pending token + target list + sizes
  Nothing is deleted

Phase 2 — delete_confirm (requires user to see Phase 1 output)
  User approves; LLM calls delete_confirm with the token
  Server snapshots each target, then deletes
  Backup paths returned in response
```

Tokens expire after 300 seconds and are consumed on use — they cannot be reused.

---

### fs_index — VERIFY / INDEX

Build and query a SQLite FTS5 filename index, or read a file's operation receipt history.

| Action | Description |
|---|---|
| `build` | Scan `path` recursively → write to `~/.mcp_fs_index/index.db` |
| `query` | Fast FTS5 lookup by filename pattern (no disk scan) |
| `list` | List all indexed entries under `path` (no pattern required) |
| `stats` | Index metadata: file count, `last_built`, indexed roots |
| `clear` | Remove index entries for `path` subtree |
| `receipt` | Read operation history from `{path}.mcp_receipt.json` |

---

### fs_manage — METADATA / SYSTEM

Read-only metadata queries. Permission changes go through `fs_write`.

| Action | Description |
|---|---|
| `disk_usage` | Total / used / free for the filesystem containing `path` |
| `permissions` | rwx bits, owner, group (Linux/macOS); ACL summary (Windows) |
| `symlink_info` | `is_symlink`, target path, `is_broken` |
| `versions` | List available snapshots for `path` from `~/.mcp_versions/` |

---

### fs_archive — ARCHIVE OPS

Create and extract zip / tar.gz archives using Python stdlib only — zero external dependencies, works fully offline.

| Action | Description |
|---|---|
| `create` | Pack `target` (file or directory) into archive at `path` |
| `extract` | Unpack archive at `path` into `target` directory |
| `list` | List archive contents without extracting |

`format_`: `"zip"` or `"tar.gz"`. `dry_run=True` previews without touching disk.
Extraction into a directory with conflicting files requires `overwrite=True`.

---

## Usage Examples

### Find files by name

```
Find all Python files in C:\Users\you\projects
```

### Search file contents

```
Search for the string "def train" in all .py files under C:\Users\you\projects
```

### Read a file

```
Show me lines 50–100 of C:\Users\you\projects\model.py
```

### Read a directory tree

```
Show me the directory structure of C:\Users\you\projects two levels deep
```

### Edit in place

```
Replace all occurrences of "old_api_url" with "new_api_url" in C:\Users\you\config.py
```

### Delete a file safely

```
Delete C:\Users\you\old_data.csv
```

*(The model will request a confirmation token first. You will see the target and size before anything is deleted.)*

### Undo a change

```
Show me the available versions of C:\Users\you\config.py, then restore the previous one
```

### Create an archive

```
Zip the folder C:\Users\you\reports into C:\Users\you\reports_backup.zip
```

### Build the file index

```
Build a file index for C:\Users\you\projects so filename lookups are instant
```

### Check operation history

```
Show me the receipt history for C:\Users\you\data\sales.csv
```

## Configuration

### Constrained Mode

For lower-memory machines, set `MCP_CONSTRAINED_MODE=1` in the `env` section of `mcp.json`. This reduces:
- Lines returned per `fs_read`: 100 → 20
- Tree entries returned: 500 → 100
- Search results: 50 → 10
- Context lines (grep): 5 → 2

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MCP_CONSTRAINED_MODE` | `0` | Set to `1` for low-memory machines |
| `MCP_OUTPUT_DIR` | `~/Downloads` | Shared directory other services and the outside world can also see |
| `MCP_PUBLIC_BASE_URL` | _(unset)_ | Public URL serving `MCP_OUTPUT_DIR`; adds `public_url` to write results |
| `MCP_FETCH_URLS` | `0` | `1` enables the `download` op in `fs_write` |
| `MCP_FETCH_ALLOW_PRIVATE` | `0` | `1` permits fetching hosts on private/loopback addresses |
| `MCP_MAX_FETCH_MB` | `100` | Size cap for a downloaded URL |

### Hybrid local + remote file handling

The same engine serves a local stdio install and a self-hosted HTTP endpoint,
but over HTTP the caller shares no filesystem with the server: the path it
just got back means nothing to it, and it may hold a link rather than a file.
Three opt-in variables close that gap without changing local behaviour — all
are unset by default:

- **`MCP_OUTPUT_DIR`** — bind-mount a directory here to give this server a
  patch of filesystem that other services (and a file server) can also see.
- **`MCP_PUBLIC_BASE_URL`** — the public URL that serves that directory. Any
  file written there comes back with a `public_url` you can open or pass on.
- **`MCP_FETCH_URLS=1`** — enables `fs_write`'s `download` op, which fetches
  an `http(s)` URL to a local path. Hosts resolving to loopback/link-local/
  private addresses are refused, redirects included, unless
  `MCP_FETCH_ALLOW_PRIVATE=1`. URL support is a distinct op rather than
  something `path` accepts everywhere, because in every other op `path` is a
  *destination* — silently downloading one would be nonsense.

```json
[{"op": "download", "url": "https://example.com/sales.csv", "path": "/files/sales.csv"}]
```

`write_file` also takes `content_encoding: "base64"`, so a caller with no
shared filesystem can push real binary content in as well as pull it out.

## Deployment

| Mode | Best for | Transport | Auth |
|---|---|---|---|
| **Local stdio** (default, above) | LM Studio / Claude Code on your machine | stdio | none |
| **Local Docker / HTTP** | Testing, or one other machine on your LAN | HTTP | optional |
| **VPS Docker** | Remote MCP clients (claude.ai, hosted harnesses) | HTTP | **required** |

fs_basic is **not** read-only (`fs_write`/`fs_archive` can modify the filesystem) —
bind-mount only the directory tree you want it to manage.

### HTTP transport (no Docker)

```bash
FS_TRANSPORT=http FS_PORT=8801 uv run python servers/fs_basic/server.py
curl http://localhost:8801/health   # {"status":"ok","version":"0.1.0"}
```

### Docker

```bash
docker compose up -d --build
curl http://localhost:8801/health
```

With auth (**required** for any publicly reachable deploy — this is how the
production `fs.casava.space` endpoint runs, and matters more here than in
sibling repos since `fs_write` can create/overwrite/delete real files):

```bash
echo "FS_API_KEY=$(openssl rand -hex 24)" > .env   # gitignored, auto-loaded by docker-compose.yml
docker compose up -d --build
```

For multiple named clients instead of one shared key (Folio-style):

```bash
cp tokens.example.json tokens.json   # edit: replace placeholders with `openssl rand -hex 32`
FS_TOKENS_FILE=/path/to/tokens.json docker compose up -d --build
```

`/mcp` requires `Authorization: Bearer <token>` once any of `FS_TOKENS_FILE` /
`FS_TOKENS` / `FS_API_KEY` is set; `/health` and `/version` stay unauthenticated.

### Deployment environment variables

| Variable | Default | Description |
|---|---|---|
| `FS_TRANSPORT` | `stdio` | `stdio` or `http` |
| `FS_HOST` | `127.0.0.1` | Bind address for HTTP mode |
| `FS_PORT` | `8801` | Port for HTTP mode |
| `FS_TOKENS_FILE` | unset | JSON file of named bearer tokens (`{"name": "token"}`) — highest priority |
| `FS_TOKENS` | unset | Inline `"name:token,name2:token2"` |
| `FS_API_KEY` | unset | Single shared bearer token |

### Remote testing (Cloudflare Quick Tunnel)

Same idea as `azzindani/Folio`'s `launch.sh`: bring the Docker deployment up
and expose it at an ephemeral `*.trycloudflare.com` URL — no VPS, no DNS, no
account — so it's reachable from any MCP-compatible harness for a quick
remote smoke test.

```bash
./launch_tunnel.sh          # docker compose up -d --build, then tunnel
./launch_tunnel.sh stop     # tear the tunnel down (containers keep running)
```

Not for production: Quick Tunnels are unauthenticated at the transport layer.
Set `FS_API_KEY` or `FS_TOKENS_FILE` before tunneling so `/mcp` still
requires a bearer token even while it's publicly reachable.

### Remote smoke test (`remote_smoke_test.sh`)

Run in CI against a container (the `e2e` job) and by hand against the
deployment. `pytest` itself stays offline. Exercises a running HTTP endpoint: auth enforcement plus a real
handwritten-prompt-style call for **all 6 tools** (`fs_query`, `fs_read`,
`fs_write`, `fs_index`, `fs_manage`, `fs_archive`), against real seeded files
in the container. This is what caught the root-owned `/home/app` bug blocking
`fs_index`'s default index path.

```bash
./remote_smoke_test.sh                      # reads FS_API_KEY from .env, targets fs.casava.space
DOMAIN=http://localhost:8801 ./remote_smoke_test.sh   # test a different target
```

## Uninstall

**Step 1:** Remove from LM Studio
1. Open LM Studio → Developer tab (`</>`)
2. Delete the `fs_basic` entry from MCP Servers
3. Restart LM Studio

**Step 2:** Delete installed files
```cmd
rmdir /s /q %USERPROFILE%\.mcp_servers\mcp-filesystem
```

Optionally remove the index and version snapshot directories:
```cmd
rmdir /s /q %USERPROFILE%\.mcp_fs_index
rmdir /s /q %USERPROFILE%\.mcp_versions
```

## Architecture

```
mcp-filesystem/
├── shared/                      ← utilities (no MCP imports)
│   ├── platform_utils.py        ← OS detection, constrained mode, backend detection
│   ├── file_utils.py            ← resolve_path, atomic_write, get_default_output_dir
│   ├── version_control.py       ← snapshot() / restore_version() / list_versions()
│   ├── progress.py              ← ok / fail / info / warn / undo helpers
│   ├── receipt.py               ← append_receipt() / read_receipt_log()
│   ├── patch_validator.py       ← validate op arrays before execution
│   └── confirm_store.py         ← in-memory deletion token store (5-min expiry)
├── servers/
│   └── fs_basic/                ← single Tier 1 server (6 tools)
│       ├── server.py            ← thin MCP wrapper; each tool is one engine call
│       ├── engine.py            ← thin router; zero MCP imports
│       ├── _basic_helpers.py    ← shared imports, constants, _error helper
│       ├── _basic_query.py      ← fs_query: name search + grep search
│       ├── _basic_read.py       ← fs_read: content / tree / meta / diff / changed_since
│       ├── _basic_write.py      ← fs_write: all ops + two-phase deletion protocol
│       ├── _basic_index.py      ← fs_index: SQLite FTS5 + receipt
│       └── pyproject.toml
├── tests/
│   ├── fixtures/
│   │   ├── simple/              ← flat dir, 10 files, clean names
│   │   ├── messy/               ← 4-level nesting, unicode names, symlinks
│   │   └── large/               ← 5 000+ files for truncation + index tests
│   ├── conftest.py
│   └── test_fs_basic.py         ← all 6 tools, all modes, all ops, all error paths
├── install/
│   ├── install.sh               ← POSIX sh installer
│   ├── install.bat              ← Windows CMD installer
│   └── mcp_config_writer.py     ← writes LM Studio / Claude Desktop mcp.json entries
├── .github/
│   └── workflows/
│       ├── ci.yml               ← lint + type-check + test (3-platform matrix)
│       └── release.yml          ← CI + GitHub release on tag push
├── pyproject.toml               ← root workspace
├── uv.lock
├── .python-version              ← 3.14
├── .gitattributes
├── .editorconfig
├── verify_tool_docstrings.py    ← CI gate: all tool docstrings ≤ 80 chars
├── CLAUDE.md
└── README.md
```

## Development

### Local Testing

```bash
# Install all dependencies
uv sync

# Run all tests
uv run pytest tests/ -q --tb=short

# Run in constrained mode
MCP_CONSTRAINED_MODE=1 uv run pytest tests/ -q --tb=short

# Format → lint → type-check → verify docstrings → test (full CI sequence)
uv run ruff format servers/ shared/ tests/
uv run ruff check servers/ shared/ tests/
uv run pyright servers/ shared/
uv run python verify_tool_docstrings.py
uv run pytest tests/ -q --tb=short
```

### Run the server locally

```bash
cd servers/fs_basic && uv sync && uv run python server.py
```

## License

MIT
