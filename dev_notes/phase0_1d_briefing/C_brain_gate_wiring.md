# Phase 0.C — Brain Cold-Start Gate Wiring

**Files:**
- `src/core/layer_manager.py` (gate logic)
- `src/config/settings.py:367-403` (BrainColdStartProtection dataclass)
- `config.toml:218-226` (`[brain.cold_start_protection]`)

## Gate location

`_cold_start_block_or_none(plan)` at `layer_manager.py:1010-1072`. Called from `_run_brain_cycle` at line 780.

## Gate logic flow

```python
packages = self._coin_packages or {}                  # line 1014
n_pkg = len(packages)
n_trades = len(plan.new_trades or [])

# A — empty packages (boot or scanner crashed)
if not packages:                                       # line 1018
    log.warning("BRAIN_NO_PACKAGES | reason=empty_packages_cache trades_dropped={n}")
    return "no_packages"

# B — qualified count + completeness check (Q3d, 2026-04-29)
qualified_count = sum(
    1 for p in packages.values()
    if (bool(getattr(p, "qualified", False))
        or getattr(p, "open_position", None) is not None)
    and float(getattr(p, "completeness", 1.0)) >= cfg.min_per_package_completeness
)                                                      # line 1029-1046

avg_completeness = sum(p.completeness for p in packages.values()) / n_pkg

# Boot grace window check
boot_grace_active = (now - boot_time) < cfg.boot_grace_period_sec  # line 1047
if boot_grace_active:
    avg_required = cfg.boot_grace_completeness          # 0.95
else:
    avg_required = cfg.min_avg_completeness             # 0.85

# C — gate fires if either threshold breached
if (avg_completeness < avg_required
    and not boot_grace_active
    and qualified_count < cfg.min_qualified_packages):  # line 1063-1065
    log.warning(
        "BRAIN_INSUFFICIENT_QUALITY | scope=qualified_count "
        f"qualified={qualified_count} threshold={cfg.min_qualified_packages} "
        f"avg_completeness={avg:.2f} packages={n_pkg} trades_dropped={n}"
    )                                                  # line 1067-1072
    return "insufficient_quality"

return None  # gate passes; trades proceed
```

## Config thresholds (today)

```toml
[brain.cold_start_protection]
enabled = true
min_avg_completeness = 0.85
min_per_package_completeness = 0.75
min_qualified_packages = 3       # <-- the count gate
boot_grace_period_sec = 600
boot_grace_completeness = 0.95
```

`BrainColdStartProtection` dataclass at `settings.py:367-403`. Defaults match config.

## Phase 7 change (from plan)

**Only `min_qualified_packages` drops 3 → 1.** All other thresholds unchanged. Reason: 1 well-formed package proves caches are warm; the gate's purpose is cache-warmup safety, not minimum cohort size.

The relaxation requires:
1. `config.toml:223` — `min_qualified_packages = 1`
2. `settings.py:401` — dataclass default 3 → 1 (lockstep with config)

## Telegram alert path

`_send_cold_start_telegram(reason)` at `layer_manager.py:1075-1088`. Fires on every gated cycle. **Stays unchanged through all phases** — operator must always see gated cycles.

## `qualified` field semantics

In current pipeline:
- `qualified=True` ← survived `_qualifies()` 5-criterion gate AND made it into top-N
- `qualified=False` ← force-included open position only

In briefing-mode pipeline (Phase 5 onwards):
- `qualified=True` ← `state_label.primary not in ADVISORY_ONLY_LABELS AND interestingness_score >= settings.scanner.briefing.qualified_threshold (0.30)`
- `qualified=False` ← advisory-only state OR low interestingness

Critical: cold-start gate counts `qualified OR open_position`, so the boolean must remain meaningful.

## Strategist filter at `strategist.py:1204-1205`

Currently:
```python
if not pkg.qualified and pkg.open_position is None:
    continue   # don't render to brain prompt
```

This must be relaxed in Phase 9 to:
```python
if briefing_mode_active:
    skip = (pkg.state_label.primary == "NO_TRADEABLE_STATE"
            and pkg.open_position is None
            and pkg.interestingness_score < cfg.prompt_floor_interestingness)  # 0.20
else:
    skip = (not pkg.qualified) and (pkg.open_position is None)
```

The original "BTC/ETH ref-pair hallucination" fix (referenced in comments at `strategist.py:1196-1203`) is preserved because briefing mode does **NOT** re-add BTC/ETH to `_active_universe`. It only allows `qualified=False` packages built by the briefing path to render.
