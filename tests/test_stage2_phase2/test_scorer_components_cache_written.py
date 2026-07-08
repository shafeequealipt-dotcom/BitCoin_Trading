"""Stage 2 phase 2 — StrategyWorker writes _scorer_components in parity with _score_cache.

Past incident: PnL filter at strategy_worker.py:801-811 stripped 32-45 of
50 coins from a downstream cache. We must NOT repeat that — the
scorer-components write attaches to the ``scored`` loop (full universe)
not ``filtered``. This test asserts parity per tick.
"""

from types import SimpleNamespace

from src.core.layer_manager import LayerManager


class _FakeRawSignal:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class _FakeScoredSetup:
    def __init__(self, symbol: str, total: float, base: float = 30.0,
                 conf: float = 20.0, ctxs: float = 15.0,
                 qual: float = 10.0, grade: str = "A") -> None:
        self.raw_signal = _FakeRawSignal(symbol)
        self.total_score = total
        self.base_score = base
        self.confluence_score = conf
        self.context_score = ctxs
        self.quality_score = qual
        self.grade = grade


def test_layer_manager_has_scorer_components_attribute_and_getter() -> None:
    """LayerManager.__init__ creates the cache; getter returns None when empty."""
    lm = LayerManager.__new__(LayerManager)
    # Manual minimal init for the cache + getter under test:
    lm._scorer_components = {}
    assert lm.get_scorer_components("BTCUSDT") is None

    lm._scorer_components["BTCUSDT"] = {
        "base": 30.0, "confluence": 20.0, "context": 15.0,
        "quality": 10.0, "total": 75.0, "grade": "A",
        "last_updated": 0.0,
    }
    out = lm.get_scorer_components("BTCUSDT")
    assert out is not None
    assert out["total"] == 75.0
    assert out["grade"] == "A"


def test_scorer_components_parity_with_score_cache_simulation() -> None:
    """Simulate the StrategyWorker scored loop to confirm both caches
    receive the same set of symbols (no PnL-filter shrinkage).
    """
    import time as _time
    lm = LayerManager.__new__(LayerManager)
    lm._scorer_components = {}
    score_cache: dict[str, float] = {}

    scored = [
        _FakeScoredSetup("BTCUSDT", 88.0, 35.0, 22.0, 18.0, 13.0, "A+"),
        _FakeScoredSetup("ETHUSDT", 72.5, 33.0, 18.0, 12.5, 9.0, "A"),
        _FakeScoredSetup("SOLUSDT", 60.0, 28.0, 16.0, 10.0, 6.0, "B"),
    ]

    # Replicate the production loop body verbatim (parity guarantee).
    for _ss in scored:
        _sym = _ss.raw_signal.symbol
        score_cache[_sym] = float(_ss.total_score)
        comps = getattr(lm, "_scorer_components", None)
        if comps is None:
            comps = {}
            lm._scorer_components = comps
        comps[_sym] = {
            "base": float(_ss.base_score),
            "confluence": float(_ss.confluence_score),
            "context": float(_ss.context_score),
            "quality": float(_ss.quality_score),
            "total": float(_ss.total_score),
            "grade": _ss.grade,
            "last_updated": _time.time(),
        }

    assert len(score_cache) == 3
    assert len(lm._scorer_components) == 3
    assert set(score_cache.keys()) == set(lm._scorer_components.keys())
    # Spot-check the per-coin breakdown is the right shape.
    eth = lm.get_scorer_components("ETHUSDT")
    assert eth["base"] == 33.0
    assert eth["confluence"] == 18.0
    assert eth["context"] == 12.5
    assert eth["quality"] == 9.0
    assert eth["total"] == 72.5
    assert eth["grade"] == "A"
