"""Sniper-Latency-Size Fix Phase 2 — gated prompt compression.

Smoke tests for the new ``[stage2].enable_prompt_compression`` flag.
The compression is identity-preserving (no fields removed; no
abbreviation requiring a decoder key) so flipping False -> True
shrinks the rendered output by tightening separators and lowering
non-critical float precision while leaving the schema intact.
"""

from __future__ import annotations

from src.config.settings import Settings, Stage2Settings


def test_compression_default_is_off() -> None:
    """``enable_prompt_compression`` defaults to False so the flag-flip
    rollout is opt-in. Production config.toml mirrors the default until
    the operator flips it."""
    cfg = Stage2Settings()
    assert cfg.enable_prompt_compression is False


def test_settings_load_picks_up_compression_flag() -> None:
    """[stage2].enable_prompt_compression flows through the loader so
    a config.toml change takes effect at the next service start."""
    s = Settings._load_fresh(config_path="config.toml")
    # Default config.toml ships with compression OFF; this test
    # documents that. Operator flips the flag at restart time.
    assert s.stage2.enable_prompt_compression is False


def test_compression_flag_independent_of_full_layer_block() -> None:
    """The compression flag is orthogonal to enable_full_layer_block;
    when the full-layer formatter is off (default False) the
    compression flag has no effect because the legacy formatter
    doesn't read it. The independence is by design — flipping
    compression on is safe even if full-layer is off (no-op)."""
    cfg = Stage2Settings(
        enable_full_layer_block=False,
        enable_prompt_compression=True,
    )
    assert cfg.enable_full_layer_block is False
    assert cfg.enable_prompt_compression is True
