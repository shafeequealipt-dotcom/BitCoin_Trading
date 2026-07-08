"""Daily universe-refresh orchestration (Phase 2).

Wires the proven Phase 1 selection engine into the live system, safely. A
refresh:

  1. Pauses ONLY Call-A (find-new-trades) via a shared state flag. Call-B
     (manage open positions) and the position watchdog keep running, so open
     positions are never neglected.
  2. Runs the two-pass selection (open-position coins and any stable core
     force-kept).
  3. Applies the new universe in memory (reassigns ``settings.universe`` so
     the per-tick readers KlineWorker and ScannerWorker pick it up, and calls
     ``MarketScanner.set_watch_list`` for the scanner's own frozen copy) and
     persists it to ``data/universe_state.json`` for restart durability.
  4. Runs a DATA-GATED warm-up: waits until the newly-added coins' analysis
     passes the existing freshness gates (confirmed regime plus a minimum
     kline count), resuming early when ready, with a hard maximum.
  5. Resumes Call-A.

Safety invariants (implement-doc Rules 2, 3, 9):
  - Only Call-A is paused; everything else keeps running. Call-A is ALWAYS
    resumed, even if the refresh errors (try/finally).
  - If open positions cannot be confirmed, the refresh aborts WITHOUT
    swapping the universe (it never risks dropping a live position's coin).
  - It changes only the contents of the 50; it adds no per-trade gate and
    touches no exit system, the brain logic, or any protected table.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from src.config.settings import UniverseSettings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import TimeFrame
from src.core.utils import now_utc
from src.database.repositories.market_repo import MarketRepository
from src.strategies.universe_selector import select_universe

log = get_logger(__name__)

_STATE_FILE = Path("data/universe_state.json")


def rebuild_universe_settings(old: UniverseSettings, new_watch_list) -> UniverseSettings:
    """Build a UniverseSettings for a swapped watch_list, safely.

    Filters ``coin_aliases`` to only the symbols present in the new
    watch_list — UniverseSettings.__post_init__ rejects aliases that
    reference symbols outside the list ("orphan alias"), so a dynamic swap
    must drop aliases for coins that left. Aliases are only used for
    news/sentiment tagging, so dropping them for untraded coins is correct;
    newly-added coins still get their tickers auto-derived in __post_init__.
    """
    new_set = set(new_watch_list)
    aliases = {k: v for k, v in old.coin_aliases.items() if k in new_set}
    try:
        return UniverseSettings(
            watch_list=list(new_watch_list),
            coin_aliases=aliases,
            refresh=old.refresh,
        )
    except Exception as e:
        # A surviving alias could still collide with a newly-added coin's
        # auto-derived ticker (UniverseSettings rejects that). Aliases are only
        # for news/sentiment tagging, so drop them rather than fail the swap.
        log.warning(f"UNIVERSE_ALIAS_DROP | reason=rebuild_conflict err='{str(e)[:100]}' | {ctx()}")
        return UniverseSettings(
            watch_list=list(new_watch_list),
            coin_aliases={},
            refresh=old.refresh,
        )


class UniverseRefreshState:
    """Shared, in-memory state for the refresh: the Call-A pause flag and a
    single-flight guard. Lives in the services dict so the LayerManager reads
    it (to skip Call-A) and the orchestrator, scheduler, and Telegram button
    set it.
    """

    def __init__(self) -> None:
        self._paused: bool = False
        self._reason: str = ""
        self._paused_at: float = 0.0
        self._running: bool = False
        self.last_result: dict[str, Any] | None = None

    # --- Call-A pause (read by LayerManager._run_brain_cycle) ---
    def is_call_a_paused(self) -> bool:
        return self._paused

    def reason(self) -> str:
        return self._reason

    def pause_call_a(self, reason: str) -> None:
        self._paused = True
        self._reason = reason
        self._paused_at = time.time()
        log.warning(f"CALL_A_PAUSED_FOR_REFRESH | rsn='{reason}' | {ctx()}")

    def resume_call_a(self) -> None:
        if not self._paused:
            return
        elapsed = time.time() - self._paused_at if self._paused_at else 0.0
        prev = self._reason
        self._paused = False
        self._reason = ""
        self._paused_at = 0.0
        log.warning(f"CALL_A_RESUMED | prev_rsn='{prev}' paused_for={elapsed:.0f}s | {ctx()}")

    # --- single-flight guard (overlap protection for scheduled + manual) ---
    def is_running(self) -> bool:
        return self._running

    def begin(self) -> bool:
        if self._running:
            return False
        self._running = True
        return True

    def end(self) -> None:
        self._running = False


class UniverseRefreshOrchestrator:
    """Runs one refresh end to end. Constructed once and held in services;
    invoked by the scheduled worker (Phase 3) and the Telegram button
    (Phase 4). Stateless per call apart from the shared UniverseRefreshState.
    """

    def __init__(self, settings, db, services: dict) -> None:
        self.settings = settings
        self.db = db
        self.services = services
        self._market_repo = MarketRepository(
            db, getattr(settings.database, "kline_save_chunk_size", 500)
        )

    def _state(self) -> UniverseRefreshState:
        st = self.services.get("universe_refresh_state")
        if st is None:
            st = UniverseRefreshState()
            self.services["universe_refresh_state"] = st
        return st

    async def run_refresh(self, trigger: str, notify=None) -> dict[str, Any]:
        """Execute one refresh. ``trigger`` is e.g. 'scheduled_23', 'manual'.

        ``notify`` is an optional ``async (stage: str, message: str) -> None``
        used by the manual Telegram button to post staged, plain-prose status
        updates. When it is None (scheduled runs), a single completion alert
        is posted instead. Returns a plain-dict summary. Call-A is guaranteed
        resumed on exit.
        """
        p = self.settings.universe.refresh
        state = self._state()

        if not state.begin():
            log.warning(f"UNIVERSE_REFRESH_OVERLAP | trigger={trigger} | a refresh is already running | {ctx()}")
            return {"status": "already_running", "trigger": trigger}

        t0 = time.time()
        log.info(f"UNIVERSE_REFRESH_START | trigger={trigger} | {ctx()}")
        try:
            state.pause_call_a(f"refresh:{trigger}")
            await self._emit(notify, "started",
                             "Universe refresh started. Finding new trades is paused; "
                             "every open position keeps full management.")
            try:
                return await self._do_refresh(trigger, p, t0, notify)
            finally:
                # Non-negotiable: never leave Call-A paused, even on error.
                state.resume_call_a()
        finally:
            state.end()

    @staticmethod
    async def _emit(notify, stage: str, message: str) -> None:
        """Best-effort staged status callback (manual button). Never raises."""
        if notify is None:
            return
        try:
            await notify(stage, message)
        except Exception as e:  # pragma: no cover - defensive
            log.debug(f"universe refresh notify failed stage={stage} err={str(e)[:80]}")

    async def _do_refresh(self, trigger: str, p, t0: float, notify=None) -> dict[str, Any]:
        market = self.services.get("market")
        bybit = self.services.get("bybit")
        if market is None:
            log.error(f"UNIVERSE_REFRESH_ABORT | reason=no_market_service trigger={trigger} | {ctx()}")
            await self._emit(notify, "aborted", "Universe refresh aborted: market data unavailable. Nothing changed.")
            return {"status": "aborted", "reason": "no_market_service", "trigger": trigger}

        # --- Confirm open positions FIRST. If we cannot, abort without
        #     swapping — we never risk dropping a live position's coin. ---
        pos_syms = await self._open_position_symbols()
        if pos_syms is None:
            log.error(
                f"UNIVERSE_REFRESH_ABORT | reason=positions_unconfirmed trigger={trigger} "
                f"| refusing to swap the universe without knowing open positions | {ctx()}"
            )
            await self._emit(notify, "aborted",
                             "Universe refresh aborted: could not confirm open positions, "
                             "so the universe was left unchanged for safety.")
            return {"status": "aborted", "reason": "positions_unconfirmed", "trigger": trigger}

        current = list(self.settings.universe.watch_list)
        core: set[str] = set()
        if p.stable_core_size > 0:
            core = set(current[: p.stable_core_size])
        force_keep = set(pos_syms) | core

        # --- Selection (read-only against the exchange + DB writes for
        #     fetched candles, which also warms new coins' history). ---
        async def fetch_daily(sym: str):
            return await market.get_klines(sym, TimeFrame.D1, p.volatility_lookback_days + 3)

        async def fetch_oi(sym: str):
            if bybit is None:
                return []
            res = await bybit.call(
                "get_open_interest", category="linear", symbol=sym,
                intervalTime="1d", limit=p.volatility_lookback_days + 2,
            )
            vals = [float(it.get("openInterest", "0")) for it in res.get("list", [])]
            vals.reverse()  # newest-first -> chronological
            return vals

        tickers = await market.get_all_linear_tickers()
        result = await select_universe(
            tickers, p,
            fetch_daily=fetch_daily,
            fetch_oi=fetch_oi if (p.oi_enabled and bybit is not None) else None,
            force_keep=force_keep,
            current=current,
        )
        new_list = result.selected

        if len(new_list) < 10:
            # UniverseSettings validation needs >= 10; also a refresh this
            # thin is not trustworthy. Abort the swap, keep the old universe.
            log.error(
                f"UNIVERSE_REFRESH_ABORT | reason=too_few_selected n={len(new_list)} "
                f"trigger={trigger} | keeping existing universe | {ctx()}"
            )
            await self._emit(notify, "aborted",
                             f"Universe refresh aborted: only {len(new_list)} coins qualified, "
                             f"too few to trust. The existing universe was kept.")
            return {"status": "aborted", "reason": "too_few_selected",
                    "selected_count": len(new_list), "trigger": trigger}

        # --- Apply: in-memory swap + scanner setter + persistence ---
        old_univ = self.settings.universe
        self.settings.universe = rebuild_universe_settings(old_univ, new_list)
        scanner = self.services.get("scanner")
        if scanner is not None and hasattr(scanner, "set_watch_list"):
            scanner.set_watch_list(set(new_list))
        self._persist(new_list, trigger, result)

        log.info(
            "UNIVERSE_REFRESH_APPLIED | trigger={t} selected={s} added={a} removed={r} "
            "softened={soft} forced_kept={fk} | {c}",
            t=trigger, s=len(new_list), a=len(result.added), r=len(result.removed),
            soft=result.softened, fk=len(force_keep), c=ctx(),
        )
        _soft_note = " The strict floor was softened this run, so this universe is weaker than usual." if result.softened else ""
        await self._emit(
            notify, "selected",
            f"Selected {len(new_list)} coins. Added {len(result.added)}, removed "
            f"{len(result.removed)}.{_soft_note}\nNew coins: "
            f"{', '.join(result.added) if result.added else 'none'}.",
        )

        # --- Data-gated warm-up for the newly-added coins (excluding open
        #     positions, which are already live and managed). ---
        added = [s for s in result.added if s not in pos_syms]
        if added:
            await self._emit(
                notify, "warmup",
                f"Warming up {len(added)} new coins so their analysis is ready before "
                f"the brain trades them. Trading resumes when their data is ready, "
                f"within about {p.warmup_max_minutes} minutes.",
            )
        warm = await self._warmup(added, p)

        summary = {
            "status": "ok",
            "trigger": trigger,
            "selected": list(new_list),
            "selected_count": len(new_list),
            "added": list(result.added),
            "removed": list(result.removed),
            "softened": result.softened,
            "warmup_seconds": warm["seconds"],
            "warmup_ready": warm["ready"],
            "warmup_pending": warm["pending"],
            "elapsed_seconds": round(time.time() - t0, 1),
        }
        self._state().last_result = summary
        log.info(
            "UNIVERSE_REFRESH_END | trigger={t} selected={s} added={a} removed={r} "
            "warmup_s={w} pending={p} total_s={tot} | {c}",
            t=trigger, s=len(new_list), a=len(result.added), r=len(result.removed),
            w=warm["seconds"], p=len(warm["pending"]), tot=summary["elapsed_seconds"], c=ctx(),
        )
        if notify is not None:
            _pend = ""
            if warm["pending"]:
                _pend = (f" {len(warm['pending'])} coins were not ready in time and will "
                         f"brief once their data lands.")
            await self._emit(
                notify, "done",
                f"Universe refresh complete. Now trading {len(new_list)} coins. "
                f"Warm-up took {warm['seconds']} seconds.{_pend} Trading is resuming.",
            )
        else:
            # Scheduled runs: a single completion alert for visibility.
            self._notify_done(summary)
        return summary

    async def _open_position_symbols(self) -> set[str] | None:
        """Symbols with open positions, or None if they cannot be confirmed.

        Prefers the confirmed-result API (so a transport failure is NOT read
        as 'no positions'); falls back to get_positions. None means abort.
        """
        svc = self.services.get("position") or self.services.get("position_service")
        if svc is None:
            return set()
        try:
            gpwc = getattr(svc, "get_positions_with_confirmation", None)
            if gpwc is not None:
                res = await gpwc()
                if not getattr(res, "confirmed", True):
                    return None
                return {p.symbol for p in res.positions}
            positions = await svc.get_positions()
            return {p.symbol for p in positions}
        except Exception as e:
            log.error(f"UNIVERSE_REFRESH_POS_FETCH_FAIL | err='{str(e)[:120]}' | {ctx()}")
            return None

    async def _warmup(self, added: list[str], p) -> dict[str, Any]:
        """Wait until the added coins pass the freshness gates, or the max."""
        start = time.monotonic()
        deadline = start + p.warmup_max_minutes * 60.0
        pending = set(added)
        ready: set[str] = set()
        if not pending:
            log.info(f"UNIVERSE_WARMUP | no new coins to warm up | {ctx()}")
            return {"seconds": 0, "ready": [], "pending": []}

        # Cycle-awareness: the freshness signals this warm-up gates on (per-coin
        # regime, etc.) are produced only by the cycle-gated Layer-1B/1C/1D
        # analysis workers, which run only when the trading cycle is active
        # (brain + execution toggles on — LayerManager.is_cycle_active). When
        # the cycle is inactive there is also no Call-A to protect, so waiting
        # is pointless and could never complete (the gating data is never
        # produced). Skip the wait; the new coins warm up once trading resumes
        # and are protected then by the brain's existing per-cycle freshness
        # gates (package validator, regime, kline-age) at decision time.
        if not self._cycle_active():
            log.warning(
                "UNIVERSE_WARMUP_SKIPPED | reason=trading_cycle_inactive new_coins={n} "
                "| analysis is gated off while trading is paused; new coins warm up and "
                "are gate-protected when trading resumes | {c}",
                n=len(pending), c=ctx(),
            )
            return {"seconds": 0, "ready": [], "pending": []}

        log.info(
            "UNIVERSE_WARMUP_START | new_coins={n} max_minutes={m} | {c}",
            n=len(pending), m=p.warmup_max_minutes, c=ctx(),
        )
        while pending and time.monotonic() < deadline:
            if not self._cycle_active():
                log.warning(
                    "UNIVERSE_WARMUP_ABORTED | reason=trading_cycle_went_inactive "
                    "ready={r} pending={pp} after={e}s | {c}",
                    r=len(ready), pp=len(pending), e=int(time.monotonic() - start), c=ctx(),
                )
                break
            for sym in list(pending):
                if await self._coin_ready(sym, p):
                    ready.add(sym)
                    pending.discard(sym)
            if not pending:
                break
            log.info(
                "UNIVERSE_WARMUP | ready={r} pending={p} elapsed={e}s | {c}",
                r=len(ready), p=len(pending), e=int(time.monotonic() - start), c=ctx(),
            )
            await asyncio.sleep(p.warmup_poll_seconds)

        secs = int(time.monotonic() - start)
        if pending:
            log.warning(
                "UNIVERSE_WARMUP_TIMEOUT | ready={r} still_pending={p} after={s}s "
                "| resuming brain anyway; pending coins will brief once their data lands | {c}",
                r=len(ready), p=sorted(pending), s=secs, c=ctx(),
            )
        else:
            log.info("UNIVERSE_WARMUP_DONE | all {n} coins ready after={s}s | {c}",
                     n=len(ready), s=secs, c=ctx())
        return {"seconds": secs, "ready": sorted(ready), "pending": sorted(pending)}

    def _cycle_active(self) -> bool:
        """True when the trading cycle (brain + execution) is active.

        The warm-up's freshness signals come from cycle-gated analysis workers
        that only run when the cycle is active, so the warm-up only data-gates
        then. Fail-open (return True) if the layer manager is unavailable, so a
        wiring gap never silently disables the warm-up while trading IS on.
        """
        lm = self.services.get("layer_manager")
        if lm is None or not hasattr(lm, "is_cycle_active"):
            return True
        try:
            return bool(lm.is_cycle_active())
        except Exception:
            return True

    async def _coin_ready(self, sym: str, p) -> bool:
        """A newly-added coin is warm when its analysis passes the existing
        freshness gates: a confirmed (non-UNKNOWN) regime and at least the
        minimum kline count. Regime confirmation requires real, recent
        history, so it is the binding gate; the kline count is the candle
        floor the strategy worker itself uses.
        """
        # Regime gate (the binding one). If the detector is absent we cannot
        # check it, so we do not block on it.
        regime_ok = True
        det = self.services.get("regime_detector")
        if det is not None and hasattr(det, "get_coin_regime"):
            try:
                r = det.get_coin_regime(sym)
                if r is None:
                    regime_ok = False
                else:
                    reg = getattr(r, "regime", None)
                    val = getattr(reg, "value", reg)
                    regime_ok = val is not None and str(val).lower() != "unknown"
            except Exception:
                regime_ok = False
        if not regime_ok:
            return False

        # Minimum kline count (reuse the strategy worker's floor).
        try:
            se = getattr(self.settings, "strategy_engine", None)
            min_count = int(getattr(se, "min_kline_count", 50))
            kl = await self._market_repo.get_klines(sym, TimeFrame.M5.value, min_count)
            return len(kl) >= min_count
        except Exception:
            return False

    def _persist(self, watch_list: list[str], trigger: str, result) -> None:
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _STATE_FILE.with_suffix(".json.tmp")
            payload = {
                "watch_list": list(watch_list),
                "timestamp": now_utc().isoformat(),
                "trigger": trigger,
                "added": list(result.added),
                "removed": list(result.removed),
                "softened": bool(result.softened),
            }
            tmp.write_text(json.dumps(payload, indent=2))
            tmp.replace(_STATE_FILE)  # atomic on POSIX
            log.info(f"UNIVERSE_STATE_PERSIST_OK | n={len(watch_list)} file={_STATE_FILE} | {ctx()}")
        except Exception as e:
            log.warning(f"UNIVERSE_STATE_PERSIST_FAIL | err='{str(e)[:120]}' | {ctx()}")

    def _notify_done(self, summary: dict[str, Any]) -> None:
        """Best-effort plain-prose status to Telegram (screen-reader safe).

        Phase 4 wires the manual button and richer staged messages; here we
        post a concise completion line so a scheduled refresh is visible.
        """
        alert = self.services.get("alert_manager")
        if alert is None or not hasattr(alert, "send_custom"):
            return
        added = summary.get("added", [])
        removed = summary.get("removed", [])
        soft = " The strict floor was softened this run, so the universe is weaker than usual." if summary.get("softened") else ""
        msg = (
            f"Universe refresh complete ({summary.get('trigger')}). "
            f"Now trading {summary.get('selected_count')} coins. "
            f"Added {len(added)}, removed {len(removed)}. "
            f"Warm-up took {summary.get('warmup_seconds')} seconds.{soft} "
            f"Trading resumed."
        )
        try:
            self._notify_task = asyncio.create_task(alert.send_custom(msg))
        except Exception:
            pass
