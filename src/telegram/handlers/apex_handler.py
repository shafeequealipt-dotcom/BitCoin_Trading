"""APEX Phase 3 — Telegram dashboard commands for optimization visibility.

Commands:
    /apex_status  — optimizer stats + APEX vs non-APEX win rate comparison
    /apex_last [N] — last N APEX-optimized trades with before/after
    /apex_flips [N] — direction flip history with outcomes
"""

from __future__ import annotations

from src.core.logging import get_logger
from src.database.connection import DatabaseManager

log = get_logger("telegram")


class APEXHandler:
    """Telegram command handler for APEX optimizer visibility.

    Args:
        db: Active DatabaseManager instance.
        services: Dict of all system services.
    """

    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.s = services

    async def apex_status(self, update, context) -> None:
        """Show APEX optimizer status and cumulative stats."""
        optimizer = self.s.get("apex_optimizer")
        if not optimizer:
            await update.message.reply_text("APEX optimizer not active.")
            return

        stats = optimizer.get_stats()

        # Query DB for APEX trade outcome comparison
        apex_db = {}
        try:
            row = await self.db.fetch_one("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN apex_optimized = 1 THEN 1 ELSE 0 END) AS optimized,
                    SUM(CASE WHEN apex_flipped = 1 THEN 1 ELSE 0 END) AS flipped,
                    SUM(CASE WHEN apex_optimized = 1 AND win = 1 THEN 1 ELSE 0 END) AS apex_wins,
                    SUM(CASE WHEN apex_optimized = 1 AND win = 0 THEN 1 ELSE 0 END) AS apex_losses,
                    SUM(CASE WHEN apex_flipped = 1 AND win = 1 THEN 1 ELSE 0 END) AS flip_wins,
                    SUM(CASE WHEN (apex_optimized IS NULL OR apex_optimized = 0)
                        AND win = 1 THEN 1 ELSE 0 END) AS nonapex_wins,
                    SUM(CASE WHEN (apex_optimized IS NULL OR apex_optimized = 0)
                        AND win = 0 THEN 1 ELSE 0 END) AS nonapex_losses,
                    ROUND(AVG(CASE WHEN apex_optimized = 1 THEN pnl_pct END), 3) AS apex_avg_pnl,
                    ROUND(AVG(CASE WHEN (apex_optimized IS NULL OR apex_optimized = 0)
                        THEN pnl_pct END), 3) AS nonapex_avg_pnl,
                    ROUND(SUM(COALESCE(apex_cost_usd, 0)), 4) AS total_cost
                FROM trade_intelligence
            """)
            if row:
                apex_db = dict(row)
        except Exception:
            pass

        lines = ["--- APEX Optimizer Status ---\n"]
        lines.append(
            f"Session: {stats.get('optimized', 0)} optimized | "
            f"{stats.get('fallbacks', 0)} fallbacks"
        )
        lines.append(
            f"Flips: {stats.get('flips', 0)} "
            f"({stats.get('flip_rate', 0):.0%})"
        )
        lines.append(f"Avg latency: {stats.get('avg_time_ms', 0)}ms")

        if apex_db:
            apex_w = apex_db.get("apex_wins") or 0
            apex_l = apex_db.get("apex_losses") or 0
            apex_total = apex_w + apex_l
            apex_wr = (apex_w / apex_total * 100) if apex_total > 0 else 0

            nonapex_w = apex_db.get("nonapex_wins") or 0
            nonapex_l = apex_db.get("nonapex_losses") or 0
            nonapex_total = nonapex_w + nonapex_l
            nonapex_wr = (nonapex_w / nonapex_total * 100) if nonapex_total > 0 else 0

            flip_w = apex_db.get("flip_wins") or 0
            flip_total = apex_db.get("flipped") or 0
            flip_wr = (flip_w / flip_total * 100) if flip_total > 0 else 0

            lines.append(f"\n--- Historical (DB) ---")
            lines.append(
                f"APEX trades: {apex_total} "
                f"({apex_wr:.0f}% WR, avg {apex_db.get('apex_avg_pnl') or 0:.3f}%)"
            )
            lines.append(
                f"Non-APEX: {nonapex_total} "
                f"({nonapex_wr:.0f}% WR, avg {apex_db.get('nonapex_avg_pnl') or 0:.3f}%)"
            )
            if flip_total > 0:
                lines.append(f"Flips: {flip_total} ({flip_wr:.0f}% WR)")
            lines.append(
                f"Total DeepSeek cost: ${apex_db.get('total_cost') or 0:.4f}"
            )

        qwen = stats.get("qwen_stats", {})
        if qwen:
            lines.append(
                f"\nDeepSeek API: {qwen.get('calls', 0)} calls, "
                f"${qwen.get('cost', 0):.4f} total"
            )

        await update.message.reply_text("\n".join(lines))

    async def apex_last(self, update, context) -> None:
        """Show last N APEX-optimized trades with before/after comparison."""
        n = 5
        if context.args:
            try:
                n = min(int(context.args[0]), 10)
            except ValueError:
                pass

        try:
            trades = await self.db.fetch_all("""
                SELECT symbol, direction, pnl_pct, pnl_usd, win,
                       apex_original_direction, apex_confidence, apex_tp_mode,
                       substr(apex_reasoning, 1, 100) AS reason,
                       apex_response_ms, gate_adjustments, trade_closed_at
                FROM trade_intelligence
                WHERE apex_optimized = 1
                ORDER BY id DESC LIMIT ?
            """, (n,))
        except Exception as e:
            await update.message.reply_text(f"Error: {str(e)[:100]}")
            return

        if not trades:
            await update.message.reply_text("No APEX-optimized trades yet.")
            return

        lines = [f"--- Last {len(trades)} APEX Trades ---\n"]
        for t in trades:
            icon = "W" if t.get("win") else "L"
            pnl = t.get("pnl_pct") or 0
            orig_dir = t.get("apex_original_direction") or ""
            final_dir = t.get("direction") or "?"
            flip_tag = " [FLIP]" if orig_dir and orig_dir != final_dir else ""
            conf = t.get("apex_confidence") or 0
            gate = t.get("gate_adjustments") or ""

            lines.append(
                f"[{icon}] {t.get('symbol', '?')} {final_dir}{flip_tag} "
                f"{pnl:+.2f}% (${(t.get('pnl_usd') or 0):+.2f})"
            )
            lines.append(
                f"   conf={conf:.0%} mode={t.get('apex_tp_mode', '?')} "
                f"ms={t.get('apex_response_ms', 0)}"
            )
            if gate:
                lines.append(f"   gate: {gate}")
            reason = t.get("reason") or ""
            if reason:
                lines.append(f"   why: {reason}")
            lines.append("")

        await update.message.reply_text("\n".join(lines))

    async def apex_flips(self, update, context) -> None:
        """Show direction flip history with outcomes."""
        n = 10
        if context.args:
            try:
                n = min(int(context.args[0]), 15)
            except ValueError:
                pass

        try:
            flips = await self.db.fetch_all("""
                SELECT symbol, direction, apex_original_direction,
                       pnl_pct, pnl_usd, win, apex_confidence,
                       substr(apex_reasoning, 1, 120) AS reason,
                       trade_closed_at
                FROM trade_intelligence
                WHERE apex_flipped = 1
                ORDER BY id DESC LIMIT ?
            """, (n,))
        except Exception as e:
            await update.message.reply_text(f"Error: {str(e)[:100]}")
            return

        if not flips:
            await update.message.reply_text("No direction flips recorded yet.")
            return

        wins = sum(1 for f in flips if f.get("win"))
        losses = len(flips) - wins
        lines = [f"--- APEX Direction Flips ({wins}W/{losses}L) ---\n"]

        for f in flips:
            icon = "W" if f.get("win") else "L"
            pnl = f.get("pnl_pct") or 0
            orig = f.get("apex_original_direction") or "?"
            final = f.get("direction") or "?"
            conf = f.get("apex_confidence") or 0

            lines.append(
                f"[{icon}] {f.get('symbol', '?')}: {orig} -> {final} "
                f"{pnl:+.2f}% conf={conf:.0%}"
            )
            reason = f.get("reason") or ""
            if reason:
                lines.append(f"   {reason}")
            lines.append("")

        await update.message.reply_text("\n".join(lines))
