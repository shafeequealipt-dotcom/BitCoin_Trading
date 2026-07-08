"""Unit tests for ``src/apex/qwen_client.py`` — Issue B fixes.

Covers the three Issue B fix phases:
- 3a — ``response_format: json_object`` is sent on every request (matches TIAS).
- 3b — ``APEXOptimizationError.retryable`` flag wired correctly per raise site.
- 3c — Raw response body captured on the exception for failure diagnostics.

Tests mock ``aiohttp.ClientSession.post`` directly so the network is never
touched. Mock fixture mirrors the shape of the real OpenRouter response so
parse paths remain exercised.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.apex.qwen_client import APEXOptimizationError, QwenClient


# ─── Fixture helpers ────────────────────────────────────────────────────


def _mk_response(
    *,
    status: int = 200,
    body: str = '{"choices":[{"message":{"content":"{\\"direction\\":\\"Buy\\"}"}}]}',
) -> Any:
    """Build a fake ``aiohttp`` response context manager.

    The real client uses ``async with session.post(...) as resp`` and then
    awaits ``resp.text()``. We mirror that shape with an AsyncMock-backed
    context manager and an async ``text`` method.
    """
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=body)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _mk_session(response: Any) -> MagicMock:
    """Build a fake aiohttp.ClientSession that returns the given response
    for any ``.post(...)`` call. Tracks the most-recent call for payload
    inspection."""
    session = MagicMock()
    session.closed = False
    session.post = MagicMock(return_value=response)
    return session


# ─── 3a — JSON mode payload tests ───────────────────────────────────────


class TestJsonModePayload:
    """Issue B Phase 3a — ``response_format: {"type": "json_object"}``
    is included on every request, matching TIAS's payload shape.
    """

    @pytest.mark.asyncio
    async def test_payload_includes_response_format_json_object(self) -> None:
        """The HTTP payload sent to OpenRouter must include
        ``response_format: {"type": "json_object"}`` so the upstream
        contract matches TIAS's. Pre-fix this field was absent and APEX
        saw 8 unique ``no choices`` failures over the 4-day audit
        window vs TIAS's 0."""
        client = QwenClient(api_key="test_key")
        response = _mk_response()
        session = _mk_session(response)

        with patch.object(client, "_get_session", return_value=session):
            await client.optimize(
                system_prompt="sys",
                user_prompt="user",
                model="deepseek/deepseek-v3.2",
            )

        # Inspect the most-recent .post() call's `json` kwarg.
        assert session.post.call_count == 1
        _args, kwargs = session.post.call_args
        payload = kwargs["json"]
        assert "response_format" in payload, (
            "response_format absent — Issue B fix regressed"
        )
        assert payload["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_payload_preserves_other_fields(self) -> None:
        """Adding ``response_format`` must not displace existing
        payload fields (model / messages / temperature / max_tokens)."""
        client = QwenClient(api_key="test_key")
        response = _mk_response()
        session = _mk_session(response)

        with patch.object(client, "_get_session", return_value=session):
            await client.optimize(
                system_prompt="sys content",
                user_prompt="user content",
                model="deepseek/deepseek-v3.2",
                temperature=0.2,
                max_tokens=800,
            )

        _args, kwargs = session.post.call_args
        payload = kwargs["json"]
        assert payload["model"] == "deepseek/deepseek-v3.2"
        assert payload["temperature"] == 0.2
        assert payload["max_tokens"] == 800
        assert payload["messages"] == [
            {"role": "system", "content": "sys content"},
            {"role": "user", "content": "user content"},
        ]

    @pytest.mark.asyncio
    async def test_success_path_still_parses_with_json_mode(self) -> None:
        """JSON-mode payload doesn't break the existing response parser
        when OpenRouter returns a well-formed response."""
        client = QwenClient(api_key="test_key")
        body = json.dumps({
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "direction": "Buy",
                        "sl_pct": 1.5,
                        "tp_pct": 2.0,
                    })
                }
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "model": "deepseek/deepseek-v3.2",
        })
        session = _mk_session(_mk_response(body=body))

        with patch.object(client, "_get_session", return_value=session):
            result = await client.optimize(
                system_prompt="sys",
                user_prompt="user",
                model="deepseek/deepseek-v3.2",
            )

        assert result["content"]["direction"] == "Buy"
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        assert result["model_used"] == "deepseek/deepseek-v3.2"


# ─── 3b — Retryable flag tests ──────────────────────────────────────────


class TestRetryableFlag:
    """Issue B Phase 3b — ``APEXOptimizationError`` carries a
    ``retryable`` boolean attribute. Empty-content failure modes set
    ``retryable=True`` so the caller (TradeOptimizer) can retry once.
    HTTP errors, body-parse errors, timeouts, and connection errors set
    ``retryable=False``.
    """

    def test_default_retryable_false(self) -> None:
        """Creating an error without specifying ``retryable`` defaults
        to False — matches the pre-fix ABI for any external constructor
        site that exists today."""
        err = APEXOptimizationError("plain error")
        assert err.retryable is False

    def test_explicit_retryable_true(self) -> None:
        err = APEXOptimizationError("transient", retryable=True)
        assert err.retryable is True

    def test_raw_body_default_none(self) -> None:
        err = APEXOptimizationError("plain")
        assert err.raw_body is None

    def test_explicit_raw_body(self) -> None:
        err = APEXOptimizationError(
            "x", raw_body="body sample", retryable=True,
        )
        assert err.raw_body == "body sample"

    @pytest.mark.asyncio
    async def test_no_choices_raises_retryable_with_body(self) -> None:
        """``OpenRouter response has no choices`` is retryable — this
        is the empirically-transient failure mode that triggered the
        16-minute cluster on 2026-05-08. Raw body is captured for
        diagnostics."""
        client = QwenClient(api_key="test_key")
        body = json.dumps({"choices": []})  # empty choices
        session = _mk_session(_mk_response(body=body))

        with patch.object(client, "_get_session", return_value=session):
            with pytest.raises(APEXOptimizationError) as excinfo:
                await client.optimize(
                    system_prompt="sys",
                    user_prompt="user",
                    model="deepseek/deepseek-v3.2",
                )

        assert excinfo.value.retryable is True
        assert "no choices" in str(excinfo.value)
        assert excinfo.value.raw_body == body

    @pytest.mark.asyncio
    async def test_empty_content_raises_retryable_with_body(self) -> None:
        """``OpenRouter response message content is empty`` — same
        upstream root cause as ``no choices``, different stage of the
        response pipeline. Retryable, body captured."""
        client = QwenClient(api_key="test_key")
        body = json.dumps({
            "choices": [{"message": {"content": ""}}]
        })
        session = _mk_session(_mk_response(body=body))

        with patch.object(client, "_get_session", return_value=session):
            with pytest.raises(APEXOptimizationError) as excinfo:
                await client.optimize(
                    system_prompt="sys",
                    user_prompt="user",
                    model="deepseek/deepseek-v3.2",
                )

        assert excinfo.value.retryable is True
        assert "content is empty" in str(excinfo.value)
        assert excinfo.value.raw_body == body

    @pytest.mark.asyncio
    async def test_invalid_content_json_raises_retryable_with_body(
        self,
    ) -> None:
        r"""Content that is not valid JSON (e.g. ``\`\``` or ``{``) —
        observed 3 times in the 4-day audit window. Likely the same
        upstream root cause as ``no choices``; retryable."""
        client = QwenClient(api_key="test_key")
        body = json.dumps({
            "choices": [{"message": {"content": "```"}}]
        })
        session = _mk_session(_mk_response(body=body))

        with patch.object(client, "_get_session", return_value=session):
            with pytest.raises(APEXOptimizationError) as excinfo:
                await client.optimize(
                    system_prompt="sys",
                    user_prompt="user",
                    model="deepseek/deepseek-v3.2",
                )

        assert excinfo.value.retryable is True
        assert "invalid JSON" in str(excinfo.value)
        assert excinfo.value.raw_body == body

    @pytest.mark.asyncio
    async def test_http_500_raises_non_retryable(self) -> None:
        """HTTP non-200 errors are NOT retryable — likely persistent
        (auth, model-not-found, etc.). Body still captured."""
        client = QwenClient(api_key="test_key")
        body = "Internal Server Error"
        session = _mk_session(_mk_response(status=500, body=body))

        with patch.object(client, "_get_session", return_value=session):
            with pytest.raises(APEXOptimizationError) as excinfo:
                await client.optimize(
                    system_prompt="sys",
                    user_prompt="user",
                    model="deepseek/deepseek-v3.2",
                )

        assert excinfo.value.retryable is False
        assert "HTTP 500" in str(excinfo.value)
        assert excinfo.value.raw_body == body

    @pytest.mark.asyncio
    async def test_non_json_body_raises_retryable(self) -> None:
        """If OpenRouter returns 200 but a non-JSON body, treat as
        retryable — gateway-side malformed response is empirically
        transient (similar to the 15:33-15:49 cluster pattern)."""
        client = QwenClient(api_key="test_key")
        body = "Garbage non-JSON text from gateway"
        session = _mk_session(_mk_response(body=body))

        with patch.object(client, "_get_session", return_value=session):
            with pytest.raises(APEXOptimizationError) as excinfo:
                await client.optimize(
                    system_prompt="sys",
                    user_prompt="user",
                    model="deepseek/deepseek-v3.2",
                )

        assert excinfo.value.retryable is True
        assert "non-JSON body" in str(excinfo.value)
        assert excinfo.value.raw_body == body


# ─── 3c — Raw body capture tests ────────────────────────────────────────


class TestRawBodyCapture:
    """Issue B Phase 3c — every failure that has access to the raw
    response body must capture (truncated to 1000 chars) on the
    exception so operators can diagnose the next incident from a single
    log line."""

    @pytest.mark.asyncio
    async def test_raw_body_truncated_to_1000_chars(self) -> None:
        """Long bodies are truncated to 1000 chars — keeps log lines
        bounded, prevents accidental log floods on huge upstream
        errors."""
        client = QwenClient(api_key="test_key")
        # 2000 chars of garbage as a non-JSON body.
        body = "X" * 2000
        session = _mk_session(_mk_response(body=body))

        with patch.object(client, "_get_session", return_value=session):
            with pytest.raises(APEXOptimizationError) as excinfo:
                await client.optimize(
                    system_prompt="sys",
                    user_prompt="user",
                    model="deepseek/deepseek-v3.2",
                )

        assert excinfo.value.raw_body is not None
        assert len(excinfo.value.raw_body) == 1000

    @pytest.mark.asyncio
    async def test_raw_body_unset_on_success(self) -> None:
        """On the success path, no exception is raised — raw_body lives
        only on the exception object, never on the return value."""
        client = QwenClient(api_key="test_key")
        body = json.dumps({
            "choices": [{
                "message": {"content": json.dumps({"direction": "Buy"})}
            }]
        })
        session = _mk_session(_mk_response(body=body))

        with patch.object(client, "_get_session", return_value=session):
            result = await client.optimize(
                system_prompt="sys",
                user_prompt="user",
                model="deepseek/deepseek-v3.2",
            )

        assert isinstance(result, dict)
        assert "raw_body" not in result  # success returns parsed content

    @pytest.mark.asyncio
    async def test_raw_body_present_on_no_choices_error(self) -> None:
        """End-to-end sanity: the failure mode that was opaque pre-fix
        (only ``str(e)[:120]`` in the log) now carries the entire
        diagnostic body on the exception."""
        client = QwenClient(api_key="test_key")
        body = json.dumps({"choices": [], "model": "deepseek/deepseek-v3.2"})
        session = _mk_session(_mk_response(body=body))

        with patch.object(client, "_get_session", return_value=session):
            with pytest.raises(APEXOptimizationError) as excinfo:
                await client.optimize(
                    system_prompt="sys",
                    user_prompt="user",
                    model="deepseek/deepseek-v3.2",
                )

        # The body parses back to JSON containing the diagnostic info
        # operators would need (e.g., model echo, usage block).
        body_back = json.loads(excinfo.value.raw_body or "{}")
        assert body_back["choices"] == []
        assert body_back["model"] == "deepseek/deepseek-v3.2"


# ─── Optimizer retry-loop integration tests (Phase 3b) ───────────────────


class TestOptimizerRetryLoop:
    """Integration tests covering the bounded-retry wrapper at
    ``optimizer.py:233``. The wrapper retries once on
    ``retryable=True`` errors with a configurable backoff; non-
    retryable errors fall through to the broad ``except`` and the
    fallback path.

    To exercise just the retry-loop wrapper (lines ~243–289 of
    optimizer.py) without standing up an entire IntelligencePackage,
    these tests patch ``build_apex_user_prompt`` to a stub and patch
    the post-call helpers (``_parse_response``, ``_apply_constraints``,
    ``_log_optimization``) to no-ops. The retry loop itself is exercised
    end-to-end against the mocked client.
    """

    @pytest.mark.asyncio
    async def test_retryable_succeeds_on_second_attempt(self) -> None:
        """A transient ``no choices`` error on the first attempt
        followed by a successful response on the second attempt should
        yield a successful optimization. Mirrors the 16-minute cluster
        pattern on 2026-05-08: a single retry would have likely
        smoothed all 4 events."""
        # Mocked client returns the second time after first raises.
        client = MagicMock()
        client.optimize = AsyncMock(side_effect=[
            APEXOptimizationError(
                "OpenRouter response has no choices",
                retryable=True,
                raw_body='{"choices":[]}',
            ),
            {"content": {"direction": "Buy"}},  # arbitrary success
        ])
        with _patched_optimizer_internals():
            opt = _build_minimal_optimizer(client)
            result = await opt.optimize(
                directive=_minimal_directive("TESTUSDT", "Buy"),
            )

        # Second attempt succeeded → not a fallback.
        assert result.is_fallback is False
        assert client.optimize.call_count == 2

    @pytest.mark.asyncio
    async def test_retryable_exhausts_falls_back(self) -> None:
        """Two consecutive retryable errors must end in fallback —
        ``APEX never blocks a trade`` is preserved by ``_fallback``
        returning is_fallback=True with Claude's directive intact."""
        client = MagicMock()
        client.optimize = AsyncMock(side_effect=[
            APEXOptimizationError(
                "OpenRouter response has no choices",
                retryable=True,
                raw_body='{"choices":[]}',
            ),
            APEXOptimizationError(
                "OpenRouter response has no choices",
                retryable=True,
                raw_body='{"choices":[]}',
            ),
        ])
        with _patched_optimizer_internals():
            opt = _build_minimal_optimizer(client)
            result = await opt.optimize(
                directive=_minimal_directive("TESTUSDT", "Buy"),
            )

        assert result.is_fallback is True
        assert result.direction == "Buy"  # Claude's direction preserved
        assert client.optimize.call_count == 2

    @pytest.mark.asyncio
    async def test_non_retryable_error_does_not_retry(self) -> None:
        """An HTTP 500 (retryable=False) must fall through to fallback
        on the first attempt — no retry storm on persistent errors."""
        client = MagicMock()
        client.optimize = AsyncMock(side_effect=APEXOptimizationError(
            "OpenRouter HTTP 500: Internal Server Error",
            retryable=False,
            raw_body="Internal Server Error",
        ))
        with _patched_optimizer_internals():
            opt = _build_minimal_optimizer(client)
            result = await opt.optimize(
                directive=_minimal_directive("TESTUSDT", "Buy"),
            )

        assert result.is_fallback is True
        assert client.optimize.call_count == 1


# ─── Auth-token redaction tests (Phase 3c) ──────────────────────────────


class TestRedactAuthTokens:
    """``_redact_auth_tokens`` strips any auth-token-shaped substring
    before raw response bodies are emitted into log lines. Defence in
    depth: response bodies should not carry auth headers, but the
    redaction guarantees they never appear in logs even if they do."""

    def test_redacts_bearer_token(self) -> None:
        from src.apex.optimizer import _redact_auth_tokens
        out = _redact_auth_tokens("error: Bearer sk-abc123def456 invalid")
        assert "Bearer sk-abc123def456" not in out
        assert "[REDACTED]" in out

    def test_redacts_authorization_json_fragment(self) -> None:
        from src.apex.optimizer import _redact_auth_tokens
        out = _redact_auth_tokens(
            'headers={"Authorization": "Bearer xyz", "Other": "ok"}'
        )
        assert "Bearer xyz" not in out
        assert "[REDACTED]" in out
        assert '"Other": "ok"' in out  # untouched

    def test_redacts_sk_token_alone(self) -> None:
        from src.apex.optimizer import _redact_auth_tokens
        out = _redact_auth_tokens("token=sk-1234567890abcdefghij1234567890")
        assert "sk-1234567890abcdefghij1234567890" not in out
        assert "[REDACTED]" in out

    def test_passes_through_normal_content(self) -> None:
        from src.apex.optimizer import _redact_auth_tokens
        body = '{"choices":[],"model":"deepseek/deepseek-v3.2"}'
        out = _redact_auth_tokens(body)
        assert out == body

    def test_empty_string_returns_empty(self) -> None:
        from src.apex.optimizer import _redact_auth_tokens
        assert _redact_auth_tokens("") == ""

    def test_case_insensitive_match(self) -> None:
        from src.apex.optimizer import _redact_auth_tokens
        out = _redact_auth_tokens("BEARER abc123def456 and bearer xyz789abc12")
        assert "abc123def456" not in out
        assert "xyz789abc12" not in out


# ─── Helper: minimal optimizer + directive for retry tests ───────────────


def _patched_optimizer_internals() -> Any:
    """Context manager that patches the optimizer's heavy internals so
    retry-loop tests exercise only the lines under test (the retry
    wrapper and the outer fallback path).

    Patches:
    - ``build_apex_user_prompt``: stubbed to return a constant string;
      avoids needing a fully-shaped ``IntelligencePackage.directive``.
    - ``asyncio.sleep`` (in optimizer's namespace): no-op AsyncMock;
      keeps the test fast — retries happen instantly.
    - ``TradeOptimizer._parse_response``: returns a minimal valid
      ``OptimizedTrade`` so the post-call flow (constraints, logging)
      runs without exploding.
    - ``TradeOptimizer._apply_constraints``: identity function.
    - ``TradeOptimizer._log_optimization``: no-op.
    """
    from contextlib import ExitStack

    from src.apex.models import OptimizedTrade
    from src.apex.optimizer import TradeOptimizer

    def _stub_optimized() -> OptimizedTrade:
        return OptimizedTrade(
            symbol="TESTUSDT",
            direction="Buy",
            sl_pct=1.5,
            tp_pct=2.0,
            tp_mode="fixed",
            position_size_usd=600,
            leverage=3,
            entry_timing="immediate",
            add_on_pullback=False,
            add_trigger_pct=0.0,
            add_size_pct=0,
            reasoning="stub",
            confidence=0.8,
            was_flipped=False,
            original_direction="Buy",
            original_sl=0.0,
            original_tp=0.0,
            original_size=600.0,
            is_fallback=False,
        )

    stack = ExitStack()
    stack.enter_context(patch(
        "src.apex.optimizer.build_apex_user_prompt",
        return_value="stub user prompt",
    ))
    stack.enter_context(patch(
        "src.apex.optimizer.asyncio.sleep", new=AsyncMock(),
    ))
    stack.enter_context(patch.object(
        TradeOptimizer, "_parse_response",
        return_value=_stub_optimized(),
    ))
    stack.enter_context(patch.object(
        TradeOptimizer, "_apply_constraints",
        side_effect=lambda opt, _coin: opt,
    ))
    stack.enter_context(patch.object(
        TradeOptimizer, "_log_optimization",
        return_value=None,
    ))
    return stack


def _minimal_directive(symbol: str, direction: str) -> dict:
    """Return a minimal Claude directive sufficient to drive the
    optimizer's retry test path. All numeric defaults match the real
    fallback values so ``_fallback(directive, ...)`` produces a
    plausible OptimizedTrade for assertions.

    T2-2 (2026-05-12): SL/TP prices kept within the SLTPValidator safe
    band (under 9% from price=1.0) so the new fallback's percentage-of-
    price clamp does NOT fire. These tests assert the retry-loop
    semantics (is_fallback=True after exhaustion), not T2-2's clamp
    behaviour — that's covered separately in
    tests/test_t2_2_apex_fallback_sl.py.
    """
    return {
        "symbol": symbol,
        "direction": direction,
        "stop_loss_price": 0.95,    # 5% from price=1.0 — within validator
        "take_profit_price": 1.05,  # 5% from price=1.0 — within validator
        "size_usd": 600,
        "leverage": 3,
        "score": 50,
    }


def _build_minimal_optimizer(qwen_client: Any) -> Any:
    """Wire a ``TradeOptimizer`` with the minimum dependencies the
    retry-loop tests require. The assembler returns a stub package
    that satisfies the ``Tier 1`` pathway (sufficient symbol history
    so we don't take the early ``return self._fallback``); the
    settings expose the per-attempt knobs.

    SimpleNamespace is used (not MagicMock) for the package's nested
    attributes because the optimizer formats numeric fields with
    ``f'{x:.1f}'`` which MagicMock can't satisfy.
    """
    from types import SimpleNamespace

    from src.apex.optimizer import TradeOptimizer

    package = SimpleNamespace(
        symbol_history=SimpleNamespace(
            total_trades=100,  # Tier 1
            pattern_summary="stub",
        ),
        situation_data=SimpleNamespace(
            total_trades_in_condition=100,
            regime="trending_up",
            buy_win_rate=60.0,
            sell_win_rate=40.0,
            direction_bias="long",
        ),
        coin_data=SimpleNamespace(
            current_price=1.0,
            recommended_tp_pct=2.0,
            volatility_class="medium",
        ),
        directive=SimpleNamespace(reasoning="stub"),
        structural_data=None,  # disables RR boost path (correct attribute)
    )

    assembler = MagicMock()
    assembler.assemble = AsyncMock(return_value=package)

    settings = MagicMock()
    settings.enabled = True
    settings.model = "deepseek/deepseek-v3.2"
    settings.temperature = 0.2
    settings.max_tokens = 800
    settings.timeout_seconds = 60
    settings.min_tias_trades_for_optimization = 3
    settings.min_regime_trades_for_fallback = 10
    settings.tp_cap_multiplier_by_class = {
        "dead": 1.4, "low": 1.5, "medium": 1.6,
        "high": 1.8, "extreme": 2.0,
    }
    settings.apex_tp_cap_hard_ceiling_pct = 5.0
    settings.apex_max_attempts = 2
    settings.apex_retry_backoff_seconds = 0.7
    settings.max_position_size_usd = 1200.0
    settings.max_leverage = 5
    settings.min_tp_pct = 0.3
    settings.apex_block_flip_resize = True
    settings.apex_min_flip_confidence = 0.70
    settings.apex_flip_rr_boost_threshold = 3.0
    settings.apex_flip_rr_boost_amount = 0.15
    settings.gate_apex_size_cap_mult = 1.5

    return TradeOptimizer(
        qwen_client=qwen_client, assembler=assembler, settings=settings,
    )
