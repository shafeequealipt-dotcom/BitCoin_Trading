"""The promotion bar, fixed in TRADING_OVERHAUL_PLAN.md Phase B BEFORE any
candidate strategy was tested. Do not loosen these thresholds after seeing a
result -- that is the exact re-tuning-until-it-passes failure mode the plan
exists to prevent. If a threshold genuinely needs revisiting, that is a
decision for the operator, made explicitly, not a silent code edit here.
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_NET_EXPECTANCY_PCT = 0.0     # OOS, at market (0.17%) cost
MIN_T_STAT = 2.0
MIN_EDGE_RATIO = 1.15
MIN_FOLDS_POSITIVE_OF = (2, 3)   # at least 2 of 3 folds net-positive
MIN_OOS_TRADES = 100


@dataclass
class PromotionResult:
    name: str
    passed: bool
    reasons: list[str]
    detail: dict


def evaluate(name: str, oos_report: dict, fold_reports: list[dict], baselines: dict) -> PromotionResult:
    reasons = []

    if oos_report.get("n", 0) < MIN_OOS_TRADES:
        reasons.append(f"only {oos_report.get('n', 0)} OOS trades (need >= {MIN_OOS_TRADES})")

    exp = oos_report.get("expectancy_pct", float("-inf"))
    if exp <= MIN_NET_EXPECTANCY_PCT:
        reasons.append(f"OOS net expectancy {exp:+.4f}% <= 0 at market cost")

    t = oos_report.get("t_stat", 0.0)
    if t < MIN_T_STAT:
        reasons.append(f"t-stat {t:.2f} < {MIN_T_STAT}")

    er = oos_report.get("edge_ratio", 0.0)
    if not (er == er) or er < MIN_EDGE_RATIO:  # nan check
        reasons.append(f"edge ratio {er:.3f} < {MIN_EDGE_RATIO}")

    need, of = MIN_FOLDS_POSITIVE_OF
    pos_folds = sum(1 for f in fold_reports if f.get("expectancy_pct", -1) > 0)
    if pos_folds < need:
        reasons.append(f"only {pos_folds}/{of} folds net-positive (need >= {need})")

    mirror_exp = baselines.get("mirror", {}).get("expectancy_pct", 0.0)
    if exp <= mirror_exp:
        reasons.append(f"does not beat mirror baseline ({exp:+.4f}% <= {mirror_exp:+.4f}%)")

    random_exp = baselines.get("random", {}).get("expectancy_pct", 0.0)
    if exp <= random_exp:
        reasons.append(f"does not beat random baseline ({exp:+.4f}% <= {random_exp:+.4f}%)")

    return PromotionResult(name=name, passed=(len(reasons) == 0), reasons=reasons, detail={
        "oos": oos_report, "folds": fold_reports, "baselines": baselines,
    })


def print_result(r: PromotionResult) -> None:
    verdict = "PASS" if r.passed else "FAIL"
    print(f"\n{'='*70}\n{r.name}: {verdict}\n{'='*70}")
    oos = r.detail["oos"]
    print(f"  OOS n={oos.get('n')} expectancy={oos.get('expectancy_pct', 0):+.4f}% "
          f"t={oos.get('t_stat', 0):.2f} edge_ratio={oos.get('edge_ratio', float('nan')):.3f} "
          f"win_rate={oos.get('win_rate_pct', 0):.1f}% profit_factor={oos.get('profit_factor', 0):.2f}")
    for i, f in enumerate(r.detail["folds"], 1):
        print(f"    fold {i}: n={f.get('n')} expectancy={f.get('expectancy_pct', 0):+.4f}%")
    b = r.detail["baselines"]
    print(f"  mirror:  expectancy={b.get('mirror', {}).get('expectancy_pct', 0):+.4f}%")
    print(f"  random:  expectancy={b.get('random', {}).get('expectancy_pct', 0):+.4f}%")
    if r.reasons:
        print("  reasons for FAIL:")
        for reason in r.reasons:
            print(f"    - {reason}")
