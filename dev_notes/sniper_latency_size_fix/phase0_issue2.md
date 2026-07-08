# Phase 0 — Issue 2 Investigation: Sniper Phase 1D Grace Gap

## Hypothesis Confirmed

**Hypothesis A** — Phase 1D shipped only `max_partials_per_position = 3`; the per-partial-to-full grace gap was never added. The cooldown `stall_escape_cooldown_seconds = 30` (6 ticks at 5s cadence) is the only inter-escape spacing.

## Evidence

### Phase 1D commit content (`git show 04a8170`)

```
feat(sniper/phase-1D): raise max_partials_per_position default to 3

Files:
- src/config/settings.py Mode4Settings.max_partials_per_position default 1 → 3
- config.toml [mode4] max_partials_per_position 1 → 3 + comment
- tests/test_layer4_sniper/test_partial_cap_default.py — 2 smoke
```

The commit message claims "Combined with Phase 1B's 60-tick (5-min) grace gap between partial and full" — but Phase 1B (`3dbd376`) only changed thresholds (`stall_escape_partial_after_ticks 20 → 120`, `stall_escape_full_after_ticks 40 → 180`). No per-emission grace enforcement was added in Phase 1B, 1D, or any other phase.

### Code trace (`src/workers/profit_sniper.py:2248-2502`)

`_stall_escape_action()` decision flow:

1. Line 2360: `_stall_ticks` increments only when `is_actionable=True AND current_action="hold"`.
2. Line 2371-2373: quiet window (`ticks <= partial_after`) returns None.
3. Line 2412-2443: PnL guards (Phase 1C — profit_guard, development_guard).
4. Line 2462-2473: forced full (`ticks > full_after = 180` OR tighten_apps cap exceeded). Sets `_stall_last_escape_ts = now`. **This is the legitimate mature-stall valve and must bypass any grace gap.**
5. Line 2477-2479: cooldown check `(now - _stall_last_escape_ts) < cooldown_s = 30`. **This is the only inter-escape gap.** At 5s cadence = 6 ticks.
6. Line 2486-2496: cap check `partials_so_far >= max_partials = 3`. Escalates to full_close.
7. Line 2500-2502: emit partial_close, increment `_partials_emitted`.

**The tracked dict has `_stall_ticks`, `_stall_last_escape_ts`, `_partials_emitted`, `_stall_tighten_applications`, `_stall_worst_pnl_pct`. NO field tracks "last emission was partial vs full".**

### Live RENDERUSDT escalation trace (10:57:40 → 10:59:19)

Verbatim from `data/logs/combined_2026-05-07_10-30_to_12-20.log`:

```
10:57:40.650 ticks=121 escalated_to=partial_close score=36 pnl=-0.39%
10:58:11.063 ticks=126 escalated_to=partial_close score=41 pnl=-0.38%   (gap: 5 ticks, 30.4s)
10:58:48.352 ticks=132 escalated_to=partial_close score=28 pnl=-0.43%   (gap: 6 ticks, 37.3s)
10:59:19.850 ticks=137 escalated_to=full_close    score=30 pnl=-0.44%   (gap: 5 ticks, 31.5s)
```

Total: 4 ladder steps, 121→137 ticks (16 ticks ≈ 80 seconds), full kill in 99 seconds. The "5-min grace gap between partial and full" never applied. Each gap matches the 30-second `stall_escape_cooldown_seconds` exactly.

### Same pattern across 6 other positions in the window

- EGLDUSDT 11:03:19→11:04:18 (173→181, gaps 4/4) — note this hit the forced full at ticks=181 (>full_after=180)
- OPUSDT 11:13:02→11:14:50 (121→139, gaps 8/5/5)
- FILUSDT 11:15:02→11:16:47 (121→136, gaps 5/5/5)
- SOLUSDT 11:15:02→11:16:47 (121→136, gaps 5/5/5)
- RENDERUSDT 11:31:29→11:33:14 (122→138, gaps 5/6/5)
- MONUSDT 11:42:58→11:43:31+ (171→177+, gaps 6+)

Total 26 SNIPER_STALL_ESCAPE events in the 110-min window. None show a 60-tick gap between consecutive escalations.

## Confirmed Fix Shape — Hypothesis A

Implement original Phase 1D specification properly:

- Add config keys `partial_to_full_grace_ticks = 60` and `partial_to_partial_grace_ticks = 60` to `[mode4]`.
- Add `Mode4Settings` fields with the same defaults.
- Add tracked-dict fields `_last_escape_type` (str), `_last_escape_tick` (int) — initialized in `_on_position_opened()`.
- Insert grace-gap check at line 2479 (after cooldown, before cap check).
- Update `_last_escape_type` and `_last_escape_tick` at every emission point (lines 2472, 2495, 2500).
- Forced-full path (`ticks > full_after`) bypasses grace gap by design (mature-stall safety valve).

## Surgical Insertion Points

| Location | File | Line | Action |
|----------|------|------|--------|
| Config | `config.toml` | After 1135 | Add 2 keys to `[mode4]` |
| Settings | `src/config/settings.py` | `Mode4Settings` | Add 2 fields |
| Position open | `src/workers/profit_sniper.py` | `_on_position_opened` | Init `_last_escape_type=""`, `_last_escape_tick=0` |
| Grace check | `src/workers/profit_sniper.py` | After 2479 | New `SNIPER_GRACE_BLOCKED` gate |
| Forced full | `src/workers/profit_sniper.py` | 2472 | Set `_last_escape_type="full"`, `_last_escape_tick=ticks` |
| Cap full | `src/workers/profit_sniper.py` | 2495 | Set `_last_escape_type="full"`, `_last_escape_tick=ticks` |
| Partial emit | `src/workers/profit_sniper.py` | 2500 | Set `_last_escape_type="partial"`, `_last_escape_tick=ticks` |
