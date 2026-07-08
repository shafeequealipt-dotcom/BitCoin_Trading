"""Pre-Trade Risk Checker: analyzes risk before executing trades from Telegram."""

from src.core.logging import get_logger

log = get_logger("telegram")


class RiskChecker:
    """Performs pre-trade risk analysis for Telegram trade commands.

    Args:
        services: Dict of system services.
    """

    def __init__(self, services: dict) -> None:
        self.services = services

    async def check(self, symbol: str, side: str, amount: float, leverage: int) -> dict:
        """Run pre-trade risk checks.

        Returns dict with checks, warnings, and suggested_leverage.
        """
        checks: list[dict] = []
        warnings: list[str] = []
        all_passed = True

        # Check 1: Position size vs account
        try:
            account = await self.services["account_service"].get_wallet_balance()
            pct = (amount / account.total_equity * 100) if account.total_equity > 0 else 100
            if pct > 10:
                checks.append({"name": "Position size", "passed": False, "detail": f"{pct:.1f}% of equity (max 10%)"})
                all_passed = False
            else:
                checks.append({"name": "Position size", "passed": True, "detail": f"{pct:.1f}% of equity"})
        except Exception:
            checks.append({"name": "Position size", "passed": True, "detail": "Could not verify"})

        # Check 2: Existing positions
        try:
            positions = await self.services["position_service"].get_positions()
            same_symbol = [p for p in positions if p.symbol == symbol]
            if same_symbol:
                warnings.append(f"Already have a {symbol} position open!")
            if len(positions) >= 5:
                warnings.append(f"Already have {len(positions)} open positions (max 5)")
            checks.append({"name": "Position count", "passed": len(positions) < 5, "detail": f"{len(positions)} open"})
        except Exception:
            pass

        # Check 3: Leverage
        max_lev = 5
        if leverage > max_lev:
            checks.append({"name": "Leverage", "passed": False, "detail": f"{leverage}x exceeds max {max_lev}x"})
            all_passed = False
        else:
            checks.append({"name": "Leverage", "passed": True, "detail": f"{leverage}x"})

        # Get TA for context
        rsi = "N/A"
        signal = "N/A"
        confidence = "N/A"
        suggested_leverage = leverage

        try:
            ta = await self.services["ta_engine"].analyze(symbol=symbol, timeframe="60", limit=100)
            overall = ta.get("overall", {})
            rsi = f"{ta.get('momentum', {}).get('rsi_14', 0):.0f}"
            signal = overall.get("signal", "N/A")
            confidence = f"{overall.get('confidence', 0)*100:.0f}"

            # Suggest lower leverage in high volatility
            natr = ta.get("volatility", {}).get("natr_14", 1)
            if natr and natr > 2:
                suggested_leverage = min(leverage, 2)
                if suggested_leverage != leverage:
                    warnings.append(f"High volatility (NATR={natr:.1f}%) — consider lower leverage")
        except Exception:
            pass

        return {
            "checks": checks,
            "warnings": warnings,
            "all_passed": all_passed and not warnings,
            "rsi": rsi,
            "signal": signal,
            "confidence": confidence,
            "suggested_leverage": suggested_leverage,
        }
