"""Self-verification for E26 — per-symbol, per-venue directional flip evidence.

Confirms (offline / temp-DB, no writes to the live database):
  A. STATIC: SymbolFlipEvidence model + flip_evidence package field; repo
     method get_symbol_flip_evidence; assembler gather + wiring; optimizer
     gate prefers venue evidence; prompts render block + sample constant.
  B. REPO: a temp DB with mixed exchange_mode rows for one symbol returns
     venue-ISOLATED directional counts (live trades do not leak into demo).
  C. GATE: the flip gate uses the venue-isolated count over a contradicting
     pooled trades list, and falls back to the pooled list when the evidence
     is pooled (exchange_mode == "").
  D. PROMPT: the per-coin line renders when the venue sample >= 8 and is
     omitted when sparse; the all-coins situation data is never regressed.
"""

import asyncio
import os
import tempfile
from types import SimpleNamespace


def static_check():
    m = open("src/apex/models.py").read()
    r = open("src/tias/repository.py").read()
    a = open("src/apex/assembler.py").read()
    o = open("src/apex/optimizer.py").read()
    p = open("src/apex/prompts.py").read()
    return {
        "model SymbolFlipEvidence + field": "class SymbolFlipEvidence" in m
        and "flip_evidence: Optional[SymbolFlipEvidence]" in m,
        "repo get_symbol_flip_evidence": "async def get_symbol_flip_evidence" in r
        and "AND exchange_mode = ?" in r,
        "assembler gather + wiring": "_gather_flip_evidence" in a
        and "flip_evidence=flip_evidence" in a
        and "current_mode" in a,
        "optimizer gate prefers venue": "APEX_FLIP_EVIDENCE_VENUE" in o
        and "ev.direction_count(qwen_direction)" in o,
        "prompts render + sample gate": "PER-COIN DIRECTIONAL HISTORY" in p
        and "_FLIP_EVIDENCE_MIN_SAMPLE" in p,
    }


_COLS = ("symbol, direction, strategy_name, strategy_category, closed_by, "
         "entry_price, exit_price, pnl_pct, pnl_usd, win, hold_seconds, "
         "regime, exchange_mode, trade_closed_at, captured_at")


async def _ins(db, sym, d, win, regime, mode):
    await db.execute(
        f"INSERT INTO trade_intelligence ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sym, d, "m", "t", "tp", 100.0, 101.0, 1.0, 5.0, int(win), 60.0,
         regime, mode, "2026-05-28T03:00:00", "2026-05-28T03:00:00"),
    )


async def repo_check():
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.tias.repository import TradeIntelligenceRepo
    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "v.db"))
        await db.connect()
        try:
            await run_migrations(db)
            for w in (True, True, False):
                await _ins(db, "BTCUSDT", "Buy", w, "trending_up", "bybit_demo")
            await _ins(db, "BTCUSDT", "Sell", False, "trending_up", "bybit_demo")
            for _ in range(5):
                await _ins(db, "BTCUSDT", "Sell", True, "trending_up", "bybit")
            repo = TradeIntelligenceRepo(db)
            demo = await repo.get_symbol_flip_evidence("BTCUSDT", "trending_up", "bybit_demo")
            pooled = await repo.get_symbol_flip_evidence("BTCUSDT", "trending_up", "")
            return (demo["buy_count"] == 3 and demo["sell_count"] == 1
                    and demo["total"] == 4 and pooled["sell_count"] == 6), demo, pooled
        finally:
            await db.disconnect()


def _ev(mode, buy, sell, buy_wr=0.0, sell_wr=0.0):
    from src.apex.models import SymbolFlipEvidence
    return SymbolFlipEvidence(symbol="BTCUSDT", exchange_mode=mode, regime="trending_up",
                              buy_count=buy, sell_count=sell, buy_win_rate=buy_wr,
                              sell_win_rate=sell_wr, total=buy + sell)


def _pkg(ev):
    from src.apex.models import (CoinData, DirectiveContext, IntelligencePackage,
                                 TIASSituationData, TIASSymbolHistory)
    return IntelligencePackage(
        directive=DirectiveContext(symbol="BTCUSDT", direction="Buy", sl=100.0,
                                   tp=110.0, leverage=3, size_usd=600,
                                   reasoning="x", plan_view="y"),
        coin_data=CoinData(symbol="BTCUSDT", current_price=105.0),
        symbol_history=TIASSymbolHistory(symbol="BTCUSDT", total_trades=0, wins=0,
                                         losses=0, win_rate=0.0, avg_win_pct=0.0,
                                         avg_loss_pct=0.0, total_pnl_usd=0.0,
                                         ev_per_trade=0.0, trades=[], regime="trending_up"),
        situation_data=TIASSituationData(regime="trending_up", fear_greed=55,
                                         total_trades_in_condition=40, buy_win_rate=55.0,
                                         sell_win_rate=45.0, avg_buy_pnl=0.5,
                                         avg_sell_pnl=-0.2, direction_bias="buy"),
        flip_evidence=ev)


def gate_check():
    from src.apex.optimizer import TradeOptimizer
    from src.config.settings import APEXSettings
    opt = TradeOptimizer.__new__(TradeOptimizer)
    opt._settings = APEXSettings()  # min 8
    # venue says 3 Sell, pooled list says 10 → must use 3 → block
    pkg = _pkg(_ev("bybit_demo", 0, 3))
    pkg.symbol_history.trades = [{"direction": "Sell"}] * 10
    insuff_v, cnt_v = opt._check_insufficient_data_for_flip(pkg, "Buy", "Sell")
    # pooled evidence (mode "") → fall back to pooled list of 9 → allow
    pkg2 = _pkg(_ev("", 0, 0))
    pkg2.symbol_history.trades = [{"direction": "Sell"}] * 9
    insuff_p, cnt_p = opt._check_insufficient_data_for_flip(pkg2, "Buy", "Sell")
    return (insuff_v is True and cnt_v == 3 and insuff_p is False and cnt_p == 9), (cnt_v, cnt_p)


def prompt_check():
    from src.apex.prompts import build_apex_user_prompt
    rich = build_apex_user_prompt(_pkg(_ev("bybit_demo", 5, 4, 60.0, 25.0)))   # 9 >= 8
    sparse = build_apex_user_prompt(_pkg(_ev("bybit_demo", 2, 1)))             # 3 < 8
    return ("PER-COIN DIRECTIONAL HISTORY" in rich
            and "venue=bybit_demo" in rich
            and "PER-COIN DIRECTIONAL HISTORY" not in sparse
            and "TIAS SITUATION DATA" in sparse)


def main():
    s = static_check()
    repo_ok, demo, pooled = asyncio.run(repo_check())
    gate_ok, gate_counts = gate_check()
    prompt_ok = prompt_check()

    print("E26 VERIFICATION — per-symbol, per-venue directional flip evidence")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  REPO venue isolation: {repo_ok} "
          f"(demo buy={demo['buy_count']} sell={demo['sell_count']} total={demo['total']}; "
          f"pooled sell={pooled['sell_count']})")
    print(f"  GATE prefers venue / falls back: {gate_ok} "
          f"(venue_count={gate_counts[0]}, pooled_fallback_count={gate_counts[1]})")
    print(f"  PROMPT sample-gated render: {prompt_ok}")

    ok = all(s.values()) and repo_ok and gate_ok and prompt_ok
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
