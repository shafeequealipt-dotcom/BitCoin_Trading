"""Phase 3 — Post-Execution Closure Fix.

Verifies that ``ClaudeStrategist.create_trade_plan`` (CALL_A) skips the
Claude subprocess invocation when the scanner has produced zero
packages. Pre-fix, a cold-start CALL_A with ``packages=0`` still ran
and Claude produced ghost trades from cached / held-position context
(no XRAY confidence, no consensus vote, no regime alignment behind
them). Post-fix, the cycle is skipped and ``None`` is returned (same
null contract as a parse failure) so callers do not need a new branch.

Edge cases preserved:
  * ``settings.brain.use_packages = False``: legacy non-package mode
    falls through to the existing path (no skip).
  * ``layer_manager`` not registered or missing
    ``get_coin_packages``: falls through (no skip).
  * Pre-check raises: fall through, log at DEBUG.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brain.strategist import ClaudeStrategist


def _make_strategist(
    *,
    packages: dict | None,
    use_packages: bool = True,
    has_layer_manager: bool = True,
) -> tuple[ClaudeStrategist, MagicMock]:
    """Build a minimal ClaudeStrategist whose pre-check sees ``packages``.

    Returns (strategist, claude_mock) so the test can assert whether
    ``claude.send_message`` was awaited (the canonical signal that
    CALL_A actually invoked the subprocess versus skipped).
    """
    services: dict = {}
    if has_layer_manager:
        lm = MagicMock()
        lm.get_coin_packages = MagicMock(
            return_value=packages if packages is not None else {},
        )
        services["layer_manager"] = lm

    settings = SimpleNamespace(
        brain=SimpleNamespace(
            use_packages=use_packages,
            surface_briefing_fields=False,
        ),
        stage2=SimpleNamespace(enable_zero_two_contract=False),
    )

    claude_mock = MagicMock()
    claude_mock.send_message = AsyncMock(return_value='{"new_trades": []}')

    strat = ClaudeStrategist(
        claude_client=claude_mock, services=services, settings=settings,
    )
    # Stub the prompt builder so any fall-through path completes quickly
    # and predictably — the relevant assertion is whether send_message
    # was reached.
    strat._build_trade_prompt = AsyncMock(return_value="(stub prompt)")
    # Stub the response parser so the fall-through path returns a
    # well-formed but empty plan rather than raising on parse.
    strat._parse_trade_plan = MagicMock(
        return_value=SimpleNamespace(
            new_trades=[],
            position_actions={},
            risk_level="low",
            market_view="",
        ),
    )
    return strat, claude_mock


# ─── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_calla_skipped_when_packages_empty() -> None:
    """packages={} → return None, no claude.send_message call.

    This is the canonical scanner cold-start scenario the fix targets.
    """
    strat, claude_mock = _make_strategist(packages={})
    plan = await strat.create_trade_plan()
    assert plan is None
    claude_mock.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_calla_proceeds_when_packages_present() -> None:
    """packages with at least one entry → CALL_A proceeds; the Claude
    subprocess is invoked.
    """
    strat, claude_mock = _make_strategist(
        packages={"BTCUSDT": MagicMock()},
    )
    await strat.create_trade_plan()
    claude_mock.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_calla_falls_through_when_layer_manager_missing() -> None:
    """No layer_manager service → pre-check skips itself and CALL_A
    proceeds along the existing path. (Defensive: the skip is meant
    only for the explicit cold-start case, not for degraded service-
    graph configurations.)
    """
    strat, claude_mock = _make_strategist(
        packages=None, has_layer_manager=False,
    )
    await strat.create_trade_plan()
    claude_mock.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_calla_falls_through_when_use_packages_false() -> None:
    """settings.brain.use_packages=False → legacy non-package mode is
    preserved; the pre-check is bypassed entirely.
    """
    strat, claude_mock = _make_strategist(
        packages={}, use_packages=False,
    )
    await strat.create_trade_plan()
    claude_mock.send_message.assert_awaited()
