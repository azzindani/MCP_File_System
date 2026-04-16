# CLAUDE.md — Unified File System MCP Server

> Standards: https://github.com/azzindani/Standards/blob/main/local_mcp/STANDARDS.md (v5.1)
> Project repo: `mcp-filesystem`
> Server: `fs_basic` — Tier 1 (File Management Layer)
> Language: Python 3.12 + uv
> Target: 8 GB VRAM, 9B local model (Qwen3 4B/9B, Gemma 4 E4B) in LM Studio

---

## 1. Project Overview

### Problem

Local LLMs in LM Studio lose most of their context window to tool definitions when
file operations are spread across multiple MCP servers (filesystem, ripgrep,
everything-search, code-index, etc.). A 4-server stack consumes 20–40 tools ×
~200 tokens = 4,000–8,000 tokens of schema overhead before any real work starts.
On a 9B model with ~10,000–12,000 effective tokens this leaves almost nothing for
actual tasks. Other MCP servers in the ecosystem also need file operations but have
no shared infrastructure — each reimplements its own path logic.

### Solution

One unified MCP server (`fs_basic`) acting as a **File Management Layer** — rich
enough to be used standalone by the user, lean enough to serve as shared file
infrastructure for other MCP servers. Maximum 8 tools, all schemas under 1,200
tokens total.

### Confirmed Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scope | Option B — File Management Layer | Most capable; useful standalone + as dependency |
| Tool budget | Max 8 tools (Tier 1 target: 6–8) | Within STANDARDS.md §8 |
| Grep | `grep_mode` param in `fs_query` | Saves LLM round trip, no extra tool |
| Grep output | Line number + match line + context lines | Real grep behaviour |
| Diff | `mode="diff"` in `fs_read` | No 5th tool needed |
| Receipt read | `action="receipt"` in `fs_index` | No 5th tool needed |
| Deletion | Two-phase confirmation always | Irreversible — never auto-approved |
| Deletion token | In-memory, 5-minute expiry | Simple; restart clears stale tokens |
| Overwrite confirm | Only when dst already exists | Balance safety vs friction |
| Bulk delete | One combined confirmation token | User sees full scope at once |
| dry_run | Coexists with confirmation flow | dry_run = LLM preview; confirm = user gate |
| Snapshot on delete | Yes — in Phase 2 before executing | Defense in depth |
| Archive ops | v1 — zip/tar.gz via Python stdlib | Zero external dependency |
| MIME in query | `include_meta: bool = False` param | Optional; keeps default lean |
| Symlinks | `follow_symlinks=False` by default | Prevent infinite loop on os.walk |
| Change detection | `changed_since: str` param in `fs_read` | mtime comparison, no polling |
| Permissions | `set_permissions` op in `fs_write` | Linux/macOS only, no-op on Windows |
| Batch ops | v1 — bulk rename/move/delete via op arrays | Already structured for it |
| In-place editing | New ops in `fs_write` | replace_text, insert_after, delete_lines, patch_lines |

### What This Server Does

```
fs_query  →  LOCATE   Find files/dirs by name, type, size, date, content + grep lines
fs_read   →  INSPECT  Read content, tree, metadata, diff, receipt history
fs_write  →  PATCH    Create, write, append, move, copy, rename, delete, archive, edit
fs_index  →  VERIFY   Build/query/refresh SQLite index; receipt; stats
```

### What This Server Does NOT Do

- No semantic / vector search → separate memory MCP server
- No code parsing / AST analysis → code-index MCP
- No file format conversion → domain-specific servers
- No cloud sync or remote file access
- No GPU usage — runs entirely on CPU
- No internet required for any core operation

---

## 2. Self-Hosted Execution Principle (STANDARDS.md §4)

Every tool must complete its primary operation with the machine disconnected from
the internet. Platform-native backends are speed optimisations only.

### Name/Path Search Backend Chain

```
Windows:  1. Everything (es.exe)       if EVERYTHING_PATH set or es.exe in PATH
          2. Pure Python os.walk + fnmatch     always available

macOS:    1. mdfind subprocess          always on macOS 10.4+
          2. Pure Python os.walk + fnmatch

Linux:    1. locate / plocate           if in PATH
          2. Pure Python os.walk + fnmatch
```

### Content Search Backend Chain

```
All platforms:
  1. ripgrep (rg)           if in PATH (fastest)
  2. Pure Python re module  always available, production quality
```

Every response includes `"backend_used"` field. Behaviour is identical
regardless of backend.

---

## 3. Repository Structure

```
mcp-filesystem/
│
├── shared/
│   ├── __init__.py
│   ├── version_control.py      snapshot / restore / list_versions
│   ├── patch_validator.py      validate fs_write op arrays before execution
│   ├── file_utils.py           resolve_path, atomic_write, get_default_output_dir
│   ├── platform_utils.py       OS detection, constrained mode, backend detection
│   ├── progress.py             ok / fail / info / warn / undo helpers
│   ├── receipt.py              append_receipt / read_receipt_log
│   └── confirm_store.py        in-memory deletion confirmation token store
│
├── servers/
│   └── fs_basic/
│       ├── __init__.py
│       ├── server.py           FastMCP setup + tool definitions (thin wrappers only)
│       ├── engine.py           thin router — imports from sub-modules, zero MCP imports
│       ├── _basic_helpers.py   shared imports, constants, _error helper
│       ├── _basic_query.py     fs_query: name search, grep search
│       ├── _basic_read.py      fs_read: content, tree, meta, diff, changed_since
│       ├── _basic_write.py     fs_write: all ops + deletion protocol
│       ├── _basic_index.py     fs_index: SQLite FTS5 build/query/stats/receipt
│       └── pyproject.toml
│
├── tests/
│   ├── fixtures/
│   │   ├── simple/             flat dir, 10 files, clean names
│   │   ├── messy/              4-level nesting, unicode names, symlinks
│   │   └── large/              5,000+ files for truncation + index tests
│   ├── conftest.py
│   └── test_fs_basic.py
│
├── install/
│   ├── install.sh              Linux / macOS POSIX sh
│   ├── install.bat             Windows CMD
│   └── mcp_config_writer.py   writes to LM Studio / Claude Desktop config
│
├── .github/
│   └── workflows/
│       ├── ci.yml              lint + test all 3 platforms
│       └── release.yml         CI + GitHub release on tag push
│
├── pyproject.toml              root workspace
├── uv.lock
├── .python-version             3.12
├── .gitattributes
├── .editorconfig
├── verify_tool_docstrings.py
├── CLAUDE.md                   this file
└── README.md
```

---

## 4. Architecture Principles

### Engine / Server Split (STANDARDS.md §14)

`server.py` — thin MCP wrapper only. Every tool body is exactly one line calling engine.

`engine.py` — thin router. Zero MCP imports. Imports and re-exports from
`_basic_*.py` sub-modules only.

`_basic_*.py` — all domain logic. Zero MCP imports. No single file exceeds
1,000 lines (STANDARDS.md §15 hard limit).

### Four-Tool Pattern Extended (STANDARDS.md §9)

```
LOCATE   → fs_query   find files; optionally grep matching lines
INSPECT  → fs_read    read content / tree / meta / diff / receipt (bounded)
PATCH    → fs_write   all write ops including two-phase deletion gate
VERIFY   → fs_read    re-read to confirm change applied correctly
```

VERIFY reuses `fs_read` — no extra tool. Core loop: query → read → write → read.

Additional tools (within Tier 1 budget of 6–8):
- `fs_manage` — permissions, symlink info, disk usage, change detection queries
- `fs_archive` — create and extract zip/tar.gz archives

Final tool count: **6 tools** (within Tier 1 target).

### Surgical Read Protocol (STANDARDS.md §10)

- `fs_query`: returns paths only by default. In `grep_mode`, returns matching
  lines only — never full file contents.
- `fs_read`: reads exactly one file (bounded lines) or one directory tree
  (bounded entries). Never returns raw binary data.
- `fs_write`: returns confirmation dict only — never file contents.
- `fs_index`: returns stats or path lists — never file contents.
- `fs_manage`: returns metadata dicts — never file contents.
- `fs_archive`: returns operation result and output path — never file contents.

### Snapshot Before Write (STANDARDS.md §19)

Every `fs_write` op that modifies or destroys data calls `snapshot()` first.
`"backup"` path always included in write response. Deletion snapshots occur
in Phase 2 (post-confirmation), immediately before the delete executes.

### Token Budget (STANDARDS.md §20)

```
All 6 tool schemas:    target ≤1,200 tokens
fs_query response:     ≤300 tokens default / ≤500 tokens grep_mode
fs_read response:      ≤500 tokens
fs_write response:     ≤200 tokens
fs_index response:     ≤300 tokens
fs_manage response:    ≤200 tokens
fs_archive response:   ≤150 tokens
```

Every response: `"token_estimate": len(str(response)) // 4`
Every truncated response: `"truncated": True` + `"hint"` with recovery instruction.

---

---

## 5. Tool Specifications

### Tool 1 — fs_query (LOCATE)

```python
@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False})
def fs_query(
    pattern: str,                   # glob: "*.py" "report_*" or literal name
    path: str = "",                 # root to search ("" = user home dir)
    type_: str = "any",             # "file" | "dir" | "any"
    content: str = "",              # substring/regex to match inside files
    grep_mode: bool = False,        # True = return line-level matches not just paths
    context_lines: int = 0,         # lines before+after each grep match (0–5)
    include_meta: bool = False,     # True = include size+mtime+mime per result
    follow_symlinks: bool = False,  # True = follow symlinks (risk: loops)
    max_results: int = 50,          # constrained mode overrides to 10
) -> dict:
    """Locate files by name/content. grep_mode returns matching lines."""
```

**Behaviour:**
- Name search: fnmatch against filename only (not full path)
- Content search: only when `content != ""` — scans name-matched files
- `grep_mode=False` (default): returns list of matching file paths only
- `grep_mode=True`: returns per-file list of `{line, text, context_before, context_after}`
- Native backend used when available; pure Python fallback always available
- `include_meta=False` by default — keeps response lean for path handoff to other servers
- `follow_symlinks=False` by default — prevents infinite loops in os.walk

**Return shape (default):**
```python
{
    "success": True,
    "op": "fs_query",
    "pattern": "*.csv",
    "root": "/home/user/projects",
    "matches": ["/home/user/projects/sales.csv", "/home/user/projects/report.csv"],
    "returned": 2,
    "total_found": 2,
    "truncated": False,
    "backend_used": "python",
    "progress": [...],
    "token_estimate": 85,
}
```

**Return shape (grep_mode=True):**
```python
{
    "success": True,
    "op": "fs_query",
    "grep_mode": True,
    "content_pattern": "def train",
    "matches": [
        {
            "path": "/home/user/project/model.py",
            "hits": [
                {
                    "line": 42,
                    "text": "def train(self, data):",
                    "context_before": ["", "class Model:"],
                    "context_after": ["    self.data = data", "    self.fit()"],
                }
            ],
        }
    ],
    "returned": 1,
    "truncated": False,
    "backend_used": "python",
    "progress": [...],
    "token_estimate": 210,
}
```

---

### Tool 2 — fs_read (INSPECT / VERIFY)

```python
@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False})
def fs_read(
    path: str,                      # absolute path to file or directory
    mode: str = "auto",             # "content"|"tree"|"meta"|"diff"|"auto"
    start_line: int = 0,            # content mode: first line (0-indexed)
    end_line: int = 100,            # content mode: last line exclusive
    depth: int = 2,                 # tree mode: max directory depth
    compare_to: str = "",           # diff mode: path or snapshot timestamp to diff against
    changed_since: str = "",        # meta mode: ISO timestamp — returns changed bool
) -> dict:
    """Read file content, tree, metadata, or diff. Bounded always."""
```

**Modes:**
- `"auto"`: file → `"content"`, directory → `"tree"`
- `"content"`: text lines `[start_line:end_line]`, max 100 lines (20 constrained)
- `"tree"`: directory structure up to `depth`, max 500 entries (100 constrained)
- `"meta"`: size, mtime, permissions, mime type, is_symlink, symlink_target
- `"diff"`: unified diff between `path` and `compare_to` (path or snapshot timestamp)

**Binary files:** never return raw bytes — return meta + 32-byte hex preview.

**`changed_since` param (meta mode):**
Pass an ISO timestamp; response includes `"changed": True/False` and `"mtime"`.
Used by other MCP servers to detect stale state before patching.

**Return shape (content):**
```python
{
    "success": True,
    "op": "fs_read",
    "path": "/home/user/projects/sales.csv",
    "mode": "content",
    "lines": ["col1,col2\n", "a,1\n"],
    "start_line": 0,
    "end_line": 2,
    "total_lines": 5000,
    "truncated": True,
    "hint": "Use fs_read with start_line/end_line to read other ranges.",
    "progress": [...],
    "token_estimate": 95,
}
```

---

### Tool 3 — fs_write (PATCH)

```python
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": False, "openWorldHint": False})
def fs_write(
    ops: list[dict],                # array of operations — see op table below
    dry_run: bool = False,          # preview without executing
) -> dict:
    """Write, edit, move, copy, rename files. Delete requires confirmation token."""
```

**Full op table:**

| op | Required fields | Optional | Snapshot | Notes |
|---|---|---|---|---|
| `write_file` | `path`, `content` | — | if overwrite | Creates or overwrites |
| `append_file` | `path`, `content` | — | No | Non-destructive |
| `create_dir` | `path` | — | No | Auto-creates parents |
| `move` | `src`, `dst` | — | if dst exists | Snapshot dst before overwrite |
| `copy` | `src`, `dst` | — | if dst exists | Snapshot dst before overwrite |
| `rename` | `path`, `name` | — | No | Same directory only |
| `replace_text` | `path`, `find`, `replace` | `regex: bool`, `count: int` | Yes | In-place edit |
| `insert_after` | `path`, `after_pattern`, `content` | `count: int` | Yes | Insert lines after match |
| `delete_lines` | `path`, `start_line`, `end_line` | — | Yes | Remove line range |
| `patch_lines` | `path`, `start_line`, `end_line`, `content` | — | Yes | Replace line range |
| `delete_request` | `path` | — | No | Phase 1 — returns confirmation token |
| `delete_confirm` | `token` | — | Yes | Phase 2 — executes after token validated |
| `delete_tree_request` | `path` | — | No | Phase 1 for directory tree |
| `delete_tree_confirm` | `token` | — | Yes | Phase 2 for directory tree |
| `set_permissions` | `path`, `mode` | — | No | Linux/macOS only, no-op Windows |

**Rules:**
- Validate entire op array before applying any operation
- Stop on first failure — never partially apply a batch
- Max 50 ops per call
- `dry_run=True` returns `"would_change"` list without touching disk
- `delete_request` / `delete_tree_request` always stop the batch and return a
  pending token — no other ops in the same batch execute until delete is resolved

**Phase 1 response (delete_request):**
```python
{
    "success": True,
    "op": "delete_pending",
    "pending": True,
    "confirmation_token": "del_a3f9b2c1",
    "expires_in_seconds": 300,
    "targets": [
        {"path": "/home/user/old.csv", "size_kb": 42, "type": "file"},
    ],
    "total_size_kb": 42,
    "warning": "Permanently deletes 1 item (42 KB). Cannot be undone.",
    "next_step": "Call fs_write with op=delete_confirm and token=del_a3f9b2c1 to proceed.",
    "progress": [...],
    "token_estimate": 110,
}
```

**Phase 2 response (delete_confirm):**
```python
{
    "success": True,
    "op": "delete_confirm",
    "deleted": ["/home/user/old.csv"],
    "backup": "~/.mcp_versions/old_2026-04-15T10-30-00Z.csv.bak",
    "progress": [...],
    "token_estimate": 75,
}
```

**Token rules:**
- Stored in-memory in `shared/confirm_store.py` (dict keyed by token string)
- Expires 300 seconds after creation
- Consumed on use — cannot be reused
- Server restart clears all pending tokens (user must re-request)
- Bulk delete: multiple paths in one `delete_request` → one combined token
- Invalid/expired token → error dict with hint to re-request

---

### Tool 4 — fs_index (VERIFY / INDEX)

```python
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False})
def fs_index(
    action: str = "query",          # "build"|"query"|"stats"|"clear"|"receipt"
    path: str = "",                 # directory to index / query / clear
    pattern: str = "",              # for action="query": filename pattern
    max_results: int = 50,
) -> dict:
    """Build/query file index or read operation receipt history."""
```

**Actions:**
- `"build"`: scan `path` recursively → write to `~/.mcp_fs_index/index.db`
- `"query"`: fast SQLite FTS5 lookup by filename pattern (no disk scan)
- `"stats"`: index metadata — file count, last_built, indexed roots
- `"clear"`: remove index entries for `path` subtree
- `"receipt"`: read operation history from `{path}.mcp_receipt.json`

**Index storage:** `~/.mcp_fs_index/index.db` (SQLite FTS5)
**Schema:** `(path TEXT, name TEXT, size INTEGER, mtime REAL, type TEXT)`

**`"receipt"` response:**
```python
{
    "success": True,
    "op": "fs_index",
    "action": "receipt",
    "file": "/home/user/projects/sales.csv",
    "history": [
        {
            "ts": "2026-04-15T10:30:00Z",
            "tool": "fs_write",
            "op": "write_file",
            "result": "created file",
            "backup": None,
        },
        {
            "ts": "2026-04-15T11:00:00Z",
            "tool": "fs_write",
            "op": "replace_text",
            "result": "replaced 3 occurrences",
            "backup": "~/.mcp_versions/sales_2026-04-15T11-00-00Z.csv.bak",
        },
    ],
    "progress": [...],
    "token_estimate": 140,
}
```

---

### Tool 5 — fs_manage (METADATA / SYSTEM)

```python
@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False})
def fs_manage(
    action: str,                    # "disk_usage"|"permissions"|"symlink_info"|"versions"
    path: str = "",
) -> dict:
    """Disk usage, permissions, symlink info, or snapshot version list."""
```

**Actions:**
- `"disk_usage"`: total/used/free for the filesystem containing `path`
- `"permissions"`: rwx bits, owner, group (Linux/macOS); ACL summary (Windows)
- `"symlink_info"`: is_symlink, target path, is_broken
- `"versions"`: list available snapshots for `path` from `~/.mcp_versions/`

**Note:** `fs_manage` is read-only. Permission changes go through `fs_write`
with `set_permissions` op (requires explicit user intent via the write tool).

---

### Tool 6 — fs_archive (ARCHIVE OPS)

```python
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": False, "openWorldHint": False})
def fs_archive(
    action: str,                    # "create"|"extract"|"list"
    path: str,                      # archive file path
    target: str = "",               # for create: source dir/files; for extract: output dir
    format_: str = "zip",           # "zip"|"tar.gz"
    dry_run: bool = False,
) -> dict:
    """Create or extract zip/tar.gz archives. Uses Python stdlib only."""
```

**Actions:**
- `"create"`: pack `target` (file or directory) into archive at `path`
- `"extract"`: unpack archive at `path` into `target` directory
- `"list"`: list archive contents without extracting

**Backend:** Python stdlib only — `zipfile` for zip, `tarfile` for tar.gz.
Zero external dependencies. Works offline on all platforms.

**Overwrite rule:** if `target` directory has existing files that would be
overwritten by extraction, returns an error with the conflicting paths listed.
User must either choose a different target or explicitly pass `overwrite=True`.


---

## 6. Deletion Confirmation Protocol (Safety Gate)

### Why Deletion Is Special

Deletion is the only irreversible operation in this server. Even with snapshots,
the user may not realize what is being deleted until it is gone. In multi-MCP
workflows, auto-approve can chain deletions silently across many files. This
protocol ensures a human always sees and approves deletions explicitly.

### How It Works

```
Phase 1 — Request (auto-approve safe)
  LLM calls fs_write with op=delete_request or op=delete_tree_request
  Server calculates targets, generates token, returns pending response
  Server does NOT delete anything
  LLM must present targets + warning to the user

Phase 2 — Confirm (requires human input)
  User sees what will be deleted and approves
  LLM calls fs_write with op=delete_confirm and the token
  Server validates token (exists + not expired)
  Server snapshots each target
  Server executes deletion
  Server returns confirmation with backup paths
```

### Why Auto-Approve Cannot Bypass This

Auto-approve in LM Studio means the MCP host executes tool calls without asking.
But Phase 1 **never deletes** — it only returns a pending state. The LLM receives
a response it must show to the user. It cannot fabricate a confirmation token
(tokens come from the server, not the LLM). Therefore auto-approve executes
Phase 1 automatically (safe), and Phase 2 still requires the user to see
Phase 1's output and instruct the LLM to proceed.

### Token Store (shared/confirm_store.py)

```python
# In-memory dict — cleared on server restart
_store: dict[str, dict] = {}

def create_token(targets: list[dict]) -> str:
    """Generate token, store with expiry. Returns token string."""

def validate_token(token: str) -> dict | None:
    """Returns token data if valid and unexpired, else None. Consumes on use."""

def cleanup_expired() -> None:
    """Remove expired tokens. Called on every fs_write invocation."""
```

Token format: `"del_"` + 8 random hex chars (e.g. `"del_a3f9b2c1"`)
Expiry: 300 seconds from creation
Consumption: token deleted from store after first successful Phase 2 use

### Scope of the Deletion Gate

| Operation | Requires confirmation | Reason |
|---|---|---|
| `delete` (file) | Always — two-phase | Irreversible |
| `delete_tree` (directory) | Always — two-phase | Irreversible, potentially massive |
| `move` (dst exists) | Error + hint to rename dst first | Could overwrite existing data |
| `write_file` (file exists) | Snapshot only, no confirmation | Reversible via restore |
| `extract_archive` (files exist) | Error + `overwrite=True` required | Explicit intent needed |
| `delete_lines` / `patch_lines` | Snapshot only, no confirmation | Reversible via restore |
| All other ops | No confirmation | Non-destructive or reversible |

### Bulk Delete Behaviour

Multiple paths in one `delete_request` → one combined confirmation token.
User sees all targets at once. Partial confirmation is not supported — either
all targets are confirmed or none are deleted.

---

## 7. Shared Module Contracts

### shared/platform_utils.py

```python
def is_constrained_mode() -> bool       # MCP_CONSTRAINED_MODE == "1"
def get_max_results() -> int            # 10 if constrained else 50
def get_max_lines() -> int              # 20 if constrained else 100
def get_max_tree_entries() -> int       # 100 if constrained else 500
def get_max_depth() -> int              # 3 if constrained else 5
def get_max_context_lines() -> int      # 2 if constrained else 5
def get_name_backend() -> str          # "everything"|"mdfind"|"locate"|"python"
def get_content_backend() -> str       # "ripgrep"|"python"
def get_platform() -> str              # "windows"|"macos"|"linux"
```

### shared/file_utils.py

```python
def resolve_path(file_path: str, must_exist: bool = False) -> Path
    # Rejects paths outside home dir (ValueError)
    # Handles Windows long paths (\\?\)
    # Raises ValueError with clear message on violation

def atomic_write(path: Path, content: str) -> None
    # Writes to temp file in same dir, then renames
    # Never leaves partial files on disk

def get_default_output_dir(input_path: str | None = None) -> Path
    # Returns input's parent dir if provided, else ~/Downloads
```

### shared/version_control.py

```python
def snapshot(file_path: str) -> str
    # Copies to ~/.mcp_versions/{stem}_{UTC_ts}{ext}.bak
    # Returns backup path string. Never raises.

def restore_version(file_path: str, timestamp: str) -> dict
def list_versions(file_path: str) -> list[dict]
```

### shared/progress.py

```python
def ok(msg: str, detail: str = "") -> dict    # {"icon": "✔", "msg": ..., "detail": ...}
def fail(msg: str, detail: str = "") -> dict  # {"icon": "✘", ...}
def info(msg: str, detail: str = "") -> dict  # {"icon": "ℹ", ...}
def warn(msg: str, detail: str = "") -> dict  # {"icon": "⚠", ...}
def undo(msg: str, detail: str = "") -> dict  # {"icon": "↩", ...}
```

### shared/receipt.py

```python
def append_receipt(file_path: str, tool: str, op: str,
                   result: str, backup: str | None) -> None
    # Never raises. Silently drops on I/O failure.

def read_receipt_log(file_path: str) -> list[dict]
```

### shared/patch_validator.py

```python
ALLOWED_OPS = {
    "write_file", "append_file", "create_dir", "move", "copy", "rename",
    "replace_text", "insert_after", "delete_lines", "patch_lines",
    "delete_request", "delete_confirm", "delete_tree_request",
    "delete_tree_confirm", "set_permissions",
}

def validate_ops(ops: list[dict]) -> list[str]
    # Returns list of error strings. Empty list = valid.
    # Checks: op key present, op in ALLOWED_OPS, required fields present,
    #         types correct, path fields non-empty, max 50 ops.
```

### shared/confirm_store.py

```python
def create_token(targets: list[dict]) -> str
    # Returns "del_" + 8 random hex chars. Stores with 300s expiry.

def validate_token(token: str) -> dict | None
    # Returns token data if valid + unexpired. Consumes token. Returns None if invalid.

def cleanup_expired() -> None
    # Removes all expired tokens. Called on every fs_write invocation.
```

---

## 8. Security Rules (STANDARDS.md §18)

### Path Traversal Prevention

First line of every engine function operating on a file path:
```python
path = resolve_path(file_path)          # raises ValueError if outside home
```

`resolve_path` rejects:
- Paths outside `Path.home()` (e.g. `/etc/passwd`, `C:\Windows\System32\`)
- Path traversal sequences (`../`, `..\`)
- UNC paths from user input on Windows

### Subprocess Security

All subprocess calls use argument lists, `shell=False`, and `timeout`:
```python
# Correct
result = subprocess.run(
    ["rg", "--line-number", pattern, str(path)],
    shell=False, capture_output=True, timeout=30, text=True,
)

# Never
subprocess.run(f"rg {pattern} {path}", shell=True)
```

### No eval / exec

Content pattern matching uses `re.compile()` with user pattern. Expression
evaluation forbidden. AST parsing with allowlist if ever needed.

### Sensitive Data in Responses

Use `Path(x).name` in progress messages — never full absolute paths.
No connection strings, credentials, or system internals in responses.

---

## 9. Error Handling Contract (STANDARDS.md §17)

Engine functions never raise to the caller. All exceptions → error dicts:

```python
def fs_query(...) -> dict:
    try:
        path = resolve_path(root_path)
        ...
        return {"success": True, "op": "fs_query", ...}
    except ValueError as e:
        return {"success": False, "error": str(e),
                "hint": "Ensure path is absolute and within your home directory."}
    except PermissionError:
        return {"success": False, "error": f"Permission denied: {Path(root_path).name}",
                "hint": "Check directory permissions or choose a path you own."}
    except Exception as e:
        return {"success": False, "error": str(e),
                "hint": "Use fs_query with a simpler pattern to narrow the scope."}
```

Hint rule: must complete "To fix this, ..." and name a specific tool or action.

---

## 10. Cross-Platform Compatibility (STANDARDS.md §28)

### Pathlib Everywhere

```python
# Wrong
path = base_dir + "/" + filename

# Correct
path = Path(base_dir) / filename
```

### Line Endings (.gitattributes)

```
* text=auto eol=lf
*.bat text eol=crlf
*.cmd text eol=crlf
```

### Windows Long Paths

```python
if sys.platform == "win32" and len(str(path)) > 200:
    path = Path("\\\\?\\" + str(path.resolve()))
```

### stdout is the MCP Channel

```python
# Wrong — corrupts MCP stdio channel
print("Scanning files...")

# Correct — stderr only
import logging, sys
logger = logging.getLogger(__name__)
logger.debug("Scanning files...")
```

`server.py` sets: `logging.basicConfig(stream=sys.stderr, level=logging.WARNING)`

### Atomic Writes

```python
import tempfile, shutil
with tempfile.NamedTemporaryFile(
    delete=False, dir=path.parent, suffix=path.suffix
) as tmp:
    tmp.write(content.encode("utf-8"))
    tmp_path = tmp.name
shutil.move(tmp_path, path)
```

### Platform Differences Handled in _basic_helpers.py

| Concern | Windows | macOS | Linux |
|---|---|---|---|
| Home dir | `%USERPROFILE%` | `~/` | `~/` |
| Path sep | `\` (pathlib handles) | `/` | `/` |
| Symlinks | Limited (requires privilege) | Full | Full |
| Permissions | ACL-based (no chmod) | POSIX | POSIX |
| Long paths | `\\?\` prefix if >200 chars | N/A | N/A |
| set_permissions op | no-op, returns warning | executes | executes |

---

## 11. Installation Entries (STANDARDS.md §31)

Install path: `~/.mcp_servers/mcp-filesystem` (all platforms)

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

## 12. Testing Requirements (STANDARDS.md §27)

Tests import `engine.py` directly. Never spin up an MCP server process.

### Fixtures

- `simple/` — flat dir, 10 files, clean ASCII names
- `messy/` — 4-level nesting, unicode filenames, symlinks, mixed extensions
- `large/` — 5,000+ files for truncation, index, and constrained mode tests

### Required Tests

**fs_query:**
- [ ] Finds files by glob pattern, returns paths only
- [ ] `grep_mode=True` returns line numbers and context
- [ ] `include_meta=True` adds size/mtime/mime per result
- [ ] `follow_symlinks=False` does not loop on circular symlinks
- [ ] Respects `max_results` cap
- [ ] Constrained mode reduces cap to 10
- [ ] No matches returns empty list + hint
- [ ] Path outside home → error dict
- [ ] Invalid root → error dict

**fs_read:**
- [ ] Content mode: correct line range returned
- [ ] Content mode: truncated at max_lines with `truncated=True` + hint
- [ ] Tree mode: respects depth limit
- [ ] Tree mode: truncates at max_tree_entries
- [ ] Meta mode: returns metadata, no content
- [ ] Meta mode with `changed_since`: returns `changed` bool + `mtime`
- [ ] Diff mode: unified diff returned between two files
- [ ] Diff mode: diff against snapshot timestamp
- [ ] Auto mode: file → content, directory → tree
- [ ] Binary file: metadata + hex preview, no raw bytes
- [ ] File not found → error dict with hint
- [ ] Path outside home → error dict

**fs_write (non-delete ops):**
- [ ] `write_file`: creates new file, content correct on re-read
- [ ] `write_file`: overwrites existing, snapshot created, backup in response
- [ ] `append_file`: content appended, original unchanged
- [ ] `create_dir`: nested directories created
- [ ] `move`: src gone, dst correct content
- [ ] `move` (dst exists): returns error, no move executed
- [ ] `copy`: src preserved, dst has same content
- [ ] `rename`: file renamed in same directory
- [ ] `replace_text`: occurrences replaced, snapshot created
- [ ] `insert_after`: lines inserted after match pattern
- [ ] `delete_lines`: line range removed, snapshot created
- [ ] `patch_lines`: line range replaced, snapshot created
- [ ] `set_permissions`: executes on Linux/macOS, no-op on Windows
- [ ] `dry_run=True`: disk unchanged, `would_change` in response
- [ ] Invalid op name: full array rejected, nothing applied
- [ ] Partial invalid batch: nothing applied
- [ ] Path outside home → error dict

**fs_write (deletion protocol):**
- [ ] `delete_request`: returns pending token, nothing deleted
- [ ] `delete_request`: targets list + total_size in response
- [ ] `delete_confirm`: valid token → file deleted, backup created
- [ ] `delete_confirm`: expired token → error dict with hint to re-request
- [ ] `delete_confirm`: invalid token → error dict
- [ ] `delete_confirm`: consumed token → error dict (cannot reuse)
- [ ] `delete_tree_request`: returns combined token for all targets
- [ ] `delete_tree_confirm`: all targets deleted, all backups created
- [ ] Bulk delete: multiple paths → one combined token
- [ ] `dry_run=True` on delete_request: shows targets, no token generated

**fs_index:**
- [ ] `build`: creates `~/.mcp_fs_index/index.db`
- [ ] `query`: returns matches from index (no disk scan)
- [ ] `stats`: returns file count and last_built timestamp
- [ ] `clear`: removes entries for path subtree
- [ ] `receipt`: returns operation history from `.mcp_receipt.json`
- [ ] Stale index: `index_age_hours` warning in query response

**fs_manage:**
- [ ] `disk_usage`: returns total/used/free correctly
- [ ] `permissions`: returns rwx on Linux/macOS, ACL summary on Windows
- [ ] `symlink_info`: is_symlink, target, is_broken
- [ ] `versions`: returns list of available snapshots for path

**fs_archive:**
- [ ] `create` zip: archive created, correct contents
- [ ] `create` tar.gz: archive created, correct contents
- [ ] `extract` zip: files extracted to target dir
- [ ] `extract` tar.gz: files extracted to target dir
- [ ] `list`: returns archive contents without extracting
- [ ] `extract` with conflicting files and no `overwrite=True` → error
- [ ] `dry_run=True`: shows what would be created/extracted

### Coverage Requirements

| Module | Minimum |
|---|---|
| `shared/` | 100% |
| `engine.py` | ≥90% |
| `_basic_*.py` | ≥90% |
| Error paths | All documented conditions tested |
| Happy paths | All 6 tools, all modes, all ops |

### CI Matrix

```yaml
matrix:
  os: [ubuntu-22.04, macos-latest, windows-latest]
env:
  MCP_CONSTRAINED_MODE: "1"
  PYTHONPATH: "."
```

`fail-fast: false` — all platforms must run even if one fails.


---

## 13. What the AI Must Never Do

1. Print to stdout in `server.py`, `engine.py`, or any `_basic_*.py` file
2. Return a plain string, list, None, or boolean from any tool — always `dict`
3. Write to disk without calling `snapshot()` first on destructive ops
4. Swallow exceptions silently — every exception becomes an error dict with hint
5. Use `eval()` or `exec()` on any user-provided input
6. Use `shell=True` in any subprocess call
7. Use string concatenation for file paths — always `pathlib.Path / operator`
8. Put business logic in `server.py` — tool bodies are single-line engine calls
9. Exceed 8 tools in this server — current count is 6, hard budget is 8
10. Hardcode size/line/result limits — always call `get_max_*()` from `platform_utils`
11. Use `git pull` in mcp.json — always `git fetch origin + git reset --hard FETCH_HEAD`
12. Use user-provided paths without calling `resolve_path()` first
13. Require internet access for any core file operation
14. Return raw binary data through the MCP channel
15. Write tool docstrings longer than 80 characters
16. Execute a deletion without a valid confirmation token from Phase 1
17. Allow a confirmation token to be reused after Phase 2
18. Allow a deletion to execute without calling `snapshot()` first
19. Let a `delete_request` op execute alongside other ops in the same batch —
    the batch must stop at the delete_request and return the pending token
20. Use project-specific env var names — always `MCP_CONSTRAINED_MODE`

---

## 14. Return Value Contract (STANDARDS.md §16)

Every tool returns a dict. Required fields in every response:

| Field | Type | When | Purpose |
|---|---|---|---|
| `"success"` | `bool` | Always | Model checks this first |
| `"op"` | `str` | Always | Confirms which operation ran |
| `"error"` | `str` | On failure | Human-readable reason |
| `"hint"` | `str` | On failure | Actionable recovery instruction |
| `"backup"` | `str` | After destructive write | Path to snapshot |
| `"progress"` | `list` | Always | Step-by-step execution log |
| `"token_estimate"` | `int` | Always | `len(str(response)) // 4` |
| `"truncated"` | `bool` | On bounded reads | Always explicit, never absent |
| `"pending"` | `bool` | On delete_request | Signals confirmation required |
| `"confirmation_token"` | `str` | On delete_request | Token for Phase 2 |

---

## 15. Progress Tracker

### Phase 1 — Shared Modules
- [ ] `shared/__init__.py`
- [ ] `shared/platform_utils.py` — OS, constrained mode, backend detection
- [ ] `shared/file_utils.py` — resolve_path, atomic_write, get_default_output_dir
- [ ] `shared/version_control.py` — snapshot, restore_version, list_versions
- [ ] `shared/progress.py` — ok, fail, info, warn, undo
- [ ] `shared/receipt.py` — append_receipt, read_receipt_log
- [ ] `shared/patch_validator.py` — validate op arrays, ALLOWED_OPS set
- [ ] `shared/confirm_store.py` — create_token, validate_token, cleanup_expired

### Phase 2 — Engine Sub-Modules
- [ ] `servers/fs_basic/__init__.py`
- [ ] `servers/fs_basic/_basic_helpers.py` — constants, _error, platform table
- [ ] `servers/fs_basic/_basic_query.py` — fs_query: name + content + grep
- [ ] `servers/fs_basic/_basic_read.py` — fs_read: content/tree/meta/diff/changed_since
- [ ] `servers/fs_basic/_basic_write.py` — fs_write: all ops + deletion protocol
- [ ] `servers/fs_basic/_basic_index.py` — fs_index: SQLite FTS5 + receipt
- [ ] `servers/fs_basic/engine.py` — thin router, zero MCP imports

### Phase 3 — Server + Config
- [ ] `servers/fs_basic/server.py` — 6 tools, thin wrappers, transport modes
- [ ] `servers/fs_basic/pyproject.toml`
- [ ] Root `pyproject.toml` workspace config
- [ ] `.python-version` = 3.12
- [ ] `.gitattributes` — lf for all, crlf for bat/cmd
- [ ] `.editorconfig`
- [ ] `verify_tool_docstrings.py` — CI docstring length checker

### Phase 4 — Tests
- [ ] `tests/fixtures/simple/` — 10 clean files
- [ ] `tests/fixtures/messy/` — unicode, nested, symlinks
- [ ] `tests/fixtures/large/` — 5,000+ files
- [ ] `tests/conftest.py`
- [ ] `tests/test_fs_basic.py` — all tools, all modes, all ops, all error paths

### Phase 5 — CI/CD
- [ ] `.github/workflows/ci.yml` — lint + format + pyright + docstrings + test
- [ ] `.github/workflows/release.yml` — CI matrix + GitHub release on tag
- [ ] CI passes on ubuntu-22.04
- [ ] CI passes on macos-latest
- [ ] CI passes on windows-latest
- [ ] `MCP_CONSTRAINED_MODE: "1"` and `PYTHONPATH: "."` set in CI env

### Phase 6 — Distribution
- [ ] `install/install.sh` (POSIX sh, `#!/bin/sh`)
- [ ] `install/install.bat`
- [ ] `install/mcp_config_writer.py` — writes LM Studio + Claude Desktop entries
- [ ] mcp.json Windows PowerShell entry tested end-to-end
- [ ] mcp.json bash entry tested end-to-end on macOS or Linux
- [ ] README.md — follows STANDARDS.md §35 section order exactly

### Definition of Done
- [ ] `verify_tool_docstrings.py` passes — all ≤80 chars
- [ ] No file exceeds 1,000 lines
- [ ] `uv run pytest` — all pass on all 3 platforms
- [ ] `uv run ruff check .` — no errors
- [ ] `uv run ruff format --check .` — no reformatting needed
- [ ] `uv run pyright servers/ shared/` — no errors
- [ ] Manual test in LM Studio (9B model) — 6-tool set loads, loop works
- [ ] Deletion protocol tested: Phase 1 returns pending, Phase 2 deletes
- [ ] Auto-approve confirmed: Phase 1 safe, Phase 2 requires user input
- [ ] `MCP_CONSTRAINED_MODE=1` enforces smaller limits correctly
- [ ] Context window not exceeded on 10-step file management task

---

*CLAUDE.md version: 2.0*
*Standards: STANDARDS.md v5.1 (azzindani/Standards)*
*Last updated: 2026-04-16*
*Changes from v1.0: Scope changed to Option B (File Management Layer),
tool count expanded to 6, grep_mode added to fs_query, diff mode added to
fs_read, receipt action added to fs_index, fs_manage and fs_archive added,
deletion confirmation protocol fully specified, in-place editing ops added,
archive ops added, symlink policy added, change detection added, permissions op
added, confirm_store.py added to shared modules.*
