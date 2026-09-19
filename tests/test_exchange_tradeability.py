"""Exchange-tradeability contract (2026-09-19).

Incident: 26 of 55 brain directives (47%, 2026-09-16..19) targeted symbols the
Shadow exchange could not fill. The bot ranks its universe by volatility over
all Bybit perpetuals; Shadow fills only the coins it selected at ITS startup,
so the rest died at placement with "Symbol not tracked" after passing every
quality gate. These tests pin each layer of the fix — the provider, the
selector filter, the execution pre-flight — and above all the FAIL-OPEN
property: an unknown answer must never restrict trading.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.config.settings import UniverseRefreshSettings
from src.core.exchange_tradeability import ExchangeTradeability
from src.core.types import OHLCV, Ticker, TimeFrame
from src.shadow.shadow_adapter import ShadowOrderService
from src.strategies.universe_selector import select_universe


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Test doubles ─────────────────────────────────────────────────────────

class _FakeShadowOrder:
    """Stands in for ShadowOrderService.get_tradeable_symbols."""

    def __init__(self, *results):
        self._results = list(results)
        self.calls = 0

    async def get_tradeable_symbols(self):
        self.calls += 1
        r = self._results[min(self.calls - 1, len(self._results) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _provider(shadow, *, transformer=None, ttl=300.0, clock=None):
    return ExchangeTradeability(
        shadow, transformer, ttl_seconds=ttl, clock=clock or _Clock(),
    )


# ── Provider: the happy path and mode-awareness ──────────────────────────

def test_returns_shadow_symbols() -> None:
    p = _provider(_FakeShadowOrder({"BTCUSDT", "ETHUSDT"}))
    assert _run(p.get_tradeable()) == {"BTCUSDT", "ETHUSDT"}
    assert _run(p.is_tradeable("BTCUSDT")) is True
    assert _run(p.is_tradeable("CHIPUSDT")) is False


def test_non_shadow_mode_does_not_restrict() -> None:
    """Bybit / Bybit-demo list every symbol — the concept does not apply."""
    shadow = _FakeShadowOrder({"BTCUSDT"})
    p = _provider(shadow, transformer=SimpleNamespace(is_shadow=False))
    assert _run(p.get_tradeable()) is None
    assert _run(p.is_tradeable("CHIPUSDT")) is None
    assert shadow.calls == 0  # never even asked


def test_shadow_mode_follows_transformer_live() -> None:
    tf = SimpleNamespace(is_shadow=True)
    p = _provider(_FakeShadowOrder({"BTCUSDT"}), transformer=tf)
    assert _run(p.is_tradeable("ETHUSDT")) is False
    tf.is_shadow = False  # operator switches exchange at runtime
    assert _run(p.is_tradeable("ETHUSDT")) is None


def test_no_transformer_assumes_shadow() -> None:
    """Matches WorkerManager's direct-services fallback when no Transformer."""
    p = _provider(_FakeShadowOrder({"BTCUSDT"}), transformer=None)
    assert _run(p.is_tradeable("ETHUSDT")) is False


def test_unreadable_transformer_state_fails_open() -> None:
    class _Broken:
        @property
        def is_shadow(self):
            raise RuntimeError("boom")

    p = _provider(_FakeShadowOrder({"BTCUSDT"}), transformer=_Broken())
    assert _run(p.is_tradeable("ETHUSDT")) is None


def test_no_shadow_adapter_fails_open() -> None:
    p = _provider(None)
    assert _run(p.get_tradeable()) is None
    assert _run(p.is_tradeable("BTCUSDT")) is None


# ── Provider: FAIL OPEN when the answer is unavailable ───────────────────

@pytest.mark.parametrize("bad", [None, set(), Exception("shadow down")])
def test_unavailable_with_no_history_is_unknown_not_empty(bad) -> None:
    """None ("cannot say") must never be confused with an empty set
    ("nothing tradeable") — the latter would halt all trading on an outage."""
    p = _provider(_FakeShadowOrder(bad))
    assert _run(p.get_tradeable()) is None
    assert _run(p.is_tradeable("BTCUSDT")) is None


def test_failed_refresh_keeps_serving_last_good_list() -> None:
    clock = _Clock()
    p = _provider(_FakeShadowOrder({"BTCUSDT"}, None), clock=clock)
    assert _run(p.get_tradeable()) == {"BTCUSDT"}
    clock.t += 301  # TTL expires, refresh returns None
    assert _run(p.get_tradeable()) == {"BTCUSDT"}
    assert _run(p.is_tradeable("BTCUSDT")) is True


# ── Provider: caching ────────────────────────────────────────────────────

def test_ttl_cache_avoids_refetching() -> None:
    clock = _Clock()
    shadow = _FakeShadowOrder({"BTCUSDT"})
    p = _provider(shadow, clock=clock)
    for _ in range(5):
        _run(p.is_tradeable("BTCUSDT"))
    assert shadow.calls == 1
    clock.t += 301
    _run(p.is_tradeable("BTCUSDT"))
    assert shadow.calls == 2


def test_outage_costs_one_attempt_per_ttl_not_one_per_directive() -> None:
    clock = _Clock()
    shadow = _FakeShadowOrder(None)
    p = _provider(shadow, clock=clock)
    for _ in range(10):
        _run(p.is_tradeable("BTCUSDT"))
    assert shadow.calls == 1


def test_list_change_is_picked_up_after_ttl() -> None:
    """Shadow restarts and re-selects its coins -> bot follows within TTL."""
    clock = _Clock()
    p = _provider(_FakeShadowOrder({"AAAUSDT"}, {"AAAUSDT", "BBBUSDT"}), clock=clock)
    assert _run(p.is_tradeable("BBBUSDT")) is False
    clock.t += 301
    assert _run(p.is_tradeable("BBBUSDT")) is True


# ── ShadowOrderService.get_tradeable_symbols (the HTTP client) ───────────

class _FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body
        self.request_info, self.history = None, ()

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc, self.urls = resp, exc, []

    def get(self, url, **kw):
        self.urls.append(url)
        if self._exc:
            raise self._exc
        return self._resp


def _svc(session):
    return ShadowOrderService(session, "http://127.0.0.1:9090")


def test_client_parses_symbols_and_hits_coins_endpoint() -> None:
    s = _FakeSession(_FakeResp(200, {"symbols": ["BTCUSDT", "ETHUSDT"], "count": 2}))
    assert _run(_svc(s).get_tradeable_symbols()) == {"BTCUSDT", "ETHUSDT"}
    assert s.urls == ["http://127.0.0.1:9090/api/coins"]


def test_client_old_shadow_without_endpoint_is_unknown() -> None:
    """A Shadow that predates /api/coins answers 404 -> None, not empty."""
    s = _FakeSession(_FakeResp(404, {"error": "not found"}))
    assert _run(_svc(s).get_tradeable_symbols()) is None


@pytest.mark.parametrize("body", [
    {}, {"symbols": []}, {"symbols": "BTCUSDT"}, {"symbols": None}, ["BTCUSDT"],
])
def test_client_malformed_or_empty_body_is_unknown(body) -> None:
    s = _FakeSession(_FakeResp(200, body))
    assert _run(_svc(s).get_tradeable_symbols()) is None


def test_client_connection_error_is_unknown() -> None:
    import aiohttp

    s = _FakeSession(exc=aiohttp.ClientConnectionError("refused"))
    assert _run(_svc(s).get_tradeable_symbols()) is None


# ── Selector: filter applied BEFORE ranking ──────────────────────────────

_NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)

# 20 candidates / target 10 (the settings validator requires a universe of at
# least 10). Coins differ in daily range so scores differ; the ranking is NOT
# hard-coded here — every test derives the unrestricted baseline from the
# function under test, so the tests pin behaviour, not a scoring formula.
SYMS = [f"P{i:02d}USDT" for i in range(20)]
TARGET = 10


def _ticker(sym: str) -> Ticker:
    return Ticker(
        symbol=sym, last_price=10.0, bid=9.999, ask=10.001,
        high_24h=10.5, low_24h=9.5, volume_24h=50_000_000.0, change_24h_pct=2.0,
    )


def _trending_days(sym: str) -> list[OHLCV]:
    """8-day clean uptrend (high directionality -> tier 'eligible'), with a
    per-coin daily range so coins score differently."""
    k = int(sym[1:3])
    out = []
    for i in range(8):
        base = 10.0 + i * 0.5
        out.append(OHLCV(
            symbol=sym, timeframe=TimeFrame.D1,
            timestamp=_NOW - timedelta(days=8 - i),
            open=base, high=base + 0.55 + k * 0.03, low=base - 0.05,
            close=base + 0.5, volume=1000.0, turnover=10_000_000.0,
        ))
    return out


async def _fetch_daily(sym: str):
    return _trending_days(sym)


def _settings(**kw) -> UniverseRefreshSettings:
    base = dict(
        enabled=True, target_universe_size=TARGET, min_universe_size=TARGET,
        shortlist_size=50, oi_enabled=False, exclude_symbols=[],
    )
    base.update(kw)
    return UniverseRefreshSettings(**base)


def _select(tradeable=None, force_keep=None, **kw):
    return _run(select_universe(
        [_ticker(s) for s in SYMS], _settings(**kw),
        fetch_daily=_fetch_daily, force_keep=force_keep, tradeable=tradeable,
    ))


def _baseline() -> set[str]:
    """What an unrestricted run selects (the 10 best-scoring of the 20)."""
    return set(_select(tradeable=None).selected)


def test_fixture_is_meaningful() -> None:
    base = _baseline()
    assert len(base) == TARGET and len(set(SYMS) - base) == TARGET


def test_selector_without_restriction_reports_nothing_dropped() -> None:
    res = _select(tradeable=None)
    assert res.dropped_untradeable == 0
    assert len(res.selected) == TARGET


def test_selector_empty_set_means_unknown_not_nothing() -> None:
    """Fail open: an empty set must not empty the universe — it behaves
    exactly like no restriction at all."""
    res = _select(tradeable=set())
    assert res.dropped_untradeable == 0
    assert set(res.selected) == _baseline()


def test_selector_selects_only_fillable_coins() -> None:
    base = sorted(_baseline())
    removed = set(base[:4])                       # 4 would-be winners not fillable
    fillable = set(SYMS) - removed
    res = _select(tradeable=fillable)
    assert set(res.selected) <= fillable
    assert not (set(res.selected) & removed)
    assert res.dropped_untradeable == 4
    assert len(res.selected) == TARGET            # refilled from the next-best


def test_selector_fills_target_from_fillable_not_pruned_afterwards() -> None:
    """The point of filtering in pass one. Make the fillable set EXACTLY the
    ten coins the unrestricted run did NOT pick. Filtering before ranking fills
    the whole 10-slot universe from them; pruning the finished top-10 instead
    would leave it EMPTY — how a post-hoc filter would have halved the live
    universe (25 of 50 watch-list coins were unfillable)."""
    fillable = set(SYMS) - _baseline()
    res = _select(tradeable=fillable)
    assert set(res.selected) == fillable
    assert len(res.selected) == TARGET
    assert res.dropped_untradeable == TARGET


def test_selector_force_keep_exempt_from_filter() -> None:
    """An open position is already filled — it must stay in the universe
    even if it is not in the exchange's tracked set."""
    held = sorted(_baseline())[0]
    fillable = set(SYMS) - _baseline()
    res = _select(tradeable=fillable, force_keep={held})
    assert held in res.selected
    assert held in res.forced_kept


# ── Settings ─────────────────────────────────────────────────────────────

def test_setting_defaults_on_and_loads_from_config() -> None:
    from src.config.settings import _build_universe_refresh

    assert UniverseRefreshSettings().require_exchange_tradeable is True
    assert _build_universe_refresh({}).require_exchange_tradeable is True
    assert _build_universe_refresh(
        {"require_exchange_tradeable": False},
    ).require_exchange_tradeable is False


# ── Shadow endpoint: GET /api/coins ──────────────────────────────────────
# shadow/ is a separate package whose top-level name (``src``) collides with
# the bot's, so the handler module is loaded BY PATH with its one internal
# import (the logger) stubbed. monkeypatch restores sys.modules afterwards.

def _load_shadow_api(monkeypatch):
    import importlib.util
    import pathlib
    import sys

    stub = SimpleNamespace(get_logger=lambda name: SimpleNamespace(
        info=lambda *a, **k: None, error=lambda *a, **k: None,
        warning=lambda *a, **k: None, debug=lambda *a, **k: None,
    ))
    monkeypatch.setitem(sys.modules, "src.utils.logging", stub)
    path = pathlib.Path(__file__).resolve().parents[1] / "shadow/src/api/shadow_client.py"
    spec = importlib.util.spec_from_file_location("shadow_client_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeShadowDb:
    def __init__(self, rows=None, exc=None):
        self._rows, self._exc, self.queries = rows or [], exc, []

    async def fetch_all(self, sql, params=()):
        self.queries.append(sql)
        if self._exc:
            raise self._exc
        return self._rows


def _call_coins(mod, db):
    import json

    resp = _run(mod.handle_get_coins(SimpleNamespace(app={"db": db})))
    return resp.status, json.loads(resp.text)


def test_endpoint_returns_active_tracked_symbols(monkeypatch) -> None:
    mod = _load_shadow_api(monkeypatch)
    db = _FakeShadowDb([{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}])
    status, body = _call_coins(mod, db)
    assert status == 200
    assert body == {"symbols": ["BTCUSDT", "ETHUSDT"], "count": 2}


def test_endpoint_uses_the_same_predicate_as_order_placement(monkeypatch) -> None:
    """It must read what OrderEngine.place_order enforces first —
    ``tracked_coins WHERE is_active = 1`` — so an answer can never disagree
    with a placement."""
    mod = _load_shadow_api(monkeypatch)
    db = _FakeShadowDb([{"symbol": "BTCUSDT"}])
    _call_coins(mod, db)
    sql = " ".join(db.queries[0].split()).lower()
    assert "from tracked_coins" in sql and "is_active = 1" in sql

    import pathlib
    engine = (pathlib.Path(__file__).resolve().parents[1]
              / "shadow/src/exchange/order_engine.py").read_text()
    assert "FROM tracked_coins WHERE symbol = ? AND is_active = 1" in engine


def test_endpoint_db_error_is_500_not_a_crash(monkeypatch) -> None:
    mod = _load_shadow_api(monkeypatch)
    status, body = _call_coins(mod, _FakeShadowDb(exc=RuntimeError("db locked")))
    assert status == 500
    assert "db locked" in body["error"]


def test_endpoint_route_is_registered() -> None:
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "shadow/src/api/shadow_client.py").read_text()
    assert 'app.router.add_get("/api/coins", handle_get_coins)' in src


# ── Execution pre-flight (StrategyWorker._execute_claude_trade) ──────────

def _worker(tradeability, *, flag=True):
    from src.workers.strategy_worker import StrategyWorker

    w = StrategyWorker.__new__(StrategyWorker)
    w.settings = SimpleNamespace(
        universe=SimpleNamespace(
            refresh=SimpleNamespace(require_exchange_tradeable=flag),
        ),
    )
    w.services = {"exchange_tradeability": tradeability} if tradeability else {}
    return w


class _StubTradeability:
    def __init__(self, answer):
        self._answer, self.asked = answer, []

    async def is_tradeable(self, symbol):
        self.asked.append(symbol)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


_TRADE = {"symbol": "CHIPUSDT", "direction": "Buy", "leverage": 3}


def _exec(w):
    return _run(w._execute_claude_trade(dict(_TRADE), set(), plan=None))


def test_preflight_skips_untradeable_symbol_with_clear_reason() -> None:
    stub = _StubTradeability(False)
    assert _exec(_worker(stub)) == (False, "symbol_not_tradeable")
    assert stub.asked == ["CHIPUSDT"]


@pytest.mark.parametrize("answer", [True, None, RuntimeError("boom")])
def test_preflight_fails_open_for_fillable_unknown_or_erroring(answer) -> None:
    """Only an explicit False skips. True proceeds; None (unknown) and a
    lookup error must fail OPEN — i.e. never yield symbol_not_tradeable.
    (Later stages need services this stub lacks and may raise; reaching them
    at all is the proof the pre-flight did not block.)"""
    try:
        result = _exec(_worker(_StubTradeability(answer)))
    except Exception:
        return
    assert result != (False, "symbol_not_tradeable")


def test_preflight_off_when_flag_disabled() -> None:
    stub = _StubTradeability(False)
    try:
        result = _exec(_worker(stub, flag=False))
    except Exception:
        result = None
    assert result != (False, "symbol_not_tradeable")
    assert stub.asked == []  # never even consulted


def test_preflight_absent_service_is_a_noop() -> None:
    try:
        result = _exec(_worker(None))
    except Exception:
        result = None
    assert result != (False, "symbol_not_tradeable")


# ── Scanner candidate exclusion (_tick_briefing_mode) ────────────────────

def test_scanner_exclusion_source_is_wired_and_guarded() -> None:
    """The scanner hold-out lives inside a large coroutine that needs the whole
    scanner stack to run, so pin its contract structurally: it is gated by the
    same flag, only ever uses a non-empty fillable set, never removes an open
    position (protected), and precedes the scoring loop."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src/workers/scanner_worker.py").read_text()
    i = src.index("SCANNER_UNTRADEABLE_EXCLUDED")
    block = src[i - 1800: i + 300]
    assert "require_exchange_tradeable" in block
    assert "if _fillable:" in block                        # empty/None -> no filter
    assert "c not in protected and c not in _fillable" in block
    assert i < src.index("for coin in all_symbols:")       # before the scoring loop
