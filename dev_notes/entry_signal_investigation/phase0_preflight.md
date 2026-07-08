# Phase 0 — Pre-flight

## Environment

- Working directory: `/home/inshadaliqbal786/trading-intelligence-mcp`
- Current branch: `main`
- Last commit: `2a035e2 c1(notes): full pipeline E2E verification report`
- Uncommitted changes at start of investigation: two runtime artifacts only — `data/layer_state.json`, `data/logs/layer1c_full.jsonl`. No source code is dirty. No git mutation will be performed by this task.

## Working Directory For This Task

- `dev_notes/entry_signal_investigation/` exists (created at task start). All phase outputs go here.

## Database

- Path: `data/trading.db` (269 MB), WAL-journaled.
- Access mode for this task: read-only (`sqlite3 -readonly`).
- 70 user tables.

### Tables Relevant To The Entry Pipeline

| Table | Rows | Notes |
|---|---|---|
| `trade_intelligence` | 2,345 | Rich per-trade entry decision context (ensemble_votes JSON, claude_signal, claude_confidence, regime, entry_regime, entry_score, apex_* columns, fear_greed, indicators at close). Primary verification table. |
| `trade_history` | 1,104 | Bybit-demo closed-trade ledger with PnL, strategy, signal_confidence. |
| `trade_log` | 2,787 | Trade lifecycle: opened_at / closed_at / thesis / close_reason / hold_minutes. |
| `signals` | 123,210 | Raw SIG_CLASSIFY outputs (signal_type, confidence, components JSON). |
| `claude_decisions` | 2,888 | Strategist CALL_A records with full_response. |
| `coin_regime_history` | 18,081 | Per-coin regime (adx, choppiness, confidence). |
| `regime_history` | (not queried yet) | Global regime history. |
| `ensemble_votes` | **0** | Defined table exists but is **empty** — never populated. Per-trade strategy votes must be reconstructed from logs or read from `trade_intelligence.ensemble_votes` JSON column. |
| `brain_decisions` | **0** | Defined but empty — `claude_decisions` is the active path. |

### Date Coverage In The Investigation Window

- `trade_intelligence` ranges `2026-04-06 → 2026-05-21 12:40`. Total rows in the 2026-05-20 05:46 → 2026-05-21 12:40 analysis window: **225**.
- `trade_history` ranges `2026-05-09 → 2026-05-21 12:40` (1,104 rows total).
- `trade_log` ends `2026-05-21 12:40` (2,787 rows total).
- `exchange_mode` distribution in `trade_intelligence`: shadow=1,070, bybit_demo=1,275.

### Schema Note: `trade_intelligence.ensemble_votes`

Defined as `TEXT` — i.e., a JSON-encoded blob, not a normalized child table. Per-trade per-strategy votes are recoverable only by parsing this column or by re-parsing the `STRAT_VOTE_TRACE` log lines. This is the data shape Phase 3 Claim 3 (herding) and Phase 3 Claim 5 (per-strategy attribution) will operate on.

## Source Code Files Located

| Path | Status |
|---|---|
| `src/workers/structure_worker.py` | found |
| `src/workers/signal_worker.py` | found |
| `src/workers/strategy_worker.py` | found |
| `src/workers/scanner_worker.py` | found |
| `src/workers/regime_worker.py` | found |
| `src/workers/scanner/state_labeler.py` | found (note: under `workers/scanner/`, not the path mentioned in the protocol prose) |
| `src/intelligence/signals/signal_generator.py` | found |
| `src/strategies/ensemble.py` | found |
| `src/strategies/regime.py` | found |
| `src/brain/strategist.py` | found |
| `src/apex/` (assembler, gate, models, optimizer, prompts, qwen_client) | found |
| `config.toml` | found |

Discrepancy noted vs the protocol prose: the protocol referred to `src/labellers/state_labeler.py`; the actual path is `src/workers/scanner/state_labeler.py`.

## Log Files Covering The Window

The 2026-05-20 05:46 → 2026-05-21 12:40 analysis window is fully covered by:

- `data/logs/workers.2026-05-20_05-46-00_485723.log` → `…_11-31-15_350654.log` → `…_13-28-17_344916.log` → `…_15-29-03_040036.log` → `…_17-16-00_187946.log` → `…_19-11-00_094547.log` (May 20 chain).
- `data/logs/workers.2026-05-21_06-48-15_278212.log` → `…_08-38-47_431089.log` → `…_10-26-33_217439.log` (May 21 chain).
- `data/logs/workers.log` (active tail, 560 KB at preflight time).
- `data/logs/brain.log` (1.9 MB, ends 12:40 May 21).
- `data/logs/general.log` (3.2 MB, active).
- `data/logs/mcp.log` (1.8 MB).

Combined pre-extracted snapshot already exists from prior analysis at `/home/inshadaliqbal786/ALL_LOGS_2026-05-21_04-50_to_12-40.log` (35 MB). Phase 3 will use the live database as the source of truth and use logs only where DB data is missing.

## Access Issues Encountered

None. All tables read successfully in read-only mode. No protected-table reads were forced.

## Phase 0 Status

Complete. Proceeding to Phase 1.
