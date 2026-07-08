"""Equity-phantom fix (2026-05-26): _build_account_info_from_v5 must base
equity/available on the USDT settlement coin, not the unified all-coin
total, and fall back to the unified totals when the per-coin USDT entry is
missing or empty (so the account path never breaks).

Pairs with IMPLEMENT_PNL_TRUTH_AND_DISABLE_OVERTIGHTENING.md (equity fix).
"""
from __future__ import annotations

import pytest

from src.bybit_demo.bybit_demo_adapter import _build_account_info_from_v5


def _unified(total_equity: float, total_avail: float, coins: list | None) -> dict:
    """A Bybit V5 result.list[0] entry: account-level totals + per-coin list."""
    d = {
        "totalEquity": str(total_equity),
        "totalAvailableBalance": str(total_avail),
        "totalInitialMargin": "0",
        "totalPerpUPL": "0",
    }
    if coins is not None:
        d["coin"] = coins
    return d


def test_uses_usdt_coin_not_unified_total() -> None:
    """The real bug: unified totalEquity is ~$175k (all coins) but the USDT
    wallet is ~$47.6k. We must report the USDT coin's equity, not $175k."""
    data = _unified(
        total_equity=175341.0,   # unified, all coins (the phantom)
        total_avail=96566.0,
        coins=[
            {"coin": "USDT", "equity": "47628.0", "walletBalance": "47600.0",
             "availableToWithdraw": "47000.0", "unrealisedPnl": "9.36"},
            {"coin": "BTC", "equity": "120000.0", "walletBalance": "120000.0",
             "usdValue": "120000.0"},
            {"coin": "ETH", "equity": "7713.0", "usdValue": "7713.0"},
        ],
    )
    info = _build_account_info_from_v5(data)
    assert info.total_equity == pytest.approx(47628.0)      # USDT, not 175341
    assert info.available_balance == pytest.approx(47000.0)  # USDT availableToWithdraw
    assert info.unrealized_pnl == pytest.approx(9.36)
    assert info.total_equity != pytest.approx(175341.0)


def test_available_falls_back_to_wallet_when_withdraw_blank() -> None:
    """Some UNIFIED responses leave availableToWithdraw blank; use the USDT
    walletBalance as the conservative available basis."""
    data = _unified(
        total_equity=175000.0, total_avail=96000.0,
        coins=[{"coin": "USDT", "equity": "47628.0", "walletBalance": "47600.0",
                "availableToWithdraw": "", "unrealisedPnl": "0"}],
    )
    info = _build_account_info_from_v5(data)
    assert info.total_equity == pytest.approx(47628.0)
    assert info.available_balance == pytest.approx(47600.0)  # walletBalance fallback


def test_falls_back_to_unified_when_no_usdt_coin() -> None:
    """No per-coin USDT entry -> legacy unified totals (no regression, never
    zero out equity)."""
    data = _unified(
        total_equity=175341.0, total_avail=96566.0,
        coins=[{"coin": "BTC", "equity": "120000.0", "walletBalance": "120000.0"}],
    )
    info = _build_account_info_from_v5(data)
    assert info.total_equity == pytest.approx(175341.0)      # unified fallback
    assert info.available_balance == pytest.approx(96566.0)


def test_falls_back_when_coin_list_missing() -> None:
    """No coin list at all -> unified totals."""
    data = _unified(total_equity=50000.0, total_avail=40000.0, coins=None)
    info = _build_account_info_from_v5(data)
    assert info.total_equity == pytest.approx(50000.0)
    assert info.available_balance == pytest.approx(40000.0)


def test_falls_back_when_usdt_coin_all_zero() -> None:
    """An all-zero USDT coin means a parse problem -> fall back rather than
    report zero equity (which would break sizing and the halt)."""
    data = _unified(
        total_equity=175000.0, total_avail=96000.0,
        coins=[{"coin": "USDT", "equity": "0", "walletBalance": "0",
                "availableToWithdraw": "0", "unrealisedPnl": "0"}],
    )
    info = _build_account_info_from_v5(data)
    assert info.total_equity == pytest.approx(175000.0)  # fell back, not 0
