"""Ensemble Voter (Layer 3): polls all active strategies for consensus.

Each strategy votes BUY/SELL/NEUTRAL on a scored setup. Votes are weighted
by the strategy's historical win rate. Only setups with sufficient consensus
pass through to Claude Brain (Layer 4).
"""

import time as _time
from datetime import datetime, timezone
from typing import Any

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import OHLCV, Side
from src.strategies.models.regime_types import RegimeState
from src.strategies.models.signal_types import (
    EnsembleResult,
    EnsembleVote,
    ScoredSetup,
)
from src.strategies.registry import StrategyRegistry

log = get_logger("strategies")


# ════════════════════════════════════════════════════════════════════
# EnsembleStateCache (Mid-Hold Trade Management Fix Phase 3.4, 2026-05-19)
# ════════════════════════════════════════════════════════════════════
#
# In-memory per-symbol cache of the latest weighted vote counts. The
# EnsembleVoter writes through to this cache on every ``vote()`` call so
# the PositionWatchdog can read the current ensemble state for an open
# position symbol during ``_monitor_position`` without having to re-run
# the full ensemble. This is the read-side surface for Mid-Hold 1A
# (ensemble-flip event detection).
#
# In-memory only — the cache rebuilds within one signal-worker cycle
# after a restart, which is well below the watchdog's flip-detection
# horizon (5-minute dedupe window).
#
# Public API:
#   record(symbol, buy_votes, sell_votes, neutral_votes) — write-through
#   get_current_consensus(symbol, strong_threshold=4.0) — read for watchdog


class EnsembleStateCache:
    """Per-symbol cache of the latest ensemble weighted-vote counts."""

    def __init__(self) -> None:
        self._state: dict[str, dict] = {}
        # STRONG / GOOD thresholds the cache uses when classifying for the
        # watchdog. The voter calls ``set_strong_threshold`` and
        # ``set_good_threshold`` at __init__ to align the cache with the same
        # StrategyEngineSettings values it uses. Issue #18 + E15 fix
        # (2026-05-28): the standalone defaults below now form a CORRECT ladder
        # (STRONG agree 4.0 > GOOD 2.5; STRONG opp 1.5 < GOOD 2.5). The pre-fix
        # GOOD defaults (5.0 / 1.0) were inverted — stricter than STRONG — so a
        # cache built without the voter wiring would mislabel tiers. Corrected
        # to match StrategyEngineSettings' corrected defaults and the live
        # config; runtime is unchanged because the voter overwrites these at boot.
        self._strong_agree: float = 4.0
        self._strong_opp: float = 1.5
        self._good_agree: float = 2.5
        self._good_opp: float = 2.5

    def set_strong_threshold(
        self, agree_floor: float, opp_ceiling: float,
    ) -> None:
        """Wire the STRONG-consensus thresholds the cache should match.

        Called once from EnsembleVoter.__init__ with the unified
        StrategyEngineSettings values so live and cache classify the
        same vote-count input identically.
        """
        self._strong_agree = float(agree_floor)
        self._strong_opp = float(opp_ceiling)

    def set_good_threshold(
        self, agree_floor: float, opp_ceiling: float,
    ) -> None:
        """Wire the GOOD-consensus thresholds the cache should match.

        Mirrors ``set_strong_threshold`` for the GOOD branch so a
        config.toml override on ``min_ensemble_agreement`` /
        ``max_ensemble_opposition`` flows to the cache too.
        """
        self._good_agree = float(agree_floor)
        self._good_opp = float(opp_ceiling)

    def record(
        self,
        symbol: str,
        buy_votes: float,
        sell_votes: float,
        neutral_votes: float,
        setup_id: str = "",
    ) -> None:
        """Write the latest weighted-vote counts for a symbol.

        Called by EnsembleVoter.vote() right after the directional
        totals are computed (before the consensus classification, since
        the watchdog computes its own classification from the raw
        votes via ``get_current_consensus``).

        Layer 2 Defect 1 (2026-05-22) — also stashes the cycle's
        ``setup_id`` so strategy_worker.register_trade can look it up
        without re-deriving (the join key flows through TradeCoordinator
        to trade_intelligence.setup_id). Default empty string preserves
        the legacy single-arg call convention.
        """
        self._state[symbol] = {
            "buy_votes": float(buy_votes),
            "sell_votes": float(sell_votes),
            "neutral_votes": float(neutral_votes),
            "setup_id": setup_id,
            "ts": _time.time(),
        }

    def get_current_consensus(
        self,
        symbol: str,
        strong_threshold: float | None = None,
    ) -> dict | None:
        """Return the dominant-direction consensus, or None when no data.

        Args:
            symbol: Trade symbol.
            strong_threshold: Optional override for the agreeing-votes
                STRONG floor. When None (default since Layer 1 Defect 6)
                the cache uses the value wired by
                ``set_strong_threshold`` from EnsembleVoter.__init__ —
                matching the live voter exactly. The override remains
                for legacy callers that want to tune just the cache.

        Returns:
            Dict with keys: ``consensus`` (STRONG/GOOD/WEAK/LEAN/CONFLICT),
            ``dominant_dir`` ('BUY' | 'SELL' | 'NEUTRAL'), ``agreeing``,
            ``opposing``, ``ts``. None when no record exists for symbol.
        """
        rec = self._state.get(symbol)
        if rec is None:
            return None
        buy = rec["buy_votes"]
        sell = rec["sell_votes"]
        if buy > sell:
            dom_dir = "BUY"
            agreeing, opposing = buy, sell
        elif sell > buy:
            dom_dir = "SELL"
            agreeing, opposing = sell, buy
        else:
            dom_dir = "NEUTRAL"
            agreeing, opposing = max(buy, sell), min(buy, sell)
        # Layer 1 Defect 6 — classification now uses the same thresholds
        # the live voter reads from StrategyEngineSettings (wired by
        # EnsembleVoter.__init__ via set_strong/good_threshold). The
        # legacy ``strong_threshold`` kwarg is honored when explicitly
        # passed for compatibility with watchdog callers that already
        # tune it via WatchdogSettings.ensemble_flip_strong_threshold.
        _strong_a = (
            float(strong_threshold)
            if strong_threshold is not None
            else self._strong_agree
        )
        if agreeing >= _strong_a and opposing <= self._strong_opp:
            consensus = "STRONG"
        elif agreeing >= self._good_agree and opposing <= self._good_opp:
            consensus = "GOOD"
        elif agreeing >= 1.5 and opposing <= 1.5:
            consensus = "WEAK"
        elif agreeing > opposing:
            consensus = "LEAN"
        else:
            consensus = "CONFLICT"
        return {
            "consensus": consensus,
            "dominant_dir": dom_dir,
            "agreeing": agreeing,
            "opposing": opposing,
            "ts": rec["ts"],
            # Layer 2 Defect 1 — setup_id exposed to callers (strategy_worker
            # at register_trade time) so they can plumb the join key to
            # TradeCoordinator without re-deriving it.
            "setup_id": rec.get("setup_id", ""),
        }


class EnsembleVoter:
    """Polls active strategies for consensus on scored setups.

    Args:
        registry: Strategy registry with performance data.
        settings: Application settings.
        state_cache: Optional EnsembleStateCache; when present, every
            vote() call writes through the raw vote counts so the
            PositionWatchdog can read current consensus per symbol for
            the Mid-Hold Trade Management Fix Phase 3.4 ensemble-flip
            detection. Default None (legacy callers that don't need
            mid-hold monitoring).
        regime_weighter: Optional Layer 3 StrategyWeightDeriver. When
            wired, vote() computes BOTH the live (equal-weight) and
            shadow (regime-conditional) consensus per cycle and logs
            both via STRAT_VOTE_TRACE_SHADOW so the operator can
            observe what the regime-weighted consensus would have been.
            When ``settings.strategy_engine.regime_weighting_enabled``
            is True, the live ensemble switches to the regime-weighted
            consensus (instant rollback by flipping the flag False with
            no code change). Default None preserves equal-weight
            behaviour for legacy callers and tests.
    """

    def __init__(
        self,
        registry: StrategyRegistry,
        settings: Settings,
        state_cache: EnsembleStateCache | None = None,
        regime_weighter: Any | None = None,
    ) -> None:
        self.registry = registry
        self.settings = settings
        self.state_cache = state_cache
        # Layer 3 — optional StrategyWeightDeriver. None disables the
        # shadow / live regime-weighted path entirely (legacy behaviour).
        self._regime_weighter = regime_weighter

        # Boot self-check — STRONG and GOOD consensus thresholds must compose
        # correctly (STRONG at least as strict as GOOD: higher agree floor,
        # lower opp ceiling). Issue #18 + E15 fix (2026-05-28) corrected the
        # code defaults and loader fallbacks so the ladder is sane out of the
        # box; this self-check now AUTO-CORRECTS (clamps STRONG to be at least
        # as strict as GOOD and writes it back so _classify and the cache use
        # the corrected values) instead of warn-and-continue, so a FUTURE config
        # that re-inverts the tiers can never silently mislabel a consensus
        # tier. Wire the state_cache to read the same (corrected) thresholds so
        # cache replay never drifts from the live voter.
        cfg = self.settings.strategy_engine
        _strong_agree = cfg.min_ensemble_agreement_strong
        _strong_opp = cfg.max_ensemble_opposition_strong
        _good_agree = cfg.min_ensemble_agreement
        _good_opp = cfg.max_ensemble_opposition
        if _strong_agree >= _good_agree and _strong_opp <= _good_opp:
            log.info(
                f"BOOT_ENSEMBLE_THRESHOLDS_OK | "
                f"strong_agree>={_strong_agree} strong_opp<={_strong_opp} "
                f"good_agree>={_good_agree} good_opp<={_good_opp} "
                f"| {ctx()}"
            )
        else:
            # Inverted config detected — clamp STRONG to be at least as strict
            # as GOOD on both axes, persist to cfg so the live classifier and
            # the cache use the corrected ladder, and log loudly.
            _orig_agree, _orig_opp = _strong_agree, _strong_opp
            _strong_agree = max(_strong_agree, _good_agree)
            _strong_opp = min(_strong_opp, _good_opp)
            for _attr, _val in (
                ("min_ensemble_agreement_strong", _strong_agree),
                ("max_ensemble_opposition_strong", _strong_opp),
            ):
                try:
                    setattr(cfg, _attr, _val)
                except Exception:  # pragma: no cover — frozen-dataclass guard
                    object.__setattr__(cfg, _attr, _val)
            log.error(
                f"BOOT_ENSEMBLE_THRESHOLDS_AUTOCORRECTED | "
                f"reason=strong_not_strictly_above_good "
                f"strong_was=(>={_orig_agree},<={_orig_opp}) "
                f"good=(>={_good_agree},<={_good_opp}) "
                f"strong_corrected_to=(>={_strong_agree},<={_strong_opp}) "
                f"effect=strong_clamped_at_least_as_strict_as_good | {ctx()}"
            )
        # Issue #19 sentinel (2026-05-27): make the consensus-weighting mode
        # visible at boot. When regime_weighting_enabled is True the consensus
        # is computed with the data-derived per-(strategy, regime) weights
        # (gradual — cells start at equal weight until enough supporting trades,
        # EMA-smoothed, bounded) instead of every strategy counting equally at
        # 1.0. Rollback is a single flag (regime_weighting_enabled=false).
        _rw_live = bool(getattr(cfg, "regime_weighting_enabled", False))
        if _rw_live and self._regime_weighter is not None:
            log.info(
                f"BOOT_REGIME_WEIGHTING_LIVE | enabled=True deriver=wired "
                f"cold_start_n={getattr(cfg, 'regime_weighting_cold_start_n', 20)} "
                f"bounds=[{getattr(cfg, 'regime_weighting_floor', 0.3)},"
                f"{getattr(cfg, 'regime_weighting_ceil', 3.0)}] "
                f"rollback=set_regime_weighting_enabled_false | {ctx()}"
            )
        else:
            log.info(
                f"BOOT_REGIME_WEIGHTING_SHADOW | enabled={_rw_live} "
                f"deriver={'wired' if self._regime_weighter is not None else 'absent'} | {ctx()}"
            )
        # P2 entry-direction fix (2026-06-04) boot sentinel — confirm the
        # two-sided ensemble vote is loaded. When on, vote() polls the
        # opposite direction and surfaces the honest opposing tally to the
        # brain. grep ENSEMBLE_TWO_SIDED for the per-decision evidence.
        log.info(
            f"BOOT_ENSEMBLE_TWO_SIDED | "
            f"enabled={bool(getattr(cfg, 'ensemble_two_sided_vote', False))} "
            f"rollback=set_ensemble_two_sided_vote_false | {ctx()}"
        )
        if self.state_cache is not None:
            # Propagate the unified strong-threshold reading to the
            # cache so get_current_consensus uses the same value as the
            # live voter, ending the pre-fix cache/live drift.
            self.state_cache.set_strong_threshold(
                _strong_agree, _strong_opp,
            )
            self.state_cache.set_good_threshold(
                _good_agree, _good_opp,
            )

    def vote(
        self,
        setup: ScoredSetup,
        candles_map: dict[str, list[OHLCV]],
        ta_map: dict[str, dict],
        sentiment_data: dict | None,
        altdata: dict | None,
        regime: RegimeState,
    ) -> EnsembleResult:
        """Collect votes from all active strategies on a scored setup.

        Excludes the strategy that generated the signal (it already "voted"
        by producing the signal).

        Returns:
            EnsembleResult with weighted vote counts and consensus determination.
        """
        signal = setup.raw_signal
        symbol = signal.symbol
        direction = signal.direction
        originator = signal.strategy_name

        # Layer 2 Defect 1 (2026-05-22) — per-cycle-per-symbol join key.
        # Generated once at the top of vote() so it's available for both
        # the state_cache.record (below, for watchdog reads) and the
        # EnsembleResult (returned to the caller, which uses it as the
        # join key for ensemble_votes + trade_intelligence.setup_id).
        _now = datetime.now(timezone.utc)
        _setup_id = f"{_now.strftime('%Y%m%dT%H%M%S')}_{symbol}"

        active = self.registry.get_active_for_regime(regime.regime)
        candles = candles_map.get(symbol, [])
        ta_data = ta_map.get(symbol, {})

        votes: list[EnsembleVote] = []
        for strategy in active:
            if strategy.name == originator:
                continue
            try:
                vote_str, confidence, reasoning = strategy.vote(
                    symbol=symbol,
                    direction=direction,
                    candles=candles,
                    ta_data=ta_data,
                    sentiment_data=sentiment_data,
                    altdata=altdata,
                )
                perf = self.registry.get_performance(strategy.name)
                weight = perf.ensemble_weight

                votes.append(EnsembleVote(
                    strategy_name=strategy.name,
                    vote=vote_str,
                    confidence=confidence,
                    weight=weight,
                    reasoning=reasoning,
                ))
            except Exception as e:
                # Phase 12.1 (lifecycle-logging-audit Gap 1.9-G1): structured
                # tag replacing tag-less prose.
                log.warning(
                    f"STRAT_VOTE_FAIL | strategy={strategy.name} sym={symbol} "
                    f"err='{str(e)[:120]}' | {ctx()}"
                )

        # Definitive-fix Phase 12 (2026-04-28) — single-strategy cap
        # so a dominant strategy cannot force STRONG on its own. Each
        # contribution = weight*confidence is clamped at
        # ``cap_share`` × the rest-of-ensemble's contribution sum. cap=1.0
        # disables the clamp (default), preserving legacy behaviour;
        # cap=0.4 means no single strategy can supply more than 40% of
        # ``agreeing``, so STRONG requires multiple independent voters.
        cap_share = float(getattr(
            self.settings.strategy_engine, "single_strategy_max_share", 1.0,
        ))

        def _capped_contribution(
            vote_str: str, votes_list: list | None = None,
            *, regime_str: str | None = None,
        ) -> float:
            # P2 cross-check fix (2026-06-04): optional regime weighting so the
            # two-sided opposing tally is computed on the SAME base as the
            # agreeing tally the brain compares it against. When regime_str is
            # given AND the weighter is wired, each vote is scaled by its
            # per-(strategy, regime) factor — identical to the live agreeing
            # path's _shadow_contribution. None reproduces the equal-weight
            # legacy behavior.
            _v = votes if votes_list is None else votes_list

            def _vw(v) -> float:
                _f = 1.0
                if regime_str is not None and self._regime_weighter is not None:
                    _f = self._regime_weighter.get_factor(
                        regime_str, v.strategy_name,
                    )
                return v.weight * _f * v.confidence

            side = [(v, _vw(v)) for v in _v if v.vote == vote_str]
            if not side or cap_share >= 1.0:
                return sum(c for _, c in side)
            total = sum(c for _, c in side)
            capped_sum = 0.0
            for v, c in side:
                # Cap each contribution at cap_share of the rest-of-side total.
                rest = total - c
                ceiling = rest * cap_share / max(1.0 - cap_share, 1e-9)
                if c > ceiling:
                    # E28 (2026-05-28): the dominance cap bound this voter — it
                    # was supplying more than its allowed share of the side
                    # total. Logged so the cap's action is observable and any
                    # over-suppression of legitimate broad agreement is visible.
                    log.info(
                        f"ENSEMBLE_DOMINANCE_CAP_BOUND | path=live side={vote_str} "
                        f"strat={getattr(v, 'strategy_name', '?')} raw={c:.3f} "
                        f"capped={ceiling:.3f} share={cap_share} | {ctx()}"
                    )
                    c = ceiling
                capped_sum += c
            return capped_sum

        buy_votes = _capped_contribution("BUY")
        sell_votes = _capped_contribution("SELL")
        neutral_votes = sum(v.weight for v in votes if v.vote == "NEUTRAL")

        # Mid-Hold Trade Management Fix Phase 3.4 (2026-05-19) — write
        # through to the per-symbol cache so PositionWatchdog can read
        # current consensus for open positions in _monitor_position. The
        # write is best-effort: a cache failure must not affect the
        # ensemble result.
        if self.state_cache is not None:
            try:
                self.state_cache.record(
                    symbol=symbol,
                    buy_votes=buy_votes,
                    sell_votes=sell_votes,
                    neutral_votes=neutral_votes,
                    setup_id=_setup_id,
                )
            except Exception as _e:  # pragma: no cover — observability-only
                log.debug(
                    f"ENSEMBLE_CACHE_WRITE_FAIL | sym={symbol} "
                    f"err='{str(_e)[:80]}' | {ctx()}"
                )

        agreeing = buy_votes if direction == Side.BUY else sell_votes
        opposing = sell_votes if direction == Side.BUY else buy_votes
        consensus_dir = "BUY" if direction == Side.BUY else "SELL"

        # P2 entry-direction fix (2026-06-04) — two-sided vote. The single
        # poll above asked every strategy only the originator's direction;
        # each vote() confirms that direction or returns NEUTRAL but never
        # opposes, so ``opposing`` above is structurally ~0 even when strong
        # opposing signals exist (a bearish supertrend stays NEUTRAL when
        # asked about a Buy). Poll the OPPOSITE direction so the brain reads
        # the honest opposing strength. Surfaced to the brain only — it does
        # NOT change ``buy_votes``/``sell_votes``, the consensus label, the
        # size, or the per-symbol cache (the watchdog reads the legacy tally
        # unchanged). The data, not a one-sided count, decides direction.
        opposing_votes = 0.0
        two_sided_active = bool(getattr(
            self.settings.strategy_engine, "ensemble_two_sided_vote", False,
        ))
        if two_sided_active:
            _opp_dir = Side.SELL if direction == Side.BUY else Side.BUY
            _opp_vote_str = "SELL" if direction == Side.BUY else "BUY"
            _opp_votes: list[EnsembleVote] = []
            for strategy in active:
                if strategy.name == originator:
                    continue
                try:
                    _vs, _conf, _rsn = strategy.vote(
                        symbol=symbol,
                        direction=_opp_dir,
                        candles=candles,
                        ta_data=ta_data,
                        sentiment_data=sentiment_data,
                        altdata=altdata,
                    )
                    _opp_votes.append(EnsembleVote(
                        strategy_name=strategy.name,
                        vote=_vs,
                        confidence=_conf,
                        weight=self.registry.get_performance(
                            strategy.name,
                        ).ensemble_weight,
                        reasoning=_rsn,
                    ))
                except Exception as _e:
                    log.debug(
                        f"STRAT_VOTE_FAIL_OPP | strategy={strategy.name} "
                        f"sym={symbol} err='{str(_e)[:80]}' | {ctx()}"
                    )
            # P2 cross-check fix (2026-06-04): weight the opposing tally on the
            # SAME base as the agreeing tally the brain compares it against.
            # When regime_weighting_enabled is live, the agreeing buy/sell_votes
            # are replaced with regime-weighted values below (~line 650), so the
            # opposing side must also be regime-weighted; otherwise the brain
            # would compare a regime-weighted agreeing side against an
            # equal-weighted opposing side (apples to oranges).
            _opp_regime_str: str | None = None
            if (
                bool(getattr(
                    self.settings.strategy_engine,
                    "regime_weighting_enabled", False,
                ))
                and self._regime_weighter is not None
            ):
                _opp_regime_str = (
                    regime.regime.value
                    if hasattr(regime.regime, "value")
                    else str(regime.regime)
                )
            opposing_votes = _capped_contribution(
                _opp_vote_str, _opp_votes, regime_str=_opp_regime_str,
            )
            log.info(
                f"ENSEMBLE_TWO_SIDED | sym={symbol} "
                f"originator_dir={consensus_dir} agreeing={agreeing:.2f} "
                f"legacy_opposing={opposing:.2f} "
                f"honest_opposing={opposing_votes:.2f} "
                f"opp_voters={len(_opp_votes)} | {ctx()}"
            )

        # Layer 4 (2026-05-22) — CONSENSUS_SIZE note.
        # This map and the size_multiplier value computed from it are
        # DISCARDED for sizing decisions. The brain is the sizer: it
        # reads the consensus label in CALL_A (per the Layer 4 truthful
        # framing at strategist._format_consensus_context) and chooses
        # ``size_usd`` directly in its response. The size_multiplier
        # value flows ONLY to the sort-rank at ensemble.py:613 (a few
        # lines after EnsembleResult construction below) where it
        # multiplies total_score for display ordering of setups.
        #
        # Do NOT wire size_multiplier into any sizing path or order
        # placement — that would force a size from a hardcoded table
        # and betray Layer 4's truth-fix aim (the brain remains the
        # sizer; size emerges from the brain's honest reading of
        # truthful consensus information, not from a code formula).
        # Operator decision at the Phase 2 design gate (2026-05-22)
        # was "leave inert with a clear comment" rather than remove,
        # so the sort-rank consumer below continues to work.
        CONSENSUS_SIZE = {"STRONG": 1.0, "GOOD": 0.75, "LEAN": 0.50, "WEAK": 0.30, "CONFLICT": 0.15}
        cfg = self.settings.strategy_engine
        # Layer 1 Defect 6 — STRONG thresholds read from config
        # (previously hardcoded 4.0/1.5). Defaults now form a correct
        # ladder: STRONG (agree 4.0 / opp 1.5) is genuinely stricter than
        # GOOD (agree 2.5 / opp 2.5). The pre-fix GOOD defaults (5.0 / 1.0)
        # were inverted — stricter than STRONG — which Issue #18/E15
        # corrected. The boot self-check at EnsembleVoter.__init__ now
        # AUTO-CORRECTS any future re-inversion (clamps STRONG to be at
        # least as strict as GOOD, logs BOOT_ENSEMBLE_THRESHOLDS_AUTOCORRECTED),
        # so _classify can never silently mislabel a consensus.
        def _classify(_agree: float, _oppose: float) -> str:
            """Shared consensus-classification logic used for both the
            live (equal-weight) consensus and the shadow (regime-weighted)
            one. Same thresholds, same ordering — only the input weights
            differ."""
            if (
                _agree >= cfg.min_ensemble_agreement_strong
                and _oppose <= cfg.max_ensemble_opposition_strong
            ):
                return "STRONG"
            if _agree >= cfg.min_ensemble_agreement and _oppose <= cfg.max_ensemble_opposition:
                return "GOOD"
            if _agree >= 1.5 and _oppose <= 1.5:
                return "WEAK"
            if _agree > _oppose:
                return "LEAN"
            return "CONFLICT"

        consensus = _classify(agreeing, opposing)
        if consensus == "CONFLICT":
            log.warning(f"ENSEMBLE_CONFLICT | sym={setup.raw_signal.symbol} buy={agreeing:.1f} sell={opposing:.1f} | {ctx()}")

        # Layer 3 (2026-05-22) — regime-conditional shadow / live block.
        # When the deriver is wired AND there is regime data, compute the
        # shadow consensus that would result from applying per-(strategy,
        # regime) factors. ALWAYS log both for the operator's shadow trial
        # comparison. If the live flag is True, REPLACE the live values
        # with the shadow ones (instant rollback by flipping the flag).
        shadow_consensus: str | None = None
        shadow_buy_votes: float = buy_votes
        shadow_sell_votes: float = sell_votes
        shadow_neutral_votes: float = neutral_votes
        shadow_would_change: bool = False
        if self._regime_weighter is not None:
            try:
                _regime_str = (
                    regime.regime.value
                    if hasattr(regime.regime, "value")
                    else str(regime.regime)
                )

                def _shadow_contribution(vote_str: str) -> float:
                    """Re-apply per-vote regime factor and reuse the same
                    cap_share clamp the live path uses."""
                    _side: list[tuple] = []
                    for v in votes:
                        if v.vote != vote_str:
                            continue
                        _factor = self._regime_weighter.get_factor(
                            _regime_str, v.strategy_name,
                        )
                        _side.append((v, v.weight * _factor * v.confidence))
                    if not _side or cap_share >= 1.0:
                        return sum(_c for _, _c in _side)
                    _total = sum(_c for _, _c in _side)
                    _capped = 0.0
                    for _v, _c in _side:
                        _rest = _total - _c
                        _ceiling = _rest * cap_share / max(1.0 - cap_share, 1e-9)
                        if _c > _ceiling:
                            # E28: same cap on the regime-weighted path, which
                            # BECOMES the live consensus when
                            # regime_weighting_enabled is true. Tagged path=shadow.
                            log.info(
                                f"ENSEMBLE_DOMINANCE_CAP_BOUND | path=shadow side={vote_str} "
                                f"strat={getattr(_v, 'strategy_name', '?')} raw={_c:.3f} "
                                f"capped={_ceiling:.3f} share={cap_share} | {ctx()}"
                            )
                            _c = _ceiling
                        _capped += _c
                    return _capped

                shadow_buy_votes = _shadow_contribution("BUY")
                shadow_sell_votes = _shadow_contribution("SELL")
                shadow_neutral_votes = sum(
                    v.weight * self._regime_weighter.get_factor(
                        _regime_str, v.strategy_name,
                    )
                    for v in votes if v.vote == "NEUTRAL"
                )
                _shadow_agree = (
                    shadow_buy_votes if direction == Side.BUY else shadow_sell_votes
                )
                _shadow_opp = (
                    shadow_sell_votes if direction == Side.BUY else shadow_buy_votes
                )
                shadow_consensus = _classify(_shadow_agree, _shadow_opp)
                shadow_would_change = (shadow_consensus != consensus)

                # Shadow observability — always log so the operator can
                # observe per-cycle what the regime weighting would change.
                _flag_on = bool(
                    self.settings.strategy_engine.regime_weighting_enabled,
                )
                log.info(
                    f"STRAT_VOTE_TRACE_SHADOW | sym={symbol} "
                    f"regime={_regime_str} live_uses={'regime' if _flag_on else 'equal'} "
                    f"live_consensus={consensus} live_buy={buy_votes:.2f} live_sell={sell_votes:.2f} "
                    f"shadow_consensus={shadow_consensus} "
                    f"shadow_buy={shadow_buy_votes:.2f} shadow_sell={shadow_sell_votes:.2f} "
                    f"would_change={shadow_would_change} | {ctx()}"
                )

                # Flag-on: flip live to use the regime-weighted values.
                # Done AFTER the log so the line shows what flipped.
                if _flag_on:
                    consensus = shadow_consensus
                    buy_votes = shadow_buy_votes
                    sell_votes = shadow_sell_votes
                    neutral_votes = shadow_neutral_votes
                    agreeing = _shadow_agree
                    opposing = _shadow_opp
            except Exception as _e:  # pragma: no cover — observability-only
                log.debug(
                    f"L3_SHADOW_COMPUTE_FAIL | sym={symbol} "
                    f"err='{str(_e)[:80]}' | {ctx()}"
                )

        size_mult = CONSENSUS_SIZE.get(consensus, 0.3)

        # XRAY counter-setup Phase 5c — scale size_mult by structural
        # confidence so counter setups (Phase 4, conf ≈ 0.35) get a
        # smaller size than in-direction setups (conf ≈ 0.55-0.85) at
        # the same consensus level. Without this, a STRONG-consensus
        # counter setup would size identically to a STRONG-consensus
        # in-direction setup despite the lower structural conviction.
        # Floor at 0.5 mirrors scorer.py:5a and scanner_worker.py:5b —
        # never zero-out legitimate structure. Default 0.85 when the
        # field is absent (legacy producers without setup_type_confidence)
        # preserves pre-fix behavior.
        # Explicit None check so a real 0.0 confidence floors at 0.5
        # instead of falling back to 0.85 via boolean coercion.
        _raw_conf = setup.scoring_details.get("setup_type_confidence")
        _struct_conf = float(_raw_conf) if _raw_conf is not None else 0.85
        _conf_factor = max(0.5, min(1.0, _struct_conf))
        _size_mult_pre = size_mult
        size_mult *= _conf_factor

        result = EnsembleResult(
            scored_setup=setup,
            votes=votes,
            buy_votes=buy_votes,
            sell_votes=sell_votes,
            neutral_votes=neutral_votes,
            opposing_votes=opposing_votes,
            two_sided_active=two_sided_active,
            consensus_strength=consensus,
            consensus_direction=consensus_dir,
            passed=True,  # All pass — consensus for sizing, not filtering
            # Layer 4 (2026-05-22) — size_multiplier is DISCARDED for
            # sizing; consumed ONLY by the sort-rank at ensemble.py:613.
            # See the CONSENSUS_SIZE comment block above for the full
            # rationale. The brain is the sizer.
            size_multiplier=size_mult,
            setup_id=_setup_id,
        )

        log.debug(
            "Ensemble: {sym} {dir} score={sc:.0f} | "
            "votes: buy={b:.1f} sell={s:.1f} neutral={n:.1f} | "
            "consensus={con} size_mult={sm:.2f}",
            sym=symbol, dir=consensus_dir, sc=setup.total_score,
            b=buy_votes, s=sell_votes, n=neutral_votes,
            con=consensus, sm=size_mult,
        )

        # XRAY counter-setup Phase 5c — INFO-level visibility into the
        # confidence weighting decision, emitted only when the factor
        # actually moves size_mult (i.e. struct_conf < 0.85, which
        # means counter setups + low-conviction in-direction). Operators
        # can verify that counter setups are receiving smaller positions
        # than equivalent-consensus in-direction setups in live data.
        if _struct_conf < 0.85:
            log.info(
                f"ENSEMBLE_VOTE_WEIGHTED | sym={symbol} consensus={consensus} "
                f"base_size_mult={_size_mult_pre:.3f} "
                f"struct_conf={_struct_conf:.3f} "
                f"conf_factor={_conf_factor:.3f} "
                f"final_size_mult={size_mult:.3f} | {ctx()}"
            )

        # Definitive-fix Phase 12 (2026-04-28) — per-coin vote trace
        # for STRONG classifications. Forensic D.1.6 documented
        # AAVEUSDT flapping STRONG→GOOD→WEAK→STRONG across 6 cycles
        # with no visibility into which strategies were actually
        # changing. STRAT_VOTE_TRACE makes the cause grep-able from a
        # single line per STRONG coin per cycle:
        #   STRAT_VOTE_TRACE | sym=AAVEUSDT consensus=STRONG agreeing=4.7
        #   opposing=1.2 votes=[name=a3_bb_squeeze_scalp,vote=BUY,
        #   conf=0.85,weight=1.0; name=…]
        # vote_trace_enabled toggles emission so production can silence
        # it once the flap is diagnosed.
        try:
            if (
                consensus == "STRONG"
                and bool(getattr(
                    self.settings.strategy_engine, "vote_trace_enabled", True,
                ))
            ):
                _detail = "; ".join(
                    f"name={v.strategy_name},vote={v.vote},"
                    f"conf={v.confidence:.2f},weight={v.weight:.2f}"
                    for v in votes
                )
                log.info(
                    f"STRAT_VOTE_TRACE | sym={symbol} consensus={consensus} "
                    f"agreeing={agreeing:.2f} opposing={opposing:.2f} "
                    f"votes=[{_detail}] | {ctx()}"
                )
        except Exception as _e:  # pragma: no cover — observability-only
            log.debug(
                f"STRAT_VOTE_TRACE_FAIL | sym={symbol} err='{str(_e)[:80]}' | {ctx()}"
            )
        return result

    def vote_batch(
        self,
        setups: list[ScoredSetup],
        candles_map: dict[str, list[OHLCV]],
        ta_map: dict[str, dict],
        sentiment_data: dict | None,
        altdata: dict | None,
        regime: RegimeState,
        coin_regimes: dict[str, RegimeState] | None = None,
    ) -> list[EnsembleResult]:
        """Vote on ALL setups. Consensus determines size, not eligibility.

        Per-coin-authority Phase 4 (2026-05-29): when ``coin_regimes`` is
        provided, each setup is voted under ITS OWN coin's regime — both the
        voter pool (get_active_for_regime) and the per-(strategy, regime)
        weighter key on that coin's regime, else an explicit UNKNOWN — NEVER the
        global ``regime``. The ``regime`` arg is kept as the legacy/default for
        callers that pass no per-coin map. This makes the consensus the brain
        reads consistent with the coin's own regime (resolves the
        display-vs-execution mismatch).
        """
        results: list[EnsembleResult] = []
        for setup in setups:
            _setup_regime = regime
            if coin_regimes is not None:
                _sym = setup.raw_signal.symbol
                _setup_regime = coin_regimes.get(_sym) or RegimeState.unknown()
            result = self.vote(setup, candles_map, ta_map, sentiment_data, altdata, _setup_regime)
            results.append(result)

        # Sort by effective strength: score * consensus size_multiplier
        # Layer 4 (2026-05-22) — this is the ONLY live consumer of
        # size_multiplier. The product is a sort key for display
        # ordering only; it does NOT flow to position size, order
        # qty, or any other sizing decision. The brain (Layer 4
        # truthful framing) is the sizer; see CONSENSUS_SIZE comment
        # block in vote() above for the full rationale.
        results.sort(key=lambda r: (
            r.scored_setup.total_score * r.size_multiplier,
        ), reverse=True)

        _str_counts = {}
        for r in results:
            _str_counts[r.consensus_strength] = _str_counts.get(r.consensus_strength, 0) + 1
        log.info(f"ENSEMBLE | setups={len(results)} strong={_str_counts.get('STRONG', 0)} good={_str_counts.get('GOOD', 0)} weak={_str_counts.get('WEAK', 0)} conflict={_str_counts.get('CONFLICT', 0)} | {ctx()}")

        return results

    @staticmethod
    async def persist_votes(db: Any, results: list[EnsembleResult]) -> int:
        """Layer 2 Defect 1 — batched per-cycle per-strategy vote persistence.

        After ``vote_batch`` completes, the caller invokes this helper to
        write every individual ``EnsembleVote`` row to the ``ensemble_votes``
        table in a single batched executemany — one DB roundtrip for the
        full cycle's worth of votes (~1,050 rows for 30 coins × 35 strategies).

        Failure handling per Rule 7: persistence failure is logged loud
        (D1_PERSIST_FAIL) but does NOT raise — trading must continue even
        if the write fails. Returns the row count written (0 on failure or
        empty input). The setup_id on each EnsembleResult is the join key
        to trade_intelligence rows that subsequently open.

        Args:
            db: DatabaseManager instance (executemany capability).
            results: List of EnsembleResult objects from vote_batch().

        Returns:
            Number of EnsembleVote rows successfully written.
        """
        rows: list[tuple] = []
        for r in results:
            if not r.setup_id or not r.votes:
                continue
            symbol = r.scored_setup.raw_signal.symbol
            direction = (
                r.scored_setup.raw_signal.direction.value
                if hasattr(r.scored_setup.raw_signal.direction, "value")
                else str(r.scored_setup.raw_signal.direction)
            )
            for v in r.votes:
                rows.append((
                    r.setup_id, symbol, direction, v.strategy_name,
                    v.vote, float(v.confidence), float(v.weight),
                    (v.reasoning or "")[:500],
                ))
        if not rows:
            return 0
        try:
            await db.executemany(
                """
                INSERT INTO ensemble_votes
                    (setup_id, symbol, direction, strategy_name,
                     vote, confidence, weight, reasoning)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            log.info(
                f"D1_VOTES_PERSIST_OK | setups={len(results)} "
                f"rows={len(rows)} | {ctx()}"
            )
            return len(rows)
        except Exception as e:
            log.error(
                f"D1_VOTES_PERSIST_FAIL | setups={len(results)} "
                f"attempted_rows={len(rows)} err='{str(e)[:120]}' | {ctx()}"
            )
            return 0
