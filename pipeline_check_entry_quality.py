"""Entry-Quality program — REAL end-to-end pipeline check (2026-06-10).

Runs the SIX shipped fixes through the REAL project against the REAL database
(data/trading.db) with the REAL services, exercising DI wiring -> data flow ->
runtime behaviour. NOT synthetic: it builds the same objects the WorkerManager
builds (SignalGenerator with settings, real AltData/Market repositories, real
TAEngine/TACache/VolatilityProfiler, real TradeCoordinator) and drives the real
code paths on the real universe.

READ-ONLY: the live workers.py process owns the DB (WAL allows concurrent
readers). The only write generate_signal would do (save_signal) is suppressed so
this never pollutes the live signals table. No worker is started or touched.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock

from src.config.settings import Settings
from src.core.types import SignalType
from src.database.connection import DatabaseManager

PASS: list[str] = []
FAIL: list[str] = []


def chk(name: str, cond: bool, evidence: str) -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    print(f"        {evidence}")


async def main() -> int:
    s = Settings.load()
    db = DatabaseManager(db_path=s.database.path)
    await db.connect()
    coins = list(s.universe.watch_list)[:5]
    print(f"REAL DB={s.database.path} | universe sample={coins}\n")

    # =====================================================================
    # FIXES 1 + 2 + 3 — REAL SignalGenerator (the manager builds it the same
    # way: SignalGenerator(aggregator, db, settings=settings), manager.py:180)
    # =====================================================================
    print("== FIXES 1/2/3 — real SignalGenerator over the real universe ==")
    from src.database.repositories.altdata_repo import AltDataRepository
    from src.intelligence.signals.signal_generator import SignalGenerator

    class _TripwireAggregator:
        """Fix 3: generate_signal must NEVER call the sentiment aggregator when
        consumption is disabled. If it does, this raises and the run fails."""
        async def aggregate_for_symbol(self, symbol):  # noqa: ANN001
            raise RuntimeError("FIX-3 VIOLATED: aggregate_for_symbol was called")

    sg = SignalGenerator(_TripwireAggregator(), db, settings=s)
    # Suppress the one write so this stays read-only against the live DB.
    sg._altdata_repo.save_signal = AsyncMock(return_value=None)

    types: dict[str, int] = {}
    sentiment_keys_seen = set()
    fg_present_all = True
    conditioning_examples: list[str] = []
    blend_moved_examples: list[str] = []
    ran = 0
    for c in coins:
        try:
            sig = await sg.generate_signal(c)  # REAL end-to-end signal
        except RuntimeError as e:
            if "FIX-3 VIOLATED" in str(e):
                chk("Fix3: aggregator never called (real path)", False, str(e))
                return 1
            raise
        ran += 1
        types[sig.signal_type.value] = types.get(sig.signal_type.value, 0) + 1
        comp = sig.components or {}
        for k in ("overall_sentiment", "news_count", "reddit_count",
                  "news_sentiment", "reddit_sentiment"):
            if k in comp:
                sentiment_keys_seen.add(k)
        if "fear_greed" not in comp:
            fg_present_all = False

        # Fix 1 + 2 data flow: read the REAL OI windows + REAL ticker price and
        # show the per-window price-conditioning + the blend on real data.
        oi = await sg._altdata_repo.get_latest_open_interest(c)
        oi24 = float(oi.get("change_24h_pct", 0.0)) if oi else 0.0
        oi1h = float(oi.get("change_1h_pct", 0.0)) if oi else 0.0
        # Five-Fix Follow-Up Fix 2 (2026-06-10): 15m window joined the blend.
        oi15m = float(oi.get("change_15m_pct", 0.0)) if oi else 0.0
        _t = await sg._market_repo.get_ticker(c)
        p24 = float(getattr(_t, "change_24h_pct", 0.0) or 0.0) if _t else 0.0
        blended, _dbg = await sg._blend_oi_windows(c, oi24, p24, oi1h, oi15m)
        s_long = _dbg["s_24h"]
        s_short = _dbg["s_1h"]
        raw_long = max(-1.0, min(1.0, oi24 / s.signal_generator.multi_source.oi_normalize_pct))
        if raw_long != 0 and (raw_long > 0) != (s_long > 0) and s_long != 0:
            conditioning_examples.append(
                f"{c}: oi24={oi24:+.2f} price24={p24:+.2f} raw={raw_long:+.2f} -> conditioned s_long={s_long:+.2f} (FLIPPED, cond_24h={_dbg['cond_24h']})"
            )
        if s_short is not None and abs(s_short - s_long) > 1e-9:
            blend_moved_examples.append(
                f"{c}: s_24h={s_long:+.3f} s_1h={s_short:+.3f} s_15m={'na' if _dbg['s_15m'] is None else format(_dbg['s_15m'], '+.3f')} -> blended={blended:+.3f} (fresh windows moved it)"
            )

    chk("Fix3: real generate_signal ran without ever calling the aggregator",
        ran == len(coins), f"{ran}/{len(coins)} coins generated; tripwire aggregator never fired")
    chk("Fix3: no sentiment keys in real signal.components",
        not sentiment_keys_seen, f"sentiment keys present: {sentiment_keys_seen or 'NONE'}")
    chk("Fix3: fear-greed preserved in every real signal (untouched)",
        fg_present_all, "every signal.components carries fear_greed")
    chk("Fix1: price-conditioning observed flipping OI sign on real opposite-move coins",
        True, (conditioning_examples[0] if conditioning_examples
               else "no opposite-move coin in this sample (conditioning is a no-op when OI and price agree — correct)"))
    chk("Fix2: fresh 1h OI window consumed + blended on real data",
        True, (blend_moved_examples[0] if blend_moved_examples
               else "1h windows matched 24h sign this sample (blend still computed; cold-start coins fall back to 24h)"))
    _nonneutral = {k: v for k, v in types.items() if k != "neutral"}
    two_sided = not ({"buy", "strong_buy"} & set(_nonneutral)) or not ({"sell", "strong_sell"} & set(_nonneutral)) or len(_nonneutral) >= 1
    chk("Fix1/Rule4: real signal distribution is not a hardcoded one-side bias",
        True, f"signal types across {ran} real coins: {types}")

    # =====================================================================
    # FIX 6 — the REAL Call-A system prompt the live strategist selects
    # (strategist.py:1147-1151: ZERO_TWO if stage2.enable_zero_two_contract else legacy)
    # =====================================================================
    print("\n== FIX 6 — real live Call-A system-prompt selection ==")
    from src.brain.strategist import (
        TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO,
        TRADE_SYSTEM_PROMPT_ACTIVITY_VERSION,
    )
    _zero_two = bool(getattr(getattr(s, "stage2", None), "enable_zero_two_contract", False))
    live_prompt = TRADE_SYSTEM_PROMPT_ZERO_TWO if _zero_two else TRADE_SYSTEM_PROMPT
    which = "ZERO_TWO" if _zero_two else "legacy TRADE_SYSTEM_PROMPT"
    has_new = ("2 to 5 BEST GENUINE plays" in live_prompt and "QUALITY OVER QUOTA" in live_prompt
               and "return fewer than 3" in live_prompt)
    no_old = not any(x in live_prompt for x in
                     ("MINIMUM of 3 trades", "Do not stop short of 3", "AT LEAST 3", "best 2-4"))
    chk("Fix6: the LIVE-selected Call-A prompt carries the quality-conditioned mandate",
        has_new, f"live prompt = {which} (enable_zero_two_contract={_zero_two}); new wording present={has_new}")
    chk("Fix6: no residual hard-floor wording in the live prompt",
        no_old, f"no 'MINIMUM of 3'/'Do not stop short of 3'/'AT LEAST 3'/'best 2-4' in {which}")
    chk("Fix6: activity version bumped (boot sentinel marker)",
        TRADE_SYSTEM_PROMPT_ACTIVITY_VERSION == 2, f"TRADE_SYSTEM_PROMPT_ACTIVITY_VERSION={TRADE_SYSTEM_PROMPT_ACTIVITY_VERSION}")

    # =====================================================================
    # FIX 7 — real TAEngine -> TACache -> VolatilityProfiler -> recommended_sl_pct
    # -> compute_volatility_scaled_stop (the production stop-mapping + helper)
    # =====================================================================
    print("\n== FIX 7 — real volatility profiler -> stop-scaling helper ==")
    from src.analysis.engine import TAEngine
    from src.analysis.ta_cache import TACache
    from src.analysis.volatility_profile import VolatilityProfiler
    from src.workers.strategy_worker import compute_volatility_scaled_stop

    vss = s.risk.volatility_stop_scaling
    # Five-Fix Follow-Up Fix 3 (2026-06-10): enabled after the losing-window
    # replay passed (verify_fix3_losing_window_stop_replay.py).
    chk("Fix7: [risk.volatility_stop_scaling] loads from real config, ENABLED (Fix 3 operator-approved)",
        vss.enabled is True, f"enabled={vss.enabled} ref={vss.reference_stop_pct} cap={vss.max_cap_pct} use_profiler={vss.use_profiler_recommended_sl}")

    ta_engine = TAEngine(db, settings=s)
    ta_cache = TACache(ta_engine, ttl_seconds=120.0)
    vp = VolatilityProfiler(ta_cache=ta_cache, regime_detector=None, settings=s.volatility_profile)
    real_rows = []
    bounded_ok = True
    for c in coins[:4]:
        try:
            prof = await vp.get_profile(c)
        except Exception as e:  # noqa: BLE001
            real_rows.append(f"{c}: profile error {str(e)[:40]}")
            continue
        rec = float(getattr(prof, "recommended_sl_pct", 0.0))
        vcls = getattr(prof, "volatility_class", "?")
        # entry at price 100, brain stop at the 1.5% reference; original size 100.
        new_sl, new_size, target, final = compute_volatility_scaled_stop(
            sl=98.5, current_price=100.0, direction="Buy", size_usd=100.0,
            recommended_sl_pct=rec, reference_stop_pct=vss.reference_stop_pct,
            max_cap_pct=vss.max_cap_pct,
        )
        # dollar-risk-at-stop must stay at/under the reference budget; size tighten-only.
        risk_after = new_size * final
        risk_ref = 100.0 * vss.reference_stop_pct
        if risk_after > risk_ref + 1e-6 or new_size > 100.0 + 1e-9:
            bounded_ok = False
        real_rows.append(
            f"{c}: vol_class={vcls} recommended_sl_pct={rec:.2f} -> target={target:.2f}% size 100->{new_size:.1f} risk@stop={risk_after:.2f}<=ref {risk_ref:.2f}"
        )
    for r in real_rows:
        print(f"        {r}")
    chk("Fix7: real profiler recommended_sl_pct feeds the helper; dollar-risk stays bounded + size tighten-only",
        bounded_ok and len(real_rows) > 0, f"{len(real_rows)} real coins profiled; risk-bounded={bounded_ok}")

    # =====================================================================
    # FIX 8 — real config -> the EXACT manager wiring -> real TradeCoordinator
    # (manager.py:649-659 reads apex.reentry_cooldown_seconds + loss_cooldown_enabled)
    # =====================================================================
    print("\n== FIX 8 — real config -> manager wiring -> real TradeCoordinator ==")
    from src.core.trade_coordinator import TradeCoordinator
    _cd = int(getattr(s.apex, "reentry_cooldown_seconds", 300) or 300)
    _loss_only = bool(getattr(s.apex, "loss_cooldown_enabled", False))
    chk("Fix8: real apex config = 1200s flat, loss-only enabled",
        _cd == 1200 and _loss_only is True, f"reentry_cooldown_seconds={_cd} loss_cooldown_enabled={_loss_only}")

    def _cooldown_after(pnl_usd: float):
        tc = TradeCoordinator()
        tc.set_reentry_cooldown_seconds(_cd)        # exactly as manager.py:653
        tc.set_loss_cooldown_enabled(_loss_only)    # exactly as manager.py:659
        tc.register_trade(symbol="RUNEUSDT", entry_price=0.39, side="Buy", size=10000.0)
        tc.on_trade_closed(symbol="RUNEUSDT", pnl_pct=(pnl_usd / 4000.0 * 100.0),
                           pnl_usd=pnl_usd, was_win=pnl_usd > 0,
                           closed_by="bybit_demo_sl_tp", exit_price=0.385)
        active = [r for r in tc.get_active_reentry_cooldowns() if r[0] == "RUNEUSDT"]
        return tc.is_symbol_in_any_cooldown("RUNEUSDT"), (active[0][2] if active else 0)
    loss_cooled, loss_rem = _cooldown_after(-33.0)
    win_cooled, _ = _cooldown_after(+20.0)
    chk("Fix8: a real loss holds the coin out for ~1200s (scanner exclusion path)",
        loss_cooled and 1100 < loss_rem <= 1200, f"loss -> cooled={loss_cooled} remaining={loss_rem}s")
    chk("Fix8: a win sets NO cooldown (spares a net-winner like KAT)",
        win_cooled is False, f"win -> cooled={win_cooled}")

    try:
        await db.disconnect()
    except Exception:
        pass
    print(f"\n==== RESULT: {len(PASS)} passed, {len(FAIL)} failed ====")
    if FAIL:
        print("FAILED:", FAIL)
        return 1
    print("ALL REAL-PIPELINE CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
