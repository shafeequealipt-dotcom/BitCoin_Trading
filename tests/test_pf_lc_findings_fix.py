"""Regression tests for the PF/LC findings fix (Findings 8, 4, 5, 6, 2) and the
cross-check follow-ups (Finding 8 de-dup, Finding 6 rung-immunity, Finding 5
buffer clamp).

These bind the REAL ProfitSniper / ThesisManager methods to minimal stubs so the
production logic is exercised without standing up the full DI container — the
same pattern the project's other targeted sniper tests use. End-to-end DI/data-
flow through the real SLGateway, TimeDial, ta_cache->engine->DB and ThesisManager
is covered separately by the pipeline check.
"""
from __future__ import annotations

import sqlite3
import sys
from types import SimpleNamespace

import pytest

from src.workers.profit_sniper import ProfitSniper
from src.core.thesis_manager import ThesisManager
from src.config.settings import Settings


# ───────────────────────────── shared helpers ─────────────────────────────

def _pf_lc_stub():
    """Minimal object exposing the attributes the bound sniper methods read."""
    s = SimpleNamespace()
    cfg = Settings.load(config_path="config.toml")
    s._pf = cfg.profit_fetching
    s._lc = cfg.loss_cutting
    s._atr_cache = {}
    s._atr_last_good = {}
    s._last_breakeven_floor_logged = {}
    s.layer4_protection = None
    return s


_THESIS_SCHEMA = """CREATE TABLE trade_thesis(
  id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT, entry_price REAL,
  stop_loss_price REAL, take_profit_price REAL, size_usd REAL, leverage INT,
  max_hold_minutes INT, trailing_activation_pct REAL, thesis TEXT,
  market_context TEXT, strategy_hints TEXT, consensus TEXT, opened_at TEXT,
  exchange_mode TEXT, apex_flipped INT, apex_original_direction TEXT,
  apex_reason TEXT, xray_flip_source TEXT, xray_flip_ratio REAL,
  xray_flip_rr_long REAL, xray_flip_rr_short REAL, thesis_invalidation TEXT,
  thesis_source TEXT, thesis_snapshot TEXT, thesis_state TEXT, order_id TEXT,
  status TEXT, closed_at TEXT, close_price REAL, actual_pnl_pct REAL,
  actual_pnl_usd REAL, close_reason TEXT, lesson TEXT)"""


class _ThesisDB:
    """Async DB shim over sqlite matching ThesisManager's db usage."""
    def __init__(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.execute(_THESIS_SCHEMA)

    async def fetch_all(self, sql, params=()):
        return self.c.execute(sql, params).fetchall()

    async def execute(self, sql, params=()):
        cur = self.c.execute(sql, params)
        self.c.commit()
        return cur

    def insert_open(self, _id, symbol, entry, order_id, opened_at):
        self.c.execute(
            "INSERT INTO trade_thesis(id,symbol,entry_price,order_id,opened_at,status) "
            "VALUES(?,?,?,?,?, 'open')", (_id, symbol, entry, order_id, opened_at))
        self.c.commit()

    def pnl(self, _id):
        return self.c.execute(
            "SELECT actual_pnl_usd FROM trade_thesis WHERE id=?", (_id,)).fetchone()[0]


# ───────────────────────── Finding 8 — zombie PnL ─────────────────────────

@pytest.mark.asyncio
async def test_f8_books_true_pnl_on_entry_match():
    db = _ThesisDB()
    db.insert_open(1, "SOLUSDT", 80.32, "oid-1", "2026-06-01 13:43:00")

    class Svc:
        async def get_last_close(self, sym):
            return {"entry_price": 80.32, "exit_price": 78.1,
                    "net_pnl_usd": -45.20, "net_pnl_pct": -2.76}

    tm = ThesisManager(db)
    tm.attach_position_service(Svc())
    n = await tm.reconcile_with_shadow(set())
    assert n == 1
    assert abs(db.pnl(1) - (-45.20)) < 1e-6  # true PnL, not zero


@pytest.mark.asyncio
async def test_f8_entry_mismatch_books_zero():
    """A stale same-symbol thesis (entry far from the recent close) must NOT
    be booked with that close's PnL."""
    db = _ThesisDB()
    db.insert_open(1, "SOLUSDT", 89.14, "oid-stale", "2026-05-06 16:58:00")

    class Svc:
        async def get_last_close(self, sym):
            return {"entry_price": 80.32, "exit_price": 78.1,
                    "net_pnl_usd": -45.20, "net_pnl_pct": -2.76}

    tm = ThesisManager(db)
    tm.attach_position_service(Svc())
    await tm.reconcile_with_shadow(set())
    assert db.pnl(1) == 0.0  # mismatch -> zero, not mis-booked


@pytest.mark.asyncio
async def test_f8_no_service_books_zero_legacy_safety_net():
    db = _ThesisDB()
    db.insert_open(1, "BTCUSDT", 50000.0, "oid-x", "2026-06-01 13:00:00")
    tm = ThesisManager(db)  # never attach_position_service
    n = await tm.reconcile_with_shadow(set())
    assert n == 1 and db.pnl(1) == 0.0


@pytest.mark.asyncio
async def test_f8_no_double_count_two_same_symbol_orphans():
    """Cross-check follow-up: two same-symbol orphans both within 0.5% of the
    single most-recent close must NOT both book it."""
    db = _ThesisDB()
    db.insert_open(1, "SOLUSDT", 80.32, "oid-new", "2026-06-01 13:43:00")
    db.insert_open(2, "SOLUSDT", 80.30, "oid-old", "2026-06-01 09:00:00")

    class Svc:
        async def get_last_close(self, sym):
            return {"entry_price": 80.31, "exit_price": 78.1,
                    "net_pnl_usd": -45.20, "net_pnl_pct": -2.76}

    tm = ThesisManager(db)
    tm.attach_position_service(Svc())
    await tm.reconcile_with_shadow(set())
    booked = [db.pnl(1), db.pnl(2)]
    # exactly one claimed the close; total equals the single truth, not 2x
    assert sum(1 for v in booked if abs(v - (-45.20)) < 1e-6) == 1
    assert abs((booked[0] + booked[1]) - (-45.20)) < 1e-6


# ───────────────────────── Finding 4 — ATR warm ──────────────────────────

class _ScriptedTACache:
    def __init__(self, script):
        self.script = list(script)
        self.i = 0

    async def analyze(self, symbol=None, timeframe=None):
        kind, val = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        if kind == "raise":
            raise RuntimeError("DataError: Need at least 50 candles, got 36")
        return {"volatility": {"atr_14": val}}


@pytest.mark.asyncio
async def test_f4_cold_read_serves_warm_not_zero():
    s = _pf_lc_stub()
    s.ta_cache = _ScriptedTACache([("raise", 0)] * 5)
    s._atr_last_good["ONDOUSDT"] = 0.002026  # seeded at open
    s._atr_cache["ONDOUSDT"] = (0.002026, 0.0)  # expired -> recompute -> DataError
    v = await ProfitSniper._get_current_atr(s, "ONDOUSDT")
    assert abs(v - 0.002026) < 1e-9  # warm, not zero


@pytest.mark.asyncio
async def test_f4_live_recompute_updates_last_good():
    s = _pf_lc_stub()
    s.ta_cache = _ScriptedTACache([("value", 0.0025)])
    s._atr_cache["ONDOUSDT"] = (0.001, 0.0)
    v = await ProfitSniper._get_current_atr(s, "ONDOUSDT")
    assert abs(v - 0.0025) < 1e-9
    assert abs(s._atr_last_good["ONDOUSDT"] - 0.0025) < 1e-9


@pytest.mark.asyncio
async def test_f4_never_warmed_returns_zero_for_fallback():
    s = _pf_lc_stub()
    s.ta_cache = _ScriptedTACache([("raise", 0)])
    v = await ProfitSniper._get_current_atr(s, "NEWUSDT")
    assert v == 0.0  # _pf_effective_atr insurance engages downstream


# ───────────────────────── Finding 5 — cap buffer ────────────────────────

def test_f5_cap_stop_inside_ceiling():
    s = _pf_lc_stub()
    s._lc.cap_slippage_buffer_pct = 0.5
    raw = 75.0 / 21.13
    d = ProfitSniper._lc_cap_stop_distance(s, 75.0, 21.13)
    assert d < raw and (d * 21.13) < 75.0


def test_f5_buffer_zero_is_noop():
    s = _pf_lc_stub()
    s._lc.cap_slippage_buffer_pct = 0.0
    assert abs(ProfitSniper._lc_cap_stop_distance(s, 75.0, 21.13) - 75.0 / 21.13) < 1e-9


def test_f5_buffer_clamped_no_loosen_and_no_flip():
    s = _pf_lc_stub()
    raw = 75.0 / 21.13
    s._lc.cap_slippage_buffer_pct = -5.0  # negative -> clamped to 0 (no loosen)
    assert abs(ProfitSniper._lc_cap_stop_distance(s, 75.0, 21.13) - raw) < 1e-9
    s._lc.cap_slippage_buffer_pct = 150.0  # >100 -> clamped, stays strictly > 0
    d = ProfitSniper._lc_cap_stop_distance(s, 75.0, 21.13)
    assert 0.0 < d < raw  # never zero/negative


def test_f5_zero_cap_or_size_returns_zero():
    s = _pf_lc_stub()
    assert ProfitSniper._lc_cap_stop_distance(s, 0.0, 21.13) == 0.0
    assert ProfitSniper._lc_cap_stop_distance(s, 75.0, 0.0) == 0.0


# ─────────────────── Finding N — net-aware hard cap ───────────────────────
# The cap bounds the GROSS price loss; the round-trip taker fee pushes the
# realized NET past the ceiling (NEAR gross -74.69 ~= $75 cap, net -81.24). The
# net-aware budget = gross cap - round-trip fee, so realized net = gross + fee
# lands at or under the ceiling. It TIGHTENS gross and never loosens.


def test_n_net_cap_subtracts_round_trip_fee():
    s = _pf_lc_stub()
    s._lc.cap_round_trip_fee_pct = 0.11
    # NEAR-like: gross cap $75 (ceiling binds), notional ~$5996
    net = ProfitSniper._lc_net_cap_dollars(s, 75.0, 5996.0)
    assert abs(net - (75.0 - 5996.0 * 0.11 / 100.0)) < 1e-9
    # worst realized net = net budget + the same fee == the ceiling
    assert abs((net + 5996.0 * 0.11 / 100.0) - 75.0) < 1e-9


def test_n_net_cap_off_switch_restores_gross():
    s = _pf_lc_stub()
    s._lc.cap_round_trip_fee_pct = 0.0
    assert ProfitSniper._lc_net_cap_dollars(s, 75.0, 5996.0) == 75.0


def test_n_net_cap_tighten_only_and_floored():
    s = _pf_lc_stub()
    s._lc.cap_round_trip_fee_pct = 0.11
    # never widens: net < gross whenever a fee applies
    assert ProfitSniper._lc_net_cap_dollars(s, 75.0, 5000.0) < 75.0
    # never goes negative even if the fee would exceed the cap
    s._lc.cap_round_trip_fee_pct = 99.0
    assert ProfitSniper._lc_net_cap_dollars(s, 75.0, 5000.0) == 0.0
    # zero/negative inputs are safe no-ops
    assert ProfitSniper._lc_net_cap_dollars(s, 0.0, 5000.0) == 0.0


# ───────────────────────── Finding 6 — breakeven floor ───────────────────

def _state(entry, direction, peak, sym="HBARUSDT"):
    return SimpleNamespace(entry_price=entry, direction=direction,
                           peak_pnl_pct=peak, symbol=sym)


def _dialed(step=0.6, offset=0.3):
    return SimpleNamespace(ladder_step_pct=step, lock_offset_pct=offset)


def test_f6_modest_peak_locks_breakeven():
    s = _pf_lc_stub()
    s._pf.min_profit_to_arm_ladder_pct = 0.5
    s._pf.ladder_breakeven_lock_pct = 0.05
    r = ProfitSniper._compute_ladder_floor(s, _state(100.0, "Buy", 0.59), _dialed(), 0.0)
    assert r.should_apply and r.breakeven_floor
    assert r.ladder_stop_price > 100.0  # above entry = locked profit (long)


def test_f6_real_rung_immune_to_floor():
    """A positive crossed-rung lock must be left untouched even if be_lock is
    mistuned above it (cross-check follow-up: gated on lock_pct <= 0)."""
    s = _pf_lc_stub()
    s._pf.min_profit_to_arm_ladder_pct = 0.5
    s._pf.ladder_breakeven_lock_pct = 0.9  # mistuned ABOVE the 0.3 rung lock
    r = ProfitSniper._compute_ladder_floor(s, _state(100.0, "Buy", 0.65), _dialed(), 0.0)
    # level=0.6, lock=0.6-0.3=0.3 ; floor must NOT raise it to 0.9
    assert not r.breakeven_floor
    assert abs(r.lock_pct - 0.3) < 1e-9


def test_f6_off_switch_restores_old_behavior():
    s = _pf_lc_stub()
    s._pf.min_profit_to_arm_ladder_pct = 0.5
    s._pf.ladder_breakeven_lock_pct = 0.0  # disabled
    r = ProfitSniper._compute_ladder_floor(s, _state(100.0, "Buy", 0.59), _dialed(), 0.0)
    assert not r.should_apply and not r.breakeven_floor


def test_f6_peak_below_arm_no_lock():
    # Issue 1 (CALL_A exploit/fetch, 2026-06-05) — the floor now arms at the
    # decoupled micro_floor_arm_pct, NOT min_profit_to_arm_ladder_pct. The F6
    # invariant (no lock until the arming threshold is reached) is preserved
    # under the micro arm: a peak below the micro arm still produces no lock.
    s = _pf_lc_stub()
    s._pf.min_profit_to_arm_ladder_pct = 0.5
    s._pf.micro_floor_arm_pct = 0.10
    s._pf.ladder_breakeven_lock_pct = 0.05
    r = ProfitSniper._compute_ladder_floor(s, _state(100.0, "Buy", 0.05), _dialed(), 0.0)
    assert not r.should_apply


def test_issue1_micro_floor_captures_sub_graduation_green():
    """Issue 1 — a peak in [micro_arm, graduation_arm) now ARMS the
    breakeven/dead-band floor and locks a stop above entry (capturing the
    small green that previously round-tripped), while the graduation arm is
    left at the higher value so loss-cutting authority is retained until +0.2%
    (graduation is tested separately at the call site)."""
    s = _pf_lc_stub()
    s._pf.min_profit_to_arm_ladder_pct = 0.5   # graduation arm
    s._pf.micro_floor_arm_pct = 0.10           # floor arms here
    s._pf.ladder_breakeven_lock_pct = 0.05
    # peak=0.30 is below graduation (0.5) but above the micro arm (0.10).
    r = ProfitSniper._compute_ladder_floor(s, _state(100.0, "Buy", 0.30), _dialed(), 0.0)
    assert r.should_apply and r.breakeven_floor
    assert r.ladder_stop_price > 100.0  # green locked above entry (long)
    # micro arm is bounded to never exceed the graduation arm.
    s._pf.micro_floor_arm_pct = 0.9
    r2 = ProfitSniper._compute_ladder_floor(s, _state(100.0, "Buy", 0.55), _dialed(), 0.0)
    assert r2.should_apply  # floor_arm capped at graduation 0.5, so 0.55 arms


# ───────────────────── Finding A — fee-aware breakeven lock ───────────────
# A gross-positive "breakeven" lock that does not clear the round-trip taker fee
# (~0.11%) books a NET loss after fees. When a sub-fee floor would lock AND the
# peak cleared the fee hurdle, the floor is raised to the fee-clearing level
# (net-breakeven); otherwise the existing floor is kept (cap-at-fee, not removed)
# and a real step lock (>= ~0.3%) is untouched.


def _pf_fee(s, fee=0.13, giveback=0.1, micro=0.10, grad=0.2, be=0.05):
    s._pf.min_profit_to_arm_ladder_pct = grad
    s._pf.micro_floor_arm_pct = micro
    s._pf.ladder_breakeven_lock_pct = be
    s._pf.ladder_deadband_giveback_pct = giveback
    s._pf.ladder_lock_fee_clearance_pct = fee
    return s


def test_a_fee_aware_lifts_sub_fee_floor_when_peak_clears():
    """peak 0.18% cleared the fee (0.13%) but the dead-band floor (peak-giveback
    = 0.08%) is sub-fee -> lift to 0.13% so the lock is net-breakeven."""
    s = _pf_fee(_pf_lc_stub())
    r = ProfitSniper._compute_ladder_floor(s, _state(100.0, "Buy", 0.18), _dialed(), 0.0)
    assert r.should_apply and r.breakeven_floor
    assert abs(r.lock_pct - 0.13) < 1e-9          # raised to clear the fee
    assert r.ladder_stop_price > 100.13 - 1e-6    # net-breakeven floor (long)


def test_a_fee_aware_keeps_floor_when_peak_below_fee():
    """peak 0.12% never cleared the fee -> no net-positive floor exists; keep the
    existing sub-fee floor (caps the loss near the fee) rather than riding."""
    s = _pf_fee(_pf_lc_stub())
    r = ProfitSniper._compute_ladder_floor(s, _state(100.0, "Buy", 0.12), _dialed(), 0.0)
    assert r.should_apply and r.breakeven_floor
    assert abs(r.lock_pct - 0.05) < 1e-9          # kept (max(be=0.05, 0.12-0.1=0.02))


def test_a_fee_aware_no_op_on_real_step_lock():
    """A crossed-rung step lock (level 0.6 - offset 0.3 = 0.3%) already clears the
    fee, so the fee-aware lift is a no-op."""
    s = _pf_fee(_pf_lc_stub())
    r = ProfitSniper._compute_ladder_floor(s, _state(100.0, "Buy", 0.65), _dialed(), 0.0)
    assert not r.breakeven_floor
    assert abs(r.lock_pct - 0.3) < 1e-9


def test_a_fee_aware_off_switch():
    """fee_clearance <= 0 restores the gross sub-fee floor (no lift)."""
    s = _pf_fee(_pf_lc_stub(), fee=0.0)
    r = ProfitSniper._compute_ladder_floor(s, _state(100.0, "Buy", 0.18), _dialed(), 0.0)
    assert r.should_apply and r.breakeven_floor
    assert abs(r.lock_pct - 0.08) < 1e-9          # max(be 0.05, 0.18-0.1=0.08), not lifted


# ───────────────────────── Finding 2 — veto budget ───────────────────────

@pytest.mark.asyncio
async def test_f2_veto_count_and_budget_observability():
    s = _pf_lc_stub()
    s._lc.stall_signs_of_life_profit_ratio = 0.25
    s._lc.stall_veto_budget_warn = 8
    state = SimpleNamespace(profit_ratio=0.30, peak_pnl_pct=0.10)  # building -> spared
    tracked = {}
    for _ in range(8):
        tracked["_lc_veto_log_ts"] = 0.0  # force the per-minute log each call
        spared = await ProfitSniper._lc_stall_decision(
            s, "ENAUSDT", SimpleNamespace(), tracked, state,
            pnl_pct=-0.20, is_long=True, age_fraction=0.70, stall_min_age_fraction=0.55)
        assert spared is False  # veto spares -> no cut (late-bloomer protection)
    assert tracked.get("_lc_veto_count") == 8


def test_f2_provisional_values_loaded():
    cfg = Settings.load(config_path="config.toml")
    assert cfg.loss_cutting.stall_signs_of_life_profit_ratio == 0.25
    assert cfg.loss_cutting.recovery_bounce_trail_atr_loss_side == 0.40
    assert cfg.loss_cutting.stall_veto_budget_warn == 8
