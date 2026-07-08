"""Migrate ``data/layer_state.json`` v1 (3-layer) → v2 (5-layer).

Layer 1 restructure Phase 8. The new scheme separates analysis from
brain, and execution from monitoring, per blueprint Section 12.1:

    Old (v1)            New (v2)
    -------- ----      -----------
    1 DATA              1 DATA
    2 BRAIN             2 ANALYSIS  ← was implicit; now explicit
                        3 BRAIN
    3 EXECUTION         4 EXECUTION
                        5 MONITORING ← was implicit; now explicit

The mapping policy: a v1 ``BRAIN=true`` toggle becomes BOTH ANALYSIS
and BRAIN in v2 (operators expressed "I want analysis going AND brain
calling Claude" with a single toggle). Same for ``EXECUTION=true`` →
EXECUTION + MONITORING. Operators can adjust each independently after
migration.

The script is idempotent: running on a v2 file is a no-op. The
original file is backed up to ``data/layer_state.v1.json.bak`` before
overwriting.

Usage::

    python3 scripts/migrate_layer_state_to_v2.py [path/to/layer_state.json]

Default path is ``data/layer_state.json`` relative to repo root.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def migrate(state_v1: dict) -> dict:
    """Convert v1 layer_state dict → v2 schema.

    Args:
        state_v1: Parsed JSON dict from a v1 file. Missing keys default
            to False so a partial / corrupt file still produces a valid
            v2 output (the operator can re-toggle as desired).

    Returns:
        v2 dict ready to ``json.dumps`` and write.
    """
    la1 = state_v1.get("layer_active", {}) or {}
    new = {
        "1": bool(la1.get("1", False)),                # DATA stays
        "2": bool(la1.get("2", False)),                # ANALYSIS = old BRAIN intent
        "3": bool(la1.get("2", False)),                # BRAIN     = old BRAIN
        "4": bool(la1.get("3", False)),                # EXECUTION = old EXECUTION
        "5": bool(la1.get("3", False)),                # MONITORING= old EXECUTION
    }
    return {
        "schema_version": 2,
        "layer_active": new,
        "user_stopped": bool(state_v1.get("user_stopped", False)),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main(path: str | None = None) -> int:
    """Run the migration. Returns 0 on success, non-zero on failure."""
    state_path = Path(path) if path else Path(__file__).resolve().parent.parent / "data" / "layer_state.json"
    if not state_path.exists():
        # No file → write a fresh v2 default. Safe — workers start
        # inactive regardless of file content per LayerManager.
        default = {
            "schema_version": 2,
            "layer_active": {"1": False, "2": False, "3": False, "4": False, "5": False},
            "user_stopped": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(default, indent=2))
        print(f"[migrate] No existing state at {state_path}; wrote default v2.")
        return 0

    try:
        raw = json.loads(state_path.read_text())
    except json.JSONDecodeError as e:
        print(f"[migrate] Failed to parse {state_path}: {e}", file=sys.stderr)
        return 2

    if raw.get("schema_version") == 2:
        print(f"[migrate] {state_path} already v2; no-op.")
        return 0

    backup = state_path.with_suffix(".v1.json.bak")
    backup.write_text(state_path.read_text())
    new_state = migrate(raw)
    state_path.write_text(json.dumps(new_state, indent=2))
    print(f"[migrate] {state_path} v1 → v2. Backup: {backup}")
    print(f"[migrate] new layer_active = {new_state['layer_active']}")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(main(target))
