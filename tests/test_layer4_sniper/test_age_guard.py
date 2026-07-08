"""Layer 4 Realignment Phase 1A — Profit Sniper minimum-age guardrail.

Smoke tests for the age guard at the entry of
``ProfitSniper._stall_escape_action``. Below ``min_age_seconds``, the
sniper does not advance the stall counter or emit any escape — it
returns ``None`` immediately. At or above ``min_age_seconds``, the
existing stall-escape logic runs unchanged.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.config.settings import Layer4SniperSettings, Mode4Settings, Settings
from src.workers.profit_sniper import ProfitSniper


def _make_sniper(
    *, min_age_seconds: float = 300.0, age_seconds: float = 600.0,
) -> ProfitSniper:
    """Build a minimal ProfitSniper sufficient to exercise the stall
    escape path. Uses ``__new__`` to skip the heavy __init__; mirrors
    ``tests/test_profit_sniper_partial_cap.py``.
    """
    sw = ProfitSniper.__new__(ProfitSniper)
    cfg = Mode4Settings()
    cfg.max_partials_per_position = 1
    cfg.stall_escape_partial_after_ticks = 1   # fire fast in tests
    cfg.stall_escape_full_after_ticks = 9999
    cfg.stall_escape_cooldown_seconds = 0
    cfg.stall_tighten_max_applications = 9999

    sniper_cfg = Layer4SniperSettings()
    sniper_cfg.min_age_seconds = min_age_seconds

    sw.settings = MagicMock()
    sw.settings.mode4 = cfg
    sw.settings.layer4_sniper = sniper_cfg

    coord = MagicMock()
    coord.get_age_seconds.return_value = float(age_seconds)
    sw.trade_coordinator = coord
    return sw


def test_age_guard_blocks_fresh_position() -> None:
    """Position younger than min_age — stall escape returns None and
    coordinator is consulted. No counter advancement."""
    sw = _make_sniper(min_age_seconds=300.0, age_seconds=60.0)
    tracked = {"_partials_emitted": 0, "last_score": {"pnl_pct": -0.5}}

    actions: list[str | None] = []
    for _ in range(8):
        a = sw._stall_escape_action("ETHUSDT", tracked, True, "hold")
        actions.append(a)

    # All emissions are None (blocked by age guard).
    assert all(a is None for a in actions), f"expected all None, got {actions}"
    # Counter never advanced.
    assert tracked.get("_stall_ticks", 0) == 0
    # Coordinator was consulted at least once.
    assert sw.trade_coordinator.get_age_seconds.called


def test_age_guard_passes_mature_position() -> None:
    """Position older than min_age — stall escape executes normal logic
    and the existing partial→full path fires as before."""
    sw = _make_sniper(min_age_seconds=300.0, age_seconds=600.0)
    tracked = {"_partials_emitted": 0, "last_score": {"pnl_pct": -0.5}}

    actions = []
    for _ in range(8):
        a = sw._stall_escape_action("BTCUSDT", tracked, True, "hold")
        if a is not None:
            actions.append(a)

    # Existing behavior: first emission is partial_close (cap=1),
    # subsequent emissions become full_close.
    assert actions, "expected at least one emission"
    assert actions[0] == "partial_close"
    assert all(a == "full_close" for a in actions[1:])


def test_age_guard_disabled_when_zero() -> None:
    """Setting min_age_seconds=0 disables the guard entirely (kill
    switch). Even a 60-second-old position emits normally."""
    sw = _make_sniper(min_age_seconds=0.0, age_seconds=60.0)
    tracked = {"_partials_emitted": 0, "last_score": {"pnl_pct": -0.5}}

    actions = []
    for _ in range(8):
        a = sw._stall_escape_action("LINKUSDT", tracked, True, "hold")
        if a is not None:
            actions.append(a)

    assert actions, "guard disabled — expected emissions"
    assert actions[0] == "partial_close"


def test_layer4_sniper_settings_defaults() -> None:
    """Layer4SniperSettings dataclass has expected fields and defaults
    matching the Phase 1A / 1C plan."""
    s = Layer4SniperSettings()
    assert s.min_age_seconds == 300.0
    assert s.profit_protection_threshold == 0.0
    assert s.development_window_lower == -0.3


def test_settings_load_includes_layer4_sniper() -> None:
    """Settings.load() / _load_fresh() wires up layer4_sniper field."""
    s = Settings._load_fresh(config_path="config.toml")
    assert hasattr(s, "layer4_sniper")
    assert isinstance(s.layer4_sniper, Layer4SniperSettings)
    # config.toml [layer4.sniper] sets explicit values; verify them.
    assert s.layer4_sniper.min_age_seconds == 300.0
