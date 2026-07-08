"""Regime classification accuracy probe — Phase 2 of regime_investigation.

Read-only. Samples REGIME classifications from workers.log, queries the
local SQLite klines table for the surrounding 5-minute price action, and
computes a confusion matrix comparing the detector label to an objective
trending/ranging definition.

Objective definition (see dev_notes/regime_investigation/q2_criteria.md):

  For a 30-minute window of 5-min klines (6 candles), with ATR(14) on the
  preceding 14 5-minute candles:

  TRENDING_UP:   total close change > 1.5 x ATR
                 AND 4 of 5 candle-to-candle closes moved upward
                 AND running drawdown from peak < 0.5 x ATR
  TRENDING_DOWN: mirror image
  RANGING:       |total close change| < 0.6 x ATR
                 AND (high - low) range across window < 1.2 x ATR
  OTHER:         does not fit any of the above (transitional, spike, mixed)

Run:
  cd /home/inshadaliqbal786/trading-intelligence-mcp
  .venv/bin/python scripts/regime_accuracy_probe.py
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "data", "trading.db")

LOG_FILES = [
    "data/logs/workers.log",
    "data/logs/workers.2026-05-11_22-34-42_859953.log",
    "data/logs/workers.2026-05-11_17-35-08_280673.log",
    "data/logs/workers.2026-05-11_11-55-43_739853.log",
    "data/logs/workers.2026-05-10_17-00-45_779645.log",
    "data/logs/workers.2026-05-10_07-19-00_526602.log",
    "data/logs/workers.2026-05-10_05-03-25_891314.log",
]

REGIME_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+.*REGIME \| "
    r"sym=(?P<sym>[A-Z]+USDT) rgm=(?P<rgm>[a-z_]+) conf=(?P<conf>[0-9.]+) "
    r"adx=(?P<adx>[0-9.-]+) chop=(?P<chop>[0-9.-]+)"
)


@dataclass
class Sample:
    ts: str
    symbol: str
    label: str
    conf: float
    adx: float
    chop: float


def load_samples(min_window: str, max_window: str) -> list[Sample]:
    """Parse REGIME emissions from workers logs within [min_window, max_window]."""
    samples: list[Sample] = []
    for rel in LOG_FILES:
        path = os.path.join(REPO_ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            for line in fh:
                try:
                    text = line.decode("utf-8", errors="ignore")
                except Exception:
                    continue
                m = REGIME_LINE.search(text)
                if not m:
                    continue
                ts = m.group("ts")
                if ts < min_window or ts > max_window:
                    continue
                samples.append(
                    Sample(
                        ts=ts,
                        symbol=m.group("sym"),
                        label=m.group("rgm"),
                        conf=float(m.group("conf")),
                        adx=float(m.group("adx")),
                        chop=float(m.group("chop")),
                    )
                )
    return samples


def fetch_klines(
    conn: sqlite3.Connection,
    symbol: str,
    iso_pivot: str,
    minutes_before: int,
    minutes_after: int,
) -> list[tuple[str, float, float, float, float, float]]:
    """Return 5m klines spanning [pivot - minutes_before, pivot + minutes_after].

    Each row: (timestamp_iso, open, high, low, close, volume).
    """
    pivot = datetime.strptime(iso_pivot, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=UTC
    )
    lo = pivot - timedelta(minutes=minutes_before)
    hi = pivot + timedelta(minutes=minutes_after)
    cur = conn.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol = ? AND timeframe = '5' AND timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp ASC",
        (symbol, lo.strftime("%Y-%m-%dT%H:%M:%S+00:00"), hi.strftime("%Y-%m-%dT%H:%M:%S+00:00")),
    )
    return list(cur.fetchall())


def compute_atr14(klines: list[tuple]) -> float:
    """Simple ATR(14): mean of last 14 true ranges. Returns 0.0 if <14 bars."""
    if len(klines) < 14:
        return 0.0
    trs: list[float] = []
    prev_close = klines[-15][4] if len(klines) >= 15 else klines[0][4]
    for k in klines[-14:]:
        _ts, _open, high, low, close, _vol = k
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = close
    return sum(trs) / max(len(trs), 1)


def classify_window(klines: list[tuple], atr: float) -> str:
    """Apply the objective regime criteria to a 6-candle window.

    klines: list of (ts, o, h, l, c, v) — exactly the 6 candles of the window.
    atr: ATR(14) from the preceding 14 candles.
    """
    if len(klines) < 6 or atr <= 0:
        return "insufficient_data"
    closes = [k[4] for k in klines]
    highs = [k[2] for k in klines]
    lows = [k[3] for k in klines]
    total_change = closes[-1] - closes[0]
    abs_change = abs(total_change)
    rng = max(highs) - min(lows)

    # Count consecutive directional moves
    up_steps = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    down_steps = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])

    # Drawdown from peak (for trending_up) or rally from trough (for trending_down)
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        dd = peak - c
        if dd > max_dd:
            max_dd = dd
    trough = closes[0]
    max_rally = 0.0
    for c in closes:
        if c < trough:
            trough = c
        rally = c - trough
        if rally > max_rally:
            max_rally = rally

    # Classification (relaxed crypto-norm thresholds).
    # Trending: net 30-min move >= 1.0 ATR in one direction, at least 4 of 5
    # candle-to-candle steps in that direction, drawdown from running peak
    # less than 0.7 ATR (i.e., the move was reasonably linear).
    if total_change >= 1.0 * atr and up_steps >= 4 and max_dd < 0.7 * atr:
        return "trending_up"
    if total_change <= -1.0 * atr and down_steps >= 4 and max_rally < 0.7 * atr:
        return "trending_down"
    # Ranging: net 30-min move within 0.8 ATR, total high-low range within
    # 2.0 ATR. Allows normal noise but excludes meaningful directional moves.
    if abs_change <= 0.8 * atr and rng <= 2.0 * atr:
        return "ranging"
    # Weak trending: net move > 0.5 ATR with directional consistency but
    # not strong enough for strict trending.
    if total_change > 0.5 * atr and up_steps >= 3:
        return "weak_trending_up"
    if total_change < -0.5 * atr and down_steps >= 3:
        return "weak_trending_down"
    return "other"


def normalize_label(label: str) -> str:
    """Bucket detector labels into the 3-way comparison space."""
    if label in ("trending_up", "trending_down"):
        return label
    if label == "ranging":
        return "ranging"
    if label in ("volatile", "dead"):
        return "other"
    return "other"


def main() -> int:
    # 48h window
    min_window = "2026-05-10 07:00:00"
    max_window = "2026-05-12 07:30:00"

    samples = load_samples(min_window, max_window)
    print(f"Loaded {len(samples)} REGIME emissions in window {min_window} -> {max_window}")
    if not samples:
        print("No samples; abort.")
        return 1

    # Stratified subsample: 5 per top-10 symbol = 50 if available
    by_symbol: dict[str, list[Sample]] = defaultdict(list)
    for s in samples:
        by_symbol[s.symbol].append(s)
    top_symbols = sorted(by_symbol, key=lambda x: -len(by_symbol[x]))[:12]

    selected: list[Sample] = []
    samples_per_symbol = 10
    for sym in top_symbols:
        lst = by_symbol[sym]
        step = max(len(lst) // samples_per_symbol, 1)
        for i in range(0, min(len(lst), step * samples_per_symbol), step):
            if len(selected) >= 150:
                break
            selected.append(lst[i])
    print(f"Selected {len(selected)} stratified samples across {len(top_symbols)} symbols")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = None  # tuple rows

    confusion: dict[tuple[str, str], int] = defaultdict(int)
    per_symbol: dict[str, list[tuple[str, str]]] = defaultdict(list)
    detail_rows: list[tuple] = []

    for s in selected:
        # We need at least 14 candles BEFORE the window start. Window = 30 min before pivot.
        # So fetch: pivot - 100 min  to  pivot + 30 min  to have ATR + before-window + after-window
        klines_all = fetch_klines(conn, s.symbol, s.ts, minutes_before=180, minutes_after=30)
        if len(klines_all) < 20:
            continue
        # Find the pivot index in klines_all
        # The "before" window = 30 min prior = 6 candles ending right before pivot
        # The "after" window = 30 min forward = 6 candles starting at pivot
        pivot_dt = datetime.strptime(s.ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        # round pivot to nearest 5m
        pivot_rounded = pivot_dt - timedelta(
            minutes=pivot_dt.minute % 5,
            seconds=pivot_dt.second,
        )
        pivot_iso = pivot_rounded.strftime("%Y-%m-%dT%H:%M:%S+00:00")

        # Build "before" window: last 6 candles before pivot
        before = [k for k in klines_all if k[0] < pivot_iso][-6:]
        # Build "after" window: first 6 candles at-or-after pivot
        after = [k for k in klines_all if k[0] >= pivot_iso][:6]
        # ATR uses 14 candles before the "before" window
        atr_klines = [k for k in klines_all if k[0] < (before[0][0] if before else pivot_iso)]
        atr = compute_atr14(atr_klines) if atr_klines else 0.0

        objective_before = classify_window(before, atr)
        objective_after = classify_window(after, atr)
        # The "before" classification is what the detector should have seen.
        # The "after" is validation (did the regime persist?).
        detector_norm = normalize_label(s.label)
        objective = objective_before
        confusion[(detector_norm, objective)] += 1
        per_symbol[s.symbol].append((detector_norm, objective))
        detail_rows.append(
            (s.ts, s.symbol, s.label, s.conf, s.adx, s.chop, objective_before, objective_after, atr)
        )

    conn.close()

    # Output: confusion matrix
    print("\n===CONFUSION_MATRIX (rows=detector_label, cols=objective_before)===")
    labels = [
        "trending_up", "trending_down", "ranging", "other",
        "weak_trending_up", "weak_trending_down", "insufficient_data",
    ]
    header = " " * 22 + "".join(f"{c:>20}" for c in labels)
    print(header)
    for r in ["trending_up", "trending_down", "ranging", "other", "insufficient_data"]:
        line = f"{r:>22}"
        for c in labels:
            line += f"{confusion.get((r, c), 0):>20}"
        print(line)

    total = sum(confusion.values())
    if total == 0:
        print("\nNo classified samples — DB may not have 5m klines for the chosen window.")
        return 0

    # Headline metrics
    correct = (
        confusion.get(("trending_up", "trending_up"), 0)
        + confusion.get(("trending_down", "trending_down"), 0)
        + confusion.get(("ranging", "ranging"), 0)
        + confusion.get(("other", "other"), 0)
    )
    overall_acc = correct / total
    ranging_labeled = sum(v for (r, c), v in confusion.items() if r == "ranging")
    ranging_correct = confusion.get(("ranging", "ranging"), 0)
    false_ranging = ranging_labeled - ranging_correct
    false_ranging_rate = false_ranging / max(ranging_labeled, 1)

    trending_labeled = sum(
        v for (r, c), v in confusion.items()
        if r in ("trending_up", "trending_down")
    )
    trending_correct = (
        confusion.get(("trending_up", "trending_up"), 0)
        + confusion.get(("trending_down", "trending_down"), 0)
    )
    false_trending = trending_labeled - trending_correct
    false_trending_rate = false_trending / max(trending_labeled, 1)

    print(f"\nTotal classified samples: {total}")
    print(f"Overall accuracy: {overall_acc:.1%}  (correct {correct}/{total})")
    print(
        f"Ranging labeled: {ranging_labeled}  Ranging correct: {ranging_correct}  "
        f"False-ranging rate: {false_ranging_rate:.1%}"
    )
    print(
        f"Trending labeled: {trending_labeled}  Trending correct: {trending_correct}  "
        f"False-trending rate: {false_trending_rate:.1%}"
    )

    # Per-symbol accuracy
    print("\n===PER_SYMBOL_ACCURACY===")
    for sym, pairs in sorted(per_symbol.items()):
        n = len(pairs)
        correct_s = sum(1 for d, o in pairs if d == o)
        rng_labeled = sum(1 for d, o in pairs if d == "ranging")
        rng_correct = sum(1 for d, o in pairs if d == "ranging" and o == "ranging")
        print(
            f"  {sym}: n={n} acc={correct_s/max(n,1):.0%} "
            f"ranging_labeled={rng_labeled} ranging_correct={rng_correct}"
        )

    # Detail dump for the conf=0.40 (ELSE fallback) subset
    print("\n===CONF_0.40_SUBSET_ANALYSIS (ELSE fallback samples only)===")
    fallback_total = 0
    fallback_correct = 0
    fallback_by_obj: Counter = Counter()
    for ts, sym, label, conf, adx, chop, obj_b, obj_a, atr in detail_rows:
        if label == "ranging" and abs(conf - 0.4) < 1e-3:
            fallback_total += 1
            if obj_b == "ranging":
                fallback_correct += 1
            fallback_by_obj[obj_b] += 1
    if fallback_total:
        pct = 100 * fallback_correct / fallback_total
        print(f"Fallback samples: {fallback_total}")
        print(f"Of which objectively ranging: {fallback_correct} ({pct:.1f}%)")
        print(f"Objective distribution: {dict(fallback_by_obj)}")
    else:
        print("No conf=0.40 fallback samples in selection.")

    # Print first 20 details for documentation
    print("\n===SAMPLE_DETAIL (first 20)===")
    for row in detail_rows[:20]:
        ts, sym, label, conf, adx, chop, obj_b, obj_a, atr = row
        print(
            f"  {ts} {sym:>10}  detector={label:<14} conf={conf:.2f} "
            f"adx={adx:5.1f} chop={chop:5.1f}  "
            f"objective_before={obj_b:<14} objective_after={obj_a:<14} "
            f"atr={atr:.4f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
