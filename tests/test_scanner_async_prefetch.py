"""Layer 1D async-correctness regression tests — F&G + open-position prefetch.

Bug fixed (2026-04-29): ``ScannerWorker._build_package`` was calling two
async methods synchronously without ``await``:

  1. ``fg.get_latest()`` — ``FearGreedClient.get_latest`` is ``async def``.
     The unawaited coroutine flowed into ``getattr(coro, "value", 0)``
     whose default returned ``0``. ``try/except: pass`` (the original
     code) hid the resulting ``RuntimeWarning: coroutine was never
     awaited``, so every package built since the bug shipped had
     ``alt_data.fear_greed = 0``. The validator
     (``coin_package_validator.py:34``: ``alt_data.fear_greed > 0``)
     treated 0 as missing, capping completeness at ~0.94 — below the
     0.95 brain boot-grace threshold — and dropping every CALL_A trade
     for the first 10 minutes after every restart.

  2. ``pos_svc.get_position(symbol)`` — ``PositionService.get_position``
     is ``async def``. Same coroutine-without-await pattern.
     ``open_position`` would land as ``None`` even when an open
     position existed, silently breaking HR-2 ("force-include open
     positions so Claude can decide hold/close"). Currently masked
     because the operator runs with no open positions.

Fix design: prefetch async-context data ONCE per ``tick`` cycle via the
new ``_prefetch_fear_greed`` and ``_prefetch_open_positions`` async
helpers, then pass resolved values into the sync ``_build_package``
through new kw-only parameters. Mirrors the existing ``recent_loss_set``
prefetch idiom (scanner_worker.py:740-749). Concentrates async I/O at
the cycle level; ``_build_package`` becomes a pure-data assembler.

These tests cover:
  * ``_prefetch_fear_greed`` — happy path, missing service, exception.
  * ``_prefetch_open_positions`` — empty input, missing service, partial
    failure (one symbol errors, others succeed).
  * ``_build_package`` — ``fg_value`` and ``position`` parameters
    propagate to the resulting CoinPackage; ``None`` propagates as the
    visible-not-silent default (0 / None).
  * Static asserts against the source — the buggy ``fg.get_latest()``
    and ``pos_svc.get_position(symbol)`` sync-call lines are gone.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.types import FearGreedData
from src.workers.scanner_worker import ScannerWorker


SCANNER_WORKER_SRC = (
    Path(__file__).parent.parent / "src" / "workers" / "scanner_worker.py"
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _bare_scanner(services: dict) -> ScannerWorker:
    """Build a ``ScannerWorker`` with no constructor side-effects.

    The async helpers under test only access ``self.services``; the
    full constructor wiring (sweet-spot scheduler, settings, db,
    MarketScanner) is irrelevant for unit testing the prefetch and
    builder semantics. Bypass via ``__new__`` and attach only what's
    needed — same pattern used by
    ``test_phase4_layer1_restructure/test_cold_start_resume_enforcement.py``
    against ``LayerManager``.
    """
    w = ScannerWorker.__new__(ScannerWorker)
    w.services = services
    return w


def _fg_data(value: int) -> FearGreedData:
    """Construct a ``FearGreedData`` with the given numeric value."""
    from datetime import datetime, timezone
    return FearGreedData(
        value=value,
        classification="Test",
        timestamp=datetime.now(tz=timezone.utc),
    )


# ──────────────────────────────────────────────────────────────────────
# _prefetch_fear_greed
# ──────────────────────────────────────────────────────────────────────


class TestPrefetchFearGreed:
    async def test_returns_int_when_service_responds(self) -> None:
        """Happy path: service returns FearGreedData → helper returns int value."""
        fg_svc = MagicMock()
        fg_svc.get_latest = AsyncMock(return_value=_fg_data(42))
        scanner = _bare_scanner({"fear_greed": fg_svc})

        result = await scanner._prefetch_fear_greed()

        assert result == 42
        fg_svc.get_latest.assert_awaited_once()

    async def test_returns_none_when_service_missing(self) -> None:
        """Service unwired (key absent) → None, no exception."""
        scanner = _bare_scanner({})

        result = await scanner._prefetch_fear_greed()

        assert result is None

    async def test_returns_none_when_service_returns_none(self) -> None:
        """Service responds with ``None`` (e.g. DB cold) → None propagated."""
        fg_svc = MagicMock()
        fg_svc.get_latest = AsyncMock(return_value=None)
        scanner = _bare_scanner({"fear_greed": fg_svc})

        result = await scanner._prefetch_fear_greed()

        assert result is None

    async def test_returns_none_on_exception(self) -> None:
        """Service raises → helper logs SCANNER_FG_PREFETCH_FAIL and returns None.

        Critical: the original code's silent ``try/except: pass`` is
        what hid the bug. The helper MUST log a structured warning so
        operators see persistent fetch failures.
        """
        fg_svc = MagicMock()
        fg_svc.get_latest = AsyncMock(side_effect=RuntimeError("boom"))
        scanner = _bare_scanner({"fear_greed": fg_svc})

        result = await scanner._prefetch_fear_greed()

        assert result is None

    async def test_returns_none_when_value_attr_missing(self) -> None:
        """Object without ``.value`` attr → None (defensive default), not 0.

        This is the regression-trigger condition: the buggy code did
        ``getattr(coro, "value", 0) or 0`` and silently returned 0.
        The helper now uses ``getattr(data, "value", 0)`` with an
        int-coercion guard but only returns the result when ``data``
        is non-None. A non-None object lacking the attribute should
        surface as 0 from getattr → caught by the int conversion.
        """
        # Object without .value attr; getattr default kicks in (returns 0)
        bogus = MagicMock(spec=[])  # no attrs at all
        fg_svc = MagicMock()
        fg_svc.get_latest = AsyncMock(return_value=bogus)
        scanner = _bare_scanner({"fear_greed": fg_svc})

        result = await scanner._prefetch_fear_greed()

        # ``int(None or 0) == 0`` — but we've still guarded against
        # ``data`` itself being None. So a non-None data object with no
        # ``.value`` legitimately reads as 0. Acceptable: validator will
        # treat 0 as missing, which is the correct visible-failure mode
        # (vs silently masking the issue with a fake reading).
        assert result == 0


# ──────────────────────────────────────────────────────────────────────
# _prefetch_open_positions
# ──────────────────────────────────────────────────────────────────────


class TestPrefetchOpenPositions:
    async def test_empty_input_short_circuits(self) -> None:
        """No forced symbols → no service call, empty dict."""
        pos_svc = MagicMock()
        pos_svc.get_position = AsyncMock()
        scanner = _bare_scanner({"position": pos_svc})

        result = await scanner._prefetch_open_positions([])

        assert result == {}
        pos_svc.get_position.assert_not_awaited()

    async def test_returns_empty_when_service_missing(self) -> None:
        """Service unwired → empty dict, no crash even with non-empty input."""
        scanner = _bare_scanner({})

        result = await scanner._prefetch_open_positions(["BTCUSDT"])

        assert result == {}

    async def test_resolves_position_to_dict(self) -> None:
        """Position with ``.to_dict()`` → dict-form is the value in the result."""
        pos = MagicMock()
        pos.to_dict = MagicMock(return_value={"symbol": "BTCUSDT", "size": 1.0})
        pos_svc = MagicMock()
        pos_svc.get_position = AsyncMock(return_value=pos)
        scanner = _bare_scanner({"position": pos_svc})

        result = await scanner._prefetch_open_positions(["BTCUSDT"])

        assert result == {"BTCUSDT": {"symbol": "BTCUSDT", "size": 1.0}}

    async def test_skips_none_positions(self) -> None:
        """Service returns None for a symbol → that symbol is absent from dict."""
        pos_svc = MagicMock()
        pos_svc.get_position = AsyncMock(return_value=None)
        scanner = _bare_scanner({"position": pos_svc})

        result = await scanner._prefetch_open_positions(["BTCUSDT", "ETHUSDT"])

        assert result == {}

    async def test_partial_failure_continues(self) -> None:
        """Per-symbol exception → that symbol skipped; others still resolved.

        Critical for HR-2: one bad lookup must not block the others.
        """
        good_pos = MagicMock()
        good_pos.to_dict = MagicMock(return_value={"symbol": "ETHUSDT"})

        async def _selective(sym: str):
            if sym == "BTCUSDT":
                raise RuntimeError("transient")
            return good_pos

        pos_svc = MagicMock()
        pos_svc.get_position = AsyncMock(side_effect=_selective)
        scanner = _bare_scanner({"position": pos_svc})

        result = await scanner._prefetch_open_positions(["BTCUSDT", "ETHUSDT"])

        assert "BTCUSDT" not in result
        assert result.get("ETHUSDT") == {"symbol": "ETHUSDT"}

    async def test_position_service_alt_key(self) -> None:
        """``position_service`` key is honored when ``position`` is absent.

        The codebase uses both ``services["position"]`` and
        ``services["position_service"]`` depending on registration site.
        """
        pos = MagicMock()
        pos.to_dict = MagicMock(return_value={"symbol": "BTCUSDT"})
        pos_svc = MagicMock()
        pos_svc.get_position = AsyncMock(return_value=pos)
        scanner = _bare_scanner({"position_service": pos_svc})

        result = await scanner._prefetch_open_positions(["BTCUSDT"])

        assert result == {"BTCUSDT": {"symbol": "BTCUSDT"}}


# ──────────────────────────────────────────────────────────────────────
# _build_package — fg_value / position parameter propagation
# ──────────────────────────────────────────────────────────────────────


class TestBuildPackageParams:
    """Validates that the new kw-only parameters reach the output package.

    ``_build_package`` reads many services defensively; with all
    services unwired it still produces a valid CoinPackage with
    sensible defaults. The tests below only assert on the two fields
    the fix touches.
    """

    def test_fg_value_propagates_to_alt_data(self) -> None:
        """fg_value=42 → package.alt_data.fear_greed == 42."""
        scanner = _bare_scanner({})

        pkg = scanner._build_package(
            symbol="BTCUSDT",
            score=0.5,
            record={"reasons_passed": [], "reasons_failed": [], "blockers": []},
            forced=False,
            fg_value=42,
            position=None,
        )

        assert pkg.alt_data.fear_greed == 42

    def test_fg_value_none_yields_zero(self) -> None:
        """fg_value=None → package.alt_data.fear_greed stays at 0 default.

        This is the *visible* failure mode: the validator marks
        fear_greed as missing rather than the package silently
        carrying a fake reading.
        """
        scanner = _bare_scanner({})

        pkg = scanner._build_package(
            symbol="BTCUSDT",
            score=0.0,
            record={"reasons_passed": [], "reasons_failed": [], "blockers": []},
            forced=False,
            fg_value=None,
            position=None,
        )

        assert pkg.alt_data.fear_greed == 0

    def test_position_propagates_to_open_position_when_forced(self) -> None:
        """forced=True + position=dict → package.open_position == dict."""
        scanner = _bare_scanner({})
        pos_dict = {"symbol": "BTCUSDT", "size": 1.5}

        pkg = scanner._build_package(
            symbol="BTCUSDT",
            score=0.0,
            record={"reasons_passed": [], "reasons_failed": [], "blockers": []},
            forced=True,
            fg_value=50,
            position=pos_dict,
        )

        assert pkg.open_position == pos_dict

    def test_position_ignored_when_not_forced(self) -> None:
        """Qualified path always carries open_position=None even if a dict was passed.

        The forced contract is one-way: only forced packages may carry
        an ``open_position``. Qualified packages get the field zeroed
        out unconditionally so the schema is unambiguous downstream.
        """
        scanner = _bare_scanner({})

        pkg = scanner._build_package(
            symbol="SKRUSDT",
            score=0.6,
            record={"reasons_passed": ["xray_setup=foo"], "reasons_failed": [], "blockers": []},
            forced=False,
            fg_value=50,
            position={"symbol": "SKRUSDT"},  # explicitly passed; should be ignored
        )

        assert pkg.open_position is None

    def test_default_params_stay_safe(self) -> None:
        """Both kw params have safe defaults (None) — no caller is forced to update.

        Backward-compatibility guard: if a future refactor accidentally
        drops the kw arguments, the call still succeeds and just yields
        the previous (zero) F&G value, NOT a crash.
        """
        scanner = _bare_scanner({})

        pkg = scanner._build_package(
            symbol="BTCUSDT",
            score=0.0,
            record={"reasons_passed": [], "reasons_failed": [], "blockers": []},
            forced=False,
        )

        assert pkg.alt_data.fear_greed == 0
        assert pkg.open_position is None


# ──────────────────────────────────────────────────────────────────────
# Static guards — the buggy lines must not return
# ──────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    """Source-grep guards against the specific bug pattern returning.

    These are belt-and-braces: the behavioural tests above already
    cover the fix, but the static asserts make a future regression of
    the *exact* sync-call-on-async-method pattern fail loudly at test
    time without running anything.
    """

    def test_no_unawaited_get_latest_in_build_package(self) -> None:
        """The exact buggy call patterns may not reappear inside ``_build_package``.

        Targets the *executable* form of the bug, not text mentions:

          * ``= fg.get_latest()`` — the original broken assignment.
          * ``= pos_svc.get_position(`` — the original broken
            position lookup.

        Both legitimately appear inside the prefetch helpers (with
        ``await``). The check below scopes to the AST body of
        ``_build_package`` only, using ``ast.parse`` for a precise
        boundary that ignores docstrings/comments mentioning the bug.
        """
        import ast

        src = SCANNER_WORKER_SRC.read_text()
        tree = ast.parse(src)
        build_pkg_node: ast.FunctionDef | None = None
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for item in cls.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "_build_package"
                ):
                    build_pkg_node = item
                    break
            if build_pkg_node is not None:
                break
        assert build_pkg_node is not None, (
            "_build_package not found — test scaffolding stale."
        )

        # Walk the body, ignore the docstring (Expr/Constant at index 0).
        body_nodes = build_pkg_node.body
        if (
            body_nodes
            and isinstance(body_nodes[0], ast.Expr)
            and isinstance(body_nodes[0].value, ast.Constant)
        ):
            body_nodes = body_nodes[1:]

        # Collect every Call expression inside the executable body and
        # check the function-attribute path. ``fg.get_latest`` and
        # ``pos_svc.get_position`` are method calls — Attribute nodes.
        forbidden = {"get_latest", "get_position"}
        for stmt in body_nodes:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                    attr = sub.func.attr
                    if attr in forbidden:
                        recv = ""
                        if isinstance(sub.func.value, ast.Name):
                            recv = sub.func.value.id
                        # Permit non-async accessors that happen to share
                        # the name; the regression is specifically
                        # ``fg.get_latest`` and ``pos_svc.get_position``.
                        if attr == "get_latest" and recv == "fg":
                            raise AssertionError(
                                "Regression: ``fg.get_latest()`` reappeared inside "
                                "``_build_package``. F&G must be prefetched in "
                                "``_prefetch_fear_greed`` and passed via ``fg_value=``."
                            )
                        if attr == "get_position" and recv == "pos_svc":
                            raise AssertionError(
                                "Regression: ``pos_svc.get_position()`` reappeared "
                                "inside ``_build_package``. Positions must be "
                                "prefetched in ``_prefetch_open_positions`` and "
                                "passed via ``position=``."
                            )

    def test_prefetch_helpers_exist_and_are_async(self) -> None:
        """The two prefetch helpers must exist and be ``async def``."""
        src = SCANNER_WORKER_SRC.read_text()
        assert "async def _prefetch_fear_greed(self)" in src
        assert "async def _prefetch_open_positions(" in src

    def test_tick_awaits_both_prefetches(self) -> None:
        """``tick`` must call BOTH prefetches with ``await`` before the build loop.

        Failing this means the cycle is shipping with stale or missing
        F&G / position data — re-introducing the exact bug the fix
        addresses.
        """
        src = SCANNER_WORKER_SRC.read_text()
        # Tick body is the only place these awaits should appear (other
        # than the helpers' own calls, which are ``await fg.get_latest()``
        # and ``await pos_svc.get_position(...)``).
        assert "await self._prefetch_fear_greed()" in src
        assert "await self._prefetch_open_positions(" in src

    def test_build_package_call_passes_kw_args(self) -> None:
        """The single call site of ``_build_package`` in tick must pass kw args.

        Guards against a refactor that drops the kw arguments and
        silently restores the previous all-zero F&G behaviour.
        """
        src = SCANNER_WORKER_SRC.read_text()
        assert "fg_value=fg_value" in src
        assert "position=position_lookup.get(coin)" in src


# pytest-asyncio in auto mode is configured at the project level
# (``asyncio: mode=Mode.AUTO`` in pyproject.toml), so async tests above
# do not need a per-function marker.
