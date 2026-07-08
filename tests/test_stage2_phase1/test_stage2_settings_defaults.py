"""Stage 2 phase 1 — Stage2Settings dataclass defaults + validation."""

import pytest

from src.config.settings import Settings, Stage2Settings
from src.core.exceptions import ConfigError


class TestStage2SettingsDefaults:
    def test_defaults(self) -> None:
        cfg = Stage2Settings()
        # 2026-05-05 operator preference — top_n widened from 6 to 10
        # to feed Claude a larger candidate set; pairs with the
        # bounded-count contract range update (1-2 → 2-4).
        assert cfg.top_n_to_brain == 10
        assert cfg.enable_full_layer_block is False
        assert cfg.enable_zero_two_contract is False
        assert cfg.enable_priority_trim is False

    def test_top_n_zero_raises(self) -> None:
        with pytest.raises(ConfigError):
            Stage2Settings(top_n_to_brain=0)

    def test_top_n_negative_raises(self) -> None:
        with pytest.raises(ConfigError):
            Stage2Settings(top_n_to_brain=-1)

    def test_top_n_above_scanner_cap_raises(self) -> None:
        with pytest.raises(ConfigError):
            Stage2Settings(top_n_to_brain=20)

    def test_settings_load_fresh_includes_stage2(self, tmp_path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text(
            "[stage2]\n"
            "top_n_to_brain = 4\n"
            "enable_full_layer_block = true\n"
        )
        env = tmp_path / ".env"
        env.write_text("")
        s = Settings._load_fresh(str(toml), str(env))
        assert s.stage2.top_n_to_brain == 4
        assert s.stage2.enable_full_layer_block is True
        assert s.stage2.enable_zero_two_contract is False
        assert s.stage2.enable_priority_trim is False

    def test_settings_missing_stage2_block_uses_defaults(self, tmp_path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text("")
        env = tmp_path / ".env"
        env.write_text("")
        s = Settings._load_fresh(str(toml), str(env))
        # Default raised to 10 (2026-05-05); see Stage2Settings docstring.
        assert s.stage2.top_n_to_brain == 10
        assert s.stage2.enable_full_layer_block is False
