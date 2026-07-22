#!/bin/bash
# Live acceptance matrix for the post-tournament lockdown (Jul 21).
# Usage: scripts/verify_lockdown.sh [BASE_URL]
# Token (optional, enables the authorized half): ~/.wc26_admin_token
#
# Phase 1 sweeps EVERY mutating route from the OpenAPI document with no
# credentials — all must 403. Phase 2 (token present) proves the critical
# operator paths work WITH credentials, including exactly one test alert.
set -u
BASE="${1:-https://wc26-bet-suggester-production.up.railway.app}"
TOKEN=""
[ -f "$HOME/.wc26_admin_token" ] && TOKEN="$(tr -d '\r\n' < "$HOME/.wc26_admin_token")"
FAIL=0

code() {  # method path [token] [body]
  local m="$1" p="$2" t="${3:-}" b="${4:-}"
  local args=(-sS --max-time 30 -o /dev/null -w '%{http_code}' -X "$m")
  [ -n "$t" ] && args+=(-H "X-Admin-Token: $t")
  [ -n "$b" ] && args+=(-H "Content-Type: application/json" -d "$b")
  curl "${args[@]}" "$BASE$p"
}

expect() {  # description got want
  if [ "$2" = "$3" ]; then echo "  ok   $1 -> $2"
  else echo "  FAIL $1 -> got $2, want $3"; FAIL=1; fi
}

echo "== phase 0: public reads stay open =="
expect "GET /api/health"   "$(code GET /api/health)"   200
expect "GET /api/bots"     "$(code GET /api/bots)"     200
expect "GET /api/bracket"  "$(code GET /api/bracket)"  200

echo "== phase 1: every OpenAPI mutation, no credentials -> 403 =="
MUTS=$(curl -sS --max-time 30 "$BASE/openapi.json" | python3 -c "
import json, sys
spec = json.load(sys.stdin)
for path, item in sorted(spec.get('paths', {}).items()):
    for method in ('post', 'put', 'patch', 'delete'):
        if method in item:
            p = path.replace('{match_id}', 'FINAL').replace('{position_id}', '1').replace('{market_id}', 'X').replace('{watch_id}', '1')
            print(method.upper(), p)")
if [ -z "$MUTS" ]; then echo "  FAIL could not enumerate OpenAPI mutations"; FAIL=1; fi
while read -r m p; do
  [ -z "$m" ] && continue
  expect "$m $p (anon)"          "$(code "$m" "$p" "" '{}')" 403
done <<< "$MUTS"
expect "empty token header"      "$(code POST /api/settings 2>/dev/null)" 403
expect "wrong token"             "$(code POST /api/settings wrongtoken '{}')" 403
BAD=$(curl -sS --max-time 30 -o /dev/null -w '%{http_code}' -X POST \
      -H "Authorization: Basic abc" -H "Content-Type: application/json" \
      -d '{}' "$BASE/api/settings")
expect "malformed Authorization" "$BAD" 403

if [ -z "$TOKEN" ]; then
  echo "== phase 2 skipped (no ~/.wc26_admin_token) =="
else
  echo "== phase 2: operator paths with credentials =="
  R=$(code POST /api/settings "$TOKEN" '{"min_edge":0.05,"min_confidence":0.45,"min_volume":1000}')
  expect "settings with token"   "$R" 200
  R=$(code POST /api/bots/restore "$TOKEN" '{"positions":[]}')
  expect "restore with token"    "$R" 200
  R=$(code POST /api/refresh-all "$TOKEN")
  expect "refresh-all with token (1st)" "$R" 200
  R=$(code POST /api/refresh-all "$TOKEN")
  expect "refresh-all with token (2nd, limited)" "$R" 429
  R=$(code POST /api/alerts/test "$TOKEN")
  expect "alerts/test with token (expect 1 notification)" "$R" 200
fi

echo "== state =="
POS=$(curl -sS --max-time 30 "$BASE/api/bots" | python3 -c "
import json,sys;d=json.load(sys.stdin)
print(sum(len(b['open'])+len(b['closed']) for b in d['bots']))")
CHAMP=$(curl -sS --max-time 30 "$BASE/api/bracket" | python3 -c "
import json,sys;print(json.load(sys.stdin).get('champion'))")
expect "ledger 84/84"      "$POS"   84
expect "champion Spain"    "$CHAMP" Spain

[ $FAIL -eq 0 ] && echo "ALL CHECKS PASSED" || echo "CHECKS FAILED"
exit $FAIL
