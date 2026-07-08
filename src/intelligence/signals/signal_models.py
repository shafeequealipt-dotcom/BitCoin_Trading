"""Signal models: thresholds, weights, and configuration for signal generation.

All threshold constants and signal configuration in one place.
"""

# Sentiment score -> signal type thresholds
SENTIMENT_THRESHOLDS: dict[str, float] = {
    "strong_buy": 0.5,
    "buy": 0.2,
    "neutral_upper": 0.2,
    "neutral_lower": -0.2,
    "sell": -0.2,
    "strong_sell": -0.5,
}

# Fear & Greed Index zones
FEAR_GREED_THRESHOLDS: dict[str, tuple[int, int]] = {
    "extreme_fear": (0, 20),
    "fear": (21, 40),
    "neutral": (41, 60),
    "greed": (61, 80),
    "extreme_greed": (81, 100),
}

# Funding rate thresholds (absolute value)
FUNDING_RATE_THRESHOLDS: dict[str, float] = {
    "extreme_positive": 0.01,    # Very high positive = crowded long
    "high_positive": 0.005,
    "normal_upper": 0.003,
    "normal_lower": -0.003,
    "high_negative": -0.005,
    "extreme_negative": -0.01,   # Very negative = crowded short
}

# Open interest change thresholds (percentage)
OI_CHANGE_THRESHOLDS: dict[str, float] = {
    "significant_increase": 10.0,
    "moderate_increase": 5.0,
    "moderate_decrease": -5.0,
    "significant_decrease": -10.0,
}

# Minimum confidence required for each signal type
CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "strong_buy": 0.6,
    "buy": 0.4,
    "neutral": 0.0,
    "sell": 0.4,
    "strong_sell": 0.6,
}

# How much each source contributes to overall signal
SOURCE_WEIGHTS: dict[str, float] = {
    "news_sentiment": 0.25,
    "reddit_sentiment": 0.20,
    "fear_greed": 0.20,
    "funding_rate": 0.15,
    "open_interest": 0.10,
    "momentum": 0.10,
}

# CoinGecko coin ID -> system symbol mapping
COINGECKO_SYMBOL_MAP: dict[str, str] = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "ripple": "XRPUSDT",
    "dogecoin": "DOGEUSDT",
}

# Reverse map: system symbol -> CoinGecko coin ID
SYMBOL_TO_COINGECKO: dict[str, str] = {v: k for k, v in COINGECKO_SYMBOL_MAP.items()}

# Crypto name/ticker -> system symbol mapping for text extraction
SYMBOL_EXTRACTION_MAP: dict[str, str] = {
    # Full names
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "ripple": "XRPUSDT",
    "dogecoin": "DOGEUSDT",
    "cardano": "ADAUSDT",
    "avalanche": "AVAXUSDT",
    "polkadot": "DOTUSDT",
    "chainlink": "LINKUSDT",
    "polygon": "MATICUSDT",
    # Tickers (uppercase handled by caller)
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
    "doge": "DOGEUSDT",
    "ada": "ADAUSDT",
    "avax": "AVAXUSDT",
    "dot": "DOTUSDT",
    "link": "LINKUSDT",
    "matic": "MATICUSDT",
}
