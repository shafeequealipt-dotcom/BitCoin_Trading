"""layer_state.json v1→v2 migration — Layer 1 restructure Phase 8."""

import json

import pytest

from scripts.migrate_layer_state_to_v2 import main, migrate


class TestMigrateFunction:
    def test_full_on_v1_to_full_on_v2(self) -> None:
        v1 = {"layer_active": {"1": True, "2": True, "3": True}, "user_stopped": False}
        v2 = migrate(v1)
        assert v2["schema_version"] == 2
        assert v2["layer_active"] == {"1": True, "2": True, "3": True, "4": True, "5": True}
        assert v2["user_stopped"] is False

    def test_brain_off_propagates_to_analysis_and_brain(self) -> None:
        v1 = {"layer_active": {"1": True, "2": False, "3": False}, "user_stopped": True}
        v2 = migrate(v1)
        # v1.2 = False → both new.2 (ANALYSIS) and new.3 (BRAIN) are False
        # v1.3 = False → both new.4 (EXECUTION) and new.5 (MONITORING) are False
        assert v2["layer_active"] == {"1": True, "2": False, "3": False, "4": False, "5": False}
        assert v2["user_stopped"] is True

    def test_partial_data_defaults_false(self) -> None:
        v1 = {"layer_active": {"1": True}}
        v2 = migrate(v1)
        assert v2["layer_active"]["1"] is True
        assert v2["layer_active"]["2"] is False  # missing v1.2 → False
        assert v2["layer_active"]["5"] is False

    def test_empty_input(self) -> None:
        v2 = migrate({})
        assert v2["schema_version"] == 2
        assert all(v is False for v in v2["layer_active"].values())


class TestMigrateMain:
    def test_no_existing_file_writes_default_v2(self, tmp_path) -> None:
        target = tmp_path / "layer_state.json"
        rc = main(str(target))
        assert rc == 0
        out = json.loads(target.read_text())
        assert out["schema_version"] == 2
        assert all(v is False for v in out["layer_active"].values())

    def test_existing_v1_creates_backup(self, tmp_path) -> None:
        target = tmp_path / "layer_state.json"
        target.write_text(json.dumps({
            "layer_active": {"1": True, "2": True, "3": False},
            "user_stopped": False,
        }))
        rc = main(str(target))
        assert rc == 0
        backup = target.with_suffix(".v1.json.bak")
        assert backup.exists()
        out = json.loads(target.read_text())
        assert out["schema_version"] == 2
        assert out["layer_active"]["3"] == True   # was old.2 → new.3
        assert out["layer_active"]["4"] == False  # was old.3 → new.4

    def test_already_v2_is_no_op(self, tmp_path) -> None:
        target = tmp_path / "layer_state.json"
        target.write_text(json.dumps({
            "schema_version": 2,
            "layer_active": {"1": True, "2": True, "3": True, "4": True, "5": True},
            "user_stopped": False,
        }))
        before = target.read_text()
        rc = main(str(target))
        assert rc == 0
        # No-op should leave content unchanged.
        assert target.read_text() == before
        # No backup created on no-op.
        assert not target.with_suffix(".v1.json.bak").exists()

    def test_corrupt_json_returns_error(self, tmp_path) -> None:
        target = tmp_path / "layer_state.json"
        target.write_text("{not json")
        rc = main(str(target))
        assert rc == 2


class TestSemanticHelpers:
    def test_can_run_brain_v1_maps_to_layer_2(self) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager.__new__(LayerManager)
        lm._layer_active = {1: True, 2: True, 3: False}
        assert lm.can_run_brain() is True
        lm._layer_active = {1: True, 2: False, 3: True}
        assert lm.can_run_brain() is False

    def test_can_execute_orders(self) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager.__new__(LayerManager)
        lm._layer_active = {1: True, 2: True, 3: True}
        assert lm.can_execute_orders() is True
        lm._layer_active = {1: True, 2: True, 3: False}
        assert lm.can_execute_orders() is False

    def test_can_run_monitoring(self) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager.__new__(LayerManager)
        lm._layer_active = {1: True, 2: True, 3: True}
        assert lm.can_run_monitoring() is True
