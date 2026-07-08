"""Telegram Bot message sender — sends alerts through a shared bot instance.

This class does NOT create its own bot connection. The bot instance is either:
- Set externally by the InteractiveTelegramBot (unified single connection), or
- Created as fallback if interactive bot is disabled
"""

import asyncio

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("alerts")

MAX_MESSAGE_LENGTH = 4096
# Truncate Telegram messages BEFORE hitting the hard 4096 cap so the
# "[truncated]" marker itself fits within the limit when escaping turns
# an ampersand into ``&amp;`` (5x expansion) or similar.
SAFE_BODY_CHARS = 3800

# Phase 5 session-stability — explicit network timeouts on every
# outbound call so slow/flaky Telegram API windows can't block the worker
# for minutes at a time. The python-telegram-bot default is ~10 min.
SEND_READ_TIMEOUT = 15.0
SEND_WRITE_TIMEOUT = 15.0
SEND_CONNECT_TIMEOUT = 10.0

# Retry ladder for transient errors (TimedOut / NetworkError). Non-retryable
# errors (forbidden, blocked, chat_not_found) still short-circuit.
_RETRY_SLEEPS_S: tuple[float, ...] = (2.0, 5.0, 10.0)


class TelegramBot:
    """Telegram message sender that uses a shared bot instance.

    Args:
        settings: Application settings with bot token and chat ID.
    """

    def __init__(self, settings: Settings) -> None:
        self.token = settings.alerts.bot_token
        self.chat_id = settings.alerts.chat_id
        self.bot = None
        self.enabled = False
        self.total_sent = 0
        self.total_errors = 0

    async def connect(self) -> bool:
        """Connect to Telegram. Only creates own Bot if no instance was shared.

        Returns:
            True if connected successfully.
        """
        # If bot was already shared by the interactive bot, we're good
        if self.bot is not None:
            self.enabled = True
            return True

        if not self.token or not self.chat_id:
            log.warning("Telegram bot token or chat_id not set — alerts disabled")
            return False

        # Fallback: create own Bot instance (only when interactive bot is disabled)
        try:
            from telegram import Bot
            self.bot = Bot(token=self.token)
            me = await self.bot.get_me()
            self.enabled = True
            log.info("Telegram bot connected (standalone): @{username}", username=me.username)
            return True
        except Exception as e:
            log.warning("Telegram bot connection failed: {err}", err=str(e))
            self.enabled = False
            return False

    def set_bot(self, bot_instance) -> None:
        """Receive a shared bot instance from the interactive bot.

        This is the primary way the bot gets connected in the unified architecture.
        Called by InteractiveTelegramBot after Application.initialize().
        """
        self.bot = bot_instance
        self.enabled = True
        log.info("TelegramBot: using shared bot instance (unified connection)")

    async def send_message(self, text: str, parse_mode: str = "HTML", silent: bool = False) -> bool:
        """Send a message to the configured chat with retry/backoff.

        Phase 5 session-stability:
          * Explicit read/write/connect timeouts cap how long one call
            can block (was unlimited at python-telegram-bot default).
          * TimedOut / NetworkError retries with exponential backoff
            (2s → 5s → 10s, max 3 attempts).
          * Parse-mode fallback (escape to plain text) preserved from
            pre-fix behaviour.
          * On exhaustion emits ``TG_SEND_ABANDONED`` so operators can
            see which chat/body was dropped.

        Never raises exceptions — returns False on failure.
        """
        if not self.enabled or not self.bot:
            return False

        # Payload-length guard — truncate BEFORE hitting the Telegram
        # 4096-char cap. SAFE_BODY_CHARS leaves headroom so the
        # "[truncated]" marker always lands inside the limit.
        if len(text) > SAFE_BODY_CHARS:
            _orig_len = len(text)
            text = (
                text[: SAFE_BODY_CHARS - 60]
                + f"\n\n[dashboard truncated — original was {_orig_len} chars]"
            )

        # Lazy import to avoid hard coupling on python-telegram-bot at
        # module import time (aligned with `connect` which imports it lazily).
        try:
            from telegram.error import (
                TimedOut,
                NetworkError,
                Forbidden,
                BadRequest,
            )
        except ImportError:
            TimedOut = NetworkError = Forbidden = BadRequest = Exception  # type: ignore

        last_err: Exception | None = None
        for attempt, delay in enumerate([0.0, *_RETRY_SLEEPS_S]):
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    disable_notification=silent,
                    read_timeout=SEND_READ_TIMEOUT,
                    write_timeout=SEND_WRITE_TIMEOUT,
                    connect_timeout=SEND_CONNECT_TIMEOUT,
                )
                self.total_sent += 1
                if attempt > 0:
                    log.info(
                        f"TG_SEND_RETRY_OK | attempt={attempt} "
                        f"delay={delay:.1f}s | {ctx()}"
                    )
                return True
            except (TimedOut, NetworkError) as e:
                last_err = e
                log.warning(
                    f"TG_SEND_RETRY | attempt={attempt + 1}/{len(_RETRY_SLEEPS_S) + 1} "
                    f"err_type={type(e).__name__} err='{str(e)[:120]}' | {ctx()}"
                )
                continue
            except Forbidden as e:
                # Bot blocked by user / removed from chat — stop attempting.
                log.warning(
                    f"Telegram bot blocked/forbidden — disabling alerts "
                    f"err='{str(e)[:120]}'"
                )
                self.enabled = False
                self.total_errors += 1
                return False
            except BadRequest as e:
                # Typically an HTML/Markdown parse error — retry without
                # parse_mode once, then give up.
                err_str = str(e).lower()
                if "parse" in err_str or "can't" in err_str:
                    try:
                        await self.bot.send_message(
                            chat_id=self.chat_id,
                            text=text,
                            disable_notification=silent,
                            read_timeout=SEND_READ_TIMEOUT,
                            write_timeout=SEND_WRITE_TIMEOUT,
                            connect_timeout=SEND_CONNECT_TIMEOUT,
                        )
                        self.total_sent += 1
                        return True
                    except Exception as e2:
                        last_err = e2
                else:
                    last_err = e
                log.error(f"Telegram BadRequest: {str(e)[:160]}")
                break
            except Exception as e:
                last_err = e
                log.error(f"Telegram send error: {str(e)[:160]}")
                break

        log.warning(
            f"TG_SEND_ABANDONED | chat={self.chat_id} "
            f"err_type={type(last_err).__name__ if last_err else 'None'} "
            f"err='{str(last_err)[:120] if last_err else ''}' | {ctx()}"
        )
        self.total_errors += 1
        return False

    async def send_message_chunked(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a long message in multiple chunks."""
        if len(text) <= MAX_MESSAGE_LENGTH:
            return await self.send_message(text, parse_mode)

        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > MAX_MESSAGE_LENGTH - 50:
                chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)

        import asyncio
        all_ok = True
        for chunk in chunks:
            ok = await self.send_message(chunk, parse_mode)
            if not ok:
                all_ok = False
            await asyncio.sleep(0.5)
        return all_ok

    async def disconnect(self) -> None:
        """Clean up."""
        log.info(
            "Telegram bot disconnected (sent={s}, errors={e})",
            s=self.total_sent, e=self.total_errors,
        )

    def get_stats(self) -> dict:
        total = self.total_sent + self.total_errors
        rate = (self.total_sent / total * 100) if total > 0 else 0
        return {
            "enabled": self.enabled,
            "total_sent": self.total_sent,
            "total_errors": self.total_errors,
            "success_rate_pct": round(rate, 1),
        }
