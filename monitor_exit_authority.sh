#!/usr/bin/env bash
# Live monitor for the exit-authority consolidation (log-only observation).
# Summarizes, for a time window, how the owner switch is behaving on live trades:
# trades closed (and whether they peaked green = the clip indicator), owner
# hand-offs at breakeven, the would-defer events (the caging writers the fix
# catches), who writes each green/red trade, the gateway clamp/reject trend, and
# any anomalies. Screen-reader friendly: labeled prose lines, no tables.
#
# Usage: bash monitor_exit_authority.sh ["YYYY-MM-DD HH:MM:SS"]   (default: last 30 min)
set -euo pipefail
cd "$(dirname "$0")"
LOG=data/logs/workers.log
SINCE="${1:-$(date -u -d '30 minutes ago' '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -u '+%Y-%m-%d %H:%M:%S')}"
win() { awk -v s="$SINCE" '$0 >= s' "$LOG" 2>/dev/null; }
cnt() { win | grep -hcE "$1" 2>/dev/null | tail -1 || true; }   # always one number, never fails

echo "=== EXIT-AUTHORITY LIVE MONITOR ==="
echo "now: $(date -u '+%Y-%m-%d %H:%M:%S UTC')   window since: $SINCE"
echo ""

echo "## Service + mode"
echo "  service: $(systemctl is-active trading-workers.service 2>/dev/null)  pid: $(systemctl show -p MainPID --value trading-workers.service 2>/dev/null)  restarts: $(systemctl show -p NRestarts --value trading-workers.service 2>/dev/null)"
grep -hE 'SL_GATEWAY_OWNER_SWITCH ' "$LOG" 2>/dev/null | tail -1 | grep -oE 'enabled=\w+ enforce=\w+ advisory_enforce=\w+ head_only_seizes_green=\w+ faded_winner_rearm_red=\w+' | sed 's/^/  config: /'
echo ""

echo "## Open positions right now"
grep -hE 'WD_TICK ' "$LOG" 2>/dev/null | tail -1 | grep -oE 'n=[0-9]+ syms=\[[^]]*\]' | sed 's/^/  /' || echo "  (no watchdog tick yet)"
echo ""

echo "## Trades closed in window (clip check: did it peak green then close red?)"
win | grep -hE 'THESIS_CLOSE' | grep -oE 'sym=[A-Z]+ .*pnl=[+-][0-9.]+%.*rsn=[a-z_]+' | grep -oE 'sym=[A-Z]+|pnl=[+-][0-9.]+%|rsn=[a-z_]+' | paste - - - | sed 's/^/  /' || true
echo "  trades closed in window: $(cnt 'THESIS_CLOSE')"
echo ""

echo "## Owner switch — the fixes observing (log-only)"
echo "  hand-offs at breakeven (OWNER_HANDOFF):   $(cnt 'SL_GATEWAY_OWNER_HANDOFF')"
echo "  would-defer a wrong-owner write (WRONG_OWNER_WOULD): $(cnt 'SL_GATEWAY_WRONG_OWNER_WOULD')"
echo "  would-defer an advisory write (ADVISORY_DEFERRED_WOULD): $(cnt 'SL_GATEWAY_ADVISORY_DEFERRED_WOULD')"
echo "  Head tightening a green trade (HEAD_OVERRIDE): $(cnt 'SL_GATEWAY_HEAD_OVERRIDE')"
echo "  --- what the would-defers caught (the caging writers, by source) ---"
win | grep -hE 'SL_GATEWAY_WRONG_OWNER_WOULD|SL_GATEWAY_ADVISORY_DEFERRED_WOULD' | grep -hoE 'src=[a-z_]+ bucket=[a-z]+ state=[a-z]+' | sort | uniq -c | sed 's/^/  /' | head -15 || true
echo "  --- accepted stop writes by owner/state (who manages each trade) ---"
win | grep -hE 'SL_GATEWAY_ACCEPT' | grep -hoE 'own=[a-z]+ st=[a-z]+' | sort | uniq -c | sed 's/^/  /' || true
echo ""

echo "## Gateway clamp/reject trend (should subside as writers stop contradicting)"
echo "  accepts: $(cnt 'SL_GATEWAY_ACCEPT')   rejects: $(cnt 'SL_GATEWAY_REJECT ')   R2 clamps: $(cnt 'SL_GATEWAY_R2_CLAMP')"
win | grep -hE 'SL_GATEWAY_STATS' | tail -1 | grep -oE 'total=[0-9]+ accept=[0-9]+ reject=[0-9]+ would=[0-9]+.*by_rsn=\{[^}]*\}' | sed 's/^/  latest stats: /' || true
echo ""

echo "## Anomalies (should be zero)"
echo "  owner-gate errors (OWNER_ERROR): $(cnt 'SL_GATEWAY_OWNER_ERROR')"
echo "  unclassified sources (OWNER_UNCLASSIFIED): $(cnt 'SL_GATEWAY_OWNER_UNCLASSIFIED')"
echo "  flag inconsistency (OWNER_SWITCH_INCONSISTENT): $(cnt 'SL_GATEWAY_OWNER_SWITCH_INCONSISTENT')"
echo "  wire failures (WIRE_FAIL): $(cnt 'SL_GATEWAY_WIRE_FAIL')"
