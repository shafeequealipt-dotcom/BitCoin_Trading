#!/usr/bin/env bash
# Per-SECOND live feed of the open trades + the owner-gate, for the exit-authority
# fix. Samples once every second and appends a timestamped line to
# data/logs/exit_authority_live_feed.txt so nothing is missed between my passes.
# Uses the AUTHORITATIVE current pnl (the sniper's per-tick "Mode4 (SYM): dir= pnl="
# line, which logs BOTH wins and losses) — never the green-only M4_DECISION.
# Usage: bash live_seconds_monitor.sh [duration_seconds]   (default 1800 = 30 min)
cd "$(dirname "$0")"
LOG=data/logs/workers.log
FEED=data/logs/exit_authority_live_feed.txt
DUR=${1:-1800}
end=$(( $(date +%s) + DUR ))
echo "# live feed started $(date -u '+%Y-%m-%d %H:%M:%S UTC') (per-second)" >> "$FEED"
while [ "$(date +%s)" -lt "$end" ]; do
  ts=$(date -u '+%H:%M:%S')
  syms=$(grep -hE 'WD_TICK ' "$LOG" 2>/dev/null | tail -1 | grep -oE 'syms=\[[^]]*\]' | sed 's/syms=\[//;s/\]//;s/,/ /g')
  line="$ts |"
  any=0
  for s in $syms; do
    [ "$s" = "none" ] && continue
    any=1
    m=$(grep -hE "Mode4 \($s\):" "$LOG" 2>/dev/null | tail -1 | grep -oE 'dir=\w+ pnl=[+-]?[0-9.]+%')
    ow=$(grep -hE "SL_GATEWAY_ACCEPT.*sym=$s " "$LOG" 2>/dev/null | tail -1 | grep -oE 'own=\w+ st=\w+')
    line="$line  $s[${m:-?} ${ow}]"
  done
  [ "$any" = 0 ] && line="$line  (no open trades)"
  # owner-gate events + closes in the last ~2s
  cut=$(date -u -d '2 seconds ago' '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
  ev=$(awk -v t="$cut" '$0>=t' "$LOG" 2>/dev/null | grep -hoE 'SL_GATEWAY_WRONG_OWNER |SL_GATEWAY_HEAD_OVERRIDE|SL_GATEWAY_ADVISORY_DEFERRED |THESIS_CLOSE|SL_GATEWAY_OWNER_ERROR|monotonic_grind_cut|loss_stall' | sort -u | tr '\n' ',')
  [ -n "$ev" ] && line="$line  <<EVENT: $ev>>"
  echo "$line" >> "$FEED"
  sleep 1
done
echo "# live feed ended $(date -u '+%H:%M:%S UTC')" >> "$FEED"
