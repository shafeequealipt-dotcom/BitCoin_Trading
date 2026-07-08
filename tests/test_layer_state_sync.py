"""Phase 2 (post-Layer-1 fix) — LAYER_STATE_SYNC heartbeat tests.

Verifies the disk/memory layer state sync added on ``LayerManager``.
``data/layer_state.json`` is the operator's source of truth (Telegram
toggles persist there); the heartbeat closes the gap when something
writes the file out-of-band (e.g. an operator hand-edits to fix a stuck
state).

The tests exercise ``_sync_state_with_disk()`` directly with a temporary
state file — we do not exercise the asyncio loop wrapper because that
adds wall-clock dependency to the test for negligible coverage gain.

Investigation: ``dev_notes/phase0_post_layer1_fixes/issue_2_fail_open_gate.md``.
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


def _make_layer_manager(drift_action: str = "rewrite_disk") -> LayerManager:
    """Build a bare LayerManager without invoking the full __init__ chain.

    LayerManager.__init__ touches services and runs ``_load_persisted_state``
    against the real ``data/layer_state.json`` — for the sync tests we
    want a clean in-memory mirror with no preloaded state.

    Args:
        drift_action: ``"rewrite_disk"`` (Phase 11 default — memory wins
            on drift) or ``"reload_memory"`` (legacy — disk wins). The
            default matches the post-fix production semantics.
    """
    lm = LayerManager.__new__(LayerManager)
    lm.settings = MagicMock()
    lm.services = {}
    lm._layer_active = {1: False, 2: False, 3: False}
    lm._layer_started_at = {1: 0.0, 2: 0.0, 3: 0.0}
    lm._user_stopped = False
    lm._state_sync_task = None
    lm._state_sync_started = False
    lm._drift_action = drift_action
    return lm


async def test_sync_match_no_drift(tmp_path: Path) -> None:
    """Disk and memory agree → match=true, no drift event."""
    state_file = tmp_path / "layer_state.json"
    state_file.write_text(json.dumps({
        "layer_active": {"1": True, "2": True, "3": False},
        "user_stopped": False,
    }))

    lm = _make_layer_manager()
    lm._layer_active = {1: True, 2: True, 3: False}

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        # No raise = pass; assertion is on logs, not on state.
        lm._sync_state_with_disk()

    # Memory unchanged (no drift to reload).
    assert lm._layer_active == {1: True, 2: True, 3: False}


async def test_sync_drift_default_rewrites_disk_from_memory(tmp_path: Path) -> None:
    """Phase 11 default — memory wins on drift, disk re-written.

    The post-fix default is ``on_drift_action='rewrite_disk'``: memory
    is the live source of truth, disk is a persistence target. When
    the heartbeat finds disk≠memory, it re-persists memory so disk
    catches up. This is the inverse of the pre-fix behaviour (which
    is now retained as ``"reload_memory"`` for emergency rollback —
    see ``test_sync_drift_legacy_reload_memory_from_disk`` below).
    """
    state_file = tmp_path / "layer_state.json"
    state_file.write_text(json.dumps({
        "layer_active": {"1": True, "2": True, "3": True},  # disk: L3 ON
        "user_stopped": False,
    }))

    lm = _make_layer_manager()  # default drift_action = "rewrite_disk"
    lm._layer_active = {1: True, 2: True, 3: False}  # memory: L3 OFF

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        lm._sync_state_with_disk()

    # Memory unchanged (memory wins on drift).
    assert lm._layer_active == {1: True, 2: True, 3: False}
    # Disk re-written from memory.
    disk_after = json.loads(state_file.read_text())
    assert disk_after["layer_active"] == {"1": True, "2": True, "3": False}


async def test_sync_drift_legacy_reload_memory_from_disk(tmp_path: Path) -> None:
    """Legacy ``reload_memory`` direction — disk wins on drift.

    Retained for emergency rollback only. Operators set this via
    ``[layer_manager.state_sync] on_drift_action = "reload_memory"``
    if the post-fix semantics surface a regression in the field.
    The pre-fix production code shipped with this direction and it
    produced the Layer 3 toggle revert observed twice live on
    2026-04-27.
    """
    state_file = tmp_path / "layer_state.json"
    state_file.write_text(json.dumps({
        "layer_active": {"1": True, "2": True, "3": True},  # disk: L3 ON
        "user_stopped": False,
    }))

    lm = _make_layer_manager(drift_action="reload_memory")
    lm._layer_active = {1: True, 2: True, 3: False}  # memory: L3 OFF

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        lm._sync_state_with_disk()

    # Memory now matches disk (legacy direction).
    assert lm._layer_active == {1: True, 2: True, 3: True}
    # Disk unchanged.
    disk_after = json.loads(state_file.read_text())
    assert disk_after["layer_active"] == {"1": True, "2": True, "3": True}


async def test_sync_missing_file_logs_no_raise(tmp_path: Path) -> None:
    """Fresh boot with no persisted state → no-op, no exception."""
    state_file = tmp_path / "does_not_exist.json"
    lm = _make_layer_manager()
    lm._layer_active = {1: False, 2: False, 3: False}

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        # Must not raise.
        lm._sync_state_with_disk()

    assert lm._layer_active == {1: False, 2: False, 3: False}


async def test_sync_corrupt_json_does_not_destroy_memory(tmp_path: Path) -> None:
    """Disk file has malformed JSON → memory preserved, no reload attempt."""
    state_file = tmp_path / "layer_state.json"
    state_file.write_text("{ not valid json")

    lm = _make_layer_manager()
    lm._layer_active = {1: True, 2: True, 3: True}

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        # Must not raise.
        lm._sync_state_with_disk()

    # Memory preserved despite corrupt disk file.
    assert lm._layer_active == {1: True, 2: True, 3: True}


async def test_sync_disk_has_phantom_keys_ignored(tmp_path: Path) -> None:
    """Disk file references a layer that doesn't exist in memory →
    only known keys are reloaded; phantom keys are ignored.

    Defends against a malformed file accidentally introducing layer 4
    or layer 0 into the in-memory dict. Exercised against the legacy
    ``reload_memory`` direction because that's the path that copies
    keys from disk into memory; the default ``rewrite_disk`` direction
    only writes memory's known keys to disk so phantoms can never
    enter memory by construction.
    """
    state_file = tmp_path / "layer_state.json"
    state_file.write_text(json.dumps({
        "layer_active": {"1": True, "2": True, "3": True, "4": True, "0": True},
        "user_stopped": False,
    }))

    lm = _make_layer_manager(drift_action="reload_memory")
    lm._layer_active = {1: False, 2: False, 3: False}

    with patch.object(lm_mod, "_STATE_FILE", state_file):
        lm._sync_state_with_disk()

    # Only known keys (1,2,3) updated; phantoms not introduced.
    assert lm._layer_active == {1: True, 2: True, 3: True}
    assert 4 not in lm._layer_active
    assert 0 not in lm._layer_active


async def test_start_state_sync_idempotent() -> None:
    """Calling start_state_sync twice does not spawn two tasks."""
    lm = _make_layer_manager()
    try:
        lm.start_state_sync(interval_sec=10.0)
        first_task = lm._state_sync_task
        assert first_task is not None
        lm.start_state_sync(interval_sec=10.0)
        assert lm._state_sync_task is first_task
    finally:
        await lm.stop_state_sync()


async def test_stop_state_sync_cancels_task() -> None:
    """stop_state_sync cancels and awaits the task."""
    lm = _make_layer_manager()
    lm.start_state_sync(interval_sec=10.0)
    task = lm._state_sync_task
    assert task is not None and not task.done()
    await lm.stop_state_sync()
    assert task.done() or task.cancelled()
    assert lm._state_sync_started is False
