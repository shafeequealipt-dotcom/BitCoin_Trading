"""Phase 11 (dead-workers fix) — persistence-ordering tests.

Verifies that ``LayerManager.start_layer`` and ``stop_layer`` persist the
``_layer_active`` snapshot AFTER the toggle has flipped the in-memory bit,
not before. The pre-fix code persisted at the top of ``start_layer`` (line
289 in commit 357516e and earlier), which captured a stale snapshot on
disk; the heartbeat at ``state_sync_interval_sec`` then saw disk≠memory
and (with the old "reload_memory" direction) reverted memory to the stale
disk state, silently dropping operator toggles within ~30 s.

The test cases cover:

  * single-layer start (L2, L3) writes the toggled state to disk
  * cascaded start (L2 then L3) writes the fully-toggled state to disk —
    this is the exact scenario that produced the 09:59:10 regression
  * stop_layer cascading toggles persist correctly
  * dependency-rejected start still persists ``_user_stopped = False``
    (the operator-intent mutation made before the dependency check)
  * persist failure surfaces as ``LAYER_STATE_PERSIST_FAIL`` at WARNING
    and start_layer still reports success on the in-memory toggle.

Sub-method machinery (``asyncio.create_task`` inside ``_start_brain_layer``)
is stubbed so the tests don't need a fully-wired brain review loop.

Investigation: ``dev_notes/phase0_dead_workers_capture.md``,
``dev_notes/phase1_dead_workers_investigation/phase1_summary.md``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core import layer_manager as lm_mod
from src.core.layer_manager import LayerManager

pytestmark = pytest.mark.asyncio


def _make_layer_manager() -> LayerManager:
    """Build a stub LayerManager with the same initial state as production.

    Bypasses ``__init__`` so the test does not need a real Settings or
    services dict, but seeds every attribute that ``start_layer`` /
    ``stop_layer`` touches. Sub-method machinery (the brain review task
    etc.) is stubbed per-test where needed.
    """
    lm = LayerManager.__new__(LayerManager)
    lm.settings = MagicMock()
    lm.services = {}
    lm._layer_active = {1: False, 2: False, 3: False}
    lm._layer_started_at = {1: 0.0, 2: 0.0, 3: 0.0}
    lm._user_stopped = False
    lm._state_sync_task = None
    lm._state_sync_started = False
    lm._drift_action = "rewrite_disk"
    lm._brain_task = None
    lm.brain_interval_seconds = 150
    return lm


def _stub_brain_layer(lm: LayerManager) -> None:
    """Replace ``_start_brain_layer`` with a no-task variant for testing.

    The real method calls ``asyncio.create_task(self._brain_review_loop())``
    which needs a fully-wired services dict; we just need the layer-active
    bit flipped to verify the persist ordering.
    """
    async def _stub() -> tuple[bool, str]:
        lm._layer_active[2] = True
        return True, "Brain started (test stub)"
    lm._start_brain_layer = _stub  # type: ignore[method-assign]


async def test_start_layer_2_persists_post_toggle(tmp_path: Path) -> None:
    """start_layer(2) writes layer_active[2]=True to disk, not the pre-toggle False."""
    state_file = tmp_path / "layer_state.json"
    lm = _make_layer_manager()
    lm._layer_active = {1: True, 2: False, 3: False}
    _stub_brain_layer(lm)

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        ok, _msg = await lm.start_layer(2, reason="test", actor="test")

    assert ok is True
    assert lm._layer_active[2] is True
    disk = json.loads(state_file.read_text())
    # Critical: disk must reflect the post-toggle state, not the pre-toggle False.
    assert disk["layer_active"] == {"1": True, "2": True, "3": False}
    assert disk["user_stopped"] is False


async def test_start_layer_3_persists_post_toggle(tmp_path: Path) -> None:
    """start_layer(3) writes layer_active[3]=True to disk."""
    state_file = tmp_path / "layer_state.json"
    lm = _make_layer_manager()
    lm._layer_active = {1: True, 2: True, 3: False}

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        ok, _msg = await lm.start_layer(3, reason="test", actor="test")

    assert ok is True
    assert lm._layer_active[3] is True
    disk = json.loads(state_file.read_text())
    assert disk["layer_active"] == {"1": True, "2": True, "3": True}


async def test_cascaded_start_l2_then_l3_both_on_disk(tmp_path: Path) -> None:
    """Phase 11 regression test — the exact 09:59:10 scenario.

    Pre-fix: start_layer(2) wrote disk={1:T,2:F,3:F}, then start_layer(3)
    wrote disk={1:T,2:T,3:F}, and the L3=True memory state was lost on
    the next heartbeat reload.

    Post-fix: each start_layer call persists AFTER the in-memory bit flips,
    so disk shows the cumulative toggled state at every step.
    """
    state_file = tmp_path / "layer_state.json"
    lm = _make_layer_manager()
    lm._layer_active = {1: True, 2: False, 3: False}
    _stub_brain_layer(lm)

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        ok2, _ = await lm.start_layer(2, reason="cascade", actor="test")
        assert ok2 is True
        disk_after_l2 = json.loads(state_file.read_text())
        assert disk_after_l2["layer_active"] == {"1": True, "2": True, "3": False}, (
            "After start_layer(2), disk must show L2=True. "
            "Pre-fix disk would have shown L2=False (the regression)."
        )

        ok3, _ = await lm.start_layer(3, reason="cascade", actor="test")
        assert ok3 is True
        disk_after_l3 = json.loads(state_file.read_text())
        assert disk_after_l3["layer_active"] == {"1": True, "2": True, "3": True}, (
            "After cascaded start_layer(3), disk must show L3=True. "
            "Pre-fix disk would have shown L3=False (the regression)."
        )


async def test_stop_layer_cascades_and_persists(tmp_path: Path) -> None:
    """stop_layer(2) cascades L3 down and persists both as False to disk."""
    state_file = tmp_path / "layer_state.json"
    lm = _make_layer_manager()
    lm._layer_active = {1: True, 2: True, 3: True}

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        ok, _msg = await lm.stop_layer(2, reason="test", actor="test")

    assert ok is True
    assert lm._layer_active == {1: True, 2: False, 3: False}
    disk = json.loads(state_file.read_text())
    assert disk["layer_active"] == {"1": True, "2": False, "3": False}
    # stop_layer marks user_stopped=True per its existing semantics.
    assert disk["user_stopped"] is True


async def test_dependency_rejected_start_still_persists_user_stopped(tmp_path: Path) -> None:
    """start_layer(2) when L1 OFF returns failure but persists user_stopped=False.

    The pre-fix call persisted at the top of start_layer; the post-fix
    moves persist after the toggle but adds an early-path persist before
    each dependency-rejection return so the ``_user_stopped = False``
    intent is durable even when no layer toggle happens.
    """
    state_file = tmp_path / "layer_state.json"
    state_file.write_text(json.dumps({
        "layer_active": {"1": False, "2": False, "3": False},
        "user_stopped": True,  # operator previously stopped trading
    }))
    lm = _make_layer_manager()
    lm._user_stopped = True
    lm._layer_active = {1: False, 2: False, 3: False}

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        ok, msg = await lm.start_layer(2, reason="test", actor="test")

    assert ok is False
    assert "Data layer must be active first" in msg
    assert lm._user_stopped is False  # mutation happened in memory
    disk = json.loads(state_file.read_text())
    # user_stopped flipped to False on disk too — the operator's intent
    # to lift the suppression is durable across crashes even if the
    # dependency check rejected the toggle.
    assert disk["user_stopped"] is False
    # layer_active unchanged (no toggle occurred).
    assert disk["layer_active"] == {"1": False, "2": False, "3": False}


async def test_persist_failure_surfaces_as_warning(tmp_path: Path, monkeypatch) -> None:
    """LAYER_STATE_PERSIST_FAIL fires when disk write raises.

    Pre-fix the persist failure was logged at WARNING with a single
    string; post-fix it emits a structured tag so observers can grep
    for the specific event. The toggle still succeeds in memory — the
    heartbeat will retry persist later.
    """
    # State file path that cannot be written (parent is a regular file)
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("blocking")
    state_file = blocker / "layer_state.json"  # parent is a file → mkdir fails

    lm = _make_layer_manager()
    lm._layer_active = {1: True, 2: True, 3: False}

    captured: list[str] = []
    real_warning = lm_mod.log.warning

    def _capture_warning(msg, *args, **kwargs):
        captured.append(str(msg))
        return real_warning(msg, *args, **kwargs)

    monkeypatch.setattr(lm_mod.log, "warning", _capture_warning)

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        ok = lm._persist_state()

    assert ok is False
    assert any("LAYER_STATE_PERSIST_FAIL" in m for m in captured), (
        f"expected LAYER_STATE_PERSIST_FAIL warning, got: {captured}"
    )


async def test_persist_success_emits_persist_ok(tmp_path: Path, monkeypatch) -> None:
    """LAYER_STATE_PERSIST_OK fires on successful write at INFO."""
    state_file = tmp_path / "layer_state.json"
    lm = _make_layer_manager()
    lm._layer_active = {1: True, 2: True, 3: False}

    captured: list[str] = []
    real_info = lm_mod.log.info

    def _capture_info(msg, *args, **kwargs):
        captured.append(str(msg))
        return real_info(msg, *args, **kwargs)

    monkeypatch.setattr(lm_mod.log, "info", _capture_info)

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        ok = lm._persist_state()

    assert ok is True
    assert any("LAYER_STATE_PERSIST_OK" in m for m in captured), (
        f"expected LAYER_STATE_PERSIST_OK info log, got: {captured}"
    )


async def test_drift_recovered_emits_after_persist(tmp_path: Path, monkeypatch) -> None:
    """LAYER_STATE_DRIFT_RECOVERED fires on rewrite_disk path.

    Verifies the new heartbeat behaviour: when disk lags memory, the
    heartbeat re-persists memory and emits the recovery event so
    operators see the action explicitly in workers.log.
    """
    state_file = tmp_path / "layer_state.json"
    state_file.write_text(json.dumps({
        "layer_active": {"1": True, "2": True, "3": False},  # disk lags
        "user_stopped": False,
    }))
    lm = _make_layer_manager()
    lm._layer_active = {1: True, 2: True, 3: True}  # memory has L3=True

    captured: list[str] = []
    real_warning = lm_mod.log.warning

    def _capture_warning(msg, *args, **kwargs):
        captured.append(str(msg))
        return real_warning(msg, *args, **kwargs)

    monkeypatch.setattr(lm_mod.log, "warning", _capture_warning)

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        lm._sync_state_with_disk()

    # Disk re-written from memory.
    disk_after = json.loads(state_file.read_text())
    assert disk_after["layer_active"] == {"1": True, "2": True, "3": True}
    assert any(
        "LAYER_STATE_DRIFT_RECOVERED" in m and "memory_to_disk" in m
        for m in captured
    ), f"expected DRIFT_RECOVERED with direction=memory_to_disk, got: {captured}"


async def test_unknown_layer_does_not_persist(tmp_path: Path) -> None:
    """start_layer(99) returns False without touching disk.

    No in-memory mutation occurred, so no persist is needed and the
    early "Unknown layer" branch must not write to disk.
    """
    state_file = tmp_path / "layer_state.json"
    state_file.write_text(json.dumps({
        "layer_active": {"1": True, "2": True, "3": False},
        "user_stopped": False,
    }))
    lm = _make_layer_manager()
    lm._layer_active = {1: True, 2: True, 3: False}
    pre_mtime = state_file.stat().st_mtime_ns

    # Wait so a file write would change mtime measurably.
    await asyncio.sleep(0.01)

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        ok, msg = await lm.start_layer(99, reason="test", actor="test")

    assert ok is False
    assert "Unknown layer" in msg
    post_mtime = state_file.stat().st_mtime_ns
    assert pre_mtime == post_mtime, (
        "Unknown-layer path must not touch disk; mtime should be unchanged."
    )
