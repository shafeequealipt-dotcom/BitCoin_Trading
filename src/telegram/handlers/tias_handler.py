"""TIAS Phase 5 — Telegram dashboard commands for trade intelligence visibility.

Commands:
    /tias_last [N]     — Last N analyzed trades with DeepSeek verdict
    /tias_patterns     — Category breakdown (what's going wrong/right)
    /tias_symbols      — Per-symbol win rate and PnL intelligence
    /tias_cost         — API cost tracking and backfill status
"""

from __future__ import annotations

from src.core.logging import get_logger
from src.database.connection import DatabaseManager

log = get_logger("telegram")

_CATEGORY_ICONS: dict[str, str] = {
    "wrong_direction": "🔄",
    # weak_signal: TIAS analysis category (post-trade autopsy classification);
    # NOT the same string as the LiquiditySweep.signal field (which became
    # weak_long/weak_short directional in the XRAY phase-1 fix).
    "weak_signal": "⚠️",
    "tp_too_wide": "📏",
    "sl_too_tight": "🔒",
    "momentum_exhausted": "💨",
    "regime_mismatch": "🌊",
    "bad_timing": "⏰",
    "overleveraged": "📈",
    "good_trade_bad_luck": "🎲",
    "perfect_execution": "🎯",
    "right_trade": "✅",
    "undersized_winner": "📐",
}


class TIASHandler:
    """Telegram command handler for TIAS (Trade Intelligence Autopsy System).

    All commands query TradeIntelligenceRepo via the services dict and format
    the results as plain-text Telegram messages.

    Args:
        db: Active DatabaseManager instance.
        services: Dict of all system services (must contain 'tias_repo').
    """

    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.s = services

    # ─── /tias_last [N] ───────────────────────────────────────────────────────

    async def tias_last(self, update, context) -> None:
        """Show the last N analyzed trades with DeepSeek verdict.

        Usage: /tias_last [N]  (default N=3, max 10)
        """
        n = 3
        if context.args:
            try:
                n = min(int(context.args[0]), 10)
            except ValueError:
                pass

        tias_repo = self.s.get("tias_repo")
        if not tias_repo:
            await update.message.reply_text("TIAS not available.")
            return

        try:
            trades = await tias_repo.get_recent_analyses(limit=n)
        except Exception as e:
            log.error("TIAS tias_last query failed: {err}", err=str(e))
            await update.message.reply_text("Error fetching TIAS data.")
            return

        if not trades:
            # Fetch capture stats so user knows the system IS recording trades
            try:
                stats = await tias_repo.get_stats()
                total = stats.get("total", 0)
                pending = stats.get("pending", 0)
                wins = stats.get("wins", 0)
                losses = stats.get("losses", 0)
                msg = (
                    "📊 TIAS — No DeepSeek Analyses Yet\n\n"
                    f"Trades captured: {total} ({wins}W / {losses}L)\n"
                    f"Awaiting analysis: {pending}\n\n"
                    "DeepSeek analysis is disabled or not configured.\n"
                    "Enable via OPENROUTER_API_KEY + tias.enabled=true in config.\n"
                    "Use /tias_cost for full status · /tias_symbols for outcomes."
                )
            except Exception:
                msg = (
                    "No analyzed trades yet. DeepSeek analysis has not run.\n"
                    "Use /tias_cost to check capture status."
                )
            await update.message.reply_text(msg)
            return

        lines = [f"📊 TIAS — Last {len(trades)} Analyses\n"]

        for t in trades:
            icon = "🟢" if t.get("win") else "🔴"
            pnl_pct = t.get("pnl_pct") or 0.0
            pnl_usd = t.get("pnl_usd") or 0.0
            sign = "+" if pnl_pct >= 0 else ""
            conf = t.get("ds_confidence") or 0.0

            lines.append(
                f"{icon} {t.get('symbol', '?')} {t.get('direction', '?')} "
                f"{sign}{pnl_pct:.2f}% (${sign}{pnl_usd:.2f})\n"
                f"   Category: {t.get('ds_category') or '?'}\n"
                f"   Why: {t.get('ds_why_short') or 'N/A'}\n"
                f"   Should have: {t.get('should_done_short') or 'N/A'}\n"
                f"   Correct dir: {t.get('ds_correct_direction') or '?'} "
                f"(conf: {conf:.0%})\n"
            )

        await update.message.reply_text("\n".join(lines))

    # ─── /tias_patterns ───────────────────────────────────────────────────────

    async def tias_patterns(self, update, context) -> None:
        """Show TIAS category pattern breakdown — what's failing and why."""
        tias_repo = self.s.get("tias_repo")
        if not tias_repo:
            await update.message.reply_text("TIAS not available.")
            return

        try:
            categories = await tias_repo.get_category_breakdown()
            stats = await tias_repo.get_stats()
        except Exception as e:
            log.error("TIAS tias_patterns query failed: {err}", err=str(e))
            await update.message.reply_text("Error fetching TIAS patterns.")
            return

        if not categories:
            total = stats.get("total", 0)
            pending = stats.get("pending", 0)
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            await update.message.reply_text(
                f"📊 TIAS Patterns — No Analyses Yet\n\n"
                f"Trades captured: {total} ({wins}W / {losses}L)\n"
                f"Awaiting analysis: {pending}\n\n"
                "Category patterns appear after DeepSeek analyses trades.\n"
                "Enable via OPENROUTER_API_KEY + tias.enabled=true in config.\n"
                "Use /tias_symbols for raw trade outcomes."
            )
            return

        total = stats.get("total", 0)
        analyzed = stats.get("analyzed", 0)
        pending = stats.get("pending", 0)

        lines = [
            "📊 TIAS Pattern Analysis",
            f"Total: {total} trades | Analyzed: {analyzed} | Pending: {pending}\n",
            "Category Breakdown:",
        ]

        for cat in categories:
            cat_name = cat.get("ds_category") or "unknown"
            icon = _CATEGORY_ICONS.get(cat_name, "📌")
            win_pct = cat.get("win_pct") or 0
            avg_pnl = cat.get("avg_pnl") or 0.0
            count = cat.get("count") or 0
            sign = "+" if avg_pnl >= 0 else ""
            lines.append(
                f"  {icon} {cat_name}: {count} trades "
                f"({win_pct:.0f}% WR, avg {sign}{avg_pnl:.2f}%)"
            )

        await update.message.reply_text("\n".join(lines))

    # ─── /tias_symbols ────────────────────────────────────────────────────────

    async def tias_symbols(self, update, context) -> None:
        """Show per-symbol intelligence: win rate, PnL, pattern categories."""
        tias_repo = self.s.get("tias_repo")
        if not tias_repo:
            await update.message.reply_text("TIAS not available.")
            return

        try:
            symbols = await tias_repo.get_symbol_intelligence()
        except Exception as e:
            log.error("TIAS tias_symbols query failed: {err}", err=str(e))
            await update.message.reply_text("Error fetching TIAS symbol data.")
            return

        if not symbols:
            await update.message.reply_text("No trade data yet.")
            return

        lines = ["📊 TIAS Symbol Intelligence\n"]

        for s in symbols:
            trades = s.get("trades") or 0
            wins = s.get("wins") or 0
            losses = s.get("losses") or 0
            wr = (wins / trades * 100) if trades > 0 else 0.0
            total_pnl = s.get("total_pnl_usd") or 0.0
            symbol = s.get("symbol", "?")
            pnl_sign = "+" if total_pnl >= 0 else ""

            if wr >= 60:
                icon = "🟢"
                verdict = "STRONG"
            elif wr >= 40:
                icon = "🟡"
                verdict = "MIXED"
            else:
                icon = "🔴"
                verdict = "WEAK"

            lines.append(
                f"{icon} {symbol}: {trades}T {wins}W/{losses}L "
                f"({wr:.0f}% WR) ${pnl_sign}{total_pnl:.2f} — {verdict}"
            )
            categories = s.get("categories")
            if categories:
                lines.append(f"   Issues: {categories}")

        await update.message.reply_text("\n".join(lines))

    # ─── /tias_cost ───────────────────────────────────────────────────────────

    async def tias_cost(self, update, context) -> None:
        """Show TIAS DeepSeek API cost tracking and backfill status."""
        tias_repo = self.s.get("tias_repo")
        if not tias_repo:
            await update.message.reply_text("TIAS not available.")
            return

        try:
            stats = await tias_repo.get_stats()
        except Exception as e:
            log.error("TIAS tias_cost query failed: {err}", err=str(e))
            await update.message.reply_text("Error fetching TIAS cost data.")
            return

        analyzed = stats.get("analyzed", 0)
        total_cost = stats.get("total_cost") or 0.0
        avg_cost = total_cost / max(analyzed, 1)
        avg_ms = stats.get("avg_response_ms") or 0

        lines = [
            "💰 TIAS Cost Report\n",
            f"Total analyses: {analyzed}",
            f"Total cost: ${total_cost:.4f}",
            f"Avg per trade: ${avg_cost:.4f}",
            f"Avg response: {avg_ms:.0f}ms",
            f"\nPending: {stats.get('pending', 0)} unanalyzed",
            f"Failed: {stats.get('failed', 0)} gave up after 3 attempts",
            f"Total trades: {stats.get('total', 0)}",
        ]

        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        total = stats.get("total", 0)
        if total > 0:
            wr = wins / total * 100
            lines.append(f"\nOverall WR: {wr:.1f}% ({wins}W / {losses}L)")

        await update.message.reply_text("\n".join(lines))
