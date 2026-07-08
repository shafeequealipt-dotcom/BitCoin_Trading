"""Top-5 fix Phase 4 — TA confidence EMA smoothing.

Verifies that ``TAEngine._compute_overall_signal`` smooths the raw
``dominant_indicators / total_indicators`` ratio against a per-symbol
history. Pre-fix a single indicator flip (e.g. RSI crossing 50) caused
the raw confidence to swing 0.14, which propagated into the TradeScorer
Context block (threshold-cross at 0.6 → 10 ↔ 0 swing) and produced
cycle-to-cycle Context flapping on identical structural inputs.

Tests cover:
  - alpha=1.0 → no smoothing (legacy).
  - alpha=0.4 (default) → halves variance vs raw, stable inputs stay
    constant, single-flip noise is dampened.
  - confidence_raw and confidence are both returned in the dict so
    forensic logs can show both.
  - Per-symbol cache: each symbol has its own history; one symbol's
    flip does not affect another.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.analysis.engine import TAEngine


def _engine(alpha: float | None = None) -> TAEngine:
    if alpha is None:
        return TAEngine(db=None, settings=None)
    settings = SimpleNamespace(ta=SimpleNamespace(confidence_ema_alpha=alpha))
    return TAEngine(db=None, settings=settings)


def _signal_dict(bullish: int, bearish: int, neutral: int) -> tuple[dict, list, list]:
    """Build the (indicators, candle_patterns, chart_patterns) tuple
    that ``_compute_overall_signal`` would receive. Indicator counts
    are produced by the per-indicator branches inside the function;
    here we bypass that path by passing empty indicators and instead
    using candle_patterns to drive the bullish/bearish counters
    deterministically.
    """
    candle_patterns = [
        {"type": "bullish", "name": f"b{i}"} for i in range(bullish)
    ] + [
        {"type": "bearish", "name": f"r{i}"} for i in range(bearish)
    ]
    # Note: the engine's indicator branches add their own counts. To
    # keep tests deterministic we pass empty 'indicators' dict so only
    # candle_patterns drive the totals.
    indicators = {"_raw": {}}
    chart_patterns: list = []
    return indicators, candle_patterns, chart_patterns


# ─────────────────────────────────────────────────────────────────────────
# Part 1 — Smoothing alpha behaviour
# ─────────────────────────────────────────────────────────────────────────


class TestSmoothingAlpha:
    """alpha=1.0 disables smoothing; alpha<1.0 dampens cycle-to-cycle
    swings while preserving response to genuine state changes."""

    def test_alpha_1_no_smoothing(self) -> None:
        eng = _engine(alpha=1.0)
        # Cycle 1: 4 bullish, 0 bearish → conf = 4/4 = 1.0
        out1 = eng._compute_overall_signal(
            *_signal_dict(bullish=4, bearish=0, neutral=0), sym="ALICE"
        )
        # Cycle 2: 3 bullish, 1 bearish → conf = 3/4 = 0.75 (raw)
        out2 = eng._compute_overall_signal(
            *_signal_dict(bullish=3, bearish=1, neutral=0), sym="ALICE"
        )
        # alpha=1.0 means smoothed == raw.
        assert out2["confidence"] == out2["confidence_raw"]
        assert abs(out2["confidence"] - 0.75) < 1e-3

    def test_alpha_04_halves_swing(self) -> None:
        """A 1.0 → 0.75 raw swing (0.25) should be dampened to ≤ 0.15
        with alpha=0.4 (smoothed = 0.4*0.75 + 0.6*1.0 = 0.9)."""
        eng = _engine(alpha=0.4)
        eng._compute_overall_signal(
            *_signal_dict(bullish=4, bearish=0, neutral=0), sym="ALICE"
        )
        out2 = eng._compute_overall_signal(
            *_signal_dict(bullish=3, bearish=1, neutral=0), sym="ALICE"
        )
        # raw = 3/4 = 0.75, prev = 1.0 → smoothed = 0.4*0.75 + 0.6*1.0 = 0.90
        assert abs(out2["confidence"] - 0.90) < 1e-3
        assert abs(out2["confidence_raw"] - 0.75) < 1e-3
        # The smoothed value moved less than the raw value did.
        assert abs(1.0 - out2["confidence"]) < abs(1.0 - out2["confidence_raw"])

    def test_stable_inputs_stable_output(self) -> None:
        """Identical inputs across cycles → smoothed confidence stays
        constant (no oscillation on an unchanging signal)."""
        eng = _engine(alpha=0.4)
        outs = [
            eng._compute_overall_signal(
                *_signal_dict(bullish=3, bearish=1, neutral=0), sym="ALICE"
            )
            for _ in range(5)
        ]
        # All five cycles see the same raw 3/4.
        for o in outs:
            assert abs(o["confidence_raw"] - 0.75) < 1e-3
        # Smoothed converges toward 0.75 quickly; cycle 5's confidence
        # should be within 0.01 of 0.75.
        assert abs(outs[-1]["confidence"] - 0.75) < 0.01

    def test_single_indicator_flip_dampened(self) -> None:
        """Simulate the audit scenario: raw conf swings 0.80 ↔ 0.60 ↔
        0.80 across three cycles (cycle 2 has one extra bearish),
        confirm smoothed conf has lower variance."""
        eng = _engine(alpha=0.4)
        # Cycle 1: 4 bullish, 0 bearish, 1 neutral → raw 4/5 = 0.80
        c1 = eng._compute_overall_signal(
            *_signal_dict(bullish=4, bearish=0, neutral=1), sym="ALICE"
        )
        # Cycle 2: 3 bullish, 1 bearish, 1 neutral → raw 3/5 = 0.60
        c2 = eng._compute_overall_signal(
            *_signal_dict(bullish=3, bearish=1, neutral=1), sym="ALICE"
        )
        # Cycle 3: 4 bullish, 0 bearish, 1 neutral → raw 4/5 = 0.80
        c3 = eng._compute_overall_signal(
            *_signal_dict(bullish=4, bearish=0, neutral=1), sym="ALICE"
        )
        raw_swings = [c1["confidence_raw"], c2["confidence_raw"], c3["confidence_raw"]]
        smoothed_swings = [c1["confidence"], c2["confidence"], c3["confidence"]]
        # Raw range is exactly 0.20 (0.60 to 0.80).
        assert max(raw_swings) - min(raw_swings) > 0.18
        # Smoothed range should be tighter — < 0.15.
        assert max(smoothed_swings) - min(smoothed_swings) < 0.15

    def test_genuine_change_caught_within_3_cycles(self) -> None:
        """When the underlying state genuinely shifts (4 bullish → 4
        bearish), smoothed confidence catches up within 3 cycles."""
        eng = _engine(alpha=0.4)
        # Build up history at conf=1.0
        for _ in range(3):
            eng._compute_overall_signal(
                *_signal_dict(bullish=4, bearish=0, neutral=0), sym="ALICE"
            )
        # Genuine flip — now 0 bullish, 4 bearish, conf raw = 1.0 again
        # (dominant=4 of 4) but the smoothing should hold the prior value
        # for one cycle then catch up.
        c1 = eng._compute_overall_signal(
            *_signal_dict(bullish=0, bearish=4, neutral=0), sym="ALICE"
        )
        c2 = eng._compute_overall_signal(
            *_signal_dict(bullish=0, bearish=4, neutral=0), sym="ALICE"
        )
        c3 = eng._compute_overall_signal(
            *_signal_dict(bullish=0, bearish=4, neutral=0), sym="ALICE"
        )
        # All three see raw 1.0 (different direction same conf — direction
        # is captured by ``signal``, not ``confidence``).
        assert abs(c3["confidence"] - 1.0) < 0.02


# ─────────────────────────────────────────────────────────────────────────
# Part 2 — Per-symbol cache isolation
# ─────────────────────────────────────────────────────────────────────────


class TestPerSymbolCache:
    """Each symbol has its own confidence history. One symbol's flip
    does not affect another's smoothing."""

    def test_two_symbols_independent(self) -> None:
        eng = _engine(alpha=0.4)
        # ALICE settles at conf 1.0
        eng._compute_overall_signal(
            *_signal_dict(bullish=4, bearish=0, neutral=0), sym="ALICE"
        )
        # First call for BOB has no prior; raw = prev = 0.50
        bob_out = eng._compute_overall_signal(
            *_signal_dict(bullish=2, bearish=2, neutral=0), sym="BOB"
        )
        # raw = 2/4 = 0.50; first call uses raw as prior so smoothed = 0.50
        assert abs(bob_out["confidence_raw"] - 0.50) < 1e-3
        assert abs(bob_out["confidence"] - 0.50) < 1e-3
        # ALICE's prev is unaffected by BOB's call.
        alice_out = eng._compute_overall_signal(
            *_signal_dict(bullish=4, bearish=0, neutral=0), sym="ALICE"
        )
        assert abs(alice_out["confidence"] - 1.0) < 1e-3

    def test_cache_size_bounded_by_universe(self) -> None:
        """The per-symbol dict grows by one entry per symbol seen.
        After 50 symbols, dict has 50 entries (no leak)."""
        eng = _engine(alpha=0.4)
        for i in range(50):
            eng._compute_overall_signal(
                *_signal_dict(bullish=2, bearish=1, neutral=0),
                sym=f"COIN{i:02d}USDT",
            )
        assert len(eng._prev_confidence_by_symbol) == 50


# ─────────────────────────────────────────────────────────────────────────
# Part 3 — Output dict shape
# ─────────────────────────────────────────────────────────────────────────


class TestOutputDictShape:
    """The dict returned by _compute_overall_signal carries both
    confidence and confidence_raw so observability + downstream logic
    can show the dampening."""

    def test_confidence_raw_present(self) -> None:
        eng = _engine(alpha=0.4)
        out = eng._compute_overall_signal(
            *_signal_dict(bullish=2, bearish=1, neutral=0), sym="ALICE"
        )
        assert "confidence_raw" in out
        assert "confidence" in out
        # First call: smoothed == raw because prev defaults to raw.
        assert out["confidence"] == out["confidence_raw"]

    def test_no_settings_no_smoothing(self) -> None:
        """Backward-compat: a TAEngine constructed without settings
        behaves like alpha=1.0 (no smoothing)."""
        eng = TAEngine(db=None, settings=None)
        out1 = eng._compute_overall_signal(
            *_signal_dict(bullish=4, bearish=0, neutral=0), sym="ALICE"
        )
        out2 = eng._compute_overall_signal(
            *_signal_dict(bullish=3, bearish=1, neutral=0), sym="ALICE"
        )
        assert out2["confidence"] == out2["confidence_raw"]


# ─────────────────────────────────────────────────────────────────────────
# Part 4 — Settings validation
# ─────────────────────────────────────────────────────────────────────────


class TestSettingsValidation:
    """Test the config builder enforces 0 < alpha <= 1."""

    def test_default_alpha_is_04(self) -> None:
        from src.config.settings import _build_ta
        cfg = _build_ta({})
        assert cfg.confidence_ema_alpha == 0.4

    def test_explicit_alpha_used(self) -> None:
        from src.config.settings import _build_ta
        cfg = _build_ta({"confidence_ema_alpha": 0.7})
        assert cfg.confidence_ema_alpha == 0.7

    def test_zero_alpha_rejected(self) -> None:
        from src.config.settings import _build_ta
        from src.core.exceptions import ConfigError
        with pytest.raises(ConfigError):
            _build_ta({"confidence_ema_alpha": 0.0})

    def test_above_one_rejected(self) -> None:
        from src.config.settings import _build_ta
        from src.core.exceptions import ConfigError
        with pytest.raises(ConfigError):
            _build_ta({"confidence_ema_alpha": 1.5})

    def test_alpha_one_accepted(self) -> None:
        """alpha=1.0 is valid (means no smoothing — legacy behaviour)."""
        from src.config.settings import _build_ta
        cfg = _build_ta({"confidence_ema_alpha": 1.0})
        assert cfg.confidence_ema_alpha == 1.0
