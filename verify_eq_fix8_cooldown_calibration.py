"""Entry-Quality Fix 8 self-verification (2026-06-10).

The flat loss-only re-entry cooldown is sized on the losing-window replay and
left enabled (no escalation curve — the operator's decision). RUNE's 11
consecutive same-direction loss re-entries were 604-1511s apart, so the prior
300s cooldown blocked ~0; a flat 1200s blocks 10 of 11. This confirms the config
(1200s + enabled), the replay sizing, and that the real TradeCoordinator honours
the duration in loss-only mode (a win sets no cooldown). Never rewrites data.
"""

from __future__ import annotations

from src.config.settings import Settings
from src.core.trade_coordinator import TradeCoordinator

# RUNE's loss-close -> next same-direction open gaps (seconds) from the losing
# window (2026-06-10 09:30-14:00), as extracted in the investigation.
RUNE_REENTRY_GAPS = [1046, 679, 678, 604, 864, 628, 1118, 1511, 692, 666, 933]


def _blocked_by_flat(gaps, cooldown_s: int) -> int:
    # A flat cooldown blocks a re-entry iff the gap is shorter than the cooldown.
    return sum(1 for g in gaps if g < cooldown_s)


def test_config_loads_1200_and_enabled() -> None:
    s = Settings.load()
    assert s.apex.reentry_cooldown_seconds == 1200, s.apex.reentry_cooldown_seconds
    assert s.apex.loss_cooldown_enabled is True, "loss-only cooldown must be enabled"
    print("PASS: [apex] loads reentry_cooldown_seconds=1200 and loss_cooldown_enabled=true.")


def test_replay_sizing_1200_blocks_ten_of_eleven() -> None:
    blocked_300 = _blocked_by_flat(RUNE_REENTRY_GAPS, 300)
    blocked_600 = _blocked_by_flat(RUNE_REENTRY_GAPS, 600)
    blocked_1200 = _blocked_by_flat(RUNE_REENTRY_GAPS, 1200)
    assert blocked_300 == 0, f"300s should block ~0 of RUNE's re-entries, got {blocked_300}"
    assert blocked_1200 >= 10, f"1200s should block >=10 of 11, got {blocked_1200}"
    print(
        f"PASS: replay sizing — flat 300s blocks {blocked_300}/11, 600s blocks "
        f"{blocked_600}/11, 1200s blocks {blocked_1200}/11 (the chosen duration)."
    )


def _state_after_close(cooldown_s: int, loss_only: bool, pnl_usd: float):
    c = TradeCoordinator()
    c.set_reentry_cooldown_seconds(cooldown_s)
    c.set_loss_cooldown_enabled(loss_only)
    c.register_trade(symbol="RUNEUSDT", entry_price=0.39, side="Buy", size=10000.0)
    c.on_trade_closed(
        symbol="RUNEUSDT", pnl_pct=(pnl_usd / 4000.0 * 100.0), pnl_usd=pnl_usd,
        was_win=pnl_usd > 0, closed_by="bybit_demo_sl_tp", exit_price=0.385,
    )
    cooled = c.is_symbol_in_any_cooldown("RUNEUSDT")
    active = c.get_active_reentry_cooldowns()
    return cooled, active


def test_coordinator_honours_1200_on_a_loss() -> None:
    cooled, active = _state_after_close(1200, True, -33.0)
    assert cooled is True, "a real loss must hold the coin out (selection exclusion)"
    # active is a list of (symbol, direction, remaining_seconds).
    rem = [r[2] for r in active if r[0] == "RUNEUSDT"]
    assert rem and 1100 < rem[0] <= 1200, f"remaining cooldown should be ~1200s, got {rem}"
    print(f"PASS: real coordinator holds a losing coin out for ~1200s ({rem[0]}s remaining).")


def test_win_sets_no_cooldown() -> None:
    cooled, _ = _state_after_close(1200, True, +20.0)
    assert cooled is False, "a win must NOT set a cooldown — a profitable coin stays selectable"
    print("PASS: a win sets no cooldown (loss-only mode spares the net-winner pattern, e.g. KAT).")


def main() -> None:
    print("=== Entry-Quality Fix 8 — flat loss-only cooldown calibration verification ===")
    test_config_loads_1200_and_enabled()
    test_replay_sizing_1200_blocks_ten_of_eleven()
    test_coordinator_honours_1200_on_a_loss()
    test_win_sets_no_cooldown()
    print("\nALL FIX-8 CHECKS PASSED.")


if __name__ == "__main__":
    main()
