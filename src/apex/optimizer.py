"""APEX TradeOptimizer — orchestrates the full DeepSeek optimization pipeline.

Takes Claude's trade directive and returns DeepSeek-optimized parameters for
maximum profit extraction. If DeepSeek fails for ANY reason, returns Claude's
original parameters unchanged. APEX failure NEVER blocks a trade.

Flow:
  1. Check enabled
  2. Translate directive keys (stop_loss_price → sl, etc.)
  3. Assemble intelligence package (IntelligenceAssembler)
  4. Check min TIAS data threshold
  5. Build prompt (APEX_SYSTEM_PROMPT + build_apex_user_prompt)
  6. Call DeepSeek (DeepSeekClient)
  7. Parse response (→ OptimizedTrade)
  8. Apply safety constraints (hard clamps)
  9. Log result (APEX_OK / APEX_FLIP)
 10. Return OptimizedTrade

Design mirrors src/tias/analyzer.py (TradeAnalyzer) with APEX-specific
naming, no fallback model, and is_fallback flag instead of retryable errors.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Optional

from src.apex.models import OptimizedTrade
from src.apex.prompts import APEX_SYSTEM_PROMPT, build_apex_user_prompt
from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("apex")


# Issue B Phase 3c (2026-05-08) — defensive redaction of auth-token-shaped
# substrings before emitting raw response bodies into log lines. The
# OpenRouter response body should never contain auth headers, but gateway
# error replies have historically been surprising; this is belt-and-braces.
_AUTH_TOKEN_RE = re.compile(
    r"(?i)(Bearer\s+[A-Za-z0-9._\-]+|"
    r'"Authorization"\s*:\s*"[^"]*"|'
    r"sk-[A-Za-z0-9]{20,})",
)


def _redact_auth_tokens(text: str) -> str:
    """Replace any auth-token-shaped substrings with ``[REDACTED]``.

    Catches three common shapes seen in HTTP responses or error
    payloads: ``Bearer <token>``, ``"Authorization": "..."`` JSON
    fragments, and OpenRouter/OpenAI-style ``sk-`` prefixes. Match is
    case-insensitive and leaves all other content untouched.

    Args:
        text: Raw response body or other free-form string.

    Returns:
        The same string with token-shaped substrings replaced by
        ``[REDACTED]``. Empty input returns empty.
    """
    if not text:
        return ""
    return _AUTH_TOKEN_RE.sub("[REDACTED]", text)


class TradeOptimizer:
    """APEX trade optimizer — takes Claude's directive, returns DeepSeek-optimized params.

    If DeepSeek fails for any reason (timeout, bad JSON, API error), the optimizer
    returns an OptimizedTrade with is_fallback=True that preserves Claude's
    original parameters. APEX failure never blocks trade execution.

    Args:
        qwen_client: QwenClient instance (DeepSeek via OpenRouter)
        assembler: IntelligenceAssembler instance (Phase 1)
        settings: APEXSettings from config
    """

    def __init__(self, qwen_client: Any, assembler: Any, settings: Any) -> None:
        self._client = qwen_client
        self._assembler = assembler
        self._settings = settings
        # J5 (2026-05-14) — late-bound account-equity getter. The
        # dynamic per-trade size cap reads trading_capital from this
        # callable when ``apex_size_cap_pct_of_equity > 0``. When the
        # getter is None (test fixtures, legacy callers), the cap
        # falls back to the static ``max_position_size_usd`` and
        # behaviour is byte-equivalent to pre-J5. Wired by
        # WorkerManager via ``attach_account_state_getter`` after
        # both fund_manager and apex_optimizer exist.
        self._account_state_getter: Any = None

        # Cumulative stats for health reporting
        self._optimized_count: int = 0
        self._fallback_count: int = 0
        self._flip_count: int = 0
        self._lock_override_count: int = 0
        self._total_time_ms: int = 0

        # R2 direction-fix (2026-05-17) — most recent composite-score
        # components from _check_direction_lock. Stamped each call so
        # the caller can emit APEX_LOCK_DECISION_EXPLAINED with the
        # full breakdown. Per-instance, single-threaded per the worker
        # dispatch contract (one optimize() call at a time).
        self._last_lock_components: dict[str, float] = {}

        # Layer 1 Defect 7 boot self-check — the TP cap multiplier map
        # exists in three places (settings.py tp_cap_multiplier_by_class,
        # this optimizer's _apply_settings_clamps fallback, and the
        # apex/models.py CoinData formatter that builds the TP_CAP value
        # shown to DeepSeek). All three must agree or the model will
        # self-limit against a tighter cap than the optimizer enforces
        # (or vice versa). Loud-error on divergence; the maps drift
        # silently otherwise.
        try:
            from src.apex.models import _CAP_MULT_MAP_DISPLAY  # type: ignore

            _settings_map = (
                getattr(self._settings, "tp_cap_multiplier_by_class", None)
                or {"dead": 1.4, "low": 1.5, "medium": 1.6,
                    "high": 1.8, "extreme": 2.0}
            )
            _diverged = [
                k for k in _settings_map
                if abs(
                    float(_settings_map[k])
                    - float(_CAP_MULT_MAP_DISPLAY.get(k, 0.0))
                ) > 1e-9
            ]
            if _diverged:
                log.error(
                    f"BOOT_TP_CAP_MISMATCH | "
                    f"settings={_settings_map} "
                    f"display={_CAP_MULT_MAP_DISPLAY} "
                    f"diverged_classes={','.join(_diverged)} | {ctx()}"
                )
            else:
                log.info(
                    f"BOOT_TP_CAP_RECONCILED | "
                    f"map={_settings_map} | {ctx()}"
                )
        except Exception as _e:
            log.debug(
                f"BOOT_TP_CAP_CHECK_FAIL | err='{str(_e)[:80]}' | {ctx()}"
            )
        # Five-Fix Follow-Up — Fix 5 (2026-06-10) — boot sentinel for the
        # size-override switch (Rule 9/12: every new config key gets one).
        # OFF (default) = the brain's parsed size_usd is authoritative: the
        # J5 dynamic-sizing adoption of the optimizer-proposed size and the
        # gate's A+ ceiling boost are both inert; every safety validation
        # (gate CHECK 0/1/2/4/5, breadth-brake, enforcer, exchange minimum)
        # stays in force. ON = the pre-fix sizing behaviour, byte-identical.
        _size_override_on = bool(getattr(
            self._settings, "apex_size_override_enabled", False,
        ))
        log.info(
            f"BOOT_APEX_SIZE_OVERRIDE_{'ON' if _size_override_on else 'OFF'} | "
            f"flag=apex_size_override_enabled={_size_override_on} "
            f"effect={'legacy_j5_dynamic_sizing_and_a_plus_boost' if _size_override_on else 'brain_size_authoritative_unmodified'} "
            f"gate_cap_mult={float(getattr(self._settings, 'gate_apex_size_cap_mult', 1.5) or 1.5):.2f}x "
            f"a_plus_mult={float(getattr(self._settings, 'gate_a_plus_size_mult', 1.20) or 1.20):.2f}x "
            f"| {ctx()}"
        )

    def attach_account_state_getter(self, getter: Any) -> None:
        """Wire a callable that returns the current trading capital.

        Used by the J5 dynamic-cap path (see
        :meth:`_apply_settings_clamps`). The callable returns a float
        (USD) or None when unavailable; the optimizer falls back to
        the static cap when the getter is unwired or returns None.

        Late-bound to avoid the boot-time circular DI between
        TradeOptimizer and FundManager (fund_manager is wired after
        the optimizer is constructed). Idempotent — subsequent calls
        rebind the getter.
        """
        self._account_state_getter = getter

    async def optimize(self, directive: dict, plan: Any = None) -> OptimizedTrade:
        """Optimize a Claude directive using DeepSeek + TIAS intelligence.

        Args:
            directive: Trade directive dict from Claude's strategic plan.
                Expected keys: symbol, direction, stop_loss_price,
                take_profit_price, leverage, size_usd, reasoning, score.
            plan: StrategicPlan object (optional). Provides plan.market_view
                for the DeepSeek prompt's plan_view field.

        Returns:
            OptimizedTrade with DeepSeek's optimized parameters.
            If optimization fails for any reason, returns an OptimizedTrade
            with is_fallback=True and Claude's original parameters.
        """
        symbol = directive.get("symbol", "?")
        _opt_t0 = time.time()
        _assemble_ms = 0.0
        _deepseek_ms = 0.0
        _parse_ms = 0.0
        _constraints_ms = 0.0
        # Issue 1 fix (2026-05-11): initialise the lock-state tuple before
        # the try block so the exception handler can always reference it,
        # regardless of whether _check_direction_lock had a chance to run.
        # Reassigned at the actual lock decision below.
        _apex_lock_state: tuple[bool, str] = (False, "")

        try:
            # Step 1: Check enabled
            if not self._settings.enabled:
                return self._fallback(directive, "disabled")

            # Step 2: Translate directive keys for the assembler.
            # The assembler was built to accept 'sl'/'tp' keys, but Claude Brain
            # outputs 'stop_loss_price'/'take_profit_price'. Also enrich with
            # plan-level context not present in the individual trade dict.
            translated = dict(directive)
            translated["sl"] = (
                directive.get("stop_loss_price")
                or directive.get("sl")
                or 0.0
            )
            translated["tp"] = (
                directive.get("take_profit_price")
                or directive.get("tp")
                or 0.0
            )
            translated["plan_view"] = (
                getattr(plan, "market_view", "") if plan else directive.get("plan_view", "")
            )
            translated["signal_score"] = (
                directive.get("score") or directive.get("signal_score")
            )

            # Step 3: Assemble the 4-section intelligence package
            _t = time.time()
            package = await self._assembler.assemble(translated)
            _assemble_ms = (time.time() - _t) * 1000

            # Step 3.5: Price validation — never send $0 to DeepSeek
            if package.coin_data.current_price <= 0:
                log.warning(
                    f"APEX_SKIP_NO_PRICE | sym={symbol} | "
                    f"No valid price available — using Claude defaults | {ctx()}"
                )
                return self._fallback(directive, "no_valid_price")

            # Step 4: Three-tier data threshold check.
            # Tier 1: coin-specific history → optimize normally
            # Tier 2: regime-wide history → optimize with regime context
            # Tier 3: no data anywhere → use Claude's defaults
            #
            # Phase 3 session-stability: emit one unified ``APEX_TIER`` log per
            # optimization so operators can see tier distribution at a glance.
            # The pre-existing ``APEX_REGIME`` and ``APEX_DEFAULT`` lines are
            # retained — they carry extra fields (e.g. pattern summary input)
            # and are relied on by downstream analysis scripts.
            min_trades = self._settings.min_tias_trades_for_optimization
            min_regime = getattr(self._settings, "min_regime_trades_for_fallback", 10)
            symbol_trades = package.symbol_history.total_trades
            regime_trades = package.situation_data.total_trades_in_condition
            regime_name = package.situation_data.regime

            if symbol_trades >= min_trades:
                # Tier 1: sufficient coin-specific data, proceed normally
                log.info(
                    f"APEX_TIER | tier=1 sym={symbol} "
                    f"sym_trades={symbol_trades} regime_trades={regime_trades} "
                    f"regime={regime_name} action=full_optimize | {ctx()}"
                )
            elif regime_trades >= min_regime:
                # Tier 2: use regime-wide data — inject summary for DeepSeek
                log.info(
                    f"APEX_TIER | tier=2 sym={symbol} "
                    f"sym_trades={symbol_trades} regime_trades={regime_trades} "
                    f"regime={regime_name} action=regime_fallback | {ctx()}"
                )
                log.info(
                    f"APEX_REGIME | sym={symbol} "
                    f"sym_trades={symbol_trades} regime_trades={regime_trades} "
                    f"regime={regime_name} | {ctx()}"
                )
                package.symbol_history.pattern_summary = (
                    f"No coin-specific history ({symbol_trades} trades). "
                    f"Using regime-wide data: {regime_trades} trades in "
                    f"{regime_name} regime.\n"
                    f"Buy WR: {package.situation_data.buy_win_rate:.1f}%, "
                    f"Sell WR: {package.situation_data.sell_win_rate:.1f}%, "
                    f"Bias: {package.situation_data.direction_bias}"
                )
            else:
                # Tier 3: no meaningful data anywhere
                log.info(
                    f"APEX_TIER | tier=3 sym={symbol} "
                    f"sym_trades={symbol_trades} regime_trades={regime_trades} "
                    f"regime={regime_name} action=use_claude_defaults | {ctx()}"
                )
                log.info(
                    f"APEX_DEFAULT | sym={symbol} "
                    f"sym_trades={symbol_trades} regime_trades={regime_trades} "
                    f"using_defaults=Y | {ctx()}"
                )
                return self._fallback(directive, "insufficient_data_and_regime")

            # ═══ DIRECTION LOCK GATE ═══
            # Code-level enforcement: decide in CODE whether DeepSeek can flip direction.
            # Same pattern as X-RAY SKIP enforcement — code gates, not LLM judgment.
            claude_direction = directive.get("direction", "Buy")
            # Issue 2.3 (2026-06-07): capture the brain's directed leverage so the
            # leverage-override kill-switch below can honor it (0 = unspecified).
            claude_leverage = int(directive.get("leverage", 0) or 0)
            regime = package.situation_data.regime
            direction_locked, lock_reason = self._check_direction_lock(
                package, claude_direction, regime,
            )
            # R2 direction-fix (2026-05-17) — emit the composite-score
            # breakdown for every call regardless of verdict so the
            # operator can audit each lock decision. Fires before
            # APEX_DIR_LOCK so the breakdown and verdict appear in the
            # same log slice. Read-only; never mutates state.
            _lc = self._last_lock_components or {}
            _td_for_log = ""
            try:
                _td_for_log = str(
                    getattr(
                        getattr(package, "structural_data", None),
                        "trade_direction", "",
                    ) or ""
                )
            except Exception:
                _td_for_log = ""
            log.info(
                f"APEX_LOCK_DECISION_EXPLAINED | sym={symbol} "
                f"dir={claude_direction} regime={regime} "
                f"trade_direction={_td_for_log or 'na'} "
                f"regime_signal={_lc.get('regime', 0.0)} "
                f"structural={_lc.get('structural', 0.0)} "
                f"trade_dir_signal={_lc.get('trade_dir', 0.0)} "
                f"wr={_lc.get('wr', 0.0)} "
                f"symbol_evidence={_lc.get('symbol_evidence', 0.0)} "
                f"score={_lc.get('score', 0.0)} "
                f"threshold={_lc.get('threshold', 0.0)} "
                f"verdict={'fired' if direction_locked else 'bailed'} | "
                f"{ctx()}"
            )
            if direction_locked:
                log.info(
                    f"APEX_DIR_LOCK | sym={symbol} dir={claude_direction} "
                    f"regime={regime} reason='{lock_reason}' | {ctx()}"
                )
                # Belt: inject lock instruction into directive reasoning for DeepSeek
                package.directive.reasoning = (
                    f"[DIRECTION LOCKED: {claude_direction} — {lock_reason}. "
                    f"Do NOT change direction.] "
                    + (package.directive.reasoning or "")
                )
            # Issue 1 fix (2026-05-11). Capture the lock decision so it can be
            # stamped onto the OptimizedTrade after parse, then plumbed through
            # layer_manager → strategy_worker to suppress the XRAY downstream
            # flip when APEX has explicitly locked the direction. The lock
            # remains advisory at the source dict; the contract is enforced
            # in strategy_worker._execute_claude_trade.
            _apex_lock_state = (bool(direction_locked), str(lock_reason or ""))

            # ═══ VOLATILITY TP CAP (per-class multiplier) ═══
            # Phase 5 of dir-block-fix (2026-05-05): multiplier raised
            # per class (medium 1.3 → 1.6, high 1.4 → 1.8, extreme
            # 1.5 → 2.0) so Qwen can recommend larger TPs when
            # structure supports them. A hard upper-bound ceiling
            # (apex_tp_cap_hard_ceiling_pct, default 5.0 %) prevents
            # wild outliers regardless of class multiplier. Unknown
            # class → falls back to medium (1.6).
            _tp_cap = None
            _tp_cap_cls = getattr(package.coin_data, "volatility_class", None) or "medium"
            if package.coin_data.recommended_tp_pct is not None:
                _cap_mult_map = (
                    getattr(self._settings, "tp_cap_multiplier_by_class", None)
                    or {"dead": 1.4, "low": 1.5, "medium": 1.6,
                        "high": 1.8, "extreme": 2.0}
                )
                _tp_cap_mult = _cap_mult_map.get(_tp_cap_cls, 1.6)
                _hard_ceiling = float(getattr(
                    self._settings, "apex_tp_cap_hard_ceiling_pct", 5.0,
                ))
                _tp_cap = round(
                    min(
                        package.coin_data.recommended_tp_pct * _tp_cap_mult,
                        _hard_ceiling,
                    ),
                    2,
                )

            # Step 5: Build prompts
            user_prompt = build_apex_user_prompt(package)

            # Step 6: Call DeepSeek via OpenRouter — Issue B fix
            # (2026-05-08) wraps the call in a single bounded retry
            # for ``retryable=True`` errors. The 2026-05-08 audit
            # window saw 4 ``no choices`` events clustered in 16
            # minutes (3 of them within 4 seconds at 15:49) — a
            # textbook transient upstream wobble that a single retry
            # would have likely smoothed. Non-retryable errors (HTTP
            # non-200, timeout, connection error) fall through
            # immediately. After retry exhaustion the outer ``except``
            # catches and falls back to Claude's original directive,
            # preserving the architectural "APEX never blocks a
            # trade" invariant.
            _max_attempts = max(1, int(getattr(
                self._settings, "apex_max_attempts", 2,
            )))
            _retry_backoff_s = float(getattr(
                self._settings, "apex_retry_backoff_seconds", 0.7,
            ))
            _t = time.time()
            result = None
            _last_exc: Exception | None = None
            for _attempt in range(1, _max_attempts + 1):
                try:
                    result = await self._client.optimize(
                        system_prompt=APEX_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        model=self._settings.model,
                        temperature=self._settings.temperature,
                        max_tokens=self._settings.max_tokens,
                        timeout_seconds=self._settings.timeout_seconds,
                    )
                    break
                except Exception as _exc:
                    _last_exc = _exc
                    _is_retryable = bool(getattr(_exc, "retryable", False))
                    if _is_retryable and _attempt < _max_attempts:
                        log.warning(
                            f"APEX_RETRY_ATTEMPT | sym={symbol} "
                            f"attempt={_attempt} max={_max_attempts} "
                            f"backoff_ms={int(_retry_backoff_s * 1000)} "
                            f"err='{str(_exc)[:120]}' | {ctx()}"
                        )
                        await asyncio.sleep(_retry_backoff_s)
                        continue
                    raise
            _deepseek_ms = (time.time() - _t) * 1000

            # Step 7: Parse DeepSeek's JSON response into OptimizedTrade
            _t = time.time()
            optimized = self._parse_response(result, directive)
            _parse_ms = (time.time() - _t) * 1000

            # ═══ ENFORCE DIRECTION LOCK ═══
            # Suspenders: if DeepSeek flipped despite the lock instruction, override back.
            # PRIMARY Sell-Bias Fix (2026-05-11): capture
            # ``_dir_lock_override_fired`` so the unified APEX_FLIP_DECISION
            # log later can attribute the decision to this gate.
            _dir_lock_override_fired = False
            _qwen_dir_before_lock = optimized.direction

            # ═══ APEX Direction-Flip Switch (IMPLEMENT_APEX_FLIP_SWITCH) ═══
            # Master gate for APEX's direction-REVERSAL only. When disabled,
            # any model-proposed flip is reverted to the brain's direction
            # HERE, before the four downstream flip gates run (they then
            # no-op since was_flipped is False / direction == claude). APEX's
            # trade-optimization (SL, TP, size, leverage, analysis) is
            # untouched and still applies to the brain's direction —
            # identical in shape to the existing flip-revert gates below.
            # Default OFF (operator decision 2026-05-25, symmetric with the
            # X-RAY switch). getattr fallback False = failure-safe to no-flip.
            _apex_flip_enabled = bool(getattr(
                self._settings, "apex_dir_flip_enabled", False,
            ))
            _flip_switch_suppressed = False
            if not _apex_flip_enabled and optimized.direction != claude_direction:
                log.warning(
                    f"APEX_FLIP_SWITCH_OFF | sym={symbol} "
                    f"brain_dir={claude_direction} "
                    f"qwen_dir={optimized.direction} regime={regime} | "
                    f"flip suppressed by switch — brain direction stands, "
                    f"optimization preserved | {ctx()}"
                )
                optimized.direction = claude_direction
                optimized.was_flipped = False
                optimized.reasoning = (
                    "[FLIP DISABLED by switch] " + optimized.reasoning
                )
                _flip_switch_suppressed = True

            # Issue 2.3 (2026-06-07): APEX leverage-override kill-switch,
            # symmetric with apex_dir_flip_enabled. The optimizer LLM can output
            # its own leverage which may EXCEED the brain's directed leverage
            # (live: brain lev3 executed lev5, amplifying the loss). When the
            # override is DISABLED (default) the brain's directed leverage stands;
            # APEX's SL/TP/size optimization is untouched. getattr fallback
            # False = failure-safe to honor the brain.
            _apex_lev_override_enabled = bool(getattr(
                self._settings, "apex_leverage_override_enabled", False,
            ))
            if (
                not _apex_lev_override_enabled
                and claude_leverage > 0
                and int(optimized.leverage) != int(claude_leverage)
            ):
                log.warning(
                    f"APEX_LEVERAGE_OVERRIDE_OFF | sym={symbol} "
                    f"brain_lev={claude_leverage} qwen_lev={optimized.leverage} | "
                    f"leverage override suppressed by switch — brain leverage "
                    f"stands, optimization preserved | {ctx()}"
                )
                optimized.leverage = int(claude_leverage)
                optimized.reasoning = (
                    "[LEV OVERRIDE DISABLED by switch] " + optimized.reasoning
                )

            if direction_locked and optimized.direction != claude_direction:
                log.warning(
                    f"APEX_DIR_LOCK_OVERRIDE | sym={symbol} "
                    f"qwen_tried={optimized.direction} locked_to={claude_direction} "
                    f"regime={regime} | {ctx()}"
                )
                # R2 direction-fix Rule 6 (spec-mandated events) — when
                # the lock is in effect and Qwen's flip is reverted,
                # this is an OVERRIDE_DENIED event. The composite-score
                # lock made the decision; the components dict was
                # already emitted via APEX_LOCK_DECISION_EXPLAINED. The
                # DENIED event is the per-attempt outcome line the
                # operator's audit playbook greps for.
                _lc = self._last_lock_components or {}
                log.warning(
                    f"APEX_LOCK_OVERRIDE_DENIED | sym={symbol} "
                    f"qwen_tried={optimized.direction} locked_to={claude_direction} "
                    f"regime={regime} composite_score={_lc.get('score', 0.0)} "
                    f"threshold={_lc.get('threshold', 0.0)} "
                    f"verdict=denied_lock_holds | {ctx()}"
                )
                optimized.direction = claude_direction
                optimized.was_flipped = False
                optimized.reasoning = (
                    f"[DIR LOCKED to {claude_direction}] " + optimized.reasoning
                )
                self._lock_override_count += 1
                _dir_lock_override_fired = True
            elif direction_locked and optimized.direction == claude_direction:
                # R2 direction-fix Rule 6 — Qwen kept the locked direction
                # (no flip attempt or attempt aligned with the lock).
                # Not strictly an "override granted" since no override was
                # needed; emit GRANTED only when a flip was attempted AND
                # the composite score permitted it. Skip the GRANTED case
                # in this branch; it fires below when not locked.
                pass
            elif not direction_locked and optimized.direction != claude_direction:
                # Lock bailed AND Qwen flipped — the composite score
                # permitted the flip (the score >= threshold means the
                # evidence supported Qwen's direction). This is an
                # OVERRIDE_GRANTED event per spec Rule 6.
                _lc = self._last_lock_components or {}
                log.info(
                    f"APEX_LOCK_OVERRIDE_GRANTED | sym={symbol} "
                    f"brain_dir={claude_direction} "
                    f"qwen_dir={optimized.direction} "
                    f"regime={regime} composite_score={_lc.get('score', 0.0)} "
                    f"threshold={_lc.get('threshold', 0.0)} "
                    f"verdict=granted_evidence_supports | {ctx()}"
                )

            # ═══ Definitive-fix Phase 9 (2026-04-28) — confidence-gated
            # flip discipline for ranging/dead/unknown regimes ═══
            # _check_direction_lock leaves these regimes unlocked
            # pre-call so DeepSeek CAN flip when warranted; this gate
            # then demands the resulting confidence clears
            # apex_min_flip_confidence (default 0.70 since Phase 3 of
            # dir-block-fix) before the flip is allowed to stand.
            #
            # Phase 3 of dir-block-fix (2026-05-05) — RR-weighted boost.
            # When the flipped direction's structural R:R is at least
            # apex_flip_rr_boost_threshold × the chosen direction's R:R,
            # the effective confidence checked by the gate is
            # raw_conf + apex_flip_rr_boost_amount. The boost is local
            # to this gate; it is NOT mutated onto optimized.confidence
            # so downstream consumers (gate.py CHECK 12, telemetry,
            # thesis records) still see the raw Qwen confidence.
            _raw_conf = float(getattr(optimized, "confidence", 0.0) or 0.0)
            _rr_boost = 0.0
            _rr_chosen = 0.0
            _rr_flipped = 0.0
            if (
                optimized.direction != claude_direction
                and regime not in ("trending_up", "trending_down", "volatile")
            ):
                # PRIMARY Sell-Bias Fix (2026-05-11) — typo repair.
                # Pre-fix this line read ``getattr(package, "structure_data", None)``
                # (missing the ``al``). The IntelligencePackage dataclass
                # defines the field as ``structural_data`` (see
                # ``src/apex/models.py:387``), so getattr always returned
                # the default ``None`` and the RR-weighted confidence boost
                # below was dead code. Every APEX_FLIP_BLOCKED event in the
                # 2026-05-11 log window showed ``rr_boost=0.00`` — confirming
                # the boost never engaged. Verified by greppling the entire
                # codebase: the typo had only one call site; no other
                # consumer depends on the wrong name. See
                # dev_notes/sell_bias_fixes/p_phase1_apex_gate_assembler.md
                # for the full diagnosis.
                _sd = getattr(package, "structural_data", None)
                if _sd is not None:
                    if claude_direction == "Buy":
                        _rr_chosen = float(getattr(_sd, "rr_long", 0.0) or 0.0)
                        _rr_flipped = float(getattr(_sd, "rr_short", 0.0) or 0.0)
                    elif claude_direction == "Sell":
                        _rr_chosen = float(getattr(_sd, "rr_short", 0.0) or 0.0)
                        _rr_flipped = float(getattr(_sd, "rr_long", 0.0) or 0.0)
                    if _rr_chosen > 0 and _rr_flipped > 0:
                        _ratio = _rr_flipped / _rr_chosen
                        _boost_thresh = float(getattr(
                            self._settings,
                            "apex_flip_rr_boost_threshold", 3.0,
                        ))
                        _boost_amt = float(getattr(
                            self._settings,
                            "apex_flip_rr_boost_amount", 0.15,
                        ))
                        if _ratio >= _boost_thresh:
                            _rr_boost = _boost_amt
            _effective_conf = min(_raw_conf + _rr_boost, 1.0)

            # ═══ PRIMARY Sell-Bias Fix (2026-05-11) — Counter-Trade Gate ═══
            # When the scanner has labeled this setup as a counter-trade
            # (BULLISH_FVG_OB_COUNTER / BEARISH_FVG_OB_COUNTER), brain
            # deliberately chose the contrarian direction. The flip
            # mechanism would silently undo that intent. Skip the
            # remaining gates and revert when the operator's config
            # endorses counter-trade preservation.
            _counter_protected = False
            if (
                optimized.was_flipped
                and bool(getattr(
                    self._settings, "apex_respect_counter_trade", True,
                ))
                and self._is_counter_trade_setup(package)
            ):
                _sd_local = getattr(package, "structural_data", None)
                _stype_local = getattr(_sd_local, "setup_type", "") if _sd_local else ""
                log.warning(
                    f"APEX_FLIP_COUNTER_PROTECTED | sym={symbol} "
                    f"claude={claude_direction} qwen={optimized.direction} "
                    f"setup_type='{str(_stype_local)[:60]}' "
                    f"raw_conf={_raw_conf:.2f} regime={regime} | "
                    f"flip reverted - operator-respected counter-trade "
                    f"| {ctx()}"
                )
                optimized.direction = claude_direction
                optimized.was_flipped = False
                optimized.reasoning = (
                    "[FLIP BLOCKED counter_trade] " + optimized.reasoning
                )
                self._lock_override_count += 1
                _counter_protected = True

            # ═══ PRIMARY Sell-Bias Fix (2026-05-11) — Insufficient-Data Gate ═══
            # Code-enforces the system prompt's "<5 trades = keep
            # trader's direction" rule that DeepSeek empirically
            # mis-reads. Closes the feedback loop where prior Sell-biased
            # flips inflate Sell history and license further Sell flips.
            # Skipped if the counter-trade gate already reverted the
            # flip (optimized.was_flipped is False).
            _insufficient = False
            _flip_dir_count = -1
            if optimized.was_flipped:
                _insufficient, _flip_dir_count = (
                    self._check_insufficient_data_for_flip(
                        package, claude_direction, optimized.direction,
                    )
                )
            if _insufficient:
                _min_trades = int(getattr(
                    self._settings, "apex_min_trades_for_flip", 8,
                ))
                log.warning(
                    f"APEX_FLIP_INSUFFICIENT_DATA | sym={symbol} "
                    f"claude={claude_direction} qwen={optimized.direction} "
                    f"flip_dir_trades={_flip_dir_count} "
                    f"min_required={_min_trades} "
                    f"raw_conf={_raw_conf:.2f} regime={regime} | "
                    f"flip reverted - insufficient history in target direction "
                    f"| {ctx()}"
                )
                optimized.direction = claude_direction
                optimized.was_flipped = False
                optimized.reasoning = (
                    "[FLIP BLOCKED insufficient_data] " + optimized.reasoning
                )
                self._lock_override_count += 1

            _flip_revert, _flip_reason = self._enforce_flip_confidence(
                optimized, claude_direction, regime,
                effective_confidence=_effective_conf,
            )
            if _flip_revert:
                log.warning(
                    f"APEX_FLIP_BLOCKED | sym={symbol} "
                    f"reason='{_flip_reason}' "
                    f"raw_conf={_raw_conf:.2f} "
                    f"eff_conf={_effective_conf:.2f} "
                    f"rr_boost={_rr_boost:.2f} "
                    f"rr_chosen={_rr_chosen:.2f} "
                    f"rr_flipped={_rr_flipped:.2f} "
                    f"regime={regime} | {ctx()}"
                )
                optimized.direction = claude_direction
                optimized.was_flipped = False
                optimized.reasoning = (
                    "[FLIP BLOCKED conf<min] " + optimized.reasoning
                )
                self._lock_override_count += 1
            elif optimized.was_flipped and getattr(
                self._settings, "apex_block_flip_resize", True,
            ):
                self._apply_flip_resize_policy(
                    optimized,
                    claude_direction=claude_direction,
                    regime=regime,
                    symbol=symbol,
                )

            # Step 8: Apply hard safety constraints (clamp all numeric values).
            # coin_data is passed so TP/SL floors can be scaled by the coin's
            # volatility class (see _apply_constraints). coin_data may be None
            # only on assembler failure; the clamp falls back to legacy
            # globals in that case.
            _t = time.time()
            optimized = self._apply_constraints(optimized, package.coin_data)
            _constraints_ms = (time.time() - _t) * 1000

            # ═══ ENFORCE VOLATILITY TP CAP ═══
            # Phase 5 of dir-block-fix (2026-05-05): the cap event now
            # carries `was_reduced=true` and emits at WARNING when the
            # cap actually reduced Qwen's TP. No-op cases (qwen_tp ==
            # cap, common today) emit at DEBUG to cut log noise.
            if _tp_cap is not None and optimized.tp_pct > _tp_cap:
                log.warning(
                    f"APEX_TP_CAP | sym={symbol} "
                    f"qwen_tp={optimized.tp_pct:.1f}% cap={_tp_cap:.1f}% "
                    f"cls={_tp_cap_cls} "
                    f"recTP={package.coin_data.recommended_tp_pct:.1f}% "
                    f"mult={_tp_cap_mult:.2f}x was_reduced=true "
                    f"| Capped to class-aware recTP | {ctx()}"
                )
                optimized.tp_pct = _tp_cap
            elif _tp_cap is not None and abs(optimized.tp_pct - _tp_cap) < 1e-9:
                log.debug(
                    f"APEX_TP_CAP | sym={symbol} "
                    f"qwen_tp={optimized.tp_pct:.1f}% cap={_tp_cap:.1f}% "
                    f"cls={_tp_cap_cls} "
                    f"recTP={package.coin_data.recommended_tp_pct:.1f}% "
                    f"mult={_tp_cap_mult:.2f}x was_reduced=false "
                    f"| No-op (qwen_tp == cap) | {ctx()}"
                )

            # Step 9: Track stats and log
            self._optimized_count += 1
            self._total_time_ms += optimized.apex_response_time_ms or 0
            if optimized.was_flipped:
                self._flip_count += 1

            # ═══ PRIMARY Sell-Bias Fix (2026-05-11) — APEX_FLIP_DECISION ═══
            # Unified per-call decision log. Single greppable line that
            # captures the entire APEX-layer rationale for the trade's
            # direction. Mirrors strategy_worker's DIRECTION_DECISION but
            # at the APEX boundary (before XRAY runs). Operators use
            # this for post-hoc audit of every direction modification.
            # Spec Rule 6 (production-quality observability).
            #
            # decision_reason precedence:
            #   1. ``lock_override``        — pre-call lock + DeepSeek tried flip
            #   2. ``counter_protected``    — counter-trade gate fired
            #   3. ``insufficient_data``    — <5 trades in target direction
            #   4. ``conf_below_threshold`` — confidence gate fired
            #   5. ``flip_accepted``        — flip stands
            #   6. ``no_flip_attempt``      — DeepSeek kept brain direction
            if _flip_switch_suppressed:
                _decision_reason = "flip_switch_off"
            elif _dir_lock_override_fired:
                _decision_reason = "lock_override"
            elif _counter_protected:
                _decision_reason = "counter_protected"
            elif _insufficient:
                _decision_reason = "insufficient_data"
            elif _flip_revert:
                _decision_reason = "conf_below_threshold"
            elif optimized.was_flipped:
                _decision_reason = "flip_accepted"
            else:
                _decision_reason = "no_flip_attempt"

            # Was a flip attempted at any point? Either DeepSeek
            # originally returned a different direction OR the lock
            # override caught one. Useful for measuring DeepSeek's flip
            # propensity separately from APEX's eventual answer.
            _flip_attempted = bool(
                _flip_switch_suppressed
                or _dir_lock_override_fired
                or _counter_protected
                or _insufficient
                or _flip_revert
                or optimized.was_flipped
            )

            log.info(
                f"APEX_FLIP_DECISION | sym={symbol} "
                f"brain_dir={claude_direction} "
                f"apex_dir={optimized.direction} "
                f"flip_attempted={'Y' if _flip_attempted else 'N'} "
                f"flip_accepted={'Y' if optimized.was_flipped else 'N'} "
                f"decision_reason={_decision_reason} "
                f"regime={regime} "
                f"raw_conf={_raw_conf:.2f} "
                f"eff_conf={_effective_conf:.2f} "
                f"rr_boost={_rr_boost:.2f} "
                f"rr_chosen={_rr_chosen:.2f} "
                f"rr_flipped={_rr_flipped:.2f} "
                f"dir_locked={'Y' if direction_locked else 'N'} "
                f"lock_reason='{str(lock_reason)[:60]}' "
                f"flip_dir_trades={_flip_dir_count} "
                f"qwen_initial_dir={_qwen_dir_before_lock} "
                f"| {ctx()}"
            )

            self._log_optimization(
                optimized, directive,
                regime=package.situation_data.regime,
                vol_class=getattr(package.coin_data, "volatility_class", None),
            )

            # Observability: per-step timing breakdown (Phase 6 of logging overhaul)
            _opt_el_ms = (time.time() - _opt_t0) * 1000
            log.info(
                f"APEX_TIMING | sym={symbol} el={_opt_el_ms:.0f}ms | "
                f"assemble={_assemble_ms:.0f}ms deepseek={_deepseek_ms:.0f}ms "
                f"parse={_parse_ms:.0f}ms constraints={_constraints_ms:.0f}ms | {ctx()}"
            )
            # T6-7 / F22 latency spike surface (six-tier-fixes 2026-05-11).
            # Per the report, DeepSeek/OpenRouter calls intermittently spike
            # to 14-30 s vs the 1.9 s baseline. Emit APEX_DEEPSEEK_SLOW at
            # WARN when deepseek elapsed > 5 s so operators can correlate
            # spikes against external OpenRouter status without grepping
            # the full APEX_TIMING stream. Threshold tunable in code; no
            # config knob added.
            if _deepseek_ms > 5000.0:
                log.warning(
                    f"APEX_DEEPSEEK_SLOW | sym={symbol} "
                    f"deepseek_ms={_deepseek_ms:.0f} threshold_ms=5000 "
                    f"total_ms={_opt_el_ms:.0f} | {ctx()}"
                )

            # Issue 1 fix (2026-05-11): stamp the lock state captured at
            # the lock gate above onto the OptimizedTrade so layer_manager
            # can forward it as ``_apex_locked`` / ``_apex_lock_reason``
            # into the trade dict that strategy_worker._execute_claude_trade
            # eventually reads.
            optimized.is_locked, optimized.lock_reason = _apex_lock_state

            # Step 10: Return
            return optimized

        except Exception as e:
            # Observability: even on failure, surface the partial sub-timings so
            # we can tell whether assemble/deepseek/parse/constraints was slow
            # before the exception fired.
            _opt_el_ms_fail = (time.time() - _opt_t0) * 1000
            log.info(
                f"APEX_TIMING | sym={symbol} el={_opt_el_ms_fail:.0f}ms outcome=fail | "
                f"assemble={_assemble_ms:.0f}ms deepseek={_deepseek_ms:.0f}ms "
                f"parse={_parse_ms:.0f}ms constraints={_constraints_ms:.0f}ms | {ctx()}"
            )
            err_str = str(e)[:120]
            # On timeout/DeepSeek failure: distinguish from data insufficiency
            is_timeout = "timeout" in err_str.lower() or "timed out" in err_str.lower()
            try:
                regime_trades = package.situation_data.total_trades_in_condition
                min_regime = getattr(self._settings, "min_regime_trades_for_fallback", 10)
                if is_timeout and regime_trades >= min_regime:
                    log.warning(
                        f"APEX_TIMEOUT_REGIME | sym={symbol} "
                        f"err='{err_str}' regime_trades={regime_trades} "
                        f"| falling to regime defaults | {ctx()}"
                    )
                    return self._fallback(
                        directive,
                        f"timeout_regime: {err_str[:40]}",
                        lock_state=_apex_lock_state,
                        # T2-2 (2026-05-12): pass volatility profile so the
                        # fallback can substitute a percentage-of-price
                        # SL/TP when Claude's original prices would have
                        # been silently dropped by SLTPValidator (F69).
                        coin_data=getattr(package, "coin_data", None),
                    )
            except Exception:
                pass  # package may not exist if assembler failed

            # Issue B Phase 3c (2026-05-08) — emit ``body_preview`` and
            # ``retryable`` so future incidents are diagnosable from a
            # single log line. ``raw_body`` is captured by qwen_client
            # at every site that has a readable HTTP body; truncated
            # to 1000 chars on the exception, further trimmed to 200
            # for the log emit. The redaction strips any ``Bearer``
            # or ``Authorization`` token-like substrings before logging
            # — defensive: response bodies should not contain auth, but
            # gateway error replies have surprised us before.
            _body_raw = getattr(e, "raw_body", None) or ""
            _body_preview = _redact_auth_tokens(_body_raw)[:200]
            _retryable = bool(getattr(e, "retryable", False))
            log.error(
                f"APEX_FAIL_UNEXPECTED | sym={symbol} "
                f"err='{err_str}' retryable={_retryable} "
                f"body_preview='{_body_preview}' "
                f"using_defaults=Y | {ctx()}"
            )
            # T2-2 (2026-05-12): pass volatility profile when available
            # so the fallback can substitute a sane percentage-of-price
            # SL/TP for invalid Claude originals. ``package`` may be
            # undefined here if the assembler step itself raised — guard
            # with locals() so the legacy behaviour holds in that case.
            _coin_data_for_fb = (
                getattr(locals().get("package"), "coin_data", None)
                if "package" in locals() else None
            )
            return self._fallback(
                directive,
                f"unexpected: {str(e)[:60]}",
                lock_state=_apex_lock_state,
                coin_data=_coin_data_for_fb,
            )

    # =========================================================================
    # Private helpers
    # =========================================================================

    def _parse_response(self, result: dict, directive: dict) -> OptimizedTrade:
        """Map DeepSeek's JSON content + API metadata into an OptimizedTrade.

        Handles alternative key names that DeepSeek sometimes produces instead of
        the template-specified keys (e.g. ``stop_loss_pct`` vs ``sl_pct``).

        Args:
            result: Dict returned by DeepSeekClient.optimize() — contains
                'content' (parsed JSON), 'response_time_ms', 'cost_usd',
                'model_used', 'input_tokens', 'output_tokens'.
            directive: Original Claude directive dict (for tracking fields).

        Returns:
            OptimizedTrade populated from DeepSeek's output.
        """
        analysis = result["content"]
        symbol = directive.get("symbol", "")
        original_dir = directive.get("direction", "Buy")
        qwen_dir = analysis.get("direction", original_dir)

        # Ensure direction is valid
        if qwen_dir not in ("Buy", "Sell"):
            qwen_dir = original_dir

        # DeepSeek sometimes uses alternative key names — resolve them.
        def _get(primary: str, *alternates, default=None):
            v = analysis.get(primary)
            if v is not None:
                return v
            for alt in alternates:
                v = analysis.get(alt)
                if v is not None:
                    return v
            return default

        sl = float(_get("sl_pct", "stop_loss_pct", "stop_loss_percent", "sl", "stop_loss", default=2.0))
        tp = float(_get("tp_pct", "take_profit_pct", "take_profit_percent", "tp", "take_profit", default=1.5))
        mode = str(_get("tp_mode", "exit_strategy", "mode", default="fixed"))
        size = float(
            _get("position_size_usd", "size_usd", "size",
                 default=directive.get("size_usd", 600))
        )
        lev = int(
            _get("leverage", "lev", default=directive.get("leverage", 3))
        )
        timing = str(_get("entry_timing", "timing", "entry", default="immediate"))
        add_on = bool(_get("add_on_pullback", "add_on", default=False))
        reasoning = str(
            _get("reasoning", "optimization_rationale", "rationale",
                 "optimization_notes", "notes", default="")
        )[:500]
        conf = _get("confidence", default=0.5)
        # Confidence may be a string like "high" — normalise to float
        if isinstance(conf, str):
            conf = {"high": 0.85, "medium": 0.6, "moderate": 0.6,
                    "low": 0.3}.get(conf.lower(), 0.5)
        conf = float(conf)

        return OptimizedTrade(
            symbol=symbol,
            direction=qwen_dir,
            sl_pct=sl,
            tp_pct=tp,
            tp_mode=mode,
            position_size_usd=size,
            leverage=lev,
            entry_timing=timing,
            add_on_pullback=add_on,
            add_trigger_pct=float(
                _get("add_trigger_pct", "add_on_trigger_pct", default=0.3)
            ) if add_on else 0.0,
            add_size_pct=int(
                _get("add_size_pct", "add_on_size_pct", default=0)
            ) if add_on else 0,
            reasoning=reasoning,
            confidence=conf,
            # Tracking fields — record what changed from Claude's original
            was_flipped=(qwen_dir != original_dir),
            original_direction=original_dir,
            original_sl=float(
                directive.get("stop_loss_price") or directive.get("sl") or 0
            ),
            original_tp=float(
                directive.get("take_profit_price") or directive.get("tp") or 0
            ),
            original_size=float(directive.get("size_usd", 600)),
            # API metadata
            apex_response_time_ms=int(result.get("response_time_ms", 0)),
            apex_cost_usd=float(result.get("cost_usd", 0.0)),
            apex_model=str(result.get("model_used", "")),
            is_fallback=False,
        )

    def _apply_constraints(
        self,
        trade: OptimizedTrade,
        coin_data: Any = None,
    ) -> OptimizedTrade:
        """Apply hard safety limits that override DeepSeek's output.

        These limits cannot be overridden by DeepSeek's response. They enforce
        the bounds defined in APEXSettings and the APEX blueprint spec.

        When ``coin_data`` is supplied (the normal path — assembler populates
        it on every call), SL and TP floors are scaled by the per-class
        recommendations from the volatility profiler, giving dead/low coins
        class-appropriate tight floors and high/extreme coins room to run.
        When ``coin_data`` is None (assembler fell back), we revert to the
        legacy global floors — exactly pre-fix behaviour.
        """
        # Position size: J5 (2026-05-14) dynamic cap + conviction scale.
        #
        # Pre-J5 the cap was the static ``max_position_size_usd`` so 15
        # of 18 audit trades (83%) clamped to identical $1200 regardless
        # of signal strength. Two settings drive the new behaviour
        # (defaults preserve byte-equivalent legacy semantics):
        #
        #   apex_size_cap_pct_of_equity (default 0.0)
        #     0.0 → use the static dollar cap (legacy)
        #     >0  → cap = max(max_position_size_usd,
        #                     trading_capital * pct / 100)
        #     The static cap acts as a minimum floor so freshly-funded
        #     small accounts cannot trade pathologically tiny positions.
        #
        #   apex_size_conviction_floor (default 0.5)
        #     Within the cap the final size is scaled by
        #         max(conviction_floor, trade.confidence)
        #     Low-conviction trades shrink relative to high-conviction
        #     ones; the absolute cap is never exceeded.
        #
        # Account equity is read from the late-bound
        # ``_account_state_getter`` callable. When unwired or it returns
        # a non-positive value, the dynamic path is silently bypassed
        # and the static cap applies.
        # Five-Fix Follow-Up — Fix 5 (2026-06-10): size-override kill-switch,
        # symmetric with apex_leverage_override_enabled (Issue 2.3) and
        # apex_dir_flip_enabled. The optimizer LLM proposes its OWN size which
        # arrives here in trade.position_size_usd and — through the J5 cap +
        # conviction scale + brain-auth floor below — could land LARGER than
        # the brain's deliberate size_usd (proven live: brain $700, APEX
        # proposal $1200, executed $1050 = 1.5x the brain's decision). When
        # False (default, operator decision 2026-06-10) the brain's parsed
        # size stands UNMODIFIED — no adoption, no raise, no conviction
        # shrink — and flows on to the gate where every safety validation
        # (CHECK 0/1/2/4/5) still applies as a ceiling. When True, the whole
        # J5 block below runs exactly as before. getattr fallback False =
        # failure-safe to honor the brain.
        _size_override_enabled = bool(getattr(
            self._settings, "apex_size_override_enabled", False,
        ))
        _brain_size_5 = float(getattr(trade, "original_size", 0.0) or 0.0)
        if not _size_override_enabled and _brain_size_5 > 0:
            _proposed_5 = float(trade.position_size_usd or 0.0)
            trade.position_size_usd = round(_brain_size_5, 2)
            if abs(_proposed_5 - _brain_size_5) > 0.005:
                trade.reasoning = (
                    "[SIZE OVERRIDE DISABLED by switch] " + (trade.reasoning or "")
                )
            log.info(
                f"APEX_SIZING_AUTHORITATIVE | sym={trade.symbol} "
                f"proposed=${_proposed_5:.0f} brain=${_brain_size_5:.0f} "
                f"final=${trade.position_size_usd:.0f} switch=off "
                f"changed={abs(_proposed_5 - _brain_size_5) > 0.005} | {ctx()}"
            )
        else:
            _static_cap = float(getattr(self._settings, "max_position_size_usd", 1200.0))
            _pct_of_equity = float(getattr(
                self._settings, "apex_size_cap_pct_of_equity", 0.0,
            ) or 0.0)
            _conviction_floor = float(getattr(
                self._settings, "apex_size_conviction_floor", 0.5,
            ) or 0.5)
            _effective_cap = _static_cap
            _capital_used: float = 0.0
            if _pct_of_equity > 0.0 and self._account_state_getter is not None:
                try:
                    _capital = float(self._account_state_getter() or 0.0)
                except Exception:
                    _capital = 0.0
                if _capital > 0.0:
                    _capital_used = _capital
                    _effective_cap = max(
                        _static_cap, _capital * _pct_of_equity / 100.0,
                    )
            _pre_cap = float(trade.position_size_usd)
            _post_cap = min(_pre_cap, _effective_cap)
            _cap_hit = _post_cap < _pre_cap
            _conf = max(0.0, min(float(getattr(trade, "confidence", 0.0) or 0.0), 1.0))
            _conviction_scale = max(_conviction_floor, _conf)
            _scaled = _post_cap * _conviction_scale
            # H3 (2026-05-30): respect the conviction-scaled size — do NOT floor a
            # weak/low-conviction setup up to an arbitrary $100. The conviction
            # DOWN-scaling above is intended (weak setups get smaller); flooring it
            # back up oversizes the worst trades. A size genuinely below the EXCHANGE
            # minimum is SKIPPED downstream (qty<=0 -> TRADE_SKIP rsn=qty_zero).
            # Brain-Authoritative Sizing (2026-05-31): when enabled, the brain's
            # size_usd (trade.original_size) is the deliberate fund-managed size —
            # APEX still optimizes direction/TP/SL/leverage but must NOT SHRINK the
            # size below the brain's directive. Floor _scaled at the brain's size;
            # the gate's CHECK 4 available-capital ceiling is the binding safety rail.
            # Flag off -> _final == _scaled (byte-identical legacy behaviour).
            _brain_auth = bool(getattr(self._settings, "brain_authoritative_sizing_enabled", False))
            _brain_size = float(getattr(trade, "original_size", 0.0) or 0.0)
            _final = max(_scaled, _brain_size) if (_brain_auth and _brain_size > 0) else _scaled
            _small_size = _final < 100.0
            trade.position_size_usd = round(_final, 2)

            log.info(
                f"APEX_SIZING_DECISION | sym={trade.symbol} "
                f"pre_cap=${_pre_cap:.0f} effective_cap=${_effective_cap:.0f} "
                f"capital_used=${_capital_used:.0f} "
                f"pct_of_equity={_pct_of_equity:.2f} "
                f"conv={_conf:.2f} conv_floor={_conviction_floor:.2f} "
                f"conv_scale={_conviction_scale:.2f} "
                f"brain_auth={_brain_auth} brain_size=${_brain_size:.0f} "
                f"scaled=${_scaled:.0f} final=${trade.position_size_usd:.0f} "
                f"cap_hit={_cap_hit} small_size={_small_size} | {ctx()}"
            )
            if _cap_hit:
                log.info(
                    f"APEX_SIZING_CAP_HIT | sym={trade.symbol} "
                    f"pre_cap=${_pre_cap:.0f} cap=${_effective_cap:.0f} | {ctx()}"
                )
            if _small_size:
                # H3: the conviction-scaled size is small but PRESERVED (no longer
                # floored to $100). If it is below the exchange minimum the trade is
                # skipped downstream; otherwise the brain's small-probe size stands.
                log.info(
                    f"APEX_SIZING_SMALL_SIZE | sym={trade.symbol} "
                    f"scaled=${_scaled:.0f} preserved=true floor_removed=H3 | {ctx()}"
                )

        # Leverage: floor 1x, cap at settings.max_leverage
        trade.leverage = max(1, min(trade.leverage, self._settings.max_leverage))

        # SL per-class floor from volatility profiler's recommended_sl_pct.
        # Multiplier 0.6 lets DeepSeek tighten modestly but not below 60 %
        # of the class's baseline noise floor. Global 0.2 % absolute floor
        # preserved as a hard-lower-bound (e.g. for bid-ask strangulation
        # protection on ultra-low-ATR coins).
        _rec_sl = getattr(coin_data, "recommended_sl_pct", None) if coin_data else None
        if _rec_sl is not None and _rec_sl > 0:
            _sl_floor = max(0.2, round(float(_rec_sl) * 0.6, 2))
        else:
            _sl_floor = 0.2
        trade.sl_pct = max(_sl_floor, min(trade.sl_pct, 5.0))

        # TP per-class floor from recommended_tp_pct. The per-class CEILING
        # is applied separately via APEX_TP_CAP (see `_tp_cap` in optimize()
        # — uses tp_cap_multiplier_by_class). Here we only enforce FLOORs.
        _rec_tp = getattr(coin_data, "recommended_tp_pct", None) if coin_data else None
        if _rec_tp is not None and _rec_tp > 0:
            _tp_floor = max(
                getattr(self._settings, "min_tp_pct", 0.3),
                round(float(_rec_tp) * 0.6, 2),
            )
        else:
            _tp_floor = getattr(self._settings, "min_tp_pct", 0.3)
        trade.tp_pct = max(_tp_floor, min(trade.tp_pct, 8.0))

        # Confidence: 0.0 to 1.0
        trade.confidence = max(0.0, min(trade.confidence, 1.0))

        # TP mode: normalise DeepSeek variants then validate
        _mode_map = {
            "trailing": "trail_only", "trail": "trail_only",
            "partial": "partial_trail", "partial_trailing": "partial_trail",
        }
        trade.tp_mode = _mode_map.get(trade.tp_mode, trade.tp_mode)
        if trade.tp_mode not in ("fixed", "trail_only", "partial_trail"):
            trade.tp_mode = "fixed"

        # Entry timing: must be one of the valid values
        if trade.entry_timing not in ("immediate", "wait_pullback"):
            trade.entry_timing = "immediate"

        # Add-on params: clamp to valid ranges
        if trade.add_on_pullback:
            trade.add_size_pct = max(0, min(trade.add_size_pct or 0, 100))
            trade.add_trigger_pct = max(0.1, min(trade.add_trigger_pct or 0.3, 2.0))

        return trade

    # T2-2 (2026-05-12) — percentage-of-price safety cap applied to
    # Claude's original SL/TP when the fallback fires. Set just under the
    # SLTPValidator default `max_distance_pct=10.0%` so a fallback whose
    # original prices would have been silently dropped at the validator
    # boundary (the F69 BLURUSDT case: SL was 22.6% from price → SLTP_SKIP)
    # is instead clamped to a volatility-aware percentage and proceeds via
    # the layer_manager's pct→price conversion path. The clamp's TARGET
    # uses `coin_data.recommended_sl_pct * 1.5` (1.5x the per-class
    # baseline) when available, else the absolute floor _APEX_FB_SL_PCT_FLOOR.
    # The 9.0 cap is deliberately under 10.0 to leave validator headroom
    # for any small post-conversion drift.
    _APEX_FB_VALIDATOR_SAFE_MAX_PCT: float = 9.0
    # Absolute safe SL when no recommended_sl_pct is available.
    _APEX_FB_SL_PCT_FLOOR: float = 1.5
    # Multiplier applied to recommended_sl_pct/recommended_tp_pct when
    # building the fallback override. 1.5x = wider than the regime
    # baseline so the fallback errs on the side of NOT clipping a
    # legitimately-aggressive trade prematurely (operator's
    # aggressive-exploitation aim).
    _APEX_FB_REC_PCT_MULT: float = 1.5

    def _fallback(
        self,
        directive: dict,
        reason: str = "",
        *,
        lock_state: tuple[bool, str] = (False, ""),
        coin_data: Any = None,
    ) -> OptimizedTrade:
        """Return an OptimizedTrade that preserves Claude's original parameters.

        Sets is_fallback=True so _apply_apex_optimization in layer_manager
        returns the original directive dict unchanged — avoiding any lossy
        pct→price→pct round-trips.

        Args:
            directive: Original Claude directive dict.
            reason: Short description of why APEX fell back (for logging).
            lock_state: (is_locked, lock_reason) captured by the caller before
                fallback fired. Issue 1 fix (2026-05-11) — preserves the
                APEX_DIR_LOCK decision through the fallback path so downstream
                consumers (layer_manager → strategy_worker) can honor it. Early
                fallbacks (before _check_direction_lock runs) pass the default
                (False, ""); late fallbacks (inside the except branch) pass
                the captured lock state.
            coin_data: T2-2 (2026-05-12). Optional volatility-profile snapshot
                with ``current_price``, ``recommended_sl_pct``,
                ``recommended_tp_pct``. When provided, the fallback validates
                Claude's original SL/TP prices against the SLTPValidator's
                safety cap and SUBSTITUTES a volatility-aware percentage if
                the original would have been silently dropped. Fixes F69 —
                the BLURUSDT case where Claude's SL was 22.6% from price
                (>10% validator cap), the timeout fallback preserved that
                invalid SL unchanged, and the trade vanished as
                SLTP_VALIDATE_SKIP. With this kwarg, the fallback returns
                ``is_fallback=False`` + a clamped sl_pct so the layer_manager
                converts pct→price using the live ticker — producing a valid
                SL distance regardless of coin price level. Legacy callers
                (the 3 early-fallback sites at line 124/159/216) pass
                ``coin_data=None`` and retain pre-fix behaviour.

        Returns:
            OptimizedTrade. ``is_fallback=True`` when Claude's original
            SL/TP are within validator caps OR when ``coin_data`` is not
            provided. ``is_fallback=False`` when the SL/TP were clamped
            (so the layer_manager applies the corrected percentages via
            current price).
        """
        symbol = directive.get("symbol", "?")
        original_dir = directive.get("direction", "Buy")

        self._fallback_count += 1

        log.warning(
            f"APEX_SKIP | sym={symbol} rsn='{reason[:80]}' "
            f"using_defaults=Y | {ctx()}"
        )

        _orig_sl = float(
            directive.get("stop_loss_price") or directive.get("sl") or 0
        )
        _orig_tp = float(
            directive.get("take_profit_price") or directive.get("tp") or 0
        )

        # T2-2: validator-safety check on Claude's original prices. When
        # `coin_data` is provided AND the original SL is beyond the safe
        # validator cap, substitute a volatility-aware percentage and
        # set `is_fallback=False` so the layer_manager converts via current
        # price (which scales correctly for low-priced coins).
        _sl_pct_override: float | None = None
        _tp_pct_override: float | None = None
        if coin_data is not None:
            _cur_price = float(getattr(coin_data, "current_price", 0.0) or 0.0)
            if _cur_price > 0:
                # SL distance check
                if _orig_sl > 0:
                    _orig_sl_dist_pct = (
                        abs(_orig_sl - _cur_price) / _cur_price * 100.0
                    )
                    if _orig_sl_dist_pct > self._APEX_FB_VALIDATOR_SAFE_MAX_PCT:
                        _rec_sl = float(
                            getattr(coin_data, "recommended_sl_pct", 0.0) or 0.0
                        )
                        if _rec_sl > 0:
                            _sl_pct_override = round(
                                _rec_sl * self._APEX_FB_REC_PCT_MULT, 2
                            )
                        else:
                            _sl_pct_override = self._APEX_FB_SL_PCT_FLOOR
                        # Clamp to validator-safe ceiling so a pathological
                        # recommended_sl_pct cannot reintroduce the bug.
                        _sl_pct_override = min(
                            _sl_pct_override,
                            self._APEX_FB_VALIDATOR_SAFE_MAX_PCT,
                        )
                        log.warning(
                            f"APEX_FALLBACK_SL_PCT_APPLIED | sym={symbol} "
                            f"price={_cur_price:.6f} "
                            f"original_sl={_orig_sl:.6f} "
                            f"original_dist_pct={_orig_sl_dist_pct:.2f}% "
                            f"safe_max_pct={self._APEX_FB_VALIDATOR_SAFE_MAX_PCT:.1f}% "
                            f"override_sl_pct={_sl_pct_override:.2f}% "
                            f"rec_sl_pct={_rec_sl:.2f}% "
                            f"reason='{reason[:60]}' | {ctx()}"
                        )
                # TP distance check (mirror of SL — validator caps both)
                if _orig_tp > 0:
                    _orig_tp_dist_pct = (
                        abs(_orig_tp - _cur_price) / _cur_price * 100.0
                    )
                    if _orig_tp_dist_pct > self._APEX_FB_VALIDATOR_SAFE_MAX_PCT:
                        _rec_tp = float(
                            getattr(coin_data, "recommended_tp_pct", 0.0) or 0.0
                        )
                        if _rec_tp > 0:
                            _tp_pct_override = round(
                                _rec_tp * self._APEX_FB_REC_PCT_MULT, 2
                            )
                        else:
                            _tp_pct_override = self._APEX_FB_SL_PCT_FLOOR
                        _tp_pct_override = min(
                            _tp_pct_override,
                            self._APEX_FB_VALIDATOR_SAFE_MAX_PCT,
                        )
                        log.warning(
                            f"APEX_FALLBACK_TP_PCT_APPLIED | sym={symbol} "
                            f"price={_cur_price:.6f} "
                            f"original_tp={_orig_tp:.6f} "
                            f"original_dist_pct={_orig_tp_dist_pct:.2f}% "
                            f"safe_max_pct={self._APEX_FB_VALIDATOR_SAFE_MAX_PCT:.1f}% "
                            f"override_tp_pct={_tp_pct_override:.2f}% "
                            f"rec_tp_pct={_rec_tp:.2f}% "
                            f"reason='{reason[:60]}' | {ctx()}"
                        )

        # T2-2: when an override was applied, surrender is_fallback=True so
        # layer_manager applies the corrected percentages via current price.
        # Otherwise preserve the legacy pass-through behaviour.
        _was_clamped = (_sl_pct_override is not None) or (_tp_pct_override is not None)
        _final_sl_pct = _sl_pct_override if _sl_pct_override is not None else 2.0
        _final_tp_pct = _tp_pct_override if _tp_pct_override is not None else 1.5

        _is_locked, _lock_reason = lock_state
        _fb = OptimizedTrade(
            symbol=symbol,
            direction=original_dir,
            sl_pct=_final_sl_pct,
            tp_pct=_final_tp_pct,
            tp_mode="fixed",
            position_size_usd=float(directive.get("size_usd", 600)),
            leverage=int(directive.get("leverage", 3)),
            entry_timing="immediate",
            add_on_pullback=False,
            add_trigger_pct=0.0,
            add_size_pct=0,
            reasoning=f"APEX fallback: {reason[:80]}",
            confidence=0.0,
            was_flipped=False,
            original_direction=original_dir,
            original_sl=_orig_sl,
            original_tp=_orig_tp,
            original_size=float(directive.get("size_usd", 600)),
            # T2-2: when SL/TP were clamped, is_fallback=False so the
            # corrected percentages flow through the pct→price conversion
            # path in layer_manager._apply_apex_optimization.
            is_fallback=not _was_clamped,
            is_locked=_is_locked,
            lock_reason=_lock_reason,
        )
        return _fb

    def _log_optimization(
        self,
        opt: OptimizedTrade,
        directive: dict,
        regime: str = "",
        vol_class: str | None = None,
    ) -> None:
        """Log the optimization result with before/after comparison.

        ``vol_class`` is the coin's volatility class from the profiler; it
        appears as ``cls=`` in APEX_OK/APEX_FLIP so post-hoc analysis can
        group TP/SL distributions by class.
        """
        _cls = vol_class or "?"
        if opt.was_flipped:
            log.warning(
                f"APEX_FLIP | sym={opt.symbol} "
                f"claude={opt.original_direction} apex={opt.direction} "
                f"sl={opt.sl_pct:.1f}% tp={opt.tp_pct:.1f}% cls={_cls} "
                f"sz=${opt.original_size:.0f}→${opt.position_size_usd:.0f} "
                f"mode={opt.tp_mode} conf={opt.confidence:.0%} "
                f"regime={regime} "
                f"ms={opt.apex_response_time_ms} | {ctx()}"
            )
        else:
            changes = []
            if opt.position_size_usd != opt.original_size:
                changes.append(
                    f"sz=${opt.original_size:.0f}→${opt.position_size_usd:.0f}"
                )
            if opt.tp_mode != "fixed":
                changes.append(f"mode={opt.tp_mode}")
            if opt.add_on_pullback:
                changes.append(f"add={opt.add_size_pct}%@{opt.add_trigger_pct}%")
            change_str = " ".join(changes) if changes else "no_param_changes"

            log.info(
                f"APEX_OK | sym={opt.symbol} dir={opt.direction} "
                f"sl={opt.sl_pct:.1f}% tp={opt.tp_pct:.1f}% cls={_cls} "
                f"lev={opt.leverage}x {change_str} "
                f"conf={opt.confidence:.0%} regime={regime} "
                f"ms={opt.apex_response_time_ms} | {ctx()}"
            )
            # Phase 12.3 (lifecycle-logging-audit Gap 3.5-G1 + 3.7-G1):
            # dedicated APEX_SIZING + APEX_LEVERAGE markers so operators
            # can grep size/leverage decisions per-coin without parsing
            # APEX_OK's mixed field set. Inputs (original_size, original
            # leverage if known) → outputs (position_size_usd, leverage).
            if opt.original_size > 0 and opt.original_size != opt.position_size_usd:
                log.info(
                    f"APEX_SIZING | sym={opt.symbol} "
                    f"input_qty=${opt.original_size:.0f} "
                    f"output_qty=${opt.position_size_usd:.0f} "
                    f"vol_class={_cls} regime={regime} "
                    f"conf={opt.confidence:.0%} | {ctx()}"
                )
            log.info(
                f"APEX_LEVERAGE | sym={opt.symbol} "
                f"output_lev={opt.leverage}x vol_class={_cls} "
                f"regime={regime} conf={opt.confidence:.0%} | {ctx()}"
            )

    def get_stats(self) -> dict:
        """Return cumulative optimization stats for health/monitoring."""
        avg_ms = (
            self._total_time_ms // max(self._optimized_count, 1)
            if self._optimized_count > 0
            else 0
        )
        return {
            "optimized": self._optimized_count,
            "fallbacks": self._fallback_count,
            "flips": self._flip_count,
            "flip_rate": self._flip_count / max(self._optimized_count, 1),
            "lock_overrides": self._lock_override_count,
            "avg_time_ms": avg_ms,
            "qwen_stats": self._client.get_stats() if self._client else {},
        }

    # =========================================================================
    # Direction lock helpers
    # =========================================================================

    def _check_flip_evidence(self, trades: list, claude_direction: str) -> bool:
        """Return True only if overwhelming TIAS evidence supports flipping
        AWAY from claude_direction (>70% WR, >8 trades for opposite direction).

        Uses trades already fetched in the IntelligencePackage — no new service
        calls required.
        """
        opposite = "Sell" if claude_direction == "Buy" else "Buy"
        opp_trades = [t for t in trades if t.get("direction") == opposite]
        if len(opp_trades) < 8:
            return False
        opp_wins = sum(1 for t in opp_trades if t.get("win"))
        opp_wr = (opp_wins / len(opp_trades)) * 100 if opp_trades else 0.0
        return opp_wr >= 70.0

    def _check_direction_lock(
        self, package, claude_direction: str, regime: str,
    ) -> tuple[bool, str]:
        """Determine if Claude's direction should be code-locked.

        Returns ``(locked, reason)``. Internally also stamps the most
        recent component breakdown on ``self._last_lock_components`` so
        the caller can emit ``APEX_LOCK_DECISION_EXPLAINED`` with full
        audit detail.

        R2 direction-fix (2026-05-17). The pre-2026-05-17 lock locked
        any trending regime to its natural direction regardless of
        evidence and vetoed all 11 DeepSeek flip attempts on the
        2026-05-16 session (including the 7.3x-favoring-Long BSBUSDT
        case that cost -$70.08). The new lock asks the same
        direction-agnostic question for both Buy and Sell — "given
        current evidence (regime alignment, structural R:R, counter-
        trade direction, recent per-direction WR, symbol-specific flip
        evidence), is the brain's direction supported?" — and locks
        only when the composite score is below
        ``apex_lock_score_threshold`` (default 0.0). The asymmetry
        between Buy and Sell EMERGES from the WR signal automatically
        rather than from hard-coded direction-specific thresholds.

        Each signal contributes to a scalar score:

        1. regime_signal: +regime_weight if regime supports brain_dir,
           -regime_weight if opposed, 0 otherwise. ranging / dead /
           unknown / volatile do not contribute through this signal.
        2. structural_signal: log(rr_brain_dir / rr_opp_dir) *
           structural_weight. Natural log; bounded by realistic R:R
           ratios.
        3. trade_dir_signal: +trade_dir_weight if structural_data.trade
           _direction matches brain_dir (ALPHA Option E plumbing),
           -trade_dir_weight if opposite, 0 if unset.
        4. wr_signal: ((dir_wr - 50) / 50) * wr_weight using global
           per-direction WR from situation_data. Positive when the
           brain's direction has > 50% WR.
        5. symbol_evidence_signal: +sym_w if the brain's direction has
           >= floor percent WR for this symbol's regime-filtered
           history; -sym_w if the opposite has >= floor percent;
           0 otherwise. Subsumes the legacy _check_flip_evidence used
           on volatile regimes.
        """
        import math

        ap = self._settings
        # Defensive: when self._settings is None (legacy test fixtures)
        # or missing one of the new R2 attributes, fall back to neutral
        # defaults so composite scoring still produces a sensible verdict.
        # Production code always passes a full APEXSettings.
        def _ap(name: str, default: float) -> float:
            try:
                return float(getattr(ap, name, default))
            except Exception:
                return float(default)

        regime_weight = _ap("apex_lock_regime_weight", 1.0)
        structural_weight = _ap("apex_lock_structural_weight", 1.0)
        trade_dir_weight = _ap("apex_lock_trade_dir_weight", 1.0)
        wr_weight = _ap("apex_lock_wr_weight", 1.0)
        symbol_evidence_weight = _ap("apex_lock_symbol_evidence_weight", 1.0)
        symbol_evidence_wr_floor = _ap(
            "apex_lock_symbol_evidence_wr_floor_pct", 70.0,
        )
        score_threshold = _ap("apex_lock_score_threshold", 0.0)

        sd = getattr(package, "structural_data", None)
        sit = getattr(package, "situation_data", None)
        sym_hist = getattr(package, "symbol_history", None)

        components: dict[str, float] = {}

        # Signal 1: regime alignment
        regime_signal = 0.0
        if regime == "trending_up":
            regime_signal = 1.0 if claude_direction == "Buy" else -1.0
        elif regime == "trending_down":
            regime_signal = 1.0 if claude_direction == "Sell" else -1.0
        components["regime"] = round(regime_signal * regime_weight, 3)

        # Signal 2: structural R:R (log-scale, signed)
        structural_signal = 0.0
        if sd is not None:
            rr_long = getattr(sd, "rr_long", None)
            rr_short = getattr(sd, "rr_short", None)
            if (
                rr_long is not None
                and rr_short is not None
                and rr_long > 0
                and rr_short > 0
            ):
                if claude_direction == "Buy":
                    ratio = rr_long / rr_short
                else:
                    ratio = rr_short / rr_long
                # Clamp ratio to [0.01, 100] before log so a 0 or extreme
                # value cannot blow up the score.
                ratio = max(0.01, min(100.0, ratio))
                structural_signal = math.log(ratio)
        components["structural"] = round(
            structural_signal * structural_weight, 3,
        )

        # Signal 3: trade_direction alignment (ALPHA Option E plumbing)
        trade_dir_signal = 0.0
        if sd is not None:
            td = (getattr(sd, "trade_direction", "") or "").lower()
            if td:
                brain_td = "long" if claude_direction == "Buy" else "short"
                trade_dir_signal = 1.0 if td == brain_td else -1.0
        components["trade_dir"] = round(
            trade_dir_signal * trade_dir_weight, 3,
        )

        # Signal 4: global per-direction WR
        wr_signal = 0.0
        if sit is not None:
            buy_wr = float(getattr(sit, "buy_win_rate", 0.0) or 0.0)
            sell_wr = float(getattr(sit, "sell_win_rate", 0.0) or 0.0)
            dir_wr = buy_wr if claude_direction == "Buy" else sell_wr
            if dir_wr > 0:
                wr_signal = (dir_wr - 50.0) / 50.0
                # Clamp to [-1, +1]
                wr_signal = max(-1.0, min(1.0, wr_signal))
        components["wr"] = round(wr_signal * wr_weight, 3)

        # Signal 5: symbol-specific flip evidence (subsumes legacy check)
        symbol_evidence_signal = 0.0
        if sym_hist is not None and getattr(sym_hist, "trades", None):
            try:
                trades = sym_hist.trades
                if claude_direction == "Buy":
                    same = [t for t in trades if t.get("direction") == "Buy"]
                    opp = [t for t in trades if t.get("direction") == "Sell"]
                else:
                    same = [t for t in trades if t.get("direction") == "Sell"]
                    opp = [t for t in trades if t.get("direction") == "Buy"]
                floor = symbol_evidence_wr_floor
                if same:
                    same_wr = sum(1 for t in same if t.get("win")) / len(same) * 100.0
                    if same_wr >= floor:
                        symbol_evidence_signal = 1.0
                if symbol_evidence_signal == 0.0 and opp:
                    opp_wr = sum(1 for t in opp if t.get("win")) / len(opp) * 100.0
                    if opp_wr >= floor:
                        symbol_evidence_signal = -1.0
            except Exception:
                # Defensive: malformed trade record must not crash the
                # lock decision. Leave the signal at 0 (no contribution).
                symbol_evidence_signal = 0.0
        components["symbol_evidence"] = round(
            symbol_evidence_signal * symbol_evidence_weight, 3,
        )

        # Composite
        score = (
            components["regime"]
            + components["structural"]
            + components["trade_dir"]
            + components["wr"]
            + components["symbol_evidence"]
        )
        components["score"] = round(score, 3)
        components["threshold"] = score_threshold

        # Stamp for caller. Single-threaded per-instance use is the
        # documented optimizer contract (one optimize() call at a time
        # per APEXOptimizer instance per the worker dispatch).
        self._last_lock_components = components

        locked = score < score_threshold
        reason = (
            f"composite_score={score:.2f}_below_{score_threshold}"
            if locked
            else f"composite_score={score:.2f}_above_or_equal_{score_threshold}"
        )
        return locked, reason

    def _check_insufficient_data_for_flip(
        self,
        package: Any,
        claude_direction: str,
        qwen_direction: str,
    ) -> tuple[bool, int]:
        """PRIMARY Sell-Bias Fix (2026-05-11) — insufficient-data flip gate.

        Code-enforces a stricter version of the system prompt's rule.
        The DeepSeek prompt advisory says "If fewer than 5 trades exist
        for a direction in the current regime, that is NOT enough to
        justify a flip." Sample reasoning from 2026-05-11 logs showed
        DeepSeek interpreting that clause inversely — treating the
        LOW-DATA direction as untrustworthy and defaulting to the
        HIGH-DATA direction. This code gate is the authoritative,
        binding threshold (``apex_min_trades_for_flip``, raised to 8 by
        E27 on 2026-05-28); it reverts any flip below the threshold
        regardless of what DeepSeek proposes, so the prompt advisory can
        be a softer hint without weakening the guarantee.

        Closes the feedback loop where prior Sell-biased flips inflate
        Sell history and license further Sell flips on coins where Buy
        has no data.

        Counts trades in the flipped direction within the package's
        regime-filtered symbol history. ``package.symbol_history.trades``
        is already regime-filtered by the assembler — no additional
        filtering required.

        Args:
            package: IntelligencePackage from IntelligenceAssembler.
            claude_direction: Brain's original direction.
            qwen_direction: DeepSeek's flipped direction.

        Returns:
            (insufficient: bool, count_in_target_direction: int).
            ``insufficient=True`` means the gate should fire and the
            caller should revert the flip.
        """
        min_trades = int(getattr(
            self._settings, "apex_min_trades_for_flip", 8,
        ))
        if min_trades <= 0:
            return False, -1  # Gate disabled
        # E26 (2026-05-28): prefer per-symbol + per-VENUE evidence when the
        # assembler attached it AND a venue filter was actually applied
        # (exchange_mode non-empty). This stops a flip being licensed on
        # pooled demo/live/paper history. When the live mode was unknown the
        # assembler leaves exchange_mode="" (pooled, non-authoritative) and we
        # fall through to the regime-filtered trades list — the pre-E26 path.
        try:
            ev = getattr(package, "flip_evidence", None)
            if ev is not None and getattr(ev, "exchange_mode", ""):
                _ev_count = ev.direction_count(qwen_direction)
                if isinstance(_ev_count, int) and _ev_count >= 0:
                    log.info(
                        f"APEX_FLIP_EVIDENCE_VENUE | sym={getattr(ev, 'symbol', '?')} "
                        f"venue={ev.exchange_mode} dir={qwen_direction} "
                        f"count={_ev_count} min={min_trades} | {ctx()}"
                    )
                    return (_ev_count < min_trades, _ev_count)
        except Exception:
            pass  # fall through to the pooled trades-list count
        # Defensive boundary: the full read-and-iterate path is wrapped
        # so a malformed package (None trades, non-list trades, non-dict
        # items, missing direction key) can NEVER raise out of the gate.
        # Per the operator's aggressive-exploitation philosophy we fail
        # PERMISSIVE on degraded data: return ``(False, -1)`` so the
        # downstream confidence gate is the only blocker. The sentinel
        # count ``-1`` surfaces "gate non-applicable / unevaluated" in
        # the APEX_FLIP_DECISION log so operators can see the gate did
        # not run on this trade.
        try:
            hist = getattr(package, "symbol_history", None)
            trades = getattr(hist, "trades", None) if hist else None
            if not isinstance(trades, list):
                return False, -1
            count = sum(
                1 for t in trades
                if isinstance(t, dict) and t.get("direction") == qwen_direction
            )
        except Exception:
            return False, -1
        return (count < min_trades, count)

    def _is_counter_trade_setup(self, package: Any) -> bool:
        """PRIMARY Sell-Bias Fix (2026-05-11) — counter-trade detection.

        Returns True when the package's structural data indicates a
        counter-trade setup. The structure engine
        (``src/analysis/structure/structure_engine.py``) emits SetupType
        enum values; only ``BULLISH_FVG_OB_COUNTER`` and
        ``BEARISH_FVG_OB_COUNTER`` (values ``bullish_fvg_ob_counter`` /
        ``bearish_fvg_ob_counter``) are counter-trade setups today.

        Matched with ``endswith("_counter")`` rather than a substring
        check so future SetupType additions like ``BULLISH_ENCOUNTER``
        (unlikely but possible) do not produce false positives. The
        scanner also emits ``COUNTER_TRADE_LONG`` /
        ``COUNTER_TRADE_SHORT`` secondary labels for the same purpose,
        but those are coin-level scanner labels and do not appear on
        ``structural_data.setup_type``; they enter the prompt via brain's
        rendering at ``src/brain/strategist.py:2150-2151``.

        Args:
            package: IntelligencePackage from IntelligenceAssembler.

        Returns:
            True if the package indicates the brain's trade was a
            deliberate contrarian play that APEX should not flip.
        """
        try:
            sd = getattr(package, "structural_data", None)
            if sd is None:
                return False
            setup_type = getattr(sd, "setup_type", "") or ""
            return str(setup_type).lower().endswith("_counter")
        except Exception:
            return False

    def _resolve_flip_threshold(
        self, claude_direction: str, qwen_direction: str,
    ) -> float:
        """Return the confidence floor that applies to this flip pair.

        PRIMARY Sell-Bias Fix (2026-05-11) — asymmetric thresholds.

        Resolution order:
          1. Buy→Sell uses ``apex_min_flip_confidence_buy_to_sell``.
          2. Sell→Buy uses ``apex_min_flip_confidence_sell_to_buy``.
          3. Anything else falls back to the legacy symmetric
             ``apex_min_flip_confidence`` (defensive — covers any future
             direction values such as "Long"/"Short" if they were ever
             plumbed end-to-end).

        Each direction-pair field has its own ``getattr`` default
        matching the dataclass default, so tests that mock ``settings``
        with only the legacy field still work.

        Args:
            claude_direction: The brain's original direction.
            qwen_direction: The direction DeepSeek returned.

        Returns:
            The confidence floor (0.0-1.0) the effective confidence must
            clear for the flip to stand.
        """
        legacy = float(getattr(self._settings, "apex_min_flip_confidence", 0.70))
        if claude_direction == "Buy" and qwen_direction == "Sell":
            return float(getattr(
                self._settings,
                "apex_min_flip_confidence_buy_to_sell",
                legacy,
            ))
        if claude_direction == "Sell" and qwen_direction == "Buy":
            return float(getattr(
                self._settings,
                "apex_min_flip_confidence_sell_to_buy",
                legacy,
            ))
        return legacy

    def _enforce_flip_confidence(
        self,
        optimized: OptimizedTrade,
        claude_direction: str,
        regime: str,
        *,
        effective_confidence: float | None = None,
    ) -> tuple[bool, str]:
        """Definitive-fix Phase 9 — confidence-gated flip discipline.

        Applies AFTER the DeepSeek response has been parsed so we can
        consult ``optimized.confidence``. Only fires when the regime is
        ranging / dead / unknown (the regimes ``_check_direction_lock``
        intentionally leaves unlocked) AND DeepSeek tried to flip
        direction. Below the direction-pair-specific threshold (see
        ``_resolve_flip_threshold``) the flip is reverted; the caller
        is expected to also reset ``optimized.was_flipped``.

        Phase 3 of dir-block-fix (2026-05-05) added the optional
        ``effective_confidence`` keyword argument. When the caller has
        already computed an RR-weighted boost over the raw
        ``optimized.confidence`` (see optimize() at the call site), it
        passes the boosted value here so the gate sees the boosted
        confidence. The boost is NOT mutated onto ``optimized.confidence``
        so downstream consumers see the raw value. When None, falls back
        to the raw ``optimized.confidence`` for backward compatibility.

        PRIMARY Sell-Bias Fix (2026-05-11) — the symmetric
        ``apex_min_flip_confidence`` floor is preserved as the fallback;
        Buy→Sell and Sell→Buy can now have distinct floors via
        ``_resolve_flip_threshold``. Defaults: Buy→Sell 0.95, Sell→Buy
        0.70. Backed by P.1.8 flip-vs-unflip performance data.

        Returns (reverted: bool, reason: str). Caller logs and applies.
        """
        if regime in ("trending_up", "trending_down", "volatile"):
            # Already governed by the pre-call lock; nothing to do.
            return False, ""
        if optimized.direction == claude_direction:
            return False, ""  # No flip happened — nothing to revert
        threshold = self._resolve_flip_threshold(
            claude_direction, optimized.direction,
        )
        if effective_confidence is not None:
            conf = float(effective_confidence)
        else:
            conf = float(getattr(optimized, "confidence", 0.0) or 0.0)
        if conf < threshold:
            return True, (
                f"flip {claude_direction}→{optimized.direction} "
                f"in regime={regime} blocked: conf={conf:.2f}<{threshold:.2f}"
            )
        return False, ""

    def _apply_flip_resize_policy(
        self,
        optimized: "OptimizedTrade",
        *,
        claude_direction: str,
        regime: str,
        symbol: str,
    ) -> None:
        """Post-Execution Closure Fix Phase 2 (2026-05-05).

        Decide whether Qwen's flip-direction resize should be honored or
        capped. Caller has already verified the flip is authorized (i.e.
        the confidence-gate passed) and that ``apex_block_flip_resize``
        is True.

        Policy:
          * ``qwen_size <= original + 0.01``: ACCEPT (smaller sizing
            correctly de-risks the lower-conviction flip).
          * ``qwen_size  > original + 0.01``: CAP back to original (no
            upsizing on flips; the existing 1.5x growth cap in
            ``apex/gate.py`` remains the global ceiling).
          * Within ±0.01 of original: no-op, no log.

        Mutates ``optimized.position_size_usd`` only when capping. Emits
        ``APEX_FLIP_RESIZE_ACCEPTED`` (INFO) on accept and
        ``APEX_FLIP_RESIZE_CAPPED`` (WARNING) on cap. Replaces the
        previous ``APEX_FLIP_RESIZE_BLOCKED`` event.
        """
        _orig_size = float(getattr(optimized, "original_size", 0.0) or 0.0)
        _qwen_size = float(optimized.position_size_usd)
        if _orig_size <= 0:
            return
        if abs(_qwen_size - _orig_size) <= 0.01:
            return
        if _qwen_size > _orig_size:
            log.warning(
                f"APEX_FLIP_RESIZE_CAPPED | sym={symbol} "
                f"flip={claude_direction}→{optimized.direction} "
                f"qwen_size=${_qwen_size:.0f} "
                f"applied=${_orig_size:.0f} "
                f"reason=upsize_on_flip_blocked "
                f"regime={regime} | {ctx()}"
            )
            optimized.position_size_usd = _orig_size
        else:
            log.info(
                f"APEX_FLIP_RESIZE_ACCEPTED | sym={symbol} "
                f"flip={claude_direction}→{optimized.direction} "
                f"qwen_size=${_qwen_size:.0f} "
                f"applied=${_qwen_size:.0f} "
                f"orig_size=${_orig_size:.0f} "
                f"regime={regime} | {ctx()}"
            )
            # No mutation — accept Qwen's smaller sizing.
