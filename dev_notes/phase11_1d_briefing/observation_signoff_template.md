# Phase 11 of the 1D Briefing Rewrite — Observation Window Sign-off

**Phase 9 cutover commit:** `72eaf1b phase9-1d-briefing: cutover`
**Cutover deployed:** _<fill at deploy>_
**Window opens:** _<fill at deploy>_
**Window closes (target):** _<fill +1-2 weeks from open>_
**Operator:** inshadaliqbal786

---

## 15-item observation checklist

Each item is read from production data over the entire window. The window does NOT close until every item is observed and signed.

| # | Item | Threshold | Source | Status |
|---|------|-----------|--------|--------|
| 1 | ≥12 packages/cycle (p50) sustained | p50 ≥ 12 | `cycle_metrics.briefing_packages_count` | ☐ |
| 2 | ≥10 packages/cycle (p5) | p5 ≥ 10 | hourly cycle_metrics percentile | ☐ |
| 3 | `BRAIN_INSUFFICIENT_QUALITY` absent except boot grace | 0 outside grace | `grep BRAIN_INSUFFICIENT data/logs/workers.log` | ☐ |
| 4 | Trade count/day ≥ pre-rewrite baseline | baseline ~5-8/day per Phase 0 A | `SELECT count(*) FROM trade_history WHERE...` | ☐ |
| 5 | P&L/day within ±20% of baseline | within ±20% | trade_history | ☐ |
| 6 | No new ERROR-level patterns | diff against pre-window | `grep ERROR data/logs/workers.log` | ☐ |
| 7 | No new WARNING-level patterns | diff against pre-window | `grep WARNING data/logs/workers.log` | ☐ |
| 8 | No CRITICAL Telegram alerts | 0 | telegram audit log | ☐ |
| 9 | Cold-start gate fires correctly when packages degraded | regression check | induce a cache miss in staging; gate fires | ☐ |
| 10 | `state_label` distribution non-degenerate | ≥4 distinct primary labels | `cycle_metrics.state_label_distribution_json` | ☐ |
| 11 | `interestingness_p50/p95` populated every hour | non-NULL each hour | `SELECT * FROM cycle_metrics ORDER BY hour_ts DESC` | ☐ |
| 12 | Brain prompt p95 size < 18 KB | strict | `STRAT_CALL_A | chars=` per cycle | ☐ |
| 13 | Validator FAIL-quarantine rate ≤ 2× pre-window | rate comparison | `PACKAGE_VALIDATE_SUMMARY` fail_quarantined | ☐ |
| 14 | No regression in existing E2E tests | `pytest -k "e2e or pipeline"` | green | ☐ |
| 15 | Operator signs this document | operator name + UTC timestamp at bottom | _<below>_ | ☐ |

---

## Daily snapshots (one per day during the window)

Reuse `scripts/observation_1d_briefing_ab.py` (Phase 8) but pass `--hours 24`. Save the output to `dev_notes/phase11_1d_briefing/observation_log_<YYYY-MM-DD>.md` each day.

Recommended cron-equivalent invocation:

```bash
python scripts/observation_1d_briefing_ab.py --hours 24 \
  --out dev_notes/phase11_1d_briefing/observation_log_$(date -u +%Y-%m-%d).md
```

Run daily at the same UTC hour for consistency. Operator reviews the daily snapshot for any item-3 / item-6 / item-7 anomalies and triggers rollback if warranted (per the rollback decision tree in the plan).

---

## Rollback procedure

If any item fails during observation:

1. Edit `config.toml`:
   ```toml
   [scanner]
   mode = "exclusion"
   [brain]
   surface_briefing_fields = false
   ```
2. Restart workers: `sudo systemctl restart trading-workers` (or operator's preferred restart command).
3. Phase 11 window remains OPEN — do not advance to Phase 10.
4. Open an incident note in `dev_notes/phase11_1d_briefing/incident_<UTC>.md` describing which item failed and what was observed.
5. Diagnose root cause; fix in a NEW commit (do NOT amend Phase 9).
6. Restart the observation window from item 1.

---

## Operator sign-off

When all 15 items pass:

```
Operator name:   ______________________________
Sign-off date:   __________ UTC
Phase 9 cutover commit:   72eaf1b
First observation log:    dev_notes/phase11_1d_briefing/observation_log_<first-date>.md
Last observation log:     dev_notes/phase11_1d_briefing/observation_log_<last-date>.md
Total cycles observed:    __________
```

After sign-off, advance to Phase 10 (legacy removal) per the rollout plan.
