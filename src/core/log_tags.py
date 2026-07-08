"""Layer 1 restructure — standardized log tag constants (Phase 1).

Single source of truth for the structured tags introduced by the Layer 1
restructure. Every tag follows the same shape:

    TAG | k=v k=v ... | {ctx()}

Tags are bare strings (not f-string templates) so that callers compose
the ``key=value`` payload locally. Keeping them in one module lets us
grep one file to see the full taxonomy and lets tests match on exact
strings without picking up unrelated occurrences.
"""

# ── Per-worker tick markers (Phase 1.2) ─────────────────────────────
# LAYER1A — always-running data fetchers (kline, price, altdata, news).
# LAYER1B — cycle-triggered analyzers (structure, signal, regime).
# LAYER1C — strategy pipeline (strategy_worker hosting Stage 1's 4 internal layers).
# LAYER1D — selector + package builder (scanner_worker).

LAYER1A_TICK_START = "LAYER1A_TICK_START"
LAYER1A_TICK_DONE = "LAYER1A_TICK_DONE"
LAYER1B_TICK_START = "LAYER1B_TICK_START"
LAYER1B_TICK_DONE = "LAYER1B_TICK_DONE"
LAYER1C_TICK_START = "LAYER1C_TICK_START"
LAYER1C_TICK_DONE = "LAYER1C_TICK_DONE"
LAYER1D_TICK_START = "LAYER1D_TICK_START"
LAYER1D_TICK_DONE = "LAYER1D_TICK_DONE"

# ── Skip markers (Phase 4 codifies; emitted at DEBUG to avoid spam when
# trading is off and 1B/1C/1D workers skip every fire). ──────────────
LAYER1A_TICK_SKIP = "LAYER1A_TICK_SKIP"  # rarely used; 1A always runs
LAYER1B_TICK_SKIP = "LAYER1B_TICK_SKIP"
LAYER1C_TICK_SKIP = "LAYER1C_TICK_SKIP"
LAYER1D_TICK_SKIP = "LAYER1D_TICK_SKIP"

# ── Per-cycle markers — emitted by CycleTracker around the analysis-→
# selection chain. ─────────────────────────────────────────────────────
LAYER1B_CYCLE_START = "LAYER1B_CYCLE_START"
LAYER1B_CYCLE_DONE = "LAYER1B_CYCLE_DONE"
LAYER1C_CYCLE_START = "LAYER1C_CYCLE_START"
LAYER1C_CYCLE_DONE = "LAYER1C_CYCLE_DONE"
LAYER1D_CYCLE_START = "LAYER1D_CYCLE_START"
LAYER1D_CYCLE_DONE = "LAYER1D_CYCLE_DONE"

# ── Cycle-level rollup ──────────────────────────────────────────────
CYCLE_COMPLETE = "CYCLE_COMPLETE"

# ── Cold-start boundary wait (Phase 4) ──────────────────────────────
CYCLE_RESUME_WAIT = "CYCLE_RESUME_WAIT"
CYCLE_RESUME = "CYCLE_RESUME"

# ── Worker liveness (Phase 11 dead-workers fix) ──────────────────────
# Watchdog detects "registered but never ticked" within ~2 minutes of
# boot, and "stopped ticking after first tick" within 2× the worker's
# expected interval. WORKER_LIVENESS_HEARTBEAT is the per-tick INFO
# rollup (always emitted so the trail is continuous); the WARNING
# tags only fire when a worker is genuinely unhealthy.
WORKER_NEVER_TICKED = "WORKER_NEVER_TICKED"
WORKER_TICK_OVERDUE = "WORKER_TICK_OVERDUE"
WORKER_LIVENESS_HEARTBEAT = "WORKER_LIVENESS_HEARTBEAT"

# ── Cradle-to-grave per-worker tick markers (Phase 11 obs upgrade) ──
# Closes the "we don't see the tick body executing" diagnostic gap.
# WORKER_TICK_START fires ONCE (one-shot), right before the first
#   `await self.tick()` of a worker's lifetime — its presence proves
#   the run loop reached the tick body; its ABSENCE on a worker that
#   has WM_START + SWEET_SPOT_FIRED means the cycle gate or scheduler
#   path is the upstream issue, NOT the tick body itself.
# WORKER_TICK_FAIL fires whenever tick() raises (every occurrence),
#   alongside the existing WARNING that BaseWorker already logs. The
#   structured tag lets operators grep for tick failures without
#   matching unrelated WARNING noise.
WORKER_TICK_START = "WORKER_TICK_START"
WORKER_TICK_FAIL = "WORKER_TICK_FAIL"

# ── Layer 1D briefing-pack rewrite (Phase 1 — observability infra) ──
# Tags registered in advance of the briefing pipeline becoming active.
# They are CONSTANTS ONLY at this phase; emission is wired in Phases
# 3-9 of the briefing rewrite. Operators can grep for them in logs
# even before the pipeline is live (no matches expected until then).
#
# BRIEFING_BUILD_START / BRIEFING_BUILD_DONE — bracket the briefing
#   builder per-cycle work; emitted by ScannerWorker briefing-mode tick.
# BRIEFING_RANK — per-cycle ranking summary (top-N, mean interestingness).
# BRIEFING_STATE_LABEL — per-coin label assignment (one line per labeled coin).
# BRIEFING_INTERESTINGNESS — per-coin interestingness score with breakdown.
# BRIEFING_AB_COMPARE — Phase 8 A/B harness daily-summary line.
# SCANNER_LABELED — per-coin label trace (alongside SCANNER_SELECTED).
# SCANNER_BRIEFING_SUMMARY — per-cycle briefing summary alongside
#   SCANNER_FILTER_AGGREGATE; preserves legacy aggregate keys, adds
#   labels_per_cycle, mean_interestingness, top_label.
BRIEFING_BUILD_START = "BRIEFING_BUILD_START"
BRIEFING_BUILD_DONE = "BRIEFING_BUILD_DONE"
BRIEFING_RANK = "BRIEFING_RANK"
BRIEFING_STATE_LABEL = "BRIEFING_STATE_LABEL"
BRIEFING_INTERESTINGNESS = "BRIEFING_INTERESTINGNESS"
BRIEFING_AB_COMPARE = "BRIEFING_AB_COMPARE"
SCANNER_LABELED = "SCANNER_LABELED"
SCANNER_BRIEFING_SUMMARY = "SCANNER_BRIEFING_SUMMARY"

# ── Price-display precision fix ─────────────────────────────────────
# PRICE_FORMATTER_WIRED — one-shot boot sentinel emitted when the
#   canonical PriceFormatter is constructed; ``tick_resolver=true`` means
#   exact exchange tick-size precision is available (InstrumentService
#   cache wired), ``false`` means magnitude-aware fallback only.
# PRICE_FMT_FALLBACK — DEBUG, deduped once per symbol per process, emitted
#   when a render falls back to magnitude precision because the symbol's
#   tick size was not cached (or the resolver errored). Never emitted on
#   the exact-tick hit path (that would be per-render spam).
PRICE_FORMATTER_WIRED = "PRICE_FORMATTER_WIRED"
PRICE_FMT_FALLBACK = "PRICE_FMT_FALLBACK"
