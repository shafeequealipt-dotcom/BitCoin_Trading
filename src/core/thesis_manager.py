"""Trade Thesis Manager — saves, retrieves, and closes trade theses.

Every Claude trade gets a thesis record (the "Data A" system).
Every cycle, Claude reads its own theses and cross-checks with reality.
When trades close, results and lessons are recorded.
"""

import asyncio

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("thesis_manager")


def compose_lesson_from_tias(
    analysis: dict,
    close_reason: str,
    hold_seconds: float,
    pnl_pct: float,
    max_chars: int = 180,
) -> str | None:
    """Compose a concise lesson string from a TIAS DeepSeek analysis dict.

    T1-3 / F9 bridge helper (six-tier-fixes 2026-05-11). The TIAS
    analyzer writes rich post-trade analysis to the `ds_*` columns on
    the `trade_intelligence` table. This function distils the most
    actionable fields (`ds_what_should_done`, `ds_how_to_exploit`,
    `ds_category`) into a single line for `trade_thesis.lesson`, which
    the strategist injects into CALL_A's "LESSONS FROM RECENT TRADES"
    block (with age + symbol-scope guards).

    Returns None when the analysis lacks usable content so the bridge
    can skip the UPDATE rather than overwriting with an empty string.

    Args:
        analysis: Dict returned by `TradeAnalyzer.analyze`. Reads
            `ds_what_should_done`, `ds_how_to_exploit`, `ds_category`.
        close_reason: Close trigger (e.g. "trailing_stop",
            "time_decay_force_close").
        hold_seconds: Trade duration in seconds.
        pnl_pct: Realised PnL percent.
        max_chars: Truncate to this length to keep prompt budget
            bounded.

    Returns:
        Lesson string, or None when no usable analysis is available.
    """
    ds_what = (analysis.get("ds_what_should_done") or "").strip()
    ds_how = (analysis.get("ds_how_to_exploit") or "").strip()
    category = (analysis.get("ds_category") or "").strip()
    body = ds_what or ds_how
    if not body:
        return None
    hold_min = max(0.0, hold_seconds / 60.0)
    prefix = (
        f"{hold_min:.0f}m hold {close_reason} pnl={pnl_pct:+.2f}%"
    )
    if category:
        prefix = f"{prefix} cat={category}"
    composed = f"{prefix}. {body}"
    if len(composed) > max_chars:
        composed = composed[: max_chars - 1].rstrip() + "…"
    return composed


def format_aggregated_stats_for_prompt(stats: dict) -> str:
    """Render the dict from ``ThesisManager.get_aggregated_stats`` for prompts.

    T1-3 / F9 fix (six-tier-fixes 2026-05-11). Produces a 3-5 line
    aggregated block injected into BOTH CALL_A and CALL_B. The block
    is closed-loop-immune by construction: it names no specific
    symbols or trades, only aggregate distributions.

    Returns an empty string when there is no data to render so the
    caller can skip appending an empty section.
    """
    count = stats.get("count", 0)
    if not count:
        return ""
    wr = stats.get("wr_pct", 0.0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    net = stats.get("net_pnl_usd", 0.0)
    lines = [
        f"\n## RECENT PERFORMANCE (last {count} closes — directional pattern only)",
        f"WR: {wr:.0f}% ({wins}W / {losses}L)  |  Net PnL: ${net:+.2f}",
    ]
    by_reason = stats.get("by_reason") or {}
    if by_reason:
        parts: list[str] = []
        for rsn, d in sorted(
            by_reason.items(),
            key=lambda kv: -kv[1].get("count", 0),
        )[:5]:
            cnt = d.get("count", 0)
            w = d.get("wins", 0)
            rsn_wr = (w / cnt * 100.0) if cnt else 0.0
            parts.append(f"{rsn} {cnt} (W {rsn_wr:.0f}%)")
        lines.append("By close reason: " + " | ".join(parts))
    return "\n".join(lines)


class ThesisManager:
    """Manages trade thesis lifecycle: create -> track -> close -> learn."""

    def __init__(self, db):
        self.db = db

    async def save_thesis(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        size_usd: float,
        leverage: int,
        max_hold_minutes: int,
        trailing_activation_pct: float,
        thesis: str,
        market_context: str = "",
        strategy_hints: str = "",
        consensus: str = "",
        order_id: str = "",
        exchange_mode: str = "shadow",
        apex_flipped: bool = False,
        apex_original_direction: str = "",
        apex_reason: str = "",
        # Time-Decay Force-Close Definitive Fix Phase 3 (2026-05-06) —
        # entry-time XRAY/regime anchors persisted on `trade_thesis` v27
        # for restart-resilient structural-invalidation detection. The
        # in-memory TradeCoordinator.TradeState carries the same data;
        # the watchdog reads from there first and only falls back to a
        # one-shot `SELECT ... FROM trade_thesis WHERE symbol=? AND
        # status='open'` for in-flight positions whose state was lost
        # across a watchdog restart. Defaults are neutral (0.0/'') so
        # legacy callers and rows pre-dating v27 are still well-formed.
        entry_xray_confidence: float = 0.0,
        entry_setup_type: str = "",
        entry_regime_at_open: str = "",
        entry_regime_confidence: float = 0.0,
        # CALL_B Framing Fix Phase 1E (2026-05-06) — XRAY/APEX flip
        # metadata persisted on `trade_thesis` v28 so CALL_B can render
        # concrete RR justification in the FLIPPED notice. Closes the
        # gap where _flip_source/_xray_flip_ratio lived only in the
        # in-memory trade dict and were lost when a position outlived
        # the worker tick. Defaults are neutral ('' / 0.0) so legacy
        # callers and rows pre-dating v28 are still well-formed.
        xray_flip_source: str = "",
        xray_flip_ratio: float = 0.0,
        xray_flip_rr_long: float = 0.0,
        xray_flip_rr_short: float = 0.0,
        # Mid-Hold Trade Management Fix Phase 3.1 (2026-05-19, schema v34) —
        # entry-thesis invalidation contract persisted at trade open.
        #
        #   thesis_invalidation: JSON-encoded {type, value} pair. type is
        #     one of 'price_close_above', 'price_close_below', 'signal',
        #     'none'. When the brain provides a parseable criterion in
        #     its CALL_A response, this is the parsed dict (caller
        #     json.dumps before passing). Empty string '' is the
        #     well-formed neutral default for legacy callers; the column
        #     defaults to '' at the DB layer so legacy INSERTs without
        #     the field still succeed via the default.
        #   thesis_source: 'brain_stated' when brain provided a parseable
        #     thesis_invalidation; 'heuristic_fallback' when brain
        #     omitted/returned-invalid and the snapshot below carries
        #     the entry-time anchor for watchdog monitoring.
        #   thesis_snapshot: JSON-encoded compact snapshot of the
        #     nearest aligned OB or FVG at entry (operator decision:
        #     nearest aligned level only). Watchdog reads this in the
        #     heuristic_fallback path to monitor close-beyond
        #     invalidation. Default '{}' means no anchor captured.
        #
        # The thesis_state column is initialised to 'VALID' by the
        # column default — no caller needs to pass it at save time.
        # State transitions during hold are written by
        # `record_thesis_state` from the watchdog path.
        thesis_invalidation: str = "",
        thesis_source: str = "brain_stated",
        thesis_snapshot: str = "{}",
        # Durable-open (2026-06-17): the trade-open path reserves a row with
        # status='reserving' BEFORE placing the order, then finalize_thesis
        # flips it to 'open' once the order is live. 'reserving' rows are
        # deliberately invisible to every status='open' consumer (the brain's
        # get_open_theses, the zombie reconciler, and recover_state_from_db),
        # so a reserved-but-not-yet-live thesis can never be mistaken for a
        # live position. Default 'open' preserves all legacy callers.
        status: str = "open",
    ) -> int:
        """Save a new trade thesis when Claude opens a trade.

        Returns the thesis ID.
        """
        # Durability (2026-06-17): the trade-open path now saves the thesis
        # BEFORE placing the exchange order and aborts the open if this returns
        # <= 0 (see strategy_worker._execute_claude_trade). So a transient DB
        # failure here must NOT silently lose the record. Retry a few times on
        # transient errors; only return -1 (caller aborts the open, so no
        # orphan) after the retries are exhausted, logging CRITICAL so the
        # failure is loud.
        _attempts = 3
        cursor = None
        for _attempt in range(1, _attempts + 1):
            try:
                cursor = await self.db.execute(
                    """INSERT INTO trade_thesis
                       (symbol, direction, entry_price, stop_loss_price, take_profit_price,
                        size_usd, leverage, max_hold_minutes, trailing_activation_pct,
                        thesis, market_context, strategy_hints, consensus,
                        status, order_id, exchange_mode,
                        apex_flipped, apex_original_direction, apex_reason,
                        entry_xray_confidence, entry_setup_type,
                        entry_regime_at_open, entry_regime_confidence,
                        xray_flip_source, xray_flip_ratio,
                        xray_flip_rr_long, xray_flip_rr_short,
                        thesis_invalidation, thesis_source, thesis_snapshot)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?)""",
                    (symbol, direction, entry_price, stop_loss_price, take_profit_price,
                     size_usd, leverage, max_hold_minutes, trailing_activation_pct,
                     thesis, market_context, strategy_hints, consensus,
                     (status if status in ("open", "reserving") else "open"),
                     order_id,
                     exchange_mode,
                     1 if apex_flipped else 0, apex_original_direction, apex_reason,
                     float(entry_xray_confidence), str(entry_setup_type or ""),
                     str(entry_regime_at_open or ""), float(entry_regime_confidence),
                     str(xray_flip_source or ""), float(xray_flip_ratio),
                     float(xray_flip_rr_long), float(xray_flip_rr_short),
                     str(thesis_invalidation or ""), str(thesis_source or "brain_stated"),
                     str(thesis_snapshot or "{}")),
                )
                break
            except Exception as e:
                if _attempt < _attempts:
                    log.warning(
                        f"THESIS_SAVE_RETRY | sym={symbol} attempt={_attempt}/{_attempts} "
                        f"err='{str(e)[:80]}' | {ctx()}"
                    )
                    await asyncio.sleep(0.15 * _attempt)
                    continue
                log.critical(
                    f"THESIS_SAVE_FAILED | sym={symbol} attempts={_attempts} "
                    f"err='{str(e)[:120]}' | order will NOT be placed (caller aborts) | {ctx()}"
                )
                return -1
        # The INSERT succeeded (DatabaseManager auto-commits). Capture the id
        # OUTSIDE the logging try so a post-insert logging error can never make
        # us return -1 for a row that is actually saved (which would abort the
        # open and leave a thesis with no position — an inverse orphan).
        thesis_id = cursor.lastrowid if hasattr(cursor, "lastrowid") else -1
        try:
            # Observability G8 (field completeness) — Phase 0 baseline
            # confirmed THESIS_OPEN is the canonical save-side event
            # (matches THESIS_OPEN/THESIS_CLOSE lifecycle pattern; the
            # audit's THESIS_SAVE tag does not exist anywhere in src/).
            # The remaining audit fields (target_pct, stop_pct,
            # expected_hold_min) are derivable from existing parameters
            # and useful for grep-by-magnitude inspection.
            _entry_for_pct = entry_price if entry_price else 1e-9
            _target_pct = abs(take_profit_price - entry_price) / _entry_for_pct * 100.0
            _stop_pct = abs(entry_price - stop_loss_price) / _entry_for_pct * 100.0
            # Durable-open: a 'reserving' row is a pre-order reservation, not a
            # live open — log it as THESIS_RESERVED so operators don't read it
            # as an opened position (finalize_thesis later emits THESIS_FINALIZED).
            _evt = "THESIS_RESERVED" if status == "reserving" else "THESIS_OPEN"
            log.info(
                f"{_evt} | id={thesis_id} sym={symbol} dir={direction} "
                f"ent={entry_price} sl={stop_loss_price} tp={take_profit_price} "
                f"target_pct={_target_pct:.3f} stop_pct={_stop_pct:.3f} "
                f"lev={leverage} size_usd={size_usd} "
                f"max_hold_min={max_hold_minutes} order_id={order_id or '-'} | {ctx()}"
            )
            # Phase 1E observability — fire only when flip metadata is
            # non-empty so non-flipped trades stay quiet. Operators tail
            # this to confirm v28 columns are populated end-to-end.
            if xray_flip_source:
                log.info(
                    f"THESIS_FLIP_PERSISTED | tid={thesis_id} sym={symbol} "
                    f"source={xray_flip_source} ratio={xray_flip_ratio:.2f} "
                    f"rr_long={xray_flip_rr_long:.2f} rr_short={xray_flip_rr_short:.2f} "
                    f"orig_dir={apex_original_direction or '?'} flipped_to={direction} "
                    f"| {ctx()}"
                )
            # Mid-Hold Trade Management Fix Phase 3.1 observability —
            # fire whenever the new invalidation contract is populated
            # (either brain_stated with a criterion, or
            # heuristic_fallback with a snapshot, or both empty in the
            # rare legacy path). Operators tail this to confirm the
            # contract is reaching the DB end-to-end and to track
            # brain-stated vs heuristic_fallback compliance over time.
            _inv_present = bool(thesis_invalidation)
            _snap_present = thesis_snapshot not in ("", "{}", "{ }")
            log.info(
                f"THESIS_PERSISTENCE_RECORDED | tid={thesis_id} sym={symbol} "
                f"source={thesis_source} criterion_present={int(_inv_present)} "
                f"snapshot_present={int(_snap_present)} "
                f"criterion_chars={len(thesis_invalidation or '')} "
                f"snapshot_chars={len(thesis_snapshot or '')} | {ctx()}"
            )
            log.info(
                "Thesis saved: #{tid} {dir} {sym} — {thesis}",
                tid=thesis_id, dir=direction, sym=symbol, thesis=thesis[:60],
            )
            return thesis_id
        except Exception as e:
            # Row is already saved; only the observability logging failed.
            log.error("Thesis saved (id={tid}) but post-save logging failed for {sym}: {err}",
                      tid=thesis_id, sym=symbol, err=str(e))
            return thesis_id

    async def finalize_thesis(
        self,
        thesis_id: int,
        order_id: str,
        *,
        entry_price: float | None = None,
        thesis: str = "",
        market_context: str = "",
        apex_flipped: bool = False,
        apex_original_direction: str = "",
        apex_reason: str = "",
        entry_xray_confidence: float = 0.0,
        entry_setup_type: str = "",
        entry_regime_at_open: str = "",
        entry_regime_confidence: float = 0.0,
        xray_flip_source: str = "",
        xray_flip_ratio: float = 0.0,
        xray_flip_rr_long: float = 0.0,
        xray_flip_rr_short: float = 0.0,
        thesis_invalidation: str = "",
        thesis_source: str = "brain_stated",
        thesis_snapshot: str = "{}",
    ) -> bool:
        """Promote a reserved (pre-order) thesis to live once the order fills.

        Durable-open Phase (2026-06-17): the trade-open path reserves a row with
        status='reserving' via ``save_thesis(status="reserving", order_id="")``
        BEFORE placing the exchange order, so a trade can never be live without
        a local record. This call flips that row to status='open' AND stamps the
        real ``order_id`` in a single atomic UPDATE — completing the
        (symbol, order_id) close-match circuit — and writes the rich entry
        context known only post-order. Scoped to the reserved/open row by id.

        The order_id linkage is what the close path matches on, so the UPDATE is
        retried on transient DB errors. If it ultimately fails the row stays
        'reserving' (NOT 'open'), so it stays invisible to the brain and the
        zombie reconciler and is resolved by ``sweep_reserving_theses`` (adopted
        if a live position exists, else voided) — no silent zero-PnL orphan.
        """
        if thesis_id is None or thesis_id <= 0:
            return False
        # status='open' FIRST so the flip reserving->open is part of the same
        # atomic UPDATE as the order_id stamp.
        _sets = [
            "status = 'open'",
            "order_id = ?", "thesis = ?", "market_context = ?",
            "apex_flipped = ?", "apex_original_direction = ?", "apex_reason = ?",
            "entry_xray_confidence = ?", "entry_setup_type = ?",
            "entry_regime_at_open = ?", "entry_regime_confidence = ?",
            "xray_flip_source = ?", "xray_flip_ratio = ?",
            "xray_flip_rr_long = ?", "xray_flip_rr_short = ?",
            "thesis_invalidation = ?", "thesis_source = ?", "thesis_snapshot = ?",
        ]
        _params: list = [
            str(order_id or ""), thesis, market_context,
            1 if apex_flipped else 0, apex_original_direction, apex_reason,
            float(entry_xray_confidence), str(entry_setup_type or ""),
            str(entry_regime_at_open or ""), float(entry_regime_confidence),
            str(xray_flip_source or ""), float(xray_flip_ratio),
            float(xray_flip_rr_long), float(xray_flip_rr_short),
            str(thesis_invalidation or ""), str(thesis_source or "brain_stated"),
            str(thesis_snapshot or "{}"),
        ]
        if entry_price is not None:
            _sets.append("entry_price = ?")
            _params.append(float(entry_price))
        _params.append(int(thesis_id))
        _sql = (
            f"UPDATE trade_thesis SET {', '.join(_sets)} "
            f"WHERE id = ? AND status IN ('reserving', 'open')"
        )
        _attempts = 3
        for _attempt in range(1, _attempts + 1):
            try:
                await self.db.execute(_sql, tuple(_params))
                log.info(
                    f"THESIS_FINALIZED | tid={thesis_id} order_id={order_id or '-'} "
                    f"src={thesis_source} | {ctx()}"
                )
                return True
            except Exception as e:
                if _attempt < _attempts:
                    log.warning(
                        f"THESIS_FINALIZE_RETRY | tid={thesis_id} "
                        f"attempt={_attempt}/{_attempts} err='{str(e)[:80]}' | {ctx()}"
                    )
                    await asyncio.sleep(0.15 * _attempt)
                    continue
                log.critical(
                    f"THESIS_FINALIZE_FAIL | tid={thesis_id} order_id={order_id or '-'} "
                    f"attempts={_attempts} err='{str(e)[:120]}' | row stays 'reserving', "
                    f"sweep_reserving_theses will adopt/void it | {ctx()}"
                )
                return False
        return False

    async def void_thesis(self, thesis_id: int, reason: str) -> bool:
        """Void a reserved thesis when its order never went live.

        Durable-open Phase (2026-06-17): if ``save_thesis`` reserved a row
        (status='reserving') but the subsequent exchange order was rejected or
        raised, mark the reservation 'voided' so it is never treated as an open
        position (no inverse orphan). Scoped to the reserved/open row by id.
        """
        if thesis_id is None or thesis_id <= 0:
            return False
        try:
            await self.db.execute(
                "UPDATE trade_thesis SET status = 'voided', "
                "close_reason = ?, closed_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status IN ('reserving', 'open')",
                (str(reason or "voided"), int(thesis_id)),
            )
            log.info(f"THESIS_VOIDED | tid={thesis_id} reason={reason} | {ctx()}")
            return True
        except Exception as e:
            log.error(f"THESIS_VOID_FAIL | tid={thesis_id} err='{str(e)[:120]}' | {ctx()}")
            return False

    # Adoption entry-price tolerance — mirrors _resolve_zombie_close's 0.5%
    # guard (the documented SOLUSDT mis-attribution lesson). A 'reserving' row
    # is adopted onto a live position ONLY when their entry prices match within
    # this band, so a stale reservation can never be attached to an unrelated
    # same-symbol position.
    _RESERVE_ADOPT_ENTRY_TOL = 0.005

    async def sweep_reserving_theses(self, live_positions) -> dict:
        """Resolve leftover status='reserving' rows (boot + periodic sweep).

        Durable-open Phase (2026-06-17): a 'reserving' row should be transient —
        it is flipped to 'open' (finalize_thesis) or 'voided' (void_thesis)
        within seconds of being created. A row still 'reserving' when this runs
        means the open path was interrupted between the reserve and the resolve
        (process crash, finalize_thesis hard-failure, or a place_order that
        neither returned REJECTED nor was caught). This sweep closes that window:

          * a live position EXISTS whose entry price matches the reserved row's
            within ``_RESERVE_ADOPT_ENTRY_TOL`` -> ADOPT: flip 'reserving' ->
            'open' so the watchdog manages it and the eventual close books PnL.
            (Recovers OUR OWN reserved trade; not the auto-adoption of arbitrary
            exchange orphans the project keeps operator-supervised.)
          * otherwise -> VOID: the order never went live, it already closed while
            we were down, or the only same-symbol position is an unrelated trade
            (the entry-price guard prevents mis-attaching to it). Any realized
            PnL of a closed orphan is healed by the operator one-shot script.

        ``live_positions`` is an iterable of CONFIRMED live position objects
        (each exposing ``.symbol`` and ``.entry_price``) or dicts with those
        keys, supplied by the caller (manager boot / position_watchdog tick).
        Callers MUST pass a ground-truth-confirmed snapshot — an unconfirmed or
        error-swallowed empty list would mass-void live reservations.
        """
        # symbol -> [entry_price, ...] for the confirmed live positions
        live: dict[str, list[float]] = {}
        for p in (live_positions or []):
            try:
                sym = getattr(p, "symbol", None)
                ep = getattr(p, "entry_price", None)
                if sym is None and isinstance(p, dict):
                    sym = p.get("symbol")
                    ep = p.get("entry_price")
                if sym:
                    live.setdefault(str(sym), []).append(float(ep or 0.0))
            except Exception:
                continue
        adopted = 0
        voided = 0
        try:
            rows = await self.db.fetch_all(
                "SELECT id, symbol, entry_price FROM trade_thesis "
                "WHERE status = 'reserving'"
            )
        except Exception as e:
            log.error(f"RESERVING_SWEEP_FAIL | err='{str(e)[:120]}' | {ctx()}")
            return {"adopted": 0, "voided": 0, "scanned": 0}
        for row in (rows or []):
            try:
                tid = row["id"]
                sym = row["symbol"]
                row_entry = float(row["entry_price"] or 0.0)
            except Exception:
                continue
            # Adopt ONLY when a live position's entry price matches this row's —
            # never on symbol coincidence alone.
            matched = any(
                ep_live > 0 and row_entry > 0
                and abs(row_entry - ep_live) / ep_live <= self._RESERVE_ADOPT_ENTRY_TOL
                for ep_live in live.get(sym, [])
            )
            if matched:
                try:
                    await self.db.execute(
                        "UPDATE trade_thesis SET status = 'open' "
                        "WHERE id = ? AND status = 'reserving'",
                        (int(tid),),
                    )
                    adopted += 1
                    log.warning(
                        f"RESERVING_ADOPTED | tid={tid} sym={sym} entry={row_entry} | "
                        f"entry-matched live position, promoted reserving->open | {ctx()}"
                    )
                except Exception as e:
                    log.error(f"RESERVING_ADOPT_FAIL | tid={tid} sym={sym} err='{str(e)[:80]}' | {ctx()}")
            else:
                try:
                    await self.db.execute(
                        "UPDATE trade_thesis SET status = 'voided', "
                        "close_reason = 'reserve_swept_no_match', "
                        "closed_at = CURRENT_TIMESTAMP "
                        "WHERE id = ? AND status = 'reserving'",
                        (int(tid),),
                    )
                    voided += 1
                    log.warning(
                        f"RESERVING_VOIDED | tid={tid} sym={sym} entry={row_entry} | "
                        f"no entry-matched live position, voided stale reservation | {ctx()}"
                    )
                except Exception as e:
                    log.error(f"RESERVING_VOID_FAIL | tid={tid} sym={sym} err='{str(e)[:80]}' | {ctx()}")
        if rows:
            log.info(
                f"RESERVING_SWEEP_DONE | scanned={len(rows)} adopted={adopted} "
                f"voided={voided} | {ctx()}"
            )
        return {"adopted": adopted, "voided": voided, "scanned": len(rows)}

    async def get_open_theses(self) -> list[dict]:
        """Get all open theses — fed to Claude every cycle.

        P4 of P1-P10: filter by current_mode so the brain doesn't
        attempt to manage theses opened in a different mode (e.g.,
        shadow theses surfacing during a bybit_demo session). Falls
        back to unfiltered query when transformer not yet wired.
        """
        # P4: late-bound transformer reference. attach_transformer()
        # is called by WorkerManager after construction.
        _xfm = getattr(self, "_transformer", None)
        _mode = None
        if _xfm is not None:
            try:
                _mode = str(_xfm.current_mode) if _xfm.current_mode else None
            except Exception:
                _mode = None
        try:
            if _mode:
                rows = await self.db.fetch_all(
                    """SELECT id, symbol, direction, entry_price, stop_loss_price,
                              take_profit_price, size_usd, leverage, max_hold_minutes,
                              trailing_activation_pct, thesis, market_context,
                              strategy_hints, consensus, opened_at, exchange_mode,
                              apex_flipped, apex_original_direction, apex_reason,
                              xray_flip_source, xray_flip_ratio,
                              xray_flip_rr_long, xray_flip_rr_short,
                              thesis_invalidation, thesis_source,
                              thesis_snapshot, thesis_state,
                              order_id
                       FROM trade_thesis
                       WHERE status = 'open' AND exchange_mode = ?
                       ORDER BY opened_at DESC""",
                    (_mode,),
                )
            else:
                rows = await self.db.fetch_all(
                    """SELECT id, symbol, direction, entry_price, stop_loss_price,
                              take_profit_price, size_usd, leverage, max_hold_minutes,
                              trailing_activation_pct, thesis, market_context,
                              strategy_hints, consensus, opened_at, exchange_mode,
                              apex_flipped, apex_original_direction, apex_reason,
                              xray_flip_source, xray_flip_ratio,
                              xray_flip_rr_long, xray_flip_rr_short,
                              thesis_invalidation, thesis_source,
                              thesis_snapshot, thesis_state,
                              order_id
                       FROM trade_thesis
                       WHERE status = 'open'
                       ORDER BY opened_at DESC"""
                )
            return [dict(row) for row in rows] if rows else []
        except Exception as e:
            log.error("Failed to get open theses: {err}", err=str(e))
            return []

    def attach_transformer(self, transformer) -> None:
        """Wire the Transformer reference for mode-aware SQL filtering.

        P4 of P1-P10. Late-bound to avoid circular DI between
        ThesisManager (constructed early) and Transformer.
        """
        self._transformer = transformer

    def attach_position_service(self, position_service) -> None:
        """Wire the position service for authoritative zombie-close recovery.

        Finding 8 (2026-06-02). Late-bound — the same pattern as
        attach_transformer — so the zombie reconciler can fetch the
        exchange's authoritative closedPnl for a position that was closed
        while this service was down, instead of booking the close at zero
        and losing the real PnL from the accounting. Accessed defensively
        via getattr so a ThesisManager that was never wired (e.g. in a
        unit test) degrades to the legacy zero-booking behaviour.
        """
        self._position_service = position_service

    async def _resolve_zombie_close(
        self, symbol: str, entry_price: float, order_id: str,
    ) -> tuple[float, float, float, str] | None:
        """Resolve a zombie position's TRUE close from the exchange.

        Finding 8 (2026-06-02). The zombie reconciler used to book every
        orphan thesis at exactly zero PnL, which silently lost the real
        profit or loss of any position that closed while the service was
        down. This recovers that truth: it asks the exchange for the
        position's authoritative closed outcome (Bybit's
        ``/v5/position/closed-pnl`` via ``position_service.get_last_close``,
        the same call the watchdog uses) and returns its real PnL.

        The single critical correctness guard is the ENTRY-PRICE match.
        ``get_last_close`` returns only the most-recent close for the
        symbol, and the orphan set legitimately contains long-stale theses
        (for example two SOLUSDT rows opened weeks before the outage that
        were never closed in the DB). Booking the latest SOL close onto a
        month-old unrelated SOL thesis would be a mis-booking. So the
        authoritative value is accepted ONLY when the exchange close's
        entry price matches this orphan's entry price within a tight
        tolerance; otherwise we return None and the caller books zero. The
        watchdog's 120-second freshness gate is deliberately NOT applied
        here — an outage close can be many hours old, and the entry-price
        match (not recency) is what proves the record belongs to this row.

        Returns ``(pnl_pct, pnl_usd, exit_price, "exchange_authoritative")``
        on a confident match, or ``None`` when the truth cannot be
        recovered (no service wired, no exchange record, or entry mismatch).
        """
        svc = getattr(self, "_position_service", None)
        if svc is None or not hasattr(svc, "get_last_close"):
            return None
        if not entry_price or entry_price <= 0:
            return None
        try:
            rec = await svc.get_last_close(symbol)
        except Exception as e:
            log.warning(
                f"ZOMBIE_LAST_CLOSE_FAIL | sym={symbol} order_id={order_id or '-'} "
                f"err='{str(e)[:100]}' | {ctx()}"
            )
            return None
        if not rec:
            return None
        try:
            rec_entry = float(rec.get("entry_price") or 0.0)
            rec_pnl_usd = float(rec.get("net_pnl_usd") or 0.0)
            rec_pnl_pct = float(rec.get("net_pnl_pct") or 0.0)
            rec_exit = float(rec.get("exit_price") or 0.0)
        except (TypeError, ValueError):
            return None
        if rec_entry <= 0:
            return None
        # Entry-price match: the record belongs to THIS orphan only when its
        # entry price is within 0.5% of the orphan's recorded entry. This is
        # what keeps a recent close from being booked onto a stale same-symbol
        # thesis (the SOLUSDT-weeks-old case).
        if abs(rec_entry - entry_price) / entry_price > 0.005:
            log.info(
                f"ZOMBIE_LAST_CLOSE_ENTRY_MISMATCH | sym={symbol} "
                f"order_id={order_id or '-'} thesis_entry={entry_price} "
                f"exchange_entry={rec_entry} | not this row — booking zero | {ctx()}"
            )
            return None
        return rec_pnl_pct, rec_pnl_usd, rec_exit, "exchange_authoritative"

    async def close_thesis(
        self,
        symbol: str,
        close_price: float,
        actual_pnl_pct: float,
        actual_pnl_usd: float,
        close_reason: str,
        lesson: str = "",
        order_id: str = "",
    ) -> None:
        """Close open thesis rows for a symbol when a position closes.

        Definitive-fix Phase 8 (2026-04-28) — when ``order_id`` is
        non-empty, the WHERE clause is narrowed to
        ``symbol AND order_id`` so the close affects ONLY the matching
        thesis row. Forensic S5 captured a regression where closing a
        Buy ETH thesis (no order_id filter) silently closed a freshly-
        opened Sell ETH thesis at the same time.

        When ``order_id`` is empty (legacy callers), the implementation
        preserves the original "close all open theses for symbol"
        behaviour — guards against silent no-op closures during
        rollouts where a caller hasn't yet been updated to forward the
        id. Operators see the difference in the THESIS_CLOSE log via
        the new ``order_id=`` field.
        """
        # T6: Auto-set lesson for administrative switch closes
        if close_reason == "transformer_switch" and not lesson:
            lesson = (
                "Administrative close during exchange switch. "
                "Not a market-driven close. Do not learn from this trade."
            )
        # P5 of P1-P10: re-target the WHERE clause to also catch
        # zombie-reconciled rows (status='closed' AND pnl=0 AND
        # close_reason='zombie_reconciler'). Audit found 36 historical
        # rows where the zombie reconciler raced ahead of the
        # watchdog's authoritative close, leaving trade_thesis with
        # pnl=0 even though trade_log + trade_intelligence captured the
        # correct PnL. The zombie signature
        # (status=closed, pnl_usd=0, close_reason=zombie_reconciler)
        # is unique to thesis_manager.reconcile_with_shadow at
        # thesis_manager.py:239-314 — no other writer produces it —
        # so widening the UPDATE filter cannot affect any other path.
        try:
            if order_id:
                await self.db.execute(
                    """UPDATE trade_thesis
                       SET status = 'closed',
                           closed_at = CURRENT_TIMESTAMP,
                           close_price = ?,
                           actual_pnl_pct = ?,
                           actual_pnl_usd = ?,
                           close_reason = ?,
                           lesson = ?
                       WHERE symbol = ? AND order_id = ?
                         AND (status = 'open'
                              OR (status = 'closed'
                                  AND actual_pnl_usd = 0
                                  AND close_reason = 'zombie_reconciler'))""",
                    (close_price, actual_pnl_pct, actual_pnl_usd, close_reason,
                     lesson, symbol, order_id),
                )
            else:
                await self.db.execute(
                    """UPDATE trade_thesis
                       SET status = 'closed',
                           closed_at = CURRENT_TIMESTAMP,
                           close_price = ?,
                           actual_pnl_pct = ?,
                           actual_pnl_usd = ?,
                           close_reason = ?,
                           lesson = ?
                       WHERE symbol = ?
                         AND (status = 'open'
                              OR (status = 'closed'
                                  AND actual_pnl_usd = 0
                                  AND close_reason = 'zombie_reconciler'))""",
                    (close_price, actual_pnl_pct, actual_pnl_usd, close_reason,
                     lesson, symbol),
                )
            # DatabaseManager auto-commits
            log.info(
                f"THESIS_CLOSE | sym={symbol} order_id={order_id or '-'} "
                f"pnl={actual_pnl_pct:+.4f}% pnl$={actual_pnl_usd:+.4f} "
                f"rsn={close_reason} ext={close_price} "
                f"lesson='{lesson[:80]}' | {ctx()}"
            )
            log.info(
                "Thesis closed: {sym} PnL={pnl:+.2f}% reason={reason}",
                sym=symbol, pnl=actual_pnl_pct, reason=close_reason,
            )
        except Exception as e:
            log.error("Failed to close thesis for {sym}: {err}", sym=symbol, err=str(e))

    async def update_outcome_by_order_id(
        self,
        symbol: str,
        order_id: str,
        *,
        actual_pnl_usd: float,
        actual_pnl_pct: float,
        close_price: float = 0.0,
    ) -> None:
        """Reconcile-only: correct an ALREADY-CLOSED thesis row's booked PnL to
        the exchange-authoritative net (Phase 1D PnL-truth indexer-lag tail).

        ``close_thesis`` deliberately gates its UPDATE to ``status='open'`` (or a
        zero-pnl ``zombie_reconciler`` row) to guard the S5 cross-close
        regression, so it is a NO-OP on a normally-closed row and therefore
        cannot carry the reconciler's correction (the original Phase-1D wiring
        re-fired ``close_thesis`` on the reconcile channel, which silently did
        nothing for an already-closed non-zombie row — caught by the Pass-3
        runtime audit). This focused UPDATE targets the precise already-closed
        row by ``(symbol, order_id)`` and rewrites ONLY the outcome fields
        (pnl, close_price) — never the status or close_reason — so it can never
        reopen or cross-close anything. A non-empty ``order_id`` is required (the
        reconciler always carries it); a missing id is logged and skipped rather
        than risk an unscoped update. ``close_price`` is only overwritten when a
        corrected value (> 0) is supplied, otherwise the existing one is kept.
        """
        if not order_id:
            log.warning(
                f"THESIS_RECONCILE_SKIP | sym={symbol} reason=no_order_id "
                f"| cannot scope the reconcile UPDATE safely | {ctx()}"
            )
            return
        try:
            await self.db.execute(
                """UPDATE trade_thesis
                   SET actual_pnl_pct = ?,
                       actual_pnl_usd = ?,
                       close_price = CASE WHEN ? > 0 THEN ? ELSE close_price END
                   WHERE symbol = ? AND order_id = ? AND status = 'closed'""",
                (actual_pnl_pct, actual_pnl_usd, close_price, close_price,
                 symbol, order_id),
            )
            log.info(
                f"THESIS_RECONCILE | sym={symbol} order_id={order_id} "
                f"pnl={actual_pnl_pct:+.4f}% pnl$={actual_pnl_usd:+.4f} "
                f"ext={close_price} | trade_thesis corrected to "
                f"exchange-authoritative net | {ctx()}"
            )
        except Exception as e:
            log.error(
                f"THESIS_RECONCILE_FAIL | sym={symbol} order_id={order_id} "
                f"err='{str(e)[:150]}' | {ctx()}"
            )

    async def get_recent_lessons(
        self,
        limit: int = 10,
        min_age_seconds: int = 0,
        exclude_symbols: frozenset[str] | None = None,
    ) -> list[dict]:
        """Get recent closed trade results for Claude to learn from.

        Args:
            limit: Maximum lessons to return.
            min_age_seconds: T1-3 anti-closed-loop guard (six-tier-fixes
                2026-05-11). Only return lessons whose ``closed_at`` is
                older than this. Prevents the recency-bias failure mode
                where a 3-min-old loss-lesson drives the next cycle's
                close on a same-symbol position. Default 0 = no age gate
                (legacy callers).
            exclude_symbols: T1-3 same-symbol-scope anti-closed-loop guard.
                Lessons for symbols in this set are excluded. Pass the
                current open-position symbol set so the brain does not
                see a lesson for symbol X while deciding on symbol X.
                Default None = no exclusion (legacy callers).
        """
        try:
            # Builder discipline: where_clauses and params are appended
            # together in lockstep so the placeholder order in the SQL
            # always matches the params tuple order. The previous insert/
            # prepend approach mis-ordered parameters when BOTH
            # min_age_seconds and exclude_symbols were provided (caught in
            # the six-tier-fixes audit cross-check).
            where_clauses: list[str] = ["status = 'closed'"]
            where_params: list = []
            if min_age_seconds > 0:
                where_clauses.append(
                    "(strftime('%s', 'now') - strftime('%s', closed_at)) > ?"
                )
                where_params.append(min_age_seconds)
            if exclude_symbols:
                placeholders = ",".join(["?"] * len(exclude_symbols))
                where_clauses.append(f"symbol NOT IN ({placeholders})")
                where_params.extend(exclude_symbols)
            sql = (
                "SELECT symbol, direction, entry_price, close_price, "
                "actual_pnl_pct, actual_pnl_usd, close_reason, lesson, "
                "thesis, opened_at, closed_at "
                "FROM trade_thesis "
                f"WHERE {' AND '.join(where_clauses)} "
                "ORDER BY closed_at DESC LIMIT ?"
            )
            params = tuple([*where_params, limit])
            rows = await self.db.fetch_all(sql, params)
            return [dict(row) for row in rows] if rows else []
        except Exception as e:
            log.error("Failed to get lessons: {err}", err=str(e))
            return []

    async def update_lesson(
        self, symbol: str, order_id: str, lesson: str,
    ) -> bool:
        """Bridge from TIAS DeepSeek output to ``trade_thesis.lesson``.

        T1-3 / F9 fix (six-tier-fixes 2026-05-11). ``close_thesis``
        writes ``lesson=''`` at close time because the synchronous
        callback path has no analysis yet. After TIAS Phase 2
        completes its DeepSeek roundtrip (background task), the bridge
        callback in manager.py composes a concise lesson summary and
        calls this method to fill the column.

        Args:
            symbol: Trade symbol; scopes the UPDATE.
            order_id: Exchange order id; combined with symbol to target
                exactly the closed row (matches close_thesis's
                ``(symbol, order_id)`` filter pattern).
            lesson: Composed lesson text (already truncated to a
                reasonable length by the caller). Empty string is
                silently ignored.

        Returns:
            True when at least one row was updated; False on empty
            lesson or DB error.
        """
        if not lesson:
            return False
        try:
            if order_id:
                await self.db.execute(
                    "UPDATE trade_thesis SET lesson = ? "
                    "WHERE symbol = ? AND order_id = ? AND status = 'closed'",
                    (lesson, symbol, order_id),
                )
            else:
                # Legacy fallback for closes without an exchange order_id.
                # Scope by symbol + most-recent closed_at.
                await self.db.execute(
                    "UPDATE trade_thesis SET lesson = ? "
                    "WHERE rowid = (SELECT rowid FROM trade_thesis "
                    "WHERE symbol = ? AND status = 'closed' "
                    "ORDER BY closed_at DESC LIMIT 1)",
                    (lesson, symbol),
                )
            log.info(
                f"TIAS_LESSON_BRIDGED | sym={symbol} "
                f"order_id={order_id or '-'} lesson_chars={len(lesson)} | {ctx()}"
            )
            return True
        except Exception as e:
            log.warning(
                f"TIAS_LESSON_BRIDGE_FAIL | sym={symbol} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )
            return False

    async def get_aggregated_stats(self, limit_closes: int = 50) -> dict:
        """Compute aggregate stats over the last N closes for prompt injection.

        T1-3 / F9 fix (six-tier-fixes 2026-05-11). Closed-loop-immune
        alternative to per-trade narrative lessons — surfaces win-rate,
        net PnL, and close-reason distribution without naming any
        specific symbol or trade. Rendered into CALL_A and CALL_B
        prompt sections via ``format_aggregated_stats_for_prompt``.

        Args:
            limit_closes: How many most-recent closes to aggregate.

        Returns:
            Dict with ``count``, ``wins``, ``losses``, ``wr_pct``,
            ``net_pnl_usd``, ``by_reason`` (mapping reason -> {count,
            wins}). Returns ``{"count": 0}`` on error or no data.
        """
        try:
            rows = await self.db.fetch_all(
                """SELECT symbol, direction, actual_pnl_pct, actual_pnl_usd,
                          close_reason
                   FROM trade_thesis
                   WHERE status = 'closed' AND actual_pnl_pct IS NOT NULL
                   ORDER BY closed_at DESC LIMIT ?""",
                (limit_closes,),
            )
            if not rows:
                return {"count": 0}
            wins = [r for r in rows if (r["actual_pnl_pct"] or 0) > 0]
            losses = [r for r in rows if (r["actual_pnl_pct"] or 0) <= 0]
            wr_pct = (len(wins) / len(rows)) * 100.0
            net_pnl = sum((r["actual_pnl_usd"] or 0) for r in rows)
            by_reason: dict[str, dict] = {}
            for r in rows:
                rsn = r["close_reason"] or "unknown"
                d = by_reason.setdefault(rsn, {"count": 0, "wins": 0})
                d["count"] += 1
                if (r["actual_pnl_pct"] or 0) > 0:
                    d["wins"] += 1
            return {
                "count": len(rows),
                "wins": len(wins),
                "losses": len(losses),
                "wr_pct": wr_pct,
                "net_pnl_usd": net_pnl,
                "by_reason": by_reason,
            }
        except Exception as e:
            log.warning(f"AGGREGATED_STATS_FAIL | err='{str(e)[:120]}'")
            return {"count": 0}

    async def get_open_symbol_list(self) -> list[str]:
        """Get list of symbols with open theses."""
        try:
            rows = await self.db.fetch_all(
                "SELECT DISTINCT symbol FROM trade_thesis WHERE status = 'open'"
            )
            return [row["symbol"] for row in rows] if rows else []
        except Exception:
            return []

    async def reconcile_with_shadow(self, shadow_symbols: set[str]) -> int:
        """Close orphan theses whose positions no longer exist on Shadow.

        This is the safety net for any close path that failed to call
        ``coordinator.on_trade_closed`` (which normally cascades to
        ``close_thesis`` via the registered callback). We NEVER delete —
        only flip ``status='open' → 'closed'``. Protected-table rules apply:
        ``trade_thesis`` rows are cumulative learning data.

        Args:
            shadow_symbols: Live open-position symbols from the Shadow API.

        Returns:
            Number of orphan theses closed this cycle (0 when healthy).
        """
        try:
            open_theses = await self.get_open_theses()
        except Exception as e:
            log.warning(
                f"ZOMBIE_RECONCILE_FAIL | stage=read err='{str(e)[:100]}' | {ctx()}"
            )
            return 0

        if not open_theses:
            return 0

        orphans = [t for t in open_theses if t.get("symbol", "") not in shadow_symbols]
        if not orphans:
            return 0

        closed_n = 0
        import time as _time
        _now = _time.time()
        # Finding 8 follow-up (2026-06-02): the exchange returns only the single
        # most-recent close per symbol (get_last_close, limit 1), so a symbol's
        # authoritative close may be booked at most ONCE per reconcile pass. This
        # tracks which symbols have already claimed their close so two same-symbol
        # orphans that both entry-match cannot double-count the same PnL.
        _claimed_symbols: set[str] = set()
        for t in orphans:
            sym = t.get("symbol", "?")
            opened_at = t.get("opened_at", "")
            order_id = str(t.get("order_id") or "")
            try:
                entry_price = float(t.get("entry_price") or 0.0)
            except (TypeError, ValueError):
                entry_price = 0.0
            # opened_at is a DB timestamp; don't fail reconciliation over date parse.
            age_s: float | str = "?"
            try:
                from datetime import datetime as _dt
                if opened_at:
                    if isinstance(opened_at, str):
                        # SQLite CURRENT_TIMESTAMP format: 'YYYY-MM-DD HH:MM:SS'
                        parsed = _dt.fromisoformat(opened_at.replace(" ", "T"))
                        age_s = round(_now - parsed.timestamp(), 0)
            except Exception:
                pass

            # Finding 8 (2026-06-02) — recover the TRUE exchange outcome instead
            # of booking zero. The order_id makes close_thesis target THIS exact
            # row (its WHERE narrows on symbol AND order_id). The entry-price
            # match inside _resolve_zombie_close keeps a recent close from being
            # booked onto an entry-MISMATCHED stale same-symbol thesis — but that
            # is a membership test, not uniqueness, so we ALSO claim each symbol's
            # recovered close once per pass (orphans are ordered newest-opened
            # first, matching the newest exchange close): the first entry-matching
            # orphan books the authoritative PnL; a later same-symbol orphan falls
            # back to zero rather than double-count the one most-recent close.
            resolved = None
            if order_id and sym not in _claimed_symbols:
                resolved = await self._resolve_zombie_close(sym, entry_price, order_id)
            if resolved is not None:
                _claimed_symbols.add(sym)
                _pnl_pct, _pnl_usd, _exit_px, _src = resolved
                _lesson = (
                    "Orphan thesis reconciled across a service downtime; true "
                    "exchange closedPnl recovered. This was an outage close (an "
                    "exchange-side stop or liquidation, not a managed exit) — "
                    "the PnL is authoritative for accounting, but do not learn "
                    "the entry/exit timing from this row."
                )
            else:
                _pnl_pct, _pnl_usd, _exit_px, _src = 0.0, 0.0, 0.0, "unrecovered"
                # Distinguish a genuine no-record fallback from a de-dup skip
                # (this symbol's single most-recent close was already claimed by
                # an earlier same-symbol orphan this pass).
                _fb_reason = (
                    "dup_close_claimed" if (order_id and sym in _claimed_symbols)
                    else "no_recoverable_close"
                )
                _lesson = (
                    "Orphan thesis closed by watchdog reconciler — no matching "
                    "Shadow position and no recoverable exchange close. Likely a "
                    "close callback was missed; PnL unknown, do not learn from "
                    "this row."
                )
            try:
                await self.close_thesis(
                    symbol=sym,
                    close_price=_exit_px,
                    actual_pnl_pct=_pnl_pct,
                    actual_pnl_usd=_pnl_usd,
                    close_reason="zombie_reconciler",
                    lesson=_lesson,
                    order_id=order_id,
                )
                closed_n += 1
                if _src == "exchange_authoritative":
                    log.warning(
                        f"ZOMBIE_LAST_CLOSE_AUTH | sym={sym} order_id={order_id or '-'} "
                        f"age={age_s}s pnl_usd={_pnl_usd:+.4f} pnl_pct={_pnl_pct:+.4f}% "
                        f"exit={_exit_px} src=exchange_authoritative | true close "
                        f"recovered (was booking zero) | {ctx()}"
                    )
                else:
                    log.warning(
                        f"ZOMBIE_LAST_CLOSE_FALLBACK | sym={sym} order_id={order_id or '-'} "
                        f"age={age_s}s pnl=0 reason={_fb_reason} | booked zero "
                        f"(exchange outcome unavailable) | {ctx()}"
                    )
            except Exception as e:
                log.warning(
                    f"ZOMBIE_RECONCILE_FAIL | stage=close sym={sym} "
                    f"err='{str(e)[:100]}' | {ctx()}"
                )

        log.info(
            f"ZOMBIE_RECONCILE | scanned={len(open_theses)} orphans={len(orphans)} "
            f"closed={closed_n} | {ctx()}"
        )
        return closed_n

    # ────────────────────────────────────────────────────────────────
    # Mid-Hold Trade Management Fix (Phase 3.1, 2026-05-19, schema v34/v35)
    # ────────────────────────────────────────────────────────────────
    #
    # These methods support the entry-thesis invalidation contract and
    # the per-position event queue. They are additive — existing callers
    # of ``save_thesis`` / ``get_open_theses`` / ``close_thesis`` keep
    # working unchanged.
    #
    #   record_thesis_state          : Watchdog updates VALID/DEGRADING/
    #                                  INVALIDATED on a per-position basis.
    #   get_open_thesis_for_symbol   : Watchdog single-row read to
    #                                  evaluate state transitions and to
    #                                  read the brain-stated criterion
    #                                  or heuristic-fallback snapshot.
    #   queue_thesis_event           : Watchdog appends an ensemble-flip
    #                                  or thesis-invalidation event for
    #                                  the next CALL_A/CALL_B to consume.
    #   get_unseen_events            : Strategist reads unseen events for
    #                                  the open-position symbol set when
    #                                  building CALL_A/CALL_B prompts.
    #   mark_events_consumed         : Strategist marks events seen-by-
    #                                  brain after a Claude response
    #                                  returns successfully.
    #   purge_events_for_closed_pos. : Coordinator drops events from the
    #                                  queue when a position closes so
    #                                  the next entry for the same symbol
    #                                  starts with a clean slate.

    _VALID_THESIS_STATES = ("VALID", "DEGRADING", "INVALIDATED")
    _VALID_EVENT_TYPES = ("ensemble_flip", "thesis_invalidation")

    async def record_thesis_state(
        self, symbol: str, order_id: str, new_state: str,
    ) -> bool:
        """Persist a thesis_state transition for a single open position.

        Called by the position watchdog after evaluating the entry
        criterion (brain_stated path) or the entry snapshot (heuristic
        fallback path). The UPDATE is scoped to symbol + order_id +
        status='open' so that:

          - Closed rows are never re-opened (status='open' filter).
          - Multiple in-flight positions on the same symbol with
            different order_id values do not cross-write each other
            (order_id filter, mirrors close_thesis semantics).

        Args:
            symbol: Trade symbol (e.g. 'ETHUSDT').
            order_id: Exchange order id (required; pass '' only for
                legacy fallback testing).
            new_state: One of 'VALID', 'DEGRADING', 'INVALIDATED'.

        Returns:
            True when at least one row was updated; False on validation
            error, DB failure, or no-matching-row.
        """
        if new_state not in self._VALID_THESIS_STATES:
            log.warning(
                f"THESIS_STATE_INVALID_VALUE | sym={symbol} "
                f"order_id={order_id or '-'} attempted_state={new_state!r} "
                f"| {ctx()}"
            )
            return False
        try:
            if order_id:
                await self.db.execute(
                    "UPDATE trade_thesis SET thesis_state = ? "
                    "WHERE symbol = ? AND order_id = ? AND status = 'open'",
                    (new_state, symbol, order_id),
                )
            else:
                await self.db.execute(
                    "UPDATE trade_thesis SET thesis_state = ? "
                    "WHERE symbol = ? AND status = 'open'",
                    (new_state, symbol),
                )
            log.info(
                f"THESIS_STATE_RECORDED | sym={symbol} "
                f"order_id={order_id or '-'} new_state={new_state} "
                f"| {ctx()}"
            )
            return True
        except Exception as e:
            log.error(
                f"THESIS_STATE_RECORD_FAIL | sym={symbol} "
                f"order_id={order_id or '-'} err='{str(e)[:120]}' | {ctx()}"
            )
            return False

    async def get_open_thesis_for_symbol(
        self, symbol: str, order_id: str = "",
    ) -> dict | None:
        """Return the most recent open thesis row for a symbol+order_id.

        Used by the watchdog to read the entry-time criterion or
        snapshot during hold-time monitoring. Returns ``None`` when no
        open row matches.

        Args:
            symbol: Trade symbol.
            order_id: Exchange order id. When empty, returns the most
                recent open row for the symbol (legacy fallback only).
                When non-empty, restricts to the exact match.
        """
        try:
            if order_id:
                row = await self.db.fetch_one(
                    "SELECT id, symbol, direction, entry_price, "
                    "stop_loss_price, take_profit_price, opened_at, "
                    "order_id, exchange_mode, apex_flipped, "
                    "apex_original_direction, apex_reason, "
                    "entry_xray_confidence, entry_setup_type, "
                    "entry_regime_at_open, entry_regime_confidence, "
                    "xray_flip_source, xray_flip_ratio, "
                    "xray_flip_rr_long, xray_flip_rr_short, "
                    "thesis_invalidation, thesis_source, "
                    "thesis_snapshot, thesis_state "
                    "FROM trade_thesis "
                    "WHERE symbol = ? AND order_id = ? AND status = 'open' "
                    "ORDER BY opened_at DESC LIMIT 1",
                    (symbol, order_id),
                )
            else:
                row = await self.db.fetch_one(
                    "SELECT id, symbol, direction, entry_price, "
                    "stop_loss_price, take_profit_price, opened_at, "
                    "order_id, exchange_mode, apex_flipped, "
                    "apex_original_direction, apex_reason, "
                    "entry_xray_confidence, entry_setup_type, "
                    "entry_regime_at_open, entry_regime_confidence, "
                    "xray_flip_source, xray_flip_ratio, "
                    "xray_flip_rr_long, xray_flip_rr_short, "
                    "thesis_invalidation, thesis_source, "
                    "thesis_snapshot, thesis_state "
                    "FROM trade_thesis "
                    "WHERE symbol = ? AND status = 'open' "
                    "ORDER BY opened_at DESC LIMIT 1",
                    (symbol,),
                )
            return dict(row) if row else None
        except Exception as e:
            log.error(
                f"OPEN_THESIS_LOOKUP_FAIL | sym={symbol} "
                f"order_id={order_id or '-'} err='{str(e)[:120]}' | {ctx()}"
            )
            return None

    async def queue_thesis_event(
        self,
        symbol: str,
        order_id: str,
        event_type: str,
        payload: str = "{}",
        thesis_id: int | None = None,
    ) -> int:
        """Append an event to the per-position queue.

        Watchdog calls this when it detects an ensemble flip (1A) or a
        thesis invalidation (2A). The event sits unconsumed
        (``consumed_at IS NULL``) until the strategist builds the next
        CALL_A or CALL_B prompt, renders it, and marks it consumed via
        ``mark_events_consumed``.

        Args:
            symbol: Trade symbol.
            order_id: Exchange order id (scopes the event to a specific
                in-flight position; passed back through close-path
                cleanup).
            event_type: One of 'ensemble_flip', 'thesis_invalidation'.
            payload: JSON-encoded detail string (caller pre-encodes).
                Defaults to '{}' so callers may emit a marker-only event.
            thesis_id: Optional thesis row id for FK observability.

        Returns:
            Newly inserted event id, or -1 on failure / invalid input.
        """
        if event_type not in self._VALID_EVENT_TYPES:
            log.warning(
                f"THESIS_EVENT_INVALID_TYPE | sym={symbol} "
                f"order_id={order_id or '-'} attempted_type={event_type!r} "
                f"| {ctx()}"
            )
            return -1
        try:
            cursor = await self.db.execute(
                "INSERT INTO thesis_events "
                "(symbol, order_id, thesis_id, event_type, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (symbol, order_id, thesis_id, event_type, payload or "{}"),
            )
            event_id = cursor.lastrowid if hasattr(cursor, "lastrowid") else -1
            log.info(
                f"THESIS_EVENT_QUEUED | eid={event_id} sym={symbol} "
                f"order_id={order_id or '-'} type={event_type} "
                f"payload_chars={len(payload or '')} | {ctx()}"
            )
            return event_id
        except Exception as e:
            log.error(
                f"THESIS_EVENT_QUEUE_FAIL | sym={symbol} "
                f"order_id={order_id or '-'} type={event_type} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )
            return -1

    async def get_unseen_events(
        self, symbols: list[str] | tuple[str, ...] | None = None,
        max_per_symbol: int = 10,
    ) -> list[dict]:
        """Return events with ``consumed_at IS NULL`` for the given symbols.

        Used by the strategist when constructing CALL_A and CALL_B
        prompts. Returns up to ``max_per_symbol`` most-recent events per
        symbol so a noisy position cannot starve the prompt budget.

        Args:
            symbols: List of trade symbols to filter on. When None,
                returns unseen events across all open positions.
            max_per_symbol: Operator-tunable cap to bound prompt growth.

        Returns:
            List of event dicts (each with id, symbol, order_id,
            thesis_id, event_type, payload, created_at), most-recent
            first. Empty list on error or no events.
        """
        try:
            if symbols:
                placeholders = ",".join(["?"] * len(symbols))
                sql = (
                    "SELECT id, symbol, order_id, thesis_id, event_type, "
                    "payload, created_at "
                    "FROM thesis_events "
                    f"WHERE consumed_at IS NULL AND symbol IN ({placeholders}) "
                    "ORDER BY symbol ASC, created_at DESC"
                )
                rows = await self.db.fetch_all(sql, tuple(symbols))
            else:
                rows = await self.db.fetch_all(
                    "SELECT id, symbol, order_id, thesis_id, event_type, "
                    "payload, created_at "
                    "FROM thesis_events "
                    "WHERE consumed_at IS NULL "
                    "ORDER BY symbol ASC, created_at DESC"
                )
            if not rows:
                return []
            # Cap per-symbol so a single noisy position cannot blow the
            # prompt budget. Cheapest correct way: group then slice.
            per_symbol_count: dict[str, int] = {}
            capped: list[dict] = []
            for r in rows:
                d = dict(r)
                sym = d.get("symbol", "")
                seen = per_symbol_count.get(sym, 0)
                if seen >= max_per_symbol:
                    continue
                per_symbol_count[sym] = seen + 1
                capped.append(d)
            return capped
        except Exception as e:
            log.error(
                f"THESIS_EVENT_LOOKUP_FAIL | symbols={symbols!r} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )
            return []

    async def mark_events_consumed(
        self, event_ids: list[int] | tuple[int, ...], consumer: str,
    ) -> int:
        """Mark a batch of events as consumed by a Claude call.

        Called by the strategist after CALL_A or CALL_B returns. The
        consumed flag prevents the next prompt cycle from re-rendering
        the same event (and double-paying token cost).

        Args:
            event_ids: Sequence of event ids to mark consumed.
            consumer: 'CALL_A' or 'CALL_B' (free-form string is
                accepted; canonical values keep operator log greps
                consistent).

        Returns:
            Number of rows updated (best-effort; SQLite cursor.rowcount
            availability varies — falls back to len(event_ids) when
            unavailable).
        """
        if not event_ids:
            return 0
        try:
            placeholders = ",".join(["?"] * len(event_ids))
            sql = (
                "UPDATE thesis_events "
                "SET consumed_at = datetime('now'), consumed_by = ? "
                f"WHERE id IN ({placeholders}) AND consumed_at IS NULL"
            )
            await self.db.execute(sql, (consumer, *event_ids))
            log.info(
                f"THESIS_EVENT_CONSUMED | n={len(event_ids)} "
                f"consumer={consumer} | {ctx()}"
            )
            return len(event_ids)
        except Exception as e:
            log.error(
                f"THESIS_EVENT_CONSUME_FAIL | n={len(event_ids)} "
                f"consumer={consumer} err='{str(e)[:120]}' | {ctx()}"
            )
            return 0

    @staticmethod
    def evaluate_thesis_state(
        thesis_row: dict,
        current_price: float,
        last_m5_close: float | None = None,
        *,
        close_buffer_pct: float = 0.5,
        degrading_buffer_pct: float = 0.1,
    ) -> tuple[str, str]:
        """Evaluate VALID / DEGRADING / INVALIDATED for an open thesis row.

        Pure function. Called by the watchdog inside _monitor_position
        to decide whether to record a state transition for the thesis.
        Both Approach C (brain-stated price criterion) and Approach A
        (heuristic fallback from XRAY snapshot) paths flow through this
        single evaluator so the watchdog has one decision surface.

        Args:
            thesis_row: A dict from get_open_thesis_for_symbol; must
                include ``direction``, ``thesis_source``,
                ``thesis_invalidation`` (JSON), ``thesis_snapshot``
                (JSON).
            current_price: Latest mark/last price; used for wick-style
                DEGRADING detection.
            last_m5_close: Most recent completed M5 candle close; used
                for close-style INVALIDATED detection. When None,
                ``current_price`` is used (a reasonable proxy for
                in-tick observers).
            close_buffer_pct: Operator-tunable percentage buffer added
                to the level before declaring INVALIDATED (close).
                Default 0.5% mirrors structural_levels.sl_buffer_pct.
            degrading_buffer_pct: Operator-tunable percentage buffer
                for DEGRADING (wick). Default 0.1%.

        Returns:
            ``(new_state, reason)`` tuple. ``new_state`` is one of
            'VALID' / 'DEGRADING' / 'INVALIDATED'. ``reason`` is a
            short grep-friendly tag for the watchdog's log line. Possible
            reason values:
                'brain_price_close_above_invalidated',
                'brain_price_close_above_degrading',
                'brain_price_close_below_invalidated',
                'brain_price_close_below_degrading',
                'brain_signal_or_none_valid',
                'heuristic_fallback_invalidated',
                'heuristic_fallback_degrading',
                'heuristic_fallback_no_anchor',
                'valid'.
        """
        import json as _json

        if not thesis_row:
            return "VALID", "valid"
        if last_m5_close is None:
            last_m5_close = current_price

        direction = str(thesis_row.get("direction", "") or "").upper()
        source = str(thesis_row.get("thesis_source", "brain_stated") or "brain_stated")

        # ── Brain-stated path ──
        if source == "brain_stated":
            raw = thesis_row.get("thesis_invalidation") or ""
            if not raw:
                # Brain marked the trade as 'none' OR somehow the column
                # is empty despite source=brain_stated (legacy). Treat as
                # VALID; the fallback rendering still tells brain about
                # the thesis at prompt time.
                return "VALID", "brain_signal_or_none_valid"
            try:
                crit = _json.loads(raw)
            except Exception:
                return "VALID", "brain_signal_or_none_valid"
            crit_type = crit.get("type")
            crit_value = crit.get("value")
            if crit_type == "price_close_above":
                try:
                    level = float(crit_value)
                except (TypeError, ValueError):
                    return "VALID", "brain_signal_or_none_valid"
                close_threshold = level * (1.0 + close_buffer_pct / 100.0)
                wick_threshold = level * (1.0 + degrading_buffer_pct / 100.0)
                if last_m5_close >= close_threshold:
                    return "INVALIDATED", "brain_price_close_above_invalidated"
                if current_price >= wick_threshold:
                    return "DEGRADING", "brain_price_close_above_degrading"
                return "VALID", "valid"
            if crit_type == "price_close_below":
                try:
                    level = float(crit_value)
                except (TypeError, ValueError):
                    return "VALID", "brain_signal_or_none_valid"
                close_threshold = level * (1.0 - close_buffer_pct / 100.0)
                wick_threshold = level * (1.0 - degrading_buffer_pct / 100.0)
                if last_m5_close <= close_threshold:
                    return "INVALIDATED", "brain_price_close_below_invalidated"
                if current_price <= wick_threshold:
                    return "DEGRADING", "brain_price_close_below_degrading"
                return "VALID", "valid"
            # 'signal' and 'none' are not price-based; signal events fire
            # through Phase 3.4's ensemble-flip path. Stay VALID here.
            return "VALID", "brain_signal_or_none_valid"

        # ── Heuristic fallback path (Approach A) ──
        snap_raw = thesis_row.get("thesis_snapshot") or "{}"
        try:
            snap = _json.loads(snap_raw)
        except Exception:
            snap = {}
        anchor = snap.get("nearest_aligned_level") or {}
        anchor_type = anchor.get("type")
        if not anchor_type or anchor_type == "none":
            return "VALID", "heuristic_fallback_no_anchor"

        # Sells were justified by a bearish level above entry; invalidation
        # is closing above its high. Buys were justified by a bullish level
        # below entry; invalidation is closing below its low.
        if direction == "SELL":
            # Bearish OB high (or bearish FVG top) acts as the ceiling.
            level = anchor.get("high") or anchor.get("top") or anchor.get("midpoint")
            if level is None:
                return "VALID", "heuristic_fallback_no_anchor"
            try:
                level = float(level)
            except (TypeError, ValueError):
                return "VALID", "heuristic_fallback_no_anchor"
            close_threshold = level * (1.0 + close_buffer_pct / 100.0)
            wick_threshold = level * (1.0 + degrading_buffer_pct / 100.0)
            if last_m5_close >= close_threshold:
                return "INVALIDATED", "heuristic_fallback_invalidated"
            if current_price >= wick_threshold:
                return "DEGRADING", "heuristic_fallback_degrading"
            return "VALID", "valid"
        if direction == "BUY":
            level = anchor.get("low") or anchor.get("bottom") or anchor.get("midpoint")
            if level is None:
                return "VALID", "heuristic_fallback_no_anchor"
            try:
                level = float(level)
            except (TypeError, ValueError):
                return "VALID", "heuristic_fallback_no_anchor"
            close_threshold = level * (1.0 - close_buffer_pct / 100.0)
            wick_threshold = level * (1.0 - degrading_buffer_pct / 100.0)
            if last_m5_close <= close_threshold:
                return "INVALIDATED", "heuristic_fallback_invalidated"
            if current_price <= wick_threshold:
                return "DEGRADING", "heuristic_fallback_degrading"
            return "VALID", "valid"
        return "VALID", "valid"

    async def purge_events_for_closed_position(self, order_id: str) -> int:
        """Drop all events for a specific (now-closed) position.

        Called by the close path after ``close_thesis`` succeeds. Keeps
        the queue lean; re-opening the same symbol on a new order_id
        starts with a clean event slate.

        Args:
            order_id: Exchange order id of the closed position.

        Returns:
            Number of rows deleted (best-effort).
        """
        if not order_id:
            return 0
        try:
            await self.db.execute(
                "DELETE FROM thesis_events WHERE order_id = ?",
                (order_id,),
            )
            log.info(
                f"THESIS_EVENTS_PURGED | order_id={order_id} | {ctx()}"
            )
            return 1
        except Exception as e:
            log.error(
                f"THESIS_EVENTS_PURGE_FAIL | order_id={order_id} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )
            return 0
