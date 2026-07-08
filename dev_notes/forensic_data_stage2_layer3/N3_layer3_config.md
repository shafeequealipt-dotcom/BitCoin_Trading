# N3 — Layer 3 Configuration

**Collected:** 2026-05-02 ~11:47 UTC
**Source:** `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml` and source files.

---

## A. config.toml — `[apex]` (verbatim, lines 952–1009)

```toml
# =============================================================================
# APEX — Aggressive Profit Extraction & Exploitation (via OpenRouter)
# =============================================================================
[apex]
enabled = true
model = "deepseek/deepseek-v3.2"
fallback_model = "deepseek/deepseek-chat"
# Layer 3: was 30s; DeepSeek frequently responds at 30-32s causing APEX_TIMEOUT.
timeout_seconds = 60
max_tokens = 800
temperature = 0.2
max_position_size_usd = 1200
max_leverage = 5
min_tias_trades_for_optimization = 3
min_regime_trades_for_fallback = 10

# Guardrails
min_tp_pct = 0.3
gate_tp_floor_enabled = true
gate_trail_activation_floor_pct_of_tp = 15.0
gate_trail_distance_floor_pct = 40.0
gate_mode_override_enabled = true
gate_confidence_floor = 0.50
# Hard size-cap: APEX/conviction inflation cannot exceed 1.5× Claude's
# pre-APEX directive size. Gate CHECK 0 enforces this and logs
# CONVICTION_SIZE_CAP when it binds. Set 0 to disable.
gate_apex_size_cap_mult = 1.5

# Conviction Allocator
conviction_enabled = true
conviction_min_trades = 3

# Definitive-fix Phase 9 (2026-04-28) — flip discipline.
#   apex_min_flip_confidence: confidence floor (0..1) for any flip in
#     ranging / dead / unknown.
#   apex_block_flip_resize: when true, a flip cannot also change size
#     in the same call. One decision change per directive.
apex_min_flip_confidence = 0.90
apex_block_flip_resize = true

# Per-class TP cap multiplier (× recommended_tp_pct from volatility profiler).
[apex.tp_cap_multiplier_by_class]
dead = 1.2
low = 1.3
medium = 1.3
high = 1.4
extreme = 1.5
```

---

## B. config.toml — `[enforcer]` (verbatim, lines 789–831)

```toml
[enforcer]
# Enforcer v2 — PnL-Based Intelligent Throttling
enabled = true
check_interval_seconds = 60

# PnL-based thresholds (daily PnL %)
pnl_caution_pct = -2.0              # Below this → el=1 (capital preservation)
pnl_survival_pct = -5.0             # Below this → el=2 (survival)

# Size reduction for mild negative PnL
size_reduction_enabled = true
size_reduction_at_pnl_pct = 0.0     # Start reducing below this PnL %
size_reduction_factor = 0.75        # 25% smaller positions (0% to caution)

# Streak as secondary signal (only when PnL is negative)
streak_boost_threshold = -5         # 5-loss streak + negative PnL → immediate el=1

# Auto-recovery
max_enforcement_minutes = 45        # Auto-recover after stuck at el>=1
grace_period_minutes = 30           # Manual reset grace (full skip)

# Per-level restrictions
level_1_max_positions = 3
level_1_max_leverage = 3
level_1_min_score = 75
level_2_max_positions = 2
level_2_max_leverage = 3
level_2_min_score = 80
level_2_min_confluence = 7
level_2_min_rr = 3.0

# Legacy fields (kept for backward compatibility)
decay_minutes = 60
min_trades_per_hour = 20
min_profit_per_hour_pct = 5.0
min_win_rate = 0.45
min_signals_per_hour = 50
min_setups_to_brain_per_hour = 10
max_seconds_between_trades = 90
max_escalation_level = 5
force_trade_on_gap = true
rewards_enabled = true
hourly_report_enabled = true
```

---

## C. config.toml — `[fund_manager]` (verbatim, lines 754–787)

```toml
[fund_manager]
# Intelligent Fund Manager — 22-module capital management
enabled = true
check_interval_seconds = 60
starting_unlock_pct = 20
active_pool_pct = 70
aplus_reserve_pct = 20
emergency_reserve_pct = 10
profit_lock_pct = 50
trade_profit_lock_pct = 25
max_correlation_bucket_pct = 30
min_profitable_trade_fee_pct = 0.12

# ─── Phase 5 (post-Layer-1 fix): FundReconciler ──────────────────────
reconcile_enabled = true
reconcile_interval_seconds = 60
reconcile_drift_alert_threshold_pct = 5.0
reconcile_auto_correct = false
```

---

## D. config.toml — `[pnl_targets]` (verbatim, lines 638–644)

```toml
[pnl_targets]
# Daily PnL — AGGRESSIVE (paper trading)
daily_target_pct = 10.0
protect_threshold_pct = 7.0
caution_threshold_pct = -3.0
survival_threshold_pct = -7.0
halt_threshold_pct = -10.0
```

---

## E. NOT FOUND — `[trade_gate]`, `[order_service]`, `[shadow]`, `[pnl_manager]`

**Searched:** config.toml for `[trade_gate]`, `[order_service]`,
`[shadow]`, `[pnl_manager]`. NOT FOUND — these are coded in
src/* and read partially from `[risk]`, `[layer_manager]`,
`[general]` (shadow_api_url), and module-level constants.

For shadow URL/config: `[general].shadow_api_url = "http://127.0.0.1:9090"`
(line 12). For trade-gate boot deadlines:
`[layer_manager].lm_attach_deadline_sec = 60.0` (line 1246).
For order-service risk caps: `[risk]` block (lines 239–251).

---

## F. Hardcoded values — src/apex/optimizer.py

(file size 743 lines)

- `optimizer.py:50` — `self._client = qwen_client`
- `optimizer.py:51` — `self._assembler = assembler`
- `optimizer.py:52` — `self._settings = settings`
- `optimizer.py:78` — `_assemble_ms = 0.0` (timing init)
- `optimizer.py:79` — `_deepseek_ms = 0.0`
- `optimizer.py:80` — `_parse_ms = 0.0`
- `optimizer.py:81` — `_constraints_ms = 0.0`
- `optimizer.py:134` — `min_regime = getattr(self._settings,
  "min_regime_trades_for_fallback", 10)` (default 10)
- `optimizer.py:496` — `_sl_floor = 0.2` (% absolute SL floor)
- `optimizer.py:505,509` — `getattr(self._settings, "min_tp_pct", 0.3)`
- `optimizer.py:562` — `sl_pct=2.0` (placeholder, ignored when
  `is_fallback=True`)
- `optimizer.py:563` — `tp_pct=1.5` (placeholder, ignored when
  `is_fallback=True`)
- `optimizer.py:569` — `add_trigger_pct=0.0`
- `optimizer.py:570` — `add_size_pct=0`
- `optimizer.py:572` — `confidence=0.0`

---

## G. Hardcoded values — src/apex/gate.py

(file size 474 lines)

- `gate.py:42` — `self._services = services`
- `gate.py:43` — `self._settings = settings`
- `gate.py:46` — `self._conviction_cache_ttl: float = 300.0  # 5 minutes`
- `gate.py:74` — `cap_mult = 1.5` (fallback when settings missing)
- `gate.py:78` — `claude_orig = 0.0`
- `gate.py:91` — `f"capped=${max_allowed:.0f} mult={cap_mult}x"`
- `gate.py:109` — `max_concurrent = 5` (max open positions hard-coded)
- `gate.py:117` — `reduced = round(size * 0.3, 2)` (30% size reduction
  when at concurrency cap)
- `gate.py:126` — `available = 1000.0` (safe default when fund_manager
  unavailable)
- `gate.py:140` — `weight *= 1.20  # A+ setup: 20% boost`
- `gate.py:144` — `weight *= 0.90  # B setup: 10% reduction`
- `gate.py:146` — `weight *= 0.80  # C/D setup: 20% reduction`
- `gate.py:148` — `base_pct = 0.4  # base 40% of available`
- `gate.py:150` — `weighted_pct = max(0.05, min(weighted_pct, 0.40))`
  (floor 5%, cap 40%)
- `gate.py:169,180,308` — `trade["size_usd"] = round(size * 0.5, 2)`
  (50% size reduction in various RR-failure paths)
- `gate.py:189` — `min_size = 50.0` (USD min trade size)
- `gate.py:241` — `_floor_pct = getattr(self._settings,
  "gate_trail_activation_floor_pct_of_tp", 50.0)`
- `gate.py:246` — `min_activation = max(min_activation, 0.5)` (absolute
  floor 0.5%)
- `gate.py:259` — `_dist_floor = getattr(self._settings,
  "gate_trail_distance_floor_pct", 40.0)`
- `gate.py:283` — `_conf_floor = getattr(self._settings,
  "gate_confidence_floor", 0.50)`
- `gate.py:286` — `scale = max(0.3, apex_confidence / _conf_floor)`
- `gate.py:304` — `trade["size_usd"] = round(size * 0.25, 2)`
- `gate.py:317` — `abs(_tp - _sl) / max(_tp, _sl) < 0.001` (SL=TP collision)
- `gate.py:320,322` — `trade["take_profit_price"] = round(_tp * 1.02, 8)`
  / `* 0.98`
- `gate.py:424` — `weight = 0.75  # Not enough history — cautious default`
- `gate.py:447` — `profit_factor = 10.0  # Cap at 10 to avoid infinity`
- `gate.py:452` — `if profit_factor > 3.0: weight = 2.0`
- `gate.py:453–458` — conviction weight ladder

---

## H. Hardcoded values — src/trading/services/order_service.py

(file size 1156 lines)

- `order_service.py:73` — `_ORDER_LINK_ID_PREFIX = "ti"`
- `order_service.py:74` — `_ORDER_LINK_ID_LEN = 24` (uuid4 hex chars)
- `order_service.py:75` — `_ORDER_PLACE_RETRY_DELAY_S = 0.5`
- `order_service.py:76` — `_ORDER_PLACE_MAX_ATTEMPTS = 2` (initial + 1 retry)
- `order_service.py:245` — `deadline_s =
  float(self._settings.layer_manager.lm_attach_deadline_sec)` (config-driven)
- `order_service.py:560–561` — `max_pct =
  self._settings.risk.max_position_size_pct; max_usd = equity * (max_pct / 100)`
- `order_service.py:575` — `max_loss = equity * 0.02` (2% max loss per
  trade)
- `order_service.py:817` — `limit=10` (recent-orders helper limit)
- `order_service.py:851` — `@retry(max_attempts=2, delay=0.5,...)` decorator
- `order_service.py:909,938` — `@retry(max_attempts=2, delay=0.5)`
- `order_service.py:965,990` — `@retry(max_attempts=3, delay=1.0)` (other
  RPC calls — set_leverage, query_position)
- `order_service.py:1040` — `if leverage is not None and leverage >
  self._settings.risk.max_leverage:` (config max_leverage = 5)

---

## I. Hardcoded values — src/strategies/performance_enforcer.py

(file size 577 lines; init in __init__ around L40–L85)

- `performance_enforcer.py:44` — `self._trades_today = 0`
- `performance_enforcer.py:45` — `self._wins_today = 0`
- `performance_enforcer.py:46` — `self._losses_today = 0`
- `performance_enforcer.py:47` — `self._profit_today_pct = 0.0`
- `performance_enforcer.py:48` — `self._streak = 0`
- `performance_enforcer.py:51–52` — `{"Buy": {"wins": 0, "losses": 0},
  "Sell": {"wins": 0, "losses": 0}}`
- `performance_enforcer.py:64` — `self._pnl_caution_pct: float =
  getattr(_ecfg, "pnl_caution_pct", -2.0)`
- `performance_enforcer.py:65` — `self._pnl_survival_pct: float =
  getattr(_ecfg, "pnl_survival_pct", -5.0)`
- `performance_enforcer.py:67` — `self._size_reduction_at_pnl_pct: float
  = getattr(_ecfg, "size_reduction_at_pnl_pct", 0.0)`
- `performance_enforcer.py:68` — `self._size_reduction_factor: float =
  getattr(_ecfg, "size_reduction_factor", 0.75)`
- `performance_enforcer.py:69` — `self._streak_boost_threshold: int =
  getattr(_ecfg, "streak_boost_threshold", -5)`
- `performance_enforcer.py:73` — `self._l1_max_pos: int = getattr(_ecfg,
  "level_1_max_positions", 3)`
- `performance_enforcer.py:74` — `self._l1_max_lev: int = getattr(_ecfg,
  "level_1_max_leverage", 3)`
- `performance_enforcer.py:75` — `self._l1_min_score: int = getattr(_ecfg,
  "level_1_min_score", 80)` (note: config has 75, default code 80)
- `performance_enforcer.py:76` — `self._l2_max_pos: int = getattr(_ecfg,
  "level_2_max_positions", 2)`
- `performance_enforcer.py:77` — `self._l2_max_lev: int = getattr(_ecfg,
  "level_2_max_leverage", 3)`
- `performance_enforcer.py:78` — `self._l2_min_score: int = getattr(_ecfg,
  "level_2_min_score", 80)`
- `performance_enforcer.py:79` — `self._l2_min_confluence: int =
  getattr(_ecfg, "level_2_min_confluence", 7)`
- `performance_enforcer.py:80` — `self._l2_min_rr: float =
  getattr(_ecfg, "level_2_min_rr", 3.0)`
- `performance_enforcer.py:140` — `return self._size_reduction_factor`
- `performance_enforcer.py:142–149` — size-reduction ladder values 0.50,
  0.40, 0.25 for deeper losses
- `performance_enforcer.py:184–196` — recovery_stage 0/1/2 thresholds

---

## J. Hardcoded values — src/strategies/pnl_manager.py

(file size 449 lines)

- `pnl_manager.py:74,175,345` — `self.realized_pnl = 0.0`
- `pnl_manager.py:77,179,348` — `self._trades_today = 0`
- `pnl_manager.py:80,182` — `self._max_drawdown_today = 0.0`
- `pnl_manager.py:81,183` — `self._best_trade_pct = 0.0`
- `pnl_manager.py:82,184` — `self._worst_trade_pct = 0.0`
- `pnl_manager.py:83,185,380,389` — `self._streak_count = 0`
- `pnl_manager.py:85,187` — `self._avg_win_pct = 0.0`
- `pnl_manager.py:86,188` — `self._avg_loss_pct = 0.0`
- `pnl_manager.py:88,190` — `self._total_win_pnl = 0.0`
- `pnl_manager.py:89,191` — `self._total_loss_pnl = 0.0`
- `pnl_manager.py:165` — `self._persist_counter = 0`
- `pnl_manager.py:201` — `self.current_pnl_pct = 0.0`
- `pnl_manager.py:347` — `self.starting_equity = 0.0` (forces re-capture)

(All thresholds and PnL targets are read from `[pnl_targets]` config
section via the BookKeeper; pnl_manager itself only stores running
state — no thresholds hardcoded.)
