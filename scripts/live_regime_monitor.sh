#!/bin/bash
# Live regime monitor for the B1a fix verification.
# Polls workers.log every 30s, prints a one-line metrics summary, and
# writes a longer running record to dev_notes/regime_investigation/live_monitor.log.
#
# Run:
#   cd /home/inshadaliqbal786/trading-intelligence-mcp
#   ./scripts/live_regime_monitor.sh   # foreground
#   ./scripts/live_regime_monitor.sh &  # background

set -u

# Restart timestamp — anchor for "since restart" windowing.
# Bash compares strings lexicographically; ISO timestamps work directly.
RESTART_TS="${RESTART_TS:-2026-05-12 09:41:54}"
LOG=/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log
OUT=/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/regime_investigation/live_monitor.log
INTERVAL="${INTERVAL:-30}"

emit() {
  local ts
  ts=$(date '+%Y-%m-%d %H:%M:%S')

  local total volatile_n trending_down_n trending_up_n ranging_n dead_n fallback_n
  total=$(awk -v r="$RESTART_TS" '/REGIME \|/ && ($1 " " $2) >= r' "$LOG" | wc -l)
  volatile_n=$(awk -v r="$RESTART_TS" '/REGIME \|/ && ($1 " " $2) >= r' "$LOG" | grep -c "rgm=volatile" || true)
  trending_down_n=$(awk -v r="$RESTART_TS" '/REGIME \|/ && ($1 " " $2) >= r' "$LOG" | grep -c "rgm=trending_down" || true)
  trending_up_n=$(awk -v r="$RESTART_TS" '/REGIME \|/ && ($1 " " $2) >= r' "$LOG" | grep -c "rgm=trending_up" || true)
  ranging_n=$(awk -v r="$RESTART_TS" '/REGIME \|/ && ($1 " " $2) >= r' "$LOG" | grep -c "rgm=ranging" || true)
  dead_n=$(awk -v r="$RESTART_TS" '/REGIME \|/ && ($1 " " $2) >= r' "$LOG" | grep -c "rgm=dead" || true)
  fallback_n=$(awk -v r="$RESTART_TS" '/REGIME \|/ && ($1 " " $2) >= r' "$LOG" | grep -c "conf=0.40" || true)

  local apex_n apex_lock_n apex_flip_acc
  apex_n=$(awk -v r="$RESTART_TS" '/APEX_FLIP_DECISION/ && ($1 " " $2) >= r' "$LOG" | wc -l)
  apex_lock_n=$(awk -v r="$RESTART_TS" '/APEX_FLIP_DECISION/ && ($1 " " $2) >= r' "$LOG" | grep -c "dir_locked=Y" || true)
  apex_flip_acc=$(awk -v r="$RESTART_TS" '/APEX_FLIP_DECISION/ && ($1 " " $2) >= r' "$LOG" | grep -c "flip_accepted=Y" || true)

  local xray_flip_n
  xray_flip_n=$(awk -v r="$RESTART_TS" '/XRAY_DIR_FLIP[^_]/ && ($1 " " $2) >= r' "$LOG" | wc -l)

  local dd_n dd_buy dd_sell
  dd_n=$(awk -v r="$RESTART_TS" '/DIRECTION_DECISION/ && ($1 " " $2) >= r' "$LOG" | wc -l)
  dd_buy=$(awk -v r="$RESTART_TS" '/DIRECTION_DECISION/ && ($1 " " $2) >= r' "$LOG" | grep -c "final_dir=Buy" || true)
  dd_sell=$(awk -v r="$RESTART_TS" '/DIRECTION_DECISION/ && ($1 " " $2) >= r' "$LOG" | grep -c "final_dir=Sell" || true)

  local fallback_pct ranging_pct trending_pct
  if [ "$total" -gt 0 ]; then
    fallback_pct=$(awk -v t="$total" -v f="$fallback_n" 'BEGIN{printf "%.1f", 100*f/t}')
    ranging_pct=$(awk -v t="$total" -v r="$ranging_n" 'BEGIN{printf "%.1f", 100*r/t}')
    trending_pct=$(awk -v t="$total" -v tu="$trending_up_n" -v td="$trending_down_n" 'BEGIN{printf "%.1f", 100*(tu+td)/t}')
  else
    fallback_pct="--"; ranging_pct="--"; trending_pct="--"
  fi

  local line="[${ts}] regime=${total} (rng=${ranging_pct}% vol=${volatile_n} td=${trending_down_n} tu=${trending_up_n} dead=${dead_n} fallback=${fallback_pct}% trend_share=${trending_pct}%) | apex=${apex_n} locked=${apex_lock_n} flips_accepted=${apex_flip_acc} | xray_flips=${xray_flip_n} | dd=${dd_n} buy=${dd_buy} sell=${dd_sell}"
  echo "$line"
  echo "$line" >> "$OUT"
}

echo "==== Live regime monitor ====" | tee -a "$OUT"
echo "Restart anchor: $RESTART_TS" | tee -a "$OUT"
echo "Log: $LOG" | tee -a "$OUT"
echo "Interval: ${INTERVAL}s" | tee -a "$OUT"
echo "" | tee -a "$OUT"

while true; do
  emit
  sleep "$INTERVAL"
done
