"""Price-display precision: canonical formatter primitives and behaviour.

Covers the C1 foundation of the price-precision fix:
  - decimals_for_tick / decimals_for_price magnitude + tick logic
  - format_price backward-compatibility lock (must stay byte-for-byte
    identical to the historical P0-7 output for existing log/test callers)
  - the new opt-in kwargs (decimals / grouped / strip_zeros)
  - the real mangle-regression cases that motivated the fix

Later commits (PriceFormatter service, surface migration) extend this file.
"""

from src.core.utils import decimals_for_price, decimals_for_tick, format_price


class TestDecimalsForTick:
    def test_common_ticks(self):
        assert decimals_for_tick(0.0001) == 4
        assert decimals_for_tick(0.01) == 2
        assert decimals_for_tick(0.5) == 1
        assert decimals_for_tick(1.0) == 0
        assert decimals_for_tick(10.0) == 0

    def test_sub_e5_ticks_not_scientific(self):
        # The whole point of using Decimal: %g would render these as
        # "1e-05"/"1e-07" and wrongly return 0.
        assert decimals_for_tick(0.00001) == 5
        assert decimals_for_tick(0.000001) == 6
        assert decimals_for_tick(0.0000001) == 7

    def test_trailing_zero_tick_minimised(self):
        # 0.0010 == 0.001 -> 3 places, not 4.
        assert decimals_for_tick(0.0010) == 3

    def test_invalid_tick_returns_zero(self):
        assert decimals_for_tick(0) == 0
        assert decimals_for_tick(-1) == 0
        assert decimals_for_tick(float("nan")) == 0
        assert decimals_for_tick(float("inf")) == 0


class TestDecimalsForPrice:
    def test_magnitude_ladder(self):
        assert decimals_for_price(70000) == 2
        assert decimals_for_price(5.1234) == 4
        assert decimals_for_price(0.5) == 6
        assert decimals_for_price(0.0001) == 8

    def test_ref_price_anchors(self):
        # Tiny SL distance anchored to a high-priced symbol -> 2 dp.
        assert decimals_for_price(0.0003, ref_price=70000) == 2

    def test_bad_input_safe_default(self):
        assert decimals_for_price("nope") == 2  # type: ignore[arg-type]
        assert decimals_for_price(float("nan")) == 2


class TestFormatPriceBackwardCompat:
    """Defaults MUST stay byte-for-byte (guards overhaul29 + log callers)."""

    def test_locked_outputs(self):
        assert format_price(70000) == "70000.00"
        assert format_price(70000, 70000) == "70000.00"
        assert format_price(5.1234) == "5.1234"
        assert format_price(0.5) == "0.500000"
        assert format_price(0.00195) == "0.00195000"

    def test_no_dollar_sign(self):
        assert "$" not in format_price(123.45)

    def test_bad_ref_falls_back_to_str(self):
        # Original semantics: a bad ref_price returns the raw value.
        assert format_price(1.23, "bad") == "1.23"


class TestFormatPriceNewKwargs:
    def test_explicit_decimals(self):
        assert format_price(0.0001584, decimals=7) == "0.0001584"
        assert format_price(653.2, decimals=2) == "653.20"

    def test_grouped(self):
        assert format_price(70000, decimals=2, grouped=True) == "70,000.00"

    def test_strip_zeros(self):
        assert format_price(0.0722, decimals=6, strip_zeros=True) == "0.0722"
        assert format_price(0.0959, decimals=4, strip_zeros=True) == "0.0959"
        assert format_price(5.0, decimals=2, strip_zeros=True) == "5"

    def test_grouped_and_stripped(self):
        assert format_price(70000, decimals=2, grouped=True, strip_zeros=True) == "70,000"

    def test_strip_does_not_touch_grouping_separators(self):
        # No decimal point present -> commas survive.
        assert format_price(70000, decimals=0, grouped=True, strip_zeros=True) == "70,000"


class TestMangleRegression:
    """The actual bug: low-priced coins must not collapse to 2/4 dp."""

    def test_sats_not_mangled_magnitude(self):
        # Old fixed-tier :.4f gave "0.0002"; magnitude path keeps it real.
        out = format_price(0.0001584)
        assert out == "0.00015840"
        assert out not in ("0.0002", "0.00")

    def test_sats_exact_tick(self):
        # tick 1e-7 -> 7 dp -> matches Bybit's display.
        dec = decimals_for_tick(0.0000001)
        assert format_price(0.0001584, decimals=dec, strip_zeros=True) == "0.0001584"

    def test_ena_not_rounded_to_dime(self):
        # Old :.2f rendered 0.0959 as "0.10".
        dec = decimals_for_tick(0.0001)  # 4
        assert format_price(0.0959, decimals=dec, strip_zeros=True) == "0.0959"


class TestPriceFormatter:
    """The canonical display seam (exact tick, magnitude fallback, $ prefix)."""

    def test_exact_tick_precision(self):
        from src.core.price_formatter import PriceFormatter

        pf = PriceFormatter(decimals_resolver=lambda s: 4)
        assert pf.format(0.0959, "ENAUSDT") == "$0.0959"

    def test_exact_tick_sub_cent(self):
        from src.core.price_formatter import PriceFormatter

        pf = PriceFormatter(decimals_resolver=lambda s: 7)
        assert pf.format(0.0001584, "10000SATSUSDT") == "$0.0001584"

    def test_grouped_high_price(self):
        from src.core.price_formatter import PriceFormatter

        pf = PriceFormatter(decimals_resolver=lambda s: 2)
        assert pf.format(70000, "BTCUSDT") == "$70,000"

    def test_no_resolver_uses_magnitude(self):
        from src.core.price_formatter import PriceFormatter

        pf = PriceFormatter()
        assert pf.has_tick_resolver is False
        # magnitude: <=0.01 -> 8dp -> "0.00015840" -> strip -> "0.0001584"
        assert pf.format(0.0001584, "X") == "$0.0001584"

    def test_resolver_none_falls_back(self):
        from src.core.price_formatter import PriceFormatter

        pf = PriceFormatter(decimals_resolver=lambda s: None)
        assert pf.format(0.0959, "X") == "$0.0959"

    def test_resolver_error_never_breaks_render(self):
        from src.core.price_formatter import PriceFormatter

        def _boom(_s):
            raise RuntimeError("resolver down")

        pf = PriceFormatter(decimals_resolver=_boom)
        assert pf.format(0.0959, "X") == "$0.0959"  # magnitude fallback

    def test_empty_symbol_skips_resolver(self):
        from src.core.price_formatter import PriceFormatter

        calls: list[str] = []

        def _track(s):
            calls.append(s)
            return 2

        pf = PriceFormatter(decimals_resolver=_track)
        assert pf.format(0.0959, "") == "$0.0959"  # magnitude, resolver not called
        assert calls == []


class TestInstrumentPriceDecimals:
    """Sync cache read that backs the exact-tick resolver."""

    def _svc(self):
        from src.trading.services.instrument_service import InstrumentService

        return InstrumentService(client=None)  # client unused by price_decimals

    def test_known_tick(self):
        from types import SimpleNamespace

        svc = self._svc()
        svc._cache["ENAUSDT"] = SimpleNamespace(price_tick=0.0001)
        assert svc.price_decimals("ENAUSDT") == 4

    def test_sub_cent_tick(self):
        from types import SimpleNamespace

        svc = self._svc()
        svc._cache["10000SATSUSDT"] = SimpleNamespace(price_tick=0.0000001)
        assert svc.price_decimals("10000SATSUSDT") == 7

    def test_cache_miss_returns_none(self):
        assert self._svc().price_decimals("NOPEUSDT") is None

    def test_nonpositive_tick_returns_none(self):
        from types import SimpleNamespace

        svc = self._svc()
        svc._cache["BADUSDT"] = SimpleNamespace(price_tick=0.0)
        assert svc.price_decimals("BADUSDT") is None
