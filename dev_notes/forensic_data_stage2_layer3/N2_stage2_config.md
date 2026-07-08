# N2 — Stage 2 Configuration

**Collected:** 2026-05-02 ~11:47 UTC
**Source:** `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml` and source files.

---

## A. config.toml — `[brain]` (verbatim, lines 162–223)

```toml
[brain]
# Claude Code CLI — no API key needed, no budget limit
# Uses existing Claude Max subscription ($0 per call)
enabled = true
use_claude_code = true

# Definitive-fix Phase 6 (2026-04-28): cold-start completeness gate.
# Forensic E.2.4 captured first-cycle packages at completeness=0.67
# (XRAY/regime/F&G caches still warming up) — auto-execute fired and
# placed losing trades on incomplete data. The gate fires BEFORE
# Claude is called: the cycle is short-circuited and a Telegram alert
# warns the operator. ``boot_grace_*`` is the stricter gate during
# the first ``boot_grace_period_sec`` seconds after process start.
# Layer 1 restructure Phase 7 — when true, the strategist reads
# per-coin sections from layer_manager._coin_packages instead of
# querying 12 services per cycle. Set false to fall back to the
# legacy service-query path during Phase 9 observation if a
# regression is detected.
use_packages = true
# Phase 9 cutover (2026-05-01): flipped default to true. The strategist
# surfaces the Phase 3/4 briefing fields (state_label, action_hint,
# interestingness_score, votes block) in the per-coin TRADE CANDIDATES
# block AND extends TRADE_SYSTEM_PROMPT with one new section that
# teaches Claude how to read those fields. Set false to roll back to
# the legacy prompt shape instantly.
surface_briefing_fields = true
# Strategic review interval (seconds) — alternating Call A (trades) / Call B (positions)
# 150s = 2.5 min between calls, giving 5 min per call type
strategic_interval = 150
# Watchdog Claude review interval (seconds) — reviews positions every 30s
watchdog_interval = 30
# Legacy settings kept for backward compatibility
analysis_interval = 900
signal_triggered = true
min_signal_confidence = 0.45
max_calls_per_hour = 30
model = "claude-sonnet-4-20250514"
max_tokens = 4096
temperature = 0.3

# Claude CLI subprocess timing (Phase 2 session-stability fix — Y-22 + timeout retune)
# Hard cap on one Claude CLI invocation. Was hardcoded 300 in manager.py.
claude_cli_timeout_seconds = 300
# Retries after failure (non-retryable errors — auth, billing — still skip retry).
claude_cli_max_retries = 2
# Floor between consecutive Claude CLI invocations (adaptive interval).
claude_cli_min_interval = 2.0
# Backoff base for timeout-path retries: sleep = (attempt+1) * base seconds.
# 10 → ladder 10s/20s/30s. Was hardcoded 30 → 30s/60s/90s.
# Lowering halves the brain-outage window after a single timeout.
claude_cli_retry_timeout_backoff_base_seconds = 10
# Phase 3 (Brain credentials) — pre-flight refresh margin in seconds.
# Trigger an OAuth refresh if the access token expires within this window;
# if the refresh fails AND we are inside the margin, raise
# CredentialRefreshError instead of spawning a doomed subprocess.
credential_refresh_margin_seconds = 600
# Phase 3 (Brain credentials) — refresh attempt budget per call.
# 3 attempts with exponential backoff (1s/3s/7s) before giving up.
credential_refresh_max_attempts = 3
# Cap on watchdog events injected into the Call A URGENT prompt.
# Defence-in-depth — EventBuffer already truncates at 3000 chars.
prompt_event_buffer_max_events = 20
```

### `[brain.cold_start_protection]` (verbatim, lines 226–237)

```toml
# Definitive-fix Phase 6 — cold-start completeness gate.
[brain.cold_start_protection]
enabled = true
min_avg_completeness = 0.85
min_per_package_completeness = 0.75
# Phase 7 of the 1D briefing rewrite — lowered from 3 to 1. The gate's
# purpose is CACHE-WARMUP safety, not minimum-cohort enforcement. One
# well-formed package proves caches are warm. The completeness floors
# (min_avg_completeness, boot_grace_completeness) still detect
# cache-degradation. See dev_notes/phase7_1d_briefing/decision_record.md.
min_qualified_packages = 1
boot_grace_period_sec = 600
boot_grace_completeness = 0.95
```

---

## B. NOT FOUND — `[claude_code_client]`, `[strategist]`

**Searched:** config.toml for `[claude_code_client]`, `[strategist]`,
`[claude]`. NOT FOUND — Claude CLI client settings are read from
`[brain]` (claude_cli_*, credential_refresh_*); strategist has no
dedicated section, all knobs are in `[brain]` or `[scanner.briefing]`.

---

## C. Hardcoded values — src/brain/strategist.py

(file size 2864 lines)

- `strategist.py:65` — `TRADE_SYSTEM_PROMPT = """You are an aggressive
  but intelligent crypto futures trader. ..."""` (multi-line constant;
  CALL_A system prompt at runtime sys=8985 chars)
- `strategist.py:150` — `POSITION_SYSTEM_PROMPT = """You are managing
  open crypto futures positions. ..."""`
- `strategist.py:171` — `STRATEGIST_SYSTEM_PROMPT = TRADE_SYSTEM_PROMPT`
- `strategist.py:180` — `BRIEFING_SYSTEM_PROMPT_SUFFIX = """ ..."""`
  (appended to TRADE_SYSTEM_PROMPT when `surface_briefing_fields=true`)
- `strategist.py:576` — `_regime_confidence = 0.5` (default when regime
  service returns None)
- `strategist.py:577` — `_fear_greed_value = 50` (neutral default)
- `strategist.py:691,1719` — `included_count = 0`, `skipped_count = 0`
- `strategist.py:692,1720` — counters init
- `strategist.py:708,1754,2649` — `rsi = 50` (neutral default)
- `strategist.py:709,1755,2650` — `macd_hist = 0`
- `strategist.py:710,1756` — `adx = 0`
- `strategist.py:1145,2072` — `deployed = 0.0`
- `strategist.py:1317` — `LABEL_NO_TRADEABLE_STATE = "NO_TRADEABLE_STATE"`
- `strategist.py:1563` — `_regime_confidence = 0.5` (CALL_B path)
- `strategist.py:1564` — `_fear_greed_value = 50` (CALL_B path)
- `strategist.py:1659` — `_packages_count = 0`
- `strategist.py:2184` — `_SECTION_CAP = 80` (max sections per prompt)
- `strategist.py:2185` — `_CHAR_CAP = 14000` (max chars; live runs trim
  to ~14000 — see brain.log:18262 CLAUDE_PROMPT_TRIMMED `cap_chars=14000`)
- `strategist.py:2319` — `sl_consumed = 0.0`

(Live observation: section count cap 80 not exceeded in observed
window; char cap 14000 was hit and trimmed in CALL_A
did=d-1777702618197 on 2026-05-02 06:16:58 — `chars_before=17506 →
chars_after=17162`. Trim algorithm prunes lower-priority sections
until under one of the caps.)

---

## D. Hardcoded values — src/brain/claude_code_client.py

(file size 1465 lines)

### Module-level constants
- `claude_code_client.py:48` —
  `_NON_RETRYABLE = frozenset([...])` (auth/billing error tags that skip retry)
- `claude_code_client.py:61` — `_PROJECT = str(Path(__file__).resolve().parents[2])`
- `claude_code_client.py:62` — `_HOME = os.environ.get("HOME") or str(Path.home())`
- `claude_code_client.py:63` — `_CREDENTIAL_PATH = Path(_HOME) / ".claude" / ".credentials.json"`
- `claude_code_client.py:66` — `_OAUTH_TOKEN_URL = "https://claude.ai/v1/oauth/token"`
- `claude_code_client.py:67` — `_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"`
- `claude_code_client.py:70` — `_AUTH_BACKOFF_SCHEDULE = [300, 600, 1200, 2400, 3600]`
  (5min/10min/20min/40min/60min ladder when auth-fails persistently)

### `__init__` defaults (claude_code_client.py:81–122)
- `timeout_seconds: int = 90` (overridden by config → 300)
- `max_retries: int = 2`
- `min_interval: float = 2.0`
- `retry_timeout_backoff_base_seconds: int = 30` (legacy default; config
  overrides to 10)
- `credential_refresh_margin_seconds: int = 600`
- `credential_refresh_max_attempts: int = 3`
- `stall_warn_buckets_seconds = (60.0, 120.0, 240.0)` — 60→INFO,
  120→WARNING, 240→ERROR

### Subprocess streaming constants
- `claude_code_client.py:932` — `_STALL_LOG_EVERY_S = 60.0` (cadence for
  CLAUDE_PROC_STALL warnings)
- `claude_code_client.py:935` — `_SUBPROC_POLL_INTERVAL_S = 0.05`
  (50ms polling cadence for chunked stdout reader)
- `claude_code_client.py:317` —
  `min_interval * (2 ** self._consecutive_failures), 30.0` (cap
  adaptive interval at 30s)
- `claude_code_client.py:336` —
  `backoff_s = max(int(reset_ts - time.time()), 300)` (usage-quota
  reset minimum backoff 300s)
- `claude_code_client.py:338` — `backoff_s = 3600` (default 1h fallback
  when usage reset unparseable)
- `claude_code_client.py:958` —
  `cmd = [self._claude_path, "-p", "--output-format", "text"]`
- `claude_code_client.py:977` — `preexec_fn=os.setsid` (process group
  isolation)
