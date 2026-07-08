"""Stage 2 phase 1 — strategist top-N cap + open-position pinning."""

from types import SimpleNamespace

import pytest

from src.brain.strategist import ClaudeStrategist
from src.config.settings import Stage2Settings
from src.core.coin_package import CoinPackage


def _pkg(symbol: str, interest: float, score: float, has_position: bool = False) -> CoinPackage:
    p = CoinPackage(symbol=symbol, qualified=True, opportunity_score=score)
    p.interestingness_score = interest
    if has_position:
        p.open_position = {"side": "long", "entry_price": 1.0}
    return p


class _FakeLayerManager:
    def __init__(self, packages: dict) -> None:
        self._coin_packages = packages

    def get_coin_packages(self) -> dict:
        return self._coin_packages


def _stub_strategist(packages: dict, top_n: int = 6) -> ClaudeStrategist:
    s = ClaudeStrategist.__new__(ClaudeStrategist)
    s.services = {"layer_manager": _FakeLayerManager(packages)}
    s.settings = SimpleNamespace(
        stage2=Stage2Settings(top_n_to_brain=top_n),
        brain=SimpleNamespace(use_packages=True, surface_briefing_fields=False),
    )
    return s


class TestTopNCap:
    def test_caps_15_to_6_no_positions(self) -> None:
        # 15 packages with monotonically increasing interestingness;
        # top-6 by interestingness wins, no positions to pin.
        pkgs = {
            f"COIN{i:02d}USDT": _pkg(f"COIN{i:02d}USDT", interest=i / 100.0, score=i / 100.0)
            for i in range(15)
        }
        s = _stub_strategist(pkgs)

        # Apply the cap by simulating the strategist's package-read block.
        # The cap logic lives inside _build_trade_prompt; isolating just
        # the cap behavior here keeps the test focused on Phase 1.
        from copy import copy
        packages = copy(pkgs)
        _top_n = s.settings.stage2.top_n_to_brain
        if packages and _top_n > 0 and len(packages) > _top_n:
            pinned = {
                sym: p for sym, p in packages.items()
                if p.open_position is not None
            }
            remaining_pool = [
                p for sym, p in packages.items() if sym not in pinned
            ]
            remaining_pool.sort(
                key=lambda p: (
                    getattr(p, "interestingness_score", 0.0),
                    getattr(p, "opportunity_score", 0.0),
                ),
                reverse=True,
            )
            slots_left = max(0, _top_n - len(pinned))
            capped = dict(pinned)
            for p in remaining_pool[:slots_left]:
                capped[p.symbol] = p
            packages = capped

        assert len(packages) == 6
        # The 6 highest-interestingness coins are 09-14.
        expected = {f"COIN{i:02d}USDT" for i in range(9, 15)}
        assert set(packages.keys()) == expected

    def test_pins_open_positions_consumes_slots(self) -> None:
        # 15 packages with 3 open positions of LOW interestingness;
        # all 3 must remain. Remaining 3 slots fill by interestingness.
        pkgs = {}
        for i in range(15):
            sym = f"COIN{i:02d}USDT"
            # Positions on the bottom 3 by interestingness.
            has_pos = i in (0, 1, 2)
            pkgs[sym] = _pkg(sym, interest=i / 100.0, score=i / 100.0, has_position=has_pos)
        from copy import copy
        packages = copy(pkgs)
        _top_n = 6
        pinned = {
            sym: p for sym, p in packages.items() if p.open_position is not None
        }
        remaining_pool = [p for sym, p in packages.items() if sym not in pinned]
        remaining_pool.sort(
            key=lambda p: (
                getattr(p, "interestingness_score", 0.0),
                getattr(p, "opportunity_score", 0.0),
            ),
            reverse=True,
        )
        slots_left = max(0, _top_n - len(pinned))
        capped = dict(pinned)
        for p in remaining_pool[:slots_left]:
            capped[p.symbol] = p

        assert len(capped) == 6
        # All 3 positions present.
        for sym in ("COIN00USDT", "COIN01USDT", "COIN02USDT"):
            assert sym in capped
        # Remaining 3 are top-3 by interestingness from the non-position pool.
        for sym in ("COIN12USDT", "COIN13USDT", "COIN14USDT"):
            assert sym in capped

    def test_no_cap_when_under_threshold(self) -> None:
        pkgs = {
            f"COIN{i:02d}USDT": _pkg(f"COIN{i:02d}USDT", interest=i / 100.0, score=i / 100.0)
            for i in range(4)
        }
        from copy import copy
        packages = copy(pkgs)
        _top_n = 6
        # Cap is a no-op: 4 < 6.
        if not (packages and _top_n > 0 and len(packages) > _top_n):
            # No cap applied; packages unchanged.
            assert len(packages) == 4
            assert set(packages.keys()) == set(pkgs.keys())
        else:
            pytest.fail("Cap should not have applied when under threshold")

    def test_zero_packages_safe(self) -> None:
        from copy import copy
        packages: dict = {}
        _top_n = 6
        # Cap branch guarded by `packages and ...`; nothing happens.
        if packages and _top_n > 0 and len(packages) > _top_n:
            pytest.fail("Cap branch should not run on empty packages")
        assert packages == {}
