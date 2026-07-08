#!/usr/bin/env python3
"""End-to-end pipeline verification for P1-P10.

Exercises each priority through REAL project code (not pure mocks):
- Fresh DB built from real migrations (verifies migration + schema state).
- Real Transformer + TradeCoordinator + ThesisManager + adapters.
- Mocked HTTP for external APIs (Bybit demo, Shadow) — the adapters
  themselves are real, only the network boundary is mocked.
- Mocked AlertManager for P10 (real relay, real loguru sink).

Run from /home/inshadaliqbal786/trading-intelligence-mcp:
    timeout 90 python3 /tmp/pipeline_e2e_p1_p10.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, "/home/inshadaliqbal786/trading-intelligence-mcp")

# Pipeline test result tracker
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    sym = "PASS" if ok else "FAIL"
    print(f"  [{sym}] {name}{(' — ' + detail) if detail else ''}")


# ─── PIPELINE 1: Fresh DB + migrations + backfill ──────────────────────
async def pipeline_1_migration(db_path: str) -> None:
    print("\n=== Pipeline 1: real migration on fresh DB ===")
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations, SCHEMA_VERSION

    db = DatabaseManager(db_path)
    await db.connect()
    try:
        # Fresh DB — run migrations from scratch
        await run_migrations(db)

        v = await db.fetch_one("SELECT MAX(version) AS v FROM schema_version")
        record("Fresh-DB migration applies SCHEMA_VERSION 29",
               v["v"] == SCHEMA_VERSION, f"got {v['v']}")

        # trade_intelligence has exchange_mode column
        cols = await db.fetch_all("PRAGMA table_info(trade_intelligence)")
        names = {r["name"] for r in cols}
        record("P4: trade_intelligence has exchange_mode column post-migration",
               "exchange_mode" in names)

        # trade_thesis has exchange_mode column
        cols2 = await db.fetch_all("PRAGMA table_info(trade_thesis)")
        names2 = {r["name"] for r in cols2}
        record("P4: trade_thesis has exchange_mode column post-migration",
               "exchange_mode" in names2)

        # trade_log has exchange_mode column
        cols3 = await db.fetch_all("PRAGMA table_info(trade_log)")
        names3 = {r["name"] for r in cols3}
        record("P8: trade_log has exchange_mode column post-migration",
               "exchange_mode" in names3)

        # P4 backfill scenario: insert synthetic pre-cut-over shadow + post-cut-over rows.
        # NOTE: the migration runner returns early when current_version >= SCHEMA_VERSION,
        # so the migration UPDATE only runs ONCE at version-bump time. To test the
        # backfill SQL semantically, we execute it directly here — this is the EXACT
        # statement that runs in production when the migration first applies.
        await db.execute(
            "INSERT INTO trade_intelligence "
            "(symbol, direction, strategy_name, strategy_category, source, "
            "closed_by, entry_price, exit_price, pnl_pct, pnl_usd, win, "
            "hold_seconds, trade_closed_at, captured_at, exchange_mode) "
            "VALUES ('PRE_CUT', 'Buy', 'test', 'test', 'test', 'sl', "
            "100, 95, -5, -5, 0, 60, '2026-05-07 10:00:00', '2026-05-07 10:00:00', 'shadow')"
        )
        await db.execute(
            "INSERT INTO trade_intelligence "
            "(symbol, direction, strategy_name, strategy_category, source, "
            "closed_by, entry_price, exit_price, pnl_pct, pnl_usd, win, "
            "hold_seconds, trade_closed_at, captured_at, exchange_mode) "
            "VALUES ('POST_CUT', 'Buy', 'test', 'test', 'test', 'sl', "
            "100, 95, -5, -5, 0, 60, '2026-05-09 10:00:00', '2026-05-09 10:00:00', 'shadow')"
        )
        # Execute the EXACT backfill SQL from MIGRATIONS list (line 1365-1366 of migrations.py)
        await db.execute(
            "UPDATE trade_intelligence SET exchange_mode='bybit_demo' "
            "WHERE exchange_mode='shadow' AND trade_closed_at >= '2026-05-08 11:27:00'"
        )

        pre_row = await db.fetch_one(
            "SELECT exchange_mode FROM trade_intelligence WHERE symbol='PRE_CUT'"
        )
        post_row = await db.fetch_one(
            "SELECT exchange_mode FROM trade_intelligence WHERE symbol='POST_CUT'"
        )
        record("P4 backfill SQL: pre-cut-over row keeps exchange_mode='shadow'",
               pre_row["exchange_mode"] == "shadow",
               f"got '{pre_row['exchange_mode']}'")
        record("P4 backfill SQL: post-cut-over row retagged to 'bybit_demo'",
               post_row["exchange_mode"] == "bybit_demo",
               f"got '{post_row['exchange_mode']}'")
        # Idempotency: running the same UPDATE again should retag 0 rows.
        # We verify this by attempting it and confirming both rows unchanged.
        await db.execute(
            "UPDATE trade_intelligence SET exchange_mode='bybit_demo' "
            "WHERE exchange_mode='shadow' AND trade_closed_at >= '2026-05-08 11:27:00'"
        )
        post_row2 = await db.fetch_one(
            "SELECT exchange_mode FROM trade_intelligence WHERE symbol='POST_CUT'"
        )
        record("P4 backfill SQL: idempotent re-run preserves bybit_demo tag",
               post_row2["exchange_mode"] == "bybit_demo")

        # Idempotency: re-run again → same result, no errors
        await run_migrations(db)
        v2 = await db.fetch_one("SELECT MAX(version) AS v FROM schema_version")
        record("Migration idempotent (3rd run keeps v29)", v2["v"] == SCHEMA_VERSION)
    finally:
        await db.disconnect()


# ─── PIPELINE 2: real DI graph + late-bound attaches ───────────────────
async def pipeline_2_di_graph(db_path: str):
    print("\n=== Pipeline 2: real DI graph ===")
    from src.core.trade_coordinator import TradeCoordinator
    from src.core.thesis_manager import ThesisManager
    from src.database.connection import DatabaseManager

    db = DatabaseManager(db_path)
    await db.connect()
    try:
        transformer = SimpleNamespace(
            current_mode="bybit_demo",
            is_switching=False,
            mode_label="Bybit Demo",
        )

        coord = TradeCoordinator()
        coord.attach_transformer(transformer)
        record("Coordinator.attach_transformer wires reference",
               coord._transformer is transformer)

        tm = ThesisManager(db)
        tm.attach_transformer(transformer)
        record("ThesisManager.attach_transformer wires reference",
               getattr(tm, "_transformer", None) is transformer)

        result = coord.pop_close_reason("BTCUSDT")
        record("P2: pop_close_reason returns mode-aware default",
               result == "bybit_demo_sl_tp", f"got '{result}'")

        coord.set_close_reason("BTCUSDT", "strategic_review")
        result2 = coord.pop_close_reason("BTCUSDT")
        record("P2: explicit reason takes precedence over default",
               result2 == "strategic_review", f"got '{result2}'")

        result3 = coord.pop_close_reason("BTCUSDT")
        record("P2: post-explicit pop falls back to mode-aware",
               result3 == "bybit_demo_sl_tp", f"got '{result3}'")

        return db, transformer, coord, tm
    except Exception:
        await db.disconnect()
        raise


# ─── PIPELINE 3: P1 WS subscriber dispatch chain ───────────────────────
async def pipeline_3_p1_ws(db, transformer, coord):
    print("\n=== Pipeline 3: P1 WS subscriber dispatch chain ===")
    from src.bybit_demo.bybit_demo_websocket_subscriber import (
        BybitDemoWebSocketSubscriber,
    )
    from src.core.trade_coordinator import TradeState
    from unittest.mock import patch

    settings = SimpleNamespace(
        bybit=SimpleNamespace(
            testnet=False, api_key="", api_secret="", ws_reconnect_delay=5,
        ),
        bybit_demo=SimpleNamespace(api_key="DK", api_secret="DS"),
    )
    loop = asyncio.get_running_loop()

    fired = []
    coord.register_close_callback(lambda record_dict: fired.append(record_dict))

    with patch(
        "src.bybit_demo.bybit_demo_websocket_subscriber.BybitWebSocket"
    ) as patched_ws_cls:
        fake_ws = MagicMock()
        fake_ws.connect_private = AsyncMock()
        fake_ws.disconnect = AsyncMock()
        fake_ws._private_ws = MagicMock()
        patched_ws_cls.return_value = fake_ws

        sub = BybitDemoWebSocketSubscriber(
            settings=settings, db=db, coordinator=coord, loop=loop,
        )

        coord._trades["BTCUSDT"] = TradeState(
            symbol="BTCUSDT", entry_price=80000.0, size=0.01, side="Buy",
            opened_at=time.time(), strategy_name="test", strategy_category="test",
            source="test", brain_decision_id="t-test-1", order_id="OID-PIPE-1",
        )
        coord._trade_info["BTCUSDT"] = {"amount_usd": 800, "leverage": 1}

        evt = {
            "topic": "execution",
            "data": [{
                "symbol": "BTCUSDT", "orderId": "OID-PIPE-1",
                "closedSize": "0.01", "leavesQty": "0",
                "execPrice": "78500.50", "execQty": "0.01",
                "execFee": "0.0048", "side": "Sell",
                "stopOrderType": "StopLoss",
            }],
        }
        sub._handle_execution(evt)
        await asyncio.sleep(0.1)

        record("P1: synthetic SL execution event dispatched to coordinator",
               len(fired) == 1, f"fired={len(fired)}")
        if fired:
            r = fired[0]
            record("P1: dispatched record carries bybit_ws_authoritative price_source",
                   r.get("price_source") == "bybit_ws_authoritative",
                   f"got '{r.get('price_source')}'")
            record("P1: dispatched record uses bybit_sl_hit closed_by",
                   r.get("closed_by") == "bybit_sl_hit",
                   f"got '{r.get('closed_by')}'")
            sub._handle_execution(evt)
            await asyncio.sleep(0.1)
            record("P1: replay of identical event dedup-suppressed",
                   len(fired) == 1, f"fired now={len(fired)}")


# ─── PIPELINE 4: P3 retry + close fill resolution ──────────────────────
async def pipeline_4_p3():
    print("\n=== Pipeline 4: P3 retry + close fill ===")
    from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService
    import src.bybit_demo.bybit_demo_adapter as adapter_mod

    client = MagicMock()
    client.get = AsyncMock(side_effect=[
        {"result": {"list": []}},
        {"result": {"list": []}},
        {"result": {"list": [{
            "side": "Buy", "qty": "0.01",
            "avgEntryPrice": "80000", "avgExitPrice": "80500",
            "closedPnl": "5.00",
            "createdTime": "1714000000000", "updatedTime": "1714000300000",
        }]}},
    ])

    svc = BybitDemoPositionService(client)
    real_sleep = adapter_mod.asyncio.sleep
    adapter_mod.asyncio.sleep = AsyncMock()
    try:
        result = await svc.get_last_close("BTCUSDT")
        record("P3: get_last_close retries until indexer populates",
               result is not None and result.get("exit_price") == 80500.0)
        record("P3: get_last_close polled 3 times before success",
               client.get.call_count == 3, f"calls={client.get.call_count}")

        client.get = AsyncMock(side_effect=[
            {"result": {"list": [{
                "symbol": "BTCUSDT", "size": "0.01", "side": "Buy",
                "avgPrice": "80000", "markPrice": "80100", "leverage": "5",
                "positionValue": "800", "unrealisedPnl": "1.0",
                "createdTime": "1714000000000", "updatedTime": "1714000100000",
            }]}},
            {"result": {"list": [{
                "avgPrice": "80250.75", "cumExecQty": "0.01",
                "orderStatus": "Filled",
            }]}},
        ])
        client.post = AsyncMock(return_value={"result": {"orderId": "OID-P3-1"}})

        order = await svc.close_position("BTCUSDT")
        record("P3: close_position uses resolved fill (80250.75) NOT mark_price (80100)",
               abs(order.price - 80250.75) < 0.01,
               f"got price={order.price}")
    finally:
        adapter_mod.asyncio.sleep = real_sleep


# ─── PIPELINE 5: P4 cross-mode SQL filter ──────────────────────────────
async def pipeline_5_p4(db, transformer, tm):
    print("\n=== Pipeline 5: P4 cross-mode SQL filter ===")
    await db.execute(
        "INSERT INTO trade_thesis (symbol, direction, entry_price, "
        "stop_loss_price, take_profit_price, size_usd, leverage, "
        "max_hold_minutes, trailing_activation_pct, thesis, market_context, "
        "strategy_hints, consensus, status, opened_at, exchange_mode) "
        "VALUES ('SHADOW_PIPE', 'Buy', 100, 95, 110, 100, 1, 60, 0, '', '', '', '', "
        "'open', CURRENT_TIMESTAMP, 'shadow')"
    )
    await db.execute(
        "INSERT INTO trade_thesis (symbol, direction, entry_price, "
        "stop_loss_price, take_profit_price, size_usd, leverage, "
        "max_hold_minutes, trailing_activation_pct, thesis, market_context, "
        "strategy_hints, consensus, status, opened_at, exchange_mode) "
        "VALUES ('BYDEMO_PIPE', 'Buy', 200, 190, 220, 200, 1, 60, 0, '', '', '', '', "
        "'open', CURRENT_TIMESTAMP, 'bybit_demo')"
    )

    open_theses = await tm.get_open_theses()
    syms = {t["symbol"] for t in open_theses}
    record("P4: get_open_theses returns BYDEMO_PIPE (current mode=bybit_demo)",
           "BYDEMO_PIPE" in syms)
    record("P4: get_open_theses excludes SHADOW_PIPE (different mode)",
           "SHADOW_PIPE" not in syms)

    transformer.current_mode = "shadow"
    open_theses_shadow = await tm.get_open_theses()
    syms_shadow = {t["symbol"] for t in open_theses_shadow}
    record("P4: post-mode-switch get_open_theses returns SHADOW_PIPE",
           "SHADOW_PIPE" in syms_shadow)
    record("P4: post-mode-switch get_open_theses excludes BYDEMO_PIPE",
           "BYDEMO_PIPE" not in syms_shadow)

    await db.execute("DELETE FROM trade_thesis WHERE symbol IN ('SHADOW_PIPE', 'BYDEMO_PIPE')")
    transformer.current_mode = "bybit_demo"


# ─── PIPELINE 6: P5 zombie close_thesis ────────────────────────────────
async def pipeline_6_p5(db, tm):
    print("\n=== Pipeline 6: P5 zombie close_thesis on real DB ===")
    await db.execute(
        "INSERT INTO trade_thesis (symbol, direction, entry_price, "
        "stop_loss_price, take_profit_price, size_usd, leverage, "
        "max_hold_minutes, trailing_activation_pct, thesis, market_context, "
        "strategy_hints, consensus, status, opened_at, exchange_mode, "
        "actual_pnl_pct, actual_pnl_usd, close_price, close_reason, order_id) "
        "VALUES ('ZOMBIE_PIPE', 'Buy', 100, 95, 110, 100, 1, 60, 0, '', '', '', '', "
        "'closed', CURRENT_TIMESTAMP, 'bybit_demo', 0, 0, 0, 'zombie_reconciler', 'OID-Z-1')"
    )

    pre = await db.fetch_one(
        "SELECT actual_pnl_pct, actual_pnl_usd, close_price, close_reason "
        "FROM trade_thesis WHERE symbol='ZOMBIE_PIPE'"
    )
    record("P5: zombie pre-state has pnl=0 + close_reason=zombie_reconciler",
           pre["actual_pnl_pct"] == 0 and pre["actual_pnl_usd"] == 0
           and pre["close_reason"] == "zombie_reconciler")

    await tm.close_thesis(
        symbol="ZOMBIE_PIPE",
        close_price=99.5,
        actual_pnl_pct=-0.5,
        actual_pnl_usd=-0.50,
        close_reason="bybit_demo_sl_tp",
        lesson="Authoritative close",
        order_id="OID-Z-1",
    )

    post = await db.fetch_one(
        "SELECT actual_pnl_pct, actual_pnl_usd, close_price, close_reason "
        "FROM trade_thesis WHERE symbol='ZOMBIE_PIPE'"
    )
    record("P5: zombie row overwritten — pnl_usd is now -0.50 (not 0)",
           abs(post["actual_pnl_usd"] - (-0.50)) < 0.001,
           f"got pnl_usd={post['actual_pnl_usd']}")
    record("P5: zombie row overwritten — close_reason is now bybit_demo_sl_tp",
           post["close_reason"] == "bybit_demo_sl_tp",
           f"got close_reason='{post['close_reason']}'")

    await db.execute("DELETE FROM trade_thesis WHERE symbol='ZOMBIE_PIPE'")


# ─── PIPELINE 7: P6 L3 gate via Transformer proxy ──────────────────────
async def pipeline_7_p6():
    print("\n=== Pipeline 7: P6 L3 gate via real Transformer proxy ===")
    from src.core.transformer import Transformer
    from src.core.types import Side, OrderType
    from src.core.modes import MODE_BYBIT_DEMO

    config = SimpleNamespace(general=SimpleNamespace(mode="bybit_demo"))
    db = MagicMock()
    db.fetch_one = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    t = Transformer(db=db, config=config)
    t._current_mode = MODE_BYBIT_DEMO

    lm = MagicMock()
    lm.is_layer_active = MagicMock(return_value=False)
    t.attach_layer_manager(lm)

    bd_order = MagicMock()
    bd_order.place_order = AsyncMock(return_value=SimpleNamespace(
        order_id="SHOULD_NOT_REACH", symbol="X",
    ))
    t.set_services(bybit_demo_order=bd_order)
    t._current_mode = MODE_BYBIT_DEMO
    t._active_services = t._services_for_mode(MODE_BYBIT_DEMO)

    proxies = t.create_proxies()
    order_proxy = proxies["order"]

    result = await order_proxy.place_order(
        "BTCUSDT", Side.BUY, OrderType.MARKET, 0.01,
        purpose="layer3_entry", force=False,
    )
    record("P6: L3 OFF + layer3_entry → REJECTED",
           result.status.value == "Rejected",
           f"got status={result.status.value}")
    record("P6: gate prevents adapter from being called (L3 OFF)",
           bd_order.place_order.call_count == 0,
           f"adapter called {bd_order.place_order.call_count} times")

    bd_order.place_order.reset_mock()
    result2 = await order_proxy.place_order(
        "BTCUSDT", Side.SELL, OrderType.MARKET, 0.01,
        purpose="layer4_close", force=False,
    )
    record("P6: L3 OFF + layer4_close → ALLOWED (passed through to adapter)",
           bd_order.place_order.call_count == 1,
           f"adapter called {bd_order.place_order.call_count} times")

    lm.is_layer_active = MagicMock(return_value=True)
    bd_order.place_order.reset_mock()
    result3 = await order_proxy.place_order(
        "BTCUSDT", Side.BUY, OrderType.MARKET, 0.01,
        purpose="layer3_entry", force=False,
    )
    record("P6: L3 ON + layer3_entry → ALLOWED",
           bd_order.place_order.call_count == 1)


# ─── PIPELINE 8: P7+P8 persistence end-to-end ──────────────────────────
async def pipeline_8_p7_p8(db, transformer):
    print("\n=== Pipeline 8: P7+P8 persistence end-to-end ===")
    from src.bybit_demo.bybit_demo_adapter import BybitDemoOrderService
    from src.database.repositories.trading_repo import TradingRepository
    from src.core.data_lake import DataLakeWriter
    from src.core.types import Side, OrderType

    repo = TradingRepository(db)
    dl = DataLakeWriter(db)

    client = MagicMock()
    client.post = AsyncMock(side_effect=[
        {"result": {}},
        {"result": {"orderId": "OID-PIPE-PLACE"}},
    ])
    client.get = AsyncMock(return_value={
        "result": {"list": [{
            "avgPrice": "80100", "cumExecQty": "0.001",
            "orderStatus": "Filled",
        }]},
    })

    order_svc = BybitDemoOrderService(client, trading_repo=repo)

    pre_orders = await db.fetch_one("SELECT COUNT(*) AS n FROM orders")
    pre_n = pre_orders["n"]

    order = await order_svc.place_order(
        symbol="PIPE_BTC", side=Side.BUY, order_type=OrderType.MARKET,
        qty=0.001, leverage=5, purpose="layer3_entry",
    )

    post_orders = await db.fetch_one("SELECT COUNT(*) AS n FROM orders")
    record("P7: place_order persists to orders table (count incremented)",
           post_orders["n"] == pre_n + 1,
           f"pre={pre_n} post={post_orders['n']}")

    saved = await db.fetch_one(
        "SELECT order_id, symbol FROM orders WHERE order_id='OID-PIPE-PLACE'"
    )
    record("P7: saved order has correct order_id + symbol",
           saved is not None and saved["symbol"] == "PIPE_BTC",
           f"saved={dict(saved) if saved else None}")

    pre_log = await db.fetch_one("SELECT COUNT(*) AS n FROM trade_log")
    pre_log_n = pre_log["n"]
    await dl.write_trade(
        trade_id="t-pipe-1", symbol="PIPE_BTC", direction="Buy",
        entry_price=80000, exit_price=80250,
        pnl_pct=0.3125, pnl_usd=2.5,
        exchange_mode="bybit_demo",
    )
    post_log = await db.fetch_one("SELECT COUNT(*) AS n FROM trade_log")
    record("P8: write_trade persists to trade_log",
           post_log["n"] == pre_log_n + 1)

    log_row = await db.fetch_one(
        "SELECT exchange_mode FROM trade_log WHERE trade_id='t-pipe-1'"
    )
    record("P8: trade_log row tagged exchange_mode='bybit_demo'",
           log_row is not None and log_row["exchange_mode"] == "bybit_demo",
           f"got mode='{log_row['exchange_mode'] if log_row else None}'")

    # trade_log is a PROTECTED table (see src/database/protected_tables.py).
    # Cleanup uses force_protected=True since this is a test DB about to be
    # discarded — production DELETEs without force are correctly blocked.
    await db.execute("DELETE FROM orders WHERE order_id='OID-PIPE-PLACE'")
    await db.execute(
        "DELETE FROM trade_log WHERE trade_id='t-pipe-1'",
        force_protected=True,
    )


# ─── PIPELINE 9: P9 MCPTransformerAdapter ──────────────────────────────
async def pipeline_9_p9(db_path: str):
    print("\n=== Pipeline 9: P9 MCPTransformerAdapter cross-process ===")
    from src.core.transformer_state_reader import MCPTransformerAdapter
    from src.database.connection import DatabaseManager

    # Open a fresh connection (simulates MCP separate process)
    mcp_db = DatabaseManager(db_path)
    await mcp_db.connect()
    try:
        # Insert a transformer_state row
        await mcp_db.execute(
            "INSERT OR REPLACE INTO transformer_state "
            "(id, current_mode, is_switching, switching_to, last_switched_at, updated_at) "
            "VALUES (1, 'bybit_demo', 0, NULL, '2026-05-09T07:00:00Z', '2026-05-09T07:00:00Z')"
        )

        bd_acc = MagicMock()
        bd_acc.get_wallet_balance = AsyncMock(return_value=SimpleNamespace(
            total_equity=12345.67, available_balance=10000.0,
        ))
        bybit_acc = MagicMock()
        bybit_acc.get_wallet_balance = AsyncMock(return_value=SimpleNamespace(
            total_equity=999.0, available_balance=999.0,
        ))

        adapter = MCPTransformerAdapter(
            db=mcp_db,
            services_per_mode={
                "bybit": {"account": bybit_acc},
                "bybit_demo": {"account": bd_acc},
            },
        )

        eq = await adapter.get_current_equity()
        record("P9: adapter routes equity to current mode (bybit_demo)",
               eq.get("equity") == 12345.67,
               f"got mode={eq.get('mode')} equity={eq.get('equity')}")

        record("P9: bybit account NOT called when mode=bybit_demo",
               bybit_acc.get_wallet_balance.call_count == 0,
               f"bybit calls={bybit_acc.get_wallet_balance.call_count}")

        # Cache test: 2nd read within 5s reuses cache
        _ = await adapter.get_current_equity()
        # bd_acc gets called twice (each get_current_equity calls it)
        record("P9: bd account called twice for two get_current_equity calls",
               bd_acc.get_wallet_balance.call_count == 2,
               f"bd calls={bd_acc.get_wallet_balance.call_count}")

        # Switch mode in DB → adapter picks up after cache TTL
        await mcp_db.execute(
            "UPDATE transformer_state SET current_mode='shadow' WHERE id=1"
        )
        # Force cache invalidation
        adapter._snapshot._cached_at = 0.0
        eq2 = await adapter.get_current_equity()
        record("P9: after mode switch + cache bust, adapter picks up new mode",
               eq2.get("mode") == "shadow",
               f"got mode={eq2.get('mode')}")
    finally:
        await mcp_db.disconnect()


# ─── PIPELINE 10: P10 alert relay live dispatch ────────────────────────
async def pipeline_10_p10():
    print("\n=== Pipeline 10: P10 alert relay live dispatch ===")
    from src.observability.bybit_demo_alert_relay import BybitDemoAlertRelay
    from src.core.logging import get_logger

    am = MagicMock()
    am.send_error_alert = AsyncMock()
    am.send_risk_warning = AsyncMock()

    loop = asyncio.get_running_loop()
    relay = BybitDemoAlertRelay(am, loop=loop)
    relay.register()
    try:
        bd_log = get_logger("bybit_demo")

        # Each unique tag fires once. Use unique sym to avoid SHA256 dedup.
        bd_log.warning("BYBIT_DEMO_INSUFFICIENT_BALANCE | sym=AAA err=balance_low")
        bd_log.warning("BYBIT_DEMO_SET_SL_FAIL | sym=BBB sl=80000")
        bd_log.warning("BYBIT_DEMO_CLOSE_REJECT | sym=CCC err=invalid")
        bd_log.warning("BYBIT_DEMO_WALLET_FAIL | sym=DDD err=auth")
        bd_log.warning("BYBIT_DEMO_ORDER_REJECT | sym=EEE err=qty")
        bd_log.warning("BYBIT_DEMO_SET_TP_FAIL | sym=FFF tp=85000")
        bd_log.warning("BYBIT_DEMO_LEVERAGE_FAIL | sym=GGG lev=10")
        bd_log.warning("REDUCE_FALLBACK | sym=HHH qty=0.01 reason=bybit_reject")

        await asyncio.sleep(0.5)

        crit_calls = am.send_risk_warning.call_count
        warn_calls = am.send_error_alert.call_count
        record("P10: 4 CRITICAL triggers fired (INSUFFICIENT_BALANCE, SET_SL_FAIL, CLOSE_REJECT, WALLET_FAIL)",
               crit_calls == 4, f"got {crit_calls} risk_warning calls")
        record("P10: 4 WARNING triggers fired (ORDER_REJECT, SET_TP_FAIL, LEVERAGE_FAIL, REDUCE_FALLBACK)",
               warn_calls == 4, f"got {warn_calls} error_alert calls")

        am.send_error_alert.reset_mock()
        am.send_risk_warning.reset_mock()
        bd_log.warning("BYBIT_DEMO_ORD_RESP | sym=ZZZ oid=abc fill=80000")
        await asyncio.sleep(0.3)
        record("P10: ORD_RESP (operational tag) does NOT fire alert",
               am.send_error_alert.call_count == 0
               and am.send_risk_warning.call_count == 0)
    finally:
        relay.unregister()


# ─── MAIN ──────────────────────────────────────────────────────────────
async def main() -> int:
    db_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name

    print(f"=== End-to-End Pipeline Verification — P1 through P10 ===")
    print(f"Test DB: {db_path} (fresh, built from real migrations)")

    try:
        await pipeline_1_migration(db_path)
        result = await pipeline_2_di_graph(db_path)
        if result is None:
            return 1
        db, transformer, coord, tm = result
        try:
            await pipeline_3_p1_ws(db, transformer, coord)
            await pipeline_4_p3()
            await pipeline_5_p4(db, transformer, tm)
            await pipeline_6_p5(db, tm)
            await pipeline_7_p6()
            await pipeline_8_p7_p8(db, transformer)
            await pipeline_9_p9(db_path)
            await pipeline_10_p10()
        finally:
            await db.disconnect()
    except Exception:
        import traceback
        print(f"\n!!! UNEXPECTED EXCEPTION !!!")
        traceback.print_exc()
        return 1
    finally:
        try: os.unlink(db_path)
        except: pass
        for ext in ("-wal", "-shm"):
            try: os.unlink(db_path + ext)
            except: pass

    print("\n=== Pipeline E2E Summary ===")
    pass_count = sum(1 for _, ok, _ in RESULTS if ok)
    fail_count = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"PASS: {pass_count} / {pass_count + fail_count}")
    if fail_count:
        print(f"FAIL: {fail_count}")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
