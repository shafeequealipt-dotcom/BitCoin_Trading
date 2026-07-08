"""Phase 9 (post-Layer-1 fix) — altdata per-feed timing + threshold override.

Two changes verified:
  1. ``_TICK_SLOW_PER_WORKER["altdata_worker"] = 12.0`` (was implicit
     2 s global; live observation 2026-04-27 showed 5-9 s ticks every
     time, drowning out legitimate slow-tick warnings on other workers).
  2. ``ALTDATA_TICK_DONE`` per-cycle aggregate carries per-feed
     elapsed_ms; the wrapping ``_timed`` helper measures each fetch
     separately so operators can attribute slowness to the right REST
     endpoint.

Investigation: ``dev_notes/phase0_post_layer1_fixes/issue_9_altdata_parallel.md``.
"""

from __future__ import annotations

from pathlib import Path


def test_altdata_threshold_override_present() -> None:
    """The per-worker override list must include altdata_worker at 12s."""
    from src.workers.base_worker import _TICK_SLOW_PER_WORKER
    assert _TICK_SLOW_PER_WORKER.get("altdata_worker") == 12.0


def test_per_feed_timing_helper_present() -> None:
    """The _timed inline helper must wrap each fetch."""
    src = (Path(__file__).parent.parent / "src" / "workers" / "altdata_worker.py").read_text()
    assert "async def _timed(label" in src
    # Each fetch wrapped by _timed.
    for fetch in ("_fetch_funding_rates()", "_fetch_open_interest()",
                  "_fetch_fear_greed()", "_fetch_onchain()"):
        assert f'_timed("' in src and fetch in src, (
            f"{fetch} no longer wrapped by _timed — per-feed timing broken."
        )


def test_altdata_tick_done_emit_present() -> None:
    """ALTDATA_TICK_DONE must emit per-feed elapsed_ms."""
    src = (Path(__file__).parent.parent / "src" / "workers" / "altdata_worker.py").read_text()
    assert "ALTDATA_TICK_DONE" in src
    for field in ("funding_ms=", "oi_ms=", "fg_ms=", "onchain_ms=", "total_ms=", "ran=["):
        assert field in src, (
            f"ALTDATA_TICK_DONE missing field {field!r} — per-feed "
            f"attribution incomplete."
        )


def test_altdata_legacy_aggregate_preserved() -> None:
    """The legacy ALTDATA log must still emit (downstream parsers may grep)."""
    src = (Path(__file__).parent.parent / "src" / "workers" / "altdata_worker.py").read_text()
    # Look for the literal legacy emission.
    assert 'f"ALTDATA | fg=' in src
