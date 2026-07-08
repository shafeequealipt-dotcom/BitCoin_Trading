"""Phase 8 (post-Layer-1 fix) — active_universe enrichment from CoinPackage.

The Phase-5 ScannerWorker rewrite intentionally wrote 0.0 placeholders
for ``volume_24h``, ``change_24h_pct``, ``funding_rate``, ``spread_pct``
because the scanner no longer makes its own market-data calls. But the
data IS already on hand — ``_build_package`` populates each CoinPackage
with funding (altdata cache) and change_24h_pct + volume_24h (market
ticker cache). Phase 8 reads those values back into the row insert so
the operator-visible Telegram /status surface shows real numbers.

Investigation: ``dev_notes/phase0_post_layer1_fixes/issue_8_universe_enrichment.md``.
"""

from __future__ import annotations

import re
from pathlib import Path


SCANNER_WORKER = (
    Path(__file__).parent.parent / "src" / "workers" / "scanner_worker.py"
)


def test_enrichment_helper_reads_from_packages() -> None:
    """The local _enrich_for helper must read from `packages`, not pass 0.0."""
    src = SCANNER_WORKER.read_text()
    assert "def _enrich_for(coin: str)" in src
    # The helper must look up `packages.get(coin)`.
    assert "packages.get(coin)" in src


def test_enrichment_pulls_volume_change_funding_from_pkg() -> None:
    """Three of four columns must come from the package's price/alt blocks.

    Layer 1 Defect 10 regression guard: CoinPackage exposes ``price_data``
    and ``alt_data`` (see core/coin_package.py:154,158). The exclusion-mode
    enrich helper historically read ``pkg.price`` and ``pkg.alt`` —
    attributes that do not exist — so its enriched rows always carried
    zeros (caught by the broad ``except`` at the call site).
    """
    src = SCANNER_WORKER.read_text()
    # Both _enrich_for helpers (briefing and exclusion mode) must use
    # the correct attribute names. No occurrences of the wrong names.
    assert 'getattr(pkg.price_data, "volume_24h_usd"' in src
    assert 'getattr(pkg.price_data, "change_24h_pct"' in src
    assert 'getattr(pkg.alt_data, "funding_rate"' in src
    # The historically-wrong attribute names must be absent.
    assert 'getattr(pkg.price,' not in src, (
        "Found the legacy wrong attribute ``pkg.price`` — Defect 10 fix "
        "should have replaced both occurrences with ``pkg.price_data``."
    )
    assert 'getattr(pkg.alt,' not in src, (
        "Found the legacy wrong attribute ``pkg.alt`` — Defect 10 fix "
        "should have replaced both occurrences with ``pkg.alt_data``."
    )


def test_enrichment_handles_missing_package() -> None:
    """Forced-include BTC/ETH may not have a package; helper must fall through to 0.0."""
    src = SCANNER_WORKER.read_text()
    # Look for the fall-through tuple.
    assert "return (0.0, 0.0, 0.0, 0.0)" in src


def test_zero_placeholders_no_longer_in_insert() -> None:
    """The legacy ``0.0,  # volume_24h`` etc. placeholders must be gone.

    Their presence would mean the Phase 8 fix didn't actually thread the
    enrichment through to the INSERT.
    """
    src = SCANNER_WORKER.read_text()
    # The exact legacy comment strings.
    legacy_markers = (
        "0.0,  # volume_24h",
        "0.0,  # change_24h_pct",
        "0.0,  # funding_rate",
    )
    for marker in legacy_markers:
        assert marker not in src, (
            f"Legacy zero-placeholder ``{marker}`` is still in scanner_worker.py "
            f"— Phase 8 did not thread enrichment from CoinPackage to the "
            f"active_universe INSERT."
        )


def test_insert_columns_unchanged() -> None:
    """The DDL contract is preserved — INSERT still names the same 7 columns."""
    src = SCANNER_WORKER.read_text()
    # Source has the column list split across two string-literal lines.
    # Strip ALL whitespace AND string-concatenation glue (quote-space-quote)
    # so the literal SQL emerges contiguously.
    stripped = re.sub(r"\s+", "", src)
    stripped = stripped.replace('""', "")  # adjacent string literals
    expected_compact = (
        "(symbol,opportunity_score,volume_24h,change_24h_pct,"
        "funding_rate,spread_pct,coin_tier)"
    )
    assert expected_compact in stripped, (
        "active_universe INSERT column list changed — schema contract broken. "
        "The Phase 8 fix should ONLY change the values, not the columns."
    )
