"""PRIMARY Sell-Bias Fix (2026-05-11) — counter-trade + insufficient-data gates.

Unit tests for the two new gates added to ``TradeOptimizer``:

  - ``_is_counter_trade_setup(package)`` — returns True when
    ``package.structural_data.setup_type`` contains "counter".
  - ``_check_insufficient_data_for_flip(package, claude_dir, qwen_dir)``
    — returns (insufficient, count_in_target_direction).

Backed by P.1.9 (DeepSeek response inspection) and the operator's
HEAVY tune decision (2026-05-11).
"""

from __future__ import annotations

from types import SimpleNamespace

from src.apex.optimizer import TradeOptimizer
from src.config.settings import APEXSettings


def _opt(settings: APEXSettings | None = None) -> TradeOptimizer:
    cfg = settings or APEXSettings()
    opt = TradeOptimizer.__new__(TradeOptimizer)
    opt._settings = cfg
    return opt


# ────────────────────────── Counter-trade gate ──────────────────────────


def test_is_counter_trade_setup_detects_bullish_counter() -> None:
    opt = _opt()
    pkg = SimpleNamespace(
        structural_data=SimpleNamespace(setup_type="BULLISH_FVG_OB_COUNTER"),
    )
    assert opt._is_counter_trade_setup(pkg) is True


def test_is_counter_trade_setup_detects_bearish_counter() -> None:
    opt = _opt()
    pkg = SimpleNamespace(
        structural_data=SimpleNamespace(setup_type="BEARISH_FVG_OB_COUNTER"),
    )
    assert opt._is_counter_trade_setup(pkg) is True


def test_is_counter_trade_setup_case_insensitive() -> None:
    opt = _opt()
    pkg = SimpleNamespace(
        structural_data=SimpleNamespace(setup_type="bullish_fvg_ob_counter"),
    )
    assert opt._is_counter_trade_setup(pkg) is True


def test_is_counter_trade_setup_rejects_non_counter() -> None:
    opt = _opt()
    pkg = SimpleNamespace(
        structural_data=SimpleNamespace(setup_type="BULLISH_FVG_OB"),
    )
    assert opt._is_counter_trade_setup(pkg) is False


def test_is_counter_trade_setup_rejects_substring_false_positive() -> None:
    """The check uses endswith("_counter") not substring, so a value
    like "BULLISH_ENCOUNTERED_..." would NOT trigger a counter-trade
    revert even though it contains the substring "counter".

    Defensive against future SetupType additions.
    """
    opt = _opt()
    pkg = SimpleNamespace(
        structural_data=SimpleNamespace(setup_type="BULLISH_ENCOUNTERED_RESISTANCE"),
    )
    assert opt._is_counter_trade_setup(pkg) is False


def test_is_counter_trade_setup_handles_missing_structural_data() -> None:
    opt = _opt()
    pkg = SimpleNamespace(structural_data=None)
    assert opt._is_counter_trade_setup(pkg) is False


def test_is_counter_trade_setup_handles_missing_setup_type() -> None:
    """Setup type field absent or empty string."""
    opt = _opt()
    pkg = SimpleNamespace(structural_data=SimpleNamespace(setup_type=""))
    assert opt._is_counter_trade_setup(pkg) is False


# ────────────────────── Insufficient-data gate ─────────────────────────


def _pkg_with_trades(*, buy_count: int, sell_count: int) -> SimpleNamespace:
    trades = (
        [{"direction": "Buy"} for _ in range(buy_count)]
        + [{"direction": "Sell"} for _ in range(sell_count)]
    )
    return SimpleNamespace(
        symbol_history=SimpleNamespace(trades=trades),
    )


def test_insufficient_data_blocks_when_target_below_min_trades() -> None:
    """E27: default min raised 5 -> 8. 8 Sell + 0 Buy: a flip Buy→Sell is
    fine (8 >= 8). A flip Sell→Buy is insufficient (0 < 8).
    """
    opt = _opt()
    pkg = _pkg_with_trades(buy_count=0, sell_count=8)

    # Flip Buy → Sell: target is Sell, has 8 trades → sufficient.
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Buy", qwen_direction="Sell",
    )
    assert insufficient is False
    assert count == 8

    # Flip Sell → Buy: target is Buy, has 0 trades → insufficient.
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Sell", qwen_direction="Buy",
    )
    assert insufficient is True
    assert count == 0


def test_insufficient_data_just_below_min_trades_blocks() -> None:
    """E27: 7 trades (one below the new min of 8) → insufficient."""
    opt = _opt()
    pkg = _pkg_with_trades(buy_count=7, sell_count=0)
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Sell", qwen_direction="Buy",
    )
    assert insufficient is True
    assert count == 7


def test_insufficient_data_at_exactly_min_trades_passes() -> None:
    """E27: 8 trades exactly == new min_required → NOT insufficient."""
    opt = _opt()
    pkg = _pkg_with_trades(buy_count=8, sell_count=0)
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Sell", qwen_direction="Buy",
    )
    assert insufficient is False
    assert count == 8


def test_insufficient_data_disabled_when_min_trades_zero() -> None:
    """apex_min_trades_for_flip=0 disables the gate entirely."""
    cfg = APEXSettings(apex_min_trades_for_flip=0)
    opt = _opt(cfg)
    pkg = _pkg_with_trades(buy_count=0, sell_count=0)
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Sell", qwen_direction="Buy",
    )
    assert insufficient is False
    assert count == -1  # Sentinel for "gate disabled"


def test_insufficient_data_handles_missing_symbol_history() -> None:
    """When package.symbol_history is None we fail PERMISSIVE — return
    (False, -1) so the downstream confidence gate is the only blocker.
    Matches the operator's aggressive-exploitation philosophy: when
    data is degraded we do NOT add conservative bias.

    The sentinel ``-1`` surfaces in APEX_FLIP_DECISION as
    ``flip_dir_trades=-1`` so operators can see the gate did not run.
    """
    opt = _opt()
    pkg = SimpleNamespace(symbol_history=None)
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Buy", qwen_direction="Sell",
    )
    assert insufficient is False
    assert count == -1


def test_insufficient_data_handles_missing_trades_attribute() -> None:
    """symbol_history exists but no `trades` attr — same fail-permissive
    semantics as missing symbol_history (Phase 3 deep audit hardening,
    2026-05-11)."""
    opt = _opt()
    pkg = SimpleNamespace(symbol_history=SimpleNamespace())
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Buy", qwen_direction="Sell",
    )
    assert insufficient is False
    assert count == -1


def test_insufficient_data_handles_non_list_trades() -> None:
    """trades=None or trades=int → fail-permissive (added in Phase 3
    deep audit hardening, 2026-05-11). The gate is wrapped with type
    checks so a partial assembler failure cannot raise out of optimize()."""
    opt = _opt()
    for bad in (None, 42, "not-a-list", {"direction": "Sell"}):
        pkg = SimpleNamespace(symbol_history=SimpleNamespace(trades=bad))
        insufficient, count = opt._check_insufficient_data_for_flip(
            pkg, claude_direction="Buy", qwen_direction="Sell",
        )
        assert insufficient is False, f"trades={bad!r}: expected fail-permissive"
        assert count == -1, f"trades={bad!r}: expected sentinel -1"


def test_insufficient_data_skips_non_dict_trade_items() -> None:
    """Mixed list with some non-dict entries: count only the valid dicts.
    Defensive parsing — does not raise on malformed entries.
    """
    opt = _opt()
    pkg = SimpleNamespace(symbol_history=SimpleNamespace(trades=[
        {"direction": "Sell"},
        None,                       # not a dict
        "string",                   # not a dict
        {"direction": "Sell"},
        {"foo": "bar"},             # dict but no direction key
        42,                          # not a dict
    ]))
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Buy", qwen_direction="Sell",
    )
    # Only 2 valid Sell dicts → 2 < 5 → insufficient
    assert insufficient is True
    assert count == 2


def test_insufficient_data_package_none_safe() -> None:
    """``package=None`` from an exception-handler partial path. Must
    not raise; must fail-permissive."""
    opt = _opt()
    insufficient, count = opt._check_insufficient_data_for_flip(
        None, claude_direction="Buy", qwen_direction="Sell",
    )
    assert insufficient is False
    assert count == -1


def test_insufficient_data_custom_min_threshold() -> None:
    """Operator can tighten or loosen the gate via config."""
    cfg = APEXSettings(apex_min_trades_for_flip=10)
    opt = _opt(cfg)
    pkg = _pkg_with_trades(buy_count=0, sell_count=8)
    # 8 Sell trades < 10 required → insufficient.
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Buy", qwen_direction="Sell",
    )
    assert insufficient is True
    assert count == 8


# ──────────────────── Defaults match operator HEAVY tune ────────────────


def test_settings_defaults_for_new_gates() -> None:
    cfg = APEXSettings()
    # E27 (2026-05-28): raised 5 -> 8 to require a more durable sample
    # before a direction flip is licensed.
    assert cfg.apex_min_trades_for_flip == 8
    assert cfg.apex_respect_counter_trade is True
