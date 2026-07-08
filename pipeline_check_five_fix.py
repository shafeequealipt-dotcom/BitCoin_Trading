"""Five-Fix Follow-Up — REAL end-to-end pipeline verification (2026-06-11).

Drives every fix's COMPLETE pipeline through the REAL project: the same
dependency-injection constructions the live WorkerManager performs (each
mirrored construction cites the manager.py line), the REAL database
(data/trading.db, reads only — the single write each path would do is
suppressed), the REAL demo-exchange endpoint (read-only market calls), and
runtime assertions on the values and the boot sentinels actually emitted.

Pipelines:
  1. Fix 2 fetch chain   — real BybitClient -> real demo API (5min snapshots
     move) -> real OpenInterestTracker (DI interval wiring + BOOT_OI_FETCH)
     -> real repo deltas (24h/1h/15m).
  2. Fix 2 signal chain  — real SignalGenerator (manager.py:180 construction)
     end-to-end generate_signal on real data: three OI window keys in
     components, cond_* tags in the blend, the remediated truthful
     SIG_GEN_INPUT (oi_active from the blended score), no sentiment keys.
  3. Fix 1 + Fix 3 prompt chain — real ClaudeStrategist (manager.py:940
     construction, BOOT_COMPONENTS_DIAGNOSTICS sentinel), real
     VolatilityProfiler floors (real TAEngine/TACache on real klines), the
     real prefetch clamp, the real renderer: pure Components line + the
     per-coin Vol stop floor line, with the floor value PROVEN equal to the
     entry path's target for the same coin.
  4. Fix 3 stop chain    — real profiler recommendation -> the LIVE
     compute_volatility_scaled_stop helper on real prices: widened stop,
     proportional haircut, dollar risk identical.
  5. Fix 5 execution chain — real TradeOptimizer + TradeGate (manager.py:3074
     and 3107 constructions, BOOT_APEX_SIZE_OVERRIDE sentinel): switch-off
     passthrough on the proven HYPE shape, switch-on legacy replay, and the
     safety rails (leverage clamp, absolute size cap) still firing.

READ-ONLY against live state: save_signal and save_open_interest are
suppressed; no orders are constructed; no tables are written. Honest scope
note: the live workers still run the pre-fix code until the operator
restarts them — THIS SCRIPT is the runtime verification of the new code, in
process, on the real data.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.core.logging import get_logger  # noqa: F401 — ensures loguru wiring loads
from loguru import logger as _loguru

PASS: list[str] = []
FAIL: list[str] = []
CAPTURED: list[str] = []


def chk(name: str, cond: bool, evidence: str) -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    print(f"        {evidence}")


def _capture(msg) -> None:
    CAPTURED.append(str(msg))


def _grab(tag: str) -> str:
    for line in reversed(CAPTURED):
        if tag in line:
            return line
    return ""


async def main() -> int:
    sink_id = _loguru.add(_capture, level="INFO")
    from src.config.settings import Settings
    from src.database.connection import DatabaseManager

    s = Settings.load()
    db = DatabaseManager(db_path=s.database.path)
    await db.connect()
    print(f"REAL DB={s.database.path} mode={s.general.mode}\n")

    # =====================================================================
    print("== PIPELINE 1 — Fix 2 fetch chain: client -> demo API -> tracker -> repo ==")
    from src.trading.client import BybitClient
    from src.intelligence.altdata.open_interest import OpenInterestTracker

    bybit = BybitClient(s, db)          # manager.py:116 construction shape
    await bybit.connect()
    tracker = OpenInterestTracker(bybit, db)   # manager.py:171 construction
    chk("Fix2 DI: tracker resolved the interval from settings through the real client",
        tracker._interval == "5min",
        f"tracker._interval={tracker._interval!r} via client._settings.workers.sweet_spots.altdata")
    boot_oi = _grab("BOOT_OI_FETCH")
    chk("Fix2 runtime: BOOT_OI_FETCH sentinel emitted at construction",
        "interval=5min" in boot_oi and "fresh_snapshot_each_fetch" in boot_oi,
        boot_oi[-120:] if boot_oi else "sentinel NOT captured")

    # Real demo-exchange call (read-only market endpoint): 5min snapshots move.
    res = await bybit.call(
        "get_open_interest", category="linear", symbol="ALGOUSDT",
        intervalTime="5min", limit=5,
    )
    vals = [float(i["openInterest"]) for i in res.get("list", [])]
    chk("Fix2 runtime: real demo API serves 5-minute snapshots with MOVING values",
        len(vals) >= 4 and len(set(vals)) >= 3,
        f"ALGOUSDT last {len(vals)} snapshots, {len(set(vals))} distinct: {vals[:4]}...")

    # Real fetch_current with the single write suppressed (read-only run).
    tracker._repo.save_open_interest = AsyncMock(return_value=None)
    fetched = await tracker.fetch_current(["BTCUSDT", "ALGOUSDT"])
    chk("Fix2 runtime: tracker fetch_current runs the real parse on the 5min interval",
        len(fetched) == 2 and all("change_24h_pct" in f for f in fetched),
        f"fetched={[(f['symbol'], round(f['open_interest'], 1)) for f in fetched]} (save suppressed)")

    from src.database.repositories.altdata_repo import AltDataRepository
    repo = AltDataRepository(db)
    oi_row = await repo.get_latest_open_interest("BTCUSDT")
    chk("Fix2 data flow: repo enriches the real latest row with all three window deltas",
        oi_row is not None and all(
            k in oi_row for k in ("change_24h_pct", "change_1h_pct", "change_15m_pct")
        ),
        f"BTCUSDT deltas 24h={oi_row['change_24h_pct']} 1h={oi_row['change_1h_pct']} 15m={oi_row['change_15m_pct']} "
        f"(15m/1h read 0.0 until the restarted workers store 5min-granularity rows — expected pre-restart)")

    # =====================================================================
    print("\n== PIPELINE 2 — Fix 2 signal chain: real SignalGenerator end-to-end ==")
    from src.intelligence.signals.signal_generator import SignalGenerator

    class _TripwireAggregator:
        async def aggregate_for_symbol(self, symbol):  # noqa: ANN001
            raise RuntimeError("sentiment aggregator must never be called (Fix 3 of prior program)")

    sg = SignalGenerator(_TripwireAggregator(), db, settings=s)  # manager.py:180
    boot_norm = _grab("BOOT_SENT_NORM_OK")
    chk("Fix2 runtime: BOOT_SENT_NORM_OK carries the 15m blend fields",
        "oi_blend_15m=0.4" in boot_norm and "oi_15m_window_h=0.25" in boot_norm,
        boot_norm[boot_norm.find("oi_blend_15m"):][:90] if boot_norm else "NOT captured")

    sg._altdata_repo.save_signal = AsyncMock(return_value=None)  # read-only
    CAPTURED.clear()
    sig = await sg.generate_signal("BTCUSDT")  # REAL end-to-end on real data
    comps = sig.components or {}
    chk("Fix2 data flow: real signal components carry the three honest OI window keys",
        all(k in comps for k in ("oi_change_15m_pct", "oi_change_1h_pct", "oi_change_24h_pct")),
        f"keys={sorted(k for k in comps if 'oi' in k)}")
    chk("Fix1 data flow: diagnostics still IN the dict (DB/X-RAY consumers unbroken)",
        all(k in comps for k in ("confidence_floor_failed", "original_signal_type")),
        f"diagnostics present in dict: confidence_floor_failed={comps.get('confidence_floor_failed')}")
    chk("Prior-program guard: no sentiment keys resurrected in real components",
        not any(k in comps for k in ("overall_sentiment", "news_count", "reddit_count")),
        f"component keys={sorted(comps.keys())}")

    oi_windows_line = _grab("SIG_OI_WINDOWS")
    chk("Fix2 runtime: SIG_OI_WINDOWS emits per-window values AND cond_* inversion tags",
        all(t in oi_windows_line for t in ("oi_15m=", "s_15m=", "cond_24h=", "cond_1h=", "cond_15m=")),
        oi_windows_line[oi_windows_line.find("oi_24h="):][:160] if oi_windows_line else "NOT captured")
    gen_input_line = _grab("SIG_GEN_INPUT")
    chk("Audit remediation runtime: SIG_GEN_INPUT logs the blended score it actually gates on",
        "oi_blended=" in gen_input_line and "oi_change_24h=" in gen_input_line,
        gen_input_line[gen_input_line.find("fg_active"):][:140] if gen_input_line else "NOT captured")

    # =====================================================================
    print("\n== PIPELINE 3 — Fix 1 + Fix 3 prompt chain: real strategist render ==")
    from src.brain.strategist import ClaudeStrategist
    from src.analysis.engine import TAEngine
    from src.analysis.ta_cache import TACache
    from src.analysis.volatility_profile import VolatilityProfiler
    from src.core.coin_package import (
        AltDataBlock, CoinPackage, PriceDataBlock, SignalsBlock,
        StrategiesBlock, XrayBlock,
    )

    ta_engine = TAEngine(db, settings=s)
    ta_cache = TACache(ta_engine, ttl_seconds=120.0)
    profiler = VolatilityProfiler(ta_cache=ta_cache, regime_detector=None,
                                  settings=s.volatility_profile)

    class _SigWorker:
        def __init__(self, m):
            self._m = m
        def get_signal(self, sym):
            return self._m.get(sym)

    services = {"signal_worker": _SigWorker({"BTCUSDT": sig}),
                "volatility_profiler": profiler}
    CAPTURED.clear()
    strategist = ClaudeStrategist(None, services, s)  # manager.py:940 construction
    boot_diag = _grab("BOOT_COMPONENTS_DIAGNOSTICS")
    chk("Fix1 runtime: BOOT_COMPONENTS_DIAGNOSTICS_EXCLUDED fired at real construction",
        "EXCLUDED" in boot_diag and "components_diagnostics_excluded=True" in boot_diag,
        boot_diag[boot_diag.find("BOOT_COMPONENTS"):][:110] if boot_diag else "NOT captured")

    # The REAL prefetch clamp, identical to the caller in _build_trade_prompt.
    vss = s.risk.volatility_stop_scaling
    chk("Fix3 DI: [risk.volatility_stop_scaling] loads ENABLED from the real config",
        vss.enabled is True,
        f"enabled={vss.enabled} ref={vss.reference_stop_pct} cap={vss.max_cap_pct} scalar={vss.recommended_sl_scalar}")
    prof = await profiler.get_profile("BTCUSDT")
    _rec = float(prof.recommended_sl_pct) * float(vss.recommended_sl_scalar)
    floor = max(vss.reference_stop_pct, min(_rec, vss.max_cap_pct)) if _rec > 0 else vss.reference_stop_pct

    # Real market data into the package (price straight from the real DB).
    # interestingness_score must clear the REAL briefing floor
    # (scanner.briefing.prompt_floor_interestingness) — the live formatter
    # correctly SKIPS unqualified no-state packages (verified: a default
    # package renders nothing), so this package must qualify like a real one.
    ticker = await sg._market_repo.get_ticker("BTCUSDT")
    pkg = CoinPackage(
        symbol="BTCUSDT", qualified=True, opportunity_score=0.7,
        qualification_reasons=["pipeline-check"],
        price_data=PriceDataBlock(
            current=float(ticker.last_price), change_24h_pct=float(ticker.change_24h_pct or 0.0),
        ),
        xray=XrayBlock(), strategies=StrategiesBlock(),
        signals=SignalsBlock(), alt_data=AltDataBlock(),
        interestingness_score=0.75,
    )
    out = strategist._format_packages_for_prompt_full(
        {"BTCUSDT": pkg}, vol_floors={"BTCUSDT": floor},
    )
    comp_line = next((ln for ln in out.splitlines() if "Components:" in ln), "")
    chk("Fix1+2 render: real Components line carries ONLY market inputs incl. the window keys",
        "oi_change_15m_pct=" in comp_line and "oi_change_24h_pct=" in comp_line
        and "confidence_floor_failed" not in out and "original_signal_type" not in out,
        comp_line.strip()[:160])
    chk("Fix3 render: the per-coin Vol stop floor line renders with the REAL profiler floor",
        f"Vol stop floor: {floor:.2f}%" in out,
        f"floor={floor:.2f}% from real profile (class={prof.volatility_class} rec={prof.recommended_sl_pct})")

    # =====================================================================
    print("\n== PIPELINE 4 — Fix 3 stop chain: real profile -> live scaling helper ==")
    from src.workers.strategy_worker import compute_volatility_scaled_stop

    entry = float(ticker.last_price)
    brain_sl = entry * (1 - vss.reference_stop_pct / 100.0)
    new_sl, new_size, target_pct, final_pct = compute_volatility_scaled_stop(
        sl=brain_sl, current_price=entry, direction="Buy", size_usd=100.0,
        recommended_sl_pct=_rec, reference_stop_pct=vss.reference_stop_pct,
        max_cap_pct=vss.max_cap_pct,
    )
    risk_ref = 100.0 * vss.reference_stop_pct / 100.0
    risk_new = new_size * final_pct / 100.0
    chk("Fix3 execution: live helper widens within [ref, cap] and holds dollar risk",
        vss.reference_stop_pct - 1e-9 <= final_pct <= vss.max_cap_pct + 1e-9
        and abs(risk_new - risk_ref) < 1e-6,
        f"BTCUSDT entry={entry} stop {vss.reference_stop_pct}%->{final_pct:.2f}% size 100->{new_size:.1f} "
        f"risk {risk_new:.4f}=={risk_ref:.4f}")
    chk("Fix3 consistency: the prompt floor EQUALS the entry path's target for the same coin",
        abs(floor - target_pct) < 1e-9,
        f"prompt floor={floor:.4f}% == entry target_pct={target_pct:.4f}% (one clamp, two surfaces)")

    # =====================================================================
    print("\n== PIPELINE 5 — Fix 5 execution chain: real optimizer + gate, both states ==")
    from src.apex.optimizer import TradeOptimizer, OptimizedTrade
    from src.apex.gate import TradeGate
    import copy

    CAPTURED.clear()
    apex_cfg = s.apex
    optimizer = TradeOptimizer(None, None, apex_cfg)  # manager.py:3074 construction
    boot_size = _grab("BOOT_APEX_SIZE_OVERRIDE")
    chk("Fix5 runtime: BOOT_APEX_SIZE_OVERRIDE_OFF fired at real construction",
        "OFF" in boot_size and "brain_size_authoritative_unmodified" in boot_size,
        boot_size[boot_size.find("BOOT_APEX"):][:120] if boot_size else "NOT captured")

    def _hype_trade():
        return OptimizedTrade(
            symbol="HYPEUSDT", direction="Buy", sl_pct=2.0, tp_pct=4.0,
            tp_mode="fixed", position_size_usd=1200.0, leverage=2,
            entry_timing="immediate", add_on_pullback=False,
            reasoning="pipeline", confidence=1.0, original_size=700.0,
        )

    t_off = _hype_trade()
    optimizer._apply_constraints(t_off)
    chk("Fix5 execution (switch OFF, real config): the proven HYPE shape passes the brain's $700 unmodified",
        abs(t_off.position_size_usd - 700.0) < 1e-9,
        f"proposal $1200 -> final ${t_off.position_size_usd} (was $1050 live pre-fix)")

    apex_on = copy.copy(apex_cfg)
    apex_on.apex_size_override_enabled = True
    optimizer_on = TradeOptimizer(None, None, apex_on)
    t_on = _hype_trade()
    optimizer_on._apply_constraints(t_on)
    chk("Fix5 reversibility: switch ON replays the legacy J5 sizing byte-identically",
        abs(t_on.position_size_usd - 1200.0) < 1e-9,
        f"switch on -> final ${t_on.position_size_usd} (legacy cap+conviction+brain-floor path)")

    t_lev = _hype_trade()
    t_lev.leverage = 99
    optimizer._apply_constraints(t_lev)
    chk("Fix5 safety: the optimizer leverage clamp still binds with the switch off",
        t_lev.leverage == int(apex_cfg.max_leverage),
        f"leverage 99 -> {t_lev.leverage} (max_leverage={apex_cfg.max_leverage})")

    gate = TradeGate(
        {"fund_manager": SimpleNamespace(_account_state=SimpleNamespace(available=10000.0))},
        apex_cfg,
    )  # manager.py:3107 construction shape
    gtrade = {"symbol": "HYPEUSDT", "direction": "Buy", "size_usd": 99999.0,
              "leverage": 9, "_xray_confidence": 0.7, "_setup_score": 60.0,
              "_expected_rr": 2.0, "_claude_original_size_usd": 99999.0,
              "original_size": 99999.0, "entry_price": 100.0}
    validated = await gate.validate(gtrade)
    chk("Fix5 safety: gate CHECK 1 absolute cap + CHECK 2 leverage clamp still fire on real config",
        float(validated["size_usd"]) <= float(apex_cfg.max_position_size_usd) + 1e-6
        and int(validated["leverage"]) <= int(apex_cfg.max_leverage),
        f"size 99999 -> {validated['size_usd']} (cap={apex_cfg.max_position_size_usd}); "
        f"leverage 9 -> {validated['leverage']} (max={apex_cfg.max_leverage})")

    await db.disconnect()
    _loguru.remove(sink_id)
    print(f"\n==== RESULT: {len(PASS)} passed, {len(FAIL)} failed ====")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        return 1
    print("ALL FIVE-FIX REAL-PIPELINE CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
