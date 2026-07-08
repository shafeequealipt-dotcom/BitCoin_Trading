"""Entry-Quality Fix 1 self-verification (2026-06-10).

The OI direction component is now price-conditioned: rising OI on a falling
price reads BEARISH (shorts piling in), not bullish. This replays the proven
RUNE/SKR wrong-side cases, confirms a genuine long-accumulation still reads
bullish, confirms the distribution stays two-sided (Rule 4 — neutrality, never a
standing short bias), and confirms the config keys load. Never rewrites data.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.config.settings import Settings, SignalGeneratorMultiSourceSettings
from src.core.types import SignalType
from src.intelligence.signals.signal_generator import SignalGenerator


def _make(cfg: SignalGeneratorMultiSourceSettings | None = None) -> SignalGenerator:
    sg = SignalGenerator.__new__(SignalGenerator)
    sg._ms_cfg = cfg or SignalGeneratorMultiSourceSettings()
    return sg


def test_condition_helper_all_four_futures_cases() -> None:
    sg = _make()
    # oi_score sign tracks oi_change sign before conditioning.
    # OI up + price up -> longs accumulating -> unchanged bullish (+)
    assert sg._condition_oi_score(+0.8, oi_change=+5.0, price_change=+3.0) == +0.8
    # OI up + price down -> shorts piling in -> inverted bearish (-)
    assert sg._condition_oi_score(+0.8, oi_change=+5.0, price_change=-3.0) == -0.8
    # OI down + price up -> short covering -> inverted weakly bullish (+)
    assert sg._condition_oi_score(-0.4, oi_change=-5.0, price_change=+3.0) == +0.4
    # OI down + price down -> long liquidation -> unchanged weakly bearish (-)
    assert sg._condition_oi_score(-0.4, oi_change=-5.0, price_change=-3.0) == -0.4
    # No OI signal or no price info -> unchanged.
    assert sg._condition_oi_score(+0.8, oi_change=0.0, price_change=-3.0) == +0.8
    assert sg._condition_oi_score(+0.8, oi_change=+5.0, price_change=0.0) == +0.8
    print("PASS: _condition_oi_score maps all four futures cases correctly.")


def test_rune_and_skr_replays_read_bearish_or_neutral() -> None:
    sg = _make()
    # RUNE: OI rising (+2.30%) while price FELL across the session. Pre-fix the
    # signal said BUY (oi_score +0.46). Post-fix it must NOT be a buy.
    sig, reason = sg._evaluate_signal(
        fear_greed=50, funding_rate=0.0, oi_change=2.30, price_change=-2.0,
        symbol="RUNEUSDT",
    )
    assert sig in (SignalType.SELL, SignalType.STRONG_SELL, SignalType.NEUTRAL), (
        f"RUNE replay should not be a BUY, got {sig.value} — {reason}"
    )
    # SKR: OI +11.11% on a coin whose price fell -> strong bearish, not strong buy.
    sig2, _ = sg._evaluate_signal(
        fear_greed=50, funding_rate=0.0, oi_change=11.11, price_change=-3.5,
        symbol="SKRUSDT",
    )
    assert sig2 in (SignalType.SELL, SignalType.STRONG_SELL), (
        f"SKR replay should read bearish, got {sig2.value}"
    )
    print("PASS: RUNE reads non-buy and SKR reads bearish (the wrong-side root is fixed).")


def test_genuine_long_accumulation_still_bullish() -> None:
    sg = _make()
    # OI rising AND price rising -> longs accumulating -> still BUY/STRONG_BUY.
    sig, _ = sg._evaluate_signal(
        fear_greed=50, funding_rate=0.0, oi_change=10.0, price_change=+4.0,
        symbol="BTCUSDT",
    )
    assert sig in (SignalType.BUY, SignalType.STRONG_BUY), (
        f"genuine long-accumulation should stay bullish, got {sig.value}"
    )
    print("PASS: genuine long-accumulation (OI up + price up) still reads bullish.")


def test_distribution_is_two_sided() -> None:
    sg = _make()
    seen = set()
    for oi in (-12.0, -6.0, 6.0, 12.0):
        for pc in (-5.0, 5.0):
            sig, _ = sg._evaluate_signal(
                fear_greed=50, funding_rate=0.0, oi_change=oi, price_change=pc,
                symbol="X",
            )
            seen.add(sig)
    assert SignalType.STRONG_BUY in seen or SignalType.BUY in seen, "no bullish reads"
    assert SignalType.STRONG_SELL in seen or SignalType.SELL in seen, "no bearish reads"
    print(f"PASS: distribution two-sided across OIxprice grid ({sorted(s.value for s in seen)}).")


def test_config_keys_load() -> None:
    settings = Settings.load()
    ms = settings.signal_generator.multi_source
    assert hasattr(ms, "oi_price_window_hours"), "oi_price_window_hours missing"
    assert hasattr(ms, "oi_price_dead_band_pct"), "oi_price_dead_band_pct missing"
    assert ms.oi_price_window_hours == 24.0, ms.oi_price_window_hours
    assert ms.oi_price_dead_band_pct == 0.0, ms.oi_price_dead_band_pct
    # And the real generator builds + logs the BOOT sentinel with the new keys.
    SignalGenerator(MagicMock(), MagicMock(), settings=settings)
    print("PASS: config keys load (window=24.0, dead_band=0.0) and boot sentinel fires.")


def main() -> None:
    print("=== Entry-Quality Fix 1 — price-conditioned OI verification ===")
    test_condition_helper_all_four_futures_cases()
    test_rune_and_skr_replays_read_bearish_or_neutral()
    test_genuine_long_accumulation_still_bullish()
    test_distribution_is_two_sided()
    test_config_keys_load()
    print("\nALL FIX-1 CHECKS PASSED.")


if __name__ == "__main__":
    main()
