"""System command handlers: /status, /pause, /resume, /errors.

Pause/resume state lives on pnl_manager (single source of truth) so every
automated trade gate that already consults pnl_manager.can_trade() inherits
the manual halt without further wiring.
"""

from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.telegram.ui.formatters import format_timestamp

log = get_logger("telegram")


class SystemHandler:
    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.s = services

    def _is_paused(self) -> bool:
        pnl = self.s.get("pnl_manager")
        return bool(pnl and getattr(pnl, "is_manually_paused", False))

    async def status(self, update, context) -> None:
        pnl = self.s.get("pnl_manager")
        mode = "N/A"
        if pnl:
            try:
                await pnl.update()
                mode = pnl.get_current_mode()["mode"]
            except Exception:
                pass

        registry = self.s.get("registry")
        strat_count = registry.count if registry and hasattr(registry, "count") else "N/A"

        msg = (
            f"\u2699\ufe0f <b>SYSTEM STATUS</b>\n\n"
            f"Status: {'PAUSED' if self._is_paused() else 'RUNNING'}\n"
            f"Trading mode: {mode}\n"
            f"Strategies: {strat_count} active\n"
        )

        # TradeCoordinator status
        coordinator = self.s.get("trade_coordinator")
        if coordinator:
            coord_status = coordinator.get_status()
            msg += "\n\U0001f517 <b>Trade Coordinator</b>\n"
            msg += f"  Active trades: {coord_status['active_trades']}\n"
            for sym, info in coord_status.get("positions", {}).items():
                if info["immune"]:
                    msg += f"  \U0001f6e1\ufe0f {sym}: {info['age_minutes']}min ({info['category']}) — {info['remaining_immunity']}s immunity\n"
                else:
                    msg += f"  \U0001f441\ufe0f {sym}: {info['age_minutes']}min ({info['category']})\n"
            msg += f"  Recent closes: {coord_status['recent_closes']}\n"
            lc = coord_status.get("last_close")
            if lc:
                msg += f"  Last: {lc['symbol']} {lc['pnl_pct']:+.2f}% by {lc['closed_by']}\n"

        # Enforcer status
        enforcer = self.s.get("enforcer")
        if enforcer:
            es = enforcer.get_status()
            msg += (
                f"\n\u26a1 <b>Enforcer L{es['escalation_level']}</b>\n"
                f"  Trades: {es['trades_this_hour']}/{es['target']}\n"
                f"  Profit: {es['profit_this_hour']:+.2f}%/{es['profit_target']}%\n"
                f"  Gap: {es['seconds_since_last_trade']}s\n"
            )

        msg += f"\n\U0001f550 {format_timestamp()}"
        await update.message.reply_text(msg, parse_mode="HTML")

    async def errors(self, update, context) -> None:
        """Show recent errors from brain decisions and service health."""
        lines = ["\u26a0\ufe0f <b>ERROR REPORT</b>\n"]
        has_errors = False

        # Check Claude AI health
        claude = self.s.get("claude_client")
        if claude and hasattr(claude, "get_stats"):
            stats = claude.get_stats()
            cf = stats.get("consecutive_failures", 0)
            if cf > 0:
                lines.append(f"\U0001f534 Claude AI: {cf} consecutive failures")
                has_errors = True

        # Check freshness guard
        guard = self.s.get("freshness_guard")
        if guard and hasattr(guard, "is_stale"):
            try:
                if guard.is_stale():
                    lines.append("\U0001f534 Data freshness: STALE — trading paused")
                    has_errors = True
            except Exception:
                pass

        # Phase conn-pool/p5-4 (2026-05-14) \u2014 the historical "Recent Brain
        # Issues" block queried ``brain_decisions`` for action_taken LIKE
        # '%error%'/'%fail%'/'%skip%'. That table is 0 rows in current
        # production (the active strategist writes to ``claude_decisions``
        # via data_lake.write_claude_decision; ``brain_decisions`` is only
        # written by the unused ``brain_v2.py:391`` path). ``claude_decisions``
        # has a different schema with no ``action_taken`` column, so the
        # error/fail/skip semantic doesn't translate. Brain failures now
        # surface via ``BRAIN_FAILURE_CASCADE`` log lines, which operators
        # grep in ``data/logs/workers.log`` directly. The block was removed
        # rather than rewritten because there is no equivalent column to
        # filter on in claude_decisions.

        # Recent trade losses (last 5 losses).
        # P4 of P1-P10: filter by current_mode so /errors doesn't mix
        # shadow + bybit_demo loss reports. Schema v29 added
        # exchange_mode to trade_intelligence. Falls back to unfiltered
        # query when transformer is unavailable.
        _xfm = self.s.get("transformer") if hasattr(self, "s") else None
        _mode = None
        if _xfm is not None:
            try:
                _mode = str(_xfm.current_mode) if _xfm.current_mode else None
            except Exception:
                _mode = None
        try:
            if _mode:
                rows = await self.db.fetch_all(
                    "SELECT symbol, direction, pnl_pct, pnl_usd, closed_by "
                    "FROM trade_intelligence WHERE win = 0 AND exchange_mode = ? "
                    "ORDER BY id DESC LIMIT 3",
                    (_mode,),
                )
            else:
                rows = await self.db.fetch_all(
                    "SELECT symbol, direction, pnl_pct, pnl_usd, closed_by "
                    "FROM trade_intelligence WHERE win = 0 "
                    "ORDER BY id DESC LIMIT 3",
                )
            if rows:
                lines.append("\n<b>Recent Losses:</b>")
                for r in rows:
                    pnl = float(r.get("pnl_usd") or 0)
                    lines.append(
                        f"  \U0001f534 {r['symbol']} {r['direction']} "
                        f"{float(r.get('pnl_pct') or 0):+.2f}% (${pnl:+,.2f}) "
                        f"by {r.get('closed_by', '?')}"
                    )
                has_errors = True
        except Exception:
            pass

        if not has_errors:
            lines.append("\U0001f7e2 No errors detected. All systems healthy.")

        lines.append(f"\n\U0001f550 {format_timestamp()}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def pause(self, update, context) -> None:
        pnl = self.s.get("pnl_manager")
        if not pnl or not hasattr(pnl, "pause_manually"):
            await update.message.reply_text("Pause unavailable: pnl_manager not wired.")
            return
        chat_id = update.effective_chat.id
        user = getattr(update.effective_user, "username", None) or str(chat_id)
        pnl.pause_manually(f"telegram /pause by {user}")
        await update.message.reply_text("\u23f8\ufe0f Trading PAUSED. Use /resume to restart.")

    async def resume(self, update, context) -> None:
        pnl = self.s.get("pnl_manager")
        if not pnl or not hasattr(pnl, "resume_manually"):
            await update.message.reply_text("Resume unavailable: pnl_manager not wired.")
            return
        pnl.resume_manually()
        await update.message.reply_text("\u25b6\ufe0f Trading RESUMED.")

    async def health(self, update, context) -> None:
        """Layer 1 restructure Phase 1 \u2014 system health snapshot.

        Reads ``services["cycle_tracker"].get_recent(10)`` plus current
        layer state, formatting a multi-section block per blueprint
        Section 14.5. Falls back to "tracker not available" if the
        CycleTracker hasn't been wired yet (e.g. during early boot).
        """
        ct = self.s.get("cycle_tracker")
        lm = self.s.get("layer_manager")
        lines: list[str] = ["\U0001f3e5 <b>SYSTEM HEALTH</b>"]
        lines.append("(Layer 1 restructure observability \u2014 Phase 1)")
        lines.append("")

        # Per-layer summary (uses CycleTracker history if available)
        if ct is None:
            lines.append("CycleTracker: not wired")
        else:
            recent = ct.get_recent(10)
            if not recent:
                lines.append("Cycle history: empty (no cycles completed yet)")
            else:
                def _p95(xs: list[int]) -> int:
                    xs2 = sorted(x for x in xs if x is not None)
                    if not xs2:
                        return 0
                    k = max(0, int(round(0.95 * (len(xs2) - 1))))
                    return xs2[k]
                l1a = [c.layer1a_ms for c in recent if c.layer1a_ms is not None]
                l1b = [c.layer1b_ms for c in recent if c.layer1b_ms is not None]
                l1c = [c.layer1c_ms for c in recent if c.layer1c_ms is not None]
                l1d = [c.layer1d_ms for c in recent if c.layer1d_ms is not None]
                totals = [c.total_ms for c in recent]
                lines.append(f"<b>Last {len(recent)} cycles</b>")
                lines.append(
                    f"  L1A p95={_p95(l1a)}ms (samples={len(l1a)})"
                )
                lines.append(
                    f"  L1B p95={_p95(l1b)}ms (samples={len(l1b)})"
                )
                lines.append(
                    f"  L1C p95={_p95(l1c)}ms (samples={len(l1c)})"
                )
                lines.append(
                    f"  L1D p95={_p95(l1d)}ms (samples={len(l1d)})"
                )
                lines.append(f"  Total p95={_p95(totals)}ms")
                last = recent[-1]
                lines.append(
                    f"  Latest: {last.cycle_id} packages={last.packages_ready} status={last.status}"
                )

        # Layer toggle status
        lines.append("")
        if lm and hasattr(lm, "is_layer_active"):
            try:
                lines.append("<b>Layer toggles</b>")
                lines.append(f"  L1 DATA:      {'ON' if lm.is_layer_active(1) else 'OFF'}")
                lines.append(f"  L2 BRAIN:     {'ON' if lm.is_layer_active(2) else 'OFF'}")
                lines.append(f"  L3 EXECUTION: {'ON' if lm.is_layer_active(3) else 'OFF'}")
            except Exception as e:
                lines.append(f"  layer_manager query failed: {str(e)[:80]}")
        else:
            lines.append("Layer manager: not wired")

        # Phase 6 (output-quality) — data freshness section. Reads the
        # cache_freshness singleton that kline_worker / structure_worker /
        # scanner_worker write into. Shows last-known age for each cache
        # so operators can SEE pipeline timing degradation.
        lines.append("")
        lines.append("<b>Data freshness (last write)</b>")
        try:
            import time as _t

            from src.core.cache_freshness import get_snapshot
            _snap = get_snapshot()
            _now = _t.time()

            def _age_summary(cache_name: str) -> str:
                ages = [
                    (_now - ts) for (cn, _key), ts in _snap.items()
                    if cn == cache_name
                ]
                if not ages:
                    return "no data"
                _min = int(min(ages))
                _max = int(max(ages))
                _med = int(sorted(ages)[len(ages) // 2])
                return (
                    f"min={_min}s med={_med}s max={_max}s n={len(ages)}"
                )

            lines.append(f"  klines:    {_age_summary('klines')}")
            lines.append(f"  xray:      {_age_summary('xray')}")
            lines.append(f"  packages:  {_age_summary('packages')}")
        except Exception as _e:
            lines.append(f"  freshness query failed: {str(_e)[:80]}")

        # Phase 11 (dead-workers fix) — per-worker liveness summary.
        # Reads the WorkerLivenessTracker that the watchdog writes into;
        # displays per-status counts and lists any worker that is NOT
        # healthy (NEVER_TICKED / OVERDUE) so operators see the same
        # information the watchdog logs without grepping workers.log.
        lines.append("")
        lines.append("<b>Worker liveness</b>")
        tracker = self.s.get("worker_liveness")
        if tracker is None:
            lines.append("  tracker: not wired")
        else:
            try:
                cycle_active = bool(
                    lm.is_cycle_active()
                    if (lm and hasattr(lm, "is_cycle_active"))
                    else False
                )
                snaps = tracker.snapshot_with_cycle(cycle_active=cycle_active)
                if not snaps:
                    lines.append("  no workers registered yet")
                else:
                    counts = {
                        "healthy": 0,
                        "never_ticked": 0,
                        "overdue": 0,
                        "idle_cycle_gate": 0,
                        "no_data": 0,
                    }
                    bad: list = []
                    for h in snaps:
                        counts[h.status] = counts.get(h.status, 0) + 1
                        if h.status in ("never_ticked", "overdue"):
                            bad.append(h)
                    summary = (
                        f"  total={len(snaps)} "
                        f"healthy={counts['healthy']} "
                        f"never_ticked={counts['never_ticked']} "
                        f"overdue={counts['overdue']} "
                        f"idle_cycle_gate={counts['idle_cycle_gate']}"
                    )
                    lines.append(summary)
                    if bad:
                        lines.append("  ⚠ unhealthy workers:")
                        for h in bad[:10]:  # cap so message stays
                            tag = "NEVER" if h.status == "never_ticked" else "OVERDUE"
                            lines.append(
                                f"    {tag} {h.name} — {h.status_reason}"
                            )
                        if len(bad) > 10:
                            lines.append(
                                f"    ... and {len(bad) - 10} more"
                            )
                    else:
                        lines.append("  ✓ all workers OK")
            except Exception as e:
                lines.append(f"  liveness query failed: {str(e)[:80]}")

        # ── Phase 1 of the Layer 1D briefing-pack rewrite ─────────────
        # The briefing pipeline (state characterizer → labeler → ranker
        # → builder) is registered here for operator visibility. While
        # the pipeline is dormant (Phases 1-4 are additive setup), this
        # section reads "(not yet active)". From Phase 5 onwards, when
        # ``[scanner].mode = "briefing"``, the section reports the most
        # recent cycle's briefing aggregates from CycleTracker.
        lines.append("")
        lines.append("<b>Briefing pipeline</b>")
        try:
            scanner_mode = "exclusion"  # default until Phase 5 introduces the flag
            try:
                _settings = self.s.get("settings")
                if _settings is not None:
                    scanner_mode = getattr(
                        getattr(_settings, "scanner", object()),
                        "mode",
                        "exclusion",
                    )
            except Exception:
                pass

            if ct is None:
                lines.append("  CycleTracker: not wired")
            elif scanner_mode == "exclusion":
                lines.append("  mode=exclusion (briefing pipeline not yet active)")
            else:
                # mode=briefing — surface latest cycle's briefing aggregates
                _recent = ct.get_recent(1) if ct else []
                if not _recent:
                    lines.append("  mode=briefing (no cycles yet)")
                else:
                    _last = _recent[-1]
                    _il = (
                        f"{_last.interestingness_score:.2f}"
                        if _last.interestingness_score is not None else "—"
                    )
                    _bc = (
                        str(_last.briefing_packages_count)
                        if _last.briefing_packages_count is not None else "—"
                    )
                    _labels = _last.state_label_counts or {}
                    _top_label = (
                        max(_labels.items(), key=lambda kv: kv[1])[0]
                        if _labels else "—"
                    )
                    lines.append(
                        f"  mode=briefing  interestingness_mean={_il} "
                        f"packages={_bc}  top_label={_top_label}"
                    )
        except Exception as e:
            lines.append(f"  briefing query failed: {str(e)[:80]}")

        lines.append("")
        lines.append(f"\U0001f550 {format_timestamp()}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

