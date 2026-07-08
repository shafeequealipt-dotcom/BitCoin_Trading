"""Chart Generator: text-based price chart rendering for Telegram."""

from src.core.logging import get_logger
from src.telegram.ui.charts import price_chart, sparkline

log = get_logger("telegram")


class ChartGenerator:
    """Generates text-based charts from OHLCV data."""

    def generate(self, candles: list, symbol: str = "", timeframe: str = "") -> str:
        if not candles:
            return "No chart data available"
        chart = price_chart(candles, width=24)
        header = f"\U0001f4c8 {symbol} {timeframe}" if symbol else "\U0001f4c8 Chart"
        return f"{header}\n{chart}"

    def mini_sparkline(self, values: list[float]) -> str:
        return sparkline(values, width=20)
