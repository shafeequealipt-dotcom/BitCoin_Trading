"""Phase 2 — Prompt mode follows transformer.

Verifies that ``TradingModeManager`` derives its prompt-text mode from
the Transformer routing state first, with ``settings.bybit.testnet`` as
a fallback only when Transformer is in bybit mode. After a transformer
switch the manager's ``refresh()`` re-derives so the Stage 2 prompt
header follows order routing in lockstep.

Three semantic states the prompt header must reach:
  SHADOW  — when ``transformer.is_shadow`` is True.
  TESTNET — when ``transformer.is_shadow`` is False AND
            ``settings.bybit.testnet`` is True.
  MAINNET — when ``transformer.is_shadow`` is False AND
            ``settings.bybit.testnet`` is False.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.trading_mode import (
    TradingMode,
    TradingModeManager,
    TradingModeType,
)


def _settings(testnet: bool) -> SimpleNamespace:
    """Minimal Settings stub exposing only ``bybit.testnet``."""
    return SimpleNamespace(bybit=SimpleNamespace(testnet=testnet))


def _transformer_stub(is_shadow: bool) -> MagicMock:
    """Transformer stub exposing only ``is_shadow``."""
    t = MagicMock()
    t.is_shadow = is_shadow
    return t


def _async_db() -> MagicMock:
    """DatabaseManager stub with async fetch_one/execute."""
    db = MagicMock()
    db.fetch_one = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    return db


# ─────────────────────────────────────────────────────────────────────────
# Part 1 — derive-from-state truth table
# ─────────────────────────────────────────────────────────────────────────


class TestDeriveModeFromState:
    """The constructor's _derive_mode_from_state must satisfy the truth
    table across (transformer.is_shadow × bybit.testnet)."""

    def test_shadow_overrides_testnet(self) -> None:
        """Shadow always wins regardless of bybit.testnet value."""
        db = _async_db()
        mgr = TradingModeManager(
            db, settings=_settings(testnet=True),
            transformer=_transformer_stub(is_shadow=True),
        )
        assert mgr.mode.is_shadow is True
        assert mgr.mode.is_testnet is False
        assert mgr.mode.is_mainnet is False

    def test_shadow_overrides_mainnet(self) -> None:
        db = _async_db()
        mgr = TradingModeManager(
            db, settings=_settings(testnet=False),
            transformer=_transformer_stub(is_shadow=True),
        )
        assert mgr.mode.is_shadow is True

    def test_bybit_with_testnet_true_yields_testnet(self) -> None:
        db = _async_db()
        mgr = TradingModeManager(
            db, settings=_settings(testnet=True),
            transformer=_transformer_stub(is_shadow=False),
        )
        assert mgr.mode.is_testnet is True
        assert mgr.mode.is_shadow is False

    def test_bybit_with_testnet_false_yields_mainnet(self) -> None:
        db = _async_db()
        mgr = TradingModeManager(
            db, settings=_settings(testnet=False),
            transformer=_transformer_stub(is_shadow=False),
        )
        assert mgr.mode.is_mainnet is True
        assert mgr.mode.is_shadow is False

    def test_no_transformer_falls_back_to_settings(self) -> None:
        """When no transformer is wired, derive from bybit.testnet only."""
        db = _async_db()
        mgr_main = TradingModeManager(
            db, settings=_settings(testnet=False), transformer=None,
        )
        assert mgr_main.mode.is_mainnet is True

        mgr_test = TradingModeManager(
            db, settings=_settings(testnet=True), transformer=None,
        )
        assert mgr_test.mode.is_testnet is True

    def test_no_settings_no_transformer_defaults_testnet(self) -> None:
        """Cold-start fallback when both are absent."""
        db = _async_db()
        mgr = TradingModeManager(db, settings=None, transformer=None)
        assert mgr.mode.is_testnet is True


# ─────────────────────────────────────────────────────────────────────────
# Part 2 — refresh() after transformer flip
# ─────────────────────────────────────────────────────────────────────────


class TestRefreshAfterTransformerFlip:
    """A live transformer that flips its is_shadow attribute should
    cause refresh() to update the manager's mode in lockstep."""

    def test_shadow_to_bybit_mainnet(self) -> None:
        db = _async_db()
        transformer = _transformer_stub(is_shadow=True)
        mgr = TradingModeManager(
            db, settings=_settings(testnet=False), transformer=transformer,
        )
        assert mgr.mode.is_shadow is True

        # Simulate transformer.switch_to("bybit") — routing flips first,
        # then the registered switch callback fires refresh().
        transformer.is_shadow = False
        mgr.refresh(persist=False)

        assert mgr.mode.is_mainnet is True
        assert mgr.mode.is_shadow is False

    def test_bybit_mainnet_to_shadow(self) -> None:
        db = _async_db()
        transformer = _transformer_stub(is_shadow=False)
        mgr = TradingModeManager(
            db, settings=_settings(testnet=False), transformer=transformer,
        )
        assert mgr.mode.is_mainnet is True

        transformer.is_shadow = True
        mgr.refresh(persist=False)

        assert mgr.mode.is_shadow is True

    def test_set_transformer_re_derives(self) -> None:
        """set_transformer wires a late-arriving transformer and refreshes."""
        db = _async_db()
        mgr = TradingModeManager(
            db, settings=_settings(testnet=False), transformer=None,
        )
        # Before wiring: transformer absent, mainnet from settings.
        assert mgr.mode.is_mainnet is True

        # Wire a shadow transformer.
        mgr.set_transformer(_transformer_stub(is_shadow=True))
        assert mgr.mode.is_shadow is True


# ─────────────────────────────────────────────────────────────────────────
# Part 3 — Claude prompt instruction text
# ─────────────────────────────────────────────────────────────────────────


class TestClaudeModeInstruction:
    """The Stage 2 prompt-header text injected into Claude's prompt
    must communicate the routing reality."""

    def test_shadow_text_communicates_paper_with_real_data(self) -> None:
        text = TradingMode.shadow().get_claude_mode_instruction()
        assert "MODE: SHADOW" in text
        assert "paper trading" in text
        assert "real Bybit" in text or "real Bybit MAINNET" in text
        # Path C philosophy — opportunity-exploit framing, not defensive.
        assert "exploit" in text or "characterize" in text
        # Defensive language must NOT appear.
        assert "REAL capital" not in text
        assert "Maximum caution required" not in text

    def test_testnet_text_warns_about_synthetic_prices(self) -> None:
        text = TradingMode.testnet().get_claude_mode_instruction()
        assert "MODE: TESTNET" in text
        assert "SYNTHETIC" in text

    def test_mainnet_text_keeps_caution_language(self) -> None:
        text = TradingMode.mainnet().get_claude_mode_instruction()
        assert "MODE: MAINNET" in text
        assert "REAL capital" in text


# ─────────────────────────────────────────────────────────────────────────
# Part 4 — Persistence round-trip
# ─────────────────────────────────────────────────────────────────────────


class TestDictRoundTrip:
    """from_dict / to_dict must round-trip all three modes."""

    @pytest.mark.parametrize("factory,expected_type", [
        (TradingMode.shadow, TradingModeType.SHADOW),
        (TradingMode.testnet, TradingModeType.TESTNET),
        (TradingMode.mainnet, TradingModeType.MAINNET),
    ])
    def test_round_trip(self, factory, expected_type) -> None:
        original = factory()
        data = original.to_dict()
        reconstructed = TradingMode.from_dict(data)
        assert reconstructed.mode == expected_type

    def test_unknown_mode_falls_back_to_testnet(self) -> None:
        """from_dict must not raise on garbage; defaults to testnet."""
        reconstructed = TradingMode.from_dict({"mode": "garbage"})
        assert reconstructed.mode == TradingModeType.TESTNET


# ─────────────────────────────────────────────────────────────────────────
# Part 5 — set_mode override semantics
# ─────────────────────────────────────────────────────────────────────────


class TestSetModeOverride:
    """The Telegram dashboard toggle calls set_mode; that path must
    continue to work for explicit testnet/mainnet/shadow overrides."""

    @pytest.mark.asyncio
    async def test_set_mode_shadow(self) -> None:
        db = _async_db()
        mgr = TradingModeManager(
            db, settings=_settings(testnet=False),
            transformer=_transformer_stub(is_shadow=False),
        )
        assert mgr.mode.is_mainnet is True

        await mgr.set_mode(TradingModeType.SHADOW)
        assert mgr.mode.is_shadow is True
        # Persistence call recorded.
        db.execute.assert_called()

    @pytest.mark.asyncio
    async def test_set_mode_testnet_when_transformer_in_bybit(self) -> None:
        db = _async_db()
        mgr = TradingModeManager(
            db, settings=_settings(testnet=False),
            transformer=_transformer_stub(is_shadow=False),
        )
        await mgr.set_mode(TradingModeType.TESTNET)
        assert mgr.mode.is_testnet is True
