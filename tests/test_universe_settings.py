"""Tests for UniverseSettings — Layer 1 watch_list validation."""

from __future__ import annotations

import os

import pytest

from src.config.settings import (
    _UNIVERSE_MIN_SIZE,
    _UNIVERSE_SYMBOL_PATTERN,
    Settings,
    UniverseSettings,
)
from src.core.exceptions import ConfigError

# A minimal valid 10-coin list used as the floor in many tests.
VALID_TEN: list[str] = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "APTUSDT",
]


class TestUniverseSettingsValidation:
    def test_default_factory_is_valid(self):
        """Calling the dataclass with no args yields a valid 10-coin default."""
        u = UniverseSettings()
        assert len(u.watch_list) == _UNIVERSE_MIN_SIZE
        assert "BTCUSDT" in u.watch_list

    def test_valid_ten_coin_list(self):
        u = UniverseSettings(watch_list=list(VALID_TEN))
        assert u.watch_list == VALID_TEN

    def test_valid_fifty_coin_list(self):
        symbols = [f"COIN{i:02d}USDT" for i in range(50)]
        u = UniverseSettings(watch_list=symbols)
        assert len(u.watch_list) == 50

    def test_empty_list_raises(self):
        with pytest.raises(ConfigError, match="cannot be empty"):
            UniverseSettings(watch_list=[])

    def test_below_min_size_raises(self):
        with pytest.raises(ConfigError, match="at least"):
            UniverseSettings(watch_list=VALID_TEN[:5])

    def test_lowercase_symbol_raises(self):
        bad = list(VALID_TEN)
        bad[5] = "btcusdt"
        with pytest.raises(ConfigError, match="^.*does not match"):
            UniverseSettings(watch_list=bad)

    def test_missing_usdt_suffix_raises(self):
        bad = list(VALID_TEN)
        bad[5] = "BTC"
        with pytest.raises(ConfigError, match="does not match"):
            UniverseSettings(watch_list=bad)

    def test_special_chars_raise(self):
        bad = list(VALID_TEN)
        bad[5] = "BTC-USDT"
        with pytest.raises(ConfigError, match="does not match"):
            UniverseSettings(watch_list=bad)

    def test_non_string_entry_raises(self):
        bad: list = list(VALID_TEN)
        bad[5] = 123
        with pytest.raises(ConfigError, match="must be str"):
            UniverseSettings(watch_list=bad)

    def test_duplicate_symbol_raises(self):
        dup = list(VALID_TEN)
        dup[3] = dup[0]
        with pytest.raises(ConfigError, match="duplicate"):
            UniverseSettings(watch_list=dup)

    def test_numeric_prefix_symbol_accepted(self):
        """Bybit perpetuals like 1000PEPEUSDT, SHIB1000USDT are valid."""
        valid = list(VALID_TEN)
        valid[0] = "1000PEPEUSDT"
        valid[1] = "SHIB1000USDT"
        u = UniverseSettings(watch_list=valid)
        assert u.watch_list[0] == "1000PEPEUSDT"

    def test_pattern_anchors(self):
        """Regex must anchor — partial matches are rejected."""
        assert _UNIVERSE_SYMBOL_PATTERN.match("BTCUSDT") is not None
        assert _UNIVERSE_SYMBOL_PATTERN.match("BTCUSDTX") is None
        assert _UNIVERSE_SYMBOL_PATTERN.match(" BTCUSDT") is None
        assert _UNIVERSE_SYMBOL_PATTERN.match("BTCUSD") is None


class TestUniverseFromConfigToml:
    def _write_config(self, tmp_dir: str, watch_list_toml: str) -> str:
        """Helper: write a minimal config.toml with the given [universe] section."""
        path = os.path.join(tmp_dir, "config.toml")
        with open(path, "w") as f:
            f.write(
                "[general]\nmode = \"paper\"\n"
                "[bybit]\ntestnet = true\ndefault_symbols = [\"BTCUSDT\"]\n"
                "[finnhub]\nenabled = false\n"
                "[reddit]\nenabled = false\n"
                "[altdata]\nenabled = false\n"
                "[database]\npath = \""
                + os.path.join(tmp_dir, "test.db").replace("\\", "/")
                + "\"\n"
                "[workers]\nenabled = false\n"
                "[brain]\nenabled = false\n"
                "[risk]\n"
                "max_leverage = 3\n"
                "mandatory_stop_loss = true\n"
                "default_stop_loss_pct = 2.0\n"
                "default_take_profit_pct = 4.0\n"
                "max_position_size_pct = 10.0\n"
                "max_open_positions = 5\n"
                "daily_loss_limit_pct = 5.0\n"
                "max_total_exposure_pct = 50.0\n"
                "max_drawdown_pct = 15.0\n"
                "min_order_value_usdt = 10.0\n"
                "loss_cooldown_seconds = 300\n"
                "[alerts]\ntelegram_enabled = false\n"
                "[mcp]\ntransport = \"stdio\"\n"
                + watch_list_toml
            )
        env = os.path.join(tmp_dir, ".env")
        with open(env, "w") as f:
            f.write("")
        return path

    def test_loads_50_coin_list(self, tmp_dir):
        unique_50 = [f"X{i:02d}USDT" for i in range(50)]
        toml_block = "[universe]\nwatch_list = [" + ", ".join(f'"{s}"' for s in unique_50) + "]\n"
        path = self._write_config(tmp_dir, toml_block)
        Settings.reset()
        s = Settings._load_fresh(path, os.path.join(tmp_dir, ".env"))
        assert len(s.universe.watch_list) == 50
        Settings.reset()

    def test_missing_universe_section_uses_defaults(self, tmp_dir):
        """No [universe] section → defaults to 10-coin watch_list."""
        path = self._write_config(tmp_dir, "")
        Settings.reset()
        s = Settings._load_fresh(path, os.path.join(tmp_dir, ".env"))
        assert len(s.universe.watch_list) == _UNIVERSE_MIN_SIZE
        Settings.reset()

    def test_invalid_watch_list_in_toml_raises(self, tmp_dir):
        """Bad watch_list in TOML triggers ConfigError on Settings load."""
        # lowercase + too short
        toml_block = "[universe]\nwatch_list = [\"btcusdt\", \"ethusdt\"]\n"
        path = self._write_config(tmp_dir, toml_block)
        Settings.reset()
        with pytest.raises(ConfigError):
            Settings._load_fresh(path, os.path.join(tmp_dir, ".env"))
        Settings.reset()

    def test_live_config_toml_loads(self):
        """The actual production config.toml's watch_list is valid (smoke test)."""
        # Run from project root so config.toml resolves.
        import os as _os
        cwd = _os.getcwd()
        try:
            _os.chdir("/home/inshadaliqbal786/trading-intelligence-mcp")
            Settings.reset()
            s = Settings._load_fresh("config.toml", ".env")
            assert s.universe.watch_list, "live watch_list is empty"
            assert len(s.universe.watch_list) >= _UNIVERSE_MIN_SIZE
            assert all(_UNIVERSE_SYMBOL_PATTERN.match(sym) for sym in s.universe.watch_list)
            assert "BTCUSDT" in s.universe.watch_list
            Settings.reset()
        finally:
            _os.chdir(cwd)


class TestExtractionMap:
    """Verify the auto-derived ticker map + coin_aliases overlay."""

    def test_default_aliases_empty(self):
        """No aliases → map only contains auto-derived tickers."""
        u = UniverseSettings(watch_list=list(VALID_TEN))
        # Each watch_list symbol has a base-asset ticker; for the canonical
        # 10 used here, all are well-formed.
        assert "btc" in u.extraction_map
        assert u.extraction_map["btc"] == "BTCUSDT"
        assert "eth" in u.extraction_map
        assert "doge" in u.extraction_map
        # Aliases dict is empty → only the 10 ticker keys present.
        assert len(u.extraction_map) == len(VALID_TEN)

    def test_50_coin_watch_list_yields_50_tickers(self):
        symbols = [f"COIN{i:02d}USDT" for i in range(50)]
        u = UniverseSettings(watch_list=symbols)
        # Every well-formed COINxxUSDT yields a unique ticker key.
        assert len(u.extraction_map) == 50

    def test_aliases_overlay(self):
        """Operator-supplied aliases sit alongside auto-derived tickers."""
        u = UniverseSettings(
            watch_list=list(VALID_TEN),
            coin_aliases={
                "BTCUSDT": ["bitcoin", "Sat0shi"],
                "ETHUSDT": ["ethereum", "ether"],
            },
        )
        # Tickers (auto-derived).
        assert u.extraction_map["btc"] == "BTCUSDT"
        assert u.extraction_map["eth"] == "ETHUSDT"
        # Aliases (case-insensitive, stripped).
        assert u.extraction_map["bitcoin"] == "BTCUSDT"
        assert u.extraction_map["sat0shi"] == "BTCUSDT"  # lowercased
        assert u.extraction_map["ethereum"] == "ETHUSDT"
        assert u.extraction_map["ether"] == "ETHUSDT"

    def test_alias_for_orphan_symbol_raises(self):
        with pytest.raises(ConfigError, match="not in watch_list"):
            UniverseSettings(
                watch_list=list(VALID_TEN),
                coin_aliases={"FOOBARUSDT": ["foo"]},
            )

    def test_alias_collision_raises(self):
        """Same alias mapping to two watch_list symbols → ConfigError."""
        with pytest.raises(ConfigError, match="maps to both"):
            UniverseSettings(
                watch_list=list(VALID_TEN),
                coin_aliases={
                    "BTCUSDT": ["coin"],
                    "ETHUSDT": ["coin"],  # collision
                },
            )

    def test_ticker_collision_raises(self):
        """Two watch_list symbols normalising to the same ticker → ConfigError.

        ``extract_base_asset`` strips ``USDT`` and the ``1000`` numeric
        prefix, so ``PEPEUSDT`` and ``1000PEPEUSDT`` both yield ``pepe``.
        """
        bad = list(VALID_TEN)
        bad[8] = "PEPEUSDT"
        bad[9] = "1000PEPEUSDT"
        with pytest.raises(ConfigError, match="ticker collision"):
            UniverseSettings(watch_list=bad)

    def test_numeric_prefix_yields_base_ticker(self):
        """1000PEPEUSDT → 'pepe' in the map."""
        wl = list(VALID_TEN)
        wl[0] = "1000PEPEUSDT"
        u = UniverseSettings(watch_list=wl)
        assert u.extraction_map["pepe"] == "1000PEPEUSDT"

    def test_non_list_alias_value_raises(self):
        with pytest.raises(ConfigError, match="must be a list"):
            UniverseSettings(
                watch_list=list(VALID_TEN),
                coin_aliases={"BTCUSDT": "bitcoin"},  # str, not list
            )

    def test_empty_alias_string_raises(self):
        with pytest.raises(ConfigError, match="non-string or empty"):
            UniverseSettings(
                watch_list=list(VALID_TEN),
                coin_aliases={"BTCUSDT": [""]},
            )

    def test_non_string_alias_raises(self):
        with pytest.raises(ConfigError, match="non-string or empty"):
            UniverseSettings(
                watch_list=list(VALID_TEN),
                coin_aliases={"BTCUSDT": [123]},  # type: ignore[list-item]
            )

    def test_alias_strip_and_lowercase(self):
        u = UniverseSettings(
            watch_list=list(VALID_TEN),
            coin_aliases={"BTCUSDT": ["  Bitcoin  "]},
        )
        assert u.extraction_map["bitcoin"] == "BTCUSDT"
        assert "  bitcoin  " not in u.extraction_map  # not stored unstripped
        assert "Bitcoin" not in u.extraction_map  # not stored uppercase

    def test_extraction_map_init_false(self):
        """Caller cannot pass extraction_map to __init__."""
        with pytest.raises(TypeError):
            # init=False fields raise TypeError when passed positionally /
            # via kwarg.
            UniverseSettings(  # type: ignore[call-arg]
                watch_list=list(VALID_TEN),
                extraction_map={"btc": "BTCUSDT"},
            )

    def test_live_config_extraction_map_populated(self):
        """Production config.toml yields a populated map (smoke test)."""
        import os as _os
        cwd = _os.getcwd()
        try:
            _os.chdir("/home/inshadaliqbal786/trading-intelligence-mcp")
            Settings.reset()
            s = Settings._load_fresh("config.toml", ".env")
            # 50 watch_list coins → at least ~45 ticker keys (some may
            # ticker-collide if numeric prefixes appear).
            assert len(s.universe.extraction_map) >= 50
            # Auto-derived tickers from the live watch_list must be present.
            assert s.universe.extraction_map.get("btc") == "BTCUSDT"
            assert s.universe.extraction_map.get("aave") == "AAVEUSDT"
            assert s.universe.extraction_map.get("render") == "RENDERUSDT"
            assert s.universe.extraction_map.get("ondo") == "ONDOUSDT"
            # Operator-supplied aliases from [universe.coin_aliases].
            assert s.universe.extraction_map.get("bitcoin") == "BTCUSDT"
            assert s.universe.extraction_map.get("ethereum") == "ETHUSDT"
            Settings.reset()
        finally:
            _os.chdir(cwd)
