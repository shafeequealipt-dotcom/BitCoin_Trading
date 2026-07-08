"""DeepSeek client for TIAS Phase 2 — calls OpenRouter to analyze closed trades.

Design:
- Lazy persistent aiohttp.ClientSession: created on first call, reused across
  all subsequent calls. Session creation overhead eliminated for 30+/day trades.
- TIASAnalysisError is raised for ALL known-failure conditions (HTTP errors,
  parse failures, timeout). Callers catch this for retry/fallback logic.
- Generic exceptions bubble up to the TradeAnalyzer for separate handling.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("tias")


class TIASAnalysisError(Exception):
    """Raised when DeepSeek returns an error or the response cannot be parsed.

    Retryable errors (HTTP 429, 503, timeout) should be handled by the caller.
    All other DeepSeek/parse failures are not retried.
    """

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class DeepSeekResponse:
    """Parsed, validated response from the OpenRouter API.

    Attributes:
        content: Parsed JSON dict returned by DeepSeek (the analysis payload).
        model: Model ID as reported by the API (may differ from requested model).
        input_tokens: Prompt token count from usage stats.
        output_tokens: Completion token count from usage stats.
        response_time_ms: Wall-clock time for the HTTP round-trip in ms.
    """

    content: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    response_time_ms: int


class DeepSeekClient:
    """Async OpenRouter client for TIAS DeepSeek analysis.

    Uses a lazy persistent aiohttp.ClientSession — created on first call and
    reused thereafter. Checks the `closed` flag before each request so the
    session is transparently re-created after a service restart or explicit close.

    Args:
        api_key: OpenRouter API key (Bearer token).
        api_url: Full OpenRouter chat completions URL.
        http_referer: HTTP-Referer header value (for OpenRouter attribution).
        x_title: X-Title header value (for OpenRouter attribution).
    """

    def __init__(
        self,
        api_key: str,
        api_url: str = "https://openrouter.ai/api/v1/chat/completions",
        http_referer: str = "https://github.com/trading-intelligence-mcp",
        x_title: str = "TIAS-TradeAnalysis",
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url
        self._http_referer = http_referer
        self._x_title = x_title
        self._session: Optional[aiohttp.ClientSession] = None

    def _get_session(self) -> aiohttp.ClientSession:
        """Return existing session or create a new one if closed/absent."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": self._http_referer,
                    "X-Title": self._x_title,
                }
            )
        return self._session

    async def analyze(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        timeout_seconds: int = 45,
    ) -> DeepSeekResponse:
        """POST to OpenRouter and return a parsed DeepSeekResponse.

        Args:
            system_prompt: System-level instructions (analyst persona + schema).
            user_prompt: Trade context formatted for analysis.
            model: OpenRouter model ID (e.g. "deepseek/deepseek-chat-v3-0324").
            temperature: Sampling temperature (lower = more deterministic).
            max_tokens: Maximum completion tokens.
            timeout_seconds: Total request timeout.

        Returns:
            DeepSeekResponse with parsed content dict and usage metadata.

        Raises:
            TIASAnalysisError: On HTTP errors, parse failures, or timeouts.
        """
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        session = self._get_session()
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        t_start = time.monotonic()

        try:
            async with session.post(
                self._api_url,
                json=payload,
                timeout=timeout,
            ) as resp:
                response_time_ms = int((time.monotonic() - t_start) * 1000)
                raw_body = await resp.text()

                if resp.status == 429:
                    raise TIASAnalysisError(
                        f"OpenRouter rate-limited (HTTP 429) after {response_time_ms}ms",
                        retryable=True,
                    )
                if resp.status == 503:
                    raise TIASAnalysisError(
                        f"OpenRouter unavailable (HTTP 503) after {response_time_ms}ms",
                        retryable=True,
                    )
                if resp.status != 200:
                    raise TIASAnalysisError(
                        f"OpenRouter HTTP {resp.status}: {raw_body[:300]}",
                        retryable=False,
                    )

                try:
                    body = json.loads(raw_body)
                except json.JSONDecodeError as e:
                    raise TIASAnalysisError(
                        f"OpenRouter returned non-JSON body: {str(e)[:100]}",
                        retryable=False,
                    )

                # Extract content string from standard OpenAI-compatible response
                choices = body.get("choices") or []
                if not choices:
                    raise TIASAnalysisError(
                        "OpenRouter response has no choices", retryable=False
                    )
                raw_content = (choices[0].get("message") or {}).get("content", "")
                if not raw_content:
                    raise TIASAnalysisError(
                        "OpenRouter response message content is empty", retryable=False
                    )

                content_dict = self._parse_json(raw_content, model)

                usage = body.get("usage") or {}
                returned_model = body.get("model", model)
                in_tokens = int(usage.get("prompt_tokens") or 0)
                out_tokens = int(usage.get("completion_tokens") or 0)

                # Phase 12.10 (lifecycle-logging-audit Gap 9.4-G1 / 10.1-G1):
                # per-call DeepSeek visibility separate from analyzer wrapper.
                # Forensic value for latency / token-cost attribution.
                log.info(
                    f"TIAS_DEEPSEEK_OK | model={returned_model} "
                    f"latency_ms={response_time_ms} tokens_in={in_tokens} "
                    f"tokens_out={out_tokens} | {ctx()}"
                )

                return DeepSeekResponse(
                    content=content_dict,
                    model=returned_model,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    response_time_ms=response_time_ms,
                )

        except TIASAnalysisError as e:
            # Phase 12.10 (lifecycle-logging-audit Gap 9.4-G1 / 10.1-G1):
            # per-call DeepSeek failure visibility.
            log.warning(
                f"TIAS_DEEPSEEK_FAIL | model={model} "
                f"err='{str(e)[:120]}' retryable={e.retryable} | {ctx()}"
            )
            raise
        except aiohttp.ServerTimeoutError:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            log.warning(
                f"TIAS_DEEPSEEK_FAIL | model={model} "
                f"err='timeout after {elapsed_ms}ms' retryable=True | {ctx()}"
            )
            raise TIASAnalysisError(
                f"OpenRouter request timed out after {elapsed_ms}ms (limit={timeout_seconds}s)",
                retryable=True,
            )
        except aiohttp.ClientError as e:
            log.warning(
                f"TIAS_DEEPSEEK_FAIL | model={model} "
                f"err='client_error: {str(e)[:120]}' retryable=True | {ctx()}"
            )
            raise TIASAnalysisError(
                f"OpenRouter connection error: {str(e)[:200]}",
                retryable=True,
            )

    def _parse_json(self, raw: str, model: str) -> dict[str, Any]:
        """Parse DeepSeek content string, stripping markdown fences if present.

        Args:
            raw: Raw content string from the API response.
            model: Model ID (used in error messages only).

        Returns:
            Parsed dict from the content.

        Raises:
            TIASAnalysisError: If content cannot be parsed as JSON.
        """
        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())

        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise TIASAnalysisError(
                f"DeepSeek ({model}) returned invalid JSON: {str(e)[:100]} | "
                f"content[:200]={raw[:200]}",
                retryable=False,
            )

        if not isinstance(result, dict):
            raise TIASAnalysisError(
                f"DeepSeek ({model}) returned non-dict JSON: {type(result).__name__}",
                retryable=False,
            )
        return result

    async def close(self) -> None:
        """Close the underlying aiohttp session. Safe to call multiple times."""
        if self._session and not self._session.closed:
            await self._session.close()
            log.debug("TIAS DeepSeekClient session closed | {ctx}", ctx=ctx())
        self._session = None
