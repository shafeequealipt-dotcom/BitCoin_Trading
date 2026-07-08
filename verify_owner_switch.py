#!/usr/bin/env python3
"""Phase 1 verification — the trade-state owner switch (exit-authority consolidation).

Read-only self-check (Rule 13). Builds the real SLGateway with the real
SLGatewaySettings loaded from config.toml's [sl_gateway] block, then exercises
the owner gate across the full scenario matrix with concrete values:

  - green trade: only the profit (green) engine is admitted; loss writers deferred
  - red trade: only the loss (red) engine is admitted; profit writers deferred
  - the Head (loss_cap) is admitted on a green trade (the forced-catastrophe case)
  - the always-allowed initial/naked writers are never blocked
  - advisory writers pass in Phase 1 (deferred only when advisory_enforce flips)
  - the faded-winner rule (rearm off -> stays green-owned; on -> red owner)
  - neutral band defaults to the red owner (opening floor baseline)
  - unknown entry and a disabled switch both fail OPEN (admit)
  - log-only mode admits everything (parity); enforce mode defers the wrong owner

Never writes or deletes data. Exits non-zero on any failed assertion.
"""
import asyncio
import sys
import tomllib
from types import SimpleNamespace

from src.config.settings import _build_sl_gateway
from src.core.sl_gateway import SLGateway, REASON_WRONG_OWNER
from src.workers.profit_sniper import ProfitSniper

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")
    if not ok:
        FAILURES.append(name)


def build_gateway(enforce=False, advisory_enforce=False, rearm=False,
                  head_only=True):
    with open("config.toml", "rb") as f:
        toml = tomllib.load(f)
    cfg = _build_sl_gateway(toml.get("sl_gateway", {}))
    # Confirm the new fields actually loaded onto the dataclass.
    assert cfg.owner_switch_enabled is True, "owner_switch_enabled did not load"
    assert cfg.head_only_seizes_green is True, "head_only_seizes_green did not load"
    cfg.owner_switch_enforce = enforce
    cfg.advisory_enforce = advisory_enforce
    cfg.faded_winner_rearm_red = rearm
    cfg.head_only_seizes_green = head_only
    settings = SimpleNamespace(sl_gateway=cfg)

    class _Pos:
        def __init__(self):
            self.set_calls = []

        async def get_position(self, symbol):
            return None

        async def set_stop_loss(self, symbol, new_sl):
            self.set_calls.append((symbol, new_sl))
            return True

    class _Mkt:
        async def get_ticker(self, symbol):
            return None

    class _EB:
        def __init__(self):
            self.events = []

        def add_event(self, priority, event_type, symbol, **data):
            self.events.append((priority, event_type, symbol, data))

    pos = _Pos()
    gw = SLGateway(settings=settings, position_service=pos, market_service=_Mkt(),
                   event_buffer=_EB())
    return gw, pos


def og(gw, *, symbol, source, is_long, entry, price):
    """Call the owner gate and return (admit, state, owner, bucket)."""
    admit, state, owner, bucket, _pnl = gw._owner_gate(
        symbol=symbol, source=source, is_long=is_long,
        entry_price=entry, current_price=price,
    )
    return admit, state, owner, bucket


def test_state_computation():
    print("\n## Trade-state computation (long and short, deadband 0.05%)")
    gw, _ = build_gateway()
    # long green / red / neutral
    check("long +1% -> green", gw._compute_trade_state(True, 100, 101, 0.05)[0], "green")
    check("long -1% -> red", gw._compute_trade_state(True, 100, 99, 0.05)[0], "red")
    check("long +0.02% -> neutral", gw._compute_trade_state(True, 100, 100.02, 0.05)[0], "neutral")
    # short: price below entry is profit
    check("short price<entry -> green", gw._compute_trade_state(False, 100, 99, 0.05)[0], "green")
    check("short price>entry -> red", gw._compute_trade_state(False, 100, 101, 0.05)[0], "red")
    # unknown
    check("entry None -> None", gw._compute_trade_state(True, None, 101, 0.05), None)


def test_owner_matrix():
    print("\n## Owner gate — green/red ownership (long)")
    gw, _ = build_gateway()
    a, s, o, b = og(gw, symbol="G1", source="loss_structure", is_long=True, entry=100, price=101)
    check("green + loss_structure -> defer", (a, s, o, b), (False, "green", "green", "red"))
    a, s, o, b = og(gw, symbol="G2", source="profit_sniper_ladder", is_long=True, entry=100, price=101)
    check("green + ladder -> admit", (a, s, o, b), (True, "green", "green", "green"))
    a, s, o, b = og(gw, symbol="R1", source="loss_structure", is_long=True, entry=100, price=99)
    check("red + loss_structure -> admit", (a, s, o, b), (True, "red", "red", "red"))
    a, s, o, b = og(gw, symbol="R2", source="profit_sniper_ladder", is_long=True, entry=100, price=99)
    check("red + ladder -> defer", (a, s, o, b), (False, "red", "red", "green"))

    print("\n## Owner gate — the Head and the always-allowed writers")
    a, s, o, b = og(gw, symbol="H1", source="loss_cap", is_long=True, entry=100, price=101)
    check("green + loss_cap (Head) -> ADMIT (forced-catastrophe path)", (a, b), (True, "head"))
    a, s, o, b = og(gw, symbol="H2", source="loss_atr_initial", is_long=True, entry=100, price=99)
    check("red + loss_atr_initial (always) -> admit", (a, b), (True, "always"))
    a, s, o, b = og(gw, symbol="H3", source="safety_sweeper", is_long=True, entry=100, price=101)
    check("green + safety_sweeper (always) -> admit", (a, b), (True, "always"))

    print("\n## Owner gate — neutral band defaults to the red owner")
    a, s, o, b = og(gw, symbol="N1", source="profit_sniper_ladder", is_long=True, entry=100, price=100.02)
    check("neutral + ladder -> defer (owner defaults red)", (a, s, o), (False, "neutral", "red"))
    a, s, o, b = og(gw, symbol="N2", source="loss_structure", is_long=True, entry=100, price=100.02)
    check("neutral + loss_structure -> admit (owner defaults red)", (a, s, o), (True, "neutral", "red"))

    print("\n## Owner gate — fail-open safety")
    a, s, o, b = og(gw, symbol="U1", source="loss_structure", is_long=True, entry=None, price=101)
    check("unknown entry -> admit (fail open)", (a, s), (True, "unknown"))
    a, s, o, b = og(gw, symbol="U2", source="mystery_source", is_long=True, entry=100, price=101)
    check("unclassified source -> admit (fail open)", (a, b), (True, "unclassified"))


def test_advisory():
    print("\n## Advisory writers — admitted off-green in Phases 1-4, deferred when advisory_enforce")
    # On a RED trade, advisory writers pass until Phase 5 (advisory_enforce).
    gw, _ = build_gateway(advisory_enforce=False)
    a, s, o, b = og(gw, symbol="A1", source="brain_tighten", is_long=True, entry=100, price=99)
    check("red + brain_tighten, advisory_enforce off -> admit", (a, b), (True, "advisory"))
    gw2, _ = build_gateway(advisory_enforce=True)
    a, s, o, b = og(gw2, symbol="A2", source="brain_tighten", is_long=True, entry=100, price=99)
    check("red + brain_tighten, advisory_enforce on -> defer", (a, b), (False, "advisory"))


def test_phase2_head_override():
    print("\n## Phase 2 — the Head as sole override of a green trade (profit-priority)")
    # head_only_seizes_green ON (default): on a green trade only the Head and the
    # green owner may write; the loss engine and advisory are deferred.
    gw, _ = build_gateway(head_only=True)
    a, s, o, b = og(gw, symbol="P1", source="loss_cap", is_long=True, entry=100, price=101)
    check("green + loss_cap (Head) -> ADMIT (only the Head seizes green)", (a, b), (True, "head"))
    a, s, o, b = og(gw, symbol="P2", source="brain_tighten", is_long=True, entry=100, price=101)
    check("green + advisory, head_only ON -> defer (profit-priority)", (a, b), (False, "advisory"))
    a, s, o, b = og(gw, symbol="P3", source="loss_structure", is_long=True, entry=100, price=101)
    check("green + loss engine -> defer (only the Head seizes green)", (a, b), (False, "red"))
    # head_only_seizes_green OFF: advisory may touch a green trade again.
    gw2, _ = build_gateway(head_only=False)
    a, s, o, b = og(gw2, symbol="P4", source="brain_tighten", is_long=True, entry=100, price=101)
    check("green + advisory, head_only OFF -> admit (pre-Phase-2)", (a, b), (True, "advisory"))
    # The Head still never loosens — proven via apply() R1 below.
    gw_e, pos_e = build_gateway(enforce=True, head_only=True)
    res = asyncio.run(_apply(
        gw_e, symbol="P5", new_sl=98.0, source="loss_cap", direction="Buy",
        current_sl=99.0, current_price=101.0, entry_price=100.0,
    ))
    check("Head loosening a long (98<99) -> rejected by R1 (never loosens)", res.accepted, False)


def test_phase3_green_owner():
    print("\n## Phase 3 — peek_owner (ever-green latch only) and the offer_profit gate")
    gw, _ = build_gateway(enforce=True)
    check("peek_owner green", gw.peek_owner("Q1", True, 100, 101), "green")
    check("peek_owner red", gw.peek_owner("Q2", True, 100, 99), "red")
    # Faded winner: graduate green (sets ever_green), then peek while red.
    gw._owner_gate(symbol="Q3", source="profit_sniper_ladder", is_long=True,
                   entry_price=100, current_price=101)
    check("peek_owner faded(red), rearm off -> green", gw.peek_owner("Q3", True, 100, 99), "green")
    check("peek_owner unknown entry -> unknown", gw.peek_owner("Q4", True, None, 101), "unknown")
    gw_off, _ = build_gateway()
    gw_off._settings.sl_gateway.owner_switch_enabled = False
    check("peek_owner switch off -> unknown", gw_off.peek_owner("Q5", True, 100, 101), "unknown")

    # peek_owner must not mutate the owner HYSTERESIS (_last_owner) or log...
    gw2, _ = build_gateway(enforce=True)
    before = dict(gw2._last_owner)
    gw2.peek_owner("Q6", True, 100, 101)
    check("peek_owner does not mutate _last_owner", gw2._last_owner == before, True)
    # ...but it DOES latch the monotonic ever-green flag (audit fix), so a faded
    # winner is classified reliably even if no gateway write fired while green.
    gw3, _ = build_gateway(enforce=True)
    gw3.peek_owner("Q7", True, 100, 101)  # green tick, no apply()
    check("peek_owner latches ever_green on a green tick", gw3._ever_green.get("Q7"), True)
    check("faded winner after peek-only green -> green-owned (rearm off)",
          gw3.peek_owner("Q7", True, 100, 99), "green")

    # The offer_profit gate on the pure selection function.
    ladder = SimpleNamespace(should_apply=True, ladder_stop_price=99.5)  # long, beats cur 99
    w = ProfitSniper._pf_select_stop(None, ladder, 99.0, True, offer_profit=True)
    check("offer_profit True: ladder wins", (w[0] if w else None), "ladder")
    w = ProfitSniper._pf_select_stop(None, ladder, 99.0, True, offer_profit=False)
    check("offer_profit False: no profit candidate -> None", w, None)
    w = ProfitSniper._pf_select_stop(
        None, ladder, 99.0, True,
        loss_candidates=[("structure", 99.3, "loss_structure")],
        offer_profit=False,
    )
    check("offer_profit False: loss candidate still wins (no starvation of red)", (w[2] if w else None), "loss_structure")
    w = ProfitSniper._pf_select_stop(
        None, ladder, 99.0, True, safety_stop=99.2, offer_profit=False,
    )
    check("offer_profit False: safety (always-allowed) still competes", (w[2] if w else None), "safety_sweeper")


def test_phase4_red_owner():
    print("\n## Phase 4 — loss tools offered only when red; the Head cap competes in both states")
    # offer_loss True (red-owned): the tightest loss tool wins.
    w = ProfitSniper._pf_select_stop(
        None, None, 99.0, True,
        loss_candidates=[("structure", 99.3, "loss_structure"), ("cap", 99.1, "loss_cap")],
        offer_loss=True,
    )
    check("offer_loss True: structure (tightest loss tool) wins", (w[2] if w else None), "loss_structure")
    # offer_loss False (green-owned): structure suppressed, the Head cap still competes.
    w = ProfitSniper._pf_select_stop(
        None, None, 99.0, True,
        loss_candidates=[("structure", 99.3, "loss_structure"), ("cap", 99.1, "loss_cap")],
        offer_loss=False,
    )
    check("offer_loss False: structure suppressed, Head cap competes -> cap wins", (w[2] if w else None), "loss_cap")
    # offer_loss False with only a red-owner tool -> nothing competes.
    w = ProfitSniper._pf_select_stop(
        None, None, 99.0, True,
        loss_candidates=[("recovery", 99.4, "loss_recovery")],
        offer_loss=False,
    )
    check("offer_loss False: lone recovery tool suppressed -> None", w, None)
    # Green-owner end-to-end: profit on, loss off — a TIGHTER loss stop must NOT
    # cage the green trade; the green owner's ladder wins.
    ladder = SimpleNamespace(should_apply=True, ladder_stop_price=99.5)
    w = ProfitSniper._pf_select_stop(
        None, ladder, 99.0, True,
        loss_candidates=[("structure", 99.6, "loss_structure"), ("cap", 99.1, "loss_cap")],
        offer_profit=True, offer_loss=False,
    )
    check("green-owned: ladder wins, tighter structure cannot cage the winner", (w[2] if w else None), "profit_sniper_ladder")


def test_faded_winner():
    print("\n## Faded winner — rearm OFF keeps green-owned; rearm ON hands to red")
    gw, _ = build_gateway(rearm=False)
    # First go green (sets ever_green), then crater to red on the SAME symbol.
    og(gw, symbol="F1", source="profit_sniper_ladder", is_long=True, entry=100, price=101)  # green
    a, s, o, b = og(gw, symbol="F1", source="loss_structure", is_long=True, entry=100, price=99)  # now red
    check("faded(red), rearm off + loss_structure -> defer (stays green-owned)", (a, s, o), (False, "red", "green"))
    a, s, o, b = og(gw, symbol="F1", source="profit_sniper_ladder", is_long=True, entry=100, price=99)
    check("faded(red), rearm off + ladder -> admit (green-owned)", (a, o), (True, "green"))

    gw2, _ = build_gateway(rearm=True)
    og(gw2, symbol="F2", source="profit_sniper_ladder", is_long=True, entry=100, price=101)  # green
    a, s, o, b = og(gw2, symbol="F2", source="loss_structure", is_long=True, entry=100, price=99)
    check("faded(red), rearm ON + loss_structure -> admit (red owner)", (a, s, o), (True, "red", "red"))
    a, s, o, b = og(gw2, symbol="F2", source="profit_sniper_ladder", is_long=True, entry=100, price=99)
    check("faded(red), rearm ON + ladder -> defer (red owner)", (a, o), (False, "red"))


def test_phase5_advisory_demotion():
    print("\n## Phase 5 — advisory writers demoted: deferred, advice routed to the owner")
    # advisory_enforce ON: an advisory write on a RED trade (admitted in Phases
    # 1-4) is now deferred as advisory_defer and routed to the owner.
    gw, pos = build_gateway(enforce=True, advisory_enforce=True)
    res = asyncio.run(_apply(
        gw, symbol="V1", new_sl=98.7, source="brain_tighten", direction="Buy",
        current_sl=98.0, current_price=99.0, entry_price=100.0,  # red trade
    ))
    check("advisory_enforce: brain_tighten on red -> rejected", res.accepted, False)
    check("advisory_enforce: reason is advisory_defer", res.reason, "advisory_defer")
    check("advisory_enforce: no wire push", len(pos.set_calls), 0)
    evs = [e for e in gw._event_buffer.events if e[1] == "sl_gateway_advisory_deferred"]
    check("advisory advice routed to EventBuffer", len(evs) >= 1, True)
    check("routed event carries the proposed stop", evs[0][3].get("new_sl") if evs else None, 98.7)

    # A wrong-owner ENGINE write keeps the wrong_owner reason (not advisory).
    gw2, _ = build_gateway(enforce=True)
    res = asyncio.run(_apply(
        gw2, symbol="V2", new_sl=99.3, source="loss_structure", direction="Buy",
        current_sl=99.0, current_price=101.0, entry_price=100.0,  # green trade
    ))
    check("loss engine on green -> wrong_owner (not advisory)", res.reason, "wrong_owner")

    # The watchdog green-side trails are gateway-enforced as advisory (cannot
    # regress even if subordinate_watchdog_trail_exit ever flips).
    gw3, _ = build_gateway(enforce=True, advisory_enforce=True)
    a, s, o, b, _p = gw3._owner_gate(
        symbol="V3", source="trail_update", is_long=True,
        entry_price=100, current_price=99,
    )
    check("watchdog trail_update is advisory bucket", b, "advisory")
    check("watchdog trail_update deferred under advisory_enforce", a, False)


def test_config_buckets_load():
    print("\n## Config wiring — the bucket lists actually load from config.toml (Rule 9)")
    import tomllib
    from src.config.settings import _build_sl_gateway
    with open("config.toml", "rb") as f:
        data = tomllib.load(f)["sl_gateway"]
    # A bucket-list edit in config must take effect (was silently dropped before
    # the loader fix because the field uses field(default_factory=...)).
    edited = dict(data)
    edited["green_sources"] = list(edited["green_sources"]) + ["operator_test_writer"]
    cfg = _build_sl_gateway(edited)
    check("config edit to green_sources is honored", "operator_test_writer" in cfg.green_sources, True)
    # The factory-default min_distance_class_ceiling also loads (not dropped).
    check("min_distance_class_ceiling loads from config",
          isinstance(cfg.min_distance_class_ceiling, dict) and "dead" in cfg.min_distance_class_ceiling, True)
    # Scalars still load.
    edited2 = dict(data); edited2["breakeven_deadband_pct"] = 0.42
    check("scalar config edit still honored", _build_sl_gateway(edited2).breakeven_deadband_pct, 0.42)


def test_disabled():
    print("\n## Owner switch disabled -> gate is a no-op (admit, state=off)")
    gw, _ = build_gateway()
    gw._settings.sl_gateway.owner_switch_enabled = False
    a, s, o, b = og(gw, symbol="D1", source="loss_structure", is_long=True, entry=100, price=101)
    check("disabled + loss_structure on green -> admit (off)", (a, s), (True, "off"))


async def _apply(gw, **kw):
    return await gw.apply(**kw)


def test_integration_apply():
    print("\n## Integration through apply() — log-only admits, enforce defers")
    # Enforce mode: a loss writer on a green trade is rejected with wrong_owner
    # BEFORE any rule/wire. Distinct symbols avoid rate-limit interference.
    gw_e, pos_e = build_gateway(enforce=True)
    res = asyncio.run(_apply(
        gw_e, symbol="IZ1", new_sl=99.3, source="loss_structure", direction="Buy",
        current_sl=99.0, current_price=101.0, entry_price=100.0,
    ))
    check("enforce: green + loss_structure -> rejected", res.accepted, False)
    check("enforce: reason is wrong_owner", res.reason, REASON_WRONG_OWNER)
    check("enforce: no wire push happened", len(pos_e.set_calls), 0)

    # Enforce: the Head (loss_cap) on a green trade is NOT rejected for ownership
    # (it is admitted past the gate; it then obeys R1 tighten-only).
    gw_e2, _ = build_gateway(enforce=True)
    res = asyncio.run(_apply(
        gw_e2, symbol="IZ2", new_sl=99.3, source="loss_cap", direction="Buy",
        current_sl=99.0, current_price=101.0, entry_price=100.0,
    ))
    check("enforce: Head (loss_cap) on green NOT wrong_owner", res.reason != REASON_WRONG_OWNER, True)
    check("enforce: Head admitted + wired", res.accepted, True)

    # Enforce: the rightful green owner (ladder) on a green trade is admitted.
    gw_e3, _ = build_gateway(enforce=True)
    res = asyncio.run(_apply(
        gw_e3, symbol="IZ3", new_sl=99.3, source="profit_sniper_ladder", direction="Buy",
        current_sl=99.0, current_price=101.0, entry_price=100.0,
    ))
    check("enforce: green owner ladder on green -> accepted", res.accepted, True)

    # Log-only mode: the same wrong-owner write is admitted (parity), with the
    # WOULD-defer logged rather than blocked.
    gw_l, pos_l = build_gateway(enforce=False)
    res = asyncio.run(_apply(
        gw_l, symbol="IZ4", new_sl=99.3, source="loss_structure", direction="Buy",
        current_sl=99.0, current_price=101.0, entry_price=100.0,
    ))
    check("log-only: green + loss_structure -> ADMITTED (parity)", res.accepted, True)
    check("log-only: wire push happened", len(pos_l.set_calls), 1)


def main():
    print("PHASE 1 OWNER-SWITCH VERIFICATION")
    test_state_computation()
    test_owner_matrix()
    test_advisory()
    test_phase2_head_override()
    test_phase3_green_owner()
    test_phase4_red_owner()
    test_phase5_advisory_demotion()
    test_faded_winner()
    test_config_buckets_load()
    test_disabled()
    test_integration_apply()
    print("\n" + ("=" * 60))
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} failed): {', '.join(FAILURES)}")
        sys.exit(1)
    print("RESULT: PASS — all owner-switch scenarios behaved as specified.")
    sys.exit(0)


if __name__ == "__main__":
    main()
