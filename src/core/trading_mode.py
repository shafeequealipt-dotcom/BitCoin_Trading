"""Trading Mode — Shadow / Testnet / Mainnet configuration.

Controls Claude's prompt-text framing (mode header sent to the brain),
SL/TP sanity thresholds, risk parameters, and Telegram alert indicators.

Three semantic states:
  - SHADOW  — orders route to local Shadow virtual exchange; market data
              from real Bybit MAINNET feed. Paper trading on real prices.
              Driven by Transformer._current_mode == "shadow".
  - TESTNET — orders route to Bybit TESTNET; market data from Bybit
              TESTNET feed (synthetic prices). Driven by
              ``[bybit].testnet=true`` while Transformer is in bybit mode.
  - MAINNET — orders route to Bybit MAINNET; real money. Driven by
              ``[bybit].testnet=false`` while Transformer is in bybit mode.

The mode is derived from the Transformer state first, with bybit.testnet
as a fallback for the testnet-vs-mainnet distinction. ``refresh()`` is
called after every Transformer.switch_to() so prompt framing follows
order routing in lockstep.

Persisted to database. ``set_mode()`` allows explicit overrides; the
next ``refresh()`` will re-derive from Transformer state.
"""

import enum
import json
import time
from dataclasses import dataclass
from typing import Any

from src.core.logging import get_logger

log = get_logger("trading_mode")


class TradingModeType(enum.Enum):
    TESTNET = "testnet"
    MAINNET = "mainnet"
    SHADOW = "shadow"


@dataclass
class TradingMode:
    """Current trading mode with all parameters."""

    mode: TradingModeType = TradingModeType.TESTNET
    sl_sanity_pct: float = 10.0
    tp_sanity_pct: float = 10.0
    sl_fallback_pct: float = 2.5
    headspace_pct: float = 1.5
    max_trade_pct: float = 25.0
    indicator: str = "Y"
    label: str = "[TESTNET]"
    changed_at: float = 0.0

    @classmethod
    def testnet(cls) -> "TradingMode":
        return cls(
            mode=TradingModeType.TESTNET,
            sl_sanity_pct=10.0, tp_sanity_pct=10.0,
            sl_fallback_pct=2.5, headspace_pct=1.5,
            max_trade_pct=25.0, indicator="Y", label="[TESTNET]",
            changed_at=time.time(),
        )

    @classmethod
    def mainnet(cls) -> "TradingMode":
        return cls(
            mode=TradingModeType.MAINNET,
            sl_sanity_pct=5.0, tp_sanity_pct=5.0,
            sl_fallback_pct=2.0, headspace_pct=1.0,
            max_trade_pct=10.0, indicator="R", label="[MAINNET]",
            changed_at=time.time(),
        )

    @classmethod
    def shadow(cls) -> "TradingMode":
        """Shadow mode — paper trading on real Bybit market data.

        Risk parameters mirror MAINNET (real prices, real volatility) but
        the indicator + label communicate the virtual-capital reality so
        Telegram alerts and dashboards do not falsely imply real money.
        """
        return cls(
            mode=TradingModeType.SHADOW,
            sl_sanity_pct=5.0, tp_sanity_pct=5.0,
            sl_fallback_pct=2.0, headspace_pct=1.0,
            max_trade_pct=10.0, indicator="S", label="[SHADOW]",
            changed_at=time.time(),
        )

    @property
    def is_testnet(self) -> bool:
        return self.mode == TradingModeType.TESTNET

    @property
    def is_mainnet(self) -> bool:
        return self.mode == TradingModeType.MAINNET

    @property
    def is_shadow(self) -> bool:
        return self.mode == TradingModeType.SHADOW

    def get_claude_mode_instruction(self) -> str:
        """Return the MODE header text injected into the Stage 2 prompt.

        Three variants matching ``TradingModeType``:
          SHADOW  — paper-trading framing on real Bybit market data with
                    opportunity-exploit philosophy. Capital allocation
                    rules still apply (FUND RULES section) but defensive
                    real-money caution language is removed.
          TESTNET — Bybit testnet framing warning Claude that prices are
                    synthetic and divergent from training-data knowledge.
          MAINNET — real capital framing. Caution language preserved.
        """
        if self.is_shadow:
            return (
                "MODE: SHADOW (paper trading on real Bybit market data)\n"
                "You are validating trading strategies with virtual capital.\n"
                "Real Bybit MAINNET prices, indicators, and order book — only execution is virtual.\n"
                "Aim: characterize each coin's situation and exploit the best opportunities each cycle.\n"
                "Missing genuine setups is as costly as taking bad ones.\n"
                "FUND RULES below cap sizing — discipline applies; defensive caution does not.\n"
            )
        if self.is_testnet:
            return (
                "MODE: TESTNET (paper trading)\n"
                "CRITICAL: You are on TESTNET. Prices are SYNTHETIC and DIFFERENT from real markets.\n"
                "BTC testnet might be $340,000 while real BTC is $87,000.\n"
                "Use ONLY the prices, RSI, MACD, and indicator values I provide below.\n"
                "Do NOT cross-reference with your training data or real market knowledge.\n"
                "Your training data prices are COMPLETELY IRRELEVANT here.\n"
                "Calculate ALL SL/TP values from the prices I give you, not from memory.\n"
            )
        return (
            "MODE: MAINNET (real money)\n"
            "You are trading with REAL capital. Maximum caution required.\n"
            "Use the data I provide AND your own market knowledge.\n"
            "If any data looks suspicious, FLAG IT and do NOT trade.\n"
            "Double-check everything. This is real capital at risk.\n"
        )

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "sl_sanity_pct": self.sl_sanity_pct,
            "tp_sanity_pct": self.tp_sanity_pct,
            "sl_fallback_pct": self.sl_fallback_pct,
            "headspace_pct": self.headspace_pct,
            "max_trade_pct": self.max_trade_pct,
            "indicator": self.indicator,
            "changed_at": self.changed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TradingMode":
        mode_str = data.get("mode")
        if mode_str == "mainnet":
            obj = cls.mainnet()
        elif mode_str == "shadow":
            obj = cls.shadow()
        else:
            obj = cls.testnet()
        obj.sl_sanity_pct = data.get("sl_sanity_pct", obj.sl_sanity_pct)
        obj.tp_sanity_pct = data.get("tp_sanity_pct", obj.tp_sanity_pct)
        obj.sl_fallback_pct = data.get("sl_fallback_pct", obj.sl_fallback_pct)
        obj.headspace_pct = data.get("headspace_pct", obj.headspace_pct)
        obj.max_trade_pct = data.get("max_trade_pct", obj.max_trade_pct)
        obj.indicator = data.get("indicator", obj.indicator)
        obj.changed_at = data.get("changed_at", 0.0)
        return obj


class TradingModeManager:
    """Manages trading mode persistence and switching.

    The prompt-text mode is derived from the Transformer (order routing
    state machine) first, with ``settings.bybit.testnet`` as a fallback
    only when Transformer is in bybit mode. ``refresh()`` is wired into
    the Transformer's ``switch_to`` callback so prompt framing follows
    routing in lockstep without a service restart.

    Args:
        db: DatabaseManager — used for persisting mode across restarts.
        settings: project Settings — bybit.testnet is the testnet/mainnet
            tiebreaker when Transformer is in bybit mode.
        transformer: optional Transformer reference. If provided at
            construction, ``_derive_mode_from_state`` consults it first;
            if omitted, ``set_transformer`` can wire it later (used in
            the workers/manager bootstrap order).
    """

    def __init__(
        self,
        db,
        settings: Any = None,
        transformer: Any = None,
    ) -> None:
        self.db = db
        self._settings = settings
        self._transformer = transformer
        self._mode = self._derive_mode_from_state()

    def set_transformer(self, transformer: Any) -> None:
        """Late-wire the Transformer reference and re-derive mode.

        Used in the workers/manager bootstrap where the Transformer is
        constructed before TradingModeManager. After wiring, the manager
        immediately re-derives so the prompt mode reflects the current
        routing state from cycle one.
        """
        self._transformer = transformer
        self.refresh(persist=False)

    def _derive_mode_from_state(self) -> "TradingMode":
        """Return the TradingMode implied by current routing/config state.

        Resolution order:
          1) Transformer.is_shadow == True  → SHADOW
          2) settings.bybit.testnet == True → TESTNET
          3) otherwise                       → MAINNET

        Defaults to TESTNET when neither transformer nor settings are
        available (pre-init fallback).
        """
        try:
            if self._transformer is not None and getattr(
                self._transformer, "is_shadow", False
            ):
                return TradingMode.shadow()
        except Exception:
            # Defensive — transformer access must not break cold start.
            pass
        if self._settings is not None and hasattr(self._settings, "bybit"):
            if not self._settings.bybit.testnet:
                return TradingMode.mainnet()
        return TradingMode.testnet()

    def refresh(self, persist: bool = True) -> None:
        """Re-derive the prompt-text mode from current Transformer state.

        Called by Transformer.switch_to() after a successful flip. Logs
        the transition when the mode actually changed and (by default)
        persists to the database so a service restart picks up the new
        mode.
        """
        prev = self._mode.mode
        self._mode = self._derive_mode_from_state()
        new = self._mode.mode
        if prev == new:
            return
        log.info(
            f"MODE_TRANSITION | from={prev.value} to={new.value} "
            f"trigger=transformer_refresh"
        )
        if persist:
            # Best-effort persistence — failure here is non-fatal.
            try:
                import asyncio as _asyncio
                # ``refresh`` is sync to keep the transformer's switch_to
                # call site simple; schedule the DB write on the loop.
                loop = _asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._persist_mode())
            except Exception as e:
                log.warning(f"Mode persist scheduling failed: {e}")

    async def _persist_mode(self) -> None:
        try:
            await self.db.execute(
                "INSERT OR REPLACE INTO fund_manager_state (key, value) VALUES ('trading_mode', ?)",
                (json.dumps(self._mode.to_dict()),),
            )
        except Exception as e:
            log.error("Failed to persist trading mode: {err}", err=str(e))

    async def initialize(self) -> None:
        """Load any explicit-override mode from DB.

        On a clean cold-start the DB row is absent and the constructor's
        derive-from-state already populated ``_mode`` correctly. When the
        DB carries an explicit override (e.g. operator set TESTNET via
        Telegram while running on bybit), restore it — the next
        ``refresh()`` after a transformer switch will re-derive from
        current routing.
        """
        try:
            row = await self.db.fetch_one(
                "SELECT value FROM fund_manager_state WHERE key = 'trading_mode'"
            )
            if row and row["value"]:
                data = json.loads(row["value"])
                # Only honor a persisted SHADOW value when the transformer
                # is also in shadow — otherwise the saved mode is stale
                # (e.g. transformer was switched while service was down).
                # Derive-from-state already handled the live truth, so
                # fall back to it whenever the persisted SHADOW state is
                # incoherent with current Transformer routing.
                persisted = TradingMode.from_dict(data)
                if persisted.is_shadow and not (
                    self._transformer is not None
                    and getattr(self._transformer, "is_shadow", False)
                ):
                    log.info(
                        "Persisted SHADOW mode discarded — transformer is on "
                        "bybit; deriving from current state."
                    )
                else:
                    self._mode = persisted
                    log.info(
                        "Trading mode loaded: {mode}", mode=self._mode.mode.value
                    )
        except Exception as e:
            log.warning("Failed to load trading mode: {err}", err=str(e))

    @property
    def mode(self) -> TradingMode:
        return self._mode

    async def set_mode(self, mode_type: TradingModeType) -> None:
        """Force a specific mode (overrides derive-from-state until next refresh).

        Preserves the legacy Telegram dashboard toggle which flips
        TESTNET/MAINNET while the transformer is in bybit mode. Calling
        this with SHADOW is supported but the next Transformer switch
        will re-derive based on routing.
        """
        if mode_type == TradingModeType.MAINNET:
            self._mode = TradingMode.mainnet()
        elif mode_type == TradingModeType.SHADOW:
            self._mode = TradingMode.shadow()
        else:
            self._mode = TradingMode.testnet()
        try:
            await self.db.execute(
                "INSERT OR REPLACE INTO fund_manager_state (key, value) VALUES ('trading_mode', ?)",
                (json.dumps(self._mode.to_dict()),),
            )
        except Exception as e:
            log.error("Failed to save trading mode: {err}", err=str(e))
