"""Layer Manager — controls the 3-layer dependency chain.

Layer 1 (DATA):      data workers, scanner, regime, TA
Layer 2 (BRAIN):     Claude strategic review every 3 min (requires Layer 1)
Layer 3 (EXECUTION): rule engine + watchdog (requires Layer 1 + 2)

Dependencies enforced: can't start Layer N without Layer N-1 active.
Stopping cascades downward: stopping Layer 1 stops Layer 2 and 3.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from src.core.log_context import ctx, get_did
from src.core.logging import get_logger
from src.core.strategic_plan import PositionAction, StrategicPlan
from src.core.types import AlertLevel

log = get_logger("layer_manager")

# Persistent state file — survives process restarts
_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "layer_state.json"


@dataclass(frozen=True)
class LayerSnapshot:
    """Frozen point-in-time view of layer_active state.

    Phase 2 (Layer 3 enforcement) — capture-and-pass pattern. The brain
    cycle, strategy_worker, etc. capture a snapshot at the START of the
    directive→execution chain; OrderService re-checks against the live
    LayerManager at placement time and aborts (Layer3RaceError) if they
    disagree for a ``layer3_entry``. The dict is wrapped in
    MappingProxyType so it cannot be mutated after capture.

    Attributes:
        layer_active: Read-only mapping ``{1: bool, 2: bool, 3: bool}``.
        captured_at_monotonic: ``time.monotonic()`` at capture for latency
            measurement on the warn path.
        captured_at_wall: UTC isoformat timestamp for log correlation.
    """
    layer_active: Mapping[int, bool]
    captured_at_monotonic: float
    captured_at_wall: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def is_layer_active(self, layer: int) -> bool:
        """Convenience accessor mirroring ``LayerManager.is_layer_active``."""
        return bool(self.layer_active.get(layer, False))

    def age_ms(self) -> float:
        """Wall-clock age of the snapshot at the time of the call."""
        return (time.monotonic() - self.captured_at_monotonic) * 1000.0


class LayerManager:
    """Central controller for the 3-layer architecture."""

    LAYER_DATA = 1
    LAYER_BRAIN = 2
    LAYER_EXECUTION = 3

    def __init__(self, settings, services: dict) -> None:
        self.settings = settings
        self.services = services

        # Layer states — restored from disk if available
        self._layer_active = {1: False, 2: False, 3: False}
        self._layer_started_at = {1: 0.0, 2: 0.0, 3: 0.0}

        # Explicit user stop — when True, auto-start on boot is suppressed
        self._user_stopped = False
        self._load_persisted_state()

        # Strategic plan (cached from Layer 2)
        self._current_plan: StrategicPlan = StrategicPlan()
        self._plan_history: list[StrategicPlan] = []

        # Layer 2 settings
        self.brain_interval_seconds = 150  # 2.5 min: alternating Call A/B gives 5 min per call type
        self._brain_task: asyncio.Task | None = None
        self._call_type: str = "A"  # alternating: "A" = find trades, "B" = manage positions
        self._currently_executing: set[str] = set()  # symbols being executed in background
        self._executing_lock = asyncio.Lock()  # protects _currently_executing
        self._background_exec_task: asyncio.Task | None = None  # current background execution
        self._background_exec_start: float = 0.0  # monotonic start time of current bg exec

        # Recently closed positions — prevents immediate re-entry
        self._recently_closed: dict[str, float] = {}  # symbol -> close timestamp

        # Rolling cycle-time history for BRAIN_HEALTH aggregate. Emitted and
        # cleared every 6 total entries across {A, B, DO}. Observability only.
        self._cycle_times: dict[str, list[float]] = {"A": [], "B": [], "DO": []}

        # Layer 1 restructure Phase 3 — per-coin ensemble consensus cache.
        # StrategyWorker writes this each tick; ScannerWorker reads via
        # get_strategy_consensus(symbol) for the Phase 5 qualitative filter.
        # Stale entries preserved (StrategyWorker uses dict.update merge).
        self._strategy_consensus: dict[str, dict] = {}
        # Legacy summary alias kept for strategist.py:1017/1587 reads.
        self._strategy_consensus_summary: dict = {}
        # Phase 2 of the 1D briefing rewrite — per-coin full vote
        # distribution cache. StrategyWorker populates this in parallel
        # with ``_strategy_consensus`` (no impact on existing consumers).
        # Each entry shape: {"votes": {name: {vote, confidence, weight,
        # reasoning}}, "buy_weighted", "sell_weighted",
        # "neutral_weighted", "consensus", "consensus_direction",
        # "size_multiplier", "last_updated"}. Read via
        # ``get_strategy_votes(symbol)``. Memory budget: ~320 KB for 50
        # coins × ~25 strategies × 250 bytes per entry — negligible.
        self._strategy_votes: dict[str, dict] = {}
        # Strategy hints list (legacy; written under is_layer_active(3) gate).
        self._strategy_hints: list = []
        # Layer 1 restructure Phase 6 — selected-coin packages cache.
        # ScannerWorker writes this each tick after qualitative selection.
        # Phase 7 rewires strategist to read from here instead of querying
        # 12 services per cycle. Use ``get_coin_packages`` accessor below.
        self._coin_packages: dict = {}

        # Stage 2 phase 2 — per-coin TradeScorer 4-component breakdown
        # cache. StrategyWorker writes this in the same loop that fills
        # ``_score_cache`` (parity guaranteed: same scored universe).
        # Strategist reads via ``get_scorer_components(symbol)`` when
        # rendering the rich Layer 1B/1C per-coin block. Each entry:
        # ``{base, confluence, context, quality, total, grade,
        # last_updated}`` — mirrors ``ScoredSetup`` so the block can
        # cite the same numbers ScannerWorker's opportunity_score read.
        self._scorer_components: dict[str, dict] = {}

        # Definitive-fix Phase 6 (2026-04-28) — boot timestamp for the
        # cold-start completeness gate. ``time.time()`` (not monotonic)
        # because the gate compares against a wall-clock window.
        self._boot_time: float = time.time()

        # Phase 2 (post-Layer-1 fix) — disk/memory state sync heartbeat.
        # The heartbeat reads ``data/layer_state.json`` every
        # ``state_sync_interval_sec`` seconds and reconciles it against
        # the in-memory mirror. Phase 11 (dead-workers fix) reversed
        # the drift recovery direction: memory is the live source of
        # truth, disk is a persistence target. On drift, the heartbeat
        # re-writes disk from memory (default ``"rewrite_disk"``) so
        # an operator's just-toggled state cannot be silently overwritten
        # by a stale disk snapshot. Legacy ``"reload_memory"`` direction
        # is retained behind a config flag for emergency rollback only.
        # Task started by ``start_state_sync()`` after WorkerManager
        # constructs the LM.
        self._state_sync_task: asyncio.Task | None = None
        self._state_sync_started: bool = False
        self._drift_action: str = "rewrite_disk"

        # Layer 1 restructure Phase 4 — cold-start boundary enforcement.
        # Blueprint HR-8 (LAYER1_RESTRUCTURE_BLUEPRINT.md §18.8) mandates
        # that the first cycle after a cold start waits for the next
        # 5-minute boundary so all four sub-layers see fresh data
        # simultaneously. The original Phase-4 implementation only
        # ANNOUNCED the wait via CYCLE_RESUME_WAIT/CYCLE_RESUME log
        # markers and trusted the sweet-spot scheduler to produce the
        # right ordering. That trust is invalid for partial-window boots:
        # ``scanner_worker`` (offset 4:00) fires alone in the boot window
        # before upstream caches exist, producing fail_no_xray=50 and
        # restricting selection to forced (protected) positions only
        # for one full cycle.
        #
        # ``_cold_start_resume_done`` is the enforced equivalent. It is
        # consulted by every ``cycle_gated`` worker BEFORE each tick:
        # while False, the worker emits a rate-limited
        # ``LAYER1{B,C,D}_TICK_SKIP | reason=cold_start_boundary_pending``
        # marker and continues without doing work. The flag flips back
        # to True the moment ``_await_resume_boundary`` finishes its
        # sleep — i.e. exactly when ``CYCLE_RESUME`` is logged.
        #
        # Default is True (fail-open): a freshly-constructed LM whose
        # ``start_layer`` chain never schedules a wait does not deadlock
        # workers. Worker-side reads use ``getattr(..., default=True)``
        # so test fixtures that build LMs via ``__new__`` keep working
        # without modification.
        self._cold_start_resume_done: bool = True
        self._cold_start_resume_task: asyncio.Task | None = None

    # ─── State Persistence ───

    def _persist_state(self) -> bool:
        """Save layer states + user_stopped flag to disk.

        Phase 11 (dead-workers fix). Now emits ``LAYER_STATE_PERSIST_OK``
        on success and ``LAYER_STATE_PERSIST_FAIL`` on failure at WARNING
        — silent persistence failures are exactly what produced the
        Layer 3 toggle revert regression, so persist outcomes must be
        loud and structured.

        Returns:
            True on success, False on failure. Failure does not raise —
            callers are not expected to roll back in-memory state because
            the heartbeat re-persist will recover within
            ``state_sync_interval_sec`` once the disk-side issue clears.
        """
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "layer_active": {str(k): v for k, v in self._layer_active.items()},
                "user_stopped": self._user_stopped,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            _STATE_FILE.write_text(json.dumps(state, indent=2))
            log.info(
                f"LAYER_STATE_PERSIST_OK | "
                f"layer_active={dict(self._layer_active)} "
                f"user_stopped={self._user_stopped} | {ctx()}"
            )
            return True
        except Exception as e:
            log.warning(
                f"LAYER_STATE_PERSIST_FAIL | reason={type(e).__name__} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )
            return False

    def _load_persisted_state(self) -> None:
        """Restore user_stopped flag from disk. Layers start inactive regardless."""
        try:
            if _STATE_FILE.exists():
                state = json.loads(_STATE_FILE.read_text())
                self._user_stopped = state.get("user_stopped", False)
                if self._user_stopped:
                    log.info("Restored user_stopped=True from disk — auto-start suppressed")
        except Exception as e:
            log.warning("Failed to load persisted layer state: {err}", err=str(e))

    @property
    def user_stopped(self) -> bool:
        """Whether the user explicitly stopped trading (survives restarts)."""
        return self._user_stopped

    # ─── State Sync Heartbeat (Phase 2 post-Layer-1 fix) ───

    def start_state_sync(
        self,
        interval_sec: float = 60.0,
        *,
        on_drift_action: str = "rewrite_disk",
    ) -> None:
        """Start the disk/memory layer state sync heartbeat.

        Phase 2 (post-Layer-1 fix) — heartbeat infrastructure.
        Phase 11 (dead-workers fix) — drift recovery direction is now
        configurable; default flips from "disk wins" to "memory wins"
        because the prior default produced the Layer 3 toggle revert
        regression.

        Idempotent: subsequent calls are no-ops while the task is alive.
        Called by WorkerManager after constructing the LayerManager so
        the heartbeat starts as soon as the event loop is available.

        Args:
            interval_sec: Heartbeat cadence in seconds. Must be >= 10
                (validated by LayerManagerSettings). Default 60 catches
                drift within one Strategy/Scanner cycle.
            on_drift_action: How to recover when disk disagrees with
                memory. ``"rewrite_disk"`` (default) re-persists memory
                to disk and emits ``LAYER_STATE_DRIFT_RECOVERED``;
                ``"reload_memory"`` reloads memory from disk (legacy,
                emergency rollback only). Validated upstream.

        Raises:
            ValueError: If ``on_drift_action`` is not one of the two
                allowed values.
        """
        if on_drift_action not in ("rewrite_disk", "reload_memory"):
            raise ValueError(
                f"on_drift_action must be 'rewrite_disk' or 'reload_memory', "
                f"got {on_drift_action!r}"
            )
        self._drift_action = on_drift_action
        if self._state_sync_started and self._state_sync_task is not None and not self._state_sync_task.done():
            return
        self._state_sync_started = True
        self._state_sync_task = asyncio.create_task(
            self._state_sync_loop(interval_sec),
            name="layer_state_sync",
        )
        log.info(
            f"LAYER_STATE_SYNC_START | interval_sec={interval_sec:.1f} "
            f"on_drift_action={self._drift_action} | {ctx()}"
        )

    async def _state_sync_loop(self, interval_sec: float) -> None:
        """Run the heartbeat until cancelled.

        Sleeps first, then ticks; this avoids racing the very first
        ``_persist_state`` write that ``start_layer(1)`` may emit during
        the same boot second. Each tick is independent — a transient
        read failure logs a warning and continues without raising.
        """
        while True:
            try:
                await asyncio.sleep(interval_sec)
                self._sync_state_with_disk()
            except asyncio.CancelledError:
                log.info(f"LAYER_STATE_SYNC_STOP | reason=cancelled | {ctx()}")
                return
            except Exception as e:
                # Defensive: never let a sync failure kill the heartbeat.
                log.error(
                    f"LAYER_STATE_SYNC_LOOP_ERROR | err='{str(e)[:120]}' | {ctx()}"
                )

    def _sync_state_with_disk(self) -> None:
        """One heartbeat iteration. Compare disk vs memory; recover on drift.

        Phase 11 (dead-workers fix). The recovery direction is determined
        by ``self._drift_action``:

        - ``"rewrite_disk"`` (default): memory is the live source of
          truth. On drift, re-persist memory to disk and emit
          ``LAYER_STATE_DRIFT_RECOVERED | direction=memory_to_disk``.
          A failed persist surfaces as ``LAYER_STATE_PERSIST_FAIL`` in
          ``_persist_state``; the next heartbeat retries.
        - ``"reload_memory"`` (legacy): disk is the source of truth. On
          drift, overwrite memory from disk and emit the original
          ``LAYER_STATE_DRIFT | action=reload_from_disk`` event. Kept
          only for emergency rollback — produces the regression that
          Phase 11 fixes.

        Emits ``LAYER_STATE_SYNC | match=true|false ...`` every tick
        regardless of action so operators have a continuous heartbeat.
        """
        try:
            if not _STATE_FILE.exists():
                # No persisted state to sync against — first boot or
                # operator wiped it. Memory will persist on next toggle.
                log.debug(
                    f"LAYER_STATE_SYNC | match=na disk=missing memory={dict(self._layer_active)} | {ctx()}"
                )
                return
            disk_raw = json.loads(_STATE_FILE.read_text())
            disk_active_raw = disk_raw.get("layer_active", {})
            # JSON keys are strings; coerce to int for comparison with
            # in-memory dict whose keys are int.
            disk_active = {int(k): bool(v) for k, v in disk_active_raw.items()}
            memory_active = dict(self._layer_active)
            match = disk_active == memory_active

            log.info(
                f"LAYER_STATE_SYNC | match={str(match).lower()} "
                f"disk={disk_active} memory={memory_active} | {ctx()}"
            )

            if match:
                return

            if self._drift_action == "rewrite_disk":
                # Memory wins. Re-persist so disk catches up. The
                # _persist_state call emits LAYER_STATE_PERSIST_OK or
                # LAYER_STATE_PERSIST_FAIL; the DRIFT_RECOVERED event
                # is emitted unconditionally so the heartbeat trail
                # makes the recovery action explicit.
                log.warning(
                    f"LAYER_STATE_DRIFT_RECOVERED | "
                    f"direction=memory_to_disk "
                    f"disk={disk_active} memory={memory_active} "
                    f"reason=disk_was_stale | {ctx()}"
                )
                self._persist_state()
                return

            # Legacy "reload_memory" — disk wins. Reload only known keys
            # so a malformed file can't introduce phantom layers.
            log.warning(
                f"LAYER_STATE_DRIFT | disk={disk_active} memory={memory_active} "
                f"action=reload_from_disk | {ctx()}"
            )
            for layer_id in self._layer_active.keys():
                if layer_id in disk_active:
                    self._layer_active[layer_id] = disk_active[layer_id]
        except json.JSONDecodeError as e:
            log.warning(
                f"LAYER_STATE_SYNC_FAIL | reason=json_decode err='{str(e)[:120]}' | {ctx()}"
            )
        except Exception as e:
            log.warning(
                f"LAYER_STATE_SYNC_FAIL | reason=unexpected err='{str(e)[:120]}' | {ctx()}"
            )

    async def stop_state_sync(self) -> None:
        """Cancel the heartbeat task. Called during shutdown."""
        if self._state_sync_task and not self._state_sync_task.done():
            self._state_sync_task.cancel()
            try:
                await self._state_sync_task
            except asyncio.CancelledError:
                pass
        self._state_sync_started = False

    # ─── Layer Control ───

    async def start_layer(
        self,
        layer: int,
        *,
        reason: str = "unspecified",
        actor: str = "system",
    ) -> tuple[bool, str]:
        """Start a layer. Checks dependencies first. Clears user_stopped flag.

        Phase 2 (Layer 3 enforcement). Every successful state transition
        emits a ``LAYER_TOGGLE`` event so operators have an explicit audit
        trail of who turned what on/off and why. ``reason`` and ``actor``
        are forwarded by Telegram handlers / CLI paths; defaults are
        deliberately generic so unmodified callers still produce
        attributable logs (better than silent toggle).
        """
        log.info(
            f"LAYER_START | layer={layer} reason={reason} actor={actor} | {ctx()}"
        )
        self._user_stopped = False
        # Phase 11 (dead-workers fix). _persist_state() formerly ran HERE,
        # before the layer-specific toggle method. That captured the
        # PRE-toggle ``_layer_active`` snapshot on disk; the heartbeat at
        # ``state_sync_interval_sec`` then saw disk≠memory and reverted
        # memory to the (stale) disk state, silently dropping operator
        # toggles within ~30 s. Persist now runs AFTER the toggle (in the
        # ``if ok:`` branch below) so disk reflects the toggled state.
        # Early-return failure paths persist before returning so the
        # ``_user_stopped = False`` mutation (cleared above) is durable
        # even when the dependency check rejects the toggle.

        if layer == 1:
            result = await self._start_data_layer()
        elif layer == 2:
            if not self._layer_active[1]:
                self._persist_state()
                return False, "Cannot start Brain -- Data layer must be active first"
            result = await self._start_brain_layer()
        elif layer == 3:
            if not self._layer_active[1]:
                self._persist_state()
                return False, "Cannot start Trading -- Data layer must be active first"
            if not self._layer_active[2]:
                self._persist_state()
                return False, "Cannot start Trading -- Brain layer must be active first"
            result = await self._start_execution_layer()
        else:
            # Unknown layer — no in-memory mutation occurred, nothing to persist.
            return False, f"Unknown layer: {layer}"

        ok, msg = result
        if ok:
            # Phase 11 (dead-workers fix): persist AFTER ``_start_*_layer``
            # has flipped ``_layer_active[layer]`` in memory. Disk now
            # reflects the toggled state, so the next heartbeat sees
            # ``match=true`` and does not revert.
            self._persist_state()
            log.warning(
                f"LAYER_TOGGLE | layer={layer} from=False to=True "
                f"reason={reason} actor={actor} | {ctx()}"
            )
            # Layer 1 restructure Phase 4 — cold-start boundary wait.
            # When this start makes is_cycle_active() True, schedule a
            # one-shot CYCLE_RESUME_WAIT/CYCLE_RESUME pair so operators
            # see when the first analytical cycle will fire on a clean
            # 5-min boundary. Sweet-spot scheduler aligns to wall-clock,
            # but does NOT prevent a worker whose offset is still ahead
            # of ``now`` in the boot window (notably ``scanner_worker``
            # at 4:00) from firing in that boot window before upstream
            # caches exist. ``_cold_start_resume_done`` enforces the
            # blueprint contract by gating cycle_gated workers' ticks
            # until the boundary passes; see the constructor for the
            # rationale.
            if layer in (2, 3) and self.is_cycle_active():
                try:
                    secs = self._seconds_to_next_window_boundary()
                    log.info(
                        f"CYCLE_RESUME_WAIT | next_boundary_in_sec={secs:.0f} "
                        f"reason=cold_start_after_toggle | {ctx()}"
                    )
                    # Cancel any prior in-flight wait so an operator who
                    # rapidly toggles trading off→on→off→on does not leak
                    # overlapping tasks (each would race to flip the
                    # ``_cold_start_resume_done`` flag back to True at
                    # different boundaries). The handle is stored on
                    # ``self`` so a re-toggle can find and cancel it.
                    prior = self._cold_start_resume_task
                    if prior is not None and not prior.done():
                        prior.cancel()
                    if secs > 0:
                        # Flip the flag BEFORE creating the task so any
                        # in-flight worker tick already past its own
                        # ``is_cycle_active()`` check this turn does not
                        # squeeze a stale tick through. The new task
                        # restores the flag to True after the boundary
                        # passes.
                        self._cold_start_resume_done = False
                        self._cold_start_resume_task = asyncio.create_task(
                            self._await_resume_boundary(secs),
                            name="cold_start_resume_wait",
                        )
                    else:
                        # Already on a boundary — no wait needed; flag
                        # stays True. Emit the resume marker for parity
                        # with the wait-then-resume path so downstream
                        # log readers always see one CYCLE_RESUME per
                        # cold-start episode regardless of timing.
                        log.info(
                            f"CYCLE_RESUME | boundary={datetime.now(timezone.utc).isoformat()} "
                            f"reason=on_boundary | {ctx()}"
                        )
                except Exception as e:
                    # On unexpected failure leave the flag at its prior
                    # value (almost always True) so a logging glitch
                    # cannot wedge the analytical pipeline indefinitely.
                    log.warning(
                        f"CYCLE_RESUME_WAIT_FAIL | err='{str(e)[:80]}' | {ctx()}"
                    )
        return result

    async def _await_resume_boundary(self, secs: float) -> None:
        """Phase 4 — sleep until next M5 boundary, restore the cold-start
        gate, and emit CYCLE_RESUME.

        The original Phase 4 implementation only emitted a log line and
        relied on the sweet-spot scheduler to produce the right ordering.
        That is invalid for partial-window boots where ``scanner_worker``
        (offset 4:00) fires inside the boot window before upstream
        offsets 0:30..1:30 have lapsed. The gap is closed by gating
        cycle_gated worker ticks on ``_cold_start_resume_done``; this
        method is the only place that flips the flag back to True.

        Cancellation contract:
            If cancelled (e.g. an operator rapid-fire re-toggles trading,
            or the process is shutting down), we leave the flag at its
            current value. A re-toggle will schedule a fresh wait and
            re-clear the flag; a shutdown is unwinding the loop anyway.
            Restoring True on cancellation could let a stale post-cancel
            tick squeeze through before the new wait clears the flag,
            so we explicitly do NOT touch the flag here.
        """
        try:
            await asyncio.sleep(max(0.0, float(secs)))
        except asyncio.CancelledError:
            # See cancellation contract in the docstring — do not
            # mutate the flag; allow caller's restart path to own it.
            raise
        # Past the boundary: clear the gate and announce. Order matters —
        # flip the flag FIRST so a tick scheduled at the same instant
        # ``CYCLE_RESUME`` is emitted does not race against an
        # observer that reads the log before the flag is set.
        self._cold_start_resume_done = True
        self._cold_start_resume_task = None
        log.info(
            f"CYCLE_RESUME | boundary={datetime.now(timezone.utc).isoformat()} | {ctx()}"
        )

    async def stop_layer(
        self,
        layer: int,
        *,
        reason: str = "unspecified",
        actor: str = "system",
    ) -> tuple[bool, str]:
        """Stop a layer. Cascades downward. Sets user_stopped flag.

        Phase 2 (Layer 3 enforcement). Each cascading transition emits its
        own ``LAYER_TOGGLE`` line with shared reason+actor so the audit
        trail captures every layer the cascade touched (not just the
        outermost one).
        """
        messages = []
        toggled: list[int] = []

        if layer <= 3 and self._layer_active[3]:
            await self._stop_execution_layer()
            messages.append("Execution stopped")
            toggled.append(3)

        if layer <= 2 and self._layer_active[2]:
            await self._stop_brain_layer()
            messages.append("Brain stopped")
            toggled.append(2)

        if layer <= 1 and self._layer_active[1]:
            await self._stop_data_layer()
            messages.append("Data stopped")
            toggled.append(1)

        self._user_stopped = True
        self._persist_state()
        log.warning("User stopped trading — layers {l}+ stopped, persisted to disk", l=layer)

        for lyr in toggled:
            log.warning(
                f"LAYER_TOGGLE | layer={lyr} from=True to=False "
                f"reason={reason} actor={actor} cascade_root={layer} | {ctx()}"
            )

        return True, " | ".join(messages) if messages else "Already stopped"

    async def emergency_close_all(
        self, *, reason: str = "manual_emergency", actor: str = "operator",
    ) -> str:
        """Close ALL positions immediately. Stop Layers 2+3. Keep 1 running for data.

        Phase 2 (Layer 3 enforcement). Cascading layer toggles emit their
        own ``LAYER_TOGGLE`` lines below; ``reason``/``actor`` defaults
        match the original critical-emergency semantics.
        """
        log.critical(
            f"LAYER_EMERGENCY | closing_all reason={reason} actor={actor} | {ctx()}"
        )
        log.warning("EMERGENCY CLOSE ALL triggered")

        closed = []
        try:
            position_service = self.services.get("position_service")
            coordinator = self.services.get("trade_coordinator")
            if position_service:
                positions = await position_service.get_positions()
                for pos in positions:
                    try:
                        if coordinator:
                            coordinator.set_close_reason(pos.symbol, "emergency_manual")
                        await position_service.close_position(pos.symbol)
                        closed.append(pos.symbol)
                        log.warning("Emergency closed: {sym}", sym=pos.symbol)
                    except Exception as e:
                        log.error(
                            "Emergency close failed for {sym}: {err}",
                            sym=pos.symbol,
                            err=str(e),
                        )
        except Exception as e:
            log.error("Emergency close error: {err}", err=str(e))

        # Stop execution AND brain layers — Brain must not place new trades
        if self._layer_active[3]:
            await self._stop_execution_layer()
            log.warning(
                f"LAYER_TOGGLE | layer=3 from=True to=False "
                f"reason={reason} actor={actor} cascade_root=emergency | {ctx()}"
            )
        if self._layer_active[2]:
            await self._stop_brain_layer()
            log.warning(
                f"LAYER_TOGGLE | layer=2 from=True to=False "
                f"reason={reason} actor={actor} cascade_root=emergency | {ctx()}"
            )

        # Mark as user-stopped so restart doesn't auto-resume
        self._user_stopped = True
        self._persist_state()

        log.warning(
            "Emergency close complete: {n} positions closed, Layers 2+3 stopped, persisted",
            n=len(closed),
        )
        return f"Closed {len(closed)} positions: {', '.join(closed) or 'none'}. Trading stopped (L2+L3 off)."

    # ─── Layer Implementation ───

    async def _start_data_layer(self) -> tuple[bool, str]:
        """Mark data layer active. Workers are managed by WorkerManager."""
        self._layer_active[1] = True
        self._layer_started_at[1] = time.time()
        log.info("Layer 1 (DATA) started")
        return True, "Data layer started"

    async def _start_brain_layer(self) -> tuple[bool, str]:
        """Start the Claude strategic review cycle."""
        self._layer_active[2] = True
        self._layer_started_at[2] = time.time()

        self._brain_task = asyncio.create_task(self._brain_review_loop())

        log.info(
            "Layer 2 (BRAIN) started -- review every {sec}s",
            sec=self.brain_interval_seconds,
        )
        return True, f"Brain started (review every {self.brain_interval_seconds // 60}min)"

    async def _start_execution_layer(self) -> tuple[bool, str]:
        """Start execution layer: position reviews handled by PositionWatchdog."""
        self._layer_active[3] = True
        self._layer_started_at[3] = time.time()

        log.info("Layer 3 (EXECUTION) started — brain reviews via PositionWatchdog")
        return True, "Execution started -- trading live"

    async def _stop_data_layer(self) -> None:
        self._layer_active[1] = False
        log.info("Layer 1 (DATA) stopped")

    async def _stop_brain_layer(self) -> None:
        self._layer_active[2] = False
        if self._brain_task and not self._brain_task.done():
            self._brain_task.cancel()
        log.info("Layer 2 (BRAIN) stopped")

    async def _stop_execution_layer(self) -> None:
        self._layer_active[3] = False
        log.info("Layer 3 (EXECUTION) stopped — PositionWatchdog continues rule-based monitoring")

    # ─── Brain Review Loop ───

    async def _brain_review_loop(self) -> None:
        """Alternating brain cycle: Call A (trades) / Call B (positions) every 2.5 min.

        Strict alternation: A -> sleep(150) -> B -> sleep(150) -> A -> ...
        UrgentQueue handles urgency by injecting watchdog concerns into the
        next scheduled Call A / Call B prompt (max wait: brain_interval_seconds).
        EventBuffer entries still flow into the Call A prompt via
        strategist._build_trade_prompt -> event_buffer.get_prompt_text().

        The mandatory sleep is load-bearing: without it, background execution
        tasks accumulate on the single event loop and starve every other
        coroutine (brain cycles degrade from ~180s to 2000s+, dashboard
        auto-refresh times out). Do NOT reintroduce event-trigger bypasses.
        """
        while self._layer_active[2]:
            try:
                await self._run_brain_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Brain cycle failed: {err}", err=str(e))

            # Mandatory sleep — UrgentQueue drains into the next scheduled call.
            try:
                await asyncio.sleep(self.brain_interval_seconds)
            except asyncio.CancelledError:
                break

    async def _run_brain_cycle(self) -> None:
        """One brain cycle — dispatches to Call A or Call B based on alternation.

        Observability contract: every BRAIN_CYCLE_{A,B} START tag gets a matching
        _DONE or _FAIL tag in this method. Exceptions are logged here (not just
        by the outer loop) so the structured tag carries el= and err= fields;
        call-type alternation still advances on failure so the next cycle tries
        the other call type.

        Two deliberate non-paired exits: the Call-B price-divergence SKIP (below),
        and the Call-A BRAIN_CYCLE_A_SKIP emitted when a universe refresh has
        paused new-trade-finding — that path advances the toggle to "B" and
        returns without a _DONE, by design (the refresh is briefly pausing Call-A
        while open positions keep full management).
        """
        strategist = self.services.get("strategist")
        if not strategist:
            log.warning("No strategist service available")
            return

        t0 = time.time()
        _call_type_at_start = self._call_type

        if self._call_type == "A":
            # ═══ CALL A: Find New Trades ═══
            log.info(f"BRAIN_CYCLE_A | Finding new trades | {ctx()}")

            # ═══ UNIVERSE-REFRESH PAUSE GUARD (Phase 2) ═══
            # A universe refresh pauses ONLY Call-A (find-new-trades). Call-B
            # (manage open positions) and the watchdog keep running, so open
            # positions are never neglected. Skip this Call-A and advance the
            # toggle to Call-B so the next cycle manages positions normally.
            try:
                _rfs = self.services.get("universe_refresh_state")
                if _rfs is not None and _rfs.is_call_a_paused():
                    log.warning(
                        f"BRAIN_CYCLE_A_SKIP | reason=universe_refresh_paused "
                        f"rsn='{_rfs.reason()}' | {ctx()}"
                    )
                    self._call_type = "B"
                    return
            except Exception as _e:
                log.warning(
                    f"BRAIN_CYCLE_A_PAUSE_CHECK_ERR | err='{str(_e)[:150]}' | {ctx()}"
                )

            # Observability G1 (try/finally pairing): guarantees one
            # BRAIN_CYCLE_A_DONE emission per BRAIN_CYCLE_A entry on
            # every exit path including cancellation. The prior
            # try/except Exception did not catch BaseException
            # (CancelledError), creating an unpaired START in the
            # audited window. Behavior unchanged: returns, exception
            # propagation, and cycle-time bookkeeping all preserved.
            _status = "success"
            _trades_count = 0
            _market_view = ""
            elapsed_ms = 0
            try:
                try:
                    plan = await strategist.create_trade_plan()
                except Exception as _e:
                    elapsed_ms = int((time.time() - t0) * 1000)
                    log.error(
                        f"BRAIN_CYCLE_A_FAIL | el={elapsed_ms}ms err='{str(_e)[:200]}' | {ctx()}"
                    )
                    _status = "failed"
                    self._cycle_times["A"].append(float(elapsed_ms))
                    self._maybe_emit_brain_health()
                    self._call_type = "B"
                    return
                elapsed_ms = int((time.time() - t0) * 1000)

                if plan:
                    # Merge trade fields into current plan (preserve position_actions from last Call B)
                    self._current_plan.market_view = plan.market_view
                    self._current_plan.risk_level = plan.risk_level
                    self._current_plan.max_positions = plan.max_positions
                    self._current_plan.default_sl_pct = plan.default_sl_pct
                    self._current_plan.default_tp_pct = plan.default_tp_pct
                    self._current_plan.default_hold_minutes = plan.default_hold_minutes
                    self._current_plan.default_leverage = plan.default_leverage
                    self._current_plan.trailing_activation_pct = plan.trailing_activation_pct
                    self._current_plan.new_trades = plan.new_trades
                    self._current_plan.coin_directives = plan.coin_directives
                    self._current_plan.focus_coins = plan.focus_coins
                    self._current_plan.avoid_coins = plan.avoid_coins
                    self._current_plan.raw_reasoning = plan.raw_reasoning
                    self._current_plan.created_at = time.time()
                    self._current_plan.created_at_dt = datetime.now(timezone.utc)
                    self._plan_history.append(plan)
                    if len(self._plan_history) > 20:
                        self._plan_history = self._plan_history[-20:]

                    # Data Lake: record decision
                    self._record_decision_to_data_lake(plan, elapsed_ms, "call_a")

                    # Gate: only execute if Layer 3 active
                    if self._layer_active[3]:
                        if hasattr(plan, "new_trades") and plan.new_trades:
                            # Definitive-fix Phase 6 (2026-04-28) — cold-start
                            # completeness gate. Fires BEFORE any execution
                            # so the brain never trades on incomplete data.
                            # See ``_cold_start_block_or_none`` for the rules.
                            _block_reason = self._cold_start_block_or_none(plan)
                            if _block_reason is not None:
                                log.warning(_block_reason)
                                self._send_cold_start_telegram(_block_reason)
                            # ═══ GUARD: only ONE background execution at a time ═══
                            # Without this guard, concurrent asyncio.create_task()
                            # calls accumulate and starve the event loop (dashboard
                            # timeouts, brain cycle time inflation).
                            elif (
                                self._background_exec_task
                                and not self._background_exec_task.done()
                            ):
                                elapsed = time.time() - self._background_exec_start
                                log.warning(
                                    f"BRAIN_DO_SKIP | prev_still_running el={elapsed:.0f}s "
                                    f"trades={len(plan.new_trades)} | {ctx()}"
                                )
                            else:
                                # ═══ THINK/DO SPLIT: Execute in background ═══
                                self._background_exec_start = time.time()
                                self._background_exec_task = asyncio.create_task(
                                    self._execute_trades_background(plan)
                                )
                    else:
                        skipped_trades = len(plan.new_trades) if hasattr(plan, "new_trades") else 0
                        if skipped_trades:
                            # Phase 10 Gap C4 (output-quality obs): structure
                            # the layer-3-inactive drop log. Prior free-text
                            # message hid the per-symbol detail; the
                            # structured tag lets operators grep
                            # BRAIN_TRADES_DROPPED for visibility into
                            # silently-dropped brain decisions.
                            try:
                                _trade_syms = [
                                    getattr(t, "symbol", "?")
                                    for t in plan.new_trades
                                ][:10]
                            except Exception:
                                _trade_syms = []
                            log.warning(
                                f"BRAIN_TRADES_DROPPED | layer=3_inactive "
                                f"trades_count={skipped_trades} "
                                f"sample_syms={_trade_syms} | {ctx()}"
                            )
                            log.warning(
                                "Layer 3 inactive — skipped {t} new trades",
                                t=skipped_trades,
                            )

                    # Execute urgent position_actions from Call A (watchdog concerns)
                    if plan.position_actions:
                        log.info(
                            f"BRAIN_CYCLE_A_URGENT_ACTS | acts={len(plan.position_actions)} | {ctx()}"
                        )
                        if self._layer_active[3]:
                            await self._execute_position_actions(plan, source="call_a_urgent")
                        else:
                            # Phase 10 Gap C4 (output-quality obs): same
                            # structured emit for skipped urgent actions.
                            log.warning(
                                f"BRAIN_TRADES_DROPPED | layer=3_inactive "
                                f"actions_type=urgent_position "
                                f"actions_count={len(plan.position_actions)} | {ctx()}"
                            )
                            log.warning(
                                "Layer 3 inactive — skipped {a} urgent position actions",
                                a=len(plan.position_actions),
                            )

                    # Telegram notification
                    self._send_plan_telegram(plan)

                    _trades_count = len(plan.new_trades)
                    _market_view = plan.market_view[:80] if plan.market_view else ""
                else:
                    # START/END symmetry: strategist returned no plan.
                    _status = "empty_plan"

                self._cycle_times["A"].append(float(elapsed_ms))
                self._maybe_emit_brain_health()
                self._call_type = "B"  # Next cycle is position review
            except BaseException:
                # CancelledError / KeyboardInterrupt / SystemExit. Mark
                # status so the finally records it, then re-raise so
                # the outer brain-review loop and asyncio see the same
                # propagation they always saw.
                if elapsed_ms == 0:
                    elapsed_ms = int((time.time() - t0) * 1000)
                _status = "cancelled"
                raise
            finally:
                if elapsed_ms == 0:
                    elapsed_ms = int((time.time() - t0) * 1000)
                log.info(
                    f"BRAIN_CYCLE_A_DONE | el={elapsed_ms}ms status={_status} "
                    f"trades={_trades_count} view='{_market_view}' | {ctx()}"
                )

        else:
            # ═══ CALL B: Manage Positions ═══
            log.info(f"BRAIN_CYCLE_B | Managing positions | {ctx()}")

            # Skip if no open positions (save a Claude call)
            position_service = self.services.get("position_service")
            positions = await position_service.get_positions() if position_service else []
            if not positions:
                # BRAIN_CYCLE_B_SKIP retains its current shape; the
                # SKIP path is the only B exit that does NOT pair with
                # a DONE event by design (the cycle never engaged the
                # strategist). Operators distinguish skip vs done by
                # tag.
                log.info(f"BRAIN_CYCLE_B_SKIP | rsn='no open positions' | {ctx()}")
                self._call_type = "A"
                return

            # Observability G1 (try/finally pairing): guarantees one
            # BRAIN_CYCLE_B_DONE emission per strategist engagement on
            # every exit path including cancellation. Same rationale
            # as the CALL_A wrapper above.
            _status = "success"
            _acts_count = 0
            elapsed_ms = 0
            try:
                try:
                    plan = await strategist.create_position_plan()
                except Exception as _e:
                    elapsed_ms = int((time.time() - t0) * 1000)
                    log.error(
                        f"BRAIN_CYCLE_B_FAIL | el={elapsed_ms}ms err='{str(_e)[:200]}' | {ctx()}"
                    )
                    _status = "failed"
                    self._cycle_times["B"].append(float(elapsed_ms))
                    self._maybe_emit_brain_health()
                    self._call_type = "A"
                    return
                elapsed_ms = int((time.time() - t0) * 1000)

                if plan:
                    # Merge position_actions into current plan (preserve trade fields from last Call A)
                    self._current_plan.position_actions = plan.position_actions
                    self._current_plan.created_at = time.time()
                    self._current_plan.created_at_dt = datetime.now(timezone.utc)

                    # Data Lake: record decision
                    self._record_decision_to_data_lake(plan, elapsed_ms, "call_b")

                    # Gate: only execute if Layer 3 active
                    if self._layer_active[3]:
                        await self._execute_position_actions(plan, source="call_b")
                    else:
                        skipped_actions = len(plan.position_actions)
                        if skipped_actions:
                            log.warning(
                                "Layer 3 inactive — skipped {a} position actions",
                                a=skipped_actions,
                            )

                    # Telegram notification
                    self._send_plan_telegram(plan)
                    _acts_count = len(plan.position_actions)
                else:
                    _status = "empty_plan"

                self._cycle_times["B"].append(float(elapsed_ms))
                self._maybe_emit_brain_health()
                self._call_type = "A"  # Next cycle is trade finding
            except BaseException:
                if elapsed_ms == 0:
                    elapsed_ms = int((time.time() - t0) * 1000)
                _status = "cancelled"
                raise
            finally:
                if elapsed_ms == 0:
                    elapsed_ms = int((time.time() - t0) * 1000)
                log.info(
                    f"BRAIN_CYCLE_B_DONE | el={elapsed_ms}ms status={_status} "
                    f"acts={_acts_count} | {ctx()}"
                )

    def _maybe_emit_brain_health(self) -> None:
        """Emit BRAIN_HEALTH aggregate every 6 total cycle entries across A/B/DO.

        Observability only — clears the rolling history after emission.
        """
        total = sum(len(v) for v in self._cycle_times.values())
        if total < 6:
            return

        def _avg(xs: list[float]) -> float:
            return (sum(xs) / len(xs)) if xs else 0.0

        def _trend(xs: list[float]) -> str:
            if len(xs) < 2:
                return "n/a"
            return "growing" if xs[-1] > xs[0] * 2 else "stable"

        a = self._cycle_times["A"]
        b = self._cycle_times["B"]
        d = self._cycle_times["DO"]
        log.info(
            f"BRAIN_HEALTH | calls_A={len(a)} avg_A={_avg(a):.0f}ms | "
            f"calls_B={len(b)} avg_B={_avg(b):.0f}ms | "
            f"calls_DO={len(d)} avg_DO={_avg(d):.0f}ms | "
            f"trend_A={_trend(a)} trend_DO={_trend(d)} | {ctx()}"
        )
        self._cycle_times = {"A": [], "B": [], "DO": []}

    def _record_decision_to_data_lake(self, plan, elapsed_ms: int, decision_type: str) -> None:
        """Fire-and-forget data lake recording.

        Layer 2 Defect 2 (2026-05-22) — after the legacy strategic_review
        row, also emit one ``decision_type='trade_directive'`` row per
        individual trade Claude returned. Each per-trade row carries
        symbol + trade_directive_id (ts_epoch_symbol) + conviction so
        Layer 3-4 analyses can query per-trade decision context without
        parsing the per-review full_response JSON.
        """
        try:
            data_lake = self.services.get("data_lake")
            if not data_lake:
                return
            new_trades = list(getattr(plan, "new_trades", []) or [])
            # Write the per-review row first (legacy contract preserved).
            asyncio.create_task(data_lake.write_claude_decision(
                decision_type=decision_type,
                new_trades_count=len(new_trades),
                position_actions_count=len(plan.position_actions),
                market_view=getattr(plan, "market_view", "")[:200],
                risk_level=getattr(plan, "risk_level", ""),
                response_time_ms=elapsed_ms,
            ))
            # Write one row per trade directive Claude returned (D2).
            # Use a deterministic ts+symbol id so strategy_worker can
            # plumb the same id forward to trade_log.trade_id later.
            import time as _t
            _ts_ms = int(_t.time() * 1000)
            for _idx, _trade in enumerate(new_trades):
                try:
                    _sym = str(_trade.get("symbol", "") or "")
                    if not _sym:
                        continue
                    _did = f"{_ts_ms}_{_sym}_{_idx}"
                    _conv_raw = _trade.get("conviction") or _trade.get("confidence")
                    try:
                        _conv = float(_conv_raw) if _conv_raw is not None else None
                    except (TypeError, ValueError):
                        _conv = None
                    asyncio.create_task(data_lake.write_claude_decision(
                        decision_type="trade_directive",
                        market_view=str(_trade.get("reasoning", "") or "")[:200],
                        risk_level=str(_trade.get("direction", "") or ""),
                        response_time_ms=elapsed_ms,
                        symbol=_sym,
                        trade_directive_id=_did,
                        conviction=_conv,
                    ))
                except Exception as _per_e:
                    log.debug(
                        f"D2_PER_TRADE_DECISION_FAIL | idx={_idx} "
                        f"err='{str(_per_e)[:80]}'"
                    )
        except Exception as e:
            log.debug("data lake write failed: {err}", err=str(e))

    def _send_plan_telegram(self, plan) -> None:
        """Fire-and-forget Telegram notification."""
        alert_manager = self.services.get("alert_manager")
        if alert_manager:
            try:
                asyncio.create_task(
                    alert_manager.send_custom(plan.to_telegram_text(), AlertLevel.INFO)
                )
            except Exception as e:
                log.debug("telegram alert failed: {err}", err=str(e))

    def _cold_start_block_or_none(self, plan) -> str | None:
        """Return a structured BRAIN_*_BLOCK log line iff cold-start gate trips.

        Definitive-fix Phase 6 (2026-04-28). The brain auto-execute path
        used to fire on whatever ``_coin_packages`` contained — even
        when post-restart caches were still warming up and packages had
        completeness 0.67. This gate runs BEFORE any execution and
        decides whether the new-trades batch is safe to send.

        Rules (configured under ``[brain.cold_start_protection]``):

          1. Disable: when ``enabled=False`` always returns None (no gate).
          2. Empty packages: returns ``BRAIN_NO_PACKAGES`` regardless of
             grace window — Claude wouldn't have anything to inform a
             new-trade decision against.
          3. Boot grace: while ``time.time() - boot_time <
             boot_grace_period_sec``, demand
             ``avg_completeness >= boot_grace_completeness`` (default 0.95).
          4. Steady state: demand
             ``avg_completeness >= min_avg_completeness`` AND
             ``len(qualified_packages) >= min_qualified_packages``
             where qualified ≡ completeness >= ``min_per_package_completeness``.

        Returns:
            ``None`` when the gate is satisfied — proceed to execute.
            A pre-formatted structured log line on block (caller
            ``log.warning(...)`` and forwards to Telegram).
        """
        cfg = getattr(self.settings.brain, "cold_start_protection", None)
        if cfg is None or not getattr(cfg, "enabled", True):
            return None

        packages = self._coin_packages or {}
        n_trades = len(plan.new_trades) if hasattr(plan, "new_trades") else 0

        if not packages:
            return (
                f"BRAIN_NO_PACKAGES | reason=empty_packages_cache "
                f"trades_dropped={n_trades} | {ctx()}"
            )

        completeness_values = [
            float(getattr(p, "completeness", 1.0))
            for p in packages.values()
        ]
        n_pkg = len(completeness_values)
        avg = sum(completeness_values) / n_pkg
        # Q3d (2026-04-29) — gate counts packages that are EITHER scanner-
        # qualified (passed the 5-criterion qualitative gate) OR open-position
        # (HR-2 force-include for management). The previous implementation
        # used the local name ``qualified`` for "completeness >= 0.75",
        # which collided semantically with ``pkg.qualified`` (scanner's
        # gate result) and let BTC/ETH ref-pair force-includes count
        # toward ``min_qualified_packages``. The new ``qualified_count``
        # aligns the gate's count with the scanner's intent. Both checks
        # (scanner-qualified-or-held AND completeness-acceptable) must pass.
        qualified_count = sum(
            1 for p in packages.values()
            if (
                bool(getattr(p, "qualified", False))
                or getattr(p, "open_position", None) is not None
            )
            and float(getattr(p, "completeness", 1.0))
                >= cfg.min_per_package_completeness
        )
        seconds_since_boot = max(time.time() - self._boot_time, 0.0)
        in_boot_grace = seconds_since_boot < cfg.boot_grace_period_sec
        threshold = (
            cfg.boot_grace_completeness if in_boot_grace
            else cfg.min_avg_completeness
        )

        if avg < threshold:
            return (
                f"BRAIN_COLD_START_BLOCK | scope=avg "
                f"avg_completeness={avg:.2f} threshold={threshold:.2f} "
                f"packages={n_pkg} qualified={qualified_count} "
                f"boot_grace={'Y' if in_boot_grace else 'N'} "
                f"seconds_since_boot={seconds_since_boot:.0f} "
                f"trades_dropped={n_trades} | {ctx()}"
            )
        if (
            not in_boot_grace
            and qualified_count < cfg.min_qualified_packages
        ):
            return (
                f"BRAIN_INSUFFICIENT_QUALITY | scope=qualified_count "
                f"qualified={qualified_count} threshold={cfg.min_qualified_packages} "
                f"avg_completeness={avg:.2f} packages={n_pkg} "
                f"trades_dropped={n_trades} | {ctx()}"
            )
        return None

    def _send_cold_start_telegram(self, block_message: str) -> None:
        """Forward a cold-start block log line to Telegram at WARNING level."""
        alert_manager = self.services.get("alert_manager")
        if alert_manager is None:
            return
        try:
            asyncio.create_task(
                alert_manager.send_custom(
                    f"⚠️ Brain auto-execute blocked\n{block_message}",
                    AlertLevel.WARNING,
                )
            )
        except Exception as e:
            log.debug("cold-start telegram alert failed: {err}", err=str(e))

    async def _execute_position_actions(
        self, plan: StrategicPlan, *, source: str = "strategic_review",
    ) -> None:
        """Execute Claude's instructions for existing positions.

        Args:
            plan: Strategic plan containing position_actions.
            source: Origin tag forwarded to the SENTINEL firewall.
                    "call_b"        — Call B position review cycle (trusted).
                    "call_a_urgent" — Call A urgent position actions (trusted).
                    Default "strategic_review" keeps legacy firewall behavior.
        """
        position_service = self.services.get("position_service")
        if not position_service:
            return

        # Queue actions to TradeCoordinator — PositionWatchdog executes them next tick
        coordinator = self.services.get("trade_coordinator")
        if not coordinator:
            log.warning("No trade_coordinator — cannot queue position actions")
            return

        # T1-1 / F18 phantom-close defense (six-tier-fixes 2026-05-11) —
        # snapshot active symbols once per dispatch so the firewall and
        # this layer's own check share a coherent view of what is open.
        # See dev_notes/six_tier_fixes/t1_1_phase1_investigation.md.
        # T2-7 (2026-05-12): the snapshot is taken HERE, NOT at CALL_B
        # prompt-build time (~60-240 s earlier). That means the
        # `active_symbols` check is fresh as of the moment we apply
        # decisions — even if positions closed during the slow CALL_B,
        # this set reflects the current state of the world. The check
        # is then extended to cover ALL non-hold actions (not just
        # close/take_profit) so tighten_stop / set_exit / scale-out
        # actions targeted at a closed position are also rejected.
        active_symbols = (
            coordinator.active_symbols()
            if hasattr(coordinator, "active_symbols")
            else frozenset()
        )

        for symbol, action in plan.position_actions.items():
            if action.action == "hold":
                continue

            # T1-1 / F18 phantom-close defense (layer_manager layer of the
            # three-layer guard). Runs BEFORE the firewall so even when
            # the firewall is disabled in settings the close-on-closed
            # path still cannot reach queue_strategic_action.
            #
            # T2-7 (2026-05-12) extension: ANY non-hold action targeted
            # at a closed position is rejected. Pre-fix only
            # close/take_profit were guarded; tighten_stop / set_exit /
            # other actions could still queue and produce no-op work or
            # spurious watchdog logs. Now all non-hold actions check
            # the freshly-snapshotted active_symbols set.
            if symbol not in active_symbols:
                log.warning(
                    f"CALL_B_STALE_SNAPSHOT_DETECTED | layer=layer_manager "
                    f"sym={symbol} act={action.action} src={source} "
                    f"rsn='{str(action.reason)[:80]}' "
                    f"reason=symbol_not_in_active_set | {ctx()}"
                )
                continue

            # SENTINEL Exit Firewall: block untrusted sources from closing positions
            if self.settings.sentinel.enabled and self.settings.sentinel.firewall_enabled:
                from src.sentinel.firewall import should_allow_strategic_action
                allowed, explanation = should_allow_strategic_action(
                    action.action, symbol, action.reason, source=source,
                    active_symbols=active_symbols,
                )
                if not allowed:
                    continue

            # For close actions, record the reason for proper attribution.
            # T6-8 / Phase5 F-21 fix (six-tier-fixes 2026-05-11) — pre-fix
            # this passed a mid-word-truncated 100-char narrative
            # (e.g. "strategic_review: Listed as recently closed with cool")
            # which produced cardinality-explosion + CSV-break + log-
            # parsing-ambiguity in trade_history.exit_reason and the
            # closed_by audit fields. Post-fix: the closed_by enum stays
            # the stable token "strategic_review"; the narrative lives
            # only in the existing STRAT_POS_ACT log line below which is
            # log-only and not used as a categorical column.
            if action.action in ("close", "take_profit"):
                coordinator.set_close_reason(symbol, "strategic_review")

            coordinator.queue_strategic_action(
                symbol=symbol,
                action=action.action,
                reason=action.reason,
                new_sl=action.new_sl if hasattr(action, "new_sl") else 0,
                exit_price=action.exit_price if hasattr(action, "exit_price") else 0,
            )
            log.info(f"STRAT_POS_ACT | sym={symbol} act={action.action} rsn='{str(action.reason)[:80]}' | {ctx()}")

    async def _execute_trades_background(self, plan) -> None:
        """Execute trade directives in background — does NOT block brain loop.

        Wraps _execute_new_trades with a 5-minute safety timeout, logging,
        and error handling. Called via asyncio.create_task() from
        _run_brain_cycle().

        If a run exceeds 300s something is wrong (APEX stall, Shadow hang,
        DB lock) and the task is aborted so the next scheduled cycle is
        not blocked. Cancellation propagates into APEX asyncio.gather and
        per-trade tasks; the _currently_executing set is cleaned in the
        per-symbol finally block of _execute_new_trades.
        """
        exec_start = time.time()
        try:
            log.info(f"BRAIN_DO_START | trades={len(plan.new_trades)} | {ctx()}")
            await asyncio.wait_for(self._execute_new_trades(plan), timeout=300)
            elapsed = time.time() - exec_start
            log.info(f"BRAIN_DO_DONE | el={elapsed:.0f}s | {ctx()}")
        except asyncio.TimeoutError:
            elapsed = time.time() - exec_start
            log.error(f"BRAIN_DO_TIMEOUT | el={elapsed:.0f}s | aborted | {ctx()}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = time.time() - exec_start
            log.error(f"BRAIN_DO_FAIL | el={elapsed:.0f}s err='{str(e)[:200]}' | {ctx()}")
        finally:
            # Record DO-cycle elapsed for BRAIN_HEALTH aggregate (observability only)
            try:
                self._cycle_times["DO"].append((time.time() - exec_start) * 1000)
                self._maybe_emit_brain_health()
            except Exception:
                pass

    def _emit_directive_rejected(
        self,
        *,
        sym: str,
        direction: str,
        rsn: str,
        detail: str,
        blocker_layer: str,
        did: str,
    ) -> None:
        """Emit a canonical ``STRAT_DIRECTIVE_REJECTED`` lifecycle event.

        Gap 3 fix (2026-05-19) — closes the silent-skip observability gap
        identified in `dev_notes/gaps_fix/gap3_phase1_synthesis.md`. Every
        rejection path in `_execute_new_trades` calls this helper IMMEDIATELY
        BEFORE its existing ``TRADE_SKIP`` log + ``continue``. The new event
        is a COMPLEMENT, not a replacement — TRADE_SKIP retains its per-site
        detail; this event names the originating brain directive (via ``did``)
        in one canonical, grep-able form.

        Args:
            sym: Symbol of the rejected directive.
            direction: Original brain-proposed direction (``Buy``/``Sell``/``?``).
            rsn: Reason code, matches existing TRADE_SKIP ``rsn=`` value
                (``halt``, ``invalid_directive``, ``pos_gate``,
                ``gate_rejected``, ``sanity_reject``, ``enforcer_block``,
                ``survival_block``, ``xray_skip``, ``xray_conflict``,
                ``dup_position``, ``service_missing``, ``price_fetch_fail``,
                ``order_reject``, ``exception``, etc.).
            detail: Brief detail (truncated to 120 chars for log line length).
            blocker_layer: Which architectural layer absorbed the directive.
                One of: ``halt`` (pre-loop pnl/enforcer halt),
                ``orchestration`` (layer_manager itself: invalid format,
                pos_gate, exception), ``gate`` (apex/gate.py CHECK 1-14),
                ``strategy_worker`` (apex/strategy_worker internal skip).
            did: Originating brain decision ID. Captured at loop entry via
                ``get_did()`` snapshot for belt-and-suspenders propagation
                (the ``ctx()`` suffix also includes did but the explicit
                attribute survives any contextvars edge case).

        The event fires at INFO level. Rejections are normal operational
        outcomes, not errors — the existing TRADE_SKIP retains WARNING for
        the same site. INFO here keeps the new event grep-friendly without
        cluttering warning-level dashboards.
        """
        log.info(
            f"STRAT_DIRECTIVE_REJECTED | sym={sym} dir={direction} "
            f"rsn={rsn} detail='{(detail or '')[:120]}' "
            f"blocker_layer={blocker_layer} did={did} | {ctx()}"
        )

    async def _execute_new_trades(self, plan) -> None:
        """Execute Claude's new trade commands. Called from _execute_trades_background.

        Delegates to strategy_worker._execute_claude_trade() which handles
        symbol validation, SL/TP validation, qty rounding, order placement,
        thesis saving, and alerting.
        """
        # Gap 3 fix (2026-05-19) — snapshot did at loop entry so every
        # STRAT_DIRECTIVE_REJECTED event in this iteration explicitly
        # carries the originating brain decision ID, even if contextvars
        # are unexpectedly reset by a downstream coroutine. The ctx()
        # suffix also includes did; this explicit snapshot is
        # belt-and-suspenders defensive coding.
        _loop_did = get_did()
        # Manual-pause gate — Telegram /pause sets pnl_manager._manual_pause.
        # The strategy_worker tick path already reads this via can_trade();
        # the brain-initiated path doesn't, so check explicitly here.
        pnl_mgr = self.services.get("pnl_manager")
        if pnl_mgr and hasattr(pnl_mgr, "can_trade"):
            allowed, reason = pnl_mgr.can_trade()
            if not allowed:
                log.warning(f"BRAIN_TRADE_HALT | rsn='{reason}' | {ctx()}")
                # Gap 3 fix — emit one STRAT_DIRECTIVE_REJECTED per pending
                # directive so the halt path surfaces the dropped batch.
                for _t in plan.new_trades:
                    if isinstance(_t, dict):
                        self._emit_directive_rejected(
                            sym=_t.get("symbol", "?"),
                            direction=_t.get("direction", "?"),
                            rsn="halt",
                            detail=f"pnl_manager_halt: {reason}",
                            blocker_layer="halt",
                            did=_loop_did,
                        )
                return

        # Enforcer halt check — block ALL new trades if performance is too bad.
        enforcer = self.services.get("enforcer")
        # Phase 16 (P1-15): re-evaluate the enforcer level BEFORE the
        # halt check. The brief observed Level 1->2 escalation firing
        # 18 s AFTER trades opened — the trades had been permitted
        # under Level 1 but would have been blocked under Level 2. The
        # `enforcer_worker` runs check_and_enforce on its own cadence;
        # invoking it here makes the level reflect the very latest
        # state (last trade outcome) before should_allow_trade reads it.
        # Best-effort: failure is non-blocking so a slow check_and_enforce
        # never starves the trade path.
        if enforcer and hasattr(enforcer, "check_and_enforce"):
            try:
                await enforcer.check_and_enforce()
            except Exception as _e:
                log.warning(
                    f"ENFORCER_PRECHECK_FAIL | err='{str(_e)[:120]}' | {ctx()}"
                )
        if enforcer and hasattr(enforcer, "should_allow_trade"):
            allowed, reason = enforcer.should_allow_trade(leverage=1)
            if not allowed:
                log.warning(f"STRAT_L4_HALT | rsn='{reason}' | {ctx()}")
                # Gap 3 fix — emit one STRAT_DIRECTIVE_REJECTED per pending
                # directive so the enforcer-halt path surfaces the dropped
                # batch.
                for _t in plan.new_trades:
                    if isinstance(_t, dict):
                        self._emit_directive_rejected(
                            sym=_t.get("symbol", "?"),
                            direction=_t.get("direction", "?"),
                            rsn="halt",
                            detail=f"enforcer_halt: {reason}",
                            blocker_layer="halt",
                            did=_loop_did,
                        )
                return

        strategy_worker = self.services.get("strategy_worker")
        if not strategy_worker or not hasattr(strategy_worker, "_execute_claude_trade"):
            log.warning("No strategy_worker available for trade execution")
            return

        position_service = self.services.get("position_service")
        current_positions = await position_service.get_positions() if position_service else []
        position_symbols = {p.symbol for p in current_positions}

        # ═══ [POS] GATE: merge with currently executing symbols ═══
        async with self._executing_lock:
            blocked_symbols = position_symbols | self._currently_executing

        # ═══ APEX: Optimize ALL directives in parallel before execution ═══
        # Each directive is sent to DeepSeek concurrently — one await for all coins.
        # Failures are caught per-trade; a failed optimization falls back to
        # Claude's original params and NEVER blocks trade execution.
        apex = self.services.get("apex_optimizer")
        optimized_results: dict = {}
        # Phase 5: stamp Claude's pre-APEX directive size so the gate's
        # CHECK 0 can cap any downstream inflation (APEX DeepSeek OR
        # conviction weighting) at N× the original. Stamped on EVERY valid
        # dict trade regardless of whether APEX runs — a failed optimize()
        # still needs the cap reference.
        for _t in plan.new_trades:
            if isinstance(_t, dict) and "_claude_original_size_usd" not in _t:
                try:
                    _t["_claude_original_size_usd"] = float(_t.get("size_usd", 0) or 0)
                except Exception:
                    _t["_claude_original_size_usd"] = 0.0
            # Brain-authoritative in-cycle aggregate guard (2026-05-31): stamp
            # the cycle did so APEX gate CHECK 4 can reset its per-cycle
            # reservation accumulator on each new brain cycle (it caps the sum of
            # a cycle's trades to usable capital since fund_manager.in_use is
            # stale within a cycle).
            if isinstance(_t, dict):
                _t["_cycle_did"] = _loop_did
        # Sniper-Latency-Size Fix Phase 3A (2026-05-07) — stamp conviction
        # signals from the per-coin CoinPackage onto each trade dict so
        # the downstream sizing layers (APEX gate CHECK 4) can consume
        # them. Phase 0 investigation showed 13 of 15 trades had
        # identical entry_xray_confidence=0.7 and entry_setup_type=
        # bearish_fvg_ob yet produced sizes ranging $100-$300, because
        # neither value was wired into the conviction-weight formula.
        # The signals come from CoinPackage (deterministic system
        # computation) rather than Claude's self-reported numbers, so
        # the size derivation is auditable and reproducible.
        _packages_for_conviction = (
            self._coin_packages or {}
            if hasattr(self, "_coin_packages")
            else {}
        )
        for _t in plan.new_trades:
            if not isinstance(_t, dict):
                continue
            _sym = _t.get("symbol", "")
            _pkg = _packages_for_conviction.get(_sym) if _sym else None
            if _pkg is None:
                # Default to neutral conviction so absence of package
                # data does not penalise or amplify size.
                _t.setdefault("_xray_confidence", 0.0)
                _t.setdefault("_setup_score", 0.0)
                _t.setdefault("_expected_rr", 0.0)
                # Entry-quality filters (2026-07-07): -1.0 = UNKNOWN
                # sentinel (distinct from a genuine low 0.0) so the apex
                # gate's per-leg fail-open can tell "no package data"
                # from "package says weak" and never blocks on missing
                # data.
                _t.setdefault("_signal_confidence", -1.0)
                _t.setdefault("_entry_adx", -1.0)
                continue
            try:
                _xray_block = getattr(_pkg, "xray", None)
                _t.setdefault(
                    "_xray_confidence",
                    float(getattr(_xray_block, "setup_type_confidence", 0.0) or 0.0),
                )
                _t.setdefault(
                    "_setup_score",
                    float(getattr(_xray_block, "setup_score", 0.0) or 0.0),
                )
                _levels = getattr(_xray_block, "structural_levels", None)
                _t.setdefault(
                    "_expected_rr",
                    float(getattr(_levels, "rr_ratio", 0.0) or 0.0),
                )
                # Entry-quality filters (2026-07-07) — stamp the two
                # additional per-leg inputs from the SAME deterministic
                # CoinPackage the conviction fields come from, so the
                # apex gate needs zero service calls at validate time.
                # signals.confidence = SignalWorker per-coin confidence;
                # strategies.scoring_regime_adx = ADX from the scored-
                # regime snapshot (Issue #2 fields). -1.0 = unknown
                # (that leg fails open at the gate).
                _signals_block = getattr(_pkg, "signals", None)
                _t.setdefault(
                    "_signal_confidence",
                    float(getattr(_signals_block, "confidence", -1.0))
                    if _signals_block is not None else -1.0,
                )
                _strats_block = getattr(_pkg, "strategies", None)
                _t.setdefault(
                    "_entry_adx",
                    float(getattr(_strats_block, "scoring_regime_adx", -1.0))
                    if _strats_block is not None else -1.0,
                )
            except Exception:
                _t.setdefault("_xray_confidence", 0.0)
                _t.setdefault("_setup_score", 0.0)
                _t.setdefault("_expected_rr", 0.0)
                _t.setdefault("_signal_confidence", -1.0)
                _t.setdefault("_entry_adx", -1.0)
        if apex:
            _apex_tasks = {}
            for _i, _t in enumerate(plan.new_trades):
                if isinstance(_t, dict) and _t.get("symbol"):
                    _apex_tasks[_i] = apex.optimize(_t, plan)
            if _apex_tasks:
                _apex_results = await asyncio.gather(
                    *_apex_tasks.values(), return_exceptions=True
                )
                for _idx, _res in zip(_apex_tasks.keys(), _apex_results):
                    if isinstance(_res, Exception):
                        _sym = plan.new_trades[_idx].get("symbol", "?")
                        log.warning(
                            f"APEX_GATHER_FAIL | sym={_sym} "
                            f"err='{str(_res)[:80]}' | {ctx()}"
                        )
                    else:
                        optimized_results[_idx] = _res

        executed = 0
        _total_trades = len(plan.new_trades)
        # Issue 2 (CALL_A exploit/fetch, 2026-06-05) — gate-open observability.
        # Counts trades that executed this cycle while carrying the X-RAY
        # suppression booklog flag, i.e. trades X-RAY suppression WOULD have
        # blocked were suppression enabled. With suppression OFF (the operator
        # default) these execute; this counter makes "the gates are open and N
        # candidates passed that suppression would have killed" visible per
        # cycle without grepping the per-trade XRAY_BOOKLOG lines. Observability
        # only — no gate logic is changed and suppression is NOT re-enabled.
        _booklog_passed = 0
        # Skip-reason accounting so the summary always explains "N/M executed"
        skipped_by_reason: dict[str, int] = {}
        def _bump_skip(rsn: str) -> None:
            skipped_by_reason[rsn] = skipped_by_reason.get(rsn, 0) + 1

        for i, trade in enumerate(plan.new_trades):
            if not isinstance(trade, dict):
                _bump_skip("invalid_directive")
                log.warning(
                    f"TRADE_SKIP | sym=? rsn=invalid_directive "
                    f"detail='type={type(trade).__name__}' idx={i} | {ctx()}"
                )
                # Gap 3 fix — unify the silent-skip lifecycle event.
                self._emit_directive_rejected(
                    sym="?",
                    direction="?",
                    rsn="invalid_directive",
                    detail=f"type={type(trade).__name__} idx={i}",
                    blocker_layer="orchestration",
                    did=_loop_did,
                )
                continue
            symbol = trade.get("symbol", "")

            # ═══ [POS] GATE: skip coins with open positions or in-flight execution ═══
            if symbol in blocked_symbols:
                rsn = "open_position" if symbol in position_symbols else "executing"
                log.info(f"POS_GATE_BLOCK | sym={symbol} rsn='{rsn}' | {ctx()}")
                _bump_skip("pos_gate")
                log.info(
                    f"TRADE_SKIP | sym={symbol} rsn=pos_gate "
                    f"detail='{rsn}' | {ctx()}"
                )
                # Gap 3 fix — unify the silent-skip lifecycle event.
                self._emit_directive_rejected(
                    sym=symbol,
                    direction=trade.get("direction", "?"),
                    rsn="pos_gate",
                    detail=rsn,
                    blocker_layer="orchestration",
                    did=_loop_did,
                )
                continue

            # Per-trade timing for BRAIN_DO_TRADE summary (observability only)
            _trade_start = time.time()
            _apex_apply_ms = 0.0
            _gate_ms = 0.0
            _exec_ms = 0.0
            _reason_code = "n/a"  # populated by _execute_claude_trade return tuple

            # Mark as currently executing (prevent duplicate from next cycle)
            async with self._executing_lock:
                self._currently_executing.add(symbol)

            try:
                # Apply APEX optimization if available for this directive
                if i in optimized_results:
                    _t0 = time.time()
                    trade = await self._apply_apex_optimization(trade, optimized_results[i])
                    _apex_apply_ms = (time.time() - _t0) * 1000
                # TradeGate: hard safety limits + (new) reject path.
                # T2-1 / F20 + T2-2 / F14 introduce a `_gate_rejected`
                # flag the gate can set when a trade should NOT proceed
                # (revenge-trade same-direction loss cooldown, zero-
                # conviction floor). Trades carrying the flag are
                # skipped here with a structured GATE_REJECT log.
                gate = self.services.get("apex_gate")
                if gate:
                    _t0 = time.time()
                    trade = await gate.validate(trade)
                    _gate_ms = (time.time() - _t0) * 1000
                if trade.get("_gate_rejected"):
                    _gate_reject_reason = str(trade.get("_gate_rejected"))
                    log.warning(
                        f"TRADE_SKIP | sym={symbol} rsn=gate_rejected "
                        f"detail='{_gate_reject_reason[:120]}' | {ctx()}"
                    )
                    _bump_skip("gate_rejected")
                    # Gap 3 fix — unify the silent-skip lifecycle event.
                    # The gate already wrote `_gate_rejected` with the
                    # specific CHECK name (e.g. ``reentry_learning_gate_*``,
                    # ``zero_conviction_*``). Surface that here so a single
                    # ``grep STRAT_DIRECTIVE_REJECTED`` shows the directive +
                    # the CHECK that absorbed it.
                    self._emit_directive_rejected(
                        sym=symbol,
                        direction=trade.get("direction", "?"),
                        rsn="gate_rejected",
                        detail=_gate_reject_reason,
                        blocker_layer="gate",
                        did=_loop_did,
                    )
                    continue
                _exec_t0 = time.time()
                try:
                    success, _reason_code = await strategy_worker._execute_claude_trade(
                        trade, position_symbols, plan,
                    )
                    _exec_ms = (time.time() - _exec_t0) * 1000
                    if success:
                        executed += 1
                        position_symbols.add(symbol)
                        # Issue 2 — count executed trades that X-RAY
                        # suppression would have blocked (gates open).
                        if isinstance(trade, dict) and trade.get(
                            "_xray_suppression_booklog"
                        ):
                            _booklog_passed += 1
                    else:
                        _bump_skip(_reason_code or "unknown")
                        # strategy_worker already logged TRADE_SKIP at the failure
                        # site; the summary at tick end will show the aggregate.
                        # Gap 3 fix — unify the silent-skip lifecycle event so
                        # the rejection is grep-able by directive id from a
                        # single canonical event name. strategy_worker's
                        # internal TRADE_SKIP retains its per-site detail.
                        self._emit_directive_rejected(
                            sym=symbol,
                            direction=trade.get("direction", "?"),
                            rsn=_reason_code or "unknown",
                            detail=f"strategy_worker rejected: {_reason_code or 'unknown'}",
                            blocker_layer="strategy_worker",
                            did=_loop_did,
                        )
                except Exception as e:
                    _exec_ms = (time.time() - _exec_t0) * 1000
                    _reason_code = "exception"
                    _bump_skip("exception")
                    log.error(
                        "Claude trade failed for {sym}: {err}",
                        sym=symbol, err=str(e),
                    )
                    log.warning(
                        f"TRADE_SKIP | sym={symbol} rsn=exception "
                        f"detail='{str(e)[:100]}' | {ctx()}"
                    )
                    # Gap 3 fix — unify the silent-skip lifecycle event.
                    self._emit_directive_rejected(
                        sym=symbol,
                        direction=trade.get("direction", "?"),
                        rsn="exception",
                        detail=str(e)[:100],
                        blocker_layer="orchestration",
                        did=_loop_did,
                    )
            finally:
                # Always remove from executing set after completion
                async with self._executing_lock:
                    self._currently_executing.discard(symbol)

                # Per-trade summary. apex_ds = DeepSeek latency already stored on
                # the directive by APEX; apex_apply = pct→price conversion only.
                _trade_el_ms = (time.time() - _trade_start) * 1000
                _apex_ds_ms = 0.0
                _gate_propagated_ms = 0.0
                if isinstance(trade, dict):
                    try:
                        _apex_ds_ms = float(trade.get("_apex_response_ms") or 0)
                        _gate_propagated_ms = float(trade.get("_gate_validation_ms") or 0)
                    except Exception:
                        pass
                # Prefer the gate-propagated value if present (set by apex/gate.py
                # in Phase 7); fall back to the local measurement otherwise.
                _gate_final_ms = _gate_propagated_ms if _gate_propagated_ms > 0 else _gate_ms
                log.info(
                    f"BRAIN_DO_TRADE | sym={symbol} [{i+1}/{_total_trades}] "
                    f"el={_trade_el_ms:.0f}ms | apex_apply={_apex_apply_ms:.0f}ms "
                    f"apex_ds={_apex_ds_ms:.0f}ms gate={_gate_final_ms:.0f}ms "
                    f"exec={_exec_ms:.0f}ms rsn={_reason_code} | {ctx()}"
                )

        # Always emit the summary — even at 0 executed — so ops can distinguish
        # "Claude proposed nothing" from "Claude proposed N but all were skipped".
        _skips_str = ",".join(f"{k}={v}" for k, v in sorted(skipped_by_reason.items())) or "none"
        log.info(
            f"Claude new trades: {executed}/{_total_trades} executed | skipped={{{_skips_str}}}"
        )
        # Issue 2 (CALL_A exploit/fetch) — per-cycle gate-open proof. Reports
        # how many of the executed trades X-RAY suppression WOULD have blocked
        # had it been enabled; with suppression OFF they passed (gates open).
        log.info(
            f"XRAY_BOOKLOG_CYCLE | passed={_booklog_passed}/{executed} executed "
            f"| trades opened that X-RAY suppression would have blocked "
            f"(gates open, suppression OFF) | {ctx()}"
        )

    async def _apply_apex_optimization(self, original: dict, optimized) -> dict:
        """Apply APEX-optimized parameters to a Claude directive dict.

        Converts APEX percentage-based SL/TP to absolute prices using the
        current ticker price (5-second cache — same source used by
        strategy_worker._execute_claude_trade a moment later).

        If optimized.is_fallback is True, returns the original dict unchanged
        so that Claude's exact SL/TP prices are preserved without any lossy
        pct→price conversion.

        Args:
            original: Claude's original trade directive dict.
            optimized: OptimizedTrade returned by TradeOptimizer.optimize().

        Returns:
            Modified copy of the directive dict with APEX parameters applied,
            or the original dict if is_fallback is True or price is unavailable.
        """
        if getattr(optimized, "is_fallback", False):
            return original

        modified = dict(original)
        modified["direction"] = optimized.direction
        modified["size_usd"] = optimized.position_size_usd
        modified["leverage"] = optimized.leverage
        # Sniper-Latency-Size Fix Phase 3D (2026-05-07) — sizing
        # breadcrumb. Captures the size APEX produced before any
        # downstream gate or enforcer modifies it, so the unified
        # SIZE_DERIVATION event can show the per-layer chain.
        modified["_apex_size_usd"] = float(optimized.position_size_usd or 0)

        # Get current price for SL/TP percentage → absolute price conversion.
        # market_service.get_ticker() uses a 5-second cache, so this is fast.
        symbol = original.get("symbol", "")
        current_price = 0.0
        market_svc = self.services.get("market_service")
        if market_svc and symbol:
            try:
                ticker = await market_svc.get_ticker(symbol)
                current_price = ticker.last_price
            except Exception:
                pass

        if current_price <= 0:
            # Cannot convert percentages — leave original SL/TP, still apply
            # the direction/size/leverage improvements from APEX.
            log.warning(
                f"APEX_PRICE_FAIL | sym={symbol} cannot_convert_pct "
                f"keeping_original_sl_tp | {ctx()}"
            )
            # Store APEX metadata even in this partial-apply case
            modified["_apex_optimized"] = True
            modified["_apex_was_flipped"] = optimized.was_flipped
            modified["_apex_confidence"] = optimized.confidence
            modified["_apex_tp_mode"] = optimized.tp_mode
            modified["_apex_reasoning"] = optimized.reasoning[:200]
            # Issue 1 fix (2026-05-11) — plumb APEX_DIR_LOCK state so
            # strategy_worker can suppress its XRAY downstream flip when
            # APEX has explicitly locked direction. See
            # dev_notes/five_critical_fixes/i1_phase2_report.md.
            modified["_apex_locked"] = bool(getattr(optimized, "is_locked", False))
            modified["_apex_lock_reason"] = str(getattr(optimized, "lock_reason", "") or "")
            return modified

        # Convert percentage SL/TP to absolute prices
        if optimized.direction == "Buy":
            modified["stop_loss_price"] = round(
                current_price * (1 - optimized.sl_pct / 100), 8
            )
            modified["take_profit_price"] = round(
                current_price * (1 + optimized.tp_pct / 100), 8
            )
        else:  # Sell
            modified["stop_loss_price"] = round(
                current_price * (1 + optimized.sl_pct / 100), 8
            )
            modified["take_profit_price"] = round(
                current_price * (1 - optimized.tp_pct / 100), 8
            )

        # Store APEX metadata for downstream TIAS feedback loop
        modified["_apex_optimized"] = True
        modified["_apex_was_flipped"] = optimized.was_flipped
        modified["_apex_confidence"] = optimized.confidence
        modified["_apex_tp_mode"] = optimized.tp_mode
        modified["_apex_reasoning"] = optimized.reasoning[:200]
        modified["_apex_original_direction"] = optimized.original_direction or ""
        modified["_apex_original_sl"] = optimized.original_sl or 0.0
        modified["_apex_original_tp"] = optimized.original_tp or 0.0
        modified["_apex_original_size"] = optimized.original_size or 0.0
        modified["_apex_model"] = optimized.apex_model or ""
        modified["_apex_response_ms"] = optimized.apex_response_time_ms or 0
        modified["_apex_cost_usd"] = optimized.apex_cost_usd or 0.0
        # Issue 1 fix (2026-05-11) — plumb APEX_DIR_LOCK state so
        # strategy_worker can suppress its XRAY downstream flip when
        # APEX has explicitly locked direction. See
        # dev_notes/five_critical_fixes/i1_phase2_report.md.
        modified["_apex_locked"] = bool(getattr(optimized, "is_locked", False))
        modified["_apex_lock_reason"] = str(getattr(optimized, "lock_reason", "") or "")

        return modified

    # ─── Watchdog Claude Loop — REMOVED ───
    # Per-position brain reviews are now handled by PositionWatchdog._maybe_trigger_brain()
    # Strategic position actions are queued via TradeCoordinator and executed by watchdog

    # (REMOVED: _watchdog_claude_loop and _run_watchdog_claude_review — ~190 lines)
    # Those methods are no longer needed. PositionWatchdog owns all position reviews.


    # ─── Status ───

    def get_status(self) -> dict:
        """Get full status for Telegram dashboard."""
        plan = self._current_plan

        return {
            "layer_1": {
                "active": self._layer_active[1],
                "name": "DATA",
                "uptime_seconds": time.time() - self._layer_started_at[1]
                if self._layer_active[1]
                else 0,
            },
            "layer_2": {
                "active": self._layer_active[2],
                "name": "BRAIN",
                "uptime_seconds": time.time() - self._layer_started_at[2]
                if self._layer_active[2]
                else 0,
                "plan_age_seconds": plan.age_seconds,
                "plan_stale": plan.is_stale,
                "review_interval": self.brain_interval_seconds,
                "next_review_in": max(
                    0, self.brain_interval_seconds - plan.age_seconds
                ),
            },
            "layer_3": {
                "active": self._layer_active[3],
                "name": "EXECUTION",
                "uptime_seconds": time.time() - self._layer_started_at[3]
                if self._layer_active[3]
                else 0,
                "watchdog_interval": "brain_via_watchdog",
            },
            "plan": {
                "market_view": plan.market_view[:80]
                if plan.market_view
                else "No plan yet",
                "risk_level": plan.risk_level,
                "max_positions": plan.max_positions,
                "max_per_coin": plan.max_per_coin,
                "focus_coins": plan.focus_coins[:5],
                "avoid_coins": plan.avoid_coins[:5],
                "defaults": {
                    "sl_pct": plan.default_sl_pct,
                    "tp_pct": plan.default_tp_pct,
                    "hold_min": plan.default_hold_minutes,
                    "leverage": plan.default_leverage,
                    "trailing": plan.trailing_activation_pct,
                    "direction": plan.default_direction,
                },
                "coin_directives_count": len(plan.coin_directives),
                "position_actions_count": len(plan.position_actions),
            },
        }

    def get_plan(self) -> StrategicPlan:
        return self._current_plan

    def is_layer_active(self, layer: int) -> bool:
        return self._layer_active.get(layer, False)

    # ─── Phase 8 semantic helpers (forward-compat with 5-layer scheme) ───

    def can_run_brain(self) -> bool:
        """Layer 8 forward-compat: should the brain (Claude calls) fire now?

        On the v1 (3-layer) scheme this maps to layer_active[2]; on the
        v2 (5-layer) scheme it will map to layer_active[3]. Helper hides
        the numbering choice from callers so the migration is config-only
        for them.
        """
        # When schema_version becomes 2 in a future commit, switch to [3].
        return self._layer_active.get(2, False)

    def can_execute_orders(self) -> bool:
        """Layer 8 forward-compat: are order placements allowed?

        v1 → layer_active[3]; v2 → layer_active[4]. Use this helper from
        OrderService/TradeGate/APEX so the renumber is transparent.
        """
        return self._layer_active.get(3, False)

    def can_run_monitoring(self) -> bool:
        """Layer 8 forward-compat: can ProfitSniper/Watchdog intervene?

        v1 → layer_active[3] (monitoring tracks execution); v2 →
        layer_active[5] (monitoring as its own toggle).
        """
        return self._layer_active.get(3, False)

    def is_cycle_active(self) -> bool:
        """Layer 1 restructure Phase 4 — should Layer 1B/1C/1D fire now?

        Today (pre-Phase-8 renumbering) a cycle is active iff both BRAIN
        (toggle 2) and EXECUTION (toggle 3) are intended on. Layer 1A
        always runs regardless. Phase 8 will rewire to toggle 2 alone
        (= ANALYSIS in the new scheme).

        ScannerWorker, StructureWorker, SignalWorker, RegimeWorker, and
        StrategyWorker check this at tick start; when False they emit a
        ``LAYER1{B,C,D}_TICK_SKIP | reason=cycle_inactive`` debug line
        and return without doing work.
        """
        return self._layer_active.get(2, False) and self._layer_active.get(3, False)

    @staticmethod
    def _seconds_to_next_window_boundary(
        *, window_minutes: int = 5, now: float | None = None,
    ) -> float:
        """Phase 4 — seconds from ``now`` until the next 5-min boundary.

        Returns 0.0 exactly on the boundary; otherwise a strictly positive
        float. Used by the cold-start wait to align the first cycle after
        OFF→ON to a clean window boundary so all four sub-layers see
        fresh data simultaneously.
        """
        import time as _t
        now = now if now is not None else _t.time()
        window_s = float(window_minutes) * 60.0
        return float((window_s - (now % window_s)) % window_s)

    def get_strategy_consensus(self, symbol: str) -> dict | None:
        """Layer 1 restructure Phase 3 — per-coin consensus accessor.

        Returns the consensus dict ``{"consensus", "consensus_score",
        "vote_count", "direction", "last_updated"}`` for ``symbol``, or
        None if StrategyWorker has not yet processed this coin.

        Stale entries are preserved across cycles by StrategyWorker's
        merge logic, so a missing entry truly means "never processed",
        not "skipped this cycle". Callers can inspect ``last_updated``
        to decide if the entry is fresh enough to act on.
        """
        return getattr(self, "_strategy_consensus", {}).get(symbol)

    def get_strategy_votes(self, symbol: str) -> dict | None:
        """Phase 2 of the 1D briefing rewrite — per-coin vote distribution.

        Returns the full per-strategy vote map for ``symbol`` in the
        shape produced by ``StrategyWorker._build_per_coin_votes`` /
        ``EnsembleResult.vote_distribution_dict``:

            {
                "votes": {
                    "<strategy_name>": {
                        "vote":       "BUY" | "SELL" | "NEUTRAL",
                        "confidence": float,
                        "weight":     float,
                        "reasoning":  str (truncated),
                    },
                    ...
                },
                "buy_weighted":     float,
                "sell_weighted":    float,
                "neutral_weighted": float,
                "consensus":        str,   # mirrors get_strategy_consensus
                "consensus_direction": str,
                "size_multiplier":  float,
                "last_updated":     float,
            }

        Returns None when StrategyWorker has not yet produced votes for
        the symbol. Stale entries are preserved across cycles by the
        same merge logic that protects ``_strategy_consensus`` —
        ``last_updated`` lets callers judge freshness.

        Phase 2 of the briefing rewrite is *additive*: this accessor
        coexists with ``get_strategy_consensus``; later phases (state
        labeler, interestingness ranker, briefing builder) read here
        for full distribution while existing consumers keep reading
        the aggregate via ``get_strategy_consensus``.
        """
        return getattr(self, "_strategy_votes", {}).get(symbol)

    def get_coin_packages(self) -> dict:
        """Layer 1 restructure Phase 6 — selected-coin packages accessor.

        Returns the dict keyed by symbol with the latest CoinPackage
        produced by ScannerWorker. Empty when ScannerWorker hasn't run
        a cycle yet OR when no coin qualified in the latest cycle.
        """
        return getattr(self, "_coin_packages", {}) or {}

    def get_scorer_components(self, symbol: str) -> dict | None:
        """Stage 2 phase 2 — per-coin TradeScorer 4-component breakdown.

        Returns the entry shape ``{base, confluence, context, quality,
        total, grade, last_updated}`` written by StrategyWorker each
        scoring tick. Returns None when the symbol has not been scored
        yet (cold start) or was filtered before the scorer ran.

        The strategist reads this when rendering the rich Layer 1B/1C
        per-coin block under ``[stage2].enable_full_layer_block``.
        """
        return getattr(self, "_scorer_components", {}).get(symbol)

    def snapshot_layer_state(self) -> LayerSnapshot:
        """Capture a frozen snapshot of the current layer_active state.

        Phase 2 (Layer 3 enforcement). Brain cycle / strategy_worker call
        this at the START of a directive→execution chain, pass the
        result through to ``OrderService.place_order(layer_snapshot=...)``,
        and OrderService re-checks against the live LayerManager. If
        ``snapshot.is_layer_active(3) != self.is_layer_active(3)`` AND
        purpose is ``"layer3_entry"``, the placement is aborted with
        ``Layer3RaceError``.

        The snapshot is read-only (``MappingProxyType``) so callers can
        pass it through arbitrarily-deep call stacks without worrying
        about mutation.
        """
        return LayerSnapshot(
            layer_active=MappingProxyType(dict(self._layer_active)),
            captured_at_monotonic=time.monotonic(),
        )
