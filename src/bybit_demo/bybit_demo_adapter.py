"""Bybit demo service adapters — drop-in replacements for the live services.

Each adapter class mirrors the EXACT interface of its live counterpart:

  BybitDemoOrderService    → mirrors OrderService
  BybitDemoPositionService → mirrors PositionService
  BybitDemoAccountService  → mirrors AccountService

They translate between Bybit's V5 JSON API and the main project's typed
dataclasses (``Order``, ``Position``, ``AccountInfo``) with proper enum
conversion (``Side``, ``OrderType``, ``OrderStatus``).

Critical contract rule mirrored from Shadow: **adapters never raise**.
On error they return REJECTED ``Order``s, empty position lists, or zero
``AccountInfo`` sentinels. Layer 4 / brain consumers check
``.status == REJECTED``; raising an exception would break that path.

See ``dev_notes/bybit_demo_adapter/phase1_synthesis.md`` Section 2 for
the full contract Shadow defines and this adapter must mirror.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from src.bybit_demo.bybit_demo_client import BybitDemoClient
from src.core.exceptions import TradingMCPError
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import (
    AccountInfo,
    BalanceQueryResult,
    Order,
    OrderStatus,
    OrderType,
    Position,
    PositionsQueryResult,
    Side,
)

# Bybit V5 Linear category — USDT perpetuals. The project trades only
# USDT-margined perps so this is a constant for every order.
_CATEGORY = "linear"

# One-Way mode — single position per symbol. Hedge mode (positionIdx
# 1/2) is intentionally out of scope; the project's strategies and
# Layer 4 logic assume one position per symbol matching Shadow.
_POSITION_IDX = 0

# P3 of P1-P10: bounded retry on get_last_close. Bybit's closed-pnl
# endpoint is asynchronously indexed (audit measured 35% miss rate
# from single-shot polls). 10 attempts × 1s = 10s total ceiling — same
# as the watchdog's tick cadence so a stalled retry never blocks
# beyond one cycle. Empirically the indexer almost always populates
# within 2-3 seconds.
_LAST_CLOSE_RETRY_ATTEMPTS = 10
_LAST_CLOSE_RETRY_INTERVAL_S = 1.0
# Phantom-loss fix (2026-06-05) Commit 2: freshness floor margin (ms) for the
# identity-matched closed-pnl reconcile. A matched row must be no older than
# (WS close time − this margin), so a stale PREVIOUS-trade row of the same
# symbol cannot satisfy the match. See PHANTOM_LOSS_FIX_DESIGN_2026-06-04.md.
_CLOSE_MATCH_SKEW_MS = 5000.0

# J1 Phase 3 Step E (2026-05-14) — hard cap on /v5/position/list
# pagination. Bybit V5 documents default limit=20 per page and supports
# up to 200; today's universes are well under 20 simultaneously-open
# positions, but the adapter previously read only page 1 and would
# silently drop tail rows if the operator's strategy ever scaled past
# the default. Five pages × the default = up to 100 positions, well
# above any realistic strategy ceiling. If the cap is ever hit (the
# 6th page still carries a non-empty cursor), the adapter returns
# confirmed=False rather than a silently-truncated truth — preserving
# the watchdog's "preserve last-known state" semantics in the I1/F-26
# pattern. Emits BYBIT_DEMO_POSITIONS_PAGINATION_CAP at WARNING so
# operators see why the cache went un-updated.
_MAX_POSITIONS_PAGES = 5

if TYPE_CHECKING:
    # Forward-reference only — Bybit demo, like Shadow, has no Layer 3
    # gate so it does not USE LayerSnapshot at runtime, but its
    # place_order signature accepts one for parity with the live
    # OrderService (Phase 2 of the Layer 1 restructure added the
    # kw-only arg). Drift between the two signatures is a TypeError on
    # every brain-driven paper trade.
    from src.core.layer_manager import LayerSnapshot


# =============================================================================
# BybitDemoPositionService — Phase 2.D
# =============================================================================


class BybitDemoPositionService:
    """Bybit demo adapter for PositionService.

    Mirrors every public method of the real PositionService. Calls
    Bybit V5 endpoints via :class:`BybitDemoClient` and returns
    Position / Order dataclass instances with proper Side enum values.
    """

    def __init__(
        self,
        client: BybitDemoClient,
        *,
        trading_repo: Any = None,
        instrument_service: Any = None,
    ) -> None:
        """Construct with optional persistence repository.

        P7 of P1-P10: trading_repo is the project's TradingRepository.
        When provided, close_position writes through to trading.db's
        orders, trade_history, and positions tables — same persistence
        contract the live PositionService follows. When None
        (legacy callers / tests), persistence is silently skipped and
        a single CLOSE_NO_PERSIST INFO log fires once per call so the
        gap is observable.

        T1-4 (2026-05-12): ``instrument_service`` is the project's
        :class:`InstrumentService` singleton (lives at
        ``src/trading/services/instrument_service.py``, used elsewhere
        by the live ``OrderService``). When provided, ``reduce_position``
        floor-quantizes ``qty`` to the symbol's ``lotSizeFilter.qtyStep``
        before POSTing to Bybit V5, eliminating the dominant cause of
        ``REDUCE_FALLBACK | reason=bybit_reject ret_code=10001
        'Qty invalid'`` events that silently downgrade 50 % scale-outs
        into full closes (verified live on OPUSDT, AEROUSDT, GMTUSDT
        2026-05-11/12). When ``None`` (Bybit live wiring failed at boot
        OR test fixtures), the partial path falls back to a logged
        full-close so unquantized qty is never sent to the exchange —
        see ``BYBIT_DEMO_QTY_QUANTIZE_UNAVAILABLE``.
        """
        self._client = client
        self._log = get_logger("bybit_demo")
        self._trading_repo = trading_repo
        self._instrument_service = instrument_service
        # Issue 4 fix (2026-05-11) — late-bound TradeCoordinator
        # reference. Set by WorkerManager via attach_coordinator() AFTER
        # both objects are constructed (coordinator is built at line ~532
        # of workers/manager.py, well after this adapter is wired). Used
        # by reduce_position to mark a partial-close intent BEFORE the
        # order goes out so the WS subscriber can route the resulting
        # execution event through coordinator.on_partial_close rather
        # than the full on_trade_closed path.
        self._coordinator = None  # late-bound; see attach_coordinator
        # HIGH-3 fix (2026-05-09): per-symbol close_trigger cache. Populated
        # by close_position when a system-initiated close is dispatched;
        # read by get_last_close to populate its return dict's
        # close_trigger field. TTL-bounded (60s) so a re-opened symbol
        # cannot inherit a stale trigger from an earlier close. When no
        # cache entry exists (genuinely exchange-initiated closes:
        # SL/TP hit on Bybit's side, manual UI close), get_last_close
        # falls back to the legacy "exchange_match" value — that label
        # remains correct for those cases.
        self._recent_close_triggers: dict[str, tuple[str, float]] = {}
        # J1 Phase 3 Step A (2026-05-14) — symmetric cache-prune dwell
        # tracking. Counts consecutive confirmed-true responses whose
        # parsed open set is empty. The prune of cached rows only runs
        # when the response is non-empty OR after two consecutive
        # confirmed-empty ticks, so a single transient ``confirmed=True,
        # positions=()`` (the documented Issue I1 follow-up case for
        # non-10002 adapter errors) cannot cascade-delete the cache.
        # Reset to 0 on any non-empty confirmed response.
        self._consecutive_empty_confirmed_ticks: int = 0

    def _record_close_trigger(self, symbol: str, trigger: str) -> None:
        """Stash the trigger for a 60-second window so get_last_close
        can return it. HIGH-3 fix."""
        import time as _t
        self._recent_close_triggers[symbol] = (trigger, _t.time() + 60.0)

    def _get_cached_close_trigger(self, symbol: str) -> str | None:
        """Return the cached close_trigger for symbol if present and not
        expired. None means no cache entry (use the default fallback).
        Side effect: prunes expired entries to bound dict size."""
        import time as _t
        entry = self._recent_close_triggers.get(symbol)
        if entry is None:
            return None
        trigger, expiry = entry
        if _t.time() > expiry:
            self._recent_close_triggers.pop(symbol, None)
            return None
        return trigger

    async def get_positions(
        self, symbol: str | None = None
    ) -> list[Position]:
        """Get all open positions via /v5/position/list.

        Mirrors :meth:`PositionService.get_positions`. Bybit returns
        zero-size positions for symbols the account ever traded but
        currently has no open position; we filter those out so the
        return matches Shadow's "currently open" semantics exactly.

        Returns empty list on exhausted retries or API error. This is
        the legacy contract that swallows error state into the empty
        list — see :meth:`get_positions_with_confirmation` for the
        ground-truth-aware variant that the watchdog uses to avoid
        phantom closes when the API call fails (Issue I1 / F-26).
        """
        result = await self.get_positions_with_confirmation(symbol=symbol)
        # Legacy callers see empty list either way. Callers that need
        # to distinguish "confirmed empty" from "unknown" use the new
        # method directly.
        return list(result.positions)

    async def get_positions_with_confirmation(
        self, symbol: str | None = None
    ) -> PositionsQueryResult:
        """Get open positions with an explicit "confirmed" flag.

        Issue I1 (F-26 TIMESTAMP_FAIL, 2026-05-14): distinguishes
        exchange-confirmed state from API-error state so the watchdog
        can avoid the phantom-close cascade triggered when an empty
        list is misinterpreted as "all positions closed on exchange."

        Returns :class:`PositionsQueryResult` with:

          * ``confirmed=True``  → ``positions`` reflects exchange truth
            (may be empty if no positions are open)
          * ``confirmed=False`` → adapter could not confirm; caller
            should preserve last-known state. Emits
            ``BYBIT_DEMO_POSITIONS_UNKNOWN_STATE`` at WARNING level.

        Currently the only error code that triggers ``confirmed=False``
        is 10002 (TIMESTAMP_FAIL) after all retries are exhausted by
        the client. Other errors (rate-limit, auth, network) continue
        to return ``confirmed=True, positions=()`` so existing callers
        that don't distinguish remain byte-for-byte identical. As the
        operator approves, additional error codes can join the
        unknown-state set in follow-up commits.
        """
        params: dict[str, Any] = {"category": _CATEGORY}
        if symbol is not None:
            params["symbol"] = symbol
        else:
            # Bybit V5 requires either symbol OR settleCoin — UNIFIED
            # account uses USDT for linear perps.
            params["settleCoin"] = "USDT"

        # J1 Phase 3 Step E (2026-05-14) — paginated fetch.
        #
        # Bybit V5 /v5/position/list returns at most ``limit`` positions
        # per page (default 20, max 200) and a ``nextPageCursor`` string
        # the caller passes back to fetch the next page. The pre-J1
        # adapter ignored both the limit parameter and the cursor, so a
        # strategy that ever held more than 20 open positions silently
        # had its tail invisible to the watchdog and reconciler.
        #
        # The loop accumulates rows across up to _MAX_POSITIONS_PAGES
        # pages. Mid-pagination errors return confirmed=False (we have
        # partial truth and must NOT prune the cache based on it) —
        # same safety posture as the 10002 TIMESTAMP_FAIL branch.
        rows: list[dict[str, Any]] = []
        cursor = ""
        for _page in range(_MAX_POSITIONS_PAGES):
            _page_params = dict(params)
            if cursor:
                _page_params["cursor"] = cursor
            try:
                envelope = await self._client.get(
                    "/v5/position/list", _page_params,
                    op=("positions" if _page == 0 else "positions_pg"),
                )
            except TradingMCPError as e:
                # Issue I1 (F-26): distinguish TIMESTAMP_FAIL (state unknown)
                # from other adapter errors. The client raises BybitAPIError
                # with details={"ret_code": 10002} when the retry loop
                # exhausts on the timestamp-fail path; the bare TradingMCPError
                # base class catches it here so we introspect details to
                # decide whether to flag unknown-state.
                _details = getattr(e, "details", None)
                _ret_code = (
                    _details.get("ret_code") if isinstance(_details, dict) else None
                )
                if _ret_code == 10002:
                    self._log.warning(
                        f"BYBIT_DEMO_POSITIONS_UNKNOWN_STATE | "
                        f"reason=timestamp_fail err='{str(e)[:120]}' | {ctx()}"
                    )
                    return PositionsQueryResult(
                        confirmed=False, reason="timestamp_fail",
                    )
                # Mid-pagination failure: we have partial truth. Returning
                # confirmed=True with the partial list would phantom-prune
                # the cache; return confirmed=False so the caller
                # preserves last-known state.
                if _page > 0:
                    self._log.warning(
                        f"BYBIT_DEMO_POSITIONS_UNKNOWN_STATE | "
                        f"reason=mid_pagination_error page={_page + 1} "
                        f"err='{str(e)[:120]}' | {ctx()}"
                    )
                    return PositionsQueryResult(
                        confirmed=False, reason="mid_pagination_error",
                    )
                # First-page non-10002 error — preserve the legacy
                # "confirmed empty" contract so existing dashboards/MCP
                # tools behave unchanged.
                return PositionsQueryResult(confirmed=True)

            _result = envelope.get("result") or {}
            rows.extend(_result.get("list") or [])
            cursor = str(_result.get("nextPageCursor") or "")
            if not cursor:
                break
        else:
            # Loop exhausted _MAX_POSITIONS_PAGES with cursor still
            # non-empty — more positions exist than we fetched. Silently
            # truncating would re-introduce H3; emit a loud warning and
            # return confirmed=False.
            self._log.warning(
                f"BYBIT_DEMO_POSITIONS_PAGINATION_CAP | "
                f"pages={_MAX_POSITIONS_PAGES} rows_collected={len(rows)} "
                f"cursor_still_present=true | {ctx()}"
            )
            return PositionsQueryResult(
                confirmed=False, reason="pagination_cap",
            )
        positions: list[Position] = []
        for row in rows:
            # Filter out zero-size entries (Bybit returns them for any
            # symbol with execution history). size > 0 means open.
            if _safe_float(row.get("size")) <= 0:
                continue
            pos = _build_position_from_v5(row)
            # I4 of cascade-fix series (2026-05-10): mirror live
            # PositionService.get_positions:76 — persist every
            # non-zero position to the ``positions`` cache table so DB
            # consumers (Telegram /positions, MCP get_positions tool,
            # post-mortem queries) see the same open set the watchdog
            # holds in memory. Pre-fix, this method returned positions
            # but never persisted; Phase 0 baseline confirmed
            # ``SELECT COUNT(*) FROM positions`` was 0 throughout
            # active bybit_demo trading.
            #
            # Schema v32 added the ``exchange_mode`` column; the
            # bybit_demo tag is hardcoded here because this adapter
            # is bybit_demo by definition (the live PositionService
            # passes 'shadow' from its own get_positions site).
            #
            # save_position is idempotent (INSERT OR REPLACE on PK
            # symbol). Failures are logged BUT NOT raised — the
            # caller must continue to receive the parsed positions
            # so the watchdog and other in-memory consumers keep
            # working even when DB writes fail. This matches the
            # error-handling contract used by close_position's
            # save_position call site below.
            if self._trading_repo is not None:
                try:
                    await self._trading_repo.save_position(
                        pos, exchange_mode="bybit_demo",
                    )
                except Exception as e:
                    self._log.warning(
                        f"BYBIT_DEMO_PERSIST_POSITION_FAIL | "
                        f"sym={pos.symbol} op=get_positions "
                        f"err='{str(e)[:120]}' | {ctx()}"
                    )
            positions.append(pos)

        # J1 Phase 3 Step A (2026-05-14) — symmetric cache prune.
        #
        # Pre-fix the adapter wrote (INSERT OR REPLACE) every confirmed
        # position but relied on the watchdog's vanished-detection plus
        # the close-callback chain to delete rows for symbols that
        # dropped out of the Bybit response. That chain only fires for
        # symbols the watchdog tracked at least once
        # (``_last_known_symbols`` is empty on first boot tick), so any
        # pre-fix or zombie-reconciler residue sat forever — the four
        # 2026-05-13 stale rows observed in the J1 investigation are
        # exactly this case.
        #
        # The prune is scoped to ``exchange_mode='bybit_demo'`` so the
        # live PositionService (which tags rows ``'shadow'``) and Shadow
        # (which writes no rows) are unaffected.
        #
        # Skip the prune when:
        #   * ``symbol`` was passed (single-symbol view; we don't see
        #     the full live set so we can't safely diff).
        #   * Repo not injected (legacy / test fixtures).
        #   * Response is empty and the dwell-time guard has not yet
        #     elapsed (one transient ``confirmed=True, positions=()``
        #     does not authorize a full cache wipe; require two
        #     consecutive empties).
        #
        # Failures are logged but NOT raised — the caller must continue
        # to receive the parsed positions even when prune writes fail.
        # Mirrors the per-row save_position error-handling contract.
        if (
            symbol is None
            and self._trading_repo is not None
            and hasattr(self._trading_repo, "prune_positions_not_in_set")
        ):
            live_syms = {p.symbol for p in positions}
            if not live_syms:
                self._consecutive_empty_confirmed_ticks += 1
            else:
                self._consecutive_empty_confirmed_ticks = 0
            _should_prune = (
                bool(live_syms)
                or self._consecutive_empty_confirmed_ticks >= 2
            )
            if _should_prune:
                try:
                    pruned = await self._trading_repo.prune_positions_not_in_set(
                        mode="bybit_demo", live_symbols=live_syms,
                    )
                    for _sym in pruned:
                        self._log.info(
                            f"POSITIONS_CACHE_PRUNE | sym={_sym} "
                            f"mode=bybit_demo reason=missing_from_response "
                            f"live_n={len(live_syms)} "
                            f"empty_dwell={self._consecutive_empty_confirmed_ticks} "
                            f"| {ctx()}"
                        )
                except Exception as e:
                    self._log.warning(
                        f"POSITIONS_CACHE_PRUNE_FAIL | "
                        f"err='{str(e)[:120]}' | {ctx()}"
                    )

        return PositionsQueryResult(
            confirmed=True, positions=tuple(positions),
        )

    async def get_position(self, symbol: str) -> Position | None:
        """Get a single position by symbol. Returns ``None`` if not open."""
        positions = await self.get_positions(symbol=symbol)
        return positions[0] if positions else None

    async def get_last_close(
        self,
        symbol: str,
        *,
        order_id: str | None = None,
        ws_exec_price: float | None = None,
        ws_close_ts_ms: float | None = None,
        qty: float | None = None,
        tick_tolerance: float | None = None,
        entry_price: float | None = None,
    ) -> dict[str, Any] | None:
        """Fetch authoritative close data for the most recent closed position.

        Phantom-loss fix Commit 2: when WS identity hints (order_id /
        ws_exec_price / qty) are supplied, fetch a window of rows and SELECT
        the one that belongs to THIS close (orderId, else exit-price+freshness,
        else qty), polling until a MATCH is found rather than accepting
        whatever rows[0] is — the stale-row root cause. With NO hints the
        behaviour is byte-identical to the legacy single-shot {limit:1,
        rows[0]} path, so the 11 watchdog/sniper callers are unaffected.

        Queries /v5/position/closed-pnl for the most recent entry. Bybit
        provides ``avgEntryPrice``, ``avgExitPrice``, ``closedPnl``,
        ``qty``, ``createdTime`` (open) and ``updatedTime`` (close). The
        return shape mirrors Shadow's keys (``exit_price``,
        ``net_pnl_pct``, ``net_pnl_usd``, ``close_trigger``, ``closed_at``,
        ``hold_duration_seconds``, ``result``) so watchdog/TIAS consumers
        index identically across exchanges.

        Returns ``None`` when no closed record exists or the API is
        unreachable — the watchdog falls back to its ticker cache.

        P3 of P1-P10: Bybit's closed-pnl indexer is asynchronously
        replicated. The audit measured a 35% fallback rate (single-shot
        polls returned None then watchdog used ticker mid). This method
        now performs bounded retry — up to 10 polls at 1-second intervals
        — before giving up. The 10s ceiling matches the watchdog tick
        cadence so a stalled retry never blocks beyond one cycle. Each
        attempt is logged at DEBUG; a final failure logs at INFO so
        operators can grep WD_LAST_CLOSE_INDEXER_RETRY_EXHAUSTED.
        """
        # Phantom-loss fix Commit 2: hints present → identity-match mode
        # (fetch a window, select THIS close's row); else legacy single-shot.
        identity_match = (
            order_id is not None or ws_exec_price is not None or qty is not None
        )
        query_limit = 50 if identity_match else 1

        rows: list[dict[str, Any]] = []
        last_err: str | None = None
        matched: dict[str, Any] | None = None
        rows_scanned = 0
        for attempt in range(_LAST_CLOSE_RETRY_ATTEMPTS):
            try:
                envelope = await self._client.get(
                    "/v5/position/closed-pnl",
                    {"category": _CATEGORY, "symbol": symbol, "limit": query_limit},
                    op="last_close",
                )
                rows = (envelope.get("result") or {}).get("list") or []
                if rows:
                    if identity_match:
                        rows_scanned = len(rows)
                        matched = self._select_close_row(
                            rows,
                            order_id=order_id,
                            ws_exec_price=ws_exec_price,
                            ws_close_ts_ms=ws_close_ts_ms,
                            qty=qty,
                            tick_tolerance=tick_tolerance,
                            entry_price=entry_price,
                        )
                        if matched is not None:
                            if attempt > 0:
                                self._log.info(
                                    f"BYBIT_DEMO_LAST_CLOSE_RETRY_OK | sym={symbol} "
                                    f"attempts={attempt + 1} | {ctx()}"
                                )
                            break
                        # Rows exist but none belong to THIS close yet — the
                        # indexer has not replicated it. Keep polling rather
                        # than accept a stale wrong-trade row (root-cause fix:
                        # wait for the MATCH, not for ANY row).
                    else:
                        if attempt > 0:
                            self._log.info(
                                f"BYBIT_DEMO_LAST_CLOSE_RETRY_OK | sym={symbol} "
                                f"attempts={attempt + 1} | {ctx()}"
                            )
                        break
            except TradingMCPError as e:
                last_err = str(e)[:120]
                # Phase 12.8 (lifecycle-logging-audit Gap 8.2-G1): promoted
                # from DEBUG to INFO. Operators need visibility into per-retry
                # attempts to monitor retry-loop health (latency / counts).
                self._log.info(
                    f"BYBIT_DEMO_LAST_CLOSE_RETRY | sym={symbol} "
                    f"attempt={attempt + 1}/{_LAST_CLOSE_RETRY_ATTEMPTS} "
                    f"err='{last_err}' | {ctx()}"
                )
            # Don't sleep after the final attempt.
            if attempt < _LAST_CLOSE_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(_LAST_CLOSE_RETRY_INTERVAL_S)

        if identity_match:
            if matched is None:
                # No row for THIS close (indexer lag / no orderId field). Return
                # None — the WS-derived net stands; the reconcile simply found
                # nothing. NEVER fall back to a stale rows[0] (the root cause).
                self._log.info(
                    f"CLOSE_PNL_NO_MATCH | sym={symbol} "
                    f"oid={(order_id or '')[:12]} rows_scanned={rows_scanned} "
                    f"reason=not_indexed_or_no_match | {ctx()}"
                )
                return None
            row = matched
            self._log.info(
                f"CLOSE_PNL_ROW_MATCHED | sym={symbol} "
                f"oid={(order_id or '')[:12]} rows_scanned={rows_scanned} | {ctx()}"
            )
        else:
            if not rows:
                self._log.info(
                    f"BYBIT_DEMO_LAST_CLOSE_INDEXER_RETRY_EXHAUSTED | sym={symbol} "
                    f"attempts={_LAST_CLOSE_RETRY_ATTEMPTS} "
                    f"interval_s={_LAST_CLOSE_RETRY_INTERVAL_S} "
                    f"last_err='{last_err or 'none'}' | {ctx()}"
                )
                return None
            row = rows[0]

        # Bybit timestamps are millisecond-epoch strings.
        created_ms = _safe_float(row.get("createdTime"))
        updated_ms = _safe_float(row.get("updatedTime"))
        hold_seconds = max(0.0, (updated_ms - created_ms) / 1000.0)

        avg_entry = _safe_float(row.get("avgEntryPrice"))
        avg_exit = _safe_float(row.get("avgExitPrice"))
        closed_pnl = _safe_float(row.get("closedPnl"))
        qty = _safe_float(row.get("qty"))
        side_str = str(row.get("side", "Buy"))

        # F1 fix (2026-06-08): derive net_pnl_pct from the AUTHORITATIVE net
        # dollar (closedPnl) over the position notional, NOT from the price
        # delta times the row's `side`. The closed-pnl row's `side` is the
        # CLOSING-order side (a Sell closes a long, a Buy closes a short — see
        # close_position ~759-760), the OPPOSITE of the open side, so the old
        # price-delta x side formula produced the EXACT NEGATION of the truth on
        # every close (a winning Buy stored a negative pct, e.g. LTC +0.372% ->
        # -0.3718). closedPnl is correctly signed by Bybit, so deriving the
        # percent from it makes the percent agree with the dollar in BOTH sign
        # and magnitude (net, post-fee) at the single SOURCE every consumer (the
        # WS close, the reconciler, the zombie reconciler, TIAS, data_lake,
        # capture) reads via get_last_close. notional = avg_entry * qty matches
        # the coordinator T2-8 corrector's notional basis exactly. When the
        # notional is unknown (avg_entry/qty missing) the percent cannot be
        # derived here — left 0.0 and recovered downstream where the notional is
        # known (the coordinator/Mode-A path, a separate named follow-up).
        notional = avg_entry * qty
        if notional > 0:
            net_pnl_pct = (closed_pnl / notional) * 100.0
            self._log.info(
                f"PNL_PCT_DERIVED | sym={symbol} avg_entry={avg_entry} "
                f"qty={qty} notional={notional:.4f} closed_pnl={closed_pnl:+.4f} "
                f"net_pnl_pct={net_pnl_pct:+.4f}% raw_side={side_str} "
                f"| derived from authoritative dollar (F1) | {ctx()}"
            )
        else:
            net_pnl_pct = 0.0

        # ISO 8601 UTC for closed_at (matches Shadow's format).
        from datetime import datetime, timezone
        closed_at_iso = (
            datetime.fromtimestamp(updated_ms / 1000.0, tz=timezone.utc).isoformat()
            if updated_ms > 0
            else ""
        )

        return {
            "symbol": symbol,
            "exit_price": avg_exit,
            "entry_price": avg_entry,
            "qty": qty,
            "side": side_str,
            "net_pnl_pct": net_pnl_pct,
            "net_pnl_usd": closed_pnl,
            # HIGH-3 fix (2026-05-09): return the cached close_trigger
            # populated by close_position when this symbol was system-
            # initiated. Falls back to "exchange_match" for genuinely
            # exchange-initiated closes (SL/TP hit on Bybit's side,
            # manual UI close — these don't go through close_position
            # so no cache entry exists, and "exchange_match" is the
            # correct semantic label for that case).
            "close_trigger": (
                self._get_cached_close_trigger(symbol) or "exchange_match"
            ),
            "closed_at": closed_at_iso,
            "hold_duration_seconds": hold_seconds,
            "result": "WIN" if closed_pnl > 0 else "LOSS",
        }

    def _select_close_row(
        self,
        rows: list[dict[str, Any]],
        *,
        order_id: str | None,
        ws_exec_price: float | None,
        ws_close_ts_ms: float | None,
        qty: float | None,
        tick_tolerance: float | None,
        entry_price: float | None = None,
    ) -> dict[str, Any] | None:
        """Phantom-loss fix Commit 2: pick the closed-pnl row that belongs to
        THIS close. Priority: (1) exact orderId; (2) avgExitPrice within tick
        tolerance AND fresh enough AND qty-consistent; (3) qty-only (the
        external-detect path) — the freshest row whose qty matches. Returns
        None when nothing qualifies, so the caller keeps polling / drops the
        reconcile rather than booking a stale wrong-trade row.
        """
        floor_ms = (
            (ws_close_ts_ms - _CLOSE_MATCH_SKEW_MS)
            if (ws_close_ts_ms and ws_close_ts_ms > 0)
            else 0.0
        )

        def _fresh_ok(r: dict[str, Any]) -> bool:
            return floor_ms <= 0 or _safe_float(r.get("updatedTime")) >= floor_ms

        def _qty_ok(r: dict[str, Any]) -> bool:
            if qty is None or qty <= 0:
                return True
            rq = _safe_float(r.get("qty"))
            return rq > 0 and abs(rq - qty) / max(qty, 1e-9) <= 0.01

        # 1. Exact orderId match — the strongest signal.
        if order_id:
            for r in rows:
                if str(r.get("orderId", "")) == str(order_id):
                    return r

        # 2. Price + freshness (+ qty) match.
        if ws_exec_price is not None and ws_exec_price > 0:
            tol = (
                tick_tolerance
                if (tick_tolerance and tick_tolerance > 0)
                else abs(ws_exec_price) * 0.001
            )
            best: dict[str, Any] | None = None
            for r in rows:
                r_exit = _safe_float(r.get("avgExitPrice"))
                if r_exit <= 0 or abs(r_exit - ws_exec_price) > tol:
                    continue
                if not _fresh_ok(r) or not _qty_ok(r):
                    continue
                if best is None or _safe_float(r.get("updatedTime")) > _safe_float(
                    best.get("updatedTime")
                ):
                    best = r
            return best

        # 3. Qty-only (external-detect path). F5-b (2026-06-08): when the trade's
        # entry price is known, DISAMBIGUATE among qty-matching, fresh rows by the
        # CLOSEST avgEntryPrice. Re-entries on the same symbol share a qty within
        # the 1% tolerance but have DIFFERENT entries (the LDO case: 0.2688 vs
        # 0.2668 vs 0.2659), so a LATER same-qty re-entry's row — which the
        # freshness floor (F5-c) cannot reject because it post-dates this trade's
        # open — is still told apart by its entry. Selection key is
        # (entry_distance, -updatedTime): closest entry wins, freshest breaks
        # ties. With no entry hint, entry_distance is 0 for every row so this is
        # byte-identical to the prior "freshest row" behaviour.
        if qty is not None and qty > 0:
            best: dict[str, Any] | None = None
            best_key: tuple[float, float] | None = None
            for r in rows:
                if not _qty_ok(r) or not _fresh_ok(r):
                    continue
                _ut = _safe_float(r.get("updatedTime"))
                if entry_price is not None and entry_price > 0:
                    _re = _safe_float(r.get("avgEntryPrice"))
                    _dist = (
                        abs(_re - entry_price) / entry_price if _re > 0 else 1e9
                    )
                    _key = (_dist, -_ut)
                else:
                    _key = (0.0, -_ut)
                if best is None or _key < best_key:
                    best = r
                    best_key = _key
            return best

        return None

    async def close_position(
        self,
        symbol: str,
        *,
        purpose: str = "layer4_close",
        close_trigger: str = "system_close",
    ) -> Order:
        """Close an open position via a reduceOnly market order.

        Mirrors :meth:`PositionService.close_position`. Looks up the
        current position to get size + side, then places an opposite-side
        market order with ``reduceOnly=true`` to flatten. Bybit's
        matching engine handles the close.

        Returns ``Order(FILLED)`` on success or ``Order(REJECTED)`` on
        rejection / transport failure / no open position. NEVER raises.

        P3 of P1-P10: replaces the audit-flagged ``pos.mark_price``
        exit-price estimate (which is 0-5s stale and excludes fees) with
        a real fill resolution via /v5/order/realtime. The 50-100ms
        added latency is well worth the elimination of systematic
        slippage understatement on every system-initiated close.

        Phase 12.7 (lifecycle-logging-audit Gap 7.4-G1): added
        ``close_trigger`` parameter so callers (sniper / CALL_B /
        watchdog / time decay / manual) can surface their trigger
        reason through to BYBIT_DEMO_POSITION_CLOSE. Defaults to
        "system_close" for back-compat with existing callers; recommended
        values: "sniper_p9", "sniper_m4", "callb_close", "wd_hard_stop",
        "wd_emergency", "wd_timeout", "wd_profit_take", "wd_plan_timer",
        "time_decay_age", "time_decay_mae", "time_decay_struct",
        "manual_telegram".
        """
        self._log.info(
            f"BYBIT_DEMO_POSITION_CLOSE | sym={symbol} purpose={purpose} "
            f"close_trigger={close_trigger} | {ctx()}"
        )
        # HIGH-3 fix (2026-05-09): stash the trigger so get_last_close can
        # return it (the watchdog polls get_last_close after detecting a
        # flat position; pre-fix it always saw the hardcoded
        # "exchange_match" and lost the original sniper_p9 / callb_close /
        # wd_emergency / time_decay_* attribution).
        self._record_close_trigger(symbol, close_trigger)

        # T3-3 / T3-4 fix (six-tier-fixes 2026-05-11) — Phase5 F-15 +
        # F-20. ALSO push the trigger into the TradeCoordinator so the
        # WS subscriber's pop_close_reason returns the real trigger
        # (sniper / watchdog / time_decay / callb) instead of the
        # mode-aware default `bybit_demo_sl_tp`. With this in place:
        #   - Phase5 F-15: WS dispatch records the correct closed_by.
        #   - Phase5 F-20: when watchdog races a redundant
        #     on_trade_closed call, the coordinator emits
        #     COORD_DOUBLE_CLOSE but the FIRST (correct) trigger has
        #     already been recorded; the duplicate is a clean dedup.
        # Best-effort: a failure here does not block the close.
        if self._coordinator is not None and hasattr(
            self._coordinator, "set_close_reason"
        ):
            try:
                self._coordinator.set_close_reason(symbol, close_trigger)
            except Exception as _e:
                self._log.warning(
                    f"COORD_SET_CLOSE_REASON_FAIL | sym={symbol} "
                    f"trigger={close_trigger} err='{str(_e)[:120]}' | {ctx()}"
                )

        # Look up the current position to derive close-side and qty.
        pos = await self.get_position(symbol)
        if pos is None or pos.size <= 0:
            self._log.warning(
                f"BYBIT_DEMO_CLOSE_NO_POSITION | sym={symbol} | {ctx()}"
            )
            return _rejected_order(symbol)

        # Opposite side closes long with Sell, short with Buy.
        close_side = Side.SELL if pos.side == Side.BUY else Side.BUY

        body: dict[str, Any] = {
            "category": _CATEGORY,
            "symbol": symbol,
            "side": close_side.value,
            "orderType": "Market",
            "qty": str(pos.size),
            "positionIdx": _POSITION_IDX,
            "reduceOnly": True,
            "timeInForce": "IOC",
        }

        try:
            envelope = await self._client.post(
                "/v5/order/create", body, op="close_position"
            )
        except TradingMCPError as e:
            self._log.warning(
                f"BYBIT_DEMO_CLOSE_REJECT | sym={symbol} err={str(e)[:160]} | {ctx()}"
            )
            return _rejected_order(symbol, side=close_side)

        # P3: capture the orderId from the order-create response and
        # poll /v5/order/realtime to resolve the actual fill price.
        # Falls back to mark_price only when orderId or fill resolution
        # fails (rare edge case — order DID place, the exchange just
        # didn't return the orderId in the response). Layer 4 reconciles
        # via get_last_close (now bounded-retry-protected).
        order_id = (envelope.get("result") or {}).get("orderId", "") or ""
        exit_price = pos.mark_price  # safe fallback
        if order_id:
            try:
                avg_price, _filled_qty, _status = await _resolve_close_fill(
                    client=self._client,
                    log=self._log,
                    symbol=symbol,
                    order_id=order_id,
                    requested_qty=pos.size,
                )
                if avg_price > 0:
                    exit_price = avg_price
                    # Phase 12.7 (lifecycle-logging-audit Gap 7.10-G1):
                    # CLOSE_FILL_CONFIRMED success log between place and
                    # last_close. Operators can confirm "close placed AND
                    # filled on Bybit" from one log line.
                    self._log.info(
                        f"CLOSE_FILL_CONFIRMED | sym={symbol} "
                        f"oid={order_id[:12]} fill_price={avg_price} "
                        f"fill_qty={_filled_qty} status={_status} | {ctx()}"
                    )
                else:
                    self._log.info(
                        f"BYBIT_DEMO_CLOSE_FILL_FALLBACK | sym={symbol} "
                        f"oid={order_id[:12]} reason=zero_avg_price "
                        f"using_mark_price={pos.mark_price} | {ctx()}"
                    )
            except Exception as e:
                self._log.warning(
                    f"BYBIT_DEMO_CLOSE_FILL_FALLBACK | sym={symbol} "
                    f"oid={order_id[:12]} reason=resolve_exception "
                    f"err='{str(e)[:80]}' using_mark_price={pos.mark_price} | {ctx()}"
                )
        else:
            self._log.info(
                f"BYBIT_DEMO_CLOSE_FILL_FALLBACK | sym={symbol} "
                f"reason=no_order_id_in_response "
                f"using_mark_price={pos.mark_price} | {ctx()}"
            )

        # T3-2 fix (six-tier-fixes 2026-05-11) — forward the resolved Bybit
        # order_id (or empty for synthetic fallback) so the orders table
        # gets a unique PK per close instead of clobbering.
        close_order = _build_close_order(
            symbol, close_side, pos.size, exit_price, order_id=order_id,
        )

        # P7 of P1-P10: persist close order + trade history + position
        # zeroing. Mirrors live PositionService.close_position:163-199.
        # Skipped when trading_repo not injected.
        # Phase 12.5 (lifecycle-logging-audit Gap 5.10-G1): added
        # BYBIT_DEMO_PERSIST_OK success-path tags so operators can
        # confirm persistence works without grepping the DB directly.
        if self._trading_repo is not None:
            try:
                # HIGH-2 fix (2026-05-09): pass exchange_mode so the
                # new orders.exchange_mode column is tagged correctly.
                # This adapter is bybit_demo by definition.
                await self._trading_repo.save_order(
                    close_order, exchange_mode="bybit_demo",
                )
                self._log.info(
                    f"BYBIT_DEMO_PERSIST_OK | sym={symbol} table=orders "
                    f"order_id='{close_order.order_id}' | {ctx()}"
                )
            except Exception as e:
                self._log.warning(
                    f"BYBIT_DEMO_PERSIST_ORDER_FAIL | sym={symbol} "
                    f"err='{str(e)[:120]}' | {ctx()}"
                )
            # CRITICAL-3 fix (2026-05-09) — trade_history persistence is
            # now owned by the coordinator-level
            # `_trade_history_close_callback` registered in
            # workers/manager.py. That callback fires once per close
            # regardless of trigger source (WS execution event,
            # watchdog poll, sniper, time-decay), covering BOTH
            # system-initiated closes (this code path) AND WS-only
            # closes (SL/TP hit on Bybit's side, manual UI close).
            # Pre-fix: this adapter wrote the row directly with
            # `trade_id=close_order.order_id or f"bd-{symbol}-close"`,
            # but `_build_close_order` hardcodes order_id="" so the
            # fallback always won and 116+ closes collapsed into 30
            # collision-overwritten rows. The callback uses
            # state.order_id (open-side, unique per trade) with an
            # epoch-ms fallback, eliminating collisions. Single
            # writer == no race, no idempotency check needed.
            #
            # The save_order and save_position calls below remain in
            # this adapter because those tables (orders, positions)
            # do not have a coordinator close-callback path.
            try:
                # Mirror live: zero the position (save_position deletes
                # rows with size==0). I4 of cascade-fix series passes
                # exchange_mode='bybit_demo' for symmetry with the
                # save_position call in get_positions; on the
                # delete-on-zero path the kwarg is ignored (DELETE
                # WHERE symbol = ?), but threading it keeps the
                # contract consistent and surfaces tagging mistakes
                # earlier if the schema ever changes.
                pos.size = 0.0
                await self._trading_repo.save_position(
                    pos, exchange_mode="bybit_demo",
                )
                self._log.info(
                    f"BYBIT_DEMO_PERSIST_OK | sym={symbol} table=positions "
                    f"action=zeroed | {ctx()}"
                )
            except Exception as e:
                self._log.warning(
                    f"BYBIT_DEMO_PERSIST_POSITION_FAIL | sym={symbol} "
                    f"err='{str(e)[:120]}' | {ctx()}"
                )

        return close_order

    def attach_coordinator(self, coordinator: Any) -> None:
        """Wire the TradeCoordinator reference for partial-close routing.

        Called by WorkerManager once both objects are constructed.
        Without this attachment, reduce_position cannot signal the
        partial-close intent to the WS subscriber, and the partial fill
        would route through the full on_trade_closed path (Issue 4
        pre-fix behaviour). Tests that don't need the coordinator may
        skip this call; reduce_position degrades gracefully (the warning
        path below makes it visible).
        """
        self._coordinator = coordinator

    def _clear_partial_close_pending_on_fallback(
        self, symbol: str, qty: float, *, reason: str,
    ) -> None:
        """T1-4 (2026-05-12) — clear the partial-close pending entry
        stamped before a reduceOnly POST when that POST cannot proceed
        as a true partial.

        Must be called on EVERY fallback exit from ``reduce_position``;
        otherwise the stale entry causes the next WS close event to be
        mislabeled ``partial='Y'`` (downstream effect F-R) and a stray
        partial trade_log row is written even though the position is
        actually flat (operator's "small amount sold but coin still in
        dashboard" symptom).

        Reuses :meth:`TradeCoordinator.pop_partial_close_pending` (no
        new coordinator API needed). Idempotent: safe to call when no
        entry exists.
        """
        if self._coordinator is None:
            return
        if not hasattr(self._coordinator, "pop_partial_close_pending"):
            return
        popped = self._coordinator.pop_partial_close_pending(symbol)
        if popped is not None:
            self._log.info(
                f"BYBIT_DEMO_PARTIAL_CLEAR_ON_FALLBACK | sym={symbol} "
                f"qty_intended={qty} reason={reason} | {ctx()}"
            )

    async def reduce_position(self, symbol: str, qty: float) -> Order:
        """Reduce position by ``qty`` via a partial reduceOnly order.

        Mirrors :meth:`PositionService.reduce_position`. Falls back to a
        full ``close_position`` on rejection or transport failure,
        emitting ``REDUCE_FALLBACK`` so the downgrade is visible (matches
        Shadow's audit log format exactly).

        Issue 4 fix (2026-05-11): when the coordinator is attached AND
        the partial qty is strictly less than the position size, stamp a
        partial-close pending entry on the coordinator BEFORE the order
        is sent. The WS subscriber's close-dispatch path consumes this
        entry to route the resulting execution event through
        on_partial_close, preventing the pre-fix bug where a reduceOnly
        partial fill (order leaves_qty=0) was treated as a full close.

        T1-4 (2026-05-12): the partial qty is now floor-quantized to the
        symbol's ``lotSizeFilter.qtyStep`` BEFORE the POST, eliminating
        the dominant cause of ``ret_code=10001 'Qty invalid'`` rejects
        that silently downgraded 50 % scale-outs into full closes
        (verified live on OPUSDT, AEROUSDT, GMTUSDT). When the
        instruments service is not wired, OR fetch fails, OR the
        quantized qty falls below ``lotSizeFilter.minOrderQty``, the
        path falls back to a logged full close (per operator decision
        2026-05-12 — Path B) and clears the pending-close entry so the
        WS event does not mislabel the close as partial.
        """
        pos = await self.get_position(symbol)
        if pos is None or pos.size <= 0:
            self._log.warning(
                f"REDUCE_FALLBACK | sym={symbol} qty={qty} "
                f"reason=no_position | {ctx()}"
            )
            return _rejected_order(symbol)

        if qty >= pos.size:
            # HIGH-7 fix (2026-05-09): emit REDUCE_FALLBACK with structured
            # reason so this silent-degrade case is visible to operators.
            # Pre-fix this fell through to close_position with no log line,
            # making qty-validation degrades indistinguishable from
            # voluntary full closes in audit history.
            self._log.warning(
                f"REDUCE_FALLBACK | sym={symbol} qty={qty} pos_size={pos.size} "
                f"reason=qty_exceeds_size | {ctx()}"
            )
            return await self.close_position(symbol, purpose="reduce_to_close")

        # ── T1-4 (2026-05-12): qty quantization pre-flight ──
        # Floor-quantize qty to the symbol's lotSizeFilter.qtyStep
        # BEFORE marking the partial-close pending entry so a downgrade
        # to full-close on the (qty < min_qty) edge case never leaves a
        # stale stamp behind. If the InstrumentService is unwired or the
        # fetch fails, fall back to a logged full close — never POST a
        # raw float qty (the pre-fix behaviour).
        snapped_qty: float = qty
        if self._instrument_service is None:
            self._log.warning(
                f"BYBIT_DEMO_QTY_QUANTIZE_UNAVAILABLE | sym={symbol} "
                f"requested={qty} action=full_close "
                f"| InstrumentService not wired — defaulting to full close so "
                f"unquantized qty is never sent to Bybit | {ctx()}"
            )
            self._clear_partial_close_pending_on_fallback(
                symbol, qty, reason="instrument_unavailable",
            )
            return await self.close_position(
                symbol, purpose="reduce_no_qty_step",
            )

        try:
            info = await self._instrument_service.get_instrument_info(symbol)
        except Exception as e:
            self._log.warning(
                f"BYBIT_DEMO_QTY_QUANTIZE_FETCH_FAIL | sym={symbol} "
                f"requested={qty} err='{str(e)[:120]}' action=full_close "
                f"| {ctx()}"
            )
            self._clear_partial_close_pending_on_fallback(
                symbol, qty, reason="fetch_fail",
            )
            return await self.close_position(
                symbol, purpose="reduce_no_qty_step",
            )

        from src.core.utils import quantize_qty_floor
        qty_step = float(getattr(info, "qty_step", 0.0) or 0.0)
        min_qty = float(getattr(info, "min_qty", 0.0) or 0.0)
        snapped_qty = quantize_qty_floor(qty, qty_step) if qty_step > 0 else qty
        if qty_step > 0 and snapped_qty != qty:
            self._log.info(
                f"BYBIT_DEMO_QTY_QUANTIZED | sym={symbol} requested={qty} "
                f"qty_step={qty_step} snapped={snapped_qty} | {ctx()}"
            )
        if snapped_qty <= 0 or (min_qty > 0 and snapped_qty < min_qty):
            # Edge case: position too small for a partial that respects
            # lotSizeFilter. Operator decision (2026-05-12 / Path B):
            # downgrade to full close + clear pending entry so the WS
            # event records the close cleanly.
            self._log.warning(
                f"BYBIT_DEMO_QTY_BELOW_MIN | sym={symbol} requested={qty} "
                f"quantized={snapped_qty} min={min_qty} action=full_close "
                f"| {ctx()}"
            )
            self._clear_partial_close_pending_on_fallback(
                symbol, qty, reason="qty_below_min",
            )
            return await self.close_position(
                symbol, purpose="reduce_qty_below_min",
            )

        # Issue 4 fix: signal partial-close intent. Must run BEFORE the
        # POST so the WS execution event (which can arrive within ~100ms
        # of the POST returning) finds the pending entry. The
        # coordinator's mark_partial_close_pending is idempotent (a
        # second mark overwrites the prior); if a retry happens, the
        # latest qty wins.
        # T1-4: stamp with the SNAPPED qty so the WS subscriber's
        # bookkeeping matches what was actually submitted to Bybit
        # (consumer reads partial.qty for residual tracking).
        if self._coordinator is not None and hasattr(
            self._coordinator, "mark_partial_close_pending"
        ):
            self._coordinator.mark_partial_close_pending(
                symbol, snapped_qty, by="mode4_partial",
            )
        else:
            self._log.warning(
                f"REDUCE_NO_COORDINATOR | sym={symbol} qty={snapped_qty} "
                f"| coordinator unattached — partial may misroute as full close "
                f"| {ctx()}"
            )

        close_side = Side.SELL if pos.side == Side.BUY else Side.BUY
        body: dict[str, Any] = {
            "category": _CATEGORY,
            "symbol": symbol,
            "side": close_side.value,
            "orderType": "Market",
            "qty": str(snapped_qty),
            "positionIdx": _POSITION_IDX,
            "reduceOnly": True,
            "timeInForce": "IOC",
        }

        try:
            # T3-2 fix (six-tier-fixes 2026-05-11) — capture the envelope
            # so the orderId can be forwarded into _build_close_order
            # and the orders table gets a unique PK per partial close.
            _envelope_red = await self._client.post(
                "/v5/order/create", body, op="reduce_position"
            )
        except TradingMCPError as e:
            # HIGH-7 fix (2026-05-09): extract structured fields from
            # e.details so operators see ret_code / ret_msg / op as
            # individual key=val fields (greppable, alert-routable) rather
            # than a stringified blob truncated mid-detail at [:160].
            # Pre-fix log line was:
            #   err='[ts] BybitAPIError: Bybit demo: API error
            #        (10001: Qty invalid) | details={'ret_code': 10001,
            #        'ret_msg': 'Qty invalid', 'op': 'redu'  ← truncated!
            # Post-fix splits each detail into its own field.
            _details = getattr(e, "details", {}) or {}
            _ret_code = _details.get("ret_code", "")
            _ret_msg = str(_details.get("ret_msg", ""))[:120]
            _op = str(_details.get("op", ""))[:40]
            self._log.warning(
                f"REDUCE_FALLBACK | sym={symbol} qty={snapped_qty} "
                f"reason=bybit_reject ret_code={_ret_code} "
                f"ret_msg='{_ret_msg}' op={_op} "
                f"err='{str(e)[:160]}' | {ctx()}"
            )
            # T1-4: clear the pending entry so the resulting WS close event
            # records as a clean full close (not a stale partial='Y').
            self._clear_partial_close_pending_on_fallback(
                symbol, snapped_qty, reason="bybit_reject",
            )
            return await self.close_position(symbol, purpose="reduce_fallback")

        # Mark-price exit estimate — Layer 4 reconciles via
        # get_last_close (see comment in close_position).
        # T3-2 fix: forward the resolved Bybit orderId (or fallback to
        # synthetic) so partial-close rows in the orders table also
        # have unique PKs.
        # T1-4: return the SNAPPED qty so downstream consumers (sniper's
        # fallback-detection block at profit_sniper.py:2986-2992) see the
        # qty actually submitted to Bybit, not the pre-quantization request.
        _red_order_id = (
            (_envelope_red.get("result") or {}).get("orderId", "")
            if isinstance(_envelope_red, dict)
            else ""
        ) or ""
        return _build_close_order(
            symbol, close_side, snapped_qty, pos.mark_price,
            order_id=_red_order_id,
        )

    async def close_all_positions(self) -> list[Order]:
        """Close every open position concurrently.

        Bybit's matching engine processes orders independently per
        symbol, so issuing closes in parallel gives roughly an N-fold
        latency reduction over a serial loop. ``return_exceptions=True``
        ensures one failure doesn't strand the rest — exceptions are
        translated to REJECTED Orders so the return list shape is
        stable.
        """
        import asyncio
        positions = await self.get_positions()
        if not positions:
            return []
        raw = await asyncio.gather(
            *[self.close_position(p.symbol) for p in positions],
            return_exceptions=True,
        )
        results: list[Order] = []
        for pos, item in zip(positions, raw):
            if isinstance(item, BaseException):
                self._log.warning(
                    f"BYBIT_DEMO_CLOSE_ALL_ITEM_FAIL | sym={pos.symbol} "
                    f"err={str(item)[:160]} | {ctx()}"
                )
                results.append(_rejected_order(pos.symbol, side=pos.side))
            else:
                results.append(item)
        return results

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set per-symbol leverage via /v5/position/set-leverage.

        Returns ``True`` on success OR on Bybit's "leverage not modified"
        idempotent response (110043). False on any other error.
        """
        try:
            await self._client.post(
                "/v5/position/set-leverage",
                {
                    "category": _CATEGORY,
                    "symbol": symbol,
                    "buyLeverage": str(leverage),
                    "sellLeverage": str(leverage),
                },
                op="set_leverage",
            )
            return True
        except TradingMCPError as e:
            ret_code = (getattr(e, "details", {}) or {}).get("ret_code")
            return ret_code == 110043  # idempotent "not modified"

    async def set_stop_loss(self, symbol: str, stop_loss: float) -> bool:
        """Set SL on an existing position via /v5/position/trading-stop.

        CRITICAL-5 fix (2026-05-09): defensive precondition validates the
        SL is on the correct side of current mark_price BEFORE posting to
        Bybit. Catches the audit's KATUSDT/RENDERUSDT wrong-side bug at
        the adapter boundary even if upstream callers (sniper, watchdog,
        time-decay, CALL_B) regress. For Buy: SL must be < mark_price.
        For Sell: SL must be > mark_price. Local rejection emits
        BYBIT_DEMO_SET_SL_DIRECTION_BUG and avoids the noisy CRITICAL
        Telegram alert from the Bybit retCode 10001 roundtrip.

        Also handles ret_code 34040 ("not modified") as idempotent
        success — Bybit returns this when the requested SL equals the
        existing SL, which is not an error condition. Mirrors the same
        pattern at set_leverage:519 for ret_code 110043.
        """
        # Defensive wrong-side validation (CRITICAL-5)
        try:
            pos = await self.get_position(symbol)
        except Exception as e:
            self._log.debug(
                f"BYBIT_DEMO_SET_SL_PREFLIGHT_SKIP | sym={symbol} "
                f"reason=get_position_failed err='{str(e)[:80]}' | {ctx()}"
            )
            pos = None
        if pos is not None and pos.size > 0 and pos.mark_price > 0 and stop_loss > 0:
            is_long = pos.side == Side.BUY
            wrong_side = (
                (is_long and stop_loss >= pos.mark_price)
                or (not is_long and stop_loss <= pos.mark_price)
            )
            if wrong_side:
                self._log.warning(
                    f"BYBIT_DEMO_SET_SL_DIRECTION_BUG | sym={symbol} "
                    f"sl={stop_loss} mark={pos.mark_price} "
                    f"side={pos.side.value if hasattr(pos.side, 'value') else pos.side} "
                    f"reason=wrong_side_for_position blocked=true | {ctx()}"
                )
                return False

        try:
            await self._client.post(
                "/v5/position/trading-stop",
                {
                    "category": _CATEGORY,
                    "symbol": symbol,
                    "stopLoss": str(stop_loss),
                    "slTriggerBy": "LastPrice",
                    "positionIdx": _POSITION_IDX,
                },
                op="set_stop_loss",
            )
            # T4-2 / Phase5 F-12 (six-tier-fixes 2026-05-11) — emit a
            # structured success log so the 67-per-window SL_PROPAGATED
            # events have a paired confirmation that the change reached
            # Bybit. Pre-fix this branch returned True silently and
            # operators could not distinguish "67 successful SL updates
            # on Bybit" from "67 silently-succeeded local-state-only
            # changes that never actually reached Bybit".
            self._log.info(
                f"BYBIT_DEMO_SET_SL_OK | sym={symbol} sl={stop_loss} | {ctx()}"
            )
            return True
        except TradingMCPError as e:
            ret_code = (getattr(e, "details", {}) or {}).get("ret_code")
            # CRITICAL-5: 34040 "not modified" = idempotent success.
            # Bybit returns this when the requested SL equals current SL.
            # Same pattern as set_leverage:519 for 110043.
            if ret_code == 34040:
                self._log.debug(
                    f"BYBIT_DEMO_SET_SL_IDEMPOTENT | sym={symbol} sl={stop_loss} "
                    f"reason=not_modified_already_at_value | {ctx()}"
                )
                return True
            self._log.warning(
                f"BYBIT_DEMO_SET_SL_FAIL | sym={symbol} sl={stop_loss} "
                f"err={str(e)[:160]} | {ctx()}"
            )
            return False

    async def set_take_profit(self, symbol: str, take_profit: float) -> bool:
        """Set TP on an existing position via /v5/position/trading-stop.

        CRITICAL-5 fix (2026-05-09): mirror SL's defensive wrong-side
        validation. For TP, the side rule INVERTS vs SL:
        - Buy: TP must be > mark_price (close at higher price = profit)
        - Sell: TP must be < mark_price (close at lower price = profit)
        Audit ISSUE 1.7-A flagged this as a latent bug in TP — same root
        cause family as SL. Local rejection emits
        BYBIT_DEMO_SET_TP_DIRECTION_BUG. Also handles 34040 idempotent.
        """
        # Defensive wrong-side validation (CRITICAL-5)
        try:
            pos = await self.get_position(symbol)
        except Exception as e:
            self._log.debug(
                f"BYBIT_DEMO_SET_TP_PREFLIGHT_SKIP | sym={symbol} "
                f"reason=get_position_failed err='{str(e)[:80]}' | {ctx()}"
            )
            pos = None
        if pos is not None and pos.size > 0 and pos.mark_price > 0 and take_profit > 0:
            is_long = pos.side == Side.BUY
            # TP side rule INVERTS vs SL: Buy TP must be ABOVE price,
            # Sell TP must be BELOW price.
            wrong_side = (
                (is_long and take_profit <= pos.mark_price)
                or (not is_long and take_profit >= pos.mark_price)
            )
            if wrong_side:
                self._log.warning(
                    f"BYBIT_DEMO_SET_TP_DIRECTION_BUG | sym={symbol} "
                    f"tp={take_profit} mark={pos.mark_price} "
                    f"side={pos.side.value if hasattr(pos.side, 'value') else pos.side} "
                    f"reason=wrong_side_for_position blocked=true | {ctx()}"
                )
                return False

        try:
            await self._client.post(
                "/v5/position/trading-stop",
                {
                    "category": _CATEGORY,
                    "symbol": symbol,
                    "takeProfit": str(take_profit),
                    "tpTriggerBy": "LastPrice",
                    "positionIdx": _POSITION_IDX,
                },
                op="set_take_profit",
            )
            # T4-2 / Phase5 F-12 (six-tier-fixes 2026-05-11) — mirror
            # SET_SL_OK confirmation log for TP changes.
            self._log.info(
                f"BYBIT_DEMO_SET_TP_OK | sym={symbol} tp={take_profit} | {ctx()}"
            )
            return True
        except TradingMCPError as e:
            ret_code = (getattr(e, "details", {}) or {}).get("ret_code")
            if ret_code == 34040:
                self._log.debug(
                    f"BYBIT_DEMO_SET_TP_IDEMPOTENT | sym={symbol} tp={take_profit} "
                    f"reason=not_modified_already_at_value | {ctx()}"
                )
                return True
            self._log.warning(
                f"BYBIT_DEMO_SET_TP_FAIL | sym={symbol} tp={take_profit} "
                f"err={str(e)[:160]} | {ctx()}"
            )
            return False

    async def get_pnl_summary(self) -> dict[str, Any]:
        """Aggregate unrealized PnL across all open positions."""
        positions = await self.get_positions()
        total_unrealized = sum(p.unrealized_pnl for p in positions)
        return {
            "total_unrealized_pnl": total_unrealized,
            "total_realized_pnl": 0.0,
            "position_count": len(positions),
            "positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side.value,
                    "size": p.size,
                    "entry_price": p.entry_price,
                    "unrealized_pnl": p.unrealized_pnl,
                }
                for p in positions
            ],
        }

    async def health_check(self) -> bool:
        """Probe Bybit demo via the underlying client."""
        return await self._client.health_check()


# =============================================================================
# BybitDemoOrderService — Phase 2.C
# =============================================================================


class BybitDemoOrderService:
    """Bybit demo adapter for OrderService.

    Mirrors every public method of the real OrderService. Converts
    Side/OrderType enums to Bybit V5 strings and translates responses
    back to ``Order`` dataclass instances. Never raises — translates
    every error to ``Order(status=REJECTED)`` so the public adapter
    contract matches Shadow's exactly.
    """

    def __init__(
        self,
        client: BybitDemoClient,
        *,
        trading_repo: Any = None,
    ) -> None:
        """Construct with optional persistence repository.

        P7 of P1-P10: trading_repo is the project's TradingRepository.
        When provided, place_order writes the FILLED Order through to
        trading.db's orders table — same persistence contract the live
        OrderService follows. When None (legacy callers / tests),
        persistence is silently skipped.
        """
        self._client = client
        self._log = get_logger("bybit_demo")
        self._trading_repo = trading_repo
        # J2 (2026-05-14) — late-bound TradeCoordinator reference. The
        # coordinator is constructed AFTER this adapter (worker manager
        # builds adapters, then the coordinator, then attaches) so the
        # reference cannot be passed at __init__. WorkerManager calls
        # attach_coordinator after both objects are live. None when not
        # wired — the cross-direction guard falls through to "no check"
        # which preserves legacy / test behaviour.
        self._coordinator = None

    def attach_coordinator(self, coordinator: Any) -> None:
        """Wire the TradeCoordinator reference for the J2 cross-direction
        pre-order guard.

        Called by WorkerManager once both objects exist. Idempotent — a
        second call simply rebinds the reference (useful for live
        reconfiguration). When the coordinator is not wired the
        pre-order check is silently skipped, preserving the pre-J2
        contract for legacy callers and unit tests that do not pass a
        coordinator.
        """
        self._coordinator = coordinator

    async def place_order(
        self,
        symbol: str,
        side: Side,
        order_type: OrderType,
        qty: float,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        leverage: int | None = None,
        *,
        purpose: str = "other",
        layer_snapshot: "LayerSnapshot | None" = None,
        force: bool = False,
    ) -> Order:
        """Place an order via /v5/order/create.

        Mirrors :meth:`OrderService.place_order` (and Shadow's adapter
        equivalent). Sets per-symbol leverage via /v5/position/set-leverage
        when ``leverage`` is provided (Bybit applies leverage at the
        symbol level, not per-order; idempotent calls are tolerated).
        Translates SL/TP into Bybit's order-attached ``stopLoss`` and
        ``takeProfit`` fields. Sends an IOC (Immediate-Or-Cancel) market
        order — Bybit's matching engine handles fill semantics.

        Returns:
            ``Order(status=FILLED)`` on success, ``Order(status=REJECTED)``
            on Bybit-side rejection, validation failure, or transport
            failure. Never raises.
        """
        side_str = side.value if isinstance(side, Side) else str(side)
        order_type_str = (
            order_type.value if isinstance(order_type, OrderType) else str(order_type)
        )

        # Audit log captured BEFORE any API call so paper-trade history
        # is reconcilable against directive→execution traces even when
        # the order subsequently rejects. Mirrors Shadow's pattern.
        snap_keys = ""
        if layer_snapshot is not None:
            try:
                snap_keys = ",".join(sorted(layer_snapshot.__dict__.keys()))
            except AttributeError:
                snap_keys = type(layer_snapshot).__name__
        self._log.info(
            f"BYBIT_DEMO_ORDER_RECEIVED | sym={symbol} side={side_str} "
            f"qty={qty} purpose={purpose} layer_snapshot_keys=[{snap_keys}] "
            f"force={force} | {ctx()}"
        )

        # J2 (2026-05-14) — cross-direction pre-order guard.
        #
        # Audit observation OBS-21 at 21:09:10 UTC: brain proposed Buy
        # DYDXUSDT while a stale cache row showed Sell, APEX_DIR_LOCK_
        # OVERRIDE forced Buy, the order was placed against what the
        # local registry thought was an existing Short. In one-way mode
        # (_POSITION_IDX = 0) the result on Bybit is a netted position
        # whose state diverges from every local consumer.
        #
        # J1 (commits b0f16ce / daf1384) removed the stale-cache trigger
        # at the source. J2 adds defence-in-depth: a chokepoint guard
        # that consults the coordinator's authoritative ``_trades`` map
        # before any /v5/order/create is sent. If a position on this
        # symbol exists in the opposite direction, the order is
        # rejected at adapter level — Bybit is never asked to net the
        # legs.
        #
        # Bypass conditions:
        #   * coordinator not wired (legacy callers / tests) — no check
        #   * force=True (operator override; mirrors the audit-clean
        #     ORDER_BLOCKED actor=force_override pattern in
        #     OrderService.place_order)
        #   * symbol absent from coordinator._trades — no conflict
        #   * existing trade matches the new direction — additive, not
        #     cross-direction (Bybit's one-way model handles this case
        #     by sizing up the existing position; that is legitimate)
        if not force and self._coordinator is not None:
            try:
                _existing = self._coordinator._trades.get(symbol)
            except Exception:
                _existing = None
            if _existing is not None:
                _existing_side = str(getattr(_existing, "side", "") or "").strip()
                # Tolerate either enum-string ("Buy") or raw value
                # ("BUY") — TradeCoordinator stores the upstream value
                # which has historically varied.
                _existing_norm = _existing_side.lower()
                _new_norm = side_str.lower()
                if (
                    _existing_norm
                    and _new_norm
                    and _existing_norm != _new_norm
                ):
                    self._log.error(
                        f"ORDER_CROSS_DIRECTION_BLOCKED | sym={symbol} "
                        f"existing_side={_existing_side} "
                        f"new_side={side_str} purpose={purpose} | {ctx()}"
                    )
                    self._log.warning(
                        f"ORDER_BLOCKED | sym={symbol} side={side_str} "
                        f"purpose={purpose} "
                        f"reason=cross_direction_conflict "
                        f"actor=system_auto force={force} "
                        f"existing_side={_existing_side} | {ctx()}"
                    )
                    return Order(
                        order_id="",
                        symbol=symbol,
                        side=side,
                        order_type=order_type,
                        price=price or 0.0,
                        qty=qty,
                        status=OrderStatus.REJECTED,
                        filled_qty=0.0,
                        avg_fill_price=0.0,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                    )

        # Set leverage if requested. Failure here logs but does not
        # block the order — Bybit may reject leverage changes when a
        # position already exists, and that's not a reason to skip
        # the trade itself.
        if leverage is not None and leverage > 0:
            try:
                await self._client.post(
                    "/v5/position/set-leverage",
                    {
                        "category": _CATEGORY,
                        "symbol": symbol,
                        "buyLeverage": str(leverage),
                        "sellLeverage": str(leverage),
                    },
                    op="set_leverage",
                )
            except TradingMCPError as e:
                # Bybit retCode 110043 = "leverage not modified" is a
                # benign idempotent case. Other errors log at WARNING.
                ret_code = (e.details or {}).get("ret_code")
                if ret_code != 110043:
                    self._log.warning(
                        f"BYBIT_DEMO_LEVERAGE_FAIL | sym={symbol} "
                        f"lev={leverage} err={str(e)[:120]} | {ctx()}"
                    )

        # Build order body. IOC for market (matches Shadow's instant
        # fill semantics); GTC for limit. Bybit V5 expects qty / price
        # as strings to avoid float-precision ambiguity.
        # Phase 12.5 (lifecycle-logging-audit Gap 5.8-G1): generate a
        # deterministic orderLinkId so Bybit's idempotent retry semantics
        # apply when we re-submit the same order. Format mirrors Bybit's
        # 36-char client-order-id limit; symbol + side + millis is
        # collision-resistant within the per-call retry window (2-3s).
        import time as _t_link
        order_link_id = f"bd-{symbol[:10]}-{side_str[:1]}-{int(_t_link.time() * 1000)}"
        body: dict[str, Any] = {
            "category": _CATEGORY,
            "symbol": symbol,
            "side": side_str,
            "orderType": order_type_str,
            "qty": str(qty),
            "positionIdx": _POSITION_IDX,
            "timeInForce": "IOC" if order_type_str == "Market" else "GTC",
            "orderLinkId": order_link_id,
        }
        if order_type_str == "Limit" and price is not None:
            body["price"] = str(price)
        if stop_loss is not None:
            body["stopLoss"] = str(stop_loss)
            body["slTriggerBy"] = "LastPrice"
        if take_profit is not None:
            body["takeProfit"] = str(take_profit)
            body["tpTriggerBy"] = "LastPrice"

        self._log.info(
            f"BYBIT_DEMO_ORD_SEND | sym={symbol} side={side_str} qty={qty} "
            f"lev={leverage} sl={stop_loss} tp={take_profit} "
            f"link_id={order_link_id} | {ctx()}"
        )

        # Phase 12.5 (lifecycle-logging-audit Gap 5.8-G1): bounded
        # idempotent retry. Pre-fix, place_order had no retry on
        # transient HTTP / network failures (P3 only addressed
        # last_close). With orderLinkId set, Bybit treats re-submissions
        # as the SAME order (no double-fill risk). 2 attempts × 1s
        # interval covers the audit's HTTP_FAIL pattern without
        # changing latency semantics in steady state.
        envelope = None
        last_err: str | None = None
        _PLACE_RETRY_ATTEMPTS = 2
        _PLACE_RETRY_INTERVAL_S = 1.0
        for attempt in range(_PLACE_RETRY_ATTEMPTS):
            try:
                envelope = await self._client.post(
                    "/v5/order/create",
                    body,
                    op="place_order",
                )
                if attempt > 0:
                    self._log.info(
                        f"BYBIT_DEMO_PLACE_RETRY_OK | sym={symbol} "
                        f"link_id={order_link_id} attempts={attempt + 1} | {ctx()}"
                    )
                break
            except TradingMCPError as e:
                last_err = str(e)[:160]
                err_str = last_err.lower()
                # Retryable: timeout / 5xx / rate-limit. Permanent:
                # insufficient balance / invalid symbol / qty too small.
                _retryable = (
                    "timeout" in err_str
                    or "503" in err_str
                    or "502" in err_str
                    or "504" in err_str
                    or "rate limit" in err_str
                    or "10003" in err_str  # Bybit rate-limit retCode
                )
                if not _retryable or attempt >= _PLACE_RETRY_ATTEMPTS - 1:
                    # Non-retryable OR exhausted retries → REJECTED.
                    self._log.warning(
                        f"BYBIT_DEMO_ORDER_REJECT | sym={symbol} side={side_str} "
                        f"qty={qty} link_id={order_link_id} "
                        f"attempts={attempt + 1} err={last_err} | {ctx()}"
                    )
                    return _rejected_order(symbol, side=side)
                # Retryable transient → wait and retry with same
                # orderLinkId (idempotent on Bybit's side).
                self._log.info(
                    f"BYBIT_DEMO_PLACE_RETRY | sym={symbol} "
                    f"link_id={order_link_id} attempt={attempt + 1}/{_PLACE_RETRY_ATTEMPTS} "
                    f"err='{last_err}' | {ctx()}"
                )
                import asyncio as _aio_retry
                await _aio_retry.sleep(_PLACE_RETRY_INTERVAL_S)

        if envelope is None:
            # Defensive — loop exited without populating envelope.
            self._log.warning(
                f"BYBIT_DEMO_ORDER_REJECT | sym={symbol} side={side_str} "
                f"qty={qty} link_id={order_link_id} reason=retry_loop_no_envelope "
                f"err='{last_err or 'unknown'}' | {ctx()}"
            )
            return _rejected_order(symbol, side=side)

        result = envelope.get("result") or {}
        order_id = str(result.get("orderId", ""))

        # Bybit returns the orderId immediately; the actual fill price
        # is fetched via a subsequent /v5/order/realtime poll. For
        # IOC market orders the fill is near-instantaneous so we look
        # up the resolved order to populate avg_fill_price.
        fill_price = 0.0
        filled_qty = qty
        status = OrderStatus.FILLED

        if order_id:
            fill_price, filled_qty, status = await self._resolve_order_fill(
                symbol=symbol, order_id=order_id, requested_qty=qty
            )

        self._log.info(
            f"BYBIT_DEMO_ORD_RESP | sym={symbol} oid={order_id} "
            f"fill={fill_price} st={status.value} | {ctx()}"
        )

        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=_parse_side(side_str),
            order_type=OrderType.MARKET if order_type_str == "Market" else OrderType.LIMIT,
            price=fill_price,
            qty=filled_qty,
            status=status,
            filled_qty=filled_qty,
            avg_fill_price=fill_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        # P7 of P1-P10: persist to trading.db.orders. Mirrors live
        # OrderService.place_order:636 (await self._trading_repo.save_order).
        # Silently skipped when trading_repo not injected (legacy /
        # test callers); production wiring at workers/manager.py:341
        # passes the repo. Failure logs at WARNING but does not flip
        # the order to REJECTED — the order DID place on Bybit; we
        # just couldn't persist locally. Operators alerting on this
        # tag should investigate disk / lock contention.
        if self._trading_repo is not None:
            try:
                # HIGH-2 fix (2026-05-09): pass exchange_mode='bybit_demo'.
                await self._trading_repo.save_order(order, exchange_mode="bybit_demo")
                # Phase 12.5 (lifecycle-logging-audit Gap 5.10-G1):
                # added BYBIT_DEMO_PERSIST_OK success-path tag.
                self._log.info(
                    f"BYBIT_DEMO_PERSIST_OK | sym={symbol} table=orders "
                    f"oid={order_id} | {ctx()}"
                )
            except Exception as e:
                self._log.warning(
                    f"BYBIT_DEMO_PERSIST_ORDER_FAIL | sym={symbol} "
                    f"oid={order_id} err='{str(e)[:120]}' | {ctx()}"
                )

        return order

    async def _resolve_order_fill(
        self,
        symbol: str,
        order_id: str,
        requested_qty: float,
    ) -> tuple[float, float, OrderStatus]:
        """Look up the fill price + filled qty for a just-placed order.

        Bybit's /v5/order/create response only carries the orderId. To
        return a usable Order dataclass with avg_fill_price set, we
        poll /v5/order/realtime once for the order. If polling fails,
        we conservatively report ``status=FILLED, price=0, qty=requested``
        — Layer 4 / brain consumers reconcile via subsequent position
        queries. Returning REJECTED here would be wrong: the order
        DID place; we just couldn't read the fill price quickly enough.
        """
        try:
            envelope = await self._client.get(
                "/v5/order/realtime",
                {"category": _CATEGORY, "orderId": order_id, "symbol": symbol},
                op="resolve_fill",
            )
            orders = (envelope.get("result") or {}).get("list") or []
            if not orders:
                return 0.0, requested_qty, OrderStatus.FILLED
            o = orders[0]
            avg_price = _safe_float(o.get("avgPrice"))
            cum_qty = _safe_float(o.get("cumExecQty"), default=requested_qty)
            status_str = str(o.get("orderStatus", ""))
            # Surface partial fills as a distinct event. The OrderStatus
            # enum maps PartiallyFilled→FILLED to keep the contract
            # parity with Shadow (downstream sizing/reconciliation
            # treats both as fills), but the operator and any forensic
            # consumer needs to see when an IOC market order under-fills.
            if status_str == "PartiallyFilled":
                ratio = (cum_qty / requested_qty) if requested_qty > 0 else 0.0
                self._log.info(
                    f"BYBIT_DEMO_PARTIAL_FILL | sym={symbol} oid={order_id} "
                    f"filled={cum_qty} requested={requested_qty} "
                    f"ratio={ratio:.4f} | {ctx()}"
                )
            status = (
                OrderStatus.FILLED if status_str in ("Filled", "PartiallyFilled")
                else OrderStatus.REJECTED if status_str in ("Rejected", "Cancelled")
                else OrderStatus.NEW
            )
            return avg_price, cum_qty, status
        except TradingMCPError:
            return 0.0, requested_qty, OrderStatus.FILLED

    async def modify_order(
        self,
        symbol: str,
        order_id: str,
        qty: float | None = None,
        price: float | None = None,
    ) -> Order:
        """Modify an open order via /v5/order/amend.

        Bybit demo supports amend on limit orders only. Market orders
        are IOC and fill instantly so amend is not applicable, matching
        Shadow's behavior (which returns a rejected order for modify).
        """
        body: dict[str, Any] = {
            "category": _CATEGORY,
            "symbol": symbol,
            "orderId": order_id,
        }
        if qty is not None:
            body["qty"] = str(qty)
        if price is not None:
            body["price"] = str(price)
        try:
            await self._client.post("/v5/order/amend", body, op="amend_order")
            return Order(
                order_id=order_id,
                symbol=symbol,
                side=Side.BUY,  # unknown post-amend; consumers re-query
                order_type=OrderType.LIMIT,
                price=price or 0.0,
                qty=qty or 0.0,
                status=OrderStatus.NEW,
            )
        except TradingMCPError:
            return _rejected_order(symbol)

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an order via /v5/order/cancel."""
        try:
            await self._client.post(
                "/v5/order/cancel",
                {"category": _CATEGORY, "symbol": symbol, "orderId": order_id},
                op="cancel_order",
            )
            return True
        except TradingMCPError:
            return False

    async def cancel_all_orders(self, symbol: str | None = None) -> int:
        """Cancel all open orders via /v5/order/cancel-all.

        Returns the count of orders cancelled (0 on transport failure).
        """
        body: dict[str, Any] = {"category": _CATEGORY}
        if symbol is not None:
            body["symbol"] = symbol
        try:
            envelope = await self._client.post(
                "/v5/order/cancel-all", body, op="cancel_all"
            )
            cancelled = (envelope.get("result") or {}).get("list") or []
            return len(cancelled)
        except TradingMCPError:
            return 0

    async def get_open_orders(
        self, symbol: str | None = None
    ) -> list[Order]:
        """Query open orders via /v5/order/realtime."""
        params: dict[str, Any] = {"category": _CATEGORY, "openOnly": 0}
        if symbol is not None:
            params["symbol"] = symbol
        try:
            envelope = await self._client.get(
                "/v5/order/realtime", params, op="open_orders"
            )
        except TradingMCPError:
            return []
        return [
            _build_order_from_v5(o)
            for o in (envelope.get("result") or {}).get("list") or []
        ]

    async def get_order_history(
        self, symbol: str | None = None, limit: int = 50
    ) -> list[Order]:
        """Query historical orders via /v5/order/history."""
        params: dict[str, Any] = {
            "category": _CATEGORY,
            "limit": min(max(limit, 1), 50),
        }
        if symbol is not None:
            params["symbol"] = symbol
        try:
            envelope = await self._client.get(
                "/v5/order/history", params, op="order_history"
            )
        except TradingMCPError:
            return []
        return [
            _build_order_from_v5(o)
            for o in (envelope.get("result") or {}).get("list") or []
        ]

    async def health_check(self) -> bool:
        """Probe Bybit demo via the underlying client."""
        return await self._client.health_check()


# =============================================================================
# BybitDemoAccountService — Phase 2.E
# =============================================================================


class BybitDemoAccountService:
    """Bybit demo adapter for AccountService.

    Mirrors every public method of the real AccountService. Calls
    /v5/account/wallet-balance and constructs ``AccountInfo`` dataclass.
    """

    def __init__(self, client: BybitDemoClient) -> None:
        self._client = client
        self._log = get_logger("bybit_demo")

    async def get_wallet_balance(self) -> AccountInfo:
        """Get wallet balance via /v5/account/wallet-balance.

        Queries the UNIFIED account type (Bybit demo accounts default to
        UNIFIED). Returns the aggregated account-level totals; per-coin
        details are summed at the account level by Bybit so we don't
        need to walk the coin list.

        Returns ``AccountInfo`` with zeroed fields if the API is
        unreachable (matches Shadow's contract — never raises). This
        is the legacy contract that swallows error state into the zero
        sentinel — see :meth:`get_wallet_balance_with_confirmation` for
        the ground-truth-aware variant that distinguishes "exchange
        confirms zero balance" from "API call failed" (Issue I1 / F-26).
        """
        result = await self.get_wallet_balance_with_confirmation()
        # Legacy callers continue to receive the AccountInfo (zero
        # sentinel on unknown-state) so existing dashboards, boot
        # validation, brain context paths stay byte-for-byte identical.
        return result.account if result.account is not None else _empty_account_info()

    async def get_wallet_balance_with_confirmation(self) -> BalanceQueryResult:
        """Get wallet balance with an explicit "confirmed" flag.

        Issue I1 (F-26 TIMESTAMP_FAIL, 2026-05-14): same pattern as
        :meth:`get_positions_with_confirmation` applied to wallet
        balance. The audit captured 2 op=balance TIMESTAMP_FAIL events
        in 1.5h; each silently zeroed equity for downstream sizing and
        capital-tier consumers.

        Returns :class:`BalanceQueryResult` with:

          * ``confirmed=True``  → ``account`` reflects exchange truth
          * ``confirmed=False`` → adapter could not confirm; caller
            should preserve last-known balance. Emits
            ``BYBIT_DEMO_BALANCE_UNKNOWN_STATE``.

        Currently the only error code that triggers ``confirmed=False``
        is 10002 (TIMESTAMP_FAIL) after retries are exhausted.
        """
        try:
            envelope = await self._client.get(
                "/v5/account/wallet-balance",
                {"accountType": "UNIFIED"},
                op="balance",
            )
        except TradingMCPError as e:
            # Issue I1 (F-26): distinguish TIMESTAMP_FAIL from other
            # adapter errors. See get_positions_with_confirmation for
            # the matching pattern.
            _details = getattr(e, "details", None)
            _ret_code = (
                _details.get("ret_code") if isinstance(_details, dict) else None
            )
            if _ret_code == 10002:
                self._log.warning(
                    f"BYBIT_DEMO_BALANCE_UNKNOWN_STATE | "
                    f"reason=timestamp_fail err='{str(e)[:120]}' | {ctx()}"
                )
                return BalanceQueryResult(
                    confirmed=False,
                    account=_empty_account_info(),
                    reason="timestamp_fail",
                )
            # Other adapter errors — preserve the legacy
            # zero-sentinel behaviour but keep the BYBIT_DEMO_WALLET_FAIL
            # surface so operators see the cause.
            self._log.warning(
                f"BYBIT_DEMO_WALLET_FAIL | err={str(e)[:160]} | {ctx()}"
            )
            return BalanceQueryResult(
                confirmed=True, account=_empty_account_info(),
            )

        accounts = (envelope.get("result") or {}).get("list") or []
        if not accounts:
            return BalanceQueryResult(
                confirmed=True, account=_empty_account_info(),
            )

        # UNIFIED is a single account record; the aggregated totals live
        # at the account level (totalEquity etc.) NOT per-coin.
        return BalanceQueryResult(
            confirmed=True,
            account=_build_account_info_from_v5(accounts[0]),
        )

    async def get_available_balance(self) -> float:
        """Available margin in USD. Wrapper around get_wallet_balance."""
        info = await self.get_wallet_balance()
        return info.available_balance

    async def get_equity(self) -> float:
        """Total equity in USD. Wrapper around get_wallet_balance."""
        info = await self.get_wallet_balance()
        return info.total_equity

    async def get_margin_usage(self) -> dict[str, float]:
        """Margin usage breakdown — matches Shadow's return shape."""
        info = await self.get_wallet_balance()
        return {
            "used_margin": info.used_margin,
            "free_margin": info.available_balance,
            "margin_ratio_pct": info.margin_level_pct,
            "total_equity": info.total_equity,
            "unrealized_pnl": info.unrealized_pnl,
        }

    async def health_check(self) -> bool:
        """Probe Bybit demo via the underlying client."""
        return await self._client.health_check()


# =============================================================================
# Shared builder helpers — translate Bybit V5 JSON → project dataclasses
# =============================================================================


def _parse_side(side_str: str) -> Side:
    """Convert Bybit 'Buy'/'Sell' string to Side enum."""
    if side_str in ("Buy", "BUY", "buy", "Long", "long"):
        return Side.BUY
    return Side.SELL


# P3 of P1-P10: bounded retry on close-side fill resolution. The
# matching engine fills market IOC orders within milliseconds, but
# /v5/order/realtime can lag by 100-300ms before the fill is queryable.
# 4 attempts × 250ms = 1s ceiling — fast enough not to lengthen the
# close path noticeably, slow enough to capture the fill on >99% of
# closes.
_CLOSE_FILL_RETRY_ATTEMPTS = 4
_CLOSE_FILL_RETRY_INTERVAL_S = 0.25


async def _resolve_close_fill(
    *,
    client: BybitDemoClient,
    log: Any,
    symbol: str,
    order_id: str,
    requested_qty: float,
) -> tuple[float, float, OrderStatus]:
    """Poll /v5/order/realtime to resolve the actual fill price for a
    just-placed close order.

    Mirrors :meth:`BybitDemoOrderService._resolve_order_fill` but with
    bounded retry tuned for close-side latency. Returns
    ``(avg_price, cum_qty, status)``. On every-attempt failure returns
    ``(0.0, requested_qty, OrderStatus.FILLED)`` — the order DID place;
    the caller is responsible for falling back to a sentinel exit-price
    (typically the position's mark_price).

    Module-level so both BybitDemoPositionService.close_position and any
    future re-use site can share the same polling contract.
    """
    last_err: str | None = None
    for attempt in range(_CLOSE_FILL_RETRY_ATTEMPTS):
        try:
            envelope = await client.get(
                "/v5/order/realtime",
                {"category": _CATEGORY, "orderId": order_id, "symbol": symbol},
                op="resolve_close_fill",
            )
            orders = (envelope.get("result") or {}).get("list") or []
            if orders:
                o = orders[0]
                avg_price = _safe_float(o.get("avgPrice"))
                cum_qty = _safe_float(o.get("cumExecQty"), default=requested_qty)
                status_str = str(o.get("orderStatus", ""))
                if avg_price > 0:
                    if attempt > 0:
                        log.info(
                            f"BYBIT_DEMO_CLOSE_FILL_RETRY_OK | sym={symbol} "
                            f"oid={order_id[:12]} attempts={attempt + 1} "
                            f"avg_price={avg_price} | {ctx()}"
                        )
                    status = (
                        OrderStatus.FILLED if status_str in ("Filled", "PartiallyFilled")
                        else OrderStatus.REJECTED if status_str in ("Rejected", "Cancelled")
                        else OrderStatus.NEW
                    )
                    return avg_price, cum_qty, status
        except TradingMCPError as e:
            last_err = str(e)[:80]
        if attempt < _CLOSE_FILL_RETRY_ATTEMPTS - 1:
            await asyncio.sleep(_CLOSE_FILL_RETRY_INTERVAL_S)
    log.debug(
        f"BYBIT_DEMO_CLOSE_FILL_RETRY_EXHAUSTED | sym={symbol} "
        f"oid={order_id[:12]} attempts={_CLOSE_FILL_RETRY_ATTEMPTS} "
        f"last_err='{last_err or 'no_data'}' | {ctx()}"
    )
    return 0.0, requested_qty, OrderStatus.FILLED


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Coerce to float, falling back to ``default`` on None/invalid."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _optional_float(val: Any) -> float | None:
    """Coerce to float or None — matches Shadow's helper of the same name."""
    if val is None or val == "" or val == "0":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _rejected_order(
    symbol: str = "",
    side: Side | str = Side.BUY,
    reason: str = "",
) -> Order:
    """Sentinel REJECTED Order. Mirrors Shadow's ``_rejected_order``."""
    if isinstance(side, str):
        side = _parse_side(side)
    return Order(
        order_id="",
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        price=0.0,
        qty=0.0,
        status=OrderStatus.REJECTED,
    )


def _build_order_from_v5(data: dict[str, Any]) -> Order:
    """Convert a Bybit V5 order/realtime / order/history dict to ``Order``."""
    side_str = str(data.get("side", "Buy"))
    order_type_str = str(data.get("orderType", "Market"))
    status_str = str(data.get("orderStatus", ""))

    status = (
        OrderStatus.FILLED
        if status_str == "Filled"
        else OrderStatus.PARTIALLY_FILLED
        if status_str == "PartiallyFilled"
        else OrderStatus.REJECTED
        if status_str == "Rejected"
        else OrderStatus.CANCELLED
        if status_str == "Cancelled"
        else OrderStatus.NEW
    )

    return Order(
        order_id=str(data.get("orderId", "")),
        symbol=str(data.get("symbol", "")),
        side=_parse_side(side_str),
        order_type=OrderType.MARKET if order_type_str == "Market" else OrderType.LIMIT,
        price=_safe_float(data.get("avgPrice") or data.get("price")),
        qty=_safe_float(data.get("qty")),
        status=status,
        filled_qty=_safe_float(data.get("cumExecQty")),
        avg_fill_price=_safe_float(data.get("avgPrice")),
        stop_loss=_optional_float(data.get("stopLoss")),
        take_profit=_optional_float(data.get("takeProfit")),
    )


def _build_position_from_v5(data: dict[str, Any]) -> Position:
    """Convert a Bybit V5 /v5/position/list entry to ``Position``.

    Field mapping (Bybit V5 → project Position):
      data["symbol"]               → symbol
      data["side"]                 → side
      data["size"]                 → size
      data["avgPrice"]             → entry_price
      data["markPrice"]            → mark_price
      data["unrealisedPnl"]        → unrealized_pnl
      data["leverage"]             → leverage
      data["liqPrice"]             → liquidation_price
      data["stopLoss"]             → stop_loss
      data["takeProfit"]           → take_profit
    """
    return Position(
        symbol=str(data.get("symbol", "")),
        side=_parse_side(str(data.get("side", "Buy"))),
        size=_safe_float(data.get("size")),
        entry_price=_safe_float(data.get("avgPrice")),
        mark_price=_safe_float(data.get("markPrice"), default=_safe_float(data.get("avgPrice"))),
        unrealized_pnl=_safe_float(data.get("unrealisedPnl")),
        realized_pnl=0.0,
        leverage=int(_safe_float(data.get("leverage"), default=1.0)),
        liquidation_price=_safe_float(data.get("liqPrice")),
        stop_loss=_optional_float(data.get("stopLoss")),
        take_profit=_optional_float(data.get("takeProfit")),
    )


def _build_close_order(
    symbol: str,
    side: Side,
    qty: float,
    exit_price: float,
    order_id: str = "",
) -> Order:
    """Construct a FILLED close-order Order. Used by close_position / reduce_position.

    T3-2 fix (six-tier-fixes 2026-05-11) — Phase5 F-8 audit-row loss.
    Previously hardcoded ``order_id=""`` which caused every close-side
    ``save_order`` UPSERT to clobber the prior blank-PK row in the
    ``orders`` table (DB held 1 row vs 24 events emitted today). The
    function now accepts an ``order_id`` parameter; when the caller has
    a resolved Bybit ``orderId`` it is passed through, otherwise a
    deterministic synthetic ``bd-close-{symbol}-{epoch_ms}`` fallback
    is used so each close-order row has a unique PK.
    """
    if not order_id:
        order_id = f"bd-close-{symbol}-{int(time.time() * 1000)}"
    return Order(
        order_id=order_id,
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        price=exit_price,
        qty=qty,
        status=OrderStatus.FILLED,
        filled_qty=qty,
        avg_fill_price=exit_price,
    )


def _build_account_info_from_v5(data: dict[str, Any]) -> AccountInfo:
    """Convert a Bybit V5 /v5/account/wallet-balance entry to ``AccountInfo``.

    Bybit returns a list of accounts; the caller picks UNIFIED. Inside
    each account, ``coin`` is a list per-asset. Aggregated totals are
    available at the account level.

    Field mapping (Bybit V5 → project AccountInfo):
      data["totalEquity"]        → total_equity
      data["totalAvailableBalance"] → available_balance
      data["totalInitialMargin"] → used_margin
      data["totalPerpUPL"]       → unrealized_pnl
      (margin_level_pct stays 0 — Bybit doesn't expose a single ratio)
    """
    # Equity-phantom fix (2026-05-26): base risk on the USDT SETTLEMENT
    # COIN, not the unified all-coin account totals. The account-level
    # totalEquity sums EVERY demo coin in USD (~$175k on this demo
    # account), but only the USDT wallet (~$47.6k) actually settles these
    # USDT-perp trades. Reading the unified total inflated equity ~3.7x,
    # so the daily-loss halt % (which uses total_equity via
    # pnl_manager.starting_equity) never fired on real drawdown, and
    # sizing ran off an inflated available. We read the USDT coin's own
    # balance instead, and fall back to the unified totals (legacy
    # behaviour) only if the per-coin list is missing or has no USDT entry.
    _log = get_logger("bybit_demo")
    _uni_equity = _safe_float(data.get("totalEquity"))
    _uni_avail = _safe_float(data.get("totalAvailableBalance"))
    _usdt = None
    for _c in (data.get("coin") or []):
        if isinstance(_c, dict) and str(_c.get("coin", "")).upper() == "USDT":
            _usdt = _c
            break

    if _usdt is not None:
        _eq = _safe_float(_usdt.get("equity"))
        _wallet = _safe_float(_usdt.get("walletBalance"))
        if _eq <= 0.0:
            # `equity` is blank on some UNIFIED responses; walletBalance
            # (the realized USDT) is the floor so we never report zero
            # equity when the coin clearly carries a balance.
            _eq = _wallet
        _avail = _safe_float(_usdt.get("availableToWithdraw"))
        if _avail <= 0.0:
            # availableToWithdraw is blank on some UNIFIED responses; the
            # realized USDT wallet is the conservative available basis.
            _avail = _wallet
        _used = (
            _safe_float(_usdt.get("totalPositionIM"))
            + _safe_float(_usdt.get("totalOrderIM"))
        )
        if _used <= 0.0:
            _used = _safe_float(data.get("totalInitialMargin"))
        # Use the USDT basis only when it actually carries a balance. An
        # all-zero USDT coin means a parse problem — fall back rather than
        # zero out equity (which would break sizing and the halt).
        if _eq > 0.0 or _wallet > 0.0:
            _log.info(
                f"BYBIT_DEMO_EQUITY_BASIS | basis=usdt_coin "
                f"usdt_equity={_eq:.2f} usdt_avail={_avail:.2f} "
                f"unified_totalEquity={_uni_equity:.2f} "
                f"non_usdt_gap={_uni_equity - _eq:.2f} | {ctx()}"
            )
            return AccountInfo(
                total_equity=_eq,
                available_balance=_avail,
                used_margin=_used,
                unrealized_pnl=_safe_float(_usdt.get("unrealisedPnl")),
                margin_level_pct=0.0,
            )

    # Fallback: per-coin USDT not found or empty -> legacy unified totals.
    _log.warning(
        f"BYBIT_DEMO_EQUITY_BASIS | basis=unified_fallback "
        f"reason=no_usdt_coin unified_totalEquity={_uni_equity:.2f} "
        f"coins={len(data.get('coin') or [])} | {ctx()}"
    )
    return AccountInfo(
        total_equity=_uni_equity,
        available_balance=_uni_avail,
        used_margin=_safe_float(data.get("totalInitialMargin")),
        unrealized_pnl=_safe_float(data.get("totalPerpUPL")),
        margin_level_pct=0.0,
    )


def _empty_account_info() -> AccountInfo:
    """Zero-valued AccountInfo for error fallback. Mirrors Shadow."""
    return AccountInfo(
        total_equity=0.0,
        available_balance=0.0,
        used_margin=0.0,
        unrealized_pnl=0.0,
    )
