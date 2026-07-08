"""Analysis command handlers: /analyze, /signals, /regime, /fear, /news."""

from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.telegram.router import MessageRouter
from src.telegram.ui.cards import analysis_card
from src.telegram.ui.formatters import format_timestamp

log = get_logger("telegram")


class AnalysisHandler:
    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.s = services

    async def analyze(self, update, context) -> None:
        args = context.args if context.args else []
        symbol = MessageRouter._normalize_symbol(args[0]) if args else ""
        if not symbol:
            await update.message.reply_text("Usage: /analyze BTC")
            return
        await update.message.reply_text(f"\U0001f50d Analyzing {symbol}...")
        try:
            ticker = await self.s["market_service"].get_ticker(symbol)
            ta = await self.s["ta_engine"].analyze(symbol=symbol, timeframe="60", limit=200)
            card = analysis_card(symbol, ticker, ta)
            await update.message.reply_text(card, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Analysis failed: {e}")

    async def signals(self, update, context) -> None:
        """Show active trading signals from X-RAY structure cache and recent strategy hits."""
        lines = ["\U0001f4e1 <b>ACTIVE SIGNALS</b>\n"]

        # X-RAY ranked setups
        structure_cache = self.s.get("structure_cache")
        if structure_cache:
            try:
                setups = structure_cache.get_ranked_setups()
                if setups:
                    lines.append("<b>X-RAY Ranked Setups:</b>")
                    for i, setup in enumerate(setups[:8], 1):
                        symbol = getattr(setup, "symbol", "?")
                        score = getattr(setup, "setup_score", 0) or 0
                        direction = getattr(setup, "bias", None) or getattr(setup, "direction", "?")
                        confluence = getattr(setup, "confluence_quality", None) or ""
                        lines.append(
                            f"  {i}. <b>{symbol}</b> {direction} "
                            f"score={score:.0f} {confluence}"
                        )
                    lines.append("")
                else:
                    lines.append("No X-RAY setups currently ranked.\n")
            except Exception:
                lines.append("X-RAY data loading...\n")
        else:
            lines.append("X-RAY structure cache not available.\n")

        # Recent strategy signals from DB
        try:
            rows = await self.db.fetch_all(
                "SELECT symbol, direction, strategy_name, entry_score "
                "FROM trade_intelligence "
                "ORDER BY id DESC LIMIT 5",
            )
            if rows:
                lines.append("<b>Recent Strategy Entries:</b>")
                for r in rows:
                    score = float(r.get("entry_score") or 0)
                    lines.append(
                        f"  \u2022 {r['symbol']} {r['direction']} "
                        f"via {(r.get('strategy_name') or '?')[:25]} "
                        f"(score={score:.0f})"
                    )
        except Exception:
            pass

        lines.append(f"\n\U0001f550 {format_timestamp()}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def regime(self, update, context) -> None:
        detector = self.s.get("regime_detector")
        if detector:
            try:
                state = await detector.detect()
                cats = ", ".join(state.active_strategy_categories[:5])
                msg = (
                    f"\U0001f30d <b>MARKET REGIME</b>\n\n"
                    f"Regime: <b>{state.regime.value.upper()}</b>\n"
                    f"Confidence: {state.confidence:.0%}\n"
                    f"ADX: {state.adx:.1f} | Choppiness: {state.choppiness:.1f}\n"
                    f"Volume ratio: {state.volume_ratio:.2f}\n"
                    f"Active categories: {cats}\n"
                    f"\n\U0001f550 {format_timestamp()}"
                )
                await update.message.reply_text(msg, parse_mode="HTML")
            except Exception as e:
                await update.message.reply_text(f"Error: {e}")
        else:
            await update.message.reply_text("Regime detector not available")

    async def fear_greed(self, update, context) -> None:
        """Show current Fear & Greed Index from the fear_greed service or DB."""
        fg_client = self.s.get("fear_greed")
        if fg_client:
            try:
                data = await fg_client.fetch_current()
                value = data.value
                label = data.classification
                emoji = (
                    "\U0001f534" if value <= 25 else
                    "\U0001f7e0" if value <= 45 else
                    "\U0001f7e1" if value <= 55 else
                    "\U0001f7e2" if value <= 75 else
                    "\U0001f7e2\U0001f7e2"
                )
                msg = (
                    f"\U0001f631 <b>FEAR & GREED INDEX</b>\n\n"
                    f"{emoji} Value: <b>{value}</b> — {label}\n\n"
                )
                if value <= 25:
                    msg += "Market in Extreme Fear — potential buying opportunity.\n"
                elif value <= 45:
                    msg += "Market fearful — caution, but contrarian buys possible.\n"
                elif value <= 55:
                    msg += "Market neutral — no strong sentiment bias.\n"
                elif value <= 75:
                    msg += "Market greedy — momentum plays but watch for tops.\n"
                else:
                    msg += "Extreme Greed — high risk of reversal.\n"
                msg += f"\n\U0001f550 {format_timestamp()}"
                await update.message.reply_text(msg, parse_mode="HTML")
                return
            except Exception as e:
                log.warning("Fear & Greed live fetch failed: {err}", err=str(e))

        # Fallback: query DB for latest cached value
        try:
            row = await self.db.fetch_one(
                "SELECT value, classification, fetched_at FROM fear_greed_index "
                "ORDER BY fetched_at DESC LIMIT 1"
            )
            if row:
                value = int(row["value"])
                label = row["classification"]
                msg = (
                    f"\U0001f631 <b>FEAR & GREED INDEX</b>\n\n"
                    f"Value: <b>{value}</b> — {label}\n"
                    f"<i>(cached: {str(row.get('fetched_at', ''))[:16]})</i>\n"
                    f"\n\U0001f550 {format_timestamp()}"
                )
                await update.message.reply_text(msg, parse_mode="HTML")
                return
        except Exception:
            pass

        await update.message.reply_text(
            "Fear & Greed data not available. Service may still be initializing."
        )

    async def news(self, update, context) -> None:
        try:
            rows = await self.db.fetch_all(
                "SELECT headline, sentiment_score, published_at FROM news_articles "
                "ORDER BY published_at DESC LIMIT 5",
            )
            if not rows:
                await update.message.reply_text("No recent news.")
                return
            msg = "\U0001f4f0 <b>LATEST NEWS</b>\n\n"
            for r in rows:
                score = float(r.get("sentiment_score", 0))
                emoji = "\U0001f7e2" if score > 0.3 else "\U0001f534" if score < -0.3 else "\u26aa"
                msg += f"{emoji} {r['headline'][:80]}\n"
            await update.message.reply_text(msg, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def opportunities(self, update, context) -> None:
        """Show top trading opportunities from X-RAY setups and strategy scanner."""
        lines = ["\U0001f525 <b>TOP OPPORTUNITIES</b>\n"]

        # X-RAY top setups (highest quality)
        structure_cache = self.s.get("structure_cache")
        if structure_cache:
            try:
                top = structure_cache.get_top_setups(n=8)
                if top:
                    lines.append("<b>X-RAY Quality Setups:</b>")
                    for setup in top:
                        symbol = getattr(setup, "symbol", "?")
                        score = getattr(setup, "setup_score", 0) or 0
                        bias = getattr(setup, "bias", None) or getattr(setup, "direction", "—")
                        confl = getattr(setup, "confluence_quality", "") or ""
                        smc = getattr(setup, "smc_confluence", 0) or 0
                        # Grade
                        grade = "A+" if score >= 80 else "A" if score >= 65 else "B" if score >= 50 else "C"
                        lines.append(
                            f"  \U0001f3af <b>{symbol}</b> {bias} "
                            f"[{grade}] score={score:.0f} smc={smc:.0f} {confl}"
                        )
                    lines.append("")
                else:
                    lines.append("No X-RAY setups detected yet.\n")
            except Exception:
                lines.append("X-RAY scanning in progress...\n")

        # Strategy scanner output if available
        scanner = self.s.get("scanner")
        if scanner and hasattr(scanner, "get_latest_results"):
            try:
                results = scanner.get_latest_results()
                if results:
                    lines.append("<b>Strategy Scanner Hits:</b>")
                    for r in list(results)[:5]:
                        symbol = r.get("symbol", "?")
                        strat = r.get("strategy", "?")
                        signal = r.get("signal", "?")
                        lines.append(f"  \u2022 {symbol} {signal} via {strat}")
                    lines.append("")
            except Exception:
                pass

        # Registry summary
        registry = self.s.get("registry")
        if registry:
            try:
                summary = registry.get_registry_summary()
                lines.append(
                    f"\u2699\ufe0f {summary['total_strategies']} strategies active | "
                    f"{summary['enabled']} enabled"
                )
            except Exception:
                pass

        lines.append(f"\n\U0001f550 {format_timestamp()}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
