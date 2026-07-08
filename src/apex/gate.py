"""APEX TradeGate — hard safety limits between optimizer and execution.

The gate NEVER blocks a trade. It adjusts parameters within safe bounds.
If APEX says $1,500 but max is $1,200 → reduce to $1,200.
If capital is low → reduce size proportionally.
If symbol already has a position → halve size.

Phase A3 additions (Checks 8-12): APEX guardrails that prevent DeepSeek from
choking winners with tight trails and low TPs.

Phase B additions (Check 4 upgrade): Conviction-weighted capital allocation
based on per-coin profit factor from TIAS history.

This is the LAST checkpoint before a trade hits Shadow. It ensures no
single trade can blow up the account regardless of what Claude or DeepSeek decided.
"""

from __future__ import annotations

import time
from typing import Any

from src.core.log_context import ctx
from src.core.utils import format_price
from src.core.logging import get_logger

log = get_logger("apex")


class TradeGate:
    """Hard safety limits between APEX optimizer and Shadow execution.

    Runs 12 checks. Each check MAY adjust parameters but NEVER blocks.
    Modifications are logged at INFO level and attached to the trade dict
    as ``_gate_adjustments`` for TIAS feedback.

    Args:
        services: The shared services dict from WorkerManager._services.
        settings: APEXSettings from config.
    """

    def __init__(self, services: dict, settings: Any) -> None:
        self._services = services
        self._settings = settings
        # Conviction weight cache: {symbol: (weight, timestamp)}
        self._conviction_cache: dict[str, tuple[float, float]] = {}
        self._conviction_cache_ttl: float = 300.0  # 5 minutes
        # Brain-authoritative in-cycle aggregate guard (2026-05-31): running
        # total of sizes approved within the CURRENT brain cycle, reset when the
        # cycle did changes. fund_manager.in_use only refreshes ~60s, so
        # `available` is stale within a cycle; this prevents the cycle's 2-4
        # trades from collectively over-deploying past usable capital.
        self._brain_auth_cycle_did: str | None = None
        self._brain_auth_cycle_reserved: float = 0.0
        # Entry-quality filter counters (win-rate enhancement, 2026-07-07).
        # In-memory only (reset on restart) — cheap running totals for the
        # daily scorecard (AlertManager._gather_summary_data reads
        # get_entry_quality_stats()). Per-reason rejects are ALSO logged
        # individually as GATE_REJECT for durable/queryable history; these
        # counters are just a fast summary, not the source of truth.
        self._eq_pass_count: int = 0
        self._eq_reject_counts: dict[str, int] = {
            "entry_quality_signal_conf": 0,
            "entry_quality_xray_conf": 0,
            "entry_quality_adx": 0,
        }

    def get_entry_quality_stats(self) -> dict:
        """Entry-quality filter pass/reject counters since process start."""
        return {
            "passed": self._eq_pass_count,
            "rejected_by_reason": dict(self._eq_reject_counts),
            "rejected_total": sum(self._eq_reject_counts.values()),
        }
        # Issue E18 (2026-05-27) boot sentinel — confirm the APEX
        # structureless-coin size-boost guard is live after restart and report
        # its thresholds. (Extended by E17's reject thresholds.)
        try:
            log.info(
                f"APEX_STRUCTURELESS_GUARD_SENTINEL | "
                f"e18_a_plus_conf_floor={float(getattr(settings, 'gate_a_plus_conf_floor', 0.0) or 0.0):.2f} "
                f"e18_a_plus_score={float(getattr(settings, 'gate_a_plus_score_threshold', 80.0) or 80.0):.0f} "
                f"e18_a_plus_mult={float(getattr(settings, 'gate_a_plus_size_mult', 1.20) or 1.20):.2f}x "
                f"e17_conf_floor={float(getattr(settings, 'gate_structureless_conf_floor', 0.0) or 0.0):.2f} "
                f"e17_score_min={float(getattr(settings, 'gate_structureless_score_min', 999.0) or 999.0):.0f} "
                # Issue 7 (2026-06-08) boot sentinel — portfolio directional-
                # drawdown breaker (default OFF until the threshold is sized).
                f"pf_dd_breaker={bool(getattr(settings, 'portfolio_dd_breaker_enabled', False))} "
                f"pf_dd_min_pos={int(getattr(settings, 'portfolio_dd_breaker_min_positions', 3))} "
                f"pf_dd_conc={float(getattr(settings, 'portfolio_dd_breaker_concentration', 0.80)):.2f} "
                f"pf_dd_open_loss_pct={float(getattr(settings, 'portfolio_dd_breaker_open_loss_pct', 1.5)):.2f} "
                f"| {ctx()}"
            )
        except Exception:  # pragma: no cover — observability only
            pass
        # Brain-Authoritative Sizing (2026-05-31) boot sentinel — confirm the
        # flag state + the CHECK 4 ceiling + the raised absolute max on restart,
        # and give the operator the instant-revert reference.
        try:
            log.info(
                f"BRAIN_AUTHORITATIVE_SIZING_SENTINEL | "
                f"enabled={bool(getattr(settings, 'brain_authoritative_sizing_enabled', False))} "
                f"per_trade_pct_of_available="
                f"{float(getattr(settings, 'brain_auth_per_trade_pct_of_available', 0.40) or 0.40):.2f} "
                f"max_position_size_usd="
                f"${float(getattr(settings, 'max_position_size_usd', 1200.0) or 1200.0):.0f} "
                f"| (enabled -> size_usd is MARGIN; CHECK 4 caps it at the per-trade MARGIN "
                f"ceiling = usable/max_positions [executor builds notional = size_usd x leverage]; "
                f"CHECK 1 absolute backstop = usable [margin, equity-tracking]; "
                f"APEX won't shrink the brain's size; revert: set false) | {ctx()}"
            )
        except Exception:  # pragma: no cover — observability only
            pass

    async def validate(self, trade: dict) -> dict:
        """Apply safety limits to an optimized trade directive.

        Returns the modified directive dict. NEVER returns None.

        Args:
            trade: Directive dict (may have APEX modifications from Phase 2).

        Returns:
            Modified directive dict with safe parameters.
        """
        symbol = trade.get("symbol", "")
        modifications: list[str] = []
        # Phase 7: total validate() elapsed is surfaced via trade["_gate_validation_ms"]
        # so layer_manager's BRAIN_DO_TRADE can break down per-trade cost.
        _gate_t0 = time.time()

        # ═══ CHECK 0: Claude directive size cap (Phase 5) ═══
        # Hard ceiling against APEX DeepSeek or conviction-weight inflation:
        # final size cannot exceed mult × Claude's pre-APEX directive. The
        # "_claude_original_size_usd" is stamped by layer_manager BEFORE
        # apex.optimize() runs, so it survives APEX regardless of outcome.
        # Set mult=0 (or missing claude-original) disables this ceiling.
        try:
            cap_mult = float(getattr(self._settings, "gate_apex_size_cap_mult", 1.5))
        except Exception:
            cap_mult = 1.5
        try:
            claude_orig = float(trade.get("_claude_original_size_usd", 0) or 0)
        except Exception:
            claude_orig = 0.0
        if claude_orig > 0 and cap_mult > 0:
            proposed_size = float(trade.get("size_usd", 0) or 0)
            max_allowed = round(claude_orig * cap_mult, 2)
            if proposed_size > max_allowed:
                trade["size_usd"] = max_allowed
                modifications.append(
                    f"CONVICTION_SIZE_CAP(claude=${claude_orig:.0f},"
                    f"req=${proposed_size:.0f},cap=${max_allowed:.0f})"
                )
                log.info(
                    f"CONVICTION_SIZE_CAP | sym={symbol} "
                    f"claude=${claude_orig:.0f} requested=${proposed_size:.0f} "
                    f"capped=${max_allowed:.0f} mult={cap_mult}x | {ctx()}"
                )
        # Sniper-Latency-Size Fix Phase 3D (2026-05-07) — sizing
        # breadcrumb. Captures the size after CHECK 0 (whether or not
        # the cap fired) so the unified SIZE_DERIVATION event can
        # distinguish "Check 0 was passive" from "Check 0 capped".
        trade["_gate_post_check0_size_usd"] = float(trade.get("size_usd", 0) or 0)

        # ═══ CHECK 1: Maximum position size ═══
        # size_usd is MARGIN. Under brain-authoritative sizing, CHECK 4 (per-trade
        # MARGIN = usable / max_positions) is the real per-trade authority. CHECK 1
        # then only needs to be a SANE ABSOLUTE MARGIN backstop — no single trade
        # may commit more MARGIN than the entire usable pool. That bound TRACKS
        # equity (usable scales with equity), so a fixed config value would clip a
        # legitimate per-trade-margin trade once the account grows. Derive it from
        # the SAME single source (tiered_capital); fall back to the config absolute
        # max only when tiered/equity is unavailable. Flag OFF -> config value as-is.
        max_size = self._settings.max_position_size_usd
        if bool(getattr(self._settings, "brain_authoritative_sizing_enabled", False)):
            try:
                _tcm1 = self._services.get("tiered_capital")
                _fm1 = self._services.get("fund_manager")
                _eq1 = float(getattr(getattr(_fm1, "_account_state", None), "total_equity", 0.0) or 0.0)
                if _tcm1 is not None and _eq1 > 0:
                    _usable1 = float(_tcm1.get_limits(_eq1, 0.0).usable_capital)
                    max_size = max(max_size, _usable1)  # MARGIN backstop = whole usable pool
            except Exception:  # pragma: no cover — falls back to config absolute max
                pass
        current_size = float(trade.get("size_usd", 600) or 600)
        if current_size > max_size:
            trade["size_usd"] = max_size
            modifications.append(f"size=${current_size:.0f}->${max_size:.0f}")

        # ═══ CHECK 2: Maximum leverage ═══
        max_lev = self._settings.max_leverage
        current_lev = int(trade.get("leverage", 3) or 3)
        if current_lev > max_lev:
            trade["leverage"] = max_lev
            modifications.append(f"lev={current_lev}->{max_lev}")

        # ═══ CHECK 3: Maximum concurrent positions ═══
        # Align the concurrency cap to the SAME tiered_capital max_positions the
        # brain is shown + the CHECK-4 fund math use (book builds to N over the
        # 5-min cycles). Default 5 only when tiered/equity is unavailable.
        max_concurrent = 5
        try:
            _tcm3 = self._services.get("tiered_capital")
            _fm3 = self._services.get("fund_manager")
            _eq3 = float(getattr(getattr(_fm3, "_account_state", None), "total_equity", 0.0) or 0.0)
            if _tcm3 is not None and _eq3 > 0:
                max_concurrent = max(1, int(_tcm3.get_limits(_eq3, 0.0).max_positions or 5))
        except Exception:
            max_concurrent = 5
        try:
            pos_svc = self._services.get("position_service")
            if pos_svc:
                positions = await pos_svc.get_positions()
                open_count = len(positions) if positions else 0
                # Issue 2.8 (2026-06-07): directional-concentration observability.
                # The all-contrarian-longs session was a market condition (every
                # coin in an extreme-fear contrarian-long regime), not a separate
                # bug — one-sided exposure is addressed by the breadth throttle
                # (2.4) plus the per-coin direction logic. This surfaces the
                # book's long/short skew so the concentration is visible; it is a
                # NOTE, never a directional gate.
                if positions:
                    # Position.side is a Side(str, Enum) whose member BUY="Buy",
                    # so str(Side.BUY) renders "Side.BUY" (->"side.buy") and never
                    # matches "buy" — which would count EVERY real position as a
                    # short (skew always 1.00). Classify on the enum .value (with
                    # a plain-string fallback for any non-enum caller) so the
                    # long/short split is the true book. (Pass-3 runtime audit.)
                    _longs = sum(
                        1 for p in positions
                        if str(
                            getattr(getattr(p, "side", None), "value", getattr(p, "side", ""))
                            or ""
                        ).lower() in ("buy", "long")
                    )
                    _shorts = open_count - _longs
                    _skew = (max(_longs, _shorts) / open_count) if open_count else 0.0
                    log.info(
                        f"DIRECTION_CONCENTRATION | sym={symbol} "
                        f"book_longs={_longs} book_shorts={_shorts} "
                        f"skew={_skew:.2f} new_dir={trade.get('direction', '')} | "
                        f"book directional skew (breadth throttle 2.4 + per-coin "
                        f"direction govern this; observability only) | {ctx()}"
                    )
                    # Issue 7 (2026-06-08) — portfolio directional-drawdown
                    # breaker. Halt a NEW SAME-DIRECTION entry when the open book
                    # is over-concentrated in that direction AND the aggregate
                    # open (unrealized) loss across that direction's positions
                    # exceeds a fraction of equity — a correlated-drawdown circuit
                    # breaker so the one-directional book cannot keep adding risk
                    # while it bleeds (the -$204 correlated cluster). It NEVER
                    # closes open positions (runners are the edge) and only halts
                    # the over-concentrated direction (the opposite stays open,
                    # which rebalances the book) — a risk breaker, NOT a
                    # coin-selection gate or broad suppression. Default OFF until
                    # the operator sizes the threshold on more live data.
                    if (
                        bool(getattr(self._settings, "portfolio_dd_breaker_enabled", False))
                        and open_count >= int(getattr(
                            self._settings, "portfolio_dd_breaker_min_positions", 3))
                        and _skew >= float(getattr(
                            self._settings, "portfolio_dd_breaker_concentration", 0.80))
                    ):
                        _conc_dir = "buy" if _longs >= _shorts else "sell"
                        _new_norm = (
                            "buy"
                            if str(trade.get("direction", "")).lower() in ("buy", "long")
                            else "sell"
                        )
                        if _new_norm == _conc_dir:
                            _dir_open_loss = 0.0
                            for _p in positions:
                                _ps = str(
                                    getattr(getattr(_p, "side", None), "value",
                                            getattr(_p, "side", "")) or ""
                                ).lower()
                                _p_dir = "buy" if _ps in ("buy", "long") else "sell"
                                if _p_dir != _conc_dir:
                                    continue
                                _upnl = float(getattr(_p, "unrealized_pnl", 0.0) or 0.0)
                                if _upnl < 0:
                                    _dir_open_loss += _upnl
                            _fm7 = self._services.get("fund_manager")
                            _eq7 = float(getattr(
                                getattr(_fm7, "_account_state", None),
                                "total_equity", 0.0) or 0.0)
                            _loss_pct = float(getattr(
                                self._settings, "portfolio_dd_breaker_open_loss_pct", 1.5))
                            _budget = -(_loss_pct / 100.0) * _eq7 if _eq7 > 0 else 0.0
                            if _eq7 > 0 and _dir_open_loss <= _budget:
                                reason = (
                                    f"portfolio_directional_drawdown dir={_conc_dir} "
                                    f"skew={_skew:.2f} open_loss=${_dir_open_loss:.2f} "
                                    f"budget=${_budget:.2f}"
                                )
                                trade["_gate_rejected"] = reason
                                modifications.append(
                                    "REJECTED:portfolio_directional_drawdown")
                                log.warning(
                                    f"GATE_PORTFOLIO_DD_HALT | sym={symbol} "
                                    f"dir={_conc_dir} book_longs={_longs} "
                                    f"book_shorts={_shorts} skew={_skew:.2f} "
                                    f"dir_open_loss=${_dir_open_loss:.2f} "
                                    f"budget=${_budget:.2f} ({_loss_pct:.1f}% of "
                                    f"${_eq7:.0f}) | halting NEW same-direction "
                                    f"entry; open runners untouched | {ctx()}"
                                )
                                trade["_gate_modifications"] = modifications
                                return trade
                if open_count >= max_concurrent:
                    size = float(trade.get("size_usd", 600) or 600)
                    reduced = round(size * 0.3, 2)
                    trade["size_usd"] = reduced
                    modifications.append(f"size_reduced_max_pos={open_count}/{max_concurrent}")
        except Exception as e:
            # Phase 12.4 (lifecycle-logging-audit Gap 4.2-G1): DEBUG -> WARNING.
            # Position service exception during gate is rare but operationally
            # meaningful (silent failure may mean position cap not enforced).
            log.warning(f"GATE_POS_CHECK | sym={symbol} err='{str(e)[:60]}' | {ctx()}")

        # ═══ CHECK 4: Capital availability (conviction-weighted) ═══
        try:
            fund_mgr = self._services.get("fund_manager")
            available = 1000.0  # safe default
            if fund_mgr:
                state = getattr(fund_mgr, "_account_state", None)
                if state and hasattr(state, "available"):
                    available = float(state.available or 1000)

            # T2-2 / F14 zero-conviction reject (six-tier-fixes 2026-05-11).
            # Runs BEFORE the conviction-weight sizing block so a trade
            # with no structural backing is rejected outright rather
            # than sized down and executed. The reject combinator is
            # "all three signals at-or-below their thresholds" — with
            # default thresholds 0.0 / 0.0 / 0.0 this is exactly the
            # "no basis whatsoever" case (today's SOLUSDT xray_conf=
            # 0.00 setup_score=0.0 expected_rr=0.00). Operator can
            # tighten any of the three thresholds in settings.
            _xray = float(trade.get("_xray_confidence", 0) or 0)
            _setup = float(trade.get("_setup_score", 0) or 0)
            _rr = float(trade.get("_expected_rr", 0) or 0)
            _min_xray = float(
                getattr(self._settings, "min_xray_conf_for_trade", 0.0) or 0.0
            )
            _min_setup = float(
                getattr(self._settings, "min_setup_score_for_trade", 0.0) or 0.0
            )
            _min_rr = float(
                getattr(self._settings, "min_expected_rr_for_trade", 0.0) or 0.0
            )
            if (
                _xray <= _min_xray
                and _setup <= _min_setup
                and _rr <= _min_rr
            ):
                reason = (
                    f"zero_conviction xray={_xray:.2f}<={_min_xray:.2f} "
                    f"setup={_setup:.1f}<={_min_setup:.1f} "
                    f"rr={_rr:.2f}<={_min_rr:.2f}"
                )
                trade["_gate_rejected"] = reason
                modifications.append("REJECTED:zero_conviction")
                log.warning(
                    f"GATE_REJECT | layer=gate sym={symbol} reason=zero_conviction "
                    f"xray_conf={_xray:.2f} setup_score={_setup:.1f} "
                    f"expected_rr={_rr:.2f} | min_xray={_min_xray:.2f} "
                    f"min_setup={_min_setup:.1f} min_rr={_min_rr:.2f} | {ctx()}"
                )
                # Trade dict still returned so the caller's contract holds;
                # layer_manager skips on _gate_rejected before executing.
                trade["_gate_modifications"] = modifications
                return trade

            # Issue E17 (2026-05-27): structureless-but-scored reject. The
            # all-low AND reject above only fires when xray AND setup AND rr
            # are ALL at/below threshold (the all-zero case), so a coin with a
            # high setup_score but near-zero structural confidence survives it.
            # #7 caps a NONE setup to score<=49 and a weak-confidence match to
            # <=64 at the producer, so in normal operation such a coin never
            # reaches the gate; this is the belt-and-suspenders net for the
            # residual leak (classify_setup raised so #7's cap was skipped, or
            # a caller stamped a high score directly). Reject ONLY the
            # contradiction: structural confidence at/below a near-zero floor
            # AND score implausibly high (>= just above #7's B-cap of 64). A
            # legitimate aggressive entry always carries real confidence
            # (> floor), so it can NEVER match — no over-reject. Inert at the
            # safe defaults (conf_floor 0.0 = exactly-zero only, score_min 999
            # = never); config.toml sets the live values (0.05 / 65).
            _se_conf_floor = float(
                getattr(self._settings, "gate_structureless_conf_floor", 0.0) or 0.0
            )
            _se_score_min = float(
                getattr(self._settings, "gate_structureless_score_min", 999.0) or 999.0
            )
            if _xray <= _se_conf_floor and _setup >= _se_score_min:
                reason = (
                    f"structureless_high_score xray={_xray:.2f}<={_se_conf_floor:.2f} "
                    f"setup={_setup:.1f}>={_se_score_min:.1f} rr={_rr:.2f}"
                )
                trade["_gate_rejected"] = reason
                modifications.append("REJECTED:structureless_high_score")
                log.warning(
                    f"GATE_REJECT | layer=gate sym={symbol} "
                    f"reason=structureless_high_score xray_conf={_xray:.2f} "
                    f"setup_score={_setup:.1f} expected_rr={_rr:.2f} | "
                    f"conf_floor={_se_conf_floor:.2f} score_min={_se_score_min:.1f} | {ctx()}"
                )
                trade["_gate_modifications"] = modifications
                return trade

            # ═══ Entry-quality filters (win-rate enhancement, 2026-07-07) ═══
            # Three per-leg minimums from ENTRIES_QUALITY_DIAGNOSIS.md — each
            # flipped the audited 395-trade window positive, but all three are
            # ONE-WINDOW hypotheses, so this gate is as much measurement as
            # filter: every REJECT and every PASS logs the three raw values so
            # a later multi-window join (values vs realized PnL) can validate
            # or kill each leg independently. Values are stamped from the
            # deterministic CoinPackage in layer_manager (zero service calls
            # here). Fail-open contract: a value < 0 is the layer_manager
            # UNKNOWN sentinel (package/block missing) — that leg is SKIPPED,
            # never rejected, so degraded data plumbing cannot silently block
            # all trading. A stamped 0.0 is a REAL "no signal / unscored"
            # reading and legitimately fails its leg (the reject log carries
            # raw values so counterfactual analysis can spot 0.0-artifacts).
            # Rollback: [apex] entry_quality_filters_enabled = false.
            if bool(getattr(
                self._settings, "entry_quality_filters_enabled", False,
            )):
                _eq_sig = float(trade.get("_signal_confidence", -1.0) or -1.0) \
                    if trade.get("_signal_confidence") is not None else -1.0
                _eq_adx = float(trade.get("_entry_adx", -1.0) or -1.0) \
                    if trade.get("_entry_adx") is not None else -1.0
                _eq_min_sig = float(getattr(
                    self._settings, "entry_quality_signal_conf_min", 0.0,
                ) or 0.0)
                _eq_min_xray = float(getattr(
                    self._settings, "entry_quality_xray_conf_min", 0.0,
                ) or 0.0)
                _eq_min_adx = float(getattr(
                    self._settings, "entry_quality_adx_min", 0.0,
                ) or 0.0)
                _eq_vals = (
                    f"signal_conf={_eq_sig:.2f} xray_conf={_xray:.2f} "
                    f"adx={_eq_adx:.1f} | min_sig={_eq_min_sig:.2f} "
                    f"min_xray={_eq_min_xray:.2f} min_adx={_eq_min_adx:.1f}"
                )
                _eq_failed_leg = ""
                if _eq_min_sig > 0 and 0 <= _eq_sig < _eq_min_sig:
                    _eq_failed_leg = "entry_quality_signal_conf"
                elif _eq_min_xray > 0 and 0 <= _xray < _eq_min_xray:
                    _eq_failed_leg = "entry_quality_xray_conf"
                elif _eq_min_adx > 0 and 0 <= _eq_adx < _eq_min_adx:
                    _eq_failed_leg = "entry_quality_adx"
                if _eq_failed_leg:
                    reason = f"{_eq_failed_leg} {_eq_vals}"
                    trade["_gate_rejected"] = reason
                    modifications.append(f"REJECTED:{_eq_failed_leg}")
                    self._eq_reject_counts[_eq_failed_leg] = (
                        self._eq_reject_counts.get(_eq_failed_leg, 0) + 1
                    )
                    log.warning(
                        f"GATE_REJECT | layer=gate sym={symbol} "
                        f"reason={_eq_failed_leg} {_eq_vals} | {ctx()}"
                    )
                    trade["_gate_modifications"] = modifications
                    return trade
                # PASS breadcrumb — the accept-side half of the
                # counterfactual measurement contract. skipped_unknown
                # lists legs that failed open on the -1.0 sentinel.
                self._eq_pass_count += 1
                _eq_skipped = ",".join(
                    leg for leg, v in (
                        ("sig", _eq_sig), ("adx", _eq_adx),
                    ) if v < 0
                ) or "none"
                log.info(
                    f"GATE_ENTRY_QUALITY_PASS | sym={symbol} {_eq_vals} "
                    f"skipped_unknown={_eq_skipped} | {ctx()}"
                )

            # Conviction weighting: proven winners get more capital
            weight = 1.0
            if getattr(self._settings, "conviction_enabled", False):
                weight = await self._get_conviction_weight(symbol)

                # Layer 2 score modifier: secondary signal on top of profit-factor.
                # Phase 3A (2026-05-07) — read from layer_manager-stamped
                # _setup_score first; fall back to trade's own score for
                # back-compat with legacy callers that didn't carry the
                # CoinPackage breadcrumb.
                _signal_score = float(
                    trade.get("_setup_score", 0)
                    or trade.get("score", 0)
                    or trade.get("signal_score", 0)
                    or 0
                )
                # Issue E18 (2026-05-27): gate the A+ size boost on X-RAY
                # structural confidence. Without a floor a structureless
                # high-score coin (score >= threshold, confidence 0.0 — the #7
                # contradiction pattern, were it to slip past the producer cap)
                # would be UPSIZED, placing extra risk on the weakest setup.
                # The floor (default 0.0 = current behaviour; config sets 0.70)
                # withholds ONLY the boost multiplier — it never blocks the
                # trade (it still sizes off base conviction). Reads the same
                # _xray_confidence the structural ladder below uses.
                _ap_score = float(getattr(self._settings, "gate_a_plus_score_threshold", 80.0) or 80.0)
                _ap_mult = float(getattr(self._settings, "gate_a_plus_size_mult", 1.20) or 1.20)
                _ap_floor = float(getattr(self._settings, "gate_a_plus_conf_floor", 0.0) or 0.0)
                _ap_xconf = float(trade.get("_xray_confidence", 0) or 0)
                # Five-Fix Follow-Up — Fix 5 (2026-06-10): the A+ boost lives
                # under the same size-override switch as the optimizer's J5
                # sizing (operator decision: gate BOTH). Investigation nuance,
                # stated honestly: this boost multiplies the CHECK-4 conviction
                # WEIGHT — a capital-CEILING loosener — so it can never raise
                # the executed size above the brain's request; and under the
                # live brain_authoritative_sizing_enabled=true mode this whole
                # weight is computed then discarded (the margin-ceiling path
                # applies). Gating it here is therefore a zero-live-effect
                # consistency measure, active only on the legacy CHECK-4 path.
                _size_override_on = bool(getattr(
                    self._settings, "apex_size_override_enabled", False,
                ))
                if _signal_score >= _ap_score:
                    if not _size_override_on:
                        # Fix 5: switch off — boost inert by operator decision.
                        modifications.append(
                            f"A_PLUS_BOOST_SWITCHED_OFF(score={_signal_score:.0f}"
                            f">={_ap_score:.0f},mult={_ap_mult:.2f}x)"
                        )
                        log.info(
                            f"GATE_ADJUST | sym={symbol} A_PLUS_BOOST_SWITCHED_OFF "
                            f"score={_signal_score:.0f} mult_skipped={_ap_mult:.2f}x "
                            f"flag=apex_size_override_enabled=False | {ctx()}"
                        )
                    elif _ap_xconf >= _ap_floor:
                        weight *= _ap_mult  # A+ setup: size boost
                    else:
                        modifications.append(
                            f"A_PLUS_BOOST_WITHHELD(score={_signal_score:.0f}"
                            f">={_ap_score:.0f},xray_conf={_ap_xconf:.2f}<{_ap_floor:.2f})"
                        )
                        log.info(
                            f"GATE_ADJUST | sym={symbol} A_PLUS_BOOST_WITHHELD "
                            f"score={_signal_score:.0f} xray_conf={_ap_xconf:.2f} "
                            f"floor={_ap_floor:.2f} mult_skipped={_ap_mult:.2f}x | {ctx()}"
                        )
                elif _signal_score >= 68:
                    pass  # A setup: no change
                elif _signal_score >= 56:
                    weight *= 0.90  # B setup: 10% reduction
                elif _signal_score > 0:
                    weight *= 0.80  # C/D setup: 20% reduction

                # Sniper-Latency-Size Fix Phase 3B (2026-05-07) — feed
                # XRAY structural confidence and expected RR into the
                # conviction weight. Phase 0 showed 13 of 15 trades had
                # identical xray_conf=0.7 and identical setup_type yet
                # got 6 different sizes because the signals never
                # reached the sizing layer. Layer_manager stamps these
                # fields onto every trade dict before APEX/gate runs.
                _xray_conf = float(trade.get("_xray_confidence", 0) or 0)
                if _xray_conf >= 0.85:
                    weight *= 1.20  # high-conviction structural setup
                elif _xray_conf >= 0.70:
                    pass  # baseline (most setups land here)
                elif _xray_conf > 0:
                    weight *= 0.85  # weaker structure: 15% reduction
                # _xray_conf == 0 means no package data; leave neutral.

                _expected_rr = float(trade.get("_expected_rr", 0) or 0)
                if _expected_rr >= 3.0:
                    weight *= 1.15  # excellent RR: 15% boost
                elif _expected_rr >= 1.5:
                    pass  # standard RR
                elif _expected_rr > 0:
                    weight *= 0.90  # poor RR: 10% reduction

                # Final weight clamp [0.5, 2.5]. Ceiling raised from 2.0
                # to allow conviction amplification within the Phase 0
                # caps (TradeGate Check 0 1.5x, Check 7 floor $50,
                # exchange minimums) which still bound the absolute size.
                weight = max(0.5, min(weight, 2.5))

            # Brain-Authoritative Sizing (2026-05-31): when enabled, CHECK 4 is
            # NO LONGER a conviction SHRINK — the brain already sized this trade
            # as a deliberate share of usable capital (it is shown open/used/
            # usable funds in its prompt). CHECK 4 becomes a HARD per-trade
            # ceiling on the REAL available capital: a single trade can't
            # over-deploy, AND an in-cycle reservation accumulator stops the 2-4
            # trades of ONE brain cycle from collectively exceeding usable
            # capital. NOTE: fund_manager.in_use only refreshes on the FundManager
            # worker tick (~60s), so `available` is STALE within a cycle (it does
            # NOT reflect this cycle's own just-approved trades). We therefore
            # subtract a per-cycle `_cycle_reserved` running total (reset when the
            # cycle did changes) so each trade is capped to min(per-trade ceiling,
            # remaining budget). Without this, N trades each pass at available*pct
            # and total ~N*pct of usable. Flag off -> exact legacy behaviour.
            _brain_auth = bool(
                getattr(self._settings, "brain_authoritative_sizing_enabled", False)
            )
            _lev = max(int(trade.get("leverage", 1) or 1), 1)
            _ba_margin_mode = False  # True when the tiered (margin) path is used
            if _brain_auth:
                # SINGLE SOURCE OF TRUTH = tiered_capital (the SAME usable pool +
                # max_positions the brain is shown in its prompt). size_usd IS the
                # MARGIN (the cash committed); the executor builds the position as
                # qty = size_usd x leverage / price (strategy_worker.py:2974), so
                # notional = size_usd x leverage. The usable pool is therefore a
                # MARGIN budget and the whole book of max_positions must fit usable,
                # so the per-trade MARGIN ceiling = usable / max_positions. We cap
                # size_usd (margin) at that ceiling DIRECTLY — NO extra x leverage
                # (multiplying here was the double-leverage bug that opened ~3x
                # oversized positions). A per-cycle accumulator (reset on cycle did)
                # reserves MARGIN so one 5-min cycle cannot over-deploy past usable
                # (fund_manager.in_use is stale within a cycle). Replaces the old
                # FundManager-available x 0.40 path that disagreed with the brain.
                _cdid = trade.get("_cycle_did")
                if _cdid != self._brain_auth_cycle_did:
                    self._brain_auth_cycle_did = _cdid
                    self._brain_auth_cycle_reserved = 0.0  # MARGIN reserved this cycle
                _tcm = self._services.get("tiered_capital")
                _st = getattr(fund_mgr, "_account_state", None) if fund_mgr else None
                _eq = float(getattr(_st, "total_equity", 0.0) or 0.0)
                _dep_margin = float(getattr(_st, "in_use", 0.0) or 0.0)
                if _tcm is not None and _eq > 0:
                    _ba_margin_mode = True
                    _lim = _tcm.get_limits(_eq, _dep_margin)
                    _usable = float(_lim.usable_capital)
                    _max_pos = max(int(_lim.max_positions or 1), 1)
                    _avail_margin = max(0.0, float(_lim.available_for_trades))
                    _per_trade_margin = _usable / _max_pos
                    _remaining_margin = max(0.0, _avail_margin - self._brain_auth_cycle_reserved)
                    _allowed_margin = min(_per_trade_margin, _remaining_margin)
                    max_from_capital = _allowed_margin  # MARGIN ceiling (size_usd IS margin)
                else:
                    # Fallback (no tiered/equity, e.g. tests): legacy notional
                    # available x pct + a NOTIONAL accumulator.
                    _per_trade_pct = max(0.05, min(float(getattr(
                        self._settings, "brain_auth_per_trade_pct_of_available", 0.40) or 0.40), 1.0))
                    _remaining = max(0.0, available - self._brain_auth_cycle_reserved)
                    max_from_capital = min(available * _per_trade_pct, _remaining)
            else:
                base_pct = 0.4  # base 40% of available
                weighted_pct = base_pct * weight
                # Phase 3B (2026-05-07) — raise the upper clamp 0.40 -> 0.50
                # so the new conviction ceiling can actually express itself
                # at high-conviction setups; the 0.05 floor is preserved.
                weighted_pct = max(0.05, min(weighted_pct, 0.50))
                max_from_capital = available * weighted_pct

            size = float(trade.get("size_usd", 600) or 600)
            # Under brain-authoritative the in-cycle budget can legitimately reach
            # 0 (cycle fully reserved) -> clamp to 0 so the trade is SKIPPED
            # downstream (qty_zero), which is what prevents aggregate over-deploy.
            # Legacy keeps the `> 0` guard (available=0/unknown must not zero a trade).
            if size > max_from_capital and (max_from_capital > 0 or _brain_auth):
                trade["size_usd"] = round(max_from_capital, 2)
                if _brain_auth and _ba_margin_mode:
                    modifications.append(
                        f"fund_cap=${max_from_capital:.0f}margin"
                        f"(per_trade=${_per_trade_margin:.0f}=usable/{_max_pos},"
                        f"notional~${max_from_capital * _lev:.0f}@{_lev}x)"
                    )
                elif _brain_auth:
                    modifications.append(f"avail_cap=${max_from_capital:.0f}(fallback)")
                else:
                    modifications.append(f"conviction_cap=${max_from_capital:.0f}(w={weight:.1f}x)")
            # Sniper-Latency-Size Fix Phase 3D (2026-05-07) — sizing
            # breadcrumb. Captures the size after CHECK 4 (after the
            # conviction-weighted capital ceiling) so the unified
            # SIZE_DERIVATION event can distinguish "capital ceiling
            # held us back" from "we cleared the ceiling untouched".
            trade["_gate_post_check4_size_usd"] = float(trade.get("size_usd", 0) or 0)
            # In-cycle aggregate guard (brain-authoritative): reserve this
            # trade's approved size against the cycle budget so the NEXT trade in
            # the same cycle sees a tightened remaining. Reserving the CHECK-4
            # size (before CHECK 4b/5 may reduce it) is intentionally
            # conservative — actual deployed <= reserved, never over.
            if _brain_auth:
                # Reserve this trade's approved size against the cycle budget.
                # size_usd IS the MARGIN now (both tiered and fallback paths), so
                # reserve it directly — the cycle's cumulative MARGIN stays within
                # usable. (Pre-fix this divided by leverage because size_usd was
                # mistaken for notional.)
                _final = float(trade.get("size_usd", 0) or 0)
                self._brain_auth_cycle_reserved += _final
        except Exception as e:
            # Phase 12.4 (lifecycle-logging-audit Gap 4.3-G1): DEBUG -> WARNING.
            log.warning(f"GATE_CAP_CHECK | sym={symbol} err='{str(e)[:60]}' | {ctx()}")

        # ═══ CHECK 4b: Breadth RISK/SIZING brake (per-coin-authority Phase 5) ═══
        # The ONLY market-wide signal that survives the global-regime removal:
        # when the whole universe is directionally lopsided (high correlation ->
        # systemic risk), shrink size. SIZING ONLY — it never sets direction and
        # never selects a roster. Derived from the PER-COIN regime distribution
        # (not a single coin) via RegimeDetector.breadth_sizing(). Applied
        # DIRECTLY to size_usd (not just the Check-4 capital ceiling) so it bites
        # regardless of which constraint binds: the ceiling binds at the current
        # dormant-FundManager $1000 `available`, but would NOT at a real ~$47k
        # balance — a direct multiplier is robust to both. Fail-open (no brake on
        # error): this is a safety REDUCTION, not a safety gate.
        try:
            _det = self._services.get("regime_detector")
            if _det is not None and hasattr(_det, "breadth_sizing"):
                _bmult, _binfo = _det.breadth_sizing()
                if _bmult < 1.0:
                    _pre = float(trade.get("size_usd", 0) or 0)
                    trade["size_usd"] = round(_pre * _bmult, 2)
                    modifications.append(f"breadth_brake={_bmult:.2f}")
                    log.info(
                        f"BREADTH_OVERLAY | sym={symbol} size_mult={_bmult:.2f} "
                        f"${_pre:.0f}->${trade['size_usd']:.0f} "
                        f"down_share={_binfo.get('down_share', 0):.2f} "
                        f"up_share={_binfo.get('up_share', 0):.2f} "
                        f"lopsided={_binfo.get('lopsided', 0):.2f} "
                        f"classified={_binfo.get('classified', 0)} | {ctx()}"
                    )
        except Exception as _be:
            log.warning(f"BREADTH_BRAKE_FAIL | sym={symbol} err='{str(_be)[:60]}' | {ctx()}")

        # ═══ CHECK 5: Duplicate position on same symbol ═══
        try:
            pos_svc = self._services.get("position_service")
            if pos_svc:
                existing = await pos_svc.get_position(symbol)
                if existing and existing.size and existing.size > 0:
                    size = float(trade.get("size_usd", 600) or 600)
                    trade["size_usd"] = round(size * 0.5, 2)
                    modifications.append("size_halved_existing_pos")
        except Exception as e:
            # Phase 12.4 (lifecycle-logging-audit Gap 4.8-G1): DEBUG -> WARNING.
            log.warning(f"GATE_DUP_CHECK | sym={symbol} err='{str(e)[:60]}' | {ctx()}")

        # ═══ CHECK 6: 5-min per-(symbol, direction) reentry cooldown ═══
        # Issue 3 (2026-05-18). Replaces the legacy T2-1 same-direction
        # loss cooldown (the prior CHECK 6) and the J6/H4 reentry
        # learning gate (the prior CHECK 6b) with a single
        # deterministic time gate. Every close (win or loss, any
        # reason, any trigger) starts a 300s cooldown on
        # (symbol, direction) in TradeCoordinator. Opposite-direction
        # trades on the same symbol are not blocked. After 300s the
        # (symbol, direction) is eligible again — no DB lookup, no
        # condition matching, no H4 escapes — just a clock. The
        # simpler rule lets the brain plan around the cooldown
        # (surfaced in the strategist prompt via
        # ``get_active_reentry_cooldowns``) instead of repeatedly
        # proposing blocked trades that the old gate rejected as
        # ``same_conditions``. Matches the operator's stated intent
        # in ``IMPLEMENT_THREE_ISSUES_FIX.md`` Issue 3.
        try:
            coordinator = self._services.get("trade_coordinator")
            if coordinator is not None and hasattr(
                coordinator, "is_reentry_blocked",
            ):
                # Periodic sweep — cheap O(N) on the small dict; ensures
                # REENTRY_COOLDOWN_5MIN_CLEARED fires for entries that
                # nobody queries via is_reentry_blocked after expiry.
                if hasattr(coordinator, "clear_expired_reentry_cooldowns"):
                    coordinator.clear_expired_reentry_cooldowns()
                _new_dir = str(trade.get("direction", "") or "")
                blocked, remaining = coordinator.is_reentry_blocked(
                    symbol, _new_dir,
                )
                if blocked:
                    reason = f"reentry_cooldown_5min_{remaining}s"
                    trade["_gate_rejected"] = reason
                    modifications.append(f"REJECTED:{reason}")
                    log.warning(
                        f"REENTRY_COOLDOWN_5MIN_BLOCKED | layer=gate "
                        f"sym={symbol} dir={_new_dir} remaining_s={remaining} | "
                        f"{ctx()}"
                    )
        except Exception as e:
            log.warning(
                f"GATE_REENTRY_COOLDOWN_CHECK | sym={symbol} "
                f"err='{str(e)[:60]}' | {ctx()}"
            )

        # ═══ CHECK 7: position size — respect the brain's risk read (H3) ═══
        # H3 (2026-05-30): do NOT floor a deliberate small probe up to an
        # arbitrary $50. The brain's smaller size stands; a size genuinely below
        # the EXCHANGE minimum is SKIPPED downstream (qty<=0 -> TRADE_SKIP
        # rsn=qty_zero), never oversized into the weakest setups.
        _size_check = float(trade.get("size_usd", 600) or 600)
        if _size_check <= 0:
            modifications.append("size_nonpositive")

        # ═══════════════════════════════════════════════════════════
        # APEX GUARDRAILS (Checks 8-12) — prevent DeepSeek from choking trades
        # These only apply to APEX-optimized trades (have _apex_optimized flag)
        # ═══════════════════════════════════════════════════════════

        try:
          if trade.get("_apex_optimized"):
            direction = trade.get("direction", "Buy")
            apex_tp_mode = trade.get("_apex_tp_mode", "fixed")
            apex_confidence = float(trade.get("_apex_confidence", 1.0) or 1.0)
            orig_tp = float(trade.get("_apex_original_tp", 0) or 0)
            orig_sl = float(trade.get("_apex_original_sl", 0) or 0)
            orig_size = float(trade.get("_apex_original_size", 0) or 0)
            current_tp = float(trade.get("take_profit_price", 0) or 0)

            # Get current market price for trail activation floor calculation
            _entry_est = 0.0
            try:
                market_svc = self._services.get("market_service")
                if market_svc:
                    ticker = await market_svc.get_ticker(symbol)
                    if ticker:
                        _entry_est = ticker.last_price
            except Exception:
                pass

            # ═══ CHECK 8: TP Floor — APEX TP cannot go below Claude's TP ═══
            if getattr(self._settings, "gate_tp_floor_enabled", True) and orig_tp > 0 and current_tp > 0:
                tp_violated = False
                if direction in ("Buy", "Long"):
                    # Buy: higher TP = better. APEX TP should be >= Claude TP
                    if current_tp < orig_tp:
                        tp_violated = True
                else:
                    # Sell: lower TP = better. APEX TP should be <= Claude TP
                    if current_tp > orig_tp:
                        tp_violated = True

                if tp_violated:
                    trade["take_profit_price"] = orig_tp
                    modifications.append(
                        f"APEX_GUARDRAIL_TP_FLOOR(apex={format_price(current_tp)}->claude={format_price(orig_tp)})"
                    )

            # ═══ CHECK 9: Trail Activation Floor — min 15% of TP distance ═══
            # Phase 2 of dir-block-fix (2026-05-05) — Discovery 2: aligned
            # the in-code fallback (was 50.0) with the dataclass default
            # in APEXSettings.gate_trail_activation_floor_pct_of_tp (15.0)
            # and the value set in config.toml [apex] (15.0). Pre-fix the
            # fallback silently disagreed with the configured value, so any
            # deployment that loaded settings without this field hit a
            # 50 % floor instead of the operator-tuned 15 %.
            if apex_tp_mode in ("trail_only", "partial_trail") and _entry_est > 0:
                _floor_pct = getattr(self._settings, "gate_trail_activation_floor_pct_of_tp", 15.0)
                _tp_price = float(trade.get("take_profit_price", 0) or 0)
                if _tp_price > 0:
                    tp_distance_pct = abs(_tp_price - _entry_est) / _entry_est * 100
                    min_activation = tp_distance_pct * (_floor_pct / 100)
                    min_activation = max(min_activation, 0.5)  # absolute floor 0.5%
                    # Store for downstream TradePlan
                    existing_activation = float(trade.get("_apex_trail_activation_pct", 0) or 0)
                    if existing_activation < min_activation:
                        trade["_apex_trail_activation_pct"] = round(min_activation, 2)
                        modifications.append(
                            f"APEX_GUARDRAIL_TRAIL_ACT({existing_activation:.1f}%->{min_activation:.1f}%)"
                        )
                    elif existing_activation == 0:
                        trade["_apex_trail_activation_pct"] = round(min_activation, 2)

            # ═══ CHECK 10: Trail Distance Floor — never tighter than 40% ═══
            if apex_tp_mode in ("trail_only", "partial_trail"):
                _dist_floor = getattr(self._settings, "gate_trail_distance_floor_pct", 40.0)
                existing_dist = float(trade.get("_apex_trail_distance_pct", 0) or 0)
                if 0 < existing_dist < _dist_floor:
                    trade["_apex_trail_distance_pct"] = _dist_floor
                    modifications.append(
                        f"APEX_GUARDRAIL_TRAIL_DIST({existing_dist:.0f}%->{_dist_floor:.0f}%)"
                    )
                elif existing_dist == 0:
                    # Default to floor if not set
                    trade["_apex_trail_distance_pct"] = _dist_floor

            # ═══ CHECK 11: Mode Override — trail_only → trail_with_ceiling ═══
            if getattr(self._settings, "gate_mode_override_enabled", True):
                if apex_tp_mode == "trail_only" and orig_tp > 0:
                    trade["_apex_tp_mode"] = "trail_with_ceiling"
                    trade["take_profit_price"] = orig_tp  # Claude's TP as ceiling
                    modifications.append(
                        f"APEX_GUARDRAIL_MODE(trail_only->trail_with_ceiling)"
                    )

            # ═══ CHECK 12: Confidence-Based Size Scaling (DeepSeek calibration) ═══
            # DeepSeek returns moderate confidence (60-70%) for good optimizations.
            # TP/SL from APEX are validated by structural checks 1-11 — never revert
            # them based on confidence alone. Only scale SIZE for low confidence.
            _conf_floor = getattr(self._settings, "gate_confidence_floor", 0.50)
            if apex_confidence < _conf_floor:
                size = float(trade.get("size_usd", 600) or 600)
                scale = max(0.3, apex_confidence / _conf_floor)
                trade["size_usd"] = round(size * scale, 2)
                modifications.append(
                    f"APEX_CONF_SIZE({apex_confidence:.0%}<{_conf_floor:.0%},"
                    f"size_scale={scale:.0%})"
                )
        except Exception as e:
            # Phase 12.4 (lifecycle-logging-audit Gap 4.X): DEBUG -> WARNING.
            log.warning(f"GATE_GUARDRAIL_CHECK | sym={symbol} err='{str(e)[:80]}' | {ctx()}")

        # ═══ CHECK 13: R:R Ratio Validation ═══
        try:
            _sc = self._services.get("structure_cache")
            if _sc and hasattr(_sc, "get"):
                _sa = _sc.get(symbol)
                if _sa and _sa.structural_placement:
                    _rr = _sa.structural_placement.rr_ratio
                    if _rr is not None and _rr == 0.0:
                        size = float(trade.get("size_usd", 600) or 600)
                        trade["size_usd"] = round(size * 0.25, 2)
                        modifications.append("rr_zero_reduce_75%")
                    elif _rr is not None and 0 < _rr < 0.5:
                        size = float(trade.get("size_usd", 600) or 600)
                        trade["size_usd"] = round(size * 0.5, 2)
                        modifications.append(f"rr_low_{_rr:.1f}_reduce_50%")
        except Exception as e:
            # Phase 12.4 (lifecycle-logging-audit Gap 4.5-G1): DEBUG -> WARNING.
            log.warning(f"GATE_RR_CHECK | sym={symbol} err='{str(e)[:60]}' | {ctx()}")

        # ═══ CHECK 14: TP/SL Sanity ═══
        try:
            _tp = float(trade.get("take_profit_price", 0) or 0)
            _sl = float(trade.get("stop_loss_price") or trade.get("sl") or 0)
            if _tp > 0 and _sl > 0 and abs(_tp - _sl) / max(_tp, _sl) < 0.001:
                _dir = trade.get("direction", "Buy")
                if _dir in ("Buy", "Long"):
                    trade["take_profit_price"] = round(_tp * 1.02, 8)
                else:
                    trade["take_profit_price"] = round(_tp * 0.98, 8)
                modifications.append(
                    f"TPSL_IDENTICAL(tp={format_price(_tp)},sl={format_price(_sl)}->tp={format_price(trade['take_profit_price'])})"
                )
        except Exception as e:
            # Phase 12.4 (lifecycle-logging-audit Gap 4.6-G2): DEBUG -> WARNING.
            log.warning(f"GATE_TPSL_CHECK | sym={symbol} err='{str(e)[:60]}' | {ctx()}")

        # ═══ Attach gate metadata and log ═══
        _gate_el_ms = (time.time() - _gate_t0) * 1000
        trade["_gate_validation_ms"] = _gate_el_ms
        if modifications:
            trade["_gate_adjustments"] = ", ".join(modifications)
            log.info(
                f"GATE_ADJUST | sym={symbol} "
                f"changes=[{', '.join(modifications)}] | {ctx()}"
            )
        else:
            log.debug(f"GATE_PASS | sym={symbol} no_changes | {ctx()}")

        # Observability: overall gate timing (Phase 7 of logging overhaul)
        log.info(
            f"GATE_TIMING | sym={symbol} el={_gate_el_ms:.0f}ms "
            f"modifications={len(modifications)} | {ctx()}"
        )
        if _gate_el_ms > 500:
            log.warning(
                f"GATE_TIMING_SLOW | sym={symbol} el={_gate_el_ms:.0f}ms "
                f"modifications={len(modifications)} — inspect I/O checks (position/fund/market) | {ctx()}"
            )

        return trade

    # ─── Conviction Weight Helper ──────────────────────────────────────

    async def _get_conviction_weight(self, symbol: str) -> float:
        """Compute conviction weight for a coin based on TIAS profit factor.

        Uses a 5-minute cache to avoid DB spam. Queries last 20 trades
        for the symbol from TIAS repository, filtered by current regime.

        Returns:
            Weight multiplier: 0.5x (loser) to 2.0x (proven winner).
        """
        # Resolve current regime for regime-filtered query
        _regime: str | None = None
        try:
            detector = self._services.get("regime_detector")
            if detector:
                coin_regime = detector.get_coin_regime(symbol)
                # Definitive-fix Phase 7 (2026-04-28) — emit per-call
                # cache-query telemetry so REGIME_FALLBACK frequency can
                # be correlated with the cold-start window. ``hit`` is
                # True only when the per-coin cache was populated for
                # this symbol; ``cache_size`` shows whether the cache
                # is even warm yet.
                _hit = coin_regime is not None
                _cache_size = (
                    len(getattr(detector, "_per_coin_regimes", {}) or {})
                )
                _ready = bool(getattr(detector, "is_ready", lambda: True)())
                log.info(
                    f"REGIME_CACHE_QUERY | sym={symbol} reader=apex_gate "
                    f"hit={_hit} ready={_ready} cache_size={_cache_size} | {ctx()}"
                )
                if coin_regime is not None:
                    _regime = str(coin_regime.regime.value)
                else:
                    # Per-coin-authority Phase 2 (2026-05-29): per-coin regime
                    # unavailable -> UNKNOWN, NEVER the global BTC regime (the
                    # back-door coupling per-coin authority removes). Conviction
                    # cache keys on ':unknown'; the WR pool has no 'unknown' rows
                    # so conviction defaults — the honest behaviour for a coin we
                    # cannot place in a regime.
                    _regime = "unknown"  # MarketRegime.UNKNOWN.value
                    log.warning(
                        "REGIME_FALLBACK | sym={sym} source=gate | "
                        "per-coin unavailable, using UNKNOWN (not global) | {ctx}",
                        sym=symbol, ctx=ctx(),
                    )
        except Exception:
            pass  # Regime is optional — fallback to all-regime query

        # Check cache (keyed by symbol + regime)
        _cache_key = f"{symbol}:{_regime or 'all'}"
        cached = self._conviction_cache.get(_cache_key)
        if cached:
            weight, ts = cached
            if time.time() - ts < self._conviction_cache_ttl:
                return weight

        # Query TIAS history (regime-filtered)
        try:
            tias_repo = self._services.get("tias_repo")
            if not tias_repo:
                return 1.0

            min_trades = getattr(self._settings, "conviction_min_trades", 3)
            data = await tias_repo.get_symbol_full_history(
                symbol, limit=20, regime=_regime,
            )
            total = data.get("total", 0)

            # If regime-filtered data is too sparse, fall back to all-regime
            if total < min_trades and _regime:
                data = await tias_repo.get_symbol_full_history(symbol, limit=20)
                total = data.get("total", 0)

            if total < min_trades:
                weight = 0.75  # Not enough history — cautious default
                self._conviction_cache[_cache_key] = (weight, time.time())
                log.info(
                    f"CONVICTION_WEIGHT | sym={symbol} regime={_regime or 'all'} "
                    f"trades={total} (< min {min_trades}) "
                    f"weight=0.75x(default) | {ctx()}"
                )
                return weight

            # Compute profit factor from trade list
            trades = data.get("trades", [])
            total_won = sum(
                float(t.get("pnl_usd", 0) or 0)
                for t in trades
                if float(t.get("pnl_usd", 0) or 0) > 0
            )
            total_lost = abs(sum(
                float(t.get("pnl_usd", 0) or 0)
                for t in trades
                if float(t.get("pnl_usd", 0) or 0) < 0
            ))

            if total_lost == 0:
                profit_factor = 10.0  # Cap at 10 to avoid infinity
            else:
                profit_factor = total_won / total_lost

            # Map profit factor to weight
            if profit_factor > 3.0:
                weight = 2.0
            elif profit_factor > 2.0:
                weight = 1.5
            elif profit_factor > 1.0:
                weight = 1.0
            elif profit_factor > 0.5:
                weight = 0.7
            else:
                weight = 0.5

            self._conviction_cache[_cache_key] = (weight, time.time())
            log.info(
                f"CONVICTION_WEIGHT | sym={symbol} regime={_regime or 'all'} "
                f"pf={profit_factor:.2f} won=${total_won:.2f} "
                f"lost=${total_lost:.2f} trades={total} "
                f"weight={weight}x | {ctx()}"
            )
            return weight

        except Exception as e:
            # Phase 12.4 (lifecycle-logging-audit Gap 4.3-G1): DEBUG -> WARNING.
            log.warning(f"CONVICTION_WEIGHT_FAIL | sym={symbol} err='{str(e)[:80]}' | {ctx()}")
            return 1.0
