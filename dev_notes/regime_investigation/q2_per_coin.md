# Q2 Step 2.5 — Per-Coin Accuracy

Computed by `scripts/regime_accuracy_probe.py` over 12 top-volume symbols. 8 valid samples per symbol. Accuracy = (detector label == objective label) / sample count.

## Per-coin accuracy table

| Symbol | Samples | Detector accuracy | Ranging-labeled | Ranging correct | Per-coin false-ranging rate |
|---|---|---|---|---|---|
| ETHUSDT | 8 | 62% | 2 | 1 | 50% (1/2) |
| BNBUSDT | 8 | 38% | 5 | 3 | 40% (2/5) |
| ARBUSDT | 8 | 12% | 8 | 1 | 87.5% (7/8) |
| ATOMUSDT | 8 | 12% | 8 | 1 | 87.5% (7/8) |
| AVAXUSDT | 8 | 12% | 8 | 1 | 87.5% (7/8) |
| DOGEUSDT | 8 | 12% | 8 | 1 | 87.5% (7/8) |
| LINKUSDT | 8 | 12% | 8 | 1 | 87.5% (7/8) |
| SOLUSDT | 8 | 12% | 8 | 1 | 87.5% (7/8) |
| ADAUSDT | 8 | 0% | 8 | 0 | 100% (8/8) |
| BTCUSDT | 8 | 0% | 6 | 0 | 100% (6/6) |
| NEARUSDT | 8 | 0% | 8 | 0 | 100% (8/8) |
| XRPUSDT | 8 | 0% | 8 | 0 | 100% (8/8) |

## Observations

- **Best symbol: ETHUSDT (62% accuracy)**. This is partly because ETHUSDT was the only symbol in the selection that frequently received the `dead` label (which I bucket as `other`). ETH spent the test window in low-volume, range-bound chop on H1 that the detector caught with the `dead` branch. Its "other" detector label happened to correlate with "other" objective regime more often.
- **Second best: BNBUSDT (38%)**. Mix of `ranging` and `dead` labels with some matching objective `ranging` windows.
- **Worst: BTCUSDT, ADAUSDT, NEARUSDT, XRPUSDT (0%)**. Despite different price behaviors, all four classify as `ranging` in 100% of their sampled emissions and never match the objective regime.
- **Most symbols cluster at 12% accuracy** (1 correct out of 8 samples). This is consistent with random matching given the imbalanced sample distribution.

## Interpretation

The per-coin accuracy variance is dominated by **what fraction of a coin's sampled emissions hit the `dead` branch vs the `ELSE = RANGING` fallback**. Coins that occasionally classify as `dead` (ETHUSDT, BNBUSDT) get partial credit when actual price action is mixed. Coins that universally classify as `ranging` (BTCUSDT, ADAUSDT, NEARUSDT, XRPUSDT) get zero credit because their price action is rarely strictly ranging on 30-min windows.

This is the per-coin manifestation of the system-wide ELSE-fallback issue: a coin whose H1 indicators put it in the ADX [20, 25) transition band gets the fallback label regardless of what its 5-min price action is doing.

## Sample-size caveat

8 samples per symbol is small. The 0% vs 12% vs 38% vs 62% breakpoints are not finely-resolved. The qualitative pattern is robust (most symbols are very low accuracy; a few outliers are better), but the precise per-symbol ranking would benefit from a larger sample.
