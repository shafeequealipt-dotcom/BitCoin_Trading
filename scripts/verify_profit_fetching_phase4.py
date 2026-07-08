"""Phase 4 behavioral self-verification — highest-stop-wins spine (Rule 7).

Confirms the pure selection (_pf_select_stop) without a live tick:
  1. Long: the highest (tightest) candidate wins; ladder wins when it locks
     above the trail, the Chandelier wins when it sits above the ladder.
  2. Short: the lowest candidate wins (mirror).
  3. A candidate not beating the current SL is dropped (tighten-only).
  4. Only-one-applies and none-apply degrade correctly.

Run from the project root:  PYTHONPATH=. python scripts/verify_profit_fetching_phase4.py
Exit code 0 = all checks pass.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from src.workers.profit_sniper import ProfitSniper

select = ProfitSniper._pf_select_stop


def _ladder(price, apply=True):
    return SimpleNamespace(ladder_stop_price=price, should_apply=apply)


def _trail(price, apply=True):
    return SimpleNamespace(trail_stop_price=price, should_apply=apply)


def main() -> int:
    # 1. Long, ladder locks above the trail -> ladder wins (max price).
    w = select(_trail(100.9), _ladder(101.4), current_sl=100.0, is_long=True)
    assert w == ("ladder", 101.4, "profit_sniper_ladder"), w

    # 2. Long, Chandelier above the ladder -> chandelier wins.
    w = select(_trail(101.4), _ladder(100.9), current_sl=100.0, is_long=True)
    assert w == ("chandelier", 101.4, "profit_sniper_trail"), w

    # 3. Long, both below the current SL -> nothing (tighten-only).
    w = select(_trail(100.9), _ladder(101.4), current_sl=102.0, is_long=True)
    assert w is None, w

    # 4. Short, lowest (tightest) wins -> ladder at 98.6.
    w = select(_trail(99.1), _ladder(98.6), current_sl=100.0, is_long=False)
    assert w == ("ladder", 98.6, "profit_sniper_ladder"), w

    # 5. Short, candidate above current SL -> dropped (tighten-only for short).
    w = select(_trail(101.0), _ladder(101.5), current_sl=100.0, is_long=False)
    assert w is None, w

    # 6. Only the trail applies -> chandelier wins.
    w = select(_trail(100.9), _ladder(101.4, apply=False), current_sl=0.0, is_long=True)
    assert w == ("chandelier", 100.9, "profit_sniper_trail"), w

    # 7. Only the ladder applies -> ladder wins.
    w = select(_trail(100.9, apply=False), _ladder(101.4), current_sl=0.0, is_long=True)
    assert w == ("ladder", 101.4, "profit_sniper_ladder"), w

    # 8. Neither applies -> None.
    w = select(_trail(100.9, apply=False), _ladder(101.4, apply=False), current_sl=0.0, is_long=True)
    assert w is None, w

    # 9. No candidates at all -> None.
    w = select(None, None, current_sl=0.0, is_long=True)
    assert w is None, w

    print("Spine selection OK: long picks highest, short picks lowest, tighten-only honored")
    print("\nPHASE_4_SELF_VERIFY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
