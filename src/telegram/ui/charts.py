"""Text-based chart rendering for Telegram messages."""


def sparkline(values: list[float], width: int = 20) -> str:
    """Render a text sparkline from a list of values."""
    if not values:
        return ""
    blocks = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    # Sample to fit width
    step = max(1, len(values) // width)
    sampled = values[::step][:width]
    return "".join(blocks[min(int((v - mn) / rng * 7) + 1, 8)] for v in sampled)


def price_chart(candles: list, width: int = 24) -> str:
    """Render a simple text bar chart of recent closes."""
    if not candles:
        return "No data"
    closes = [c.close for c in candles[-width:]]
    line = sparkline(closes, width)
    mn, mx = min(closes), max(closes)
    return f"{line}\nL: ${mn:,.0f}  H: ${mx:,.0f}"


def mini_bar(label: str, value: float, max_val: float, width: int = 10) -> str:
    """Render a labeled progress bar."""
    filled = int((value / max_val) * width) if max_val > 0 else 0
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    return f"{label}: {bar} {value:.1f}"
