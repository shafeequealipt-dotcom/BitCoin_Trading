"""Issue C Phase 3b + 3c — mature-stall valve refinements.

Two changes share the same code path in ``_stall_escape_action``:

- 3b: peak-protected stall extension. Positions whose
  ``state.peak_pnl_pct`` ever crossed
  ``peak_protection_threshold_pct`` get the extended
  ``peak_protected_full_after_ticks`` (default 80) instead of the base
  ``stall_escape_full_after_ticks`` (default 40) before the valve
  fires.

- 3c: recovering-PnL gate. Even when the tick threshold would
  otherwise force the valve, a position rebounding by at least
  ``recovering_threshold_pct`` from its worst observed PnL is given
  another tick; the valve re-evaluates next tick.

The 13:00–16:00 UTC 2026-05-08 audit window had four mature-stall
valve closures (SANDUSDT, INJUSDT, ARBUSDT, HYPERUSDT). With the
defaults shipped here:

- SANDUSDT (peak +0.06 %) — no peak protection; ticks=181 still
  fires the valve. Runaway-loss protection preserved.
- INJUSDT (peak +0.30 %) — peak qualifies; extended threshold spares
  it for 80 ticks; recovery gate gives further protection if
  rebounding from worst.
- ARBUSDT (peak +0.13 %) — peak qualifies; same as INJUSDT.
- HYPERUSDT (peak 0 %) — never reached profit; valve fires at base
  threshold. Runaway-loss protection preserved.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.config.settings import Layer4SniperSettings, Mode4Settings
from src.workers.profit_sniper import ProfitSniper


def _make_sniper(
    *,
    sniper_cfg: Layer4SniperSettings | None = None,
    full_after_ticks: int = 40,
    peak_pnl_pct: float | None = None,
) -> ProfitSniper:
    """Build a minimal ProfitSniper with the bare minimum state to
    drive ``_stall_escape_action`` through the mature-stall valve.

    Mirrors the pattern in ``test_age_guard.py``:
    - ``ProfitSniper.__new__`` skips heavy ``__init__``.
    - ``mode4`` carries the base tick / cooldown knobs.
    - ``layer4_sniper`` defaults to the production dataclass so the
      new Phase 3b/3c knobs read correctly.
    - Trade coordinator returns 99999 s so the age guard always
      passes (we are testing the valve, not the age guard).
    - ``_profit_states`` is populated when ``peak_pnl_pct`` is given,
      so the new peak-protection branch has data to read.
    """
    sw = ProfitSniper.__new__(ProfitSniper)
    cfg = Mode4Settings()
    # Partial-cap unreachable so the cap-exhausted-→-full path never
    # competes with the mature-stall valve under test.
    cfg.max_partials_per_position = 9999
    cfg.stall_escape_partial_after_ticks = 1   # fire fast in tests
    cfg.stall_escape_full_after_ticks = full_after_ticks
    cfg.stall_escape_cooldown_seconds = 0
    cfg.stall_tighten_max_applications = 9999
    cfg.partial_to_partial_grace_ticks = 0
    cfg.partial_to_full_grace_ticks = 0
    cfg.stall_recovery_threshold_pct = 0.15

    if sniper_cfg is None:
        sniper_cfg = Layer4SniperSettings()
        # Defaults: profit_protection_threshold=0.0 (blocks pnl > 0 —
        # not relevant for losing-pnl tests) and development_window_
        # lower=-0.3 (blocks pnl in (-0.3, 0]). Tests below use
        # pnl <= -0.5 so both guards pass through naturally.

    sw.settings = MagicMock()
    sw.settings.mode4 = cfg
    sw.settings.layer4_sniper = sniper_cfg

    coord = MagicMock()
    coord.get_age_seconds.return_value = 99999.0
    sw.trade_coordinator = coord

    if peak_pnl_pct is not None:
        sw._profit_states = {
            "BTCUSDT": SimpleNamespace(peak_pnl_pct=peak_pnl_pct),
            "ETHUSDT": SimpleNamespace(peak_pnl_pct=peak_pnl_pct),
            "INJUSDT": SimpleNamespace(peak_pnl_pct=peak_pnl_pct),
            "ARBUSDT": SimpleNamespace(peak_pnl_pct=peak_pnl_pct),
            "SANDUSDT": SimpleNamespace(peak_pnl_pct=peak_pnl_pct),
            "HYPERUSDT": SimpleNamespace(peak_pnl_pct=peak_pnl_pct),
        }
    return sw


def _drive_to_tick(sw: ProfitSniper, symbol: str, tick_target: int,
                   pnl_pct: float = -0.5) -> dict:
    """Repeatedly call ``_stall_escape_action`` until ``_stall_ticks``
    reaches ``tick_target`` or the valve fires.

    The partial-cap is set unreachably high in ``_make_sniper`` so the
    partial-cap-exhausted-→-full path doesn't fire prematurely; the
    only way to get a ``full_close`` return in these tests is the
    mature-stall valve. ``partial_close`` returns are normal during
    the run and counted as "not full_close".

    Returns the ``tracked`` dict for inspection.
    """
    tracked: dict = {
        "_partials_emitted": 0,
        "last_score": {"pnl_pct": pnl_pct},
    }
    last_action: str | None = None
    for _ in range(tick_target + 5):
        a = sw._stall_escape_action(symbol, tracked, True, "hold")
        last_action = a
        if a == "full_close":
            break
    tracked["_last_action_returned"] = last_action
    return tracked


# ─── 3b — Peak-protected stall extension ─────────────────────────────


class TestPeakProtection:
    def test_peak_below_threshold_uses_base_full_after(self) -> None:
        """HYPERUSDT-shape: peak never crossed threshold (0.00 %).
        Valve fires at base ``stall_escape_full_after_ticks=40``."""
        sw = _make_sniper(full_after_ticks=40, peak_pnl_pct=0.00)
        tracked = _drive_to_tick(sw, "HYPERUSDT", tick_target=42)
        # Valve fired at ticks just past 40.
        assert tracked["_last_action_returned"] == "full_close"
        assert tracked["_stall_ticks"] == 41 or tracked["_stall_ticks"] == 42

    def test_peak_at_threshold_extends_to_peak_protected(self) -> None:
        """INJUSDT-shape: peak +0.30 % >> threshold +0.10 %. Valve
        does NOT fire at base 40 ticks; only fires at extended 80."""
        sniper_cfg = Layer4SniperSettings()
        # development_window_lower stays at default -0.3 — test pnls below it
        # profit_protection_threshold stays at default 0.0 — test pnls negative
        sniper_cfg.peak_protection_threshold_pct = 0.10
        sniper_cfg.peak_protected_full_after_ticks = 80
        sniper_cfg.recovering_threshold_pct = 0.0  # disable recovery gate for this test
        sw = _make_sniper(
            sniper_cfg=sniper_cfg,
            full_after_ticks=40,
            peak_pnl_pct=0.30,
        )
        # Drive past base threshold (45 ticks). Valve must NOT fire yet.
        tracked = _drive_to_tick(sw, "INJUSDT", tick_target=45)
        assert tracked["_last_action_returned"] != "full_close", (
            "peak-qualifying position should be spared at ticks=45 "
            "under extended threshold 80"
        )

    def test_peak_qualifies_eventually_kills_at_extended_threshold(
        self,
    ) -> None:
        """Even with peak protection, a stuck position eventually dies
        at the extended threshold. Confirms no infinite-hold case."""
        sniper_cfg = Layer4SniperSettings()
        # development_window_lower stays at default -0.3 — test pnls below it
        # profit_protection_threshold stays at default 0.0 — test pnls negative
        sniper_cfg.peak_protection_threshold_pct = 0.10
        sniper_cfg.peak_protected_full_after_ticks = 80
        sniper_cfg.recovering_threshold_pct = 0.0
        sw = _make_sniper(
            sniper_cfg=sniper_cfg,
            full_after_ticks=40,
            peak_pnl_pct=0.30,
        )
        # Drive to 85 — past extended threshold of 80.
        tracked = _drive_to_tick(sw, "INJUSDT", tick_target=85)
        assert tracked["_last_action_returned"] == "full_close"

    def test_peak_protection_disabled_when_threshold_zero(self) -> None:
        """Setting peak_protection_threshold_pct=0 disables the
        extension (kill-switch). Even a profitable peak gets only the
        base full_after threshold."""
        sniper_cfg = Layer4SniperSettings()
        # development_window_lower stays at default -0.3 — test pnls below it
        # profit_protection_threshold stays at default 0.0 — test pnls negative
        sniper_cfg.peak_protection_threshold_pct = 0.0
        sniper_cfg.peak_protected_full_after_ticks = 80
        sniper_cfg.recovering_threshold_pct = 0.0
        sw = _make_sniper(
            sniper_cfg=sniper_cfg,
            full_after_ticks=40,
            peak_pnl_pct=2.00,  # would have qualified
        )
        tracked = _drive_to_tick(sw, "BTCUSDT", tick_target=42)
        assert tracked["_last_action_returned"] == "full_close"

    def test_no_profit_states_does_not_extend(self) -> None:
        """Worker built without ``_profit_states`` (legacy test
        pattern) treats peak as unknown; uses base threshold."""
        sw = _make_sniper(full_after_ticks=40)
        # No _profit_states attribute set → peak_pnl_pct is None
        # → peak_qualifies is False → base 40-tick threshold.
        assert not hasattr(sw, "_profit_states")
        tracked = _drive_to_tick(sw, "ETHUSDT", tick_target=42)
        assert tracked["_last_action_returned"] == "full_close"


# ─── 3c — Recovering-PnL gate ────────────────────────────────────────


class TestRecoveringPnLGate:
    def test_recovering_position_spared_even_past_threshold(self) -> None:
        """A position whose PnL has rebounded by ``recovering_threshold_pct``
        from its worst observed PnL is spared the valve, even when
        ticks > effective_full_after."""
        sniper_cfg = Layer4SniperSettings()
        # development_window_lower stays at default -0.3 — test pnls below it
        # profit_protection_threshold stays at default 0.0 — test pnls negative
        sniper_cfg.peak_protection_threshold_pct = 100.0  # disable peak path
        sniper_cfg.peak_protected_full_after_ticks = 80
        sniper_cfg.recovering_threshold_pct = 0.10
        sw = _make_sniper(sniper_cfg=sniper_cfg, full_after_ticks=40)

        # Drive the worst PnL down first.
        tracked: dict = {
            "_partials_emitted": 999,
            "last_score": {"pnl_pct": -1.50},  # worst
        }
        # Drive 30 ticks at -1.50 % to record worst.
        for _ in range(30):
            sw._stall_escape_action("BTCUSDT", tracked, True, "hold")
        assert tracked.get("_stall_worst_pnl_pct") == -1.50

        # Now jump to -1.30 % (recovered by 0.20 %, > 0.10 % threshold).
        tracked["last_score"] = {"pnl_pct": -1.30}
        # Continue to tick > 40.
        last_action: str | None = None
        for _ in range(15):
            a = sw._stall_escape_action("BTCUSDT", tracked, True, "hold")
            last_action = a

        # Recovery gate must spare it.
        assert last_action != "full_close", (
            "recovering position should be spared by recovery gate"
        )

    def test_non_recovering_stuck_position_killed(self) -> None:
        """A position stuck at its worst PnL (no recovery) gets killed
        normally at the tick threshold. Recovery gate does not save it."""
        sniper_cfg = Layer4SniperSettings()
        # development_window_lower stays at default -0.3 — test pnls below it
        # profit_protection_threshold stays at default 0.0 — test pnls negative
        sniper_cfg.peak_protection_threshold_pct = 100.0
        sniper_cfg.peak_protected_full_after_ticks = 80
        sniper_cfg.recovering_threshold_pct = 0.10
        sw = _make_sniper(sniper_cfg=sniper_cfg, full_after_ticks=40)

        # Drive 50 ticks at constant -1.50 % — never recovers.
        tracked = _drive_to_tick(sw, "BTCUSDT", tick_target=42, pnl_pct=-1.50)
        assert tracked["_last_action_returned"] == "full_close", (
            "non-recovering stuck position must be killed by valve"
        )

    def test_partial_recovery_below_threshold_still_kills(self) -> None:
        """A position recovering by less than ``recovering_threshold_pct``
        does NOT pass the gate."""
        sniper_cfg = Layer4SniperSettings()
        # development_window_lower stays at default -0.3 — test pnls below it
        # profit_protection_threshold stays at default 0.0 — test pnls negative
        sniper_cfg.peak_protection_threshold_pct = 100.0
        sniper_cfg.peak_protected_full_after_ticks = 80
        sniper_cfg.recovering_threshold_pct = 0.10
        sw = _make_sniper(sniper_cfg=sniper_cfg, full_after_ticks=40)

        tracked: dict = {
            "_partials_emitted": 999,
            "last_score": {"pnl_pct": -1.50},
        }
        for _ in range(30):
            sw._stall_escape_action("BTCUSDT", tracked, True, "hold")

        # Now jump to -1.45 % (recovered by 0.05 %, below 0.10 % threshold).
        tracked["last_score"] = {"pnl_pct": -1.45}
        last_action: str | None = None
        for _ in range(15):
            a = sw._stall_escape_action("BTCUSDT", tracked, True, "hold")
            last_action = a

        assert last_action == "full_close", (
            "below-threshold recovery should NOT spare the position"
        )

    def test_recovery_gate_disabled_when_threshold_zero(self) -> None:
        """recovering_threshold_pct <= 0 disables the recovery check
        entirely; valve fires on stuck-flat positions at the tick
        threshold even if they recovered."""
        sniper_cfg = Layer4SniperSettings()
        # development_window_lower stays at default -0.3 — test pnls below it
        # profit_protection_threshold stays at default 0.0 — test pnls negative
        sniper_cfg.peak_protection_threshold_pct = 100.0
        sniper_cfg.peak_protected_full_after_ticks = 80
        sniper_cfg.recovering_threshold_pct = 0.0
        sw = _make_sniper(sniper_cfg=sniper_cfg, full_after_ticks=40)

        tracked: dict = {
            "_partials_emitted": 999,
            "last_score": {"pnl_pct": -1.50},
        }
        for _ in range(30):
            sw._stall_escape_action("BTCUSDT", tracked, True, "hold")
        # Big recovery (would have spared with gate enabled).
        tracked["last_score"] = {"pnl_pct": -0.50}
        last_action: str | None = None
        for _ in range(15):
            a = sw._stall_escape_action("BTCUSDT", tracked, True, "hold")
            last_action = a

        assert last_action == "full_close", (
            "with gate disabled, valve must fire at tick threshold "
            "regardless of recovery"
        )


# ─── Defaults sanity ─────────────────────────────────────────────────


class TestLayer4SniperSettingsDefaults:
    def test_phase3b_defaults(self) -> None:
        cfg = Layer4SniperSettings()
        assert cfg.peak_protection_threshold_pct == 0.10
        assert cfg.peak_protected_full_after_ticks == 80

    def test_phase3c_defaults(self) -> None:
        cfg = Layer4SniperSettings()
        assert cfg.recovering_threshold_pct == 0.10
