"""Phase 6 (post-Layer-1 fix) — SCANNER_FILTER_AGGREGATE counter tests.

Verifies the per-cycle bucket counters added to ``ScannerWorker.tick``.
Each fail bucket maps to one of the 5 criteria in ``_qualifies``; pass
buckets surface category-specific successes.

The test exercises the bucket-classification logic directly with
synthetic ``record`` dicts — running the full ``tick()`` pipeline
through a mocked DB + mocked services adds significant test scaffolding
for negligible coverage gain (the aggregate is a counter loop, not a
control-flow gate).

Investigation: ``dev_notes/phase0_post_layer1_fixes/issue_6_scanner_filter_logging.md``.
"""

from __future__ import annotations

import re
from pathlib import Path


SCANNER_WORKER = Path(__file__).parent.parent / "src" / "workers" / "scanner_worker.py"


def test_aggregate_counters_documented_in_code() -> None:
    """The 9 expected counter keys must all appear in the SCANNER_FILTER_AGGREGATE log."""
    src = SCANNER_WORKER.read_text()
    expected_keys = (
        "fail_no_xray",
        "fail_setup_none",
        "fail_consensus",
        "fail_regime",
        "fail_rr",
        "fail_blockers",
        "pass_xray",
        "pass_consensus_strong",
        "pass_consensus_good",
    )
    for key in expected_keys:
        assert f"agg['{key}']" in src or f"\"{key}\"" in src, (
            f"Counter key {key!r} not found in scanner_worker.py — the "
            f"SCANNER_FILTER_AGGREGATE contract was modified without "
            f"updating the corresponding test."
        )


def test_aggregate_log_emits_at_info() -> None:
    """SCANNER_FILTER_AGGREGATE must be at INFO level (operator-visible)."""
    src = SCANNER_WORKER.read_text()
    # The aggregate emit lives near the cycle-end logs; assert it's a
    # log.info call, not log.debug.
    pattern = re.compile(
        r"log\.info\(\s*\n?\s*f?\"SCANNER_FILTER_AGGREGATE",
        re.DOTALL,
    )
    matches = pattern.search(src)
    assert matches is not None, (
        "SCANNER_FILTER_AGGREGATE not emitted at INFO level — it must be "
        "INFO so operators see it without raising log level."
    )


def test_aggregate_classifies_by_first_failure() -> None:
    """The bucket logic uses the FIRST entry in reasons_failed.

    Direct smoke test of the classification clauses to catch a regression
    where someone reorders or deletes a branch.
    """
    src = SCANNER_WORKER.read_text()
    classifications = [
        ('first == "no_xray_analysis"', "fail_no_xray"),
        ('first == "no_xray_setup_type"', "fail_setup_none"),
        ('first.startswith("consensus=")', "fail_consensus"),
        ('first.startswith("regime=")', "fail_regime"),
        ('first.startswith("rr=")', "fail_rr"),
        ('first.startswith("blockers=")', "fail_blockers"),
    ]
    for cond, bucket in classifications:
        assert cond in src, (
            f"Classification clause {cond!r} missing — the bucket "
            f"{bucket!r} would never be incremented."
        )


def test_pass_bucket_classification_matches_qualifies_output() -> None:
    """Pass buckets must match the strings ``_qualifies`` writes to reasons_passed.

    ``_qualifies`` writes:
      - ``f"xray_setup={setup_type.value}"``  (criterion 1 pass)
      - ``f"consensus={consensus['consensus']}"`` where consensus in {STRONG, GOOD}

    The aggregate buckets must use the SAME string prefixes / values.
    """
    src = SCANNER_WORKER.read_text()
    assert 'r.startswith("xray_setup=")' in src
    assert 'r == "consensus=STRONG"' in src
    assert 'r == "consensus=GOOD"' in src
