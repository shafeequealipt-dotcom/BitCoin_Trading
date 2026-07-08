"""Tests for sweet-spot scheduler + SweetSpotsSettings validation (Phase 1).

Covers:
- ``parse_sweet_spot`` accepts/rejects expected inputs
- ``seconds_until_next_sweet_spot`` math under deterministic ``now``
- ``is_at_sweet_spot`` tolerance
- ``SweetSpotScheduler.wait_for_sweet_spot`` real-clock fire + drift
- ``SweetSpotsSettings.__post_init__`` rejects malformed MM:SS, out-of-window
  minutes, chain-order violations
- ``AltDataSweetSpotsSettings.__post_init__`` rejects bad funding format / bad
  open_interest / fear_greed minutes

References:
- IMPLEMENT_LAYER1_CORRECTED_MIGRATION_PROFESSIONAL.md §"PHASE 1" Trial 1.1/1.2/1.3
- LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md §8
"""

import asyncio
import time

import pytest

from src.config.settings import (
    AltDataSweetSpotsSettings,
    SweetSpotsSettings,
)
from src.core.exceptions import ConfigError
from src.workers.sweet_spot_scheduler import (
    SweetSpotScheduler,
    is_at_sweet_spot,
    parse_sweet_spot,
    seconds_until_next_sweet_spot,
)


# -- parse_sweet_spot ----------------------------------------------------


class TestParseSweetSpot:
    def test_basic(self):
        assert parse_sweet_spot("0:30") == (0, 30)
        assert parse_sweet_spot("4:00") == (4, 0)
        assert parse_sweet_spot("1:45") == (1, 45)

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="must be a string"):
            parse_sweet_spot(45)  # type: ignore[arg-type]

    def test_rejects_bad_format(self):
        with pytest.raises(ValueError, match="MM:SS format"):
            parse_sweet_spot("4")
        with pytest.raises(ValueError, match="MM:SS format"):
            parse_sweet_spot("0:30:0")

    def test_rejects_non_integer(self):
        with pytest.raises(ValueError, match="integers"):
            parse_sweet_spot("a:30")
        with pytest.raises(ValueError, match="integers"):
            parse_sweet_spot("0:b")

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError, match="minute must be 0-59"):
            parse_sweet_spot("60:00")
        with pytest.raises(ValueError, match="second must be 0-59"):
            parse_sweet_spot("0:60")
        with pytest.raises(ValueError, match="minute must be 0-59"):
            parse_sweet_spot("-1:0")


# -- seconds_until_next_sweet_spot ---------------------------------------


class TestSecondsUntilNextSweetSpot:
    def test_now_before_spot_in_window(self):
        # Window starts at t=0, spot at 0:30 = 30s. now=10s → 20s away.
        delta = seconds_until_next_sweet_spot((0, 30), window_minutes=5, now=10.0)
        assert delta == pytest.approx(20.0)

    def test_now_at_spot_skips_to_next_window(self):
        # If we're exactly at the spot (or within skip_threshold_s=0.1),
        # the next firing is in the FOLLOWING window.
        delta = seconds_until_next_sweet_spot((0, 30), window_minutes=5, now=30.0)
        assert delta == pytest.approx(300.0)  # one full window away

    def test_now_just_past_spot(self):
        # 200ms after the spot — past the 100ms skip threshold but within
        # the same window. Wait for next window.
        delta = seconds_until_next_sweet_spot((0, 30), window_minutes=5, now=30.2)
        assert delta == pytest.approx(299.8, abs=0.01)

    def test_after_spot_in_window(self):
        # Spot at 1:00 = 60s. now=120s → past spot in current window;
        # wait for next window.
        delta = seconds_until_next_sweet_spot((1, 0), window_minutes=5, now=120.0)
        assert delta == pytest.approx(240.0)  # 60 + 240 = 300

    def test_aligned_to_clock_minute_boundaries(self):
        # Real wall clock: at 12:01:30 (i.e. 90s past the 12:00 window start),
        # spot 0:30 is 60s ago, so wait for 12:05:30 = 240s away.
        # Using wall-time-style calculation:
        # epoch position = some_now % 300
        # We construct a now where now % 300 == 90
        now = 86400.0 + 90.0  # 1 day + 90s; now % 300 == 90
        delta = seconds_until_next_sweet_spot((0, 30), window_minutes=5, now=now)
        assert delta == pytest.approx(240.0)


# -- is_at_sweet_spot ----------------------------------------------------


class TestIsAtSweetSpot:
    def test_within_tolerance(self):
        assert is_at_sweet_spot((0, 30), now=30.0, tolerance_s=1.0)
        assert is_at_sweet_spot((0, 30), now=30.5, tolerance_s=1.0)
        assert is_at_sweet_spot((0, 30), now=29.5, tolerance_s=1.0)

    def test_outside_tolerance(self):
        assert not is_at_sweet_spot((0, 30), now=32.0, tolerance_s=1.0)
        assert not is_at_sweet_spot((0, 30), now=28.0, tolerance_s=1.0)


# -- SweetSpotScheduler.wait_for_sweet_spot (real clock) -----------------


class TestSchedulerWait:
    """Real-clock test with a short window so the test runs quickly.

    Uses window_minutes=1 internally (we override the validation by
    passing the value directly to the scheduler, not via Settings)
    so the test fires within ~30s instead of ~5min.
    """

    @pytest.mark.asyncio
    async def test_fire_and_drift(self):
        # Build a scheduler with a 1-min window, spot 0:00.
        # That means it fires at every clock minute boundary.
        scheduler = SweetSpotScheduler(
            worker_name="test_worker",
            offset="0:00",
            window_minutes=1,
        )
        # Compute time-to-next-fire — must be < 60s and > 0s
        delay = scheduler.seconds_until_next()
        assert 0.0 < delay <= 60.0

        # Cap test runtime: skip if next fire is more than 30s away
        if delay > 30.0:
            pytest.skip(
                f"next fire is {delay:.1f}s away; skipping to keep test fast"
            )

        t0 = time.time()
        drift_ms = await scheduler.wait_for_sweet_spot()
        elapsed = time.time() - t0
        # We slept approximately delay seconds
        assert elapsed == pytest.approx(delay, abs=0.5)
        # Drift should be bounded — asyncio.sleep is loose but well within 1s
        assert abs(drift_ms) < 1000.0
        # Stats updated
        assert scheduler.stats.fires == 1
        assert abs(scheduler.stats.last_drift_ms - drift_ms) < 0.001

    def test_get_stats_initial(self):
        scheduler = SweetSpotScheduler("test", "0:30", window_minutes=5)
        stats = scheduler.get_stats()
        assert stats["worker"] == "test"
        assert stats["offset"] == "0:30"
        assert stats["fires"] == 0
        assert stats["mean_drift_ms"] == 0.0

    def test_invalid_window(self):
        with pytest.raises(ValueError, match="window_minutes"):
            SweetSpotScheduler("test", "0:30", window_minutes=0)


# -- SweetSpotsSettings validation ---------------------------------------


class TestSweetSpotsSettingsValidation:
    def test_defaults_pass(self):
        s = SweetSpotsSettings()
        assert s.kline_worker == "0:30"
        assert s.scanner_worker == "4:00"
        assert s.window_minutes == 5

    def test_bad_format_raises(self):
        with pytest.raises(ConfigError, match="MM:SS format"):
            SweetSpotsSettings(kline_worker="bad")

    def test_minute_out_of_window(self):
        # window_minutes=5 → max minute is 4. "5:00" is one minute past.
        with pytest.raises(ConfigError, match="minute must be 0-4"):
            SweetSpotsSettings(kline_worker="5:00")

    def test_second_out_of_range(self):
        with pytest.raises(ConfigError, match="second must be 0-59"):
            SweetSpotsSettings(kline_worker="0:60")

    def test_chain_order_violation(self):
        # Set structure_worker BEFORE kline_worker — should fail.
        with pytest.raises(ConfigError, match="chain order violated"):
            SweetSpotsSettings(
                kline_worker="2:00",
                structure_worker="1:00",
            )

    def test_chain_order_equal_seconds_violation(self):
        # Two sequential workers with the same offset — strict >.
        with pytest.raises(ConfigError, match="chain order violated"):
            SweetSpotsSettings(
                kline_worker="0:30",
                structure_worker="0:30",
            )

    def test_window_minutes_validation(self):
        with pytest.raises(ConfigError, match="window_minutes"):
            SweetSpotsSettings(window_minutes=0)
        with pytest.raises(ConfigError, match="window_minutes"):
            SweetSpotsSettings(window_minutes=-1)

    def test_altdata_funding_validation(self):
        with pytest.raises(ConfigError, match="MM:SS format"):
            SweetSpotsSettings(
                altdata=AltDataSweetSpotsSettings(funding_rates="bad"),
            )

    def test_altdata_funding_must_precede_scanner(self):
        # E20: funding_rates == scanner (both 4:00) → not strictly before.
        with pytest.raises(ConfigError, match="must fire strictly BEFORE"):
            SweetSpotsSettings(
                altdata=AltDataSweetSpotsSettings(funding_rates="4:00"),
            )

    def test_altdata_funding_after_scanner_rejected(self):
        # E20: funding_rates (4:30) after scanner (4:00) → rejected.
        with pytest.raises(ConfigError, match="must fire strictly BEFORE"):
            SweetSpotsSettings(
                altdata=AltDataSweetSpotsSettings(funding_rates="4:30"),
            )

    def test_altdata_funding_before_scanner_passes(self):
        # E20: the default 1:45 < 4:00 passes; an explicit 3:00 < 4:00 passes;
        # 1:45 after strategy (1:30) is intentionally NOT enforced (benign #10).
        assert SweetSpotsSettings().altdata.funding_rates == "1:45"
        s = SweetSpotsSettings(
            altdata=AltDataSweetSpotsSettings(funding_rates="3:00"),
        )
        assert s.altdata.funding_rates == "3:00"

    def test_altdata_oi_minutes_validation(self):
        with pytest.raises(ConfigError, match="open_interest_minutes"):
            AltDataSweetSpotsSettings(open_interest_minutes=0)

    def test_altdata_fg_minutes_validation(self):
        with pytest.raises(ConfigError, match="fear_greed_minutes"):
            AltDataSweetSpotsSettings(fear_greed_minutes=-5)

    def test_custom_window_size(self):
        # 10-min window: minute can go 0-9
        s = SweetSpotsSettings(
            kline_worker="0:30",
            structure_worker="2:00",
            signal_worker="4:00",
            regime_worker="6:00",
            strategy_worker="8:00",
            scanner_worker="9:30",
            window_minutes=10,
        )
        assert s.window_minutes == 10
        # And 10:00 (full minute) is rejected
        with pytest.raises(ConfigError, match="minute must be 0-9"):
            SweetSpotsSettings(
                kline_worker="10:00",
                window_minutes=10,
            )
