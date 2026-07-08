"""GLM-5.2 brain client via Cloudflare Workers AI.

Drop-in replacement for ClaudeCodeClient (same send_message/extract_json/
get_stats/shutdown/set_alert_callback interface — see claude_code_client.py).
Wired in as the sole brain provider per operator decision (2026-07-06).

GLM-5.2 is a reasoning model: the API returns both `reasoning_content` (chain
of thought) and `content` (final answer) per choice. If max_tokens is too low,
the response can be truncated mid-reasoning with `content` empty — callers
must budget max_tokens generously (reasoning + final JSON), not just for the
expected JSON size.

API: POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}
Auth: Bearer {cloudflare_api_token}
Verified live (2026-07-06): returns an OpenAI-style chat.completion wrapped in
`result`, with `content`/`reasoning_content` split per the reasoning-model shape above.
"""

import json
import re
import time as _time

import aiohttp

from src.core.exceptions import BrainError, ClaudeAPIError
from src.core.decorators import retry
from src.core.logging import get_logger

log = get_logger("brain")

_CF_API_BASE = "https://api.cloudflare.com/client/v4/accounts"


class GLMClient:
    """Cloudflare Workers AI client for GLM-5.2 — same interface as ClaudeCodeClient.

    Args:
        api_key: Cloudflare API token (Bearer auth).
        account_id: Cloudflare account ID.
        model: Workers AI model path, e.g. "@cf/zai-org/glm-5.2".
        timeout_seconds: Per-request timeout. Reasoning models are slow —
            keep this generous relative to Claude CLI's 300s default.
        max_tokens: Token budget covering BOTH the hidden reasoning trace and
            the final answer. Too low truncates before `content` is written.
        temperature: Sampling temperature.
        max_retries: Retry attempts on transient (retryable) failures.
    """

    def __init__(
        self,
        api_key: str,
        account_id: str,
        model: str = "@cf/zai-org/glm-5.2",
        timeout_seconds: float = 180.0,
        max_tokens: int = 8000,
        temperature: float = 0.3,
        max_retries: int = 2,
    ) -> None:
        if not api_key or not account_id:
            log.warning("GLM_CLIENT_NO_CREDENTIALS | Cloudflare Workers AI key/account_id not set — brain will not function")
        self._api_key = api_key
        self._account_id = account_id
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_tokens = max_tokens
        self._temperature = temperature
        self.max_retries = max_retries
        self._url = f"{_CF_API_BASE}/{account_id}/ai/run/{model}"
        self._session: aiohttp.ClientSession | None = None

        # Watchdog-compat heartbeat fields (see position_watchdog.py).
        self._last_call_time: float = _time.time()
        self._last_response_time: float = _time.time()
        self._consecutive_failures: int = 0
        self._call_count: int = 0
        self._total_calls_today: int = 0
        self._alert_callback = None

    def _get_session(self) -> aiohttp.ClientSession:
        """Deprecated — kept only so any stale external reference doesn't
        crash. send_message no longer calls this; see its docstring for why
        (2026-07-08 stuck-connection fix)."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                }
            )
        return self._session

    def set_alert_callback(self, callback) -> None:
        """Interface parity with ClaudeCodeClient — stored, not currently used."""
        self._alert_callback = callback

    @retry(max_attempts=2, delay=5.0, exceptions=(ClaudeAPIError,))
    async def send_message(
        self, prompt: str, system_prompt: str = "", max_tokens: int | None = None,
        call_type: str = "other",
    ) -> str:
        """Send a message to GLM-5.2 via Cloudflare Workers AI and return the
        final answer text (reasoning_content is logged at debug, not returned).

        Raises:
            ClaudeAPIError: HTTP failure, empty/non-JSON body, or truncated
                response (finish_reason == "length" with empty content —
                raise max_tokens if this happens repeatedly).
            BrainError: Missing credentials.
        """
        if not self._api_key or not self._account_id:
            raise BrainError("Cloudflare Workers AI credentials not set — cannot call GLM-5.2")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "messages": messages,
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": self._temperature,
        }

        # Stuck-connection fix (2026-07-08): a PERSISTED session across calls
        # (the prior self._session reuse pattern) let one call's aborted/timed-
        # out connection leave the pool in a state where the NEXT call's
        # supposedly-240s-bounded request instead hung for ~670s — nearly 3x
        # past `total`. Observed live: one GLM call's outer elapsed was 692s
        # while its own final-attempt error reported only 15.5s, meaning the
        # FIRST attempt silently blocked far past its timeout. At this call
        # cadence (~every 5 min) there is no meaningful perf cost to opening a
        # fresh connection per call — doing so removes the whole bug class
        # instead of chasing the exact aiohttp/keepalive edge case. sock_connect
        # + sock_read are set alongside total as defense-in-depth (per-phase
        # bounds tend to be enforced more reliably than `total` alone across
        # aiohttp versions).
        timeout = aiohttp.ClientTimeout(
            total=self._timeout_seconds,
            sock_connect=30.0,
            sock_read=self._timeout_seconds,
        )
        self._last_call_time = _time.time()
        t0 = _time.monotonic()

        try:
            async with aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            ) as session, session.post(
                self._url, json=payload, timeout=timeout,
            ) as resp:
                elapsed_ms = int((_time.monotonic() - t0) * 1000)
                raw_body = await resp.text()

                if resp.status != 200:
                    self._consecutive_failures += 1
                    raise ClaudeAPIError(
                        f"Cloudflare Workers AI HTTP {resp.status} after {elapsed_ms}ms: {raw_body[:300]}",
                    )

                try:
                    body = json.loads(raw_body)
                except json.JSONDecodeError as e:
                    self._consecutive_failures += 1
                    raise ClaudeAPIError(f"Cloudflare returned non-JSON body: {str(e)[:100]}")

                if not body.get("success", False):
                    self._consecutive_failures += 1
                    raise ClaudeAPIError(f"Cloudflare reported failure: {body.get('errors')}")

                choices = (body.get("result") or {}).get("choices") or []
                if not choices:
                    self._consecutive_failures += 1
                    raise ClaudeAPIError("GLM response has no choices")

                choice = choices[0]
                message = choice.get("message") or {}
                content = message.get("content", "") or ""
                reasoning = message.get("reasoning_content", "") or ""
                finish_reason = choice.get("finish_reason", "")

                if not content:
                    self._consecutive_failures += 1
                    if finish_reason == "length":
                        raise ClaudeAPIError(
                            f"GLM truncated before final answer (max_tokens={payload['max_tokens']} "
                            f"exhausted by reasoning, {len(reasoning)} reasoning chars produced, "
                            f"0 answer chars) — raise max_tokens"
                        )
                    raise ClaudeAPIError(f"GLM response content is empty (finish_reason={finish_reason})")

                self._last_response_time = _time.time()
                self._consecutive_failures = 0
                self._call_count += 1
                self._total_calls_today += 1

                usage = (body.get("result") or {}).get("usage") or {}
                log.info(
                    f"GLM_CALL_OK | call_type={call_type} elapsed_ms={elapsed_ms} "
                    f"prompt_tokens={usage.get('prompt_tokens', 0)} "
                    f"completion_tokens={usage.get('completion_tokens', 0)} "
                    f"reasoning_chars={len(reasoning)} answer_chars={len(content)} "
                    f"finish_reason={finish_reason}"
                )
                log.debug(f"GLM_REASONING | call_type={call_type} reasoning='{reasoning[:500]}'")

                return content

        except aiohttp.ClientError as e:
            self._consecutive_failures += 1
            raise ClaudeAPIError(f"Cloudflare connection error: {e}")
        except (ClaudeAPIError, BrainError):
            raise
        except Exception as e:
            self._consecutive_failures += 1
            raise ClaudeAPIError(f"Unexpected GLM client error: {e}")

    def extract_json(self, response: str) -> dict:
        """Extract JSON from a GLM response — same multi-strategy approach
        used across the brain's providers (fenced block, bare object, bare
        array, raw parse)."""
        text = response.strip()

        match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            try:
                result = json.loads(text[start : end + 1])
                return {"decisions": result} if isinstance(result, list) else result
            except json.JSONDecodeError:
                pass

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            log.warning(f"GLM_PARSE_FAIL | err='{str(e)[:80]}' raw_response='{text[:100]}...'")
            raise ValueError(f"Cannot extract JSON from GLM response:\n{text[:300]}...")

    def get_stats(self) -> dict:
        return {
            "calls_today": self._total_calls_today,
            "cost_today": 0.0,
            "consecutive_failures": self._consecutive_failures,
            "model": self._model,
        }

    def shutdown(self) -> None:
        """Best-effort, idempotent, non-blocking — matches ClaudeCodeClient's
        sync shutdown() contract (called unawaited from manager.py teardown)."""
        if self._session is not None and not self._session.closed:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._session.close())
                else:
                    loop.run_until_complete(self._session.close())
            except Exception as e:
                log.debug(f"GLM_CLIENT_SHUTDOWN_FAIL | err='{str(e)[:100]}'")
