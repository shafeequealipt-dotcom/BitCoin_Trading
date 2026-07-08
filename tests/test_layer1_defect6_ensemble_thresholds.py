"""Layer 1 Defect 6 — ensemble consensus threshold reconciliation.

Pre-fix:
- EnsembleVoter.vote (ensemble.py:263) hardcoded STRONG at
  ``agreeing >= 4.0 AND opposing <= 1.5``.
- GOOD read from config (settings default 5.0 / 1.0).
- STRONG floor was LOWER than GOOD floor — branch ordering meant a
  vote of (agreeing=4.0, opposing=1.5) classified STRONG even though
  it failed the GOOD floor.
- EnsembleStateCache.get_current_consensus hardcoded BOTH thresholds
  so any config.toml override on min_ensemble_agreement /
  max_ensemble_opposition produced cache-vs-live divergence.

Fix (Layer 1 Defect 6 + Issue #18/E15):
- STRONG thresholds promoted to config (min_ensemble_agreement_strong,
  max_ensemble_opposition_strong); defaults 4.0 / 1.5.
- Issue #18/E15: the inverted GOOD defaults (5.0 / 1.0 — stricter than
  STRONG) were corrected to 2.5 / 2.5 in StrategyEngineSettings, the
  loader fallbacks, and the EnsembleStateCache defaults, forming a
  correct ladder (STRONG agree 4.0 > GOOD 2.5; STRONG opp 1.5 < GOOD
  2.5). config.toml already supplied 2.5 / 2.5, so live runtime is
  unchanged — the fix removes the silent-regression risk of a
  config-less deploy.
- EnsembleVoter.__init__ wires the cache's STRONG and GOOD thresholds
  to the same StrategyEngineSettings values so cache and live agree
  by construction.
- Boot self-check logs BOOT_ENSEMBLE_THRESHOLDS_OK when the ladder is
  sane and AUTO-CORRECTS (clamps STRONG to be at least as strict as
  GOOD, logs BOOT_ENSEMBLE_THRESHOLDS_AUTOCORRECTED) on a future
  re-inversion, so a misconfig can never silently mislabel a consensus.
"""

from __future__ import annotations


def test_strong_thresholds_are_in_settings() -> None:
    """STRONG fields exist (4.0 / 1.5); GOOD defaults corrected to
    2.5 / 2.5 (Issue #18/E15); and config-less defaults form a correct
    ladder — STRONG strictly stricter than GOOD on BOTH axes."""
    from src.config.settings import StrategyEngineSettings
    cfg = StrategyEngineSettings()
    # STRONG unchanged.
    assert cfg.min_ensemble_agreement_strong == 4.0
    assert cfg.max_ensemble_opposition_strong == 1.5
    # GOOD corrected from the inverted 5.0 / 1.0 to 2.5 / 2.5.
    assert cfg.min_ensemble_agreement == 2.5
    assert cfg.max_ensemble_opposition == 2.5
    # Config-less ladder: STRONG requires MORE agreement and tolerates
    # LESS opposition than GOOD.
    assert cfg.min_ensemble_agreement_strong > cfg.min_ensemble_agreement
    assert cfg.max_ensemble_opposition_strong < cfg.max_ensemble_opposition


def test_cache_strong_and_good_thresholds_are_set_by_voter() -> None:
    """When the EnsembleVoter is constructed with a cache, both STRONG
    and GOOD thresholds propagate to the cache so its classifier
    matches the live voter exactly."""
    from unittest.mock import MagicMock

    from src.config.settings import StrategyEngineSettings
    from src.strategies.ensemble import EnsembleStateCache, EnsembleVoter

    cache = EnsembleStateCache()
    # Defaults out of the box: STRONG 4.0 / 1.5; GOOD corrected to
    # 2.5 / 2.5 (Issue #18/E15 — was the inverted 5.0 / 1.0).
    assert cache._strong_agree == 4.0
    assert cache._strong_opp == 1.5
    assert cache._good_agree == 2.5
    assert cache._good_opp == 2.5

    # Construct a voter with custom-tuned settings; the wiring should
    # rewrite the cache's thresholds to match.
    cfg = StrategyEngineSettings(
        min_ensemble_agreement=6.0,
        max_ensemble_opposition=0.8,
        min_ensemble_agreement_strong=8.0,
        max_ensemble_opposition_strong=0.5,
    )
    settings = MagicMock()
    settings.strategy_engine = cfg
    registry = MagicMock()

    EnsembleVoter(registry=registry, settings=settings, state_cache=cache)

    assert cache._strong_agree == 8.0
    assert cache._strong_opp == 0.5
    assert cache._good_agree == 6.0
    assert cache._good_opp == 0.8


def test_cache_and_live_agree_on_same_vote_input_under_override() -> None:
    """A tuned config (STRONG floor strictly above GOOD floor) must
    produce the same consensus label from the cache as from the live
    voter for the same vote-count input. This is the contract the
    pre-fix code broke whenever config.toml overrode the defaults."""
    from unittest.mock import MagicMock

    from src.config.settings import StrategyEngineSettings
    from src.strategies.ensemble import EnsembleStateCache, EnsembleVoter

    # Tightened STRONG above GOOD per the operator-decision option;
    # both branches read from settings now.
    cfg = StrategyEngineSettings(
        min_ensemble_agreement=5.0,
        max_ensemble_opposition=1.0,
        min_ensemble_agreement_strong=6.5,
        max_ensemble_opposition_strong=0.5,
    )
    cache = EnsembleStateCache()
    settings = MagicMock()
    settings.strategy_engine = cfg
    registry = MagicMock()
    EnsembleVoter(registry=registry, settings=settings, state_cache=cache)

    # A vote that meets GOOD floor but not STRONG: agreeing=5, opp=1
    cache.record(symbol="BTCUSDT", buy_votes=5.0,
                 sell_votes=1.0, neutral_votes=0.0)
    result = cache.get_current_consensus("BTCUSDT")
    assert result is not None
    assert result["consensus"] == "GOOD"

    # A vote that meets STRONG floor: agreeing=7, opp=0
    cache.record(symbol="ETHUSDT", buy_votes=7.0,
                 sell_votes=0.0, neutral_votes=0.0)
    result = cache.get_current_consensus("ETHUSDT")
    assert result is not None
    assert result["consensus"] == "STRONG"


def test_default_ladder_is_correct_after_fix() -> None:
    """Under the corrected default settings (STRONG=4.0/1.5,
    GOOD=2.5/2.5 — Issue #18/E15), the ladder is sane: a vote that
    meets GOOD but not STRONG classifies GOOD, and a vote meeting the
    STRONG floor classifies STRONG. The cache (wired by the voter)
    agrees with the live thresholds. This replaces the pre-fix
    'legacy lie' test, where the inverted 5.0/1.0 GOOD floor sat above
    STRONG."""
    from unittest.mock import MagicMock

    from src.config.settings import StrategyEngineSettings
    from src.strategies.ensemble import EnsembleStateCache, EnsembleVoter

    cfg = StrategyEngineSettings()
    # The defaults now compose correctly — the boot self-check sees a
    # sane ladder and does NOT auto-correct.
    assert cfg.min_ensemble_agreement_strong > cfg.min_ensemble_agreement
    assert cfg.max_ensemble_opposition_strong < cfg.max_ensemble_opposition

    cache = EnsembleStateCache()
    settings = MagicMock()
    settings.strategy_engine = cfg
    registry = MagicMock()
    EnsembleVoter(registry=registry, settings=settings, state_cache=cache)

    # Meets GOOD (agree>=2.5, opp<=2.5) but not STRONG (agree<4.0): GOOD.
    cache.record(symbol="SOLUSDT", buy_votes=3.0,
                 sell_votes=2.0, neutral_votes=0.0)
    result = cache.get_current_consensus("SOLUSDT")
    assert result is not None
    assert result["consensus"] == "GOOD"

    # Meets STRONG (agree>=4.0, opp<=1.5): STRONG.
    cache.record(symbol="DOGEUSDT", buy_votes=4.0,
                 sell_votes=1.5, neutral_votes=0.0)
    result = cache.get_current_consensus("DOGEUSDT")
    assert result is not None
    assert result["consensus"] == "STRONG"


def test_boot_autocorrects_a_reinverted_config() -> None:
    """A deliberately re-inverted config (STRONG floor below GOOD) is
    clamped by the boot self-check so STRONG ends up at least as strict
    as GOOD on both axes (Issue #18/E15 auto-correct)."""
    from unittest.mock import MagicMock

    from src.config.settings import StrategyEngineSettings
    from src.strategies.ensemble import EnsembleStateCache, EnsembleVoter

    # Re-invert: STRONG agree (3.0) below GOOD agree (5.0); STRONG opp
    # (2.0) above GOOD opp (1.0).
    cfg = StrategyEngineSettings(
        min_ensemble_agreement=5.0,
        max_ensemble_opposition=1.0,
        min_ensemble_agreement_strong=3.0,
        max_ensemble_opposition_strong=2.0,
    )
    cache = EnsembleStateCache()
    settings = MagicMock()
    settings.strategy_engine = cfg
    registry = MagicMock()
    EnsembleVoter(registry=registry, settings=settings, state_cache=cache)

    # STRONG clamped to be at least as strict as GOOD on both axes.
    assert cfg.min_ensemble_agreement_strong >= cfg.min_ensemble_agreement
    assert cfg.max_ensemble_opposition_strong <= cfg.max_ensemble_opposition
    # And the corrected values propagated to the cache.
    assert cache._strong_agree >= cache._good_agree
    assert cache._strong_opp <= cache._good_opp
