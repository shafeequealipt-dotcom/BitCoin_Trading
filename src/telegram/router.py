"""Message Router: classifies incoming messages into actionable intents."""

import re

from src.core.logging import get_logger
from src.telegram.models.telegram_types import ConversationState

log = get_logger("telegram")

SYMBOL_MAP = {
    "btc": "BTCUSDT", "bitcoin": "BTCUSDT",
    "eth": "ETHUSDT", "ethereum": "ETHUSDT",
    "sol": "SOLUSDT", "solana": "SOLUSDT",
    "xrp": "XRPUSDT", "ripple": "XRPUSDT",
    "doge": "DOGEUSDT", "dogecoin": "DOGEUSDT",
    "ada": "ADAUSDT", "cardano": "ADAUSDT",
    "avax": "AVAXUSDT", "avalanche": "AVAXUSDT",
    "link": "LINKUSDT", "chainlink": "LINKUSDT",
    "dot": "DOTUSDT", "polkadot": "DOTUSDT",
    "matic": "MATICUSDT", "polygon": "MATICUSDT",
}

# Words that look like symbols but aren't — ignore these after buy/sell
NOISE_WORDS = {
    "it", "the", "my", "all", "some", "this", "that", "now", "please",
    "position", "positions", "trade", "order", "asap", "immediately",
    "quick", "fast", "half", "full", "everything",
}

TRADE_PATTERNS = [
    re.compile(r"(?P<action>buy|sell|long|short)\s+(?:the\s+|my\s+|some\s+)?(?P<symbol>[a-zA-Z]{2,10})\s*(?P<amount>\d+\.?\d*)?\s*(?P<leverage>\d+x)?", re.I),
    re.compile(r"(?P<action>close)\s+(?:the\s+|my\s+)?(?P<symbol>[a-zA-Z]{2,10}|all)", re.I),
    re.compile(r"set\s+(?P<param>sl|tp)\s+(?P<symbol>[a-zA-Z]{2,10})\s+(?P<value>\d+\.?\d*)", re.I),
]

EMERGENCY_PATTERNS = [
    re.compile(p, re.I) for p in
    [r"emergency", r"911", r"close everything", r"kill", r"stop all", r"panic", r"close all now"]
]

QUICK_QUERIES = {
    "portfolio": ["portfolio", "how am i doing", "my portfolio", "performance"],
    "positions": ["positions", "open trades", "my trades", "what am i holding"],
    "pnl": ["pnl", "profit", "loss", "how much", "money today"],
    "balance": ["balance", "equity", "available", "how much money"],
    "fear": ["fear", "greed", "fear and greed", "f&g"],
    "status": ["status", "system", "health", "running"],
}


class MessageRouter:
    """Classifies free-form text messages into actionable intents."""

    def classify(self, text: str, conv_state: ConversationState | None = None) -> dict:
        """Classify message into intent type.

        Returns dict with type (trade_command, quick_query, emergency, ai_question)
        and parsed parameters.
        """
        text_lower = text.lower().strip()

        # Emergency (highest priority)
        for pattern in EMERGENCY_PATTERNS:
            if pattern.search(text_lower):
                return {"type": "emergency"}

        # Trade commands
        for pattern in TRADE_PATTERNS:
            match = pattern.search(text_lower)
            if match:
                groups = {k: v for k, v in match.groupdict().items() if v}
                if "symbol" in groups:
                    normalized = self._normalize_symbol(groups["symbol"])
                    if normalized:
                        groups["symbol"] = normalized
                    else:
                        # "sell it", "sell the" etc — symbol is a noise word
                        # Check conversation context for last discussed symbol
                        if conv_state and conv_state.last_symbol:
                            groups["symbol"] = conv_state.last_symbol
                        else:
                            groups["symbol"] = ""  # Handler will ask which coin
                return {"type": "trade_command", **groups}

        # Quick queries
        for query_type, keywords in QUICK_QUERIES.items():
            if any(kw in text_lower for kw in keywords):
                return {"type": "quick_query", "query": query_type}

        # Extract symbol for context
        symbol = self._extract_symbol(text)
        if not symbol and conv_state and conv_state.last_symbol:
            symbol = conv_state.last_symbol

        return {"type": "ai_question", "symbol": symbol, "text": text}

    @staticmethod
    def _extract_symbol(text: str) -> str | None:
        """Extract crypto symbol from text."""
        text_lower = text.lower()
        for name, symbol in SYMBOL_MAP.items():
            if name in text_lower.split():
                return symbol
        match = re.search(r'([A-Z]{2,10}USDT)', text.upper())
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _normalize_symbol(raw: str) -> str:
        """Normalize a symbol string to Bybit format. Returns empty if not a valid symbol."""
        raw_lower = raw.lower().strip()

        # Reject noise words that aren't crypto symbols
        if raw_lower in NOISE_WORDS:
            return ""

        # Known symbol names
        if raw_lower in SYMBOL_MAP:
            return SYMBOL_MAP[raw_lower]

        # Already a USDT pair
        raw_upper = raw.upper()
        if raw_upper.endswith("USDT") and len(raw_upper) > 4:
            return raw_upper

        # Short ticker (3-5 chars) → append USDT
        if 2 <= len(raw_lower) <= 5 and raw_lower.isalpha():
            return raw_upper + "USDT"

        return ""
