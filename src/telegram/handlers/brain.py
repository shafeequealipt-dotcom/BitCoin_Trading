"""Brain command handlers: /brain, /decisions, /leaderboard, /factory."""

from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.telegram.ui.formatters import format_timestamp

log = get_logger("telegram")


class BrainHandler:
    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.s = services

    async def status(self, update, context) -> None:
        cost_tracker = self.s.get("cost_tracker")
        if cost_tracker:
            stats = cost_tracker.get_daily_stats()
            msg = (
                f"\U0001f9e0 <b>BRAIN STATUS</b>\n\n"
                f"Calls today: {stats['calls_today']}\n"
                f"Cost today: ${stats['cost_today_usd']:.4f}\n"
                f"Budget remaining: ${stats['budget_remaining_usd']:.4f}\n"
                f"Budget used: {stats['budget_used_pct']:.0f}%\n"
                f"\n\U0001f550 {format_timestamp()}"
            )
            await update.message.reply_text(msg, parse_mode="HTML")
        else:
            await update.message.reply_text("Brain not available")

    async def decisions(self, update, context) -> None:
        # Phase conn-pool/p5-4 (2026-05-14) \u2014 redirect /decisions from the
        # deprecated ``brain_decisions`` table (0 rows; last written by
        # ``brain_v2.py:391`` which is not on the active strategist path)
        # to ``claude_decisions``, which the active strategist writes via
        # ``data_lake.write_claude_decision``. The schema is different:
        # claude_decisions has ``decision_type``, ``market_view``,
        # ``risk_level``, ``response_time_ms``, ``new_trades_count``,
        # ``position_actions_count`` (no ``action_taken``/``trigger``/
        # ``cost_usd`` \u2014 Claude Code CLI is $0).
        try:
            rows = await self.db.fetch_all(
                "SELECT decision_type, new_trades_count, position_actions_count, "
                "market_view, risk_level, response_time_ms, created_at "
                "FROM claude_decisions ORDER BY id DESC LIMIT 5",
            )
            if not rows:
                await update.message.reply_text("No recent Brain decisions.")
                return
            msg = "\U0001f9e0 <b>RECENT DECISIONS</b>\n\n"
            for r in rows:
                dtype = r.get("decision_type") or "?"
                trades = r.get("new_trades_count") or 0
                actions = r.get("position_actions_count") or 0
                view = (r.get("market_view") or "")[:32]
                risk = r.get("risk_level") or "?"
                rt = r.get("response_time_ms") or 0
                msg += (
                    f"\u2022 {dtype} | new_trades={trades} actions={actions} "
                    f"risk={risk} rt={rt}ms\n  view: {view}\n"
                )
            await update.message.reply_text(msg, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def leaderboard(self, update, context) -> None:
        registry = self.s.get("registry")
        if not registry:
            await update.message.reply_text("Strategy registry not available")
            return
        summary = registry.get_registry_summary()
        msg = f"\U0001f3c6 <b>STRATEGY LEADERBOARD</b>\n\n"
        msg += f"Total: {summary['total_strategies']} | Active: {summary['enabled']}\n\n"
        top = sorted(summary["strategies"], key=lambda x: x["profit_factor"], reverse=True)[:10]
        for i, s in enumerate(top, 1):
            emoji = "\U0001f947" if i == 1 else "\U0001f948" if i == 2 else "\U0001f949" if i == 3 else f"{i}."
            msg += f"{emoji} <b>{s['name']}</b>\n"
            msg += f"   WR={s['win_rate']:.0%} PF={s['profit_factor']:.1f} W={s['ensemble_weight']:.1f} trades={s['total_trades']}\n"
        msg += f"\n\U0001f550 {format_timestamp()}"
        await update.message.reply_text(msg, parse_mode="HTML")

    async def factory_status(self, update, context) -> None:
        try:
            patterns = await self.db.fetch_one("SELECT COUNT(*) as cnt FROM discovered_patterns WHERE is_valid = 1")
            strategies = await self.db.fetch_one("SELECT COUNT(*) as cnt FROM generated_strategies")
            msg = (
                f"\U0001f3ed <b>STRATEGY FACTORY</b>\n\n"
                f"Discovered patterns: {patterns['cnt'] if patterns else 0}\n"
                f"Generated strategies: {strategies['cnt'] if strategies else 0}\n"
                f"Discovery runs daily at 02:00 UTC"
            )
            await update.message.reply_text(msg, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Factory status: {e}")
