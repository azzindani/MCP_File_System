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
if [ -f .env ]; then
  set -a; source .env; set +a
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
echo "ALL 6 TOOLS PASSED against $DOMAIN"
