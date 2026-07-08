"""End-to-end PIPELINE check — upstream fixes #2-#5 on the REAL project.

Drives the REAL objects wired the way WorkerManager wires them (same constructor
signatures), from the REAL config.toml through Settings, against the REAL live
trading.db (opened READ-ONLY — no writes), proving DI wiring + data flow +
actual runtime behaviour for every phase:

  Settings.load -> StructureEngine(settings.structure)            (#5 flag reaches engine)
  manager sig    -> StructureWorker(settings,db,engine,cache,...)  (#5 DI intact + glue)
  MarketRepository(db).get_klines/get_klines_batch (REAL klines)   (#5 data source)
  RegimeDetector(s,ta,market_repo).detect (REAL klines+TA)         (#3B volume_ratio_known)
  StructureEngine.analyze + analyze_direction_only (REAL H1/H4/D1) (#5 blend on real data)
  ClaudeStrategist._format_packages_for_prompt_full (REAL render)  (#2/#3A/#4)

Run:  PYTHONPATH=. .venv/bin/python pipeline_check_upstream_issues_2_5.py
Exit 0 = the whole pipeline behaves correctly end-to-end on the real project.
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import aiosqlite

from src.config.settings import Settings

DB_PATH = "data/trading.db"
_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class _RoDB:
    """Read-only DatabaseManager stand-in: real queries against the live DB via
    a `file:...?mode=ro` connection; any write path raises (proves no writes)."""

    def __init__(self, path: str) -> None:
        self._uri = f"file:{path}?mode=ro"

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        async with aiosqlite.connect(self._uri, uri=True) as c:
            c.row_factory = aiosqlite.Row
            cur = await c.execute(sql, params)
            return [dict(r) for r in await cur.fetchall()]

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        async with aiosqlite.connect(self._uri, uri=True) as c:
            c.row_factory = aiosqlite.Row
            cur = await c.execute(sql, params)
            r = await cur.fetchone()
            return dict(r) if r else None

    async def execute(self, *a, **k):
        raise RuntimeError("read-only pipeline check — refusing write")

    async def executemany(self, *a, **k):
        raise RuntimeError("read-only pipeline check — refusing write")

    async def connect(self) -> None:  # no-op (per-query connections)
        pass

    async def close(self) -> None:
        pass


async def main() -> int:
    from src.analysis.engine import TAEngine
    from src.analysis.structure.structure_cache import StructureCache
    from src.analysis.structure.structure_engine import StructureEngine
    from src.brain.strategist import ClaudeStrategist
    from src.core.coin_package import (
        AltDataBlock, CoinPackage, PriceDataBlock, SignalsBlock,
        StateLabelBlock, StrategiesBlock, StructuralLevels, XrayBlock,
    )
    from src.core.types import TimeFrame
    from src.database.repositories.market_repo import MarketRepository
    from src.strategies.regime import RegimeDetector
    from src.workers.structure_worker import StructureWorker

    s = Settings.load()
    db = _RoDB(DB_PATH)
    market_repo = MarketRepository(db)

    # ───── PHASE 0: DI wiring (exact manager signatures) ─────
    print("\n== PHASE 0: DI wiring (real manager signatures) ==")
    st = s.structure
    check("#5 settings.structure carries mtf flags",
          all(hasattr(st, f) for f in ["mtf_multi_timeframe_enabled", "mtf_timeframes",
              "mtf_h4_cache_ttl_seconds", "mtf_d1_cache_ttl_seconds", "mtf_htf_weight", "mtf_htf_limit"]),
          f"enabled={st.mtf_multi_timeframe_enabled} tfs={st.mtf_timeframes}")
    check("#5 flag default OFF (dataclass+config.toml)", st.mtf_multi_timeframe_enabled is False)
    engine = StructureEngine(st)  # manager line 230
    check("#5 StructureEngine carries the flag", engine._settings.mtf_multi_timeframe_enabled is False)
    cache = StructureCache(ttl_seconds=float(st.cache_ttl_seconds))  # manager line 231
    worker = StructureWorker(settings=s, db=db, engine=engine, cache=cache,
                             scanner=None, shadow_kline_reader=None)  # manager line 1626
    check("#5 StructureWorker DI intact (manager sig) + mtf state",
          worker._mtf_enabled is False and hasattr(worker, "_htf_cache"),
          f"htf_limit={worker._mtf_htf_limit} h4_ttl={worker._mtf_h4_ttl:.0f} d1_ttl={worker._mtf_d1_ttl:.0f}")
    ta = TAEngine(db, settings=s)  # manager line 195
    detector = RegimeDetector(s, ta, market_repo)  # manager line 1644
    strategist = ClaudeStrategist.__new__(ClaudeStrategist)  # avoid claude_client/network in __init__
    # Phase 4 renders REAL regime/structure data through the candidate per-coin
    # sub-block code (strategist.py:2965-3170). Briefing-mode (the live default)
    # gates candidates by scanner-produced interestingness/labels we don't
    # synthesize here, so we exercise the IDENTICAL sub-block code via the legacy
    # full-block path (surface_briefing_fields=False) for deterministic
    # assertions. (Briefing-mode rendering of these same strings is already
    # proven by the live stage2 dumps.) This is an in-process Settings copy — it
    # does NOT touch the running system.
    s.brain.surface_briefing_fields = False
    strategist.settings = s
    check("#5 worker flag-OFF -> _build_htf_views returns None (byte-identical path)",
          worker._build_htf_views("BTCUSDT") is None)

    # ───── PHASE 1: REAL klines from the live DB ─────
    print("\n== PHASE 1: REAL klines from live trading.db (read-only) ==")
    syms = list(getattr(s.universe, "watch_list", []) or [])[:12] or ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    h1_by = {}
    for sym in syms:
        try:
            k = await market_repo.get_klines(sym, TimeFrame.H1.value, 200)
            if k and len(k) >= s.structure.min_candles:
                h1_by[sym] = k
        except Exception:
            pass
    check("REAL H1 klines pulled for >=3 symbols", len(h1_by) >= 3,
          f"{len(h1_by)} symbols: {list(h1_by)[:6]}")
    batch_h4 = await market_repo.get_klines_batch(list(h1_by), TimeFrame.H4.value, st.mtf_htf_limit)
    batch_d1 = await market_repo.get_klines_batch(list(h1_by), TimeFrame.D1.value, st.mtf_htf_limit)
    check("REAL H4 batch fetched", any(batch_h4.get(x) for x in h1_by),
          f"H4 nonempty for {sum(1 for x in h1_by if batch_h4.get(x))}/{len(h1_by)}")
    check("REAL D1 batch fetched (the previously-unused daily)", any(batch_d1.get(x) for x in h1_by),
          f"D1 nonempty for {sum(1 for x in h1_by if batch_d1.get(x))}/{len(h1_by)}")

    # ───── PHASE 2: #5 MTF blend on REAL data + worker glue ─────
    print("\n== PHASE 2: #5 daily->MTF on REAL data (engine + worker, flag on/off) ==")
    sym0 = next(iter(h1_by))
    price0 = h1_by[sym0][-1].close
    base = engine.analyze(sym0, price0, h1_by[sym0], higher_tf_views=None)
    check("#5 flag-OFF analyze() runs on real H1 (legacy path)", base is not None,
          f"{sym0} mtf_score={getattr(base,'mtf_confluence_score',None)}")
    # build REAL higher-TF views from the real H4/D1 klines
    hv = {}
    if batch_h4.get(sym0):
        hv["240"] = engine.analyze_direction_only(sym0, batch_h4[sym0], timeframe="240")
    if batch_d1.get(sym0):
        hv["D"] = engine.analyze_direction_only(sym0, batch_d1[sym0], timeframe="D")
    h4v, d1v = hv.get("240"), hv.get("D")
    check("#5 analyze_direction_only: H4 view has REAL data", h4v is not None and h4v.has_data,
          f"H4 structure={getattr(h4v, 'structure', None)}")
    # D1 gracefully degrades to has_data=False when daily history < min_candles.
    # The live DB currently holds only ~7 daily candles per symbol (daily history
    # is young), so D1 contribution is INACTIVE-by-design until ~50 days accrue —
    # the fallback contract: has_data is True IFF >= min_candles daily klines.
    d1_n = len(batch_d1.get(sym0) or [])
    check("#5 D1 graceful-degradation contract (has_data iff >=min_candles)",
          bool(d1v) and (d1v.has_data == (d1_n >= st.min_candles)),
          f"D1 candles={d1_n} min={st.min_candles} has_data={getattr(d1v, 'has_data', None)} (daily history young -> D1 inactive by design)")
    blended = engine.analyze(sym0, price0, h1_by[sym0], higher_tf_views=hv)
    # the blended score must be a valid 0-10 and within the bounded window of the base
    import math
    b0 = base.mtf_confluence_score if base else 0
    b1 = blended.mtf_confluence_score if blended else 0
    check("#5 blended MTF score is bounded vs base (+/-mtf_htf_weight)",
          0 <= b1 <= 10 and abs(b1 - b0) <= math.ceil(b0 * st.mtf_htf_weight),
          f"base={b0} blended={b1} (alpha={st.mtf_htf_weight})")
    # worker glue ON: real batched refresh against the live DB, then build views
    worker._mtf_enabled = True
    await worker._refresh_htf_views(list(h1_by)[:5])
    wv = worker._build_htf_views(sym0)
    check("#5 worker._refresh_htf_views populated cache from REAL DB", wv is not None and any(v.has_data for v in wv.values()),
          f"views={list(wv) if wv else None}")
    worker._mtf_enabled = False

    # ───── PHASE 3: #3B regime on REAL data ─────
    print("\n== PHASE 3: #3B regime missing-volume correctness on REAL data ==")
    regimes = {}
    for sym in list(h1_by)[:8]:
        try:
            rs = await detector.detect(sym)
            regimes[sym] = rs
        except Exception as e:
            print(f"    (detect {sym} failed: {str(e)[:60]})")
    check("REAL RegimeDetector.detect ran on live klines", len(regimes) >= 3,
          f"{len(regimes)} regimes: " + ", ".join(f"{k}={v.regime.value}" for k, v in list(regimes.items())[:4]))
    check("#3B every real regime carries volume_ratio_known",
          all(hasattr(v, "volume_ratio_known") for v in regimes.values()))
    check("#3B real regimes serialize coherently (known->float, unknown->None)",
          all((v.to_dict()["volume_ratio"] is None) == (not v.volume_ratio_known) for v in regimes.values()))
    # show the honest vol_ratio values (the #3 symptom, now truthful)
    vr_line = ", ".join(f"{k}={'n/a' if not v.volume_ratio_known else format(v.volume_ratio,'.3f')}" for k, v in list(regimes.items())[:5])
    print(f"    real vol_ratio (honest): {vr_line}")

    # ───── PHASE 4: #2/#3A/#4 render on REAL data (end-to-end to brain prompt) ─────
    print("\n== PHASE 4: #2 + #3A + #4 brain-prompt render on REAL data ==")
    # pick a real symbol whose structure actually produced a nearest_fvg (for #4)
    rendered = None
    chosen = None
    for sym in h1_by:
        a = engine.analyze(sym, h1_by[sym][-1].close, h1_by[sym])
        if a is None:
            continue
        rs = regimes.get(sym) or await detector.detect(sym)
        # real scored metrics captured onto the package (as strategy_worker does)
        strat = StrategiesBlock(
            fired_count=10, ensemble_consensus="GOOD", total_score=float(a.setup_score),
            scoring_regime=rs.regime.value,
            scoring_regime_confidence=rs.confidence, scoring_regime_adx=rs.adx,
            scoring_regime_atr_percentile=rs.atr_percentile, scoring_regime_choppiness=rs.choppiness,
            scoring_regime_volume_ratio=rs.volume_ratio,
            scoring_regime_volume_ratio_known=rs.volume_ratio_known,
            scoring_regime_trend_direction=rs.trend_direction,
        )
        pkg = CoinPackage(
            symbol=sym, qualified=True, opportunity_score=0.5, qualification_reasons=["real"],
            price_data=PriceDataBlock(current=a.current_price, change_24h_pct=0.0, regime=rs.regime.value),
            xray=XrayBlock(setup_type=str(getattr(a, "setup_type", "") or "none"),
                           setup_score=int(a.setup_score), setup_type_confidence=float(getattr(a, "setup_type_confidence", 0.0) or 0.0),
                           trade_direction=str(a.suggested_direction or "long"),
                           structural_levels=StructuralLevels(suggested_sl=0.0, suggested_tp=0.0, rr_ratio=0.0)),
            strategies=strat,
            signals=SignalsBlock(confidence=0.4, direction=str(a.suggested_direction or "long")),
            alt_data=AltDataBlock(funding_rate=0.0, funding_signal="n/a", oi_change_24h_pct=0.0, fear_greed=50),
            state_label=StateLabelBlock(primary="NO_TRADEABLE_STATE", confidence=0.5),
        )
        cache.set(sym, a)
        detector._per_coin_regimes[sym] = rs  # live cache == scored (no-drift case)
        strategist.services = {
            "structure_cache": cache,
            "signal_worker": SimpleNamespace(get_scorer_components=lambda x: None),
            "regime_detector": detector,
            "layer_manager": SimpleNamespace(get_score_breakdown=lambda x: None,
                                             get_strategy_consensus=lambda x: None),
        }
        out = strategist._format_packages_for_prompt_full({sym: pkg})
        if a.nearest_fvg is not None:  # symbol with a real FVG -> can assert #4 positively
            rendered, chosen = out, (sym, a, rs)
            break
        rendered = rendered or out
    check("REAL render produced a prompt", bool(rendered))
    if chosen:
        sym, a, rs = chosen
        fvg_dir = a.nearest_fvg.direction
        check(f"#4 SMC shows real FVG polarity (fvg={fvg_dir}@, not n/a) [{sym}]",
              f"fvg={fvg_dir}@" in rendered and "fvg=n/a" not in rendered)
        check(f"#2 Regime line shows scored word '{rs.regime.value}' [{sym}]",
              f"Regime: {rs.regime.value} " in rendered)
        check("#2 no drift note when scored==live cache",
              "live conditions now read" not in rendered)
        vr_expect = "n/a" if not rs.volume_ratio_known else f"{rs.volume_ratio:.3f}"
        check(f"#3A vol_ratio rendered honestly (vol_ratio={vr_expect}) [{sym}]",
              f"vol_ratio={vr_expect}" in rendered)
    else:
        check("#4/#2/#3A render assertions (no real FVG found in sample)", False,
              "no sampled symbol had a nearest_fvg; widen sample")

    # #2 drift note — force live cache != scored on a real symbol
    if chosen:
        sym, a, rs = chosen
        from src.strategies.models.regime_types import MarketRegime, RegimeState
        other = MarketRegime.DEAD if rs.regime != MarketRegime.DEAD else MarketRegime.RANGING
        detector._per_coin_regimes[sym] = RegimeState(
            regime=other, confidence=0.8, adx=9.0, atr_percentile=1.0, choppiness=40.0,
            volume_ratio=0.5, volume_ratio_known=True, trend_direction=0)
        out2 = strategist._format_packages_for_prompt_full({sym: pkg if False else _repkg(sym, a, rs)})
        check("#2 drift note appears when live cache drifts off scored",
              f"(live conditions now read {other.value})" in out2)

    # summary
    print("\n" + "=" * 56)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"PIPELINE: {passed} passed, {failed} failed")
    return 1 if failed else 0


def _repkg(sym, a, rs):
    """Rebuild a package for the drift-note case (scored word kept; live cache differs)."""
    from src.core.coin_package import (
        AltDataBlock, CoinPackage, PriceDataBlock, SignalsBlock,
        StateLabelBlock, StrategiesBlock, StructuralLevels, XrayBlock,
    )
    return CoinPackage(
        symbol=sym, qualified=True, opportunity_score=0.5, qualification_reasons=["real"],
        price_data=PriceDataBlock(current=a.current_price, change_24h_pct=0.0, regime=rs.regime.value),
        xray=XrayBlock(setup_type=str(getattr(a, "setup_type", "") or "none"),
                       setup_score=int(a.setup_score), setup_type_confidence=float(getattr(a, "setup_type_confidence", 0.0) or 0.0),
                       trade_direction=str(a.suggested_direction or "long"),
                       structural_levels=StructuralLevels(suggested_sl=0.0, suggested_tp=0.0, rr_ratio=0.0)),
        strategies=StrategiesBlock(
            fired_count=10, ensemble_consensus="GOOD", total_score=float(a.setup_score),
            scoring_regime=rs.regime.value, scoring_regime_confidence=rs.confidence,
            scoring_regime_adx=rs.adx, scoring_regime_atr_percentile=rs.atr_percentile,
            scoring_regime_choppiness=rs.choppiness, scoring_regime_volume_ratio=rs.volume_ratio,
            scoring_regime_volume_ratio_known=rs.volume_ratio_known,
            scoring_regime_trend_direction=rs.trend_direction),
        signals=SignalsBlock(confidence=0.4, direction=str(a.suggested_direction or "long")),
        alt_data=AltDataBlock(funding_rate=0.0, funding_signal="n/a", oi_change_24h_pct=0.0, fear_greed=50),
        state_label=StateLabelBlock(primary="NO_TRADEABLE_STATE", confidence=0.5),
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
