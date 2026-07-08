#!/usr/bin/env python3
"""PF/LC Top-15 Problem 2.5 — deadline-extension dial freeze (conflict item).

The audit measured the deadline-extension re-loosening as a near-wash and
recommended KEEPING it; the code analysis showed the re-loosening DOES leak via
non-stop force-close paths (the dialed stall_min_age_fraction / cap / structure
buffer slide back toward their young anchors as the age fraction drops, and the
stall/cap force-closes are not gated by R1 tighten-only). This is shipped behind
a default-OFF switch for the operator to decide at the gate.

This proves: (a) _pf_age_and_deadline returns the EXTENDED deadline when off and
the ORIGINAL when on; (b) the real loss dial's stall_min_age_fraction DOES
re-loosen under the extended deadline (the leak) and does NOT when frozen.

Run: python3 verify_pf_lc_top15_2_5.py
"""
import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.time_dial import TimeDial
from src.workers.profit_sniper import ProfitSniper


def _age_deadline(freeze):
    f = SimpleNamespace()
    f._pf = SimpleNamespace(dial_freeze_on_original_deadline_enabled=freeze,
                            default_deadline_minutes=50.0)
    plan = SimpleNamespace(age_minutes=29.0, max_hold_minutes=40,
                           _original_max_hold_minutes=30)  # extended 30 -> 40
    f.trade_coordinator = SimpleNamespace(get_trade_plan=lambda s: plan)
    f._tracked = {}
    fn = ProfitSniper._pf_age_and_deadline.__get__(f, ProfitSniper)
    return fn("ENAUSDT")


def _run():
    fails = []

    age_off, dl_off = _age_deadline(False)
    age_on, dl_on = _age_deadline(True)

    if abs(dl_off - 40.0) > 1e-9:
        fails.append(f"off: dial should use the extended deadline 40, got {dl_off}")
    if abs(dl_on - 30.0) > 1e-9:
        fails.append(f"on: dial should use the original deadline 30, got {dl_on}")

    # Downstream: the REAL loss dial's stall_min_age_fraction under each deadline.
    # Extended deadline -> lower age fraction -> value slides toward the looser
    # young anchor (the leak). Frozen deadline -> stays tight.
    dial = TimeDial(Settings.load().loss_cutting)
    smaf_extended = dial.resolve_loss(29.0, 40.0).stall_min_age_fraction
    smaf_frozen = dial.resolve_loss(29.0, 30.0).stall_min_age_fraction
    # young anchor is looser (higher = more patient); old anchor is tighter
    # (lower). A lower age fraction (extended) yields a HIGHER (looser) value.
    if not (smaf_extended > smaf_frozen):
        fails.append(
            "expected the extended deadline to re-loosen stall_min_age_fraction "
            f"(extended={smaf_extended:.4f} should exceed frozen={smaf_frozen:.4f})"
        )

    print(f"  (leak demonstrated: stall_min_age_fraction extended={smaf_extended:.4f} "
          f"vs frozen={smaf_frozen:.4f} — the freeze holds the tighter value)")
    return fails


if __name__ == "__main__":
    fails = _run()
    if fails:
        print("FAIL — PF/LC 2.5 dial-freeze verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — PF/LC 2.5: off uses the extended deadline (re-loosening leaks via "
          "the dialed stall threshold); on freezes the dial on the original deadline "
          "so the extension grants close-timer grace without re-loosening protection. "
          "Default off — conflict (audit: near-wash) escalated for the operator's call.")
