"""TP-Volume-Closure fix — REAL-RUNTIME pipeline verification.

Distinct from `tests/test_flip_tp_capper.py` (pure-function unit tests)
and `tests/test_xray_flip_tp_integration.py` (mocked-services
integration tests), this script wires up REAL component instances
against the live `config.toml`, captures actual loguru output, and
asserts the expected log tags and side effects fire when the fix runs
end-to-end through the production code path.

Pipelines covered:
  1.  Settings._load_fresh on REAL config.toml → all 4 FlipTPSettings
      fields populated.
  2.  RiskSettings nested-dataclass DI: `s.risk.flip_tp` is a
      FlipTPSettings instance, not a dict (fail-fast for
      mis-wired builders).
  3.  Backstop unchanged: SLTPValidator default max_distance_pct still
      10% (validator stays as the correctness safety net; cap is the
      desirable bound that fires upstream).
  4.  Pure cap helper: real CoinVolatilityProfile (live OPUSDT shape)
      drives compute_capped_flip_tp through every method branch and
      asserts the cap math.
  5.  Live workers.log evidence: the bug pattern still exists in the
      RUNNING process (sltp_skip + XRAY_DIR_FLIP correlated by did).
      Confirms restart is required to load the fix.
  6.  Module imports + circular-import sanity: every fix-touched
      module loads cleanly from a cold interpreter.
  7.  Trade dict mutation contract: live StrategyWorker.__new__ +
      stubbed services drives _execute_claude_trade through the cap
      path; assert dual-key mutation, log emission, and the exact
      XRAY_FLIP_TP_DERIVATION format.
  8.  Live structure_cache schema sanity: confirm the StructuralPlacement
      type exposes the long_*/short_* fields the flip path consumes
      (catches future renames that would break the cap upstream).

Usage:
    python3 tests/tp_volume_fix_pipeline_test.py

Each pipeline prints PASS/FAIL with the actual evidence captured.
Exit code 0 = all pipelines green, non-zero = first failure.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from types import SimpleNamespace
from typing import Any

# Run from any CWD by inserting project root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)  # so config.toml resolves relative to the project

PASS_GREEN = "\033[32mPASS\033[0m"
FAIL_RED = "\033[31mFAIL\033[0m"

_results: list[tuple[str, bool, str]] = []
_log_buf: list[str] = []


class PipelineFailed(AssertionError):
    pass


def _check(name: str, ok: bool, evidence: str = "") -> None:
    _results.append((name, ok, evidence))
    marker = PASS_GREEN if ok else FAIL_RED
    msg = f"  {marker}  {name}"
    if evidence:
        msg += f"\n         evidence: {evidence}"
    print(msg)
    if not ok:
        raise PipelineFailed(name)


def _logs_contain(tag: str, since_index: int = 0) -> bool:
    return any(tag in line for line in _log_buf[since_index:])


def _setup_log_capture() -> None:
    """In-memory loguru sink so we can grep emitted events."""
    from loguru import logger
    logger.add(
        lambda msg: _log_buf.append(str(msg).rstrip()),
        level="DEBUG",
        format="{message}",
        filter=lambda r: True,
    )


# ─── Pipeline 1 — Settings load from REAL config.toml ────────────────


async def pipeline_01_real_config_load() -> None:
    print("\n■ Pipeline 1 — Settings._load_fresh('config.toml') populates [risk.flip_tp]")
    from src.config.settings import FlipTPSettings, Settings
    Settings._instance = None
    s = Settings._load_fresh("config.toml", ".env")
    cases = [
        ("risk.flip_tp.enabled", s.risk.flip_tp.enabled, True),
        ("risk.flip_tp.hard_ceiling_pct", s.risk.flip_tp.hard_ceiling_pct, 5.0),
        ("risk.flip_tp.fallback_tp_distance_pct", s.risk.flip_tp.fallback_tp_distance_pct, 2.0),
        ("risk.flip_tp.structural_buffer_multiplier", s.risk.flip_tp.structural_buffer_multiplier, 1.0),
    ]
    for name, actual, expected in cases:
        _check(f"settings.{name} = {expected!r}", actual == expected, f"actual={actual!r}")
    _check(
        "type(s.risk.flip_tp) is FlipTPSettings",
        isinstance(s.risk.flip_tp, FlipTPSettings),
        f"type={type(s.risk.flip_tp).__name__}",
    )


# ─── Pipeline 2 — RiskSettings DI sanity ─────────────────────────────


async def pipeline_02_risk_settings_di() -> None:
    print("\n■ Pipeline 2 — RiskSettings.flip_tp wiring + missing-block backwards-compat")
    from src.config.settings import (
        FlipTPSettings,
        RiskSettings,
        _build_flip_tp,
        _build_risk,
    )

    # Default construction — no config block at all.
    rs_default = RiskSettings()
    _check(
        "RiskSettings() default has flip_tp populated",
        isinstance(rs_default.flip_tp, FlipTPSettings),
        f"type={type(rs_default.flip_tp).__name__}",
    )

    # Builder with empty input — equivalent to a missing [risk.flip_tp] block.
    rs_no_block = _build_risk({})
    _check(
        "_build_risk({}) → flip_tp is dataclass defaults",
        rs_no_block.flip_tp == FlipTPSettings(),
        f"got={rs_no_block.flip_tp}",
    )

    # Builder with explicit override — values flow through.
    rs_explicit = _build_risk({
        "flip_tp": {
            "enabled": False,
            "hard_ceiling_pct": 7.5,
            "fallback_tp_distance_pct": 1.5,
            "structural_buffer_multiplier": 1.25,
        },
    })
    _check(
        "_build_risk(explicit) propagates all 4 fields",
        rs_explicit.flip_tp.enabled is False
        and rs_explicit.flip_tp.hard_ceiling_pct == 7.5
        and rs_explicit.flip_tp.fallback_tp_distance_pct == 1.5
        and rs_explicit.flip_tp.structural_buffer_multiplier == 1.25,
        f"got={rs_explicit.flip_tp}",
    )

    # Builder isolation — _build_flip_tp can be called standalone.
    fs = _build_flip_tp({"enabled": True, "hard_ceiling_pct": 6.0})
    _check(
        "_build_flip_tp({partial}) fills missing fields with defaults",
        fs.enabled is True
        and fs.hard_ceiling_pct == 6.0
        and fs.fallback_tp_distance_pct == 2.0
        and fs.structural_buffer_multiplier == 1.0,
        f"got={fs}",
    )


# ─── Pipeline 3 — Backstop unchanged ─────────────────────────────────


async def pipeline_03_validator_backstop() -> None:
    print("\n■ Pipeline 3 — SLTPValidator backstop unchanged (still 10% nonsensical-rejection)")
    from src.core.sl_tp_validator import SLTPValidator

    v = SLTPValidator()
    _check(
        "SLTPValidator default max_distance_pct = 0.10 (10%)",
        v.max_distance_pct == 0.10, f"actual={v.max_distance_pct}",
    )
    # Smoke: a 15% TP still gets rejected. The cap is upstream of this.
    action, _, reason = v.validate_tp(
        tp_price=85.0, current_price=100.0, direction="Sell", symbol="X",
    )
    _check(
        "validate_tp at 15% distance still returns SKIP=nonsensical",
        action == "SKIP" and "nonsensical" in reason,
        f"action={action} reason={reason!r}",
    )


# ─── Pipeline 4 — Pure cap helper end-to-end ─────────────────────────


async def pipeline_04_cap_helper_branches() -> None:
    print("\n■ Pipeline 4 — compute_capped_flip_tp drives all 5 method branches")
    from src.analysis.volatility_profile import CoinVolatilityProfile
    from src.config.settings import FlipTPSettings
    from src.core.flip_tp_capper import (
        METHOD_DISABLED,
        METHOD_FALLBACK,
        METHOD_HARD_CEILING,
        METHOD_STRUCTURAL_KEPT,
        METHOD_VOLATILITY_CAPPED,
        compute_capped_flip_tp,
    )

    def _profile(**ovr):
        base = dict(
            symbol="OPUSDT", atr_pct_5m=0.46, atr_pct_1h=0.50,
            volatility_class="high", recommended_tp_pct=3.90,
            recommended_sl_pct=1.80, recommended_hold_min=54,
            recommended_strategy="trend_follow",
            regime="trending_up", regime_confidence=0.70,
        )
        base.update(ovr)
        return CoinVolatilityProfile(**base)

    # Branch 1 — structural_kept
    tp, m, _ = compute_capped_flip_tp(
        "OPUSDT", "Sell", 0.148, 0.148 * (1 - 0.015),
        _profile(), FlipTPSettings(),
    )
    _check(f"branch=structural_kept (struct 1.5% < vol 3.9%)", m == METHOD_STRUCTURAL_KEPT, f"method={m}")

    # Branch 2 — volatility_capped (live GALAUSDT-shaped failure case)
    tp, m, _ = compute_capped_flip_tp(
        "GALAUSDT", "Sell", 0.148, 0.148 * (1 - 0.157),  # 15.7% from price
        _profile(volatility_class="high", recommended_tp_pct=3.90), FlipTPSettings(),
    )
    expected_capped = 0.148 * (1 - 0.039)
    _check(
        f"branch=volatility_capped (struct 15.7% > vol 3.9%) returns capped tp",
        m == METHOD_VOLATILITY_CAPPED and abs(tp - expected_capped) < 1e-6,
        f"method={m} tp={tp:.6f} expected={expected_capped:.6f}",
    )

    # Branch 3 — hard_ceiling
    tp, m, _ = compute_capped_flip_tp(
        "X", "Buy", 100.0, 120.0,
        _profile(volatility_class="extreme", recommended_tp_pct=6.0),
        FlipTPSettings(structural_buffer_multiplier=1.5, hard_ceiling_pct=5.0),
    )
    _check(f"branch=hard_ceiling (vol*mult 9.0% > ceiling 5%)", m == METHOD_HARD_CEILING, f"method={m}")

    # Branch 4 — fallback (no profile)
    tp, m, _ = compute_capped_flip_tp(
        "NEWCOIN", "Sell", 100.0, 85.0, None, FlipTPSettings(),
    )
    _check(f"branch=fallback (vol_profile=None)", m == METHOD_FALLBACK, f"method={m}")

    # Branch 5 — disabled
    tp, m, _ = compute_capped_flip_tp(
        "X", "Sell", 100.0, 80.0,
        _profile(), FlipTPSettings(enabled=False),
    )
    _check(f"branch=disabled (settings.enabled=False)", m == METHOD_DISABLED, f"method={m}")


# ─── Pipeline 5 — Live workers.log evidence (pre-fix bug confirmed) ──


async def pipeline_05_live_log_evidence() -> None:
    print("\n■ Pipeline 5 — Live workers.log shows the bug pattern (pre-restart runtime)")
    log_path = os.path.join(_ROOT, "data/logs/workers.log")
    if not os.path.isfile(log_path):
        _check("workers.log present", False, f"path={log_path} not found")
        return

    with open(log_path, "r", errors="ignore") as fh:
        content = fh.read()

    sltp_skip_count = len(re.findall(r"TRADE_SKIP \| sym=\S+ rsn=sltp_skip", content))
    flip_count = len(re.findall(r"XRAY_DIR_FLIP \| sym=", content))
    derivation_count = len(re.findall(r"XRAY_FLIP_TP_DERIVATION \|", content))
    derivation_degraded_count = len(re.findall(r"XRAY_FLIP_TP_DERIVATION_DEGRADED \|", content))

    _check(
        f"sltp_skip events present in log (={sltp_skip_count})",
        sltp_skip_count > 0,
        f"count={sltp_skip_count}",
    )
    _check(
        f"XRAY_DIR_FLIP events present in log (={flip_count})",
        flip_count > 0,
        f"count={flip_count}",
    )
    # The new event MUST NOT be in the log yet — running process predates the fix.
    # If it IS present, either (a) operator restarted already (good), or (b) we're
    # inspecting a stale/already-fixed log.
    _check(
        f"XRAY_FLIP_TP_DERIVATION events NOT in current log "
        f"(={derivation_count}; restart pending)",
        derivation_count == 0,
        f"count={derivation_count} "
        f"({'restart already happened' if derivation_count else 'pre-restart confirmed'})",
    )
    _check(
        "XRAY_FLIP_TP_DERIVATION_DEGRADED count consistent",
        derivation_degraded_count == 0,
        f"count={derivation_degraded_count}",
    )


# ─── Pipeline 6 — Cold imports + circular-import sanity ──────────────


async def pipeline_06_cold_imports() -> None:
    print("\n■ Pipeline 6 — Every fix-touched module imports cleanly cold (subprocess)")
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; sys.path.insert(0, '.'); "
            "from src.config.settings import FlipTPSettings, Settings, _build_flip_tp, _build_risk; "
            "from src.core.flip_tp_capper import "
            "  compute_capped_flip_tp, "
            "  METHOD_DISABLED, METHOD_FALLBACK, METHOD_HARD_CEILING, "
            "  METHOD_STRUCTURAL_KEPT, METHOD_VOLATILITY_CAPPED; "
            "from src.workers.strategy_worker import StrategyWorker; "
            "from src.analysis.volatility_profile import CoinVolatilityProfile, VolatilityProfiler; "
            "from src.core.sl_tp_validator import SLTPValidator; "
            "print('OK')"
        )],
        cwd=_ROOT, capture_output=True, text=True, timeout=30,
    )
    _check(
        "cold subprocess import of all fix modules succeeds",
        result.returncode == 0 and "OK" in result.stdout,
        f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr[-200:]!r}",
    )


# ─── Pipeline 7 — Real worker integration (live log capture) ─────────


async def pipeline_07_real_worker_cap_path() -> None:
    print("\n■ Pipeline 7 — Real StrategyWorker._execute_claude_trade emits XRAY_FLIP_TP_DERIVATION")
    from dataclasses import dataclass

    from src.analysis.volatility_profile import CoinVolatilityProfile
    from src.config.settings import FlipTPSettings
    from src.core.types import OrderStatus
    from src.workers.strategy_worker import StrategyWorker

    @dataclass
    class _FakePlacement:
        rr_long: float
        rr_short: float
        long_sl_price: float = 0.0
        long_tp_price: float = 0.0
        short_sl_price: float = 0.0
        short_tp_price: float = 0.0
        rr_ratio: float = 1.0

    @dataclass
    class _FakeMS:
        structure: str

    @dataclass
    class _FakeStruct:
        structural_placement: _FakePlacement
        market_structure: _FakeMS | None
        setup_quality: str = "A"

    class _SCache:
        def __init__(self, p): self._p = p
        def get(self, _s): return self._p

    class _MS:
        def __init__(self, p): self._p = p
        async def get_ticker(self, _s): return SimpleNamespace(last_price=self._p)

    class _OS:
        async def place_order(self, **_):
            return SimpleNamespace(status=OrderStatus.REJECTED, order_id="pl-test-001")

    class _VP:
        def __init__(self, p): self._p = p
        async def get_profile(self, _s): return self._p

    class _Enf:
        def should_allow_trade(self, leverage=1): return True, "ok"
        def qualify_survival_trade(self, _s, _c=None): return True, "ok"
        def get_size_multiplier(self): return 1.0

    last_price = 0.148
    structural_short_tp = last_price * (1 - 0.14)
    placement = _FakePlacement(
        rr_long=0.1, rr_short=5.7,
        long_sl_price=last_price * 1.02, long_tp_price=last_price * 1.04,
        short_sl_price=last_price * 1.02, short_tp_price=structural_short_tp,
    )
    structural = _FakeStruct(
        structural_placement=placement,
        market_structure=_FakeMS(structure="ranging"),
    )
    profile = CoinVolatilityProfile(
        symbol="OPUSDT", atr_pct_5m=0.46, atr_pct_1h=0.50,
        volatility_class="high", recommended_tp_pct=3.90,
        recommended_sl_pct=1.80, recommended_hold_min=54,
        recommended_strategy="trend_follow",
        regime="trending_up", regime_confidence=0.70,
    )

    sw = StrategyWorker.__new__(StrategyWorker)
    sw.settings = SimpleNamespace(
        risk=SimpleNamespace(
            xray_dir_flip_threshold_ratio=3.0,
            xray_dir_flip_enabled=True,  # IMPLEMENT_XRAY_FLIP_SWITCH: ON-state test
            flip_tp=FlipTPSettings(),
            default_stop_loss_pct=3.0,
            default_take_profit_pct=6.0,
        ),
        bybit=None,
    )
    sw.services = {
        "structure_cache": _SCache(structural),
        "market_service": _MS(last_price),
        "order_service": _OS(),
        "volatility_profiler": _VP(profile),
    }
    sw._enforcer = _Enf()

    trade: dict[str, Any] = {
        "symbol": "OPUSDT",
        "direction": "Buy",
        "leverage": 3,
        "size_usd": 200.0,
        "stop_loss_price": last_price * 0.98,
        "take_profit_price": last_price * 1.04,
    }

    log_idx_before = len(_log_buf)
    ok, reason = await sw._execute_claude_trade(trade, set(), plan=None)

    _check(
        "function reached order_reject (cap fired downstream of cap site)",
        ok is False and reason == "order_reject",
        f"ok={ok} reason={reason!r}",
    )
    _check(
        "trade flipped Buy → Sell",
        trade["direction"] == "Sell" and trade.get("_flip_source") == "xray",
        f"dir={trade['direction']} flip_source={trade.get('_flip_source')}",
    )
    _check(
        "cap method = volatility_capped",
        trade["_xray_flip_tp_method"] == "volatility_capped",
        f"method={trade.get('_xray_flip_tp_method')}",
    )
    expected_capped = last_price * (1 - 0.039)
    _check(
        "trade['take_profit_price'] is the capped value",
        abs(trade["take_profit_price"] - expected_capped) < 1e-6,
        f"got={trade['take_profit_price']:.6f} expected={expected_capped:.6f}",
    )
    flip_lines = [
        l for l in _log_buf[log_idx_before:]
        if "XRAY_FLIP_TP_DERIVATION" in l and "DEGRADED" not in l
    ]
    _check(
        "XRAY_FLIP_TP_DERIVATION emitted with structured fields",
        bool(flip_lines)
        and "method=volatility_capped" in flip_lines[-1]
        and "vol_profile_present=True" in flip_lines[-1]
        and "degraded=False" in flip_lines[-1],
        evidence=(flip_lines[-1][:160] if flip_lines else "no event captured"),
    )


# ─── Pipeline 8 — StructuralPlacement schema sanity ──────────────────


async def pipeline_08_structural_placement_schema() -> None:
    print("\n■ Pipeline 8 — StructuralPlacement still exposes long_*/short_* TP fields")
    from src.analysis.structure.models.structure_types import StructuralPlacement
    sp = StructuralPlacement()
    required = (
        "long_sl_price", "long_tp_price",
        "short_sl_price", "short_tp_price",
        "rr_long", "rr_short",
    )
    missing = [f for f in required if not hasattr(sp, f)]
    _check(
        "StructuralPlacement exposes long_*/short_* SL/TP + rr_* fields",
        not missing,
        evidence=f"missing={missing}" if missing else "all 6 fields present",
    )


# ─── Driver ──────────────────────────────────────────────────────────


async def main() -> int:
    _setup_log_capture()

    pipelines = [
        pipeline_01_real_config_load,
        pipeline_02_risk_settings_di,
        pipeline_03_validator_backstop,
        pipeline_04_cap_helper_branches,
        pipeline_05_live_log_evidence,
        pipeline_06_cold_imports,
        pipeline_07_real_worker_cap_path,
        pipeline_08_structural_placement_schema,
    ]
    print("=" * 72)
    print("TP-Volume-Closure fix — REAL-RUNTIME pipeline test")
    print("=" * 72)
    failed = False
    for fn in pipelines:
        try:
            await fn()
        except PipelineFailed:
            failed = True
            break
        except Exception as exc:
            print(f"\n  {FAIL_RED}  unhandled in {fn.__name__}: {exc!r}")
            failed = True
            break

    print("\n" + "=" * 72)
    n_pass = sum(1 for _, ok, _ in _results if ok)
    n_fail = sum(1 for _, ok, _ in _results if not ok)
    print(f"Total: {n_pass} pass / {n_fail} fail across {len(pipelines)} pipelines")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
