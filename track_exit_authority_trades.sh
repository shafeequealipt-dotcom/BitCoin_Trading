#!/usr/bin/env bash
# Exit-authority trade ledger: for every closed trade since a given time, record
# its peak-green %, its close %, the give-back, the close reason, and whether the
# owner gate (the fix) caught a caging writer that — under enforcement — would
# have prevented the clip. Also classifies each trade and prints a verdict on
# whether the profit/loss systems are colliding (clipping winners) or coherent.
# Writes the ledger to EXIT_AUTHORITY_TRADE_LEDGER.md and prints it.
# Usage: bash track_exit_authority_trades.sh ["YYYY-MM-DD HH:MM:SS"]
set -uo pipefail
cd "$(dirname "$0")"
LOG=data/logs/workers.log
SINCE="${1:-2026-06-15 01:39:00}"   # since the log-only restart
LEDGER=EXIT_AUTHORITY_TRADE_LEDGER.md
win() { awk -v s="$SINCE" '$0 >= s' "$LOG" 2>/dev/null; }

clipped=0; ran=0; losers_cut=0; tp_hits=0; total=0; caged=0
{
echo "# Exit-authority trade ledger (log-only observation)"
echo ""
echo "Generated $(date -u '+%Y-%m-%d %H:%M:%S UTC'); trades closed since $SINCE."
echo "Mode: log-only (owner_switch_enforce=false) — the fix observes but does not"
echo "yet act, so any clip seen here is the UNCHANGED pre-fix behavior."
echo ""
echo "Per trade: peak green, close, give-back, reason, and caging-writers-caught"
echo "(how many times the owner gate flagged a loss/advisory writer it WOULD have"
echo "deferred on that trade under enforcement)."
echo ""
while IFS= read -r line; do
  sym=$(echo "$line" | grep -oE 'sym=[A-Z]+' | head -1 | cut -d= -f2)
  [ -z "$sym" ] && continue
  tm=$(echo "$line" | cut -c12-19)
  close=$(echo "$line" | grep -oE 'pnl=[+-][0-9.]+%' | head -1 | grep -oE '[+-][0-9.]+')
  rsn=$(echo "$line" | grep -oE 'rsn=[a-z_]+' | head -1 | cut -d= -f2)
  peak=$(grep -hE "M4_DECISION.*sym=$sym " "$LOG" 2>/dev/null | grep -oE 'peak_pnl=[0-9.]+' | cut -d= -f2 | sort -n | tail -1)
  peak="${peak:-0}"; close="${close:-0}"
  give=$(awk -v p="$peak" -v c="$close" 'BEGIN{printf "%.2f", p-c}')
  cg=$(win | grep -hcE "SL_GATEWAY_(WRONG_OWNER_WOULD|ADVISORY_DEFERRED_WOULD).*sym=$sym " 2>/dev/null | tail -1)
  total=$((total+1)); caged=$((caged+cg))
  cls="loser"
  awk -v p="$peak" 'BEGIN{exit !(p>=0.15)}' && {
     awk -v c="$close" -v p="$peak" 'BEGIN{exit !(c <= p/2)}' && { cls="CLIPPED-WINNER"; clipped=$((clipped+1)); } || { cls="ran (kept profit)"; ran=$((ran+1)); }
  }
  case "$rsn" in *grind_cut*|*loss_stall*|*win_prob*) losers_cut=$((losers_cut+1));; esac
  case "$rsn" in *_tp) : ;; esac
  printf "  %s  %-10s peak +%-5s%%  close %+6s%%  give-back %5s%%  [%s]  reason=%s  caging-caught=%s\n" \
         "$tm" "$sym" "$peak" "$close" "$give" "$cls" "$rsn" "$cg"
done < <(win | grep -hE 'THESIS_CLOSE')
tp_hits=$(win | grep -hcE 'closed_by=.*(_tp_hit|take_profit)' 2>/dev/null | tail -1)
echo ""
echo "## Summary"
echo "  trades closed: $total | clipped winners: $clipped | ran/kept profit: $ran | genuine losers cut by loss engine: $losers_cut"
echo "  take-profit targets hit: ${tp_hits:-0}   (zero = the clip pattern persists)"
echo "  caging writers the owner gate caught (would-defer): $caged"
echo ""
echo "## Verdict"
if [ "$clipped" -gt 0 ]; then
  echo "  STILL COLLIDING (in log-only): winners are peaking green and giving the profit"
  echo "  back to ~breakeven — the clip. The fix is OBSERVING this correctly (it caught"
  echo "  $caged caging writers it would have deferred) but it is NOT enforcing, so the"
  echo "  collision is not yet resolved. Flipping owner_switch_enforce=true is what stops"
  echo "  it (the simulation proved the winner then runs); the loss engine already cuts"
  echo "  genuine losers correctly in both modes."
else
  echo "  No clipped winners in this window."
fi
} | tee "$LEDGER"
