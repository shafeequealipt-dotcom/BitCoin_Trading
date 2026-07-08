#!/usr/bin/env bash
# Live-streaming monitor of the adaptive exit systems acting on open trades.
# Read-only: tails the workers log and records every line where an exit system
# touches a position, tagging each with a category so we can confirm the
# adaptive geometry fires on real open trades. Bounded window; exits cleanly.
#
#   $1 = window seconds (default 1500)
set -u
LOG="data/logs/workers.log"
OUT="data/logs/adaptive_monitor_feed.txt"
WINDOW="${1:-1500}"

# Adaptive exit + ownership + open/close lifecycle tags (exact strings from source).
PAT='LADDER_ADAPTIVE|MICRO_FLOOR_ARM|LADDER_ZERO_CROSSING_FLOOR|LADDER_FLOOR_JUMP|LADDER_FIRST_LOCK_JUMP|SL_GATEWAY_R2_PROFIT_LOCK_HELD|SL_GATEWAY_R2_FLOOR_HELD|SL_GATEWAY_R2_CLAMP|DEAD_DRIFTER_SCRATCH|SL_GATEWAY_OWNER_HANDOFF|COORD_CLOSE_START|M4_DECISION|SNIPER_SPINE_SELECT'

{
  echo "MONITOR_START $(date -u +%FT%TZ) window=${WINDOW}s log=${LOG}"
} >> "$OUT"

# Stream new lines only (-n0), filter to the exit-system tags, append live.
timeout "${WINDOW}" stdbuf -oL -eL tail -F -n0 "$LOG" 2>/dev/null \
  | stdbuf -oL -eL grep -aE "$PAT" \
  | while IFS= read -r line; do
      echo "$line" >> "$OUT"
    done

echo "MONITOR_END $(date -u +%FT%TZ)" >> "$OUT"
