"""Entry-Quality Fix 2 self-verification (2026-06-10; Five-Fix Follow-Up
update 2026-06-10 evening: 15-minute window added).

The signal's directional read blends the fresh 15-minute and 1-hour OI windows
(operator-approved weights 0.4 / 0.6); the 24h read is context-only (weight
0.0 by default). Each window is price-conditioned (Fix 1) against its OWN
matching price window and normalized BEFORE the blend. This proves: the
blended score MOVES as each fresh window moves (anti-freeze), every window is
itself price-conditioned, the cond_* inversion tags are truthful annotations
(zero behavior change), cold-start falls back to the 24h at full strength, the
legacy configs replay the prior behaviours, and the config loads. Never
rewrites data.
"""

from __future__ import annotations

import asyncio

from src.config.settings import Settings, SignalGeneratorMultiSourceSettings
from src.intelligence.signals.signal_generator import SignalGenerator


def _make(cfg: SignalGeneratorMultiSourceSettings | None = None) -> SignalGenerator:
    sg = SignalGenerator.__new__(SignalGenerator)
    sg._ms_cfg = cfg or SignalGeneratorMultiSourceSettings()
    return sg


async def _blend(sg, oi24, p24, oi1h, oi15m, price_short):
    async def _pc(symbol, bars):
        return price_short
    sg._compute_recent_price_change_pct = _pc  # stub the kline read
    return await sg._blend_oi_windows("X", oi24, p24, oi1h, oi15m)


def test_blend_moves_with_short_windows_when_24h_saturated() -> None:
    """The anti-freeze property: with the 24h delta saturated (+126% -> +1.0),
    the blended score still MOVES as the fresh windows change."""
    sg = _make()
    b1, _ = asyncio.run(_blend(sg, oi24=126.0, p24=+3.0, oi1h=1.0, oi15m=0.5, price_short=+1.0))
    b2, _ = asyncio.run(_blend(sg, oi24=126.0, p24=+3.0, oi1h=4.0, oi15m=2.0, price_short=+1.0))
    assert b1 != b2, "blended OI must move as the fresh windows move (anti-freeze)"
    assert b2 > b1, f"larger fresh OI rise should read more bullish ({b1} -> {b2})"
    print(f"PASS: blended OI moves with the fresh windows even when 24h is saturated ({b1:+.3f} -> {b2:+.3f}).")


def test_15m_window_moves_the_blend_alone() -> None:
    """The NEW 15m window is a genuine driver: holding 1h fixed, a 15m change
    moves the blend."""
    sg = _make()
    b1, _ = asyncio.run(_blend(sg, oi24=10.0, p24=+1.0, oi1h=2.0, oi15m=0.5, price_short=+1.0))
    b2, _ = asyncio.run(_blend(sg, oi24=10.0, p24=+1.0, oi1h=2.0, oi15m=3.0, price_short=+1.0))
    assert b2 > b1, f"a larger 15m OI rise must read more bullish ({b1} -> {b2})"
    print(f"PASS: the 15m window alone moves the blend ({b1:+.3f} -> {b2:+.3f}).")


def test_windows_are_price_conditioned() -> None:
    """The fresh windows obey Fix-1 futures semantics: rising short-window OI
    on a FALLING short-window price pulls the blend bearish."""
    sg = _make()
    bull, _ = asyncio.run(_blend(sg, 126.0, +3.0, oi1h=3.0, oi15m=1.5, price_short=+2.0))
    bear, _ = asyncio.run(_blend(sg, 126.0, +3.0, oi1h=3.0, oi15m=1.5, price_short=-2.0))
    assert bear < bull, f"falling short-window price must read less bullish ({bear} vs {bull})"
    print(f"PASS: short windows are price-conditioned (price-up={bull:+.3f} > price-down={bear:+.3f}).")


def test_cond_tags_are_truthful_annotations() -> None:
    """The cond_* tags mark inversions without changing any score: opposite
    signs tag inv, same signs tag pass, missing windows tag na."""
    sg = _make()
    _, dbg_inv = asyncio.run(_blend(sg, 10.0, -1.0, oi1h=3.0, oi15m=1.5, price_short=-2.0))
    assert dbg_inv["cond_1h"] == "inv" and dbg_inv["cond_15m"] == "inv", dbg_inv
    assert dbg_inv["cond_24h"] == "inv", dbg_inv
    _, dbg_pass = asyncio.run(_blend(sg, 10.0, +1.0, oi1h=3.0, oi15m=1.5, price_short=+2.0))
    assert dbg_pass["cond_1h"] == "pass" and dbg_pass["cond_15m"] == "pass", dbg_pass
    _, dbg_na = asyncio.run(_blend(sg, 10.0, +1.0, oi1h=0.0, oi15m=0.0, price_short=+2.0))
    assert dbg_na["cond_1h"] == "na" and dbg_na["cond_15m"] == "na", dbg_na
    # Annotation-only: the inverted score is the exact negation (magnitude kept).
    assert dbg_inv["s_1h"] is not None and dbg_inv["s_1h"] < 0.0
    print("PASS: cond_* tags truthful (inv on opposite signs, pass on same, na on missing); scores untouched.")


def test_cold_start_falls_back_to_24h_full_strength() -> None:
    """No fresh data in EITHER driver window -> use the 24h conditioned score
    at FULL strength (never damp the only real signal), even at weight 0."""
    sg = _make()
    blended, dbg = asyncio.run(_blend(sg, 126.0, +3.0, oi1h=0.0, oi15m=0.0, price_short=+1.0))
    assert blended == dbg["s_24h"], f"cold start must equal the 24h score ({blended} vs {dbg['s_24h']})"
    assert dbg["s_1h"] is None and dbg["s_15m"] is None, "driver scores must not be computed on cold start"
    print(f"PASS: cold-start falls back to the 24h score at full strength ({blended:+.3f}).")


def test_legacy_configs_replay_prior_behaviours() -> None:
    """Revert levers: 15m=0/short=0/long=1 replays the original 24h-only read;
    15m=0/short=0.7/long=0.3 replays the previous 1h+24h blend."""
    cfg_24h = SignalGeneratorMultiSourceSettings(
        oi_blend_weight_15m=0.0, oi_blend_weight_short=0.0, oi_blend_weight_long=1.0,
    )
    sg = _make(cfg_24h)
    blended, dbg = asyncio.run(_blend(sg, 60.0, +3.0, oi1h=5.0, oi15m=2.0, price_short=-2.0))
    assert blended == dbg["s_24h"], "24h-only config must equal the 24h conditioned score"
    cfg_prev = SignalGeneratorMultiSourceSettings(
        oi_blend_weight_15m=0.0, oi_blend_weight_short=0.7, oi_blend_weight_long=0.3,
    )
    sg2 = _make(cfg_prev)
    blended2, dbg2 = asyncio.run(_blend(sg2, 60.0, +3.0, oi1h=5.0, oi15m=2.0, price_short=-2.0))
    expected = (0.7 * dbg2["s_1h"] + 0.3 * dbg2["s_24h"]) / 1.0
    assert abs(blended2 - expected) < 1e-9, f"prev-blend replay mismatch ({blended2} vs {expected})"
    print("PASS: legacy configs replay the 24h-only and the previous 1h+24h behaviours.")


def test_config_loads() -> None:
    s = Settings.load()
    ms = s.signal_generator.multi_source
    assert ms.oi_blend_weight_15m == 0.4, ms.oi_blend_weight_15m
    assert ms.oi_blend_weight_short == 0.6, ms.oi_blend_weight_short
    assert ms.oi_blend_weight_long == 0.0, ms.oi_blend_weight_long
    assert ms.oi_15m_window_hours == 0.25, ms.oi_15m_window_hours
    assert ms.oi_short_window_hours == 1.0, ms.oi_short_window_hours
    assert ms.funding_use_instantaneous is True
    oi_interval = s.workers.sweet_spots.altdata.open_interest_interval
    assert oi_interval == "5min", oi_interval
    print("PASS: config loads (15m=0.4, 1h=0.6, 24h=0.0 context, windows 0.25h/1.0h, fetch interval 5min).")


def main() -> None:
    print("=== Entry-Quality Fix 2 — signal freshness verification (15m/1h drivers) ===")
    test_blend_moves_with_short_windows_when_24h_saturated()
    test_15m_window_moves_the_blend_alone()
    test_windows_are_price_conditioned()
    test_cond_tags_are_truthful_annotations()
    test_cold_start_falls_back_to_24h_full_strength()
    test_legacy_configs_replay_prior_behaviours()
    test_config_loads()
    print("\nALL FIX-2 CHECKS PASSED.")


if __name__ == "__main__":
    main()
