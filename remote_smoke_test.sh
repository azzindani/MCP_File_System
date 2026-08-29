#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# MCP_File_System (fs_basic) — remote smoke test.
#
# NOT part of pytest / CI (see CLAUDE.md §13 "Remote smoke tests"). This
# script is the separate, manual/on-demand check that actually exercises the
# deployed HTTP endpoint: real auth enforcement + a real handwritten-prompt-
# style tool call with a real file, against the real public domain.
#
# Usage:
#   ./remote_smoke_test.sh                      # reads FS_API_KEY from .env
#   FS_API_KEY=sk-... ./remote_smoke_test.sh     # or pass it directly
#   DOMAIN=http://localhost:8801 ./remote_smoke_test.sh   # test a different target
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DOMAIN="${DOMAIN:-https://fs.casava.space}"
# Read the key out of .env without executing it. `source` runs every line of
# the file, so a line that is not a KEY=VALUE assignment is a command; that has
# already turned a stray summary line into a file named after a secret. A plain
# read of one assignment cannot do that.
if [ -z "${FS_API_KEY:-}" ] && [ -f .env ]; then
  FS_API_KEY=$(sed -n 's/^[[:space:]]*FS_API_KEY[[:space:]]*=[[:space:]]*//p' .env | tail -n1 | tr -d '\042\047\r')
fi
KEY="${FS_API_KEY:?Set FS_API_KEY (env var or .env file) before running}"
TEST_PATH="/tmp/remote-smoke-test/notes.md"

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; exit 1; }

echo "Target: $DOMAIN"
echo
echo "== auth enforcement =="

code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$DOMAIN/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}')
[ "$code" = "401" ] && pass "no token -> 401" || fail "no token -> expected 401, got $code"

SID=$(curl -s -i -X POST "$DOMAIN/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}' \
  | grep -i mcp-session-id | tr -d '\r' | awk '{print $2}')
[ -n "$SID" ] && pass "valid token -> session established" || fail "valid token -> no session id returned"

curl -s -X POST "$DOMAIN/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $KEY" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}' > /dev/null

echo
echo "== prompt: \"write a note to $TEST_PATH\" -> fs_write =="
CONTENT="# Remote smoke test\n\nWritten by remote_smoke_test.sh over $DOMAIN.\n"
RESULT=$(curl -s -X POST "$DOMAIN/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $KEY" -H "mcp-session-id: $SID" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"fs_write\",\"arguments\":{\"ops\":[{\"op\":\"write_file\",\"path\":\"$TEST_PATH\",\"content\":\"$CONTENT\"}]}}}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' && pass "fs_write created a real file on the host" || fail "unexpected result: $RESULT"

echo
echo "== prompt: \"read it back\" -> fs_read =="
RESULT=$(curl -s -X POST "$DOMAIN/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $KEY" -H "mcp-session-id: $SID" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"fs_read\",\"arguments\":{\"path\":\"$TEST_PATH\"}}}")
echo "$RESULT" | grep -qi 'Remote smoke test' && pass "fs_read read the real file back correctly" || fail "unexpected result: $RESULT"

call() {
  local id="$1" name="$2" args="$3"
  curl -s -X POST "$DOMAIN/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -H "Authorization: Bearer $KEY" -H "mcp-session-id: $SID" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":$id,\"method\":\"tools/call\",\"params\":{\"name\":\"$name\",\"arguments\":$args}}"
}

echo
echo "== prompt: \"find the word 'smoke' inside /tmp/remote-smoke-test\" -> fs_query (grep mode) =="
RESULT=$(call 5 fs_query '{"pattern":"*","path":"/tmp/remote-smoke-test","content":"smoke","grep_mode":true}')
echo "$RESULT" | grep -qi 'notes.md' && pass "fs_query grep-mode found the real match inside notes.md" || fail "unexpected result: $RESULT"

echo
echo "== prompt: \"build a search index of /tmp/remote-smoke-test\" -> fs_index (build) =="
RESULT=$(call 6 fs_index '{"action":"build","path":"/tmp/remote-smoke-test"}')
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' && pass "fs_index built a real index over the real directory" || fail "unexpected result: $RESULT"

echo
echo "== prompt: \"query that index for notes.md\" -> fs_index (query) =="
RESULT=$(call 7 fs_index '{"action":"query","path":"/tmp/remote-smoke-test","pattern":"notes.md"}')
echo "$RESULT" | grep -qi 'notes.md' && pass "fs_index query found the real indexed file" || fail "unexpected result: $RESULT"

echo
echo "== prompt: \"how much disk space is that file using?\" -> fs_manage (disk_usage) =="
RESULT=$(call 8 fs_manage "{\"action\":\"disk_usage\",\"path\":\"$TEST_PATH\"}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' && pass "fs_manage(disk_usage) reported real size info for the real file" || fail "unexpected result: $RESULT"

echo
echo "== prompt: \"zip up /tmp/remote-smoke-test\" -> fs_archive (create) =="
RESULT=$(call 9 fs_archive '{"action":"create","path":"/tmp/remote-smoke-test-archive.zip","target":"/tmp/remote-smoke-test","format_":"zip"}')
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' && pass "fs_archive created a real .zip on the host" || fail "unexpected result: $RESULT"

echo
echo "===== boundary regression: truncated must be exact at the result-cap, not off-by-one ====="
echo "A prior bug computed 'truncated' from a count already capped during collection,"
echo "which is a false positive exactly when the true count equals the cap. Verified"
echo "here against the real deployed endpoint by hitting the cap exactly, then by one."

echo
echo "== read (content mode): exactly max_lines vs. one over =="
CONTENT_100=$(python3 -c "
import json
print(json.dumps(''.join(f'line {i}\n' for i in range(1, 101))))
")
RESULT=$(call 20 fs_write "{\"ops\":[{\"op\":\"write_file\",\"path\":\"/tmp/remote-smoke-test/boundary_100.txt\",\"content\":$CONTENT_100}]}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' && pass "wrote a real 100-line file" || fail "unexpected result: $RESULT"
RESULT=$(call 21 fs_read '{"path":"/tmp/remote-smoke-test/boundary_100.txt"}')
echo "$RESULT" | grep -Eq '\\?"total_lines\\?":[[:space:]]*100' || fail "expected a 100-line file, got: $RESULT"
echo "$RESULT" | grep -Eq '\\?"truncated\\?":[[:space:]]*false' && pass "reading exactly 100 lines (the max_lines cap) is NOT flagged truncated" || fail "false positive at exact cap: $RESULT"

CONTENT_101=$(python3 -c "
import json
print(json.dumps(''.join(f'line {i}\n' for i in range(1, 102))))
")
RESULT=$(call 22 fs_write "{\"ops\":[{\"op\":\"write_file\",\"path\":\"/tmp/remote-smoke-test/boundary_101.txt\",\"content\":$CONTENT_101}]}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' && pass "wrote a real 101-line file" || fail "unexpected result: $RESULT"
RESULT=$(call 23 fs_read '{"path":"/tmp/remote-smoke-test/boundary_101.txt"}')
echo "$RESULT" | grep -Eq '\\?"truncated\\?":[[:space:]]*true' && pass "reading a 101-line file (1 over the cap) IS flagged truncated" || fail "expected truncated:true, got: $RESULT"

echo
echo "== read (tree mode): exactly max_tree_entries vs. one over =="
TREE_DIR="/tmp/remote-smoke-test/tree_boundary"
# Start from an empty directory: these are exact-count assertions, so files
# left by a previous run (the extra_dir below, the 6th grep file) would make
# the "exactly at the cap" case see one entry too many and fail. The script
# has to be re-runnable.
docker exec "${CONTAINER:-mcp-filesystem-fs-basic}" rm -rf "$TREE_DIR"
for batch in 0 1 2 3 4; do
  OPS=$(python3 -c "
import json
b = $batch
ops = [{'op': 'write_file', 'path': f'$TREE_DIR/f{b*50+i:04d}.txt', 'content': ''} for i in range(50)]
print(json.dumps(ops))
")
  RESULT=$(call $((30 + batch)) fs_write "{\"ops\":$OPS}")
  echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' || fail "batch $batch file creation failed: $RESULT"
done
pass "created 250 real files under $TREE_DIR (write_file also snapshots a .mcp_receipt.json per file, so 250 files = 500 tree entries)"
RESULT=$(call 41 fs_read "{\"path\":\"$TREE_DIR\",\"mode\":\"tree\",\"depth\":1}")
echo "$RESULT" | grep -Eq '\\?"returned\\?":[[:space:]]*500' || fail "expected 500 entries returned, got: $RESULT"
echo "$RESULT" | grep -Eq '\\?"truncated\\?":[[:space:]]*false' && pass "tree of exactly 500 entries (the max_tree_entries cap) is NOT flagged truncated" || fail "false positive at exact cap: $RESULT"

RESULT=$(call 42 fs_write "{\"ops\":[{\"op\":\"create_dir\",\"path\":\"$TREE_DIR/extra_dir\"}]}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' || fail "501st entry (extra dir, no receipt) creation failed: $RESULT"
RESULT=$(call 43 fs_read "{\"path\":\"$TREE_DIR\",\"mode\":\"tree\",\"depth\":1}")
echo "$RESULT" | grep -Eq '\\?"truncated\\?":[[:space:]]*true' && pass "tree of 501 entries (1 over the cap) IS flagged truncated" || fail "expected truncated:true, got: $RESULT"

echo
echo "== fs_query (grep mode): exactly max_results vs. one over =="
GREP_DIR="/tmp/remote-smoke-test/grep_boundary"
docker exec "${CONTAINER:-mcp-filesystem-fs-basic}" rm -rf "$GREP_DIR"
OPS=$(python3 -c "
import json
ops = [{'op': 'write_file', 'path': f'$GREP_DIR/m{i}.txt', 'content': 'boundarytoken'} for i in range(5)]
print(json.dumps(ops))
")
RESULT=$(call 50 fs_write "{\"ops\":$OPS}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' && pass "created 5 real files containing the grep target string" || fail "$RESULT"
RESULT=$(call 51 fs_query "{\"pattern\":\"*\",\"path\":\"$GREP_DIR\",\"content\":\"boundarytoken\",\"grep_mode\":true,\"max_results\":5}")
echo "$RESULT" | grep -Eq '\\?"truncated\\?":[[:space:]]*false' && pass "grep matching exactly 5 files (max_results=5) is NOT flagged truncated" || fail "false positive at exact cap: $RESULT"

RESULT=$(call 52 fs_write "{\"ops\":[{\"op\":\"write_file\",\"path\":\"$GREP_DIR/m5.txt\",\"content\":\"boundarytoken\"}]}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' || fail "6th file creation failed: $RESULT"
RESULT=$(call 53 fs_query "{\"pattern\":\"*\",\"path\":\"$GREP_DIR\",\"content\":\"boundarytoken\",\"grep_mode\":true,\"max_results\":5}")
echo "$RESULT" | grep -Eq '\\?"truncated\\?":[[:space:]]*true' && pass "grep matching 6 files with max_results=5 IS flagged truncated" || fail "expected truncated:true, got: $RESULT"

echo
echo "== hybrid file exchange (remote-only behaviour) =="
# Only meaningful against a deployment that sets MCP_OUTPUT_DIR /
# MCP_PUBLIC_BASE_URL / MCP_FETCH_URLS — exactly what pytest cannot check,
# since pytest never spins up a server or touches the network.
CONTAINER="${CONTAINER:-mcp-filesystem-fs-basic}"
SHARED_DIR=$(docker exec "$CONTAINER" printenv MCP_OUTPUT_DIR 2>/dev/null || true)
if [ -z "$SHARED_DIR" ]; then
  echo "  SKIP: MCP_OUTPUT_DIR is unset on $CONTAINER — nothing to verify"
else
  RESULT=$(call 60 fs_write "{\"ops\":[{\"op\":\"write_file\",\"path\":\"$SHARED_DIR/smoke_exchange.txt\",\"content\":\"shared\"}]}")
  echo "$RESULT" | grep -q 'public_url' && pass "a write into the shared dir came back with a public_url" || fail "no public_url: $RESULT"
  MODE=$(docker exec "$CONTAINER" stat -c '%a' "$SHARED_DIR/smoke_exchange.txt" 2>/dev/null)
  case "$MODE" in
    *[4567]) pass "written file is readable by the file server sharing the dir (mode $MODE)" ;;
    *) fail "mode $MODE leaves the file unreadable to anything else sharing the directory" ;;
  esac

  # A *sibling* endpoint's public /health, never this server's own: fetching
  # its own public URL deadlocks, because the tool call occupies the worker
  # that would have to serve the request, and the fetch dies on the timeout.
  RESULT=$(call 61 fs_write "{\"ops\":[{\"op\":\"download\",\"url\":\"https://math.casava.space/health\",\"path\":\"$SHARED_DIR/smoke_download.json\"}]}")
  if echo "$RESULT" | grep -q "does not fetch URLs"; then
    echo "  SKIP: MCP_FETCH_URLS is not enabled on $CONTAINER"
  else
    echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' && pass "download op fetched a real URL to a real path" || fail "download -> $RESULT"
    docker exec "$CONTAINER" grep -q 'status' "$SHARED_DIR/smoke_download.json" && pass "downloaded file holds the real remote content" || fail "downloaded file is empty or wrong"
  fi

  RESULT=$(call 62 fs_write "{\"ops\":[{\"op\":\"download\",\"url\":\"http://169.254.169.254/latest/meta-data/\",\"path\":\"$SHARED_DIR/ssrf.txt\"}]}")
  if echo "$RESULT" | grep -q "non-public address"; then
    pass "SSRF guard refused the link-local metadata address"
  elif echo "$RESULT" | grep -q "does not fetch URLs"; then
    echo "  SKIP: URL fetching disabled, guard not reachable"
  else
    fail "SSRF guard did not fire -> $RESULT"
  fi
  docker exec "$CONTAINER" sh -c "rm -f '$SHARED_DIR'/smoke_exchange.txt* '$SHARED_DIR'/smoke_download.json* '$SHARED_DIR'/ssrf.txt*"
fi

echo
echo "===== the snapshots are usable, not just taken ====="
echo "Every destructive op takes a snapshot and fs_manage action=versions lists them,"
echo "but until now nothing could put one back: restore_version had no caller. This"
echo "walks the whole round trip against the deployed endpoint -- edit, list, restore --"
echo "and feeds the listing's own timestamp straight into the restore."

RESTORE_PATH="/tmp/remote-smoke-test/restore_me.txt"
RESULT=$(call 70 fs_write "{\"ops\":[{\"op\":\"write_file\",\"path\":\"$RESTORE_PATH\",\"content\":\"keep me\\n\"}]}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' || fail "could not seed the restore fixture: $RESULT"

RESULT=$(call 71 fs_write "{\"ops\":[{\"op\":\"replace_text\",\"path\":\"$RESTORE_PATH\",\"find\":\"keep me\",\"replace\":\"oops\"}]}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' && pass "edited the file, which takes a snapshot" || fail "replace_text -> $RESULT"

RESULT=$(call 72 fs_manage "{\"action\":\"versions\",\"path\":\"$RESTORE_PATH\"}")
echo "$RESULT" | grep -q 'timestamp' && pass "the version listing carries the timestamp a restore takes" || fail "listing has no timestamp field: $RESULT"
RESULT=$(call 73 fs_write "{\"ops\":[{\"op\":\"restore\",\"path\":\"$RESTORE_PATH\"}]}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' && pass "restore op put the snapshot back" || fail "restore -> $RESULT"

RESULT=$(call 74 fs_read "{\"path\":\"$RESTORE_PATH\",\"mode\":\"content\"}")
echo "$RESULT" | grep -q 'keep me' && pass "the file holds its pre-edit content again" || fail "restore did not bring the content back: $RESULT"

RESULT=$(call 75 fs_write "{\"ops\":[{\"op\":\"append_file\",\"path\":\"$RESTORE_PATH\",\"content\":\"appended\\n\"}]}")
echo "$RESULT" | grep -q '\.bak' && pass "append_file now reports a backup, like every other content op" || fail "append_file still reports no backup: $RESULT"

echo
echo "===== an op field this server does not take is refused, not dropped ====="
echo "strict_args guards each tool's own arguments, but fs_write declares two -- ops"
echo "and dry_run -- so everything that varies a write sits one level below it. A"
echo "misspelled flag used to be discarded: use_regex made 'X+' a literal search and"
echo "the caller was told the pattern is missing, and encoding= wrote the base64 text"
echo "into the file instead of the bytes it stands for, both under success: true."

FIELD_PATH="/tmp/remote-smoke-test/op_fields.txt"
RESULT=$(call 76 fs_write "{\"ops\":[{\"op\":\"write_file\",\"path\":\"$FIELD_PATH\",\"content\":\"aXXbXXc\\n\"}]}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' || fail "could not seed the op-field fixture: $RESULT"

RESULT=$(call 77 fs_write "{\"ops\":[{\"op\":\"replace_text\",\"path\":\"$FIELD_PATH\",\"find\":\"X+\",\"replace\":\"-\",\"use_regex\":true}]}")
if echo "$RESULT" | grep -q 'use_regex'; then
  echo "$RESULT" | grep -q "accepts:" && pass "use_regex refused, and the refusal lists what replace_text accepts" || fail "refused without naming the accepted fields: $RESULT"
else
  fail "use_regex was dropped instead of refused -> $RESULT"
fi

RESULT=$(call 78 fs_read "{\"path\":\"$FIELD_PATH\",\"mode\":\"content\"}")
echo "$RESULT" | grep -q 'aXXbXXc' && pass "the refused call left the file untouched" || fail "the file changed under a refused call: $RESULT"

RESULT=$(call 79 fs_write "{\"ops\":[{\"op\":\"replace_text\",\"path\":\"$FIELD_PATH\",\"find\":\"X+\",\"replace\":\"-\",\"regex\":true}]}")
echo "$RESULT" | grep -Eq 'success\\?":[[:space:]]*true' && pass "the documented spelling still applies the regex" || fail "regex=true -> $RESULT"

RESULT=$(call 80 fs_write "{\"ops\":[{\"op\":\"write_file\",\"path\":\"/tmp/remote-smoke-test/op_fields.bin\",\"content\":\"aGVsbG8=\",\"encoding\":\"base64\"}]}")
echo "$RESULT" | grep -q 'content_encoding' && pass "encoding= refused, and the refusal names content_encoding" || fail "encoding= was dropped instead of refused -> $RESULT"

echo
echo "ALL 6 TOOLS + boundary regression + restore round trip PASSED against $DOMAIN"
