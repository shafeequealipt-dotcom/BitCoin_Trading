"""Pipeline-2 — Live R1 trace through REAL project classes.

This is not a unit test; it instantiates the production StructuralAnalysis,
StructureCache, and the assembler helper to verify the trade_direction
field flows through the actual code path that production uses.

Run: python3 scripts/pipeline_audit_R1_live.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis.structure.models.structure_types import (
    StructuralAnalysis,
    SetupType,
)
from src.analysis.structure.structure_cache import StructureCache
from src.apex.assembler import _gather_structural_data_from_cache
from loguru import logger


def main() -> None:
    captured = []
    sink_id = logger.add(
        lambda msg: captured.append(msg.record["message"]),
        level="DEBUG",
        format="{message}",
    )

    # === Layer 1B side: simulate what structure_engine.analyze() produces ===
    # The real classify_setup() in structure_engine inverts trade_direction
    # for counter setups; here we directly inject the post-classification
    # state to validate the downstream wiring.
    print("=== Step 1: Build a real StructuralAnalysis with counter setup ===")
    analysis = StructuralAnalysis(
        symbol="BSBUSDT",
        current_price=1.0,
        setup_quality="GOOD",
        setup_score=80,
        setup_type=SetupType.BULLISH_FVG_OB_COUNTER,
        setup_type_confidence=0.7,
        suggested_direction="short",   # regime label (trending_down)
        trade_direction="long",        # COUNTER-INVERTED — what brain reads
        position_in_range=0.5,
    )
    print(f"  StructuralAnalysis.suggested_direction = {analysis.suggested_direction!r}")
    print(f"  StructuralAnalysis.trade_direction     = {analysis.trade_direction!r}")
    print(f"  StructuralAnalysis.setup_type          = {analysis.setup_type}")
    print()

    # === structure_cache lifecycle ===
    print("=== Step 2: Push into real StructureCache (real worker would call set()) ===")
    cache = StructureCache(ttl_seconds=300)
    cache.set("BSBUSDT", analysis)
    print(f"  StructureCache size = {cache.get_stats()['cached_entries']}")
    print()

    # === Layer 3 side: real assembler reads cache and builds StructuralData ===
    print("=== Step 3: Real _gather_structural_data_from_cache pulls + builds StructuralData ===")
    services = {"structure_cache": cache}
    sd = _gather_structural_data_from_cache(services, "BSBUSDT")
    assert sd is not None, "assembler must return a StructuralData, not None"
    print(f"  StructuralData.symbol              = {sd.symbol!r}")
    print(f"  StructuralData.suggested_direction = {sd.suggested_direction!r}  (regime label, propagated)")
    print(f"  StructuralData.setup_type          = {sd.setup_type!r}            (propagated)")
    print(f"  StructuralData.trade_direction     = {sd.trade_direction!r}     (R1 plumbed field)")
    print()

    # === Verify the cross-layer hand-off ===
    print("=== Step 4: Assertions on the R1 contract ===")
    assert sd.trade_direction == "long", f"R1 propagation failed: {sd.trade_direction!r}"
    assert sd.suggested_direction == "short", "suggested_direction must remain untouched"
    assert sd.setup_type == "bullish_fvg_ob_counter", (
        f"setup_type must propagate as the enum .value (lowercase), "
        f"got {sd.setup_type!r}"
    )
    assert sd.trade_direction != sd.suggested_direction, (
        "counter setup must produce divergent trade_direction vs suggested_direction"
    )
    print("  PASS — every contract upheld")
    print()

    # === Capture log emissions for evidence ===
    logger.remove(sink_id)
    apex_lines = [m for m in captured if "APEX_ASSEMBLE_XRAY" in m]
    print(f"=== Step 5: Real loguru emissions captured ===")
    for line in apex_lines[:3]:
        print(f"  {line[:140]}")
    print()
    print("=== R1 LIVE PIPELINE: GREEN ===")


if __name__ == "__main__":
    main()
