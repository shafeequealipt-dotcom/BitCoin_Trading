# Phase 0 — Issue 3 Investigation: CALL_A Latency

## Confirmed Hypotheses

**Hypothesis A** — Prompt growth from cumulative architectural fixes is real. Top-N raised 6→10 plus full-layer block enabled has grown per-cycle prompt size from ~10K to ~15K chars (max observed).

**Hypothesis E** — Specific data fields are unnecessarily verbose. Per-coin section dominates; abbreviation tables for state labels, setup types, and strategy names are the highest-value compression targets.

The agent's earlier estimate of "32-40K char CALL_A" was overstated — actual measurement shows max 15,259 chars in the live dumps.

## Evidence

### Stage2 dump prompt sizes (top 8 by size)

Sampled from `data/stage2_dumps/`:

| Prompt chars | Latency ms | Filename |
|--------------|------------|----------|
| 15,259 | 120,759 | 20260506T151506_call0020 |
| 15,244 | 147,696 | 20260507T080831_call0011 |
| 15,082 | 110,858 | 20260506T145844_call0016 |
| 15,021 | 112,226 | 20260506T150644_call0018 |
| 14,962 | 79,561 | 20260507T081523_call0013 |
| 14,849 | 82,751 | 20260506T152221_call0022 |
| 14,666 | 87,553 | 20260507T112002_call0011 |
| 14,640 | 115,325 | 20260505T220433_call0013 |

**CALL_A range: ~14,600 — 15,300 chars at full-layer + top-10 mode.** Latency loosely correlates with size (87s @ 14.7K vs 148s @ 15.2K) but other factors (Claude reasoning depth, network) dominate.

### CLAUDE_CALL_OK distribution from 110-min window (26 calls)

| Metric | Value |
|--------|-------|
| count | 26 |
| median | 92.7s |
| p95 | 161.0s |
| peak | 162.9s |

The doc's "median 121s" appears to have been computed over a slightly different sub-window. Median 92.7s for the full 110 min still shows the climbing trend — 30% of calls above 121s.

### Static prompt blocks

- `TRADE_SYSTEM_PROMPT` at `src/brain/strategist.py:65-141` ≈ 6,600 chars
- `POSITION_SYSTEM_PROMPT` at `src/brain/strategist.py:162-178` ≈ 1,900 chars
- A sampled CALL_B prompt at 12:18 measured `system_prompt_chars=1783, prompt_chars=3324` — CALL_B is already compact.

### Approximate breakdown of 15K CALL_A prompt

- System prompt (TRADE_SYSTEM_PROMPT): ~6.6K
- Per-coin × 10 packages: ~6-8K (full-layer mode enabled)
- Market data + regime + capital + contract: ~1-2K

So per-coin section is ~600-800 chars per coin (not 2500 as earlier estimated). Compression target shifts:

### Highest-value compression targets

1. **Per-coin abbreviations** (~150-200 chars/coin × 10 = 1.5-2K saved)
   - State labels: BREAKOUT/CONFLUENCE/OVERBOUGHT → BR/CF/OB
   - Setup types: BEARISH_FVG_OB_COUNTER → B_FVG_C
   - Strategy names: RSI_MEAN_REVERSION → RSI_MR

2. **Qualification reasons as tags** (~100-150 chars/coin × 10 = 1-1.5K saved)
   - Replace English sentences with short tag identifiers

3. **Float precision rounding** (~30-50 chars/coin × 10 = 300-500 saved)
   - 2 decimals → 1 decimal on non-critical floats

4. **Compact volume notation** (~10-20 chars/coin × 10 = 100-200 saved)
   - $2,451,230,000 → $2.45B

5. **Cache-friendly system prompt structure** (potentially 6.6K cached after first call)
   - Add `--exclude-dynamic-system-prompt-sections` flag at `src/brain/claude_code_client.py:993`

**Realistic compression total: 3-4K chars (~20-25% of CALL_A prompt). Plus cache benefit on TRADE_SYSTEM_PROMPT.**

### Claude CLI capability check

`claude --help` confirms `--exclude-dynamic-system-prompt-sections` flag exists:
> Move per-machine sections (cwd, env info, memory paths, git status) from the system prompt into the first user message. Improves cross-user prompt-cache reuse.

Current invocation at `src/brain/claude_code_client.py:993`:
```
cmd = [self._claude_path, "-p", "--output-format", "text"]
```
Does NOT include the cache flag. Adding it is sub-phase 2D.

### Recent commits affecting prompt size

`git log --oneline -- src/brain/strategist.py` last entries:
- `9c2235f` — CALL_B Phase 1E XRAY flip metadata (+~200 chars)
- `50b5356` — CALL_B contract added (+~1K chars)
- `e00c5d5` — Drop original-thesis line (-~80 chars)
- `f62683c` — Drop regime-reversal close rules (-~150 chars)
- `0d38f54` — Stage 2 phase 1: top-10 + 2-4 contract (+~3-4K cumulative — top-N raise from 6 to 10 = +4 coins × ~700 chars/coin)

Aggressive-framing rewrites (2026-05-05) reduced static text by ~2K. Top-10 raise added ~3K. Net growth ~1-2K from 2026-05-03 baseline.

## Confirmed Fix Shape — Compression + Caching

Per operator's choice in plan-mode dialogue: combine compression with prompt-cache reuse.

| Sub-phase | Target | Estimated saving |
|-----------|--------|-----------------|
| 2A — Abbreviations | state labels, setup types, strategies | 1.5-2K |
| 2B — Qualification reasons as tags | per-coin reasons | 1-1.5K |
| 2C — Compact volume | per-coin volumes | 100-200 |
| 2D — Cache flag | TRADE_SYSTEM_PROMPT | 6.6K cached after warm-up |

Total compression: ~3-4K reduction in CALL_A prompt size. Cache gives additional latency benefit on warm calls.

Target: median latency below 80s, peak below 130s.
