# I3 — APEX TradeOptimizer

Forensic refresh: 2026-05-02 (replaces 2026-04-28 baseline).

## 1. File path & size

`src/apex/optimizer.py` — 743 lines (`wc -l`).

`src/apex/qwen_client.py` — 248 lines (the API client used by the optimizer).

## 2. Public methods

`class TradeOptimizer` (`src/apex/optimizer.py:36`).

| Method | Signature | File:line | Purpose |
|--------|-----------|-----------|---------|
| `__init__` | `(qwen_client, assembler, settings)` | `:49` | Stores collaborators; initializes counters (`_optimized_count`, `_fallback_count`, `_flip_count`, `_lock_override_count`, `_total_time_ms`). |
| `optimize` | `async (directive, plan=None) -> OptimizedTrade` | `:61` | Main pipeline (10-step flow per docstring `:7-17`). |
| `get_stats` | `() -> dict` | `:629` | Returns cumulative `{optimized, fallbacks, flips, flip_rate, lock_overrides, avg_time_ms, qwen_stats}`. |

Private helpers: `_parse_response` (`:369`), `_apply_constraints` (`:462`), `_fallback` (`:535`), `_log_optimization` (`:585`), `_check_flip_evidence` (`:650`), `_check_direction_lock` (`:665`), `_enforce_flip_confidence` (`:713`).

## 3. Qwen / DeepSeek API integration

The truth doc (`dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md:571`) specifies "Qwen 3.6 via OpenRouter" in some legacy notes, but the live code calls **DeepSeek v3.2 via OpenRouter**. Direct evidence:

- Config default: `model: str = "deepseek/deepseek-v3.2"` (`src/config/settings.py:1394`).
- Live config: `model = "deepseek/deepseek-v3.2"` (`config.toml:957`).
- Cost constants encode DeepSeek pricing: `_DS_COST_PER_M_INPUT = 0.30`, `_DS_COST_PER_M_OUTPUT = 0.88` (`src/apex/qwen_client.py:32-33`, comment "DeepSeek V3.2 pricing via OpenRouter (per-million tokens)").
- The class `QwenClient` is named for legacy reasons; its docstring at `qwen_client.py:1-3` explicitly says "DeepSeek client for APEX — calls OpenRouter".

Live log evidence (snapshot row 821 written 2026-05-02 06:29 UTC): `apex_model = "deepseek/deepseek-v3.2-20251201"`.

### Where the API call is made

`src/apex/qwen_client.py:138-143`:
```python
async with session.post(
    self._api_url,
    json=payload,
    timeout=timeout,
) as resp:
```

Default URL: `"https://openrouter.ai/api/v1/chat/completions"` (`qwen_client.py:64`).
Configured URL: `api_url = "https://openrouter.ai/api/v1/chat/completions"` (`config/settings.py:1393`).

### Auth

`qwen_client.py:81-88` — Bearer token + OpenRouter attribution headers, set on the persistent `aiohttp.ClientSession`:
```python
self._session = aiohttp.ClientSession(
    headers={
        "Authorization": f"Bearer {self._api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": self._http_referer,
        "X-Title": self._x_title,
    }
)
```
API key resolution: `_build_apex` at `src/config/settings.py:2856-2861` — `APEX_API_KEY` env var takes precedence over shared `OPENROUTER_API_KEY`.

### Retry policy

NONE. `qwen_client.py:38-44` (class docstring of `APEXOptimizationError`):
> Unlike TIASAnalysisError, there is no retryable flag. APEX operates in the live trade execution path — if DeepSeek fails for any reason the caller immediately falls back to Claude's original parameters. APEX failure NEVER blocks a trade.

`max_retries` is NOT a field on `APEXSettings` (verified by reading `config/settings.py:1389-1446`). Compare TIAS: `TIASSettings.max_retries: int = 1` (`config/settings.py:1382`).

### Current timeout

- Code default (kwarg): `timeout_seconds: int = 30` (`qwen_client.py:98`).
- Settings default: `timeout_seconds: int = 60` (`config/settings.py:1396`).
- Live config: `timeout_seconds = 60` (`config.toml:960`) with comment `"Layer 3: was 30s; DeepSeek frequently responds at 30-32s causing APEX_TIMEOUT."`.
- Pass-through site: `optimizer.py:229` — `timeout_seconds=self._settings.timeout_seconds` so the configured 60s wins.

VERIFIED: timeout was raised from 30s to 60s and is currently 60s in production config.

## 4. What DeepSeek optimizes

System prompt section "WHAT YOU OPTIMIZE" (`src/apex/prompts.py:49-55`):

```
1. DIRECTION: Same as the trader OR flipped if TIAS overwhelmingly shows the opposite wins.
2. STOP LOSS: ATR-proportional. Tight enough to limit damage, wide enough to survive noise.
3. TAKE PROFIT: CRITICAL RULE — NEVER set TP below the trader's original TP. The trader set that target based on analysis. Match or EXCEED it. Regime-adjust upward, never downward.
4. POSITION SIZE: Scale by TIAS profit factor. High profit factor (>2.0) coins get MORE capital. Low profit factor (<1.0) coins get LESS.
5. EXIT STRATEGY: Prefer "fixed" mode (fixed TP target). Use "trail_only" ONLY when TIAS shows >70% win rate AND avg capture >1.5% for this coin with trailing exits. Otherwise use "fixed".
6. ADD-ON: Recommend adding to position on pullback ONLY when TIAS shows the coin trends after pullbacks.
```

Required output JSON (`prompts.py:210-224`): `direction, sl_pct, tp_pct, tp_mode, position_size_usd, leverage, entry_timing, add_on_pullback, add_trigger_pct, add_size_pct, reasoning, confidence`.

Decision tree mapping these to `OptimizedTrade` parsing happens in `_parse_response` (`optimizer.py:369-460`).

## 5. Direction flip discipline

### Pre-call code-level direction lock (`optimizer.py:665-711`)

Verbatim logic:
- `trending_down` → natural direction `Sell`; `trending_up` → natural direction `Buy` (`:685-688`).
- For trending regimes: ALWAYS lock (`:691-699`):
  ```python
  if natural_dir:
      if claude_direction == natural_dir:
          return True, f"{regime} aligns with {claude_direction}"
      else:
          return (True, f"Claude chose {claude_direction} against {regime} (per-coin override)")
  ```
- For `volatile`: lock unless `_check_flip_evidence` returns True (≥70% WR with ≥8 opposite-direction trades, `:650-663`).
- For `ranging`/`dead`/`unknown`: NO pre-call lock (`:709-711`).

### Post-parse confidence-gated discipline (`optimizer.py:713-743`)

```python
def _enforce_flip_confidence(self, optimized, claude_direction, regime):
    if regime in ("trending_up", "trending_down", "volatile"):
        return False, ""  # Already governed by pre-call lock
    if optimized.direction == claude_direction:
        return False, ""  # No flip happened
    threshold = float(getattr(self._settings, "apex_min_flip_confidence", 0.90))
    conf = float(getattr(optimized, "confidence", 0.0) or 0.0)
    if conf < threshold:
        return True, (f"flip {claude_direction}→{optimized.direction} "
                      f"in regime={regime} blocked: conf={conf:.2f}<{threshold:.2f}")
    return False, ""
```

If reverted, `optimizer.py:266-275` resets direction, sets `was_flipped=False`, prepends `[FLIP BLOCKED conf<min]` to reasoning, increments `_lock_override_count`.

### Authorized-flip-blocks-resize (`optimizer.py:276-290`)

When `apex_block_flip_resize` setting is True (default; `config/settings.py:1446`):
```python
elif optimized.was_flipped and getattr(self._settings, "apex_block_flip_resize", True):
    _orig_size = float(getattr(optimized, "original_size", 0.0) or 0.0)
    if _orig_size > 0 and abs(optimized.position_size_usd - _orig_size) > 0.01:
        log.warning(f"APEX_FLIP_RESIZE_BLOCKED | sym={symbol} flip=...")
        optimized.position_size_usd = _orig_size
```

### Per Issue 9: "no rolling FLIP-rate check"

VERIFIED. `flip_rate` is exposed only as a cumulative health stat at `optimizer.py:640`:
```python
"flip_rate": self._flip_count / max(self._optimized_count, 1),
```
No time-windowed throttle exists — searched: `grep -n "flip_rate\|rolling\|flip_count" src/apex/optimizer.py src/apex/gate.py`. Disciplines that DO exist are per-trade only:
- `_check_direction_lock` (regime-based, `optimizer.py:665`).
- `_enforce_flip_confidence` (per-trade confidence ≥0.90, `optimizer.py:713`).
- `apex_block_flip_resize` (per-trade size revert, `optimizer.py:276`).

There is NO check that says "if recent flip rate is X%, block further flips". Issue 9's observation stands.

### 5 examples of APEX_FLIP with full context (24-hour window)

From log union `workers.log + workers.2026-05-02_04-31-00_392071.log + workers.2026-05-01_00-01-33_829054.log` filtered to `ts >= 2026-05-01 11:48`:

1. `2026-05-02 03:00:16.700 | WARNING | APEX_FLIP | sym=RENDERUSDT claude=Buy apex=Sell sl=0.3% tp=0.5% cls=low sz=$1000→$1000 mode=fixed conf=100% regime=ranging ms=4119 | did=d-1777690683074`
2. `2026-05-02 03:59:14.939 | WARNING | APEX_FLIP | sym=RENDERUSDT claude=Buy apex=Sell sl=0.8% tp=1.4% cls=medium sz=$1200→$1200 mode=fixed conf=100% regime=ranging ms=8204 | did=d-1777694209734`
3. `2026-05-02 04:16:10.859 | WARNING | APEX_FLIP | sym=RENDERUSDT claude=Buy apex=Sell sl=0.8% tp=1.4% cls=medium sz=$600→$600 mode=fixed conf=100% regime=ranging ms=2311 | did=d-1777695235927`
4. `2026-05-02 04:57:18.801 | WARNING | APEX_FLIP | sym=RENDERUSDT claude=Buy apex=Sell sl=0.3% tp=0.5% cls=low sz=$500→$500 mode=fixed conf=100% regime=ranging ms=2267 | did=d-1777697693903`
5. `2026-05-02 06:19:24.907 | WARNING | APEX_FLIP | sym=NEARUSDT claude=Sell apex=Buy sl=0.3% tp=0.5% cls=low sz=$500→$500 mode=fixed conf=95% regime=ranging ms=9031 | did=d-1777702618197`

(One additional flip: `2026-05-02 06:26:33.827 NEARUSDT claude=Sell apex=Buy ... conf=95% regime=ranging`.)

Observation: ALL six flips happened in `regime=ranging` (the unlocked regime). FOUR were `RENDERUSDT Buy→Sell` at conf=100%; TWO were `NEARUSDT Sell→Buy` at conf=95%. Both NEARUSDT flips triggered `APEX_FLIP_RESIZE_BLOCKED` (forced size $1200→$500 to original).

Two `APEX_FLIP_BLOCKED` events in the same window (confidence below 0.90):
- `2026-05-02 04:48:52.346 SANDUSDT Sell→Buy regime=ranging conf=0.75<0.90`
- `2026-05-02 05:25:03.906 HYPERUSDT Buy→Sell regime=ranging conf=0.85<0.90`

## 6. Size / SL / TP modification: bounds & limits

`_apply_constraints` (`optimizer.py:462-533`) — applied AFTER DeepSeek response parsed:

| Field | Floor | Ceiling | Source |
|-------|-------|---------|--------|
| `position_size_usd` | `100.0` | `self._settings.max_position_size_usd` (1200, `config.toml:963`) | `:480-482` |
| `leverage` | `1` | `self._settings.max_leverage` (5, `config.toml:964`) | `:485` |
| `sl_pct` | `max(0.2, recommended_sl_pct × 0.6)` (per-class) | `5.0` | `:492-497` |
| `tp_pct` | `max(min_tp_pct=0.3, recommended_tp_pct × 0.6)` (per-class) | `8.0` (then `APEX_TP_CAP` per-class on top) | `:502-510` |
| `confidence` | `0.0` | `1.0` | `:513` |

Volatility-class TP cap (`optimizer.py:200-216,302-309`):
- Map `tp_cap_multiplier_by_class` (default `{"dead": 1.2, "low": 1.3, "medium": 1.3, "high": 1.4, "extreme": 1.5}`, `config/settings.py:1428-1430`, `config.toml:1004-1009`).
- `_tp_cap = round(recommended_tp_pct × multiplier, 2)`. Enforced after DeepSeek by clamping `optimized.tp_pct = _tp_cap` and emitting `APEX_TP_CAP`.

### 5 examples of size changes (APEX_OK with size delta or GATE adjustments) — 24-hour window

1. `2026-05-02 03:50:34.249 APEX_OK | sym=ONDOUSDT dir=Buy sl=0.3% tp=0.5% cls=low lev=3x sz=$1200→$600 conf=65% regime=ranging` (DeepSeek sized $1200 → constrained/decided $600).
2. `2026-05-02 03:50:43.128 APEX_OK | sym=INJUSDT dir=Buy sl=0.8% tp=1.4% cls=medium lev=3x sz=$800→$400 conf=100% regime=ranging`.
3. `2026-05-02 04:41:02.572 APEX_OK | sym=ONDOUSDT dir=Buy sl=0.3% tp=0.5% cls=low lev=3x sz=$500→$300 conf=60% regime=ranging`.
4. `2026-05-02 04:48:47.295 APEX_OK | sym=AXSUSDT dir=Buy sl=0.9% tp=2.5% cls=medium lev=3x sz=$500→$300 conf=100% regime=trending_up`.
5. `2026-05-02 05:18:04.271 APEX_OK | sym=AEROUSDT dir=Buy sl=0.8% tp=1.4% cls=medium lev=5x sz=$500→$800 conf=75% regime=ranging` (size INCREASE — gate then capped at 1.5×: `2026-05-02 05:18:07.214 CONVICTION_SIZE_CAP | sym=AEROUSDT claude=$500 requested=$800 capped=$750 mult=1.5x`).

Gate-side size adjustments (24-hour window, examples):
- `GATE_ADJUST | sym=INJUSDT changes=[conviction_cap=$247(w=0.5x)]` (low profit-factor weight).
- `GATE_ADJUST | sym=MANAUSDT changes=[conviction_cap=$246(w=0.5x), APEX_GUARDRAIL_TP_FLOOR(apex=0.09->claude=0.09), APEX_CONF_SIZE(30%<50%,size_scale=60%)]`.

## 7. Failure modes — counts in last 24h

Time window: 2026-05-01 11:48:00 → 2026-05-02 11:49:30 UTC.

| Tag | Source | Count |
|-----|--------|------:|
| `APEX_TIMEOUT` (raised by `qwen_client.py:195-200`) | grep `APEX_TIMEOUT` | 0 |
| `APEX_TIMEOUT_REGIME` (`optimizer.py:351`, regime-fallback path) | grep | 0 |
| `APEX_PARSE_FAIL` | grep | 0 |
| `APEX_FAIL_UNEXPECTED` (`optimizer.py:359`) | grep | 0 |
| `APEX_FALLBACK` literal | grep | 0 |
| `APEX_SKIP` (`optimizer.py:555` — generic fallback log) | grep | 0 |
| `APEX_SKIP_NO_PRICE` (`optimizer.py:118`) | grep | 0 |
| `APEX_PRICE_FALLBACK` (`assembler.py:170`) | grep | 1 |
| `APEX_OK` | grep | 51 |
| `APEX_FLIP` | grep | 6 |
| `APEX_FLIP_BLOCKED` | grep | 2 |
| `APEX_FLIP_RESIZE_BLOCKED` | grep | 6 |
| `APEX_TIER` (total optimizations) | grep | 57 |
| `APEX_TP_CAP` | grep | 38 |
| `APEX_GUARDRAIL_TP_FLOOR` | grep | 23 |
| `APEX_CONF_SIZE` | grep | 1 |

Failure rate (true failures): 0/57 = 0% in this window. NOTE: APEX module appears stable since the timeout bump from 30s → 60s; the only "skip" event was a single `APEX_PRICE_FALLBACK` to REST ticker, which still produced an APEX_OK.

## 8. Parallelism — `asyncio.gather` usage

VERIFIED. The optimize-fan-out happens in the orchestrator, not inside `optimizer.py` itself.

`src/core/layer_manager.py:1254-1271`:
```python
if apex:
    _apex_tasks = {}
    for _i, _t in enumerate(plan.new_trades):
        if isinstance(_t, dict) and _t.get("symbol"):
            _apex_tasks[_i] = apex.optimize(_t, plan)
    if _apex_tasks:
        _apex_results = await asyncio.gather(
            *_apex_tasks.values(), return_exceptions=True
        )
        for _idx, _res in zip(_apex_tasks.keys(), _apex_results):
            if isinstance(_res, Exception):
                _sym = plan.new_trades[_idx].get("symbol", "?")
                log.warning(
                    f"APEX_GATHER_FAIL | sym={_sym} "
                    f"err='{str(_res)[:80]}' | {ctx()}"
                )
            else:
                optimized_results[_idx] = _res
```

`asyncio.gather(..., return_exceptions=True)` ensures one failed coin does not abort the others.

### Single-coin vs multi-coin timing

Per-call timing is logged via `APEX_TIMING` at `optimizer.py:324-328`:
```
APEX_TIMING | sym={symbol} el={_opt_el_ms:.0f}ms | assemble={_assemble_ms:.0f}ms deepseek={_deepseek_ms:.0f}ms parse={_parse_ms:.0f}ms constraints={_constraints_ms:.0f}ms
```

Single-coin examples (24-hour window):
- `APEX_TIMING | sym=ENAUSDT el=14285ms | assemble=149ms deepseek=14135ms parse=0ms constraints=0ms`
- `APEX_TIMING | sym=AXSUSDT el=5777ms | assemble=178ms deepseek=5598ms parse=0ms constraints=0ms`
- `APEX_TIMING | sym=HYPEUSDT el=34353ms | assemble=118ms deepseek=34234ms parse=0ms constraints=0ms`

Range observed: `el=2099ms` (NEARUSDT 06:26 flip) up to `el=34353ms` (HYPEUSDT). DeepSeek HTTP latency dominates — `assemble` is consistently 100-300 ms, parse/constraints are 0 ms.

Multi-coin parallelism evidence — `did=d-1777693698066` (3-coin batch ONDOUSDT, INJUSDT, BLURUSDT):

```
03:50:21.529 APEX_TIER | sym=ONDOUSDT  ranging fallback
03:50:21.591 APEX_TIER | sym=INJUSDT   full_optimize
03:50:21.625 APEX_TIER | sym=BLURUSDT  ranging fallback
03:50:34.249 APEX_TIMING | ONDOUSDT  el=12916ms
03:50:34.728 APEX_TIMING | BLURUSDT  el=13394ms
03:50:43.128 APEX_TIMING | INJUSDT   el=21795ms
```

All three started within 100 ms of each other (`asyncio.gather` fan-out) and finished as their respective DeepSeek responses arrived. Wall-clock for the whole batch: ~22 seconds (slowest single call), not the sum (~48 seconds). This is the parallelism payoff.

distinct-`did` count (24h window, where ≥2 coins): three batches with 3 coins, eight batches with 2 coins. So multi-coin batches are common.
