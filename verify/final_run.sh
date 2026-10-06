#!/bin/bash
# Wait until the public close-1 archive lists sweep >= 2556, then run the verifier.
# GET only. No prompts. Writes only under this directory (verify/). Safe to re-run:
# the fetch stage skips records already present with the right size.
# The report stage (close1_verify_public.py) locates the final board by the signed
# d-close1-pnl post with t="standings". That post has no sweep number. A pnl post
# that still carries n is the fallback when no standings post is in the export.

set -u

VER="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$VER/.." && pwd)"
LOG="$VER/final-run.log"
PY="/opt/homebrew/bin/python3"
URL="https://challenges.technocore.chat/close-1/index.json"
POLL="$VER/.final-poll-index.json"
HDR="$VER/.final-poll-index.hdr"
NEED=2556
INTERVAL=600
START=$(date +%s)
DEADLINE=$((START + 24 * 60 * 60))

log() {
  local line
  line="$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*"
  printf '%s\n' "$line" >> "$LOG"
  printf '%s\n' "$line"
}

give_up() {
  log "giving up after 24h: highest sweep listed by index.json is ${1}; needed >= ${NEED}. The verifier was not started."
  exit 1
}

# Sleep up to $1 seconds, but not past the deadline. Returns 1 once the deadline has passed.
sleep_capped() {
  local want=$1 now left
  now=$(date +%s)
  left=$((DEADLINE - now))
  if (( left <= 0 )); then
    return 1
  fi
  if (( want > left )); then
    want=$left
  fi
  if (( want > 0 )); then
    sleep "$want"
  fi
  return 0
}

highest_sweep() {
  "$PY" - "$POLL" << 'PY'
import json, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        idx = json.load(f)
    sweeps = idx.get("sweeps") or []
    ns = [e["n"] for e in sweeps if isinstance(e, dict) and type(e.get("n")) is int]
except (OSError, ValueError, TypeError, KeyError) as exc:
    print(f"unreadable: {exc}", file=sys.stderr)
    sys.exit(2)
if not ns:
    print("unreadable: no sweeps", file=sys.stderr)
    sys.exit(2)
print(max(ns))
PY
}

log "final run: waiting for ${URL} to list sweep >= ${NEED}; checking every ${INTERVAL}s; deadline $(date -u -r "$DEADLINE" '+%Y-%m-%dT%H:%M:%SZ')"

last_max="none"
backoff=30
while true; do
  now=$(date +%s)
  if (( now >= DEADLINE )); then
    give_up "$last_max"
  fi

  http="000"
  http=$(curl -sS --max-time 180 \
    -A "close1-verify-public/1.0 (read-only)" \
    -D "$HDR" -o "$POLL" -w "%{http_code}" \
    "$URL" 2>>"$LOG") || http="000"

  if [[ "$http" == "200" ]]; then
    if max=$(highest_sweep 2>>"$LOG"); then
      last_max=$max
      log "index.json highest sweep ${max} (HTTP 200)"
      if (( max >= NEED )); then
        break
      fi
      backoff=30
      log "sweep ${max} < ${NEED}; next check in ${INTERVAL}s"
      sleep_capped "$INTERVAL" || give_up "$last_max"
      continue
    fi
    log "index.json HTTP 200 but the sweep list was unreadable; backing off ${backoff}s"
    sleep_capped "$backoff" || give_up "$last_max"
    if (( backoff < INTERVAL )); then
      backoff=$((backoff * 2))
      if (( backoff > INTERVAL )); then
        backoff=$INTERVAL
      fi
    fi
    continue
  fi

  if [[ "$http" == "429" || "$http" == 5* ]]; then
    ra=$(awk 'BEGIN{IGNORECASE=1} /^Retry-After:/ {gsub(/\r/,"",$2); print $2; exit}' "$HDR" 2>/dev/null || true)
    if [[ "$ra" =~ ^[0-9]+$ ]]; then
      wait_s=$ra
    else
      wait_s=$backoff
    fi
    log "index.json HTTP ${http}; backing off ${wait_s}s"
    sleep_capped "$wait_s" || give_up "$last_max"
    if [[ ! "$ra" =~ ^[0-9]+$ ]] && (( backoff < INTERVAL )); then
      backoff=$((backoff * 2))
      if (( backoff > INTERVAL )); then
        backoff=$INTERVAL
      fi
    fi
    continue
  fi

  log "index.json HTTP ${http}; backing off ${backoff}s"
  sleep_capped "$backoff" || give_up "$last_max"
  if (( backoff < INTERVAL )); then
    backoff=$((backoff * 2))
    if (( backoff > INTERVAL )); then
      backoff=$INTERVAL
    fi
  fi
done

rm -f "$POLL" "$HDR"
log "index lists sweep >= ${NEED}; running verifier all --refresh (venue and indexsig exports are re-fetched; the final board is the signed t=standings post)"
export PYTHONUNBUFFERED=1
set +e
"$PY" "$VER/close1_verify_public.py" all --refresh --repo "$ROOT/close-call" >>"$LOG" 2>&1
code=$?
set -e
log "verifier all --refresh exited ${code}"
exit "$code"
