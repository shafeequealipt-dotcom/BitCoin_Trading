"""InstrumentInfo dataclass: trading pair rules and precision limits from Bybit."""

from dataclasses import dataclass
from typing import Any

from src.core.types import SerializableMixin


@dataclass
class InstrumentInfo(SerializableMixin):
    """Trading instrument specification from the exchange.

    Contains all rules for a trading pair: lot sizes, tick sizes,
    leverage limits, and minimum notional value.
    """

    symbol: str
    base_coin: str
    quote_coin: str
    status: str
    min_qty: float
    max_qty: float
    qty_step: float
    min_price: float
    max_price: float
    price_tick: float
    min_leverage: int
    max_leverage: int
    leverage_step: float
    min_notional: float

    @classmethod
    def from_bybit(cls, data: dict[str, Any]) -> "InstrumentInfo":
        """Parse Bybit instruments_info response into InstrumentInfo.

        Args:
            data: A single item from Bybit's get_instruments_info result list.

        Returns:
            Populated InstrumentInfo instance.
        """
        lot_filter = data.get("lotSizeFilter", {})
        price_filter = data.get("priceFilter", {})
        leverage_filter = data.get("leverageFilter", {})

        return cls(
            symbol=data.get("symbol", ""),
            base_coin=data.get("baseCoin", ""),
            quote_coin=data.get("quoteCoin", ""),
            status=data.get("status", ""),
            min_qty=float(lot_filter.get("minOrderQty", "0")),
            max_qty=float(lot_filter.get("maxOrderQty", "0")),
            qty_step=float(lot_filter.get("qtyStep", "0")),
            min_price=float(price_filter.get("minPrice", "0")),
            max_price=float(price_filter.get("maxPrice", "0")),
            price_tick=float(price_filter.get("tickSize", "0")),
            min_leverage=int(float(leverage_filter.get("minLeverage", "1"))),
            max_leverage=int(float(leverage_filter.get("maxLeverage", "1"))),
            leverage_step=float(leverage_filter.get("leverageStep", "0.01")),
            min_notional=float(lot_filter.get("minNotionalValue", "0")),
        )
