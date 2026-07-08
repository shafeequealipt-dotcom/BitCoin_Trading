"""Layer 4 Commit 1 — truthful consensus framing in CALL_A.

Per IMPLEMENT_LAYER4_CONSENSUS_TRUTH.md A.4 the fix INFORMS the brain
rather than forces a size. These tests verify:

  - The helper renders the truthful framing with the required tokens
    (the operator can grep the rendered prompt and see the warning)
  - The flag is honored: False → no output (instant rollback path)
  - Helper failure is non-fatal (returns silently, debug log only)
  - The framing line is wired into the legacy CALL_A render path
  - The framing line is wired into the full-block render path
"""
from __future__ import annotations

import io
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@contextmanager
def capture_logs():
    from loguru import logger
    buf = io.StringIO()
    hid = logger.add(buf, level="DEBUG", format="{level} | {message}")
    try:
        yield buf
    finally:
        logger.remove(hid)


def _make_pkg(symbol="BTCUSDT", fired=7, consensus="STRONG",
              direction="BUY", regime="trending_up", scoring_regime=""):
    """Synthetic CoinPackage-like object with only the fields the
    consensus-context helper reads."""
    return SimpleNamespace(
        symbol=symbol,
        strategies=SimpleNamespace(
            fired_count=fired,
            ensemble_consensus=consensus,
            total_score=80.0,
            scoring_regime=scoring_regime,
        ),
        signals=SimpleNamespace(direction=direction, confidence=0.7),
        price_data=SimpleNamespace(regime=regime, current=100.0,
                                    change_24h_pct=0.0),
    )


def _make_strategist():
    """ClaudeStrategist with the bare minimum to call the helper —
    bypasses __init__ side effects (claude client / cache / log spam)."""
    from src.brain.strategist import ClaudeStrategist
    from src.config.settings import Settings
    s = Settings.load()
    strat = ClaudeStrategist.__new__(ClaudeStrategist)
    strat.settings = s
    strat.services = {}
    return strat


def test_settings_flag_default_is_true() -> None:
    """Layer 4 ships ON by default per the operator's decision; OFF is
    the rollback path. Default must be True so the truthful framing
    reaches the brain on first restart."""
    from src.config.settings import Settings
    s = Settings.load()
    assert s.strategy_engine.brain_prompt_l4_consensus_context_enabled is True


def test_helper_appends_required_tokens_when_flag_on() -> None:
    """Rendered text must contain: 'Consensus Context:', '5+',
    'crowded/late entries', plus the live fired/regime/direction values
    so the brain reads the actual numbers, not boilerplate."""
    strat = _make_strategist()
    pkg = _make_pkg(fired=7, consensus="STRONG", direction="BUY",
                    regime="trending_up")
    lines: list[str] = []
    strat._format_consensus_context(lines, pkg)
    text = "\n".join(lines)
    # Helper appended exactly two lines (one fact, one note)
    assert len(lines) == 2
    # Required tokens for the brain to read
    assert "Consensus Context:" in text
    assert "5+" in text
    assert "crowded/late entries" in text
    assert "broad agreement is not always strength" in text
    # Live values surface
    assert "7 strategies fired" in text
    assert "trending_up regime" in text
    assert "STRONG BUY" in text


def test_helper_uses_scoring_regime_over_price_data() -> None:
    """Issue #2 (2026-05-31): this line describes the regime the strategies
    FIRED in (the scoring event), so it must label with the scoring regime,
    not the (possibly drifted) live-cache regime carried on price_data."""
    strat = _make_strategist()
    pkg = _make_pkg(fired=7, consensus="GOOD", direction="BUY",
                    regime="dead", scoring_regime="ranging")
    lines: list[str] = []
    strat._format_consensus_context(lines, pkg)
    text = "\n".join(lines)
    assert "fired in ranging regime" in text
    assert "dead regime" not in text


def test_helper_falls_back_to_price_data_when_unscored() -> None:
    """When the coin was not scored this cycle (scoring_regime empty), the
    line falls back to the package's live-cache regime — pre-#2 behaviour."""
    strat = _make_strategist()
    pkg = _make_pkg(regime="trending_up", scoring_regime="")
    lines: list[str] = []
    strat._format_consensus_context(lines, pkg)
    assert "trending_up regime" in "\n".join(lines)


def test_helper_appends_nothing_when_flag_off() -> None:
    """Operator flips flag False → helper is silent → brain sees no L4
    framing. The instant rollback path must work without code change."""
    strat = _make_strategist()
    strat.settings.strategy_engine.brain_prompt_l4_consensus_context_enabled = False
    pkg = _make_pkg()
    lines: list[str] = []
    strat._format_consensus_context(lines, pkg)
    assert lines == []  # rollback honored


def test_helper_is_non_fatal_on_broken_package() -> None:
    """Per Rule 7 the helper must never crash the prompt build. A
    broken pkg (e.g., missing strategies attr) → debug log + return.
    Pre-existing prompt lines unaffected."""
    strat = _make_strategist()
    strat.settings.strategy_engine.brain_prompt_l4_consensus_context_enabled = True
    broken = SimpleNamespace(symbol="BAD")  # no .strategies / .signals
    lines: list[str] = ["prior line"]
    with capture_logs() as buf:
        strat._format_consensus_context(lines, broken)
    # Existing lines preserved; helper appended nothing
    assert lines == ["prior line"]
    # Failure logged at DEBUG (loud-on-error wouldn't fit a per-cycle hot path)
    assert "L4_CONSENSUS_CONTEXT_FAIL" in buf.getvalue()


def test_helper_handles_missing_price_data() -> None:
    """Some packages lack price_data (regime unknown). Helper should
    still emit the framing with regime='unknown' — the brain still
    benefits from the truthful note."""
    strat = _make_strategist()
    pkg = SimpleNamespace(
        symbol="X",
        strategies=SimpleNamespace(fired_count=3, ensemble_consensus="WEAK",
                                    total_score=40.0),
        signals=SimpleNamespace(direction="SELL", confidence=0.5),
        price_data=None,
    )
    lines: list[str] = []
    strat._format_consensus_context(lines, pkg)
    assert any("unknown regime" in line for line in lines)
    assert any("3 strategies fired" in line for line in lines)


def _make_pkg_with_xray(direction="BUY", xray_dir="short"):
    """Consensus-context pkg carrying an X-RAY structural direction so the
    Issue 1b (2026-06-09) disagreement note can be exercised."""
    pkg = _make_pkg(fired=23, consensus="WEAK", direction=direction,
                    regime="ranging")
    pkg.xray = SimpleNamespace(trade_direction=xray_dir)
    return pkg


def test_issue1b_disagreement_note_when_ensemble_contradicts_xray() -> None:
    """Issue 1b: when the ensemble lean (BUY/long) contradicts the X-RAY
    structural direction (short) on the same coin — the BSB case — a labeled
    DISAGREEMENT line must follow the Consensus Context line so the brain does
    not fade the structure on one-sided consensus alone."""
    strat = _make_strategist()
    pkg = _make_pkg_with_xray(direction="BUY", xray_dir="short")
    lines: list[str] = []
    strat._format_consensus_context(lines, pkg)
    text = "\n".join(lines)
    assert "DISAGREEMENT" in text
    assert "ensemble leans LONG" in text
    assert "X-RAY structure is SHORT" in text


def test_issue1b_no_disagreement_note_when_ensemble_agrees_with_xray() -> None:
    """No false positive: ensemble long + X-RAY long → no DISAGREEMENT line."""
    strat = _make_strategist()
    pkg = _make_pkg_with_xray(direction="BUY", xray_dir="long")
    lines: list[str] = []
    strat._format_consensus_context(lines, pkg)
    assert "DISAGREEMENT" not in "\n".join(lines)


def test_issue1b_disagreement_note_suppressed_when_flag_off() -> None:
    """Instant rollback: emit_direction_disagreement_notes=False removes the
    DISAGREEMENT line (the Consensus Context line itself still renders)."""
    strat = _make_strategist()
    strat.settings.brain.emit_direction_disagreement_notes = False
    pkg = _make_pkg_with_xray(direction="BUY", xray_dir="short")
    lines: list[str] = []
    strat._format_consensus_context(lines, pkg)
    text = "\n".join(lines)
    assert "DISAGREEMENT" not in text
    assert "Consensus Context:" in text  # base line unaffected


def test_legacy_render_path_calls_helper() -> None:
    """The Consensus Context line must appear in the legacy
    _format_packages_for_prompt path BEFORE the brain sees the prompt.
    Verified by grepping the actual prompt construction code for the
    helper call so a future refactor can't accidentally remove it."""
    import inspect
    from src.brain.strategist import ClaudeStrategist
    src = inspect.getsource(ClaudeStrategist._format_packages_for_prompt)
    assert "_format_consensus_context" in src, (
        "Legacy CALL_A render path must call _format_consensus_context "
        "so the brain sees the truthful framing"
    )


def test_full_block_render_path_calls_helper() -> None:
    """Same guard for the full-block render path
    (_format_packages_for_prompt_full)."""
    import inspect
    from src.brain.strategist import ClaudeStrategist
    src = inspect.getsource(ClaudeStrategist._format_packages_for_prompt_full)
    assert "_format_consensus_context" in src, (
        "Full-block CALL_A render path must call _format_consensus_context "
        "so the brain sees the truthful framing"
    )


def test_render_paths_use_defensive_callable_lookup() -> None:
    """Both render paths must call the helper via a defensive
    ``getattr(self, '_format_consensus_context', None)`` so subclass
    test mocks (e.g., ``_FakeStrategist``) that don't override the
    method don't crash the prompt build. The 2026-05-22 cross-check
    found 16 brain_enrichment / phase6_briefing tests breaking
    because their fake strategist lacked the new helper — fixed by
    making the call site defensive (no-op when helper is absent =
    pre-Layer-4 behaviour for those tests)."""
    import inspect
    from src.brain.strategist import ClaudeStrategist
    for fn in (
        ClaudeStrategist._format_packages_for_prompt,
        ClaudeStrategist._format_packages_for_prompt_full,
    ):
        src = inspect.getsource(fn)
        assert "getattr(self, \"_format_consensus_context\"" in src, (
            f"{fn.__name__} must call _format_consensus_context via "
            f"defensive getattr so subclass mocks don't crash the build"
        )
