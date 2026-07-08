"""6-minute live system test with periodic status reports."""
import asyncio, time, sys, os
os.chdir("/home/inshadaliqbal786/trading-intelligence-mcp")

DURATION = 360

async def main():
    from src.config.settings import Settings
    from src.database.connection import DatabaseManager
    from src.workers.manager import WorkerManager
    from src.core.strategic_plan import StrategicPlan, CoinDirective

    settings = Settings._load_fresh()
    db = DatabaseManager(settings.database.path)
    manager = WorkerManager(settings, db)

    print(f"{'='*70}")
    print(f"  SYSTEM RUN — 6 MIN | {time.strftime('%H:%M:%S UTC')}")
    print(f"{'='*70}", flush=True)

    await manager.initialize()

    lm = manager._services.get("layer_manager")
    plan = StrategicPlan(
        market_view="BTC volatile. Conservative high-conviction only.",
        risk_level="normal", max_positions=3, max_per_coin=1,
        default_sl_pct=2.0, default_tp_pct=2.5, default_hold_minutes=30,
        default_leverage=2, trailing_activation_pct=1.0,
        focus_coins=["BTCUSDT","ETHUSDT","SOLUSDT"], avoid_coins=[],
    )
    plan.created_at = time.time()
    for sym in ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","DOTUSDT","LINKUSDT","AVAXUSDT"]:
        plan.coin_directives[sym] = CoinDirective(symbol=sym, direction="both", leverage=2, sl_pct=2.0, tp_pct=2.5, max_hold_minutes=30, reason="normal")
    lm._current_plan = plan
    lm._layer_active = {1: True, 2: True, 3: True}
    lm._layer_started_at = {1: time.time(), 2: time.time(), 3: time.time()}
    lm.brain_interval_seconds = 99999
    lm.watchdog_claude_interval_seconds = 99999

    print(f"  Plan: {len(plan.coin_directives)} coins | Layers: ALL ON", flush=True)

    tasks = [asyncio.create_task(manager._run_worker(w), name=w.name) for w in manager.workers]
    start = time.time()
    last_report = start

    while time.time() - start < DURATION:
        await asyncio.sleep(5)
        elapsed = time.time() - start

        if time.time() - last_report >= 30:
            last_report = time.time()
            print(f"\n--- STATUS @ {int(elapsed)}s (rem {int(DURATION-elapsed)}s) ---", flush=True)
            for wn in ["strategy_worker","position_watchdog","kline_worker","enforcer_worker"]:
                w = next((w for w in manager.workers if w.name == wn), None)
                if w: print(f"  {wn:25s} ticks={w.total_ticks:3d} err={w.error_count}", flush=True)

            pos_svc = manager._services.get("position_service")
            if pos_svc:
                try:
                    positions = await pos_svc.get_positions()
                    tc = manager._services.get("trade_coordinator")
                    if positions:
                        print(f"  Positions: {len(positions)}", flush=True)
                        for p in positions:
                            pnl_pct = ((p.mark_price - p.entry_price) / p.entry_price * 100) if p.entry_price > 0 else 0
                            sv = p.side.value if hasattr(p.side,'value') else str(p.side)
                            if sv in ("Sell","Short"): pnl_pct = -pnl_pct
                            tp = tc.get_trade_plan(p.symbol) if tc else None
                            ti = tc.get_trade_info(p.symbol) if tc else {}
                            age = f"{tp.age_minutes:.0f}m" if tp else "?"
                            rem = f"{tp.remaining_minutes:.0f}m" if tp else "?"
                            trail = "ON" if tp and tp.trailing_active else "off"
                            print(f"    {p.symbol} {sv} ${p.entry_price:.2f}->${p.mark_price:.2f} PnL={pnl_pct:+.2f}% age={age} rem={rem} trail={trail} {ti.get('strategy_name','?')}", flush=True)
                    else:
                        print(f"  Positions: 0", flush=True)
                except Exception as e:
                    print(f"  Pos err: {e}", flush=True)

            try:
                st = (await db.fetch_one("SELECT count(*) as c FROM strategy_trades"))["c"]
                kl = (await db.fetch_one("SELECT count(*) as c FROM klines"))["c"]
                print(f"  DB: trades={st} klines={kl}", flush=True)
            except: pass

            tc = manager._services.get("trade_coordinator")
            if tc:
                s = tc.get_status()
                print(f"  Coord: {s['active_trades']} active, {s['recent_closes']} closes", flush=True)
                if s.get('last_close'):
                    lc = s['last_close']
                    print(f"    Last: {lc['symbol']} {lc['pnl_pct']:+.2f}% by {lc['closed_by']}", flush=True)

            plan.created_at = time.time()

    print(f"\n{'='*70}", flush=True)
    print(f"  FINAL SUMMARY | {time.strftime('%H:%M:%S UTC')}", flush=True)
    print(f"{'='*70}", flush=True)
    for w in sorted(manager.workers, key=lambda x: x.name):
        f = " ***" if w.error_count > 0 else ""
        print(f"  {w.name:30s} ticks={w.total_ticks:4d} err={w.error_count}{f}", flush=True)

    pos_svc = manager._services.get("position_service")
    if pos_svc:
        positions = await pos_svc.get_positions()
        print(f"\n  POSITIONS: {len(positions)}", flush=True)
        for p in positions:
            pnl = getattr(p,'unrealized_pnl',0)
            sv = p.side.value if hasattr(p.side,'value') else str(p.side)
            print(f"    {p.symbol} {sv} ${p.entry_price:.2f}->${p.mark_price:.2f} PnL=${pnl:+.2f}", flush=True)

    try:
        st = (await db.fetch_one("SELECT count(*) as c FROM strategy_trades"))["c"]
        print(f"\n  strategy_trades: {st}", flush=True)
    except: pass

    tc = manager._services.get("trade_coordinator")
    if tc:
        s = tc.get_status()
        print(f"  Coord: {s['active_trades']} active, {s['recent_closes']} closes", flush=True)

    print(f"\n  Shutting down...", flush=True)
    lm._layer_active = {1: False, 2: False, 3: False}
    for t in tasks: t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await manager.stop_all()
    print(f"  DONE.", flush=True)

asyncio.run(main())
