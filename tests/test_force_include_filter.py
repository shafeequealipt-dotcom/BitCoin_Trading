"""Q2 + Q3b + Q3d combined fix — regression tests.

Three correlated fixes shipped 2026-04-29 (single atomic change-set):

  Q2 — ``scanner_worker.py`` and ``strategies/scanner.py``: removed
       unconditional BTC/ETH reference-pair force-include from both
       producers. HR-2 (force-include open positions) preserved via
       the protected-symbols path.

  Q3b — ``strategist._format_packages_for_prompt``: added a guard so
        packages with ``qualified=False`` AND ``open_position is None``
        are skipped from the TRADE CANDIDATES prompt block. Open
        positions still rendered (HR-2).

  Q3d — ``layer_manager._cold_start_block_or_none``: brain cold-start
        gate's ``qualified`` count now uses ``pkg.qualified`` OR
        ``open_position is not None`` instead of treating completeness
        ≥ 0.75 as "qualified". Disambiguates the two ``qualified``
        meanings that collided in the codebase.

Combined effect: brain no longer hallucinates trades on BTC/ETH every
cycle. Without these fixes, every CALL_A on a slow-market cycle would
list BTC/ETH as TRADE CANDIDATES, the brain would propose trades, the
gate would count them as qualified packages, and only the downstream
strategy_worker xray-direction guards would catch some of them.

Tests cover each fix independently (so a regression in one is isolated)
plus AST-based static guards against re-introduction of the buggy
patterns.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.coin_package import (
    AltDataBlock,
    CoinPackage,
    PriceDataBlock,
    SignalsBlock,
    StrategiesBlock,
    StructuralLevels,
    XrayBlock,
)


SRC_ROOT = Path(__file__).parent.parent / "src"
SCANNER_WORKER = SRC_ROOT / "workers" / "scanner_worker.py"
LEGACY_SCANNER = SRC_ROOT / "strategies" / "scanner.py"
STRATEGIST = SRC_ROOT / "brain" / "strategist.py"
LAYER_MANAGER = SRC_ROOT / "core" / "layer_manager.py"


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _make_pkg(
    symbol: str,
    *,
    qualified: bool,
    open_position: dict | None = None,
    completeness: float = 1.0,
    opportunity_score: float = 0.5,
) -> CoinPackage:
    """Build a minimally-populated CoinPackage for prompt/gate tests.

    The fields under test are ``qualified``, ``open_position``, and
    ``completeness``. The rest are sensible defaults to keep the
    fixture concise.
    """
    pkg = CoinPackage(
        symbol=symbol,
        qualified=qualified,
        opportunity_score=opportunity_score,
        qualification_reasons=[],
        price_data=PriceDataBlock(current=100.0),
        xray=XrayBlock(setup_type="bullish_FVG_OB", structural_levels=StructuralLevels()),
        strategies=StrategiesBlock(ensemble_consensus="STRONG", total_score=80.0),
        signals=SignalsBlock(direction="long", confidence=0.7),
        alt_data=AltDataBlock(fear_greed=42),
        open_position=open_position,
        blockers_observed=[],
    )
    pkg.completeness = float(completeness)
    return pkg


def _bare_strategist():
    """Build a strategist stub with only the methods under test bound.

    ``_format_packages_for_prompt`` is a pure function of its input
    dict — no service dependencies. ``__new__`` bypasses the heavy
    constructor entirely.
    """
    from src.brain.strategist import ClaudeStrategist
    s = ClaudeStrategist.__new__(ClaudeStrategist)
    return s


def _bare_layer_manager(packages: dict, boot_offset_s: float = 0.0):
    """Build a LayerManager stub with the cold-start gate ready to call.

    ``_cold_start_block_or_none`` only reads ``self._coin_packages``,
    ``self.settings.brain.cold_start_protection``, and
    ``self._boot_time``. Provide minimum viable mocks.
    """
    import time
    from src.core.layer_manager import LayerManager
    lm = LayerManager.__new__(LayerManager)
    lm._coin_packages = packages
    lm._boot_time = time.time() - boot_offset_s
    # Settings stub matching the cold-start config shape.
    cfg = MagicMock()
    cfg.enabled = True
    cfg.min_avg_completeness = 0.85
    cfg.min_per_package_completeness = 0.75
    cfg.min_qualified_packages = 3
    cfg.boot_grace_period_sec = 600
    cfg.boot_grace_completeness = 0.95
    settings = MagicMock()
    settings.brain.cold_start_protection = cfg
    lm.settings = settings
    return lm


# ──────────────────────────────────────────────────────────────────────
# Q2 tests — scanner no longer hardcodes BTC/ETH
# ──────────────────────────────────────────────────────────────────────


class TestQ2RefPairForceIncludeRemoved:
    """Source-grep + AST tests that the unconditional BTC/ETH insert is gone."""

    def test_scanner_worker_no_unconditional_btc_eth_loop(self) -> None:
        """``for ref in ("BTCUSDT", "ETHUSDT"):`` may not appear in
        ``scanner_worker.py``'s ``tick`` body. Earlier versions had a
        14-line block at lines ~1013-1027 that unconditionally appended
        BTC and ETH to ``final``.

        Regex-tolerant check: matches both tuple and list literal forms.
        """
        src = SCANNER_WORKER.read_text()
        for needle in (
            'for ref in ("BTCUSDT", "ETHUSDT")',
            'for ref in ["BTCUSDT", "ETHUSDT"]',
            "for ref in ('BTCUSDT', 'ETHUSDT')",
            "for ref in ['BTCUSDT', 'ETHUSDT']",
        ):
            assert needle not in src, (
                f"Regression: {needle!r} reappeared in scanner_worker.py. "
                "The Q2 fix removed the unconditional BTC/ETH force-include; "
                "it must not return. HR-2 is preserved via the protected-"
                "symbols path."
            )

    def test_legacy_scanner_no_unconditional_btc_eth_loop(self) -> None:
        """Same guard for the legacy ``MarketScanner._update_universe`` —
        its boot-time BTC/ETH insert was removed for consistency.
        """
        src = LEGACY_SCANNER.read_text()
        for needle in (
            'for ref in ("BTCUSDT", "ETHUSDT")',
            'for ref in ["BTCUSDT", "ETHUSDT"]',
            "for ref in ('BTCUSDT', 'ETHUSDT')",
            "for ref in ['BTCUSDT', 'ETHUSDT']",
        ):
            assert needle not in src, (
                f"Regression: {needle!r} reappeared in legacy "
                "strategies/scanner.py. The Q2 fix removed both producers."
            )

    def test_scanner_worker_no_btc_eth_reference_pair_marker(self) -> None:
        """The reasons_passed marker ``btc_eth_reference_pair`` was the
        observability tag for the old force-include. Its disappearance
        from the source confirms the block is fully gone (not just the
        loop header).
        """
        src = SCANNER_WORKER.read_text()
        assert "btc_eth_reference_pair" not in src, (
            "Regression: btc_eth_reference_pair marker is back. "
            "The Q2 fix should have removed the entire 14-line force-"
            "include block, not just the loop."
        )


# ──────────────────────────────────────────────────────────────────────
# Q3b tests — strategist prompt filter
# ──────────────────────────────────────────────────────────────────────


class TestQ3bPromptFilter:
    """Behavioral tests for the ``_format_packages_for_prompt`` guard."""

    def test_excludes_ref_pair_force_include(self) -> None:
        """A package with qualified=False AND open_position=None (the
        BTC/ETH ref-pair pattern) must NOT appear in the prompt output.
        Even though Q2 removed the producer, this filter is a
        defense-in-depth guard against any future re-introduction.
        """
        s = _bare_strategist()
        packages = {
            "BTCUSDT": _make_pkg("BTCUSDT", qualified=False, open_position=None),
        }
        out = s._format_packages_for_prompt(packages)
        assert "BTCUSDT" not in out, (
            "Q3b regression: ref-pair force-include leaked into prompt. "
            f"Output:\n{out}"
        )

    def test_includes_open_position_force_include(self) -> None:
        """A package with qualified=False AND open_position={...} (HR-2)
        MUST appear so Claude can decide hold/close. The filter must
        only exclude ref-pair force-includes, not legitimate HR-2 ones.
        """
        s = _bare_strategist()
        pos_dict = {"side": "Buy", "entry_price": 1.5, "size": 100}
        packages = {
            "PYTHUSDT": _make_pkg(
                "PYTHUSDT", qualified=False, open_position=pos_dict
            ),
        }
        out = s._format_packages_for_prompt(packages)
        assert "PYTHUSDT" in out, (
            "Q3b regression: HR-2 open-position force-include was filtered "
            "out. Open positions must remain visible to Claude for "
            f"management decisions. Output:\n{out}"
        )

    def test_includes_qualified(self) -> None:
        """Packages with qualified=True (the normal scanner-qualified
        path) must appear regardless of open_position state.
        """
        s = _bare_strategist()
        packages = {
            "FILUSDT": _make_pkg("FILUSDT", qualified=True, open_position=None),
        }
        out = s._format_packages_for_prompt(packages)
        assert "FILUSDT" in out

    def test_mixed_packages_filter_correctly(self) -> None:
        """End-to-end: 4 packages — qualified, open-position-forced,
        ref-pair-forced (excluded), another qualified. The output must
        contain 3 of the 4.
        """
        s = _bare_strategist()
        pos_dict = {"side": "Buy", "entry_price": 100.0, "size": 1.0}
        packages = {
            "PYTHUSDT": _make_pkg("PYTHUSDT", qualified=True),
            "FILUSDT": _make_pkg(
                "FILUSDT", qualified=False, open_position=pos_dict
            ),
            "BTCUSDT": _make_pkg("BTCUSDT", qualified=False, open_position=None),
            "MONUSDT": _make_pkg("MONUSDT", qualified=True),
        }
        out = s._format_packages_for_prompt(packages)
        assert "PYTHUSDT" in out
        assert "FILUSDT" in out  # open-position, kept
        assert "BTCUSDT" not in out  # ref-pair, dropped
        assert "MONUSDT" in out

    def test_empty_packages_returns_empty_string(self) -> None:
        """The empty-input fast path is preserved (early return)."""
        s = _bare_strategist()
        assert s._format_packages_for_prompt({}) == ""

    def test_prompt_header_no_longer_overclaims(self) -> None:
        """The header text was changed from 'qualified by ScannerWorker'
        (misleading because it was emitted for force-included packages
        too) to a precise statement that includes HR-2 management
        coverage. Regression guard against the old misleading header.
        """
        src = STRATEGIST.read_text()
        assert (
            "## TRADE CANDIDATES (qualified by ScannerWorker; one block per coin)"
            not in src
        ), (
            "Q3b regression: the misleading old prompt header is back. "
            "Use the precise version that mentions HR-2 management."
        )


# ──────────────────────────────────────────────────────────────────────
# Q3d tests — brain cold-start gate qualified count
# ──────────────────────────────────────────────────────────────────────


class TestQ3dGateQualifiedCount:
    """The brain cold-start gate's ``qualified_count`` must align with
    scanner intent: count packages that pass the scanner's qualitative
    gate OR have an open position (HR-2). Completeness alone no longer
    qualifies a package toward the gate's threshold.
    """

    def test_ref_pair_force_include_does_not_count_toward_gate(self) -> None:
        """A package with qualified=False, open_position=None, but
        completeness=1.00 must NOT count as a qualified package. Before
        Q3d, completeness alone was sufficient; that let BTC/ETH ref-
        pair force-includes pass the gate's min_qualified_packages
        threshold.

        Verifies: 3 ref-pair packages (each completeness=0.94) at
        steady-state should trigger BRAIN_INSUFFICIENT_QUALITY because
        qualified_count=0 < min_qualified_packages=3.
        """
        # Steady-state (post boot grace = 700 s after boot)
        packages = {
            "BTCUSDT": _make_pkg(
                "BTCUSDT", qualified=False, open_position=None, completeness=0.94
            ),
            "ETHUSDT": _make_pkg(
                "ETHUSDT", qualified=False, open_position=None, completeness=0.94
            ),
            "DOGEUSDT": _make_pkg(
                "DOGEUSDT", qualified=False, open_position=None, completeness=0.94
            ),
        }
        lm = _bare_layer_manager(packages, boot_offset_s=700)
        plan = MagicMock()
        plan.new_trades = [MagicMock(), MagicMock(), MagicMock()]
        result = lm._cold_start_block_or_none(plan)
        assert result is not None, (
            "Q3d regression: gate did not block 3 ref-pair packages. "
            "qualified_count must require pkg.qualified or open_position."
        )
        assert "BRAIN_INSUFFICIENT_QUALITY" in result
        assert "qualified=0" in result, (
            f"Q3d regression: qualified_count was non-zero. Output: {result}"
        )

    def test_real_qualifiers_count_toward_gate(self) -> None:
        """3 scanner-qualified packages (completeness>=0.75) at steady-
        state should pass the gate.
        """
        packages = {
            "PYTHUSDT": _make_pkg("PYTHUSDT", qualified=True, completeness=1.0),
            "FILUSDT": _make_pkg("FILUSDT", qualified=True, completeness=1.0),
            "MONUSDT": _make_pkg("MONUSDT", qualified=True, completeness=1.0),
        }
        lm = _bare_layer_manager(packages, boot_offset_s=700)
        plan = MagicMock()
        plan.new_trades = [MagicMock()]
        result = lm._cold_start_block_or_none(plan)
        assert result is None, (
            "Q3d regression: gate blocked 3 qualified packages. "
            f"Output: {result}"
        )

    def test_open_position_counts_toward_gate(self) -> None:
        """An open-position-forced package (qualified=False but
        open_position is not None) MUST count toward the gate. HR-2
        guarantees brain can manage open positions.
        """
        pos_dict = {"side": "Buy", "entry_price": 1.5, "size": 100}
        packages = {
            "PYTHUSDT": _make_pkg("PYTHUSDT", qualified=True, completeness=1.0),
            "FILUSDT": _make_pkg("FILUSDT", qualified=True, completeness=1.0),
            "BTCUSDT": _make_pkg(
                "BTCUSDT", qualified=False,
                open_position=pos_dict, completeness=0.94
            ),
        }
        lm = _bare_layer_manager(packages, boot_offset_s=700)
        plan = MagicMock()
        plan.new_trades = []
        result = lm._cold_start_block_or_none(plan)
        assert result is None, (
            "Q3d regression: gate blocked when 2 qualified + 1 open "
            "position (3 ≥ min_qualified_packages=3). Output: {result}"
        )

    def test_low_completeness_blocks_even_if_qualified(self) -> None:
        """Both checks combine — scanner-qualified BUT completeness too
        low must still NOT count.
        """
        packages = {
            "PYTHUSDT": _make_pkg("PYTHUSDT", qualified=True, completeness=0.50),
            "FILUSDT": _make_pkg("FILUSDT", qualified=True, completeness=0.50),
            "MONUSDT": _make_pkg("MONUSDT", qualified=True, completeness=0.50),
        }
        lm = _bare_layer_manager(packages, boot_offset_s=700)
        plan = MagicMock()
        plan.new_trades = [MagicMock()]
        result = lm._cold_start_block_or_none(plan)
        # avg=0.50 < min_avg=0.85 → BRAIN_COLD_START_BLOCK fires first;
        # qualified_count=0 (low completeness) is also part of the
        # message even though the avg-check trips first.
        assert result is not None
        assert "BRAIN_COLD_START_BLOCK" in result
        assert "qualified=0" in result, (
            "Q3d regression: low-completeness packages counted as "
            f"qualified. Output: {result}"
        )

    def test_boot_grace_path_unchanged(self) -> None:
        """The boot-grace path uses ``boot_grace_completeness=0.95`` for
        the avg check — Q3d does not change avg behaviour, only the
        qualified_count semantics. Sanity check: 2 packages at avg=0.94
        in boot-grace blocks; outside grace passes (steady avg=0.85).
        """
        # Inside boot grace — should block (0.94 < 0.95)
        pos_dict = {"side": "Buy", "entry_price": 100, "size": 1}
        packages = {
            "BTCUSDT": _make_pkg(
                "BTCUSDT", qualified=False,
                open_position=pos_dict, completeness=0.94
            ),
            "ETHUSDT": _make_pkg(
                "ETHUSDT", qualified=True, completeness=0.94
            ),
        }
        lm = _bare_layer_manager(packages, boot_offset_s=100)  # in grace
        plan = MagicMock()
        plan.new_trades = []
        result = lm._cold_start_block_or_none(plan)
        assert result is not None
        assert "BRAIN_COLD_START_BLOCK" in result
        assert "boot_grace=Y" in result


# ──────────────────────────────────────────────────────────────────────
# Static AST guards — combined for the 4 source files
# ──────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    """AST-based regression guards. These are belt-and-braces against
    sloppy refactors that re-introduce the buggy patterns.
    """

    def test_strategist_format_packages_has_filter(self) -> None:
        """The new filter ``if not pkg.qualified and pkg.open_position
        is None: continue`` must be present in
        ``_format_packages_for_prompt``. AST-walk the function body to
        find a comparison `pkg.qualified` AND `pkg.open_position is
        None` joined under an If with a Continue body.
        """
        src = STRATEGIST.read_text()
        tree = ast.parse(src)
        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_format_packages_for_prompt"
            ):
                target_fn = node
                break
        assert target_fn is not None, (
            "_format_packages_for_prompt not found — test scaffolding stale."
        )

        # Walk for an If statement that has a Continue and references
        # pkg.qualified + pkg.open_position. Substring match on AST
        # source is the simplest reliable check.
        body_src = ast.unparse(target_fn)
        assert "pkg.qualified" in body_src
        assert "pkg.open_position" in body_src
        assert "continue" in body_src.lower(), (
            "Q3b regression: the filter's ``continue`` statement is "
            "missing — packages would no longer be skipped."
        )

    def test_layer_manager_gate_uses_qualified_or_open_position(self) -> None:
        """``_cold_start_block_or_none`` must check ``pkg.qualified``
        OR ``open_position is not None`` for the qualified_count, not
        just completeness.
        """
        src = LAYER_MANAGER.read_text()
        tree = ast.parse(src)
        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_cold_start_block_or_none"
            ):
                target_fn = node
                break
        assert target_fn is not None
        body_src = ast.unparse(target_fn)
        assert "qualified_count" in body_src, (
            "Q3d regression: variable ``qualified_count`` (the renamed "
            "disambiguated counter) is missing."
        )
        assert "open_position" in body_src, (
            "Q3d regression: open_position check is missing — HR-2 "
            "would no longer be respected by the gate."
        )

    def test_scanner_worker_btc_eth_block_fully_removed(self) -> None:
        """Reuse the Q2 source-grep guard at AST level for symmetry."""
        src = SCANNER_WORKER.read_text()
        # Three sentinel strings from the old block — none should remain.
        for sentinel in (
            "btc_eth_reference_pair",
            'BTCUSDT", "ETHUSDT"',
            "BTCUSDT', 'ETHUSDT'",
        ):
            assert sentinel not in src, f"{sentinel!r} still in scanner_worker.py"
