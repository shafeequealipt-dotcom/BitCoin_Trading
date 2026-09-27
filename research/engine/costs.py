"""Cost models, in percent of notional, ROUND TRIP (entry + exit combined).

Measured directly from the live shadow-exchange ledger (see
scripts/research/forensic_ledger.py "COST STRUCTURE" section, 1,090 trades):
median fee 0.11%, median slippage 0.06% -- both fixed constants in the shadow
exchange's market-order model, not a function of size or liquidity.

MAKER assumes a resting limit order fills at the posted price with no
slippage, at Bybit's linear-perp maker fee (0.02% each side = 0.04% round
trip). This is the OPTIMISTIC case -- research/engine/backtest.py does not
yet model fill probability for resting orders (see Phase D in the plan), so a
strategy that only clears the bar at MAKER cost is not yet provably tradeable
-- it clears the bar CONDITIONAL ON a limit-order execution layer being built
and behaving as assumed.
"""
from __future__ import annotations

MARKET_ROUND_TRIP_PCT = 0.17
MAKER_ROUND_TRIP_PCT = 0.04

COST_MODELS = {"market": MARKET_ROUND_TRIP_PCT, "maker": MAKER_ROUND_TRIP_PCT}
