"""Manual universe-refresh Telegram button (Phase 4).

A command (/universe_refresh) that asks for confirmation, then runs the exact
same orchestration as a scheduled refresh, posting plain-prose status updates
at each stage. All messages are screen-reader friendly: plain sentences, no
emoji, no tables (the operator uses a screen reader). It shares the
orchestrator's single-flight state as the overlap guard so a manual press can
never stack on a scheduled or in-flight refresh.
"""

from __future__ import annotations

import asyncio

from src.core.logging import get_logger

log = get_logger(__name__)


class UniverseHandler:
    """Telegram handler for the on-demand universe refresh."""

    def __init__(self, db, services: dict) -> None:
        self.db = db
        self.s = services
        # Retain the background refresh task so it is not garbage-collected
        # mid-warm-up (asyncio only holds a weak reference to bare tasks).
        self._task = None

    def _orch(self):
        return self.s.get("universe_refresh")

    def _state(self):
        return self.s.get("universe_refresh_state")

    async def refresh_prompt(self, update, context) -> None:
        """/universe_refresh — confirm before running a manual refresh."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        orch = self._orch()
        if orch is None:
            await update.message.reply_text("Universe refresh is not available right now.")
            return
        p = orch.settings.universe.refresh
        if not p.enabled:
            await update.message.reply_text(
                "The universe refresh feature is currently disabled. Enable it in "
                "the configuration before running a manual refresh."
            )
            return
        state = self._state()
        if state is not None and state.is_running():
            await update.message.reply_text(
                "A universe refresh is already in progress. Please wait for it to finish."
            )
            return

        size = len(orch.settings.universe.watch_list)
        msg = (
            "Manual universe refresh. This rebuilds the trading universe "
            f"(currently {size} coins) around coins that are moving now. It pauses "
            f"finding new trades for the warm-up, up to about {p.warmup_max_minutes} "
            "minutes; every open position keeps full management throughout. "
            "Confirm to proceed, or cancel."
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Confirm refresh", callback_data="universe_refresh_confirm"),
            InlineKeyboardButton("Cancel", callback_data="universe_refresh_cancel"),
        ]])
        await update.message.reply_text(msg, reply_markup=kb)

    async def refresh_cancel(self, update, context, params=None) -> None:
        await update.callback_query.edit_message_text(
            "Universe refresh cancelled. Nothing changed."
        )

    async def refresh_confirm(self, update, context, params=None) -> None:
        q = update.callback_query
        orch = self._orch()
        if orch is None:
            await q.edit_message_text("Universe refresh is not available right now.")
            return
        if not orch.settings.universe.refresh.enabled:
            await q.edit_message_text(
                "The universe refresh feature is disabled. Nothing changed."
            )
            return
        state = self._state()
        if state is not None and state.is_running():
            await q.edit_message_text(
                "A universe refresh is already in progress. Nothing started."
            )
            return

        alert = self.s.get("alert_manager")

        async def notify(stage: str, message: str) -> None:
            if alert is not None and hasattr(alert, "send_custom"):
                try:
                    await alert.send_custom(message)
                except Exception:
                    pass

        await q.edit_message_text(
            "Universe refresh starting. You will get status updates here as it "
            "selects the new coins, warms them up, and resumes trading."
        )
        # Run in the background: the warm-up can take minutes, so the callback
        # must return promptly. Status flows through the notify callback. Retain
        # the task reference (+ clear on done) so it cannot be GC'd mid-run.
        self._task = asyncio.create_task(self._run(orch, notify))
        self._task.add_done_callback(lambda t: setattr(self, "_task", None))

    async def _run(self, orch, notify) -> None:
        try:
            result = await orch.run_refresh("manual_telegram", notify=notify)
            if result.get("status") == "already_running":
                await notify("overlap",
                             "A refresh was already running, so this manual one was skipped.")
        except Exception as e:
            log.error(f"MANUAL_UNIVERSE_REFRESH_FAIL | err='{str(e)[:150]}'")
            try:
                await notify("error", f"Universe refresh failed: {str(e)[:150]}. Trading resumed.")
            except Exception:
                pass
