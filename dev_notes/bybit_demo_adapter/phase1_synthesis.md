# Phase 1 Synthesis — Foundation Verification

**Date:** 2026-05-08
**Branch:** feature/bybit-demo-adapter
**Purpose:** Single reference document combining the 9 sub-investigations. Subsequent phases cite this instead of re-reading the codebase.

---

## 1. Transformer (`src/core/transformer.py`, 1118 lines)

### Class Anatomy
- `class Transformer:` (line 23)
- Constructor `__init__(self, db: DatabaseManager, config: Any)` (line 35)
- Internal state:
  - `self._current_mode: str = "shadow"` (line 38)
  - `self._is_switching: bool = False` (line 39)
  - `self._switching_to: str | None = None` (line 40)
  - `self._last_switched_at: str | None = None` (line 41)
  - `self._initialized: bool = False` (line 42)
  - `self._shadow_services: dict[str, Any] = {}` (line 45)
  - `self._bybit_services: dict[str, Any] = {}` (line 46)
  - `self._active_services: dict[str, Any] = {}` (line 47)
  - `self._shadow_available: bool = True` (line 48)
  - `self._event_buffer: Any = None` (line 49)
  - `self._on_switch_callbacks: list = []` (line 50)
  - `self._last_enrichment_max_divergence_pct: float = 0.0` (line 56)

### Adapter Registration — `set_services()` (line 77)
```python
def set_services(
    self,
    shadow_order: Any = None,
    shadow_position: Any = None,
    shadow_account: Any = None,
    bybit_order: Any = None,
    bybit_position: Any = None,
    bybit_account: Any = None,
) -> None:
    self._shadow_services = {"order": shadow_order, "position": shadow_position, "account": shadow_account}
    self._bybit_services = {"order": bybit_order, "position": bybit_position, "account": bybit_account}
```
**Phase 3.B extension:** Add `bybit_demo_order`, `bybit_demo_position`, `bybit_demo_account` kwargs (default `None`) and a parallel `self._bybit_demo_services` dict. Existing kwargs preserved.

### Boot Init — `initialize()` (line 101)
- Reads `transformer_state` row (id=1) from DB.
- If `is_switching=True` was persisted, runs crash recovery (lines 124-176).
- Calls `self._apply_mode()` to set `_active_services`.
- Health-probes Shadow if `current_mode="shadow"`.

### Mode Dispatch — `_apply_mode()` (line 207)
```python
def _apply_mode(self) -> None:
    if self._current_mode == "shadow":
        self._active_services = self._shadow_services
    else:
        self._active_services = self._bybit_services
```
**Phase 3.B change:** 3-way dispatch — shadow / bybit / bybit_demo.

### Hot-Swap — `switch_to(target_mode, reason, confirmed)` (line 241)
- **Stays untouched per operator decision** (preserves existing live-bybit hot-swap path).
- Phase 3.B only extends the validation list at line 264 to accept `"bybit_demo"`. The `confirmed=True` requirement at line 273-277 stays for `"bybit"` (live); `"bybit_demo"` does NOT require it (paper money).
- Body (close-all + in-memory flip + DB persist + history record) untouched.

### Public API
- Properties: `current_mode`, `is_shadow`, `is_bybit`, `is_switching`, `mode_label` (lines 564-589) → add `is_bybit_demo`.
- Convenience: `get_open_positions_summary()` (488), `get_current_equity()` (509), `get_target_equity(target_mode)` (524) → extend `get_target_equity` for new mode.
- Service accessors: `active_order_service` / `active_position_service` / `active_account_service` (541-551).
- `create_proxies()` (553) → returns `{"order": _OrderProxy, "position": _PositionProxy, "account": _AccountProxy}`.
- `register_switch_callback(cb)` (218) → cb receives `(old_mode, new_mode)`.
- `set_event_buffer(eb)` (214).

### DB Schema (migrations.py:995-1021)
- `transformer_state` (id, current_mode, is_switching, switching_to, last_switched_at, updated_at).
- `switch_history` (timestamp, from_mode, to_mode, positions_closed, close_results_json, reason, success, error_message, shadow_equity, bybit_equity).
- Index `idx_switch_history_ts`.

---

## 2. Shadow Adapter (`src/shadow/shadow_adapter.py`)

### Class Layout
- `ShadowOrderService` (line 409) — `__init__(session, base_url)`.
- `ShadowPositionService` (line 135) — `__init__(session, base_url)`.
- `ShadowAccountService` (line 597) — `__init__(session, base_url)`.

### Boot Grace + Retry — `_shadow_get_with_retry()` (line 59)
- Module-level `_PROCESS_START_MONOTONIC = time.monotonic()` (line 50).
- `_BOOT_GRACE_SECONDS = 30.0` (line 51).
- Default 5 attempts, base_delay 0.2s, backoff `0.2 * 2^(n-1)`.
- HTTP 4xx (except 429): no retry. HTTP 429/5xx/connection: retry.
- During boot grace → DEBUG log; after grace → ERROR log.

### Method Contracts (verified — what BybitDemo must mirror)

#### `ShadowOrderService.place_order(symbol, side, order_type, qty, price=None, stop_loss=None, take_profit=None, leverage=None, *, purpose="other", layer_snapshot=None, force=False) -> Order`
Returns `Order` dataclass (from `src/core/types.py`). Success → `OrderStatus.FILLED`; error → `OrderStatus.REJECTED`.

#### `ShadowPositionService.get_positions(symbol=None) -> list[Position]`
Returns list of `Position` dataclasses with: `symbol, side, size, entry_price, mark_price, unrealized_pnl, realized_pnl=0.0, leverage, liquidation_price=0.0, stop_loss, take_profit`.

#### `ShadowPositionService.get_position(symbol) -> Position | None`
#### `ShadowPositionService.get_last_close(symbol) -> dict | None`
Returns raw JSON dict with: `exit_price, net_pnl_pct, net_pnl_usd, close_trigger, closed_at (ISO 8601), hold_duration_seconds, result`.

#### `ShadowPositionService.close_position(symbol, *, purpose="layer4_close") -> Order`
Returns `Order` (FILLED) with `price=exit_price, qty=qty, status=OrderStatus.FILLED`.

#### `ShadowPositionService.reduce_position(symbol, qty) -> Order`
Falls back to `close_position()` on rejection; logs `REDUCE_FALLBACK`.

#### `ShadowPositionService.set_stop_loss(symbol, stop_loss) -> bool`
#### `ShadowPositionService.set_take_profit(symbol, take_profit) -> bool`

#### `ShadowAccountService.get_wallet_balance() -> AccountInfo`
Returns `AccountInfo(total_equity, available_balance, used_margin, unrealized_pnl, margin_level_pct=0.0)`.

#### `ShadowAccountService.health_check() -> bool`
GET `/api/health` with 5s timeout.

### Critical Contract Rule (from Shadow's behavior)
**Shadow does NOT raise exceptions.** It returns sentinel values:
- `Order(status=OrderStatus.REJECTED, ...)` on error
- `[]` (empty list) for `get_positions` on error
- `_empty_account_info()` (zeroed `AccountInfo`) for `get_wallet_balance` on error
- `None` for `get_last_close` on error
- `False` for `set_stop_loss` / `set_take_profit` on error

**The Bybit demo adapter MUST mirror this.** Returning exceptions instead of sentinels would break Layer 4 / brain consumers that check `.status == REJECTED` not `try/except`.

### Log Tags (Shadow)
- `SHADOW_HTTP_FAIL` (WARN, line 101) — non-200 4xx response
- `SHADOW_CALL_FAIL` (DEBUG/ERR, line 121) — exhausted retries
- `SHADOW_POSITION_CLOSE` (INFO, line 251)
- `SHADOW_ORDER_RECEIVED` (INFO, line 491)
- `SHADOW_ORD_SEND` (INFO, line 505)
- `SHADOW_ORD_RESP` (INFO, line 533)
- `REDUCE_FALLBACK` (WARN, lines 298, 325)

**Bybit demo will emit:** `BYBIT_DEMO_HTTP_FAIL`, `BYBIT_DEMO_CALL_FAIL`, `BYBIT_DEMO_RATE_LIMIT`, `BYBIT_DEMO_POSITION_CLOSE`, `BYBIT_DEMO_ORDER_RECEIVED`, `BYBIT_DEMO_ORD_SEND`, `BYBIT_DEMO_ORD_RESP`, `BYBIT_DEMO_REDUCE_FALLBACK`.

---

## 3. Trading Services (`src/trading/services/`)

### `OrderService.place_order()` (order_service.py:400)
```python
async def place_order(
    self, symbol: str, side: Side, order_type: OrderType, qty: float,
    price: float | None = None, stop_loss: float | None = None,
    take_profit: float | None = None, leverage: int | None = None,
    *, purpose: str = "other",
    layer_snapshot: "LayerSnapshot | None" = None,
    force: bool = False,
) -> Order: ...
```

### `PositionService` (position_service.py)
- `get_positions(symbol=None) -> list[Position]` (line 54)
- `get_position(symbol) -> Position | None` (line 84)
- `close_position(symbol, *, purpose="layer4_close") -> Order` (line 100)
- `reduce_position(symbol, qty) -> Order` (line 227)
- `set_stop_loss(symbol, stop_loss) -> bool` (line 385)
- `set_take_profit(symbol, take_profit) -> bool` (line 407)
- **NOTE:** `get_last_close` is Shadow-only on the live PositionService — but the Shadow ADAPTER still has it. The Bybit demo adapter must implement `get_last_close` to match Shadow's adapter contract.

### `AccountService.get_wallet_balance() -> AccountInfo` (account_service.py:29)

### Delegation Pattern
The live services (`PositionService`, `OrderService`, `AccountService`) wrap a `BybitClient` — they're for the LIVE Bybit pybit-based path. The Shadow adapter implements the SAME interface but talks to localhost:9090. The Bybit demo adapter will implement the SAME interface but talks to api-demo.bybit.com.

---

## 4. Configuration

### `src/config/settings.py:40` — `GeneralSettings`
```python
@dataclass
class GeneralSettings:
    mode: str = "paper"
```

### `config.toml:8-18`
```toml
[general]
mode = "shadow"
shadow_api_url = "http://127.0.0.1:9090"
timezone = "UTC"
log_level = "INFO"
log_dir = "data/logs"
```

### `src/config/validators.py:50` — reads `settings.general.mode`.

### Runtime Override
- NOT via config — mode is persisted in DB `transformer_state.current_mode`.
- Switch at runtime via `Transformer.switch_to(target_mode, reason, confirmed)`.
- On boot, `Transformer.initialize()` reads DB and applies.

### Phase 3.A Additions
- `BybitDemoSettings` dataclass: `enabled, api_key_env, api_secret_env, base_url, recv_window, timeout_seconds, retry_attempts`.
- New `[bybit_demo]` section in `config.toml`.
- Add `"bybit_demo"` to validator's allowed list.

---

## 5. Boot Sequence (`src/workers/manager.py:initialize`)

### Critical Steps
1. Line 65: `await self.db.connect()`.
2. Line 66: `await run_migrations(self.db)`.
3. Lines 88-92: **Transformer instantiated** + `await transformer.initialize()` (reads DB mode).
4. Lines 99-120: BybitClient (market data) created.
5. Lines 256-280: **Bybit live trading services** created (only if BybitClient configured).
6. Lines 282-298: **Shadow adapters** created.
7. Lines 301-312: `transformer.set_services(...)` + re-`initialize()` to apply DB mode.
8. Lines 314-330: Proxies created and stored in `_services` (`position_service`, `order_service`, `account_service`).

### Phase 3.C Boot Insertion
Add a **new block between lines 298 and 301** (after Shadow, before set_services) that creates Bybit demo adapter when `settings.bybit_demo.enabled`. Then extend the `transformer.set_services(...)` call to pass `bybit_demo_*=...`.

### Mode Branches Outside Transformer
- `src/trading/client.py:84` — `if not settings.bybit.testnet and settings.general.mode == "paper"`.
- `src/trading/client.py:140` — `if self._settings.general.mode == "shadow"`.

These two branches are about LIVE Bybit credentials. Bybit demo doesn't intersect — Phase 3 doesn't need to touch them.

---

## 6. Telegram Dashboard (`src/telegram/handlers/dashboard_handler.py`)

### Switch Buttons (lines 578-594, current)
```python
transformer = _svc(context, "transformer")
if transformer and not transformer.is_switching:
    if transformer.is_shadow:
        switch_btn = InlineKeyboardButton("🔴 Switch to Bybit", callback_data="dash_switch_bybit")
    else:
        switch_btn = InlineKeyboardButton("🟡 Switch to Shadow", callback_data="dash_switch_shadow")
    keyboard.append([
        InlineKeyboardButton("🚨 CLOSE ALL", callback_data="dash_emergency_close"),
        switch_btn,
    ])
```

### Confirmation Handlers (lines 1490-1547)
- `dash_switch_bybit` → confirmation text → `dash_confirm_bybit` → `transformer.switch_to("bybit", confirmed=True)`.
- `dash_switch_shadow` → confirmation text → `dash_confirm_shadow` → `transformer.switch_to("shadow")`.
- `dash_switch_cancel` → return to dashboard.

### Phase 5 Additions
- New callback `dash_switch_bybit_demo` (button shown when mode in shadow/bybit_demo).
- New callback `dash_confirm_bybit_demo` → `await exchange_switcher.execute_switch_with_restart("bybit_demo", force=True)`.
- New callback `dash_switch_shadow_from_demo` → mirror back to shadow via switcher.
- Existing `dash_switch_bybit`/`dash_confirm_bybit`/`dash_switch_shadow`/`dash_confirm_shadow` UNTOUCHED.
- Pattern match registered at line 2336-2339; add `^dash_switch_bybit_demo|^dash_confirm_bybit_demo|^dash_switch_shadow_from_demo`.

### Accessibility Convention (lines 1-17)
```
ACCESSIBILITY-FIRST DESIGN:
  The user is blind and uses a screen reader.
  Every command must be PACKED with detailed, text-based content.
  Emojis are semantic markers (screen readers announce them).
```
**Implication:** All Phase 5 button labels and confirmation text must include screen-reader-friendly full-word labels.

### Bot Architecture
- `InteractiveTelegramBot` instantiated in `src/telegram/bot.py:33`.
- `TelegramBotWorker` runs the bot as a background asyncio task within `trading-workers` systemd unit.
- **Bot is killed and restarted with trading-workers on systemd restart.** This means after a switch-with-restart, the bot is briefly unavailable then comes back. Post-switch verifier sends notification once bot is up.

---

## 7. systemd (`systemd/`)

### trading-workers.service
- ExecStart: `/home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python workers.py`
- User/Group: `inshadaliqbal786`
- Restart: `always`, RestartSec=15
- After: `network-online.target shadow.service`

### trading-mcp-sse.service
- ExecStart: `/home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python server.py --transport sse --port 8080`
- User/Group: `inshadaliqbal786`
- Restart: `always`, RestartSec=10
- After: `network-online.target trading-workers.service`

### Permission Verification
- User `inshadaliqbal786` owns the services → can `systemctl restart trading-workers trading-mcp-sse` without sudo.
- Phase 4.A uses `subprocess.Popen([...], start_new_session=True)` so the restart child survives parent termination.

---

## 8. Exception Hierarchy (`src/core/exceptions.py`)

All inherit from `TradingMCPError`:
- `ConfigError`, `AuthenticationError`
- `TradingError` → `OrderError` (`InsufficientBalanceError`, `InvalidOrderError`, `OrderRejectedError`, `DuplicateOrderLinkIdError`, `Layer3DisabledError`, `Layer3RaceError`, `Layer3BootNotReadyError`); `PositionError`; `RateLimitError`
- `DataError` → `MarketDataError`, `DatabaseError`, `APIError` (`BybitAPIError`, `FinnhubError`, `RedditError`)
- `IntelligenceError`, `WorkerError`, `BrainError`, `RiskError`

### Bybit Demo Adapter Exception Strategy
Per Shadow's contract, the adapter **does not raise** these — returns sentinels. But the Bybit demo HTTP CLIENT (lower layer) may raise these for the adapter to catch and translate to sentinels:
- HTTP 110001 → `InvalidOrderError`
- HTTP 110007 → `InsufficientBalanceError`
- HTTP 110xxx → `OrderRejectedError`
- Network → `BybitAPIError`
- HTTP 429 → `RateLimitError`

The adapter catches these in its method body and returns the appropriate sentinel (`Order(REJECTED)`, `[]`, etc.).

---

## 9. Existing Bybit Code — What's Reusable

### `src/trading/client.py` — `BybitClient`
- Uses `pybit.unified_trading.HTTP` for live mainnet.
- HMAC auth via `src/trading/auth.py:BybitAuth`.
- **Limited reuse for demo:** `pybit` library does not support custom base URL → cannot point at api-demo.bybit.com easily. Therefore the Bybit demo client is BUILT FROM SCRATCH using `aiohttp` directly (mirroring Shadow's HTTP pattern), not extending BybitClient.

### `src/trading/auth.py` — HMAC signing reference
- HMAC-SHA256: `signature = HMAC(secret, timestamp + api_key + recv_window + body_or_qs)`.
- The Phase 2.B BybitDemoClient implements equivalent signing inline (no shared dependency on `BybitAuth` to avoid coupling).

---

## 10. Git History (since 2026-04-01)

Recent relevant commits on transformer / shadow / trading services:
- `36e51aa docs(strategist/phase-2): correct enable_prompt_compression docstring`
- `b94d0bd fix(logging/phase-3D): route sizing component to workers.log`
- `d4c33d0 feat(sizing/phase-3D): unified SIZE_DERIVATION observability event`
- `960f8cf feat(fund_manager/phase-3C): capital tier hysteresis`
- `69ccc0c feat(sizing/phase-3AB): wire xray_confidence and expected_rr into APEX conviction weight`

No commits since 2026-04-01 modify `Transformer.switch_to` body, Shadow adapter contract, or trading service signatures. The audit (2026-05-08) is current with respect to these files.

---

## Synthesis Conclusion

**The implementation is well-defined:**
1. Build `src/bybit_demo/` mirroring Shadow's contract exactly (return dataclass instances, no exceptions, BYBIT_DEMO_* logs, retry pattern from Shadow).
2. Extend Transformer additively (3rd slot for bybit_demo, 3-way `_apply_mode`, switch_to validation list extended only).
3. Wire in boot sequence (new block between Shadow creation and set_services).
4. Build new ExchangeSwitcher (separate path from existing hot-swap switch_to) that does close-all + DB persist + systemctl restart.
5. Build PostSwitchVerifier called at end of WorkerManager.initialize.
6. Build MCP exchange_tools (additive registration).
7. Add new Telegram button (additive — existing live/shadow buttons untouched).
8. End-to-end test 11 scenarios.

**Risk:** All modifications to `transformer.py`, `manager.py`, `dashboard_handler.py`, `settings.py`, `config.toml`, `mcp/server.py` are strictly additive. Default behavior on `mode="shadow"` is byte-identical pre/post change. Existing live `mode="bybit"` hot-swap is preserved verbatim.
