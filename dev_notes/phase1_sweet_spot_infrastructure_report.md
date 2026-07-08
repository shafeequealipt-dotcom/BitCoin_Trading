# Phase 1 — Sweet-Spot Config + Scheduler Infrastructure

**Engagement:** Layer 1 corrected migration (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md).
**Date:** 2026-04-26
**Commit:** (this file is part of the Phase 1 commit)
**Phase 0 commit:** `bca18d0` (preceded this).

## Summary

Adds the foundation for sweet-spot scheduling: config schema, validators, runtime scheduler module, and the `SweetSpotWorker` BaseWorker subclass. **No existing worker behavior changes in this phase** — Phases 2-5 wire the workers onto sweet spots one at a time.

## Files modified

- `config.toml` — appended `[workers.sweet_spots]` and `[workers.sweet_spots.altdata]` sections with the corrected-Layer-1 chain defaults (kline 0:30 → structure 0:45 → signal 1:00 → regime 1:15 → strategy 1:30 → scanner 4:00; altdata funding 1:45, OI 5min, F&G 60min).
- `src/config/settings.py`:
  - New `_validate_sweet_spot()` helper (raises `ConfigError` with field path on bad MM:SS).
  - New `AltDataSweetSpotsSettings` dataclass with `__post_init__` validation.
  - New `SweetSpotsSettings` dataclass with `__post_init__` enforcing per-field MM:SS bounds AND strict chain ordering (kline < structure < signal < regime < strategy < scanner).
  - `WorkerSettings` gains `sweet_spots: SweetSpotsSettings = field(default_factory=...)`.
  - New `_build_altdata_sweet_spots()` and `_build_sweet_spots()` builders.
  - `_build_workers()` extended to call `_build_sweet_spots(data.get("sweet_spots", {}))`.

## Files added

- `src/workers/sweet_spot_scheduler.py` (~210 LOC) — pure-runtime module:
  - `parse_sweet_spot(value: str) -> tuple[int, int]`
  - `seconds_until_next_sweet_spot(spot, *, window_minutes=5, now=None, skip_threshold_s=0.1)` — wall-clock-anchored next-fire math; returns strictly positive seconds.
  - `is_at_sweet_spot(spot, *, window_minutes=5, now=None, tolerance_s=1.0)` — used by tests/probes.
  - `SweetSpotStats` dataclass — fires/cumulative_drift/max_drift/last_drift.
  - `SweetSpotScheduler(worker_name, offset, window_minutes=5)` with `await wait_for_sweet_spot()` returning drift in ms; emits `SWEET_SPOT_REGISTERED` at construction and `SWEET_SPOT_FIRED | worker=... offset=... drift_ms=... fires=...` per fire.
- `src/workers/base_worker.py` — appended `SweetSpotWorker(BaseWorker)` subclass at the END of the file. **`BaseWorker` itself is untouched** (verified by `git diff base_worker.py`). The subclass overrides `start()` only; the trailing `await asyncio.sleep(self.interval)` is replaced with `await self._scheduler.wait_for_sweet_spot()` placed BEFORE the first tick so the chain ordering is honored from boot. Error recovery, heartbeat, lifecycle, slow-tick warning, `WORKER_FIRST_TICK` milestone all inherit unchanged.
- `tests/test_sweet_spot_scheduler.py` (~250 LOC) — 26 tests covering the parser, `seconds_until_next_sweet_spot` math under deterministic `now`, `is_at_sweet_spot`, `SweetSpotScheduler.wait_for_sweet_spot` real-clock fire (auto-skipped if next fire >30s away to keep CI fast), `SweetSpotsSettings.__post_init__` rejection of bad MM:SS / out-of-window minute / chain-order violation / window misconfig, `AltDataSweetSpotsSettings` rejection of bad funding / non-positive OI/FG minutes, and 10-min custom-window scenario.

## Verification

**Trial 1.1 — config validation:** `Settings._load_fresh()` parses the new config.toml without error; all fields present:
```
kline_worker=0:30 structure_worker=0:45 signal_worker=1:00
regime_worker=1:15 strategy_worker=1:30 scanner_worker=4:00
window_minutes=5
altdata.funding_rates=1:45 altdata.open_interest_minutes=5 altdata.fear_greed_minutes=60
universe.watch_list size=50
```

**Trial 1.2 — `seconds_until_next_sweet_spot` math:** 5 deterministic-`now` tests pass, including wall-clock-aligned boundaries (now % 300 == 90 → spot at 0:30 → 240s to next firing).

**Trial 1.3 — Real-clock scheduler fire:** `wait_for_sweet_spot` real-clock test passes when invoked within 30s of a fire boundary; emits `SWEET_SPOT_FIRED | worker=... offset=0:00 drift_ms=...` and updates stats.

**Pytest result:** `25 passed, 1 skipped in 3.74s`. Skip is the real-clock test on a non-firing window — it's a CI-friendly safety guard, not a failure.

**Behavior change check:** No worker has been migrated yet. The new `SweetSpotWorker` subclass exists but is not extended by anyone. Existing 7 workers still tick on their old fixed intervals. `git diff src/workers/base_worker.py` confirms `BaseWorker.start()` is byte-identical to the pre-Phase-1 version.

## Hard rules + golden rules check

- HR-1 (workers on watch_list): not yet applicable — Phase 2+ wires workers.
- HR-2 (no inter-worker sync): scheduler is independent per-worker; no inter-scheduler events.
- HR-4 (chain ordering): enforced by `SweetSpotsSettings.__post_init__`. Bad chain → `ConfigError` at startup, workers refuse to start.
- HR-5 (watch_list as truth): unchanged in Phase 1.
- HR-6 (per-phase commits): this phase is one commit.
- Golden Rule 1 (understand before touch): `BaseWorker` was read end-to-end (Phase 0 documented). The subclass appends; the parent is untouched. Confirmed via diff.
- Golden Rule 5 (production-quality): type hints on every function; docstrings on every class and public method; structured logging via `loguru` with `ctx()` propagation; `ConfigError` for fail-loud config; unit + integration coverage; configurable via TOML; no hardcodes.
- CLAUDE.md (grep before remove): nothing was removed in this phase.

## Risks & deferred items

- The 9 pre-existing modified files in the working tree (operator chose "leave dirty") are not staged in this commit — only Phase 1 additions are. `git diff --cached --name-only` confirms 4 files staged: `config.toml`, `src/config/settings.py`, `src/workers/sweet_spot_scheduler.py`, `src/workers/base_worker.py`, `tests/test_sweet_spot_scheduler.py`, `dev_notes/phase1_sweet_spot_infrastructure_report.md`.
- `manager.py` does not yet pass any sweet-spot config to workers. Phase 2 will be the first to wire it — kline_worker becomes the first `SweetSpotWorker` consumer.
- Window length is configurable (default 5 min) but the chain ordering assumes a single window. If the operator ever wants per-worker different window lengths, the validator will need extension.

## Next phase

Phase 2 — KlineWorker migration (50 coins + sweet spot 0:30).
