"""Observability G1 — STRAT_CALL_A/B end-pair guarantees.

The audit's 12 STRAT_CALL_A_START vs 10 STRAT_CALL_A_END pair gap was
caused by a ``try/except Exception`` that did not catch
``BaseException`` (CancelledError, KeyboardInterrupt, SystemExit).
The fix wraps both ``create_trade_plan`` (CALL_A) and
``create_position_plan`` (CALL_B) in ``try/except/except
BaseException/finally`` so the END event fires on every exit path.
This test suite verifies the four documented paths (success, skip,
caught error, cancellation) each emit the expected
``STRAT_CALL_A_END`` / ``STRAT_CALL_B_END`` line with a stable
``status=`` field.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.brain.strategist import ClaudeStrategist


@pytest.fixture
def loguru_sink():
    """Capture loguru records into a list for assertion.

    The project uses loguru (not stdlib logging), so pytest's ``caplog``
    fixture cannot see emissions. We add a temporary list-sink and
    remove it at teardown.
    """
    records: list[str] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append(msg.record["message"]),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records: list[str], tag: str) -> list[str]:
    """Return messages from ``records`` that start with ``tag``."""
    return [m for m in records if m.startswith(tag)]


def _parse_kv(msg: str) -> dict[str, str]:
    """Extract key=value pairs from a structured log line, including the trailing ctx."""
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


def _make_calla_strategist(
    *,
    packages: dict | None,
    use_packages: bool = True,
    raise_in_claude: BaseException | None = None,
    raise_in_parse: Exception | None = None,
) -> tuple[ClaudeStrategist, MagicMock]:
    """Build a strategist whose CALL_A behavior can be steered for each test path."""
    services: dict = {}
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
    if raise_in_claude is not None:
        claude_mock.send_message = AsyncMock(side_effect=raise_in_claude)
    else:
        claude_mock.send_message = AsyncMock(return_value='{"new_trades": []}')

    strat = ClaudeStrategist(
        claude_client=claude_mock, services=services, settings=settings,
    )
    strat._build_trade_prompt = AsyncMock(return_value="(stub prompt) " * 4)

    if raise_in_parse is not None:
        strat._parse_trade_plan = MagicMock(side_effect=raise_in_parse)
    else:
        strat._parse_trade_plan = MagicMock(
            return_value=SimpleNamespace(
                new_trades=[{"symbol": "BTCUSDT", "direction": "long"}],
                position_actions={},
                risk_level="low",
                market_view="bullish bias",
            ),
        )
    return strat, claude_mock


def _make_callb_strategist(
    *,
    blocking_divergence: bool = False,
    raise_in_claude: BaseException | None = None,
    raise_in_parse: Exception | None = None,
) -> tuple[ClaudeStrategist, MagicMock]:
    """Build a strategist whose CALL_B behavior can be steered for each test path."""
    services: dict = {}
    tf = SimpleNamespace(_last_enrichment_max_divergence_pct=2.5 if blocking_divergence else 0.0)
    services["transformer"] = tf

    settings = SimpleNamespace(
        brain=SimpleNamespace(
            use_packages=False,
            surface_briefing_fields=False,
        ),
        stage2=SimpleNamespace(enable_zero_two_contract=False),
        price=SimpleNamespace(divergence_block_prompt_pct=1.0),
    )

    claude_mock = MagicMock()
    if raise_in_claude is not None:
        claude_mock.send_message = AsyncMock(side_effect=raise_in_claude)
    else:
        claude_mock.send_message = AsyncMock(
            return_value='{"position_actions": {}}',
        )

    strat = ClaudeStrategist(
        claude_client=claude_mock, services=services, settings=settings,
    )
    strat._build_position_prompt = AsyncMock(return_value="(stub prompt) " * 4)

    # The default _has_blocking_price_divergence reads transformer
    # state; stub it so we can drive the deferred path explicitly.
    strat._has_blocking_price_divergence = MagicMock(return_value=blocking_divergence)

    if raise_in_parse is not None:
        strat._parse_position_plan = MagicMock(side_effect=raise_in_parse)
    else:
        strat._parse_position_plan = MagicMock(
            return_value=SimpleNamespace(
                new_trades=[],
                position_actions={"BTCUSDT": SimpleNamespace(action="hold", reason="ok")},
            ),
        )
    return strat, claude_mock


# ─── CALL_A success path ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_calla_success_emits_end_with_status_success(loguru_sink) -> None:
    """Success path: START fires once, END fires once with status=success."""
    _ = loguru_sink
    strat, _ = _make_calla_strategist(packages={"BTCUSDT": MagicMock()})

    plan = await strat.create_trade_plan()

    assert plan is not None
    starts = _records_with_tag(loguru_sink, "STRAT_CALL_A_START")
    ends = _records_with_tag(loguru_sink, "STRAT_CALL_A_END")
    assert len(starts) == 1
    assert len(ends) == 1
    kv = _parse_kv(ends[0])
    assert kv.get("status") == "success"
    assert kv.get("trades") == "1"
    # prompt_chars / sys_prompt_chars must be populated on success path
    assert int(kv.get("prompt_chars", "0")) > 0
    assert int(kv.get("sys_prompt_chars", "0")) > 0


# ─── CALL_A skip path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_calla_skip_emits_end_with_status_skipped(loguru_sink) -> None:
    """Skip path (no packages): START + SKIPPED + END(status=skipped)."""
    _ = loguru_sink
    strat, claude_mock = _make_calla_strategist(packages={})

    plan = await strat.create_trade_plan()

    assert plan is None
    claude_mock.send_message.assert_not_awaited()
    starts = _records_with_tag(loguru_sink, "STRAT_CALL_A_START")
    skips = _records_with_tag(loguru_sink, "STRAT_CALL_A_SKIPPED")
    ends = _records_with_tag(loguru_sink, "STRAT_CALL_A_END")
    assert len(starts) == 1
    assert len(skips) == 1
    assert len(ends) == 1, "skip path must still pair with exactly one END"
    kv = _parse_kv(ends[0])
    assert kv.get("status") == "skipped"
    assert kv.get("trades") == "0"
    # The skip exit happens before the prompt is built — chars stay 0.
    assert kv.get("prompt_chars") == "0"
    assert kv.get("sys_prompt_chars") == "0"


# ─── CALL_A caught-Exception path ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_calla_caught_exception_emits_fail_and_end_failed(loguru_sink) -> None:
    """A normal ``Exception`` (e.g. parse error) → FAIL + END(status=failed)."""
    _ = loguru_sink
    strat, _ = _make_calla_strategist(
        packages={"BTCUSDT": MagicMock()},
        raise_in_parse=ValueError("synthetic parse failure"),
    )

    plan = await strat.create_trade_plan()

    assert plan is None
    fails = _records_with_tag(loguru_sink, "STRAT_CALL_A_FAIL")
    ends = _records_with_tag(loguru_sink, "STRAT_CALL_A_END")
    assert len(fails) == 1, "caught Exception must still fire FAIL"
    assert len(ends) == 1, "caught Exception must still fire exactly one END"
    kv = _parse_kv(ends[0])
    assert kv.get("status") == "failed"
    assert kv.get("trades") == "0"


# ─── CALL_A cancellation path (the actual gap) ──────────────────────────────


@pytest.mark.asyncio
async def test_calla_cancelled_emits_end_with_status_cancelled(loguru_sink) -> None:
    """``asyncio.CancelledError`` mid-cycle → END(status=cancelled) fires AND
    the exception propagates to the caller.

    This is the bug the audit's 12/10 pairing exposed: the prior
    try/except Exception swallowed nothing on cancellation — it just
    didn't run, so the END was silent. The fix routes through except
    BaseException → finally.
    """
    _ = loguru_sink
    strat, _ = _make_calla_strategist(
        packages={"BTCUSDT": MagicMock()},
        raise_in_claude=asyncio.CancelledError(),
    )

    with pytest.raises(asyncio.CancelledError):
        await strat.create_trade_plan()

    ends = _records_with_tag(loguru_sink, "STRAT_CALL_A_END")
    fails = _records_with_tag(loguru_sink, "STRAT_CALL_A_FAIL")
    assert len(ends) == 1, "cancellation path MUST emit exactly one END"
    assert len(fails) == 0, "FAIL is reserved for caught Exceptions; cancellation is not Exception"
    kv = _parse_kv(ends[0])
    assert kv.get("status") == "cancelled"
    # On cancel during await self.claude.send_message, the prompt has
    # been built so prompt_chars must be populated; whether
    # sys_prompt_chars was captured before cancel is timing-sensitive
    # but the field is present.
    assert int(kv.get("prompt_chars", "0")) > 0


# ─── CALL_B success path ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_callb_success_emits_end_with_status_success(loguru_sink) -> None:
    """CALL_B success → START + END(status=success)."""
    _ = loguru_sink
    strat, _ = _make_callb_strategist()

    plan = await strat.create_position_plan()

    assert plan is not None
    starts = _records_with_tag(loguru_sink, "STRAT_CALL_B_START")
    ends = _records_with_tag(loguru_sink, "STRAT_CALL_B_END")
    assert len(starts) == 1
    assert len(ends) == 1
    kv = _parse_kv(ends[0])
    assert kv.get("status") == "success"
    assert kv.get("acts") == "1"
    assert int(kv.get("prompt_chars", "0")) > 0
    assert int(kv.get("sys_prompt_chars", "0")) > 0


# ─── CALL_B deferred path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_callb_deferred_emits_end_with_status_deferred(loguru_sink) -> None:
    """Blocking price divergence → PROMPT_DEFERRED + END(status=deferred)."""
    _ = loguru_sink
    strat, claude_mock = _make_callb_strategist(blocking_divergence=True)

    plan = await strat.create_position_plan()

    assert plan is None
    claude_mock.send_message.assert_not_awaited()
    deferred = _records_with_tag(loguru_sink, "PROMPT_DEFERRED")
    ends = _records_with_tag(loguru_sink, "STRAT_CALL_B_END")
    assert len(deferred) == 1
    assert len(ends) == 1, "deferred path must pair with one END"
    kv = _parse_kv(ends[0])
    assert kv.get("status") == "deferred"


# ─── CALL_B cancellation path ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_callb_cancelled_emits_end_with_status_cancelled(loguru_sink) -> None:
    """CALL_B cancellation → END(status=cancelled) fires, exception propagates."""
    _ = loguru_sink
    strat, _ = _make_callb_strategist(raise_in_claude=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await strat.create_position_plan()

    ends = _records_with_tag(loguru_sink, "STRAT_CALL_B_END")
    assert len(ends) == 1
    kv = _parse_kv(ends[0])
    assert kv.get("status") == "cancelled"


# ─── CALL_A pairing invariant across N cycles ───────────────────────────────


@pytest.mark.asyncio
async def test_calla_start_end_pair_holds_across_many_cycles(loguru_sink) -> None:
    """Over a mixed run of success/skip/fail/cancel paths, START : END is 1:1.

    This is the invariant the audit's 12/10 gap exposed. Run a
    representative mix and confirm parity holds.
    """
    _ = loguru_sink

    # 3 success cycles
    strat_ok, _ = _make_calla_strategist(packages={"BTCUSDT": MagicMock()})
    for _ in range(3):
        await strat_ok.create_trade_plan()

    # 2 skip cycles (separate strategist so log accumulation is clean)
    strat_skip, _ = _make_calla_strategist(packages={})
    for _ in range(2):
        await strat_skip.create_trade_plan()

    # 1 failed cycle
    strat_fail, _ = _make_calla_strategist(
        packages={"BTCUSDT": MagicMock()},
        raise_in_parse=RuntimeError("synthetic"),
    )
    await strat_fail.create_trade_plan()

    # 1 cancelled cycle
    strat_cancel, _ = _make_calla_strategist(
        packages={"BTCUSDT": MagicMock()},
        raise_in_claude=asyncio.CancelledError(),
    )
    with pytest.raises(asyncio.CancelledError):
        await strat_cancel.create_trade_plan()

    starts = _records_with_tag(loguru_sink, "STRAT_CALL_A_START")
    ends = _records_with_tag(loguru_sink, "STRAT_CALL_A_END")
    assert len(starts) == 7, f"expected 7 starts, got {len(starts)}"
    assert len(ends) == 7, f"expected 7 ends, got {len(ends)}"
