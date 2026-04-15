# CLAUDE.md — Unified File System MCP Server

> Standards reference: https://github.com/azzindani/Standards/blob/main/local_mcp/STANDARDS.md (v5.1)
> Project: `fs_basic` — Unified file system server for cross-platform local LLM use
> Tier: Basic (Tier 1)
> Language: Python 3.12 + uv
> Target hardware: 8 GB VRAM, 9B local model (e.g. Qwen3 4B / 9B, Gemma 4 E4B)

---

## 1. Project Overview

### Problem

Local LLMs running in LM Studio lose most of their context window to tool definitions
when file operations are spread across multiple MCP servers (filesystem, ripgrep,
everything-search, code-index, etc.). A typical 4-server file management stack
consumes 20–40 tools × ~200 tokens = 4,000–8,000 tokens of context just for schemas
— before any actual work begins. On a 9B model with ~10,000–12,000 effective tokens,
this leaves no room for real tasks.

### Solution

One unified MCP server (`fs_basic`) that handles ALL file system operations with
**4 tools only**, following the LOCATE → INSPECT → PATCH → VERIFY loop. Other MCP
servers in the ecosystem depend on this as shared file infrastructure.

### Goals

- Replace 4+ separate file MCP servers with a single 4-tool server
- Keep total tool schema budget under 800 tokens
- Run fully offline, no API keys, no cloud dependencies
- Support Windows, macOS, and Linux from one codebase
- Integrate cleanly as a dependency for other MCP servers in the ecosystem
- Follow STANDARDS.md v5.1 exactly — no deviations

### What This Server Does

```
fs_query   →  LOCATE     Find files/dirs by name pattern, type, size, date, content
fs_read    →  INSPECT    Read file content, directory tree, or file metadata
fs_write   →  PATCH      Create, move, copy, rename, delete, or write file content
fs_index   →  VERIFY     Build/query/refresh persistent file index for fast lookups
```

### What This Server Does NOT Do

- No semantic/vector search (that belongs in a separate memory MCP server)
- No code parsing or AST analysis (belongs in code-index MCP)
- No file format conversion (belongs in domain-specific servers)
- No cloud sync or remote file access
- No GPU usage — runs entirely on CPU

---

## 2. Self-Hosted Execution Principle

Every tool must be able to complete its primary operation with the machine
**disconnected from the internet**. This is non-negotiable per STANDARDS.md §4.

- Windows search: uses `os.scandir` + `fnmatch` + optional Everything SDK (es.exe)
  if installed — falls back to pure Python scan if Everything is not present
- macOS search: uses `os.scandir` + `fnmatch` + optional `mdfind` subprocess
- Linux search: uses `os.scandir` + `fnmatch` + optional `locate`/`plocate` subprocess
- Content search: uses pure Python regex scan (no ripgrep required — uses subprocess
  if available, falls back gracefully)
- Index: SQLite + pure Python — zero external service required

---

## 3. Repository Structure

```
fs-mcp/                              ← repo root (suggested name: mcp-filesystem)
│
├── shared/
│   ├── __init__.py
│   ├── version_control.py           ← snapshot / restore
│   ├── patch_validator.py           ← validate op arrays
│   ├── file_utils.py                ← resolve_path, atomic writes, output dir
│   ├── platform_utils.py            ← OS detection, constrained mode, search backend
│   ├── progress.py                  ← ok / fail / info / warn / undo helpers
│   └── receipt.py                   ← operation receipt log
│
├── servers/
│   └── fs_basic/
│       ├── __init__.py
│       ├── server.py                ← FastMCP setup + 4 tool definitions (thin)
│       ├── engine.py                ← thin router — imports from sub-modules
│       ├── _basic_helpers.py        ← shared imports, constants, _error helper
│       ├── _basic_query.py          ← fs_query logic (locate/search)
│       ├── _basic_read.py           ← fs_read logic (content, tree, metadata)
│       ├── _basic_write.py          ← fs_write logic (create/move/copy/delete/write)
│       └── _basic_index.py          ← fs_index logic (SQLite index build/query)
│       └── pyproject.toml
│
├── tests/
│   ├── fixtures/
│   │   ├── simple/                  ← clean directory structure, few files
│   │   ├── messy/                   ← deep nesting, unicode names, symlinks
│   │   └── large/                   ← 5000+ files for truncation/index tests
│   ├── conftest.py
│   └── test_fs_basic.py
│
├── install/
│   ├── install.sh                   ← Linux / macOS POSIX sh
│   ├── install.bat                  ← Windows CMD
│   └── mcp_config_writer.py         ← writes to LM Studio / Claude Desktop config
│
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── release.yml
│
├── pyproject.toml                   ← root workspace
├── uv.lock
├── .python-version                  ← 3.12
├── .gitattributes
├── .editorconfig
├── verify_tool_docstrings.py
├── CLAUDE.md                        ← this file
└── README.md
```

---

## 4. Architecture Principles

### Engine / Server Split (STANDARDS.md §14)

`server.py` — thin MCP wrapper only. Each tool body is a single line:
```python
return engine.fs_query(pattern, path, type_, content, max_results, dry_run)
```

`engine.py` — thin router. Imports from `_basic_*.py` sub-modules, zero MCP imports.

`_basic_*.py` — all real logic lives here. No MCP imports. Pure functions.

### Four-Tool Pattern (STANDARDS.md §9)

```
LOCATE   → fs_query   (find files without reading them)
INSPECT  → fs_read    (read exactly what was located)
PATCH    → fs_write   (apply targeted change)
VERIFY   → fs_read    (re-read to confirm change)
```

The VERIFY step reuses `fs_read` — no fifth tool needed.

### Surgical Read Protocol (STANDARDS.md §10)

`fs_query` returns paths only — zero file content.
`fs_read` reads exactly one file or one bounded directory tree.
`fs_write` confirms the write — never returns file contents.
`fs_index` returns index stats or search results — never file contents.

### Snapshot Before Write (STANDARDS.md §19)

Every `fs_write` operation that modifies or deletes data calls `snapshot()` first.
The backup path is always included in the response.

### Token Budget (STANDARDS.md §20)

4 tools × ≤80 char docstrings ≈ 800 tokens total for all tool schemas.
Individual tool responses capped at 500 tokens (reads) / 150 tokens (writes).
`token_estimate` field present in every response.

---

---

## 5. Tool Specifications

### Tool 1 — fs_query (LOCATE)

```python
@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False})
def fs_query(
    pattern: str,             # glob or regex: "*.py", "report_*.csv", "sales*"
    path: str = "",           # root directory to search ("" = home dir)
    type_: str = "any",       # "file", "dir", "any"
    content: str = "",        # substring to match inside files ("" = skip)
    max_results: int = 50,    # hard cap — constrained mode overrides to 10
    dry_run: bool = False,
) -> dict:
    """Locate files by name pattern, type, or content. Returns paths only."""
```

**Behaviour:**
- Name search: fnmatch against filename — fast, no index required
- Content search: triggered only when `content != ""` — scans matched files
- Uses platform-native backend when available (es.exe / mdfind / locate)
- Falls back to pure Python os.walk + fnmatch when native backend unavailable
- Returns list of absolute paths — zero file content
- Constrained mode caps max_results at 10

**Return shape:**
```python
{
    "success": True, "op": "fs_query",
    "matches": ["/abs/path/file.csv", ...],
    "returned": 5, "total_found": 5, "truncated": False,
    "backend_used": "python",
    "progress": [...], "token_estimate": 120,
}
```

---

### Tool 2 — fs_read (INSPECT / VERIFY)

```python
@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False})
def fs_read(
    path: str,                # absolute path to file or directory
    mode: str = "auto",       # "content", "tree", "meta", "auto"
    start_line: int = 0,
    end_line: int = 100,
    depth: int = 2,           # tree mode: max depth
) -> dict:
    """Read file content, directory tree, or file metadata."""
```

**Modes:**
- `"auto"`: detects file vs directory, picks content or tree
- `"content"`: reads text with start_line:end_line window (max 100 lines / 20 constrained)
- `"tree"`: directory structure up to depth levels (max 500 entries / 100 constrained)
- `"meta"`: size, mtime, permissions, mime type only

Binary files: never return raw bytes — return metadata + 32-byte hex preview.

---

### Tool 3 — fs_write (PATCH)

```python
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": False, "openWorldHint": False})
def fs_write(
    ops: list[dict],          # array of operations
    dry_run: bool = False,
) -> dict:
    """Create, move, copy, rename, delete, or write files. Snapshots before write."""
```

**Supported ops:**

| op | Required fields | Notes |
|---|---|---|
| `write_file` | `path`, `content` | Overwrite or create text file |
| `append_file` | `path`, `content` | Append to file |
| `create_dir` | `path` | Auto-creates parents |
| `delete` | `path` | File or empty dir only |
| `delete_tree` | `path`, `confirm: true` | Requires explicit confirm field |
| `move` | `src`, `dst` | Move or rename |
| `copy` | `src`, `dst` | Copy file or directory |
| `rename` | `path`, `name` | Rename in same directory |

Rules: validate entire array before applying, stop on first failure, snapshot every
destructive op, max 50 ops per call.

---

### Tool 4 — fs_index (INDEX)

```python
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False})
def fs_index(
    path: str = "",
    action: str = "query",    # "build", "query", "stats", "clear"
    pattern: str = "",
    max_results: int = 50,
) -> dict:
    """Build, query, or refresh the persistent SQLite file index."""
```

Index stored at `~/.mcp_fs_index/index.db` (SQLite FTS5).
Schema: `(path TEXT, name TEXT, size INTEGER, mtime REAL, type TEXT)`

---

## 6. Platform Search Backends

Backend detection and fallback chain in `_basic_query.py`:

```
Windows:
  1. Everything (es.exe) — if EVERYTHING_PATH set or es.exe in PATH
  2. Pure Python os.walk + fnmatch (fallback, always available)

macOS:
  1. mdfind — always available on macOS 10.4+
  2. Pure Python os.walk + fnmatch (fallback)

Linux:
  1. locate / plocate — if in PATH
  2. Pure Python os.walk + fnmatch (fallback, always available)
```

`platform_utils.get_search_backend()` → `"everything" | "mdfind" | "locate" | "python"`

The pure Python fallback is production quality — native backends are speed
optimisations only. The server behaves identically on all platforms.

---

## 7. Shared Module Contracts

### shared/platform_utils.py

```python
def is_constrained_mode() -> bool        # MCP_CONSTRAINED_MODE == "1"
def get_max_results() -> int             # 10 if constrained else 50
def get_max_lines() -> int               # 20 if constrained else 100
def get_max_tree_entries() -> int        # 100 if constrained else 500
def get_max_depth() -> int               # 3 if constrained else 5
def get_search_backend() -> str          # "everything"|"mdfind"|"locate"|"python"
def get_platform() -> str                # "windows"|"macos"|"linux"
```

### shared/file_utils.py

```python
def resolve_path(file_path: str, must_exist: bool = False) -> Path
    # Rejects paths outside home dir.
    # Handles Windows long paths (\\?\).
    # Raises ValueError with clear message on violation.

def atomic_write(path: Path, content: str) -> None
    # Write to temp file in same dir, then rename. Never partial files.

def get_default_output_dir(input_path: str | None = None) -> Path
    # Returns input's parent dir if provided, else ~/Downloads.
```

### shared/version_control.py

```python
def snapshot(file_path: str) -> str
    # Copies to ~/.mcp_versions/{stem}_{UTC_ts}{ext}.bak
    # Returns backup path string.

def restore_version(file_path: str, timestamp: str) -> dict
def list_versions(file_path: str) -> list[dict]
```

### shared/progress.py

```python
def ok(msg: str, detail: str = "") -> dict    # {"icon": "✔", ...}
def fail(msg: str, detail: str = "") -> dict  # {"icon": "✘", ...}
def info(msg: str, detail: str = "") -> dict  # {"icon": "ℹ", ...}
def warn(msg: str, detail: str = "") -> dict  # {"icon": "⚠", ...}
def undo(msg: str, detail: str = "") -> dict  # {"icon": "↩", ...}
```

### shared/receipt.py

```python
def append_receipt(file_path: str, tool: str, args: dict,
                   result: str, backup: str | None) -> None
    # Never raises. Silently drops on I/O failure.

def read_receipt_log(file_path: str) -> list[dict]
```


---

## 8. Security Rules (STANDARDS.md §18)

All file paths from tool parameters must pass through `resolve_path()` before any
disk operation. This rejects:
- Paths outside the user's home directory
- Path traversal sequences (`../`, `..\\`)
- Windows UNC paths from user input

```python
# In every engine function, first line on any file path param:
path = resolve_path(file_path)
```

Subprocess calls (es.exe, mdfind, locate) must use argument lists with `shell=False`
and always set `timeout`:

```python
# Wrong
subprocess.run(f"mdfind {pattern}", shell=True)

# Correct
subprocess.run(["mdfind", pattern], shell=False, capture_output=True, timeout=30)
```

Never use `eval()` or `exec()`. Content pattern matching uses the `re` module with
a compiled pattern and `re.escape()` for literal strings.

---

## 9. Error Handling Contract (STANDARDS.md §17)

Engine functions never raise exceptions to the caller. All exceptions become
error dicts:

```python
def fs_query(pattern: str, path: str, ...) -> dict:
    try:
        ...
        return {"success": True, "op": "fs_query", ...}
    except ValueError as e:
        return {"success": False, "error": str(e),
                "hint": "Check that path is absolute and within your home directory."}
    except PermissionError as e:
        return {"success": False, "error": f"Permission denied: {e}",
                "hint": "Check directory permissions or use a path you own."}
    except Exception as e:
        return {"success": False, "error": str(e),
                "hint": "Use fs_query with a simpler pattern to narrow the scope."}
```

Hint rules: must complete "To fix this, ..." and name a specific tool or action.

---

## 10. Version Control and Receipts (STANDARDS.md §19, §25)

`fs_write` snapshots before every destructive op:

```
~/.mcp_versions/
  sales_q3_2026-04-15T10-30-00Z.csv.bak
  report_2026-04-15T11-00-00Z.pdf.bak
```

Every `fs_write` call appends to a receipt log beside each modified file:
```
/path/to/sales.csv
/path/to/sales.csv.mcp_receipt.json
```

---

## 11. Token Budget Enforcement (STANDARDS.md §20)

| Budget item | Target | Hard limit |
|---|---|---|
| All 4 tool schemas | ~800 tokens | 1,000 tokens |
| fs_query response | ≤200 tokens | 500 tokens |
| fs_read response | ≤400 tokens | 500 tokens |
| fs_write response | ≤100 tokens | 150 tokens |
| fs_index response | ≤150 tokens | 300 tokens |

Every response includes:
```python
response["token_estimate"] = len(str(response)) // 4
```

Truncated responses always include:
```python
{"truncated": True, "returned": N, "total_available": M,
 "hint": "Use fs_read with start_line/end_line to read specific ranges."}
```

---

## 12. Cross-Platform Compatibility (STANDARDS.md §28)

### Paths — pathlib everywhere

```python
# Wrong
path = base_dir + "/" + filename

# Correct
path = Path(base_dir) / filename
```

### Line endings

`.gitattributes`:
```
* text=auto eol=lf
*.bat text eol=crlf
*.cmd text eol=crlf
```

### Windows long paths

```python
if sys.platform == "win32" and len(str(path)) > 200:
    path = Path("\\\\?\\" + str(path.resolve()))
```

### stdout is the MCP channel — never print

```python
# Wrong — corrupts MCP stdio channel
print("Searching files...")

# Correct — stderr only
import logging, sys
logger = logging.getLogger(__name__)
logger.debug("Searching files...")
```

`server.py` configures logging to stderr at WARNING level.

### Atomic writes

```python
import tempfile, shutil
with tempfile.NamedTemporaryFile(delete=False, dir=path.parent,
                                  suffix=path.suffix) as tmp:
    tmp.write(content.encode())
    tmp_path = tmp.name
shutil.move(tmp_path, path)
```

---

## 13. Installation Entries (STANDARDS.md §31)

### Windows (PowerShell)

```json
{
  "mcpServers": {
    "fs_basic": {
      "command": "powershell",
      "args": [
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        "$d = Join-Path $env:USERPROFILE '.mcp_servers\\mcp-filesystem'; $g = Join-Path $d '.git'; if (!(Test-Path $g)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d }; git clone https://github.com/{owner}/mcp-filesystem.git $d --quiet } else { Set-Location $d; git fetch origin --quiet; git reset --hard FETCH_HEAD --quiet }; Set-Location (Join-Path $d 'servers\\fs_basic'); uv sync --quiet; uv run python server.py"
      ],
      "env": { "MCP_CONSTRAINED_MODE": "0" },
      "timeout": 600000
    }
  }
}
```

### macOS / Linux (bash)

```json
{
  "mcpServers": {
    "fs_basic": {
      "command": "bash",
      "args": [
        "-c",
        "d=\"$HOME/.mcp_servers/mcp-filesystem\"; if [ ! -d \"$d/.git\" ]; then rm -rf \"$d\"; git clone https://github.com/{owner}/mcp-filesystem.git \"$d\" --quiet; else cd \"$d\" && git fetch origin --quiet && git reset --hard FETCH_HEAD --quiet; fi; cd \"$d/servers/fs_basic\"; uv sync --quiet; uv run python server.py"
      ],
      "env": { "MCP_CONSTRAINED_MODE": "0" },
      "timeout": 600000
    }
  }
}
```

Set `MCP_CONSTRAINED_MODE: "1"` on machines with ≤8 GB VRAM.

---

## 14. Testing Requirements (STANDARDS.md §27)

Tests import from `engine.py` directly. Never spin up an MCP server process.

### Fixtures (tests/fixtures/)

- `simple/` — flat directory, 10 files, clean names
- `messy/` — nested 4 levels, unicode filenames, symlinks, mixed extensions
- `large/` — 5,000+ files for truncation/index/constrained mode tests

### Required tests per tool

**fs_query:**
- [ ] Finds files by glob pattern
- [ ] Returns paths only, no content
- [ ] Respects max_results cap
- [ ] Constrained mode cap (MCP_CONSTRAINED_MODE=1)
- [ ] Content search finds files containing pattern
- [ ] Pattern with no matches returns empty list + hint
- [ ] Path outside home dir → error dict
- [ ] Invalid root path → error dict

**fs_read:**
- [ ] Content mode: reads correct line range
- [ ] Content mode: truncates at max_lines with truncated=True
- [ ] Tree mode: respects depth limit
- [ ] Tree mode: truncates at max_tree_entries
- [ ] Meta mode: returns metadata, no content
- [ ] Auto mode: file → content, directory → tree
- [ ] Binary file: returns metadata + hex preview, not raw bytes
- [ ] File not found → error dict with hint
- [ ] Path outside home → error dict

**fs_write:**
- [ ] write_file: creates new file, content correct on re-read
- [ ] write_file: overwrites existing, snapshot created
- [ ] write_file: backup path in response
- [ ] append_file: appends, original unchanged before append
- [ ] create_dir: creates nested directories
- [ ] delete: removes file, snapshot created
- [ ] delete_tree: requires confirm=True, errors without it
- [ ] move: src gone, dst exists
- [ ] copy: src still exists, dst has same content
- [ ] dry_run=True: disk unchanged, would_change in response
- [ ] Invalid op name: full array rejected, nothing applied
- [ ] Partial invalid batch: nothing applied on any op
- [ ] Path outside home → error dict

**fs_index:**
- [ ] build: creates ~/.mcp_fs_index/index.db
- [ ] query: returns matches from index
- [ ] stats: returns file count and last_built
- [ ] clear: removes entries for path subtree
- [ ] query on stale index: includes index_age_hours warning

### Coverage requirements

| Module | Minimum |
|---|---|
| shared/ | 100% |
| engine.py | ≥90% |
| Error paths | All documented conditions |
| Happy paths | All 4 tools |

### CI matrix

All 3 platforms: ubuntu-22.04, macos-latest, windows-latest
`MCP_CONSTRAINED_MODE: "1"` set in CI env
`PYTHONPATH: "."` set in CI env

---

## 15. What the AI Must Never Do

(Applies to any AI coding agent working in this repo)

1. Print to stdout anywhere in `server.py`, `engine.py`, or `_basic_*.py`
2. Return a plain string, list, None, or boolean from a tool — always dict
3. Write to disk without calling `snapshot()` first (for write/delete/move ops)
4. Swallow exceptions silently — every exception becomes an error dict
5. Use `eval()` or `exec()` on any user-provided input
6. Use `shell=True` in any subprocess call
7. Use string concatenation for file paths — always pathlib
8. Put business logic in `server.py` — tool bodies are single-line calls to engine
9. Exceed 4 tools in this server — the budget is 4, no exceptions
10. Hardcode row/line/result limits — always call `get_max_*()` from platform_utils
11. Use `git pull` in mcp.json — always `git fetch + git reset --hard FETCH_HEAD`
12. Use user-provided paths without calling `resolve_path()` first
13. Require internet access for any core file operation
14. Return raw binary data through the MCP channel
15. Write tool docstrings longer than 80 characters

---

## 16. Progress Tracker

### Phase 1 — Shared Modules
- [ ] `shared/__init__.py`
- [ ] `shared/platform_utils.py` — OS detection, constrained mode, backend detection
- [ ] `shared/file_utils.py` — resolve_path, atomic_write, get_default_output_dir
- [ ] `shared/version_control.py` — snapshot, restore_version, list_versions
- [ ] `shared/progress.py` — ok, fail, info, warn, undo
- [ ] `shared/receipt.py` — append_receipt, read_receipt_log
- [ ] `shared/patch_validator.py` — validate op arrays for fs_write

### Phase 2 — Engine Sub-Modules
- [ ] `servers/fs_basic/__init__.py`
- [ ] `servers/fs_basic/_basic_helpers.py` — shared constants, _error helper
- [ ] `servers/fs_basic/_basic_query.py` — fs_query logic, backend detection
- [ ] `servers/fs_basic/_basic_read.py` — fs_read logic, all modes
- [ ] `servers/fs_basic/_basic_write.py` — fs_write logic, all ops
- [ ] `servers/fs_basic/_basic_index.py` — SQLite FTS5 index build/query
- [ ] `servers/fs_basic/engine.py` — thin router

### Phase 3 — Server
- [ ] `servers/fs_basic/server.py` — 4 tools, thin wrappers, transport modes
- [ ] `servers/fs_basic/pyproject.toml`
- [ ] Root `pyproject.toml` workspace config
- [ ] `.python-version` = 3.12
- [ ] `verify_tool_docstrings.py`

### Phase 4 — Tests
- [ ] `tests/fixtures/simple/`
- [ ] `tests/fixtures/messy/`
- [ ] `tests/fixtures/large/`
- [ ] `tests/conftest.py`
- [ ] `tests/test_fs_basic.py` — all tools, all error paths, all platforms

### Phase 5 — CI/CD
- [ ] `.github/workflows/ci.yml` — lint + test on all 3 platforms
- [ ] `.github/workflows/release.yml`
- [ ] All CI checks pass on ubuntu-22.04
- [ ] All CI checks pass on macos-latest
- [ ] All CI checks pass on windows-latest

### Phase 6 — Distribution
- [ ] `install/install.sh` (POSIX sh)
- [ ] `install/install.bat`
- [ ] `install/mcp_config_writer.py`
- [ ] mcp.json entries tested on Windows (PowerShell format)
- [ ] mcp.json entries tested on macOS/Linux (bash format)
- [ ] README.md following STANDARDS.md §35 section order

### Definition of Done
- [ ] verify_tool_docstrings.py passes (all ≤80 chars)
- [ ] No file exceeds 1,000 lines
- [ ] uv run pytest — all pass on all 3 platforms
- [ ] uv run ruff check . — no errors
- [ ] uv run ruff format --check . — no reformatting needed
- [ ] uv run pyright servers/ shared/ — no errors
- [ ] Manual test in LM Studio (9B model) — 4-tool loop works
- [ ] 10-step file task test — context window not exceeded
- [ ] MCP_CONSTRAINED_MODE=1 enforces smaller limits correctly

---

*CLAUDE.md version: 1.0*
*Standards: STANDARDS.md v5.1 (azzindani/Standards)*
*Last updated: 2026-04-15*
