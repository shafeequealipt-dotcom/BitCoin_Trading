"""Market Regime Detector: classifies current market conditions.

Uses BTC as the primary indicator since it leads the crypto market.
"""

import numpy as np

from src.analysis.engine import TAEngine
from src.analysis.indicators import volatility
from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import TimeFrame
from src.database.repositories.market_repo import MarketRepository
from src.strategies.models.regime_types import (
    REGIME_ACTIVE_CATEGORIES,
    MarketRegime,
    RegimeState,
)

log = get_logger("strategies")


class RegimeDetector:
    """Classifies the market into one of 5 regimes.

    Args:
        settings: Application settings.
        ta_engine: For computing indicators.
        market_repo: For fetching klines from DB.
    """

    def __init__(
        self,
        settings: Settings,
        ta_engine: TAEngine,
        market_repo: MarketRepository,
    ) -> None:
        self.settings = settings
        self.ta_engine = ta_engine
        self.market_repo = market_repo
        self._last_regime: RegimeState | None = None
        self._per_coin_regimes: dict[str, RegimeState] = {}
        # Hysteresis: require 2 consecutive readings of a new regime before confirming.
        # Prevents flip-flopping when ADX/choppiness hover near classification thresholds.
        self._confirmed_regimes: dict[str, RegimeState] = {}
        self._pending_regime: dict[str, tuple[MarketRegime, int]] = {}
        # Issue 2.4 (2026-06-07) boot sentinel — confirm the calibrated breadth
        # brake config loaded (vs the pre-2.4 0.65/0.50 curve), per Rule 12.
        _bcfg = getattr(settings, "regime", None)
        if _bcfg is not None:
            log.info(
                f"BREADTH_BRAKE_INIT | enabled={getattr(_bcfg, 'breadth_brake_enabled', True)} "
                f"start={getattr(_bcfg, 'breadth_brake_start', 0.60):.2f} "
                f"floor={getattr(_bcfg, 'breadth_brake_floor', 0.40):.2f} "
                f"min_coins={getattr(_bcfg, 'breadth_brake_min_coins', 10)} | {ctx()}"
            )

    def get_coin_regime(self, symbol: str) -> "RegimeState | None":
        """Get per-coin regime if available, else None (use global)."""
        return self._per_coin_regimes.get(symbol)

    def is_ready(self) -> bool:
        """Definitive-fix Phase 7 — has the per-coin regime cache populated yet?

        Returns True iff at least one ``_per_coin_regimes`` entry exists.
        Used by APEX/TIAS callers (and the worker liveness watchdog) to
        distinguish "cold-start race — RegimeWorker first tick hasn't
        landed yet" from "consistent miss — should fall back to global".
        Cheap (zero IO) — safe to call from per-call hot paths.
        """
        return bool(self._per_coin_regimes)

    def get_last_regime(self) -> "RegimeState | None":
        """Return the most-recently-computed global RegimeState.

        Zero-cost read (no IO, no compute). `_last_regime` is updated on
        every successful `detect()` call (see the four assignment sites in
        the method below, including the hysteresis branches). Returns None
        ONLY before the first detect() call has completed (boot race);
        callers MUST handle that by falling through to `await detect()`.

        Exposed for consumers that run on a faster cadence than RegimeWorker
        (~600s interval) and just need the last known value — e.g. the
        strategist's market_data prompt build, which previously did a full
        re-detection per cycle. Zero-cost here vs ~150-200ms for detect()
        (which fetches 200 H1 klines + runs full TA + hysteresis).
        """
        return self._last_regime

    def _make_unknown(self) -> RegimeState:
        """Per-coin-authority Phase 0b — build an explicit UNKNOWN state.

        Emitted when a coin cannot be classified: insufficient klines (<50), a
        core TA input (ADX / choppiness) genuinely absent, or per-coin
        detection raised. Distinct from a real RANGING reading so consumers can
        tell "no data" from a genuine range. Carries zero-confidence neutral
        metrics and the BROAD, non-restrictive UNKNOWN strategy roster
        (operator decision: UNKNOWN coins still trade on their own TA/structure,
        they are NOT silenced). Never goes through hysteresis — it is a
        data-availability state, not a market state. Delegates to the canonical
        RegimeState.unknown() factory (single source of truth).
        """
        return RegimeState.unknown()

    def breadth_sizing(self) -> tuple[float, dict]:
        """Per-coin-authority Phase 5 (2026-05-29): the breadth RISK/SIZING brake.

        Returns ``(size_multiplier, info)`` derived from the PER-COIN regime
        distribution (NOT a single coin). This is the ONLY sanctioned survivor
        of the old "global regime" concept: it shrinks position size when the
        whole universe is directionally lopsided (most coins trending the same
        way -> high correlation -> systemic risk). It NEVER sets a direction and
        NEVER selects a strategy roster — sizing only. Zero-IO (reads the cached
        per-coin regimes), safe to call from the APEX gate hot path.

        Graduated: multiplier is 1.0 while the dominant trending-direction share
        is <= ``breadth_brake_start``, then falls linearly to
        ``breadth_brake_floor`` as that share approaches 1.0. UNKNOWN coins are
        excluded from the denominator; with fewer than ``breadth_brake_min_coins``
        classified the brake is disabled (too little breadth to judge -> 1.0).
        """
        cfg = self.settings.regime
        info = {
            "down_share": 0.0, "up_share": 0.0, "lopsided": 0.0,
            "classified": 0, "mult": 1.0,
        }
        if not getattr(cfg, "breadth_brake_enabled", True):
            return 1.0, info
        regimes = self._per_coin_regimes or {}
        up = down = classified = 0
        for rs in regimes.values():
            r = (
                rs.regime.value if hasattr(rs.regime, "value") else str(rs.regime)
            ).lower()
            if "unknown" in r:
                continue  # exclude UNKNOWN from the denominator
            classified += 1
            if "up" in r:
                up += 1
            elif "down" in r:
                down += 1
        info["classified"] = classified
        if classified < int(getattr(cfg, "breadth_brake_min_coins", 10)):
            return 1.0, info
        down_share = down / classified
        up_share = up / classified
        lopsided = max(down_share, up_share)
        info.update(
            {"down_share": down_share, "up_share": up_share, "lopsided": lopsided}
        )
        start = float(getattr(cfg, "breadth_brake_start", 0.60))  # Issue 2.4: aligned to calibrated default
        floor = float(getattr(cfg, "breadth_brake_floor", 0.40))  # Issue 2.4: aligned to calibrated default
        if lopsided <= start:
            mult = 1.0
        else:
            frac = (lopsided - start) / max(1e-9, (1.0 - start))
            mult = 1.0 - (1.0 - floor) * min(1.0, max(0.0, frac))
        mult = max(floor, min(1.0, mult))
        info["mult"] = mult
        return mult, info

    async def detect(self, symbol: str | None = None) -> RegimeState:
        """Detect the current market regime for a symbol.

        Args:
            symbol: Defaults to settings.regime.primary_symbol (BTCUSDT).

        Returns:
            RegimeState with classification and supporting metrics.
        """
        symbol = symbol or self.settings.regime.primary_symbol
        cfg = self.settings.regime

        klines = await self.market_repo.get_klines(symbol, TimeFrame.H1.value, 200)
        if len(klines) < 50:
            # Per-coin-authority Phase 0b (2026-05-29): insufficient data is no
            # longer fabricated as RANGING/0.30 (downstream could not tell that
            # apart from a genuine range). Emit an explicit UNKNOWN so the honest
            # "no data" state propagates. ``_last_regime`` is still populated
            # (non-None) so get_last_regime() callers don't force a re-detect.
            log.warning(
                f"REGIME_INSUFFICIENT_KLINES | sym={symbol} n={len(klines)} "
                f"-> UNKNOWN | {ctx()}"
            )
            unknown = self._make_unknown()
            self._last_regime = unknown
            return unknown

        ta = await self.ta_engine.analyze(candles=klines)

        # Per-coin-authority Phase 0c (2026-05-29): distinguish a genuinely
        # MISSING core input from a real value. The old `or 0` / `or 50` /
        # `or 1.0` masked absent TA fields with constants (choppiness=50 sits
        # exactly on the ranging threshold), so a data gap silently produced a
        # confident-looking label. ADX and choppiness are the core classifiers:
        # if either is absent, emit UNKNOWN rather than fabricate from
        # constants. Non-core fields (DI, volume) keep benign fallbacks.
        _adx_raw = ta.get("trend", {}).get("adx", {}).get("adx")
        _chop_raw = ta.get("volatility", {}).get("choppiness_index")
        if _adx_raw is None or _chop_raw is None:
            log.warning(
                f"REGIME_MISSING_FIELD | sym={symbol} adx={_adx_raw} "
                f"chop={_chop_raw} -> UNKNOWN | {ctx()}"
            )
            unknown = self._make_unknown()
            self._last_regime = unknown
            return unknown
        adx = float(_adx_raw)
        plus_di = float(ta.get("trend", {}).get("adx", {}).get("plus_di") or 0)
        minus_di = float(ta.get("trend", {}).get("adx", {}).get("minus_di") or 0)
        choppiness = float(_chop_raw)
        # Issue #3B (2026-05-31): distinguish a genuinely-missing volume ratio
        # from a real one. The old `or 1.0` silently turned BOTH a missing value
        # AND a true 0.0 into a healthy-looking 1.0, masking thin/new coins as
        # average-volume. Keep a neutral 1.0 for the classification arithmetic
        # (so the formula is byte-identical when present — no recalibration), but
        # carry `volume_ratio_known` so the DEAD/VOLATILE branches can decline to
        # use volume when it is absent, and the brain prompt can render `n/a`.
        _vol_sma_ratio = ta.get("volume", {}).get("volume_sma_ratio")
        volume_ratio_known = _vol_sma_ratio is not None
        volume_ratio = float(_vol_sma_ratio) if volume_ratio_known else 1.0

        # Issue #9 fix (2026-05-27): a TRUE rolling percentile, not scaled NATR.
        # The old `atr_percentile = natr * 100` was a fixed absolute level (live
        # max was 641 — impossible for a percentile), so VOLATILE fired on
        # merely-normal movement. Rank the current NATR against the NATR series
        # over the already-fetched window (cheap numpy; reuses volatility.natr,
        # no extra IO, no TAEngine change). Bounded 0-100, self-normalizing
        # across high- and low-volatility coins.
        natr = ta.get("volatility", {}).get("natr_14") or 1.0
        try:
            _highs = np.array([k.high for k in klines], dtype=np.float64)
            _lows = np.array([k.low for k in klines], dtype=np.float64)
            _closes = np.array([k.close for k in klines], dtype=np.float64)
            _natr_series = volatility.natr(_highs, _lows, _closes, 14)
            _valid = _natr_series[np.isfinite(_natr_series)]
            if _valid.size >= 20:
                _current = float(_valid[-1])
                # Percentile rank: fraction of the window at or below current.
                atr_percentile = float((_valid <= _current).sum()) / float(_valid.size) * 100.0
                natr = _current
            else:
                # Cold-start (too few valid bars for a stable rank): fall back to
                # the bounded scaled value rather than fabricate a rank.
                atr_percentile = min(natr * 100.0, 100.0)
        except Exception as _e:  # pragma: no cover — defensive; never crash detect()
            atr_percentile = min(natr * 100.0, 100.0)
            log.warning(f"REGIME_ATR_PCT_FALLBACK | sym={symbol} err={str(_e)[:80]} | {ctx()}")

        # Classification
        regime: MarketRegime
        confidence: float
        trend_direction: int

        # Per-coin-authority Phase 0a (2026-05-29): classify trend and RANGE/
        # DEAD STRUCTURE *before* the VOLATILE magnitude test. Previously
        # VOLATILE was evaluated first, so a low-ADX high-choppiness coin (a
        # textbook RANGING signature) whose own NATR ticked into its top
        # percentile was mislabelled VOLATILE — and VOLATILE then silenced the
        # mean-reversion strategies a ranging coin needs. Structure now wins;
        # VOLATILE only catches coins that are neither cleanly trending, nor
        # ranging, nor dead, yet still carry elevated volatility.
        if adx > cfg.trending_adx_threshold and plus_di > minus_di and choppiness < cfg.trending_choppiness_max:
            regime = MarketRegime.TRENDING_UP
            confidence = min(adx / 50, 1.0)
            trend_direction = 1
        elif adx > cfg.trending_adx_threshold and minus_di > plus_di and choppiness < cfg.trending_choppiness_max:
            regime = MarketRegime.TRENDING_DOWN
            confidence = min(adx / 50, 1.0)
            trend_direction = -1
        elif adx < cfg.ranging_adx_threshold and choppiness > cfg.ranging_choppiness_threshold:
            regime = MarketRegime.RANGING
            confidence = min(choppiness / 80, 1.0)
            trend_direction = 0
        elif (
            adx < cfg.dead_adx_threshold
            and volume_ratio_known and volume_ratio < cfg.dead_volume_ratio
            and atr_percentile < 50
        ):
            # Issue #3B: require KNOWN volume for the DEAD volume sub-condition.
            # A coin with no volume data must not be forced DEAD on a placeholder
            # ratio — it falls through to the fully-tiled else branch and is
            # classified by ADX/choppiness instead.
            regime = MarketRegime.DEAD
            confidence = 0.8
            trend_direction = 0
        elif atr_percentile > cfg.volatile_atr_percentile or (
            volume_ratio_known and volume_ratio > cfg.volatile_volume_ratio
        ):
            regime = MarketRegime.VOLATILE
            # Issue #9 fix: non-degenerate confidence. atr_percentile is now a
            # real 0-100 rank, so the old `/200` (which capped at 0.5) is
            # replaced by the stronger of the two triggers' normalized
            # strength, floored so a volatile label is always signal-bearing.
            _vol_by_pct = min(atr_percentile / 100.0, 1.0)
            # Issue #3B: only let volume contribute to VOLATILE confidence when it
            # is a real measurement; with volume unknown this branch was reached
            # via the atr_percentile trigger, so confidence rests on _vol_by_pct.
            _vol_by_volume = min(volume_ratio / 4.0, 1.0) if volume_ratio_known else 0.0
            confidence = max(0.40, _vol_by_pct, _vol_by_volume)
            # Per-coin-authority Phase 0d (2026-05-29): VOLATILE asserts NO
            # direction. The old `1 if plus_di>minus_di else -1` injected a
            # spurious long/short lean on a coin with no real trend; once
            # per-coin direction is authoritative that noise would steer trades.
            trend_direction = 0
        else:
            # Issue #6 tiling fix (2026-05-27): the (ADX, choppiness) plane
            # previously left a structural dead-zone — coins meeting none of
            # the strict branches above fell through to a fabricated
            # RANGING / 0.40 label (~37% of all classifications, confirmed on
            # live data). That suppressed momentum/predatory voting and fed
            # the brain a false 40% confidence. The space is now fully tiled:
            # a fall-through coin is classified by its dominant metric with a
            # COMPUTED, signal-bearing confidence, never a flat constant.
            if adx > cfg.trending_adx_threshold and plus_di != minus_di:
                # ADX shows directional pressure but choppiness exceeded the
                # clean-trend ceiling — a weak/choppy trend, NOT range-bound.
                # Labeling it trending (not RANGING) keeps momentum strategies
                # eligible to vote, per the aggression aim.
                regime = (
                    MarketRegime.TRENDING_UP if plus_di > minus_di
                    else MarketRegime.TRENDING_DOWN
                )
                trend_direction = 1 if plus_di > minus_di else -1
                # Confidence = ADX strength tempered by how far choppiness sits
                # above the clean-trend ceiling (choppier -> weaker conviction).
                _chop_excess = max(0.0, choppiness - cfg.trending_choppiness_max)
                _chop_penalty = max(0.4, 1.0 - _chop_excess / 100.0)
                confidence = max(0.3, min((adx / 50.0) * _chop_penalty, 1.0))
            else:
                # No meaningful directional pressure -> ranging. Confidence is
                # derived from choppiness (more choppy -> more confidently
                # mean-reverting), bounded so it is always signal-bearing
                # rather than the old flat 0.40.
                regime = MarketRegime.RANGING
                confidence = max(0.30, min(choppiness / 100.0, 0.95))
                trend_direction = 0
            log.info(
                f"REGIME_TILED | sym={symbol} rgm={regime.value} conf={confidence:.2f} "
                f"adx={adx:.1f} chop={choppiness:.1f} +di={plus_di:.1f} -di={minus_di:.1f} "
                f"reason=former_else_deadzone | {ctx()}"
            )

        active_cats = REGIME_ACTIVE_CATEGORIES.get(regime, [])

        state = RegimeState(
            regime=regime,
            confidence=confidence,
            adx=adx,
            atr_percentile=atr_percentile,
            choppiness=choppiness,
            volume_ratio=volume_ratio,
            volume_ratio_known=volume_ratio_known,
            trend_direction=trend_direction,
            active_strategy_categories=list(active_cats),
        )

        log.info(f"REGIME | sym={symbol} rgm={regime.value} conf={confidence:.2f} adx={adx:.1f} chop={choppiness:.1f} atr_pct={atr_percentile:.1f} natr={natr:.3f} | {ctx()}")

        # Per-symbol hysteresis: require 2 consecutive readings of the same new regime
        # before confirming a change. Eliminates single-snapshot flip-flopping caused
        # by ADX/choppiness hovering near thresholds in StrategyWorker's 45-second calls.
        confirmed = self._confirmed_regimes.get(symbol)

        if confirmed is None:
            # First reading for this symbol — immediately confirm; no prior state to compare.
            self._confirmed_regimes[symbol] = state
            self._pending_regime.pop(symbol, None)
            self._last_regime = state
            return state

        if regime == confirmed.regime:
            # Same regime as confirmed — update metrics in-place, clear any pending candidate.
            self._confirmed_regimes[symbol] = state
            self._pending_regime.pop(symbol, None)
            self._last_regime = state
            return state

        # Regime differs from confirmed — apply hysteresis.
        pending_regime, pending_count = self._pending_regime.get(symbol, (None, 0))
        new_count = (pending_count + 1) if pending_regime == regime else 1

        # Phase 3 (output-quality): hysteresis count is now config-driven
        # via [regime] hysteresis_count (default 2). Pre-fix it was a
        # hardcoded magic number; operators can tune for sticky vs
        # responsive regime classification without redeploy.
        _hyst = int(getattr(cfg, "hysteresis_count", 2))
        if new_count >= _hyst:
            # Required-N consecutive reading of the new regime — confirm the change.
            self._confirmed_regimes[symbol] = state
            self._pending_regime.pop(symbol, None)
            _old_rgm = confirmed.regime.value
            log.warning(
                f"REGIME_CHG | sym={symbol} old={_old_rgm} new={regime.value} conf={confidence:.2f} | {ctx()}"
            )
            log.info(
                "Regime change: {old} -> {new} (conf={c:.2f}, ADX={adx:.1f})",
                old=_old_rgm, new=regime.value, c=confidence, adx=adx,
            )
            self._last_regime = state
            return state
        else:
            # First reading of a new regime — hold confirmed, track the candidate.
            self._pending_regime[symbol] = (regime, new_count)
            log.info(
                f"REGIME_PENDING | sym={symbol} confirmed={confirmed.regime.value} "
                f"candidate={regime.value} count={new_count}/{_hyst} adx={adx:.1f} | {ctx()}"
            )
            self._last_regime = confirmed
            return confirmed  # return stable confirmed regime until hysteresis satisfied

    async def detect_per_coin(self, symbols: list[str]) -> dict[str, RegimeState]:
        """Detect regime for each symbol individually."""
        results: dict[str, RegimeState] = {}
        for symbol in symbols:
            try:
                results[symbol] = await self.detect(symbol)
            except Exception as e:
                # Per-coin-authority Phase 0b (2026-05-29): on a per-coin
                # detection failure, emit an explicit UNKNOWN rather than
                # OMITTING the symbol. RegimeWorker merges results with
                # ``.update()``, so an omitted symbol silently RETAINED its
                # prior (possibly confident) label — a stale-label trap. UNKNOWN
                # is honest; consumers fall back to last-known/UNKNOWN by choice
                # (Phase 2), not by accident.
                log.warning(
                    f"REGIME_DETECT_FAILED | sym={symbol} err={str(e)[:80]} "
                    f"-> UNKNOWN | {ctx()}"
                )
                results[symbol] = self._make_unknown()
        return results
