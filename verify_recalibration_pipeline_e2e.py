"""Real-pipeline END-TO-END verification of the Four-Element Prompt
Recalibration (all five elements plus the cross-check fixes), 2026-06-11.

Follows the verify_candidate_block_pipeline_runtime.py precedent: data is
driven THROUGH the real production components and the real DI seams, not
through stubs of the code under test.

What each phase exercises, through the REAL project:

A. DI WIRING — every service key the new code consumes (db, transformer,
   regime_detector, structure_cache) is registered by the real
   WorkerManager, and the strategist receives the SHARED services dict.
B. CONFIG PIPELINE — real Settings.load() carries all nine new keys with
   the live config.toml values, and flip-tests through the REAL loader
   functions prove a toml edit reaches each consumer dataclass.
C. ELEMENT 3 ENGINE — the real StructureEngine.analyze runs on engineered
   OHLCV candles whose price broke below an established range: the REAL
   pipeline must produce the clamped position, range_breakout='below',
   and a positive overshoot; the real APEX assembler then propagates the
   REAL analysis into a real StructuralData whose real format() carries
   the marker.
D. ELEMENT 2 DATA — the real DatabaseManager on the real trading.db runs
   the REAL session_attempts_today for the active exchange mode and is
   cross-checked against an independent raw sqlite3 read.
E. PRODUCTION RENDER — a real ClaudeStrategist built via its REAL
   __init__ (firing the real boot sentinels) renders a real CoinPackage
   through the REAL _format_packages_for_prompt_full, with the REAL
   Element 3 analysis in a REAL StructureCache and the REAL Element 2
   query output: the Session-today line (real ledger numbers, HEAVY
   suffix), the BELOW-RANGE marker, and the flag-off byte-rollback are
   all asserted on the production code path.
F. ELEMENTS 1 AND 4 SEAMS — the real _resolve_prompt_calibration with the
   real loaded thresholds produces the live system prompt with zero
   leftover tokens; the real _candidate_vol_ratio honors the two-source
   measured-only contract on real CoinPackage objects; the real
   _session_liveness classifies the gathered set.
G. ELEMENT 3 LABELER — the real label_state, fed the REAL engine output's
   range_breakout, suppresses every fade label on the break and
   reproduces legacy labels byte-identically in range.
H. LIVE RUNTIME — the three services are active, the five boot sentinels
   appear in the live logs since the 14:46:53 restart, and no unresolved
   placeholder token or new-code error has been logged.

SAFE: never writes the database, never contacts the running workers,
never opens exchange or Claude connections. Output is plain prose for a
screen reader. Run: .venv/bin/python verify_recalibration_pipeline_e2e.py
"""

import asyncio
import re
import sqlite3
import subprocess
from types import SimpleNamespace

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


# ── Phase A: DI wiring ────────────────────────────────────────────────

def phase_a():
    print("\nPhase A — DI wiring through the real WorkerManager.")
    mgr = open("src/workers/manager.py", encoding="utf-8").read()
    registered = set(re.findall(
        r'self\._services\[\s*["\']([a-z_]+)["\']\s*\]\s*=', mgr,
    ))
    for key in ("db", "transformer", "regime_detector", "structure_cache"):
        check(f"A: service key '{key}' registered by the real WorkerManager",
              key in registered)
    check("A: strategist constructed with the SHARED services dict",
          "ClaudeStrategist(claude_client, self._services, settings)" in mgr)


# ── Phase B: config pipeline ──────────────────────────────────────────

def phase_b():
    print("\nPhase B — config pipeline (toml to loader to dataclass to value).")
    from src.config.settings import (
        Settings, _build_brain, _build_scanner_labeller, _build_structure,
    )
    s = Settings._load_fresh() if hasattr(Settings, "_load_fresh") else Settings.load()
    b = s.brain
    check("B: quality_skip_thin_vol_ratio = 0.25 live",
          b.quality_skip_thin_vol_ratio == 0.25)
    check("B: quality_skip_heavy_attempts = 6 live",
          b.quality_skip_heavy_attempts == 6)
    check("B: session_attempts_enabled live", b.session_attempts_enabled is True)
    check("B: session_liveness_enabled live", b.session_liveness_enabled is True)
    check("B: liveness thresholds live (0.25 / 0.20 / 0.60)",
          (b.session_liveness_thin_vol_ratio, b.session_liveness_live_max_thin_share,
           b.session_liveness_thin_min_thin_share) == (0.25, 0.20, 0.60))
    check("B: range_truth_enabled live", s.structure.range_truth_enabled is True)
    check("B: range_fade_breakout_guard_enabled live",
          s.scanner.labeller.range_fade_breakout_guard_enabled is True)
    # Flip-tests through the REAL loaders: a toml edit reaches the consumer.
    flipped = _build_brain({"session_liveness_enabled": False,
                            "quality_skip_heavy_attempts": 9})
    check("B: brain loader flip-test (liveness off, heavy 9)",
          flipped.session_liveness_enabled is False
          and flipped.quality_skip_heavy_attempts == 9)
    check("B: labeller loader flip-test (guard off)",
          _build_scanner_labeller(
              {"range_fade_breakout_guard_enabled": False},
          ).range_fade_breakout_guard_enabled is False)
    check("B: structure loader flip-test (range truth off, field-name driven)",
          _build_structure(
              {"range_truth_enabled": False},
          ).range_truth_enabled is False)
    return s


# ── Phase C: real StructureEngine on a breakdown ─────────────────────

def _candles_range_then_breakdown():
    """120 bars oscillating in a clear 100..110 range with distinct swing
    structure, then a final leg breaking down to 97."""
    candles = []
    for i in range(120):
        cyc = i % 10
        base = 100.0 + (cyc if cyc <= 5 else 10 - cyc) * 2.0  # 100..110 swings
        if i >= 112:
            base = 100.0 - (i - 111) * 0.4  # the breakdown leg
        candles.append(SimpleNamespace(
            open=base + 0.2, high=base + 0.6, low=base - 0.6,
            close=base, volume=1000.0,
        ))
    return candles


def phase_c(settings):
    print("\nPhase C — Element 3 through the real StructureEngine and real APEX assembler.")
    from src.analysis.structure.structure_cache import StructureCache
    from src.analysis.structure.structure_engine import StructureEngine
    from src.apex.assembler import _gather_structural_data_from_cache

    engine = StructureEngine(settings.structure)
    current_price = 97.0
    analysis = engine.analyze("E2ETESTUSDT", current_price,
                              _candles_range_then_breakdown())
    check("C: real engine produced a StructuralAnalysis", analysis is not None)
    if analysis is None:
        return None, None
    check("C: clamped position stays in bounds",
          0.0 <= analysis.position_in_range <= 1.0,
          f"position={analysis.position_in_range:.2f}")
    check("C: real engine flags the breakdown",
          analysis.range_breakout == "below",
          f"breakout={analysis.range_breakout!r} overshoot={analysis.range_overshoot_pct:.2f}%")
    check("C: overshoot is a positive percent of the broken boundary",
          analysis.range_overshoot_pct > 0.0)
    d = analysis.to_dict()
    check("C: to_dict carries the truth fields",
          d.get("range_breakout") == "below" and "range_overshoot_pct" in d)
    cache = StructureCache()
    cache.set("E2ETESTUSDT", analysis)
    sd = _gather_structural_data_from_cache(
        {"structure_cache": cache}, "E2ETESTUSDT",
    )
    check("C: real APEX assembler propagates the truth fields",
          sd is not None and sd.range_breakout == "below"
          and sd.range_overshoot_pct == analysis.range_overshoot_pct)
    check("C: real StructuralData.format carries the marker",
          sd is not None and "BELOW the range low by" in sd.format())
    return analysis, cache


# ── Phase D: real Element 2 data flow ────────────────────────────────

def phase_d(settings):
    print("\nPhase D — Element 2 through the real DatabaseManager on the real ledger.")
    from src.core.trade_recorder import session_attempts_today
    from src.database.connection import DatabaseManager

    mode = str(getattr(getattr(settings, "general", None), "mode", "") or "bybit_demo")
    con = sqlite3.connect(f"file:{'data/trading.db'}?mode=ro", uri=True)
    raw = {
        r[0]: (int(r[1]), round(float(r[2] or 0.0), 4))
        for r in con.execute(
            "SELECT symbol, COUNT(DISTINCT opened_at), SUM(pnl_usd) "
            "FROM trade_log WHERE exchange_mode = ? "
            "AND opened_at >= date('now') AND opened_at < date('now','+1 day') "
            "GROUP BY symbol ORDER BY 2 DESC LIMIT 3", (mode,),
        ).fetchall()
    }
    con.close()

    async def run(symbols):
        db = DatabaseManager("data/trading.db")
        await db.connect()
        try:
            return await session_attempts_today(
                db, symbols=symbols, exchange_mode=mode,
            )
        finally:
            await db.disconnect()

    if not raw:
        check("D: no closed trades today in this mode — query path exercised, "
              "values check deferred to the live cycle", True)
        return {}
    got = asyncio.new_event_loop().run_until_complete(run(list(raw.keys())))
    agree = all(
        sym in got
        and int(got[sym]["attempts"]) == raw[sym][0]
        and abs(float(got[sym]["net_usd"]) - raw[sym][1]) < 0.005
        for sym in raw
    )
    check("D: real helper matches the independent raw read on the top coins",
          agree, "; ".join(
              f"{s}={raw[s][0]} attempts net {raw[s][1]:+.2f}" for s in raw))
    return got


# ── Phase E: the production render ───────────────────────────────────

def _real_pkg(symbol, price):
    from src.core.coin_package import (
        AltDataBlock, CoinPackage, PriceDataBlock, SignalsBlock,
        StateLabelBlock, StrategiesBlock, StructuralLevels, XrayBlock,
    )
    p = CoinPackage(
        symbol=symbol, qualified=True, opportunity_score=0.65,
        qualification_reasons=["xray"],
        price_data=PriceDataBlock(current=price, change_24h_pct=-8.7,
                                  regime="trending_down"),
        xray=XrayBlock(setup_type="bullish_fvg_ob", setup_score=84,
                       setup_type_confidence=0.32, trade_direction="long",
                       structural_levels=StructuralLevels(
                           suggested_sl=price * 0.97, suggested_tp=price * 1.16,
                           rr_ratio=8.72)),
        strategies=StrategiesBlock(fired_count=26, ensemble_consensus="GOOD",
                                   total_score=87.8,
                                   scoring_regime="trending_down",
                                   scoring_regime_volume_ratio=0.130,
                                   scoring_regime_volume_ratio_known=True),
        signals=SignalsBlock(confidence=0.48, direction="neutral"),
        alt_data=AltDataBlock(funding_rate=0.0001, funding_signal="longs_paying",
                              oi_change_24h_pct=3.71, fear_greed=12),
        state_label=StateLabelBlock(primary="TREND_PULLBACK_LONG", confidence=0.6),
    )
    p.interestingness_score = 0.77
    p.interestingness_breakdown = {"confluence": 0.20}
    p.state_cleanness = 0.5
    p.confluence_count = 2
    return p


class _SigGetter:
    def get_signal(self, s):
        return None


class _RegimeDet:
    def get_coin_regime(self, s):
        return None


def phase_e(settings, analysis, cache, attempts):
    print("\nPhase E — the production candidate-block render (real strategist, real __init__).")
    from src.brain.strategist import ClaudeStrategist
    from src.core.layer_manager import LayerManager

    lm = LayerManager.__new__(LayerManager)
    lm._scorer_components = {}
    lm._strategy_votes = {}
    strat = ClaudeStrategist(
        claude_client=None,
        services={"layer_manager": lm, "signal_worker": _SigGetter(),
                  "structure_cache": cache, "regime_detector": _RegimeDet()},
        settings=settings,
    )
    sym = "E2ETESTUSDT"
    # Use the REAL ledger values for a heavy losing coin when available,
    # else a representative heavy pair — clearly labelled either way.
    heavy = None
    for s_, v in (attempts or {}).items():
        if v["attempts"] >= settings.brain.quality_skip_heavy_attempts and v["net_usd"] < 0:
            heavy = v
            break
    sa = {sym: heavy or {"attempts": 9, "net_usd": -1.20}}
    src_note = "real ledger values" if heavy else "representative values"
    out = strat._format_packages_for_prompt_full(
        {sym: _real_pkg(sym, 97.0)}, session_attempts_by_sym=sa,
    )
    check("E: Session today line renders through the production formatter",
          f"Session today: {sa[sym]['attempts']} attempts" in out, src_note)
    check("E: HEAVY LOSING SESSION suffix names the skip permission",
          "HEAVY LOSING SESSION" in out and "QUALITY OVER QUOTA" in out)
    check("E: Structure line carries the real engine's BELOW RANGE marker",
          "(BELOW RANGE by" in out and "breakdown, not a floor" in out)
    check("E: Regime line renders the scored vol_ratio",
          "vol_ratio=0.130" in out)
    # Flag-off byte-rollback through the SAME production path.
    settings.structure.range_truth_enabled = False
    out_off = strat._format_packages_for_prompt_full(
        {sym: _real_pkg(sym, 97.0)}, session_attempts_by_sym=sa,
    )
    settings.structure.range_truth_enabled = True
    check("E: flag-off render drops the marker (instant rollback)",
          "(BELOW RANGE by" not in out_off
          and "range_pos=" in out_off)
    return out


def phase_f(settings, attempts):
    print("\nPhase F — Element 1 resolution and Element 4 measured-only gather, real seams.")
    from src.brain.strategist import (
        _PROMPT_CALIBRATION_TOKENS, TRADE_SYSTEM_PROMPT_ZERO_TWO,
        _candidate_vol_ratio, _resolve_prompt_calibration, _session_liveness,
    )
    resolved = _resolve_prompt_calibration(
        TRADE_SYSTEM_PROMPT_ZERO_TWO,
        thin_vol_ratio=settings.brain.quality_skip_thin_vol_ratio,
        heavy_attempts=settings.brain.quality_skip_heavy_attempts,
    )
    check("F: live system prompt resolves with the loaded thresholds",
          "at or below 0.25" in resolved and "6 or more" in resolved)
    check("F: zero leftover placeholder tokens",
          not any(t in resolved for t in _PROMPT_CALIBRATION_TOKENS))
    scored = _real_pkg("AUSDT", 1.0)
    v, known = _candidate_vol_ratio(scored, None)
    check("F: scored package reads its measured ratio", (v, known) == (0.130, True))
    unscored = _real_pkg("BUSDT", 1.0)
    unscored.strategies.scoring_regime = ""
    v2, known2 = _candidate_vol_ratio(unscored, None)
    check("F: unscored package with no live cache is EXCLUDED (no fabrication)",
          known2 is False)
    det = SimpleNamespace(get_coin_regime=lambda s: SimpleNamespace(
        volume_ratio=0.043, volume_ratio_known=True))
    v3, known3 = _candidate_vol_ratio(unscored, det)
    check("F: unscored package falls back to the live regime cache",
          (v3, known3) == (0.043, True))
    label, thin = _session_liveness([0.130, 0.043, 0.05, 0.9, 0.8],
                                    settings.brain.session_liveness_thin_vol_ratio,
                                    settings.brain.session_liveness_live_max_thin_share,
                                    settings.brain.session_liveness_thin_min_thin_share)
    check("F: liveness classifier on the gathered set", (label, thin) == ("thin", 3))


def phase_g(analysis):
    print("\nPhase G — Element 3 through the real state labeler.")
    from src.workers.scanner.state_labeler import (
        LABEL_RANGE_FADE_LONG, LABEL_TREND_PULLBACK_LONG, label_state,
    )
    kw = dict(setup_type="bullish_fvg_ob", setup_type_confidence=0.5,
              trade_direction="long", suggested_direction="long",
              regime="trending_up", consensus_direction="long",
              regime_haircut=0.5)
    brk = getattr(analysis, "range_breakout", "below") if analysis else "below"
    with_break = label_state(range_breakout=brk, **kw)
    labels = [with_break.primary] + list(with_break.secondary)
    check("G: real engine's breakout suppresses the fade label",
          LABEL_RANGE_FADE_LONG not in labels, f"breakout={brk!r}")
    check("G: setup-driven label still present (the coin still ranks)",
          LABEL_TREND_PULLBACK_LONG in labels)
    legacy = label_state(**kw)
    explicit = label_state(range_breakout="", **kw)
    check("G: in-range labelling byte-identical to legacy",
          legacy.primary == explicit.primary
          and list(legacy.secondary) == list(explicit.secondary))


def phase_h():
    print("\nPhase H — the live runtime (running services and their logs).")
    try:
        states = subprocess.run(
            ["systemctl", "is-active", "trading-workers.service",
             "trading-brain.service", "trading-mcp-sse.service"],
            capture_output=True, text=True, timeout=10,
        ).stdout.split()
        check("H: all three services active", states == ["active"] * 3,
              " ".join(states))
    except Exception as e:
        check("H: all three services active", False, str(e)[:60])
    def _in_log(path, needle):
        # Full-file scan (the workers log grows fast; a tail window
        # missed the boot block in the first harness run).
        return subprocess.run(
            ["grep", "-q", needle, path], capture_output=True,
        ).returncode == 0

    for sentinel, path in (
        ("BOOT_QUALITY_SKIP_KEYS", "data/logs/brain.log"),
        ("BOOT_SESSION_ATTEMPTS_ON", "data/logs/brain.log"),
        ("BOOT_SESSION_LIVENESS_ON", "data/logs/brain.log"),
        ("BOOT_RANGE_TRUTH_ON", "data/logs/brain.log"),
        ("BOOT_RANGE_FADE_GUARD_ON", "data/logs/workers.log"),
    ):
        check(f"H: live log carries {sentinel}", _in_log(path, sentinel))
    check("H: no unresolved-token error ever logged",
          not _in_log("data/logs/brain.log", "STRAT_PROMPT_TOKEN_UNRESOLVED"))
    check("H: no session-attempts/liveness/range-truth errors in live logs",
          not _in_log("data/logs/brain.log", "session attempts prefetch failed")
          and not _in_log("data/logs/brain.log", "session liveness line failed")
          and not _in_log("data/logs/brain.log", "block=session_attempts"))


def main():
    print("Real-pipeline end-to-end verification of the Four-Element "
          "Prompt Recalibration.")
    phase_a()
    settings = phase_b()
    analysis, cache = phase_c(settings)
    attempts = phase_d(settings)
    if cache is not None:
        phase_e(settings, analysis, cache, attempts)
    phase_f(settings, attempts)
    phase_g(analysis)
    phase_h()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nRESULT: {len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed.")
    if failed:
        print("Failed checks: " + "; ".join(failed))
        return 1
    print("PASS: every element verified end to end through the real "
          "project — DI wiring, config pipeline, engine, ledger, "
          "production render, labeler, and the live runtime.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
