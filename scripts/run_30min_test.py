"""30-minute full system test — all services including Claude + watchdog.

Logs every Claude response, every watchdog action, every trade open/close.
Reports every 60s with full position and plan details.
"""
import asyncio, time, sys, os
os.chdir("/home/inshadaliqbal786/trading-intelligence-mcp")

DURATION = 1800  # 30 minutes
REPORT_INTERVAL = 60  # status every 60s
BRAIN_INTERVAL = 180  # Claude strategic review every 3 min
WATCHDOG_CLAUDE_INTERVAL = 60  # Claude position review every 60s


def ts():
    return time.strftime("%H:%M:%S")


async def main():
    from src.config.settings import Settings
    from src.database.connection import DatabaseManager
    from src.workers.manager import WorkerManager
    from src.core.strategic_plan import StrategicPlan, CoinDirective

    settings = Settings._load_fresh()
    db = DatabaseManager(settings.database.path)
    manager = WorkerManager(settings, db)

    print(f"{'='*70}")
    print(f"  30-MINUTE FULL SYSTEM TEST | {ts()} UTC")
    print(f"  Claude brain review: every {BRAIN_INTERVAL}s")
    print(f"  Claude watchdog review: every {WATCHDOG_CLAUDE_INTERVAL}s")
    print(f"  Watchdog code rules: every 10s")
    print(f"  Strategy pipeline: every 45s")
    print(f"  Status report: every {REPORT_INTERVAL}s")
    print(f"{'='*70}", flush=True)

    await manager.initialize()

    lm = manager._services.get("layer_manager")

    # Seed an initial plan so system can trade immediately
    # Claude will overwrite this on first successful review
    seed_plan = StrategicPlan(
        market_view="Initial seed plan — waiting for first Claude review",
        risk_level="normal", max_positions=3, max_per_coin=1,
        default_sl_pct=2.0, default_tp_pct=2.5, default_hold_minutes=30,
        default_leverage=2, trailing_activation_pct=1.0,
        focus_coins=["BTCUSDT", "ETHUSDT", "SOLUSDT"], avoid_coins=[],
    )
    seed_plan.created_at = time.time()
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
                "ADAUSDT", "DOTUSDT", "LINKUSDT", "AVAXUSDT"]:
        seed_plan.coin_directives[sym] = CoinDirective(
            symbol=sym, direction="both", leverage=2,
            sl_pct=2.0, tp_pct=2.5, max_hold_minutes=30, reason="seed"
        )
    lm._current_plan = seed_plan

    # Configure intervals BEFORE starting layers
    lm.brain_interval_seconds = BRAIN_INTERVAL
    lm.watchdog_claude_interval_seconds = WATCHDOG_CLAUDE_INTERVAL

    # Start all 3 layers PROPERLY (creates brain review + watchdog Claude loops)
    await lm.start_layer(1)
    await lm.start_layer(2)  # starts _brain_review_loop task
    await lm.start_layer(3)  # starts _watchdog_claude_loop task

    print(f"\n  [INIT] Seed plan injected: {len(seed_plan.coin_directives)} coins")
    print(f"  [INIT] Claude calls ENABLED (brain={BRAIN_INTERVAL}s, watchdog={WATCHDOG_CLAUDE_INTERVAL}s)")
    print(f"  [INIT] Starting {len(manager.workers)} workers...", flush=True)

    # Monkey-patch strategist to log full Claude responses
    strategist = manager._services.get("strategist")
    if strategist:
        original_create = strategist.create_strategic_plan
        async def logged_create():
            print(f"\n  [{ts()}] CLAUDE BRAIN REVIEW starting...", flush=True)
            plan = await original_create()
            if plan:
                print(f"  [{ts()}] CLAUDE PLAN RECEIVED:", flush=True)
                print(f"    market_view:   {plan.market_view[:120]}", flush=True)
                print(f"    risk_level:    {plan.risk_level}", flush=True)
                print(f"    max_positions: {plan.max_positions}", flush=True)
                print(f"    SL/TP/Hold:    {plan.default_sl_pct}% / {plan.default_tp_pct}% / {plan.default_hold_minutes}min", flush=True)
                print(f"    leverage:      {plan.default_leverage}x", flush=True)
                print(f"    trailing:      +{plan.trailing_activation_pct}%", flush=True)
                print(f"    focus:         {plan.focus_coins}", flush=True)
                print(f"    avoid:         {plan.avoid_coins}", flush=True)
                if plan.coin_directives:
                    print(f"    directives:    {len(plan.coin_directives)} coins", flush=True)
                    for sym, d in list(plan.coin_directives.items())[:6]:
                        print(f"      {sym}: {d.direction} lev={d.leverage}x sl={d.sl_pct}% tp={d.tp_pct}% hold={d.max_hold_minutes}m | {d.reason[:50]}", flush=True)
                if plan.position_actions:
                    print(f"    pos_actions:   {len(plan.position_actions)}", flush=True)
                    for sym, a in plan.position_actions.items():
                        detail = ""
                        if a.exit_price > 0: detail = f" exit=${a.exit_price:.2f}"
                        if a.new_sl > 0: detail = f" sl=${a.new_sl:.2f}"
                        print(f"      {sym}: {a.action}{detail} | {a.reason[:60]}", flush=True)
            else:
                cc = manager._services.get("claude_client")
                stats = cc.get_stats() if cc else {}
                print(f"  [{ts()}] CLAUDE PLAN FAILED (stats: {stats})", flush=True)
            return plan
        strategist.create_strategic_plan = logged_create

        original_review = strategist.review_positions
        async def logged_review(positions):
            if not positions:
                return {}
            print(f"\n  [{ts()}] CLAUDE WATCHDOG REVIEW ({len(positions)} positions)...", flush=True)
            result = await original_review(positions)
            if result:
                print(f"  [{ts()}] CLAUDE WATCHDOG RESPONSE:", flush=True)
                for sym, action in result.items():
                    if isinstance(action, dict):
                        act = action.get("action", "?")
                        reason = action.get("reason", "")[:80]
                        detail = ""
                        if action.get("exit_price", 0) > 0: detail = f" exit=${action['exit_price']:.2f}"
                        if action.get("new_sl", 0) > 0: detail = f" sl=${action['new_sl']:.2f}"
                        print(f"    {sym}: {act}{detail} | {reason}", flush=True)
                    else:
                        print(f"    {sym}: {action}", flush=True)
            else:
                print(f"  [{ts()}] CLAUDE WATCHDOG: no response or all hold", flush=True)
            return result
        strategist.review_positions = logged_review

    # Start workers
    tasks = [asyncio.create_task(manager._run_worker(w), name=w.name) for w in manager.workers]

    start = time.time()
    last_report = start
    trade_opens = 0
    trade_closes = 0
    watchdog_actions = {"hard_stop": 0, "timer": 0, "early_exit": 0, "timeout": 0,
                        "trailing_on": 0, "trailing_hit": 0, "profit_taken": 0, "duplicate": 0}
    claude_calls = {"brain_ok": 0, "brain_fail": 0, "watchdog_ok": 0, "watchdog_fail": 0}
    plan_updates = 0

    # Track coordinator events
    tc = manager._services.get("trade_coordinator")
    if tc:
        original_register = tc.register_trade
        def logged_register(*args, **kwargs):
            nonlocal trade_opens
            trade_opens += 1
            sym = kwargs.get("symbol", args[0] if args else "?")
            src = kwargs.get("source", "?")
            strat = kwargs.get("strategy_name", "?")
            print(f"  [{ts()}] TRADE OPEN: {sym} source={src} strategy={strat}", flush=True)
            return original_register(*args, **kwargs)
        tc.register_trade = logged_register

        original_close = tc.on_trade_closed
        def logged_close(*args, **kwargs):
            nonlocal trade_closes
            trade_closes += 1
            sym = kwargs.get("symbol", args[0] if args else "?")
            pnl = kwargs.get("pnl_pct", args[1] if len(args) > 1 else 0)
            by = kwargs.get("closed_by", "?")
            win = kwargs.get("was_win", False)
            result = "WIN" if win else "LOSS"
            print(f"  [{ts()}] TRADE CLOSE: {sym} PnL={pnl:+.2f}% by {by} [{result}]", flush=True)
            if by in watchdog_actions:
                watchdog_actions[by] += 1
            return original_close(*args, **kwargs)
        tc.on_trade_closed = logged_close

    print(f"\n  [{ts()}] System running. Monitoring for {DURATION//60} minutes...\n", flush=True)

    while time.time() - start < DURATION:
        await asyncio.sleep(5)
        elapsed = time.time() - start

        # Keep plan fresh to prevent staleness (whether seed or Claude-generated)
        current_plan = lm.get_plan()
        if current_plan:
            current_plan.created_at = time.time()

        if time.time() - last_report >= REPORT_INTERVAL:
            last_report = time.time()
            remaining = DURATION - elapsed
            mins_elapsed = int(elapsed) // 60
            secs_elapsed = int(elapsed) % 60

            print(f"\n{'━'*70}", flush=True)
            print(f"  STATUS @ {mins_elapsed}m{secs_elapsed:02d}s  |  remaining {int(remaining)}s  |  {ts()} UTC", flush=True)
            print(f"{'━'*70}", flush=True)

            # Workers
            print(f"  WORKERS:", flush=True)
            for wn in ["strategy_worker", "position_watchdog", "kline_worker",
                        "enforcer_worker", "price_worker", "fund_manager_worker"]:
                w = next((w for w in manager.workers if w.name == wn), None)
                if w:
                    err_flag = " !!!" if w.error_count > 0 else ""
                    print(f"    {wn:25s} ticks={w.total_ticks:4d} err={w.error_count}{err_flag}", flush=True)

            # Positions
            pos_svc = manager._services.get("position_service")
            if pos_svc:
                try:
                    positions = await pos_svc.get_positions()
                    if positions:
                        print(f"  POSITIONS: {len(positions)}", flush=True)
                        for p in positions:
                            pnl_pct = ((p.mark_price - p.entry_price) / p.entry_price * 100) if p.entry_price > 0 else 0
                            sv = p.side.value if hasattr(p.side, 'value') else str(p.side)
                            if sv in ("Sell", "Short"): pnl_pct = -pnl_pct
                            tp = tc.get_trade_plan(p.symbol) if tc else None
                            ti = tc.get_trade_info(p.symbol) if tc else {}
                            age = f"{tp.age_minutes:.0f}m" if tp else "?"
                            rem = f"{tp.remaining_minutes:.0f}m" if tp else "?"
                            trail = "ON" if tp and tp.trailing_active else "off"
                            sl_p = f"${tp.stop_loss_price:.2f}" if tp else "?"
                            tp_p = f"${tp.target_price:.2f}" if tp else "?"
                            print(f"    {p.symbol:10s} {sv:4s} ${p.entry_price:.2f}->${p.mark_price:.2f} "
                                  f"PnL={pnl_pct:+.2f}% age={age} rem={rem} trail={trail} "
                                  f"SL={sl_p} TP={tp_p} {ti.get('strategy_name','?')}", flush=True)
                    else:
                        print(f"  POSITIONS: 0 open", flush=True)
                except Exception as e:
                    print(f"  POSITIONS: err ({e})", flush=True)

            # Plan
            p = lm.get_plan()
            print(f"  PLAN: age={p.age_seconds:.0f}s stale={p.is_stale} risk={p.risk_level} "
                  f"max_pos={p.max_positions} focus={p.focus_coins[:3]}", flush=True)
            print(f"    view: {p.market_view[:100]}", flush=True)

            # DB
            try:
                st = (await db.fetch_one("SELECT count(*) as c FROM strategy_trades"))["c"]
                kl = (await db.fetch_one("SELECT count(*) as c FROM klines"))["c"]
                print(f"  DB: strategy_trades={st} klines={kl}", flush=True)
            except:
                pass

            # Trade stats
            print(f"  TRADES: {trade_opens} opened, {trade_closes} closed", flush=True)

            # Watchdog actions
            total_wd = sum(watchdog_actions.values())
            if total_wd > 0:
                print(f"  WATCHDOG ACTIONS: {total_wd} total", flush=True)
                for k, v in watchdog_actions.items():
                    if v > 0:
                        print(f"    {k}: {v}", flush=True)

            # Claude stats
            cc = manager._services.get("claude_client")
            if cc:
                stats = cc.get_stats()
                print(f"  CLAUDE: calls={stats['calls_today']} cost=${stats['cost_today']} "
                      f"failures={stats['consecutive_failures']} interval={stats['adaptive_interval']}s", flush=True)

            # Coordinator
            if tc:
                s = tc.get_status()
                print(f"  COORD: {s['active_trades']} active, {s['recent_closes']} total closes", flush=True)
                if s.get('last_close'):
                    lc = s['last_close']
                    print(f"    Last close: {lc['symbol']} {lc['pnl_pct']:+.2f}% by {lc['closed_by']} "
                          f"(held {lc['hold_seconds']:.0f}s)", flush=True)

    # ═══════════ FINAL SUMMARY ═══════════
    print(f"\n{'='*70}", flush=True)
    print(f"  FINAL SUMMARY — 30 MIN RUN | {ts()} UTC", flush=True)
    print(f"{'='*70}", flush=True)

    print(f"\n  ALL WORKERS:", flush=True)
    total_ticks = 0
    total_errors = 0
    for w in sorted(manager.workers, key=lambda x: x.name):
        f = " ***" if w.error_count > 0 else ""
        print(f"    {w.name:30s} ticks={w.total_ticks:4d} err={w.error_count}{f}", flush=True)
        total_ticks += w.total_ticks
        total_errors += w.error_count
    print(f"    {'TOTAL':30s} ticks={total_ticks:4d} err={total_errors}", flush=True)

    print(f"\n  TRADING:", flush=True)
    print(f"    Trades opened:  {trade_opens}", flush=True)
    print(f"    Trades closed:  {trade_closes}", flush=True)
    print(f"    Net positions:  {trade_opens - trade_closes}", flush=True)

    print(f"\n  WATCHDOG ACTIONS:", flush=True)
    for k, v in watchdog_actions.items():
        print(f"    {k:20s} {v}", flush=True)

    print(f"\n  CLAUDE:", flush=True)
    cc = manager._services.get("claude_client")
    if cc:
        stats = cc.get_stats()
        print(f"    Total calls:    {stats['calls_today']}", flush=True)
        print(f"    Total cost:     ${stats['cost_today']}", flush=True)
        print(f"    Failures:       {stats['consecutive_failures']}", flush=True)

    pos_svc = manager._services.get("position_service")
    if pos_svc:
        positions = await pos_svc.get_positions()
        print(f"\n  FINAL POSITIONS: {len(positions)}", flush=True)
        for p in positions:
            pnl = getattr(p, 'unrealized_pnl', 0)
            sv = p.side.value if hasattr(p.side, 'value') else str(p.side)
            print(f"    {p.symbol} {sv} ${p.entry_price:.2f}->${p.mark_price:.2f} PnL=${pnl:+.2f}", flush=True)

    try:
        st = (await db.fetch_one("SELECT count(*) as c FROM strategy_trades"))["c"]
        print(f"\n  DB strategy_trades: {st}", flush=True)
    except:
        pass

    print(f"\n  Shutting down...", flush=True)
    await lm.stop_layer(1)  # cascades: stops 3, 2, 1
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await manager.stop_all()
    print(f"  DONE at {ts()} UTC", flush=True)
    print(f"{'='*70}", flush=True)


asyncio.run(main())
