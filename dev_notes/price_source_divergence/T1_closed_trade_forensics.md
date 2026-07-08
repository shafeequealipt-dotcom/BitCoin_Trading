# T1 — Closed Trade Forensics

## Method

Query both DBs and join by `(symbol, closed_at within ±90 s)`. For each main-project `trade_intelligence` row, locate the closest Shadow `virtual_positions` row.

**Capture timestamp:** 2026-05-02 11:30 UTC. The 8 most recent closed trades all occurred between 04:53 and 06:29 UTC the same day.

## T.1.1 — Per-trade cross-source matrix (8 trades)

Source tags:
- **`M.ti`** = main `trade_intelligence` table (`pnl_pct`, `pnl_usd`, `entry_price`, `exit_price`, `position_size_usd`)
- **`S.vp`** = Shadow `virtual_positions` table (`entry_price`, `exit_price`, `quantity`, `notional_value`, `gross_pnl_usd`, `net_pnl_usd`, `entry_slippage_pct`, `exit_slippage_pct`, `entry_fee_usd`, `exit_fee_usd`, `close_trigger`)

| # | Symbol | Side | Closed_at (main) | M.ti entry | S.vp entry | Δentry % | M.ti exit | S.vp exit | M.ti pnl_usd | S.vp gross | S.vp net | Δpnl (M − S.net) | Close trigger (S) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ONDOUSDT | Buy | 06:29:10 | 0.27 | 0.270081 | -0.0300% | 0.269719 | 0.26971906 | **-0.2880** | -0.3710 | **-0.5232** | **+0.2352** | manual (Δexit=0; main close was `time_decay_p_win_low`) |
| 2 | MANAUSDT | Buy | 06:13:38 | 0.08952 | 0.089546856 | -0.0300% | 0.089473 | 0.08947315 | **-0.1449** | -0.2280 | **-0.3803** | **+0.2354** | manual (main: `time_decay_p_win_low`) |
| 3 | AXSUSDT | Buy | 06:05:17 | 1.3794 | 1.37981382 | -0.0300% | 1.379262 | 1.37918612 | **-0.0630** | -0.1260 | **-0.2784** | **+0.2154** | manual (main: `mode4_p9`) |
| 4 | DOGEUSDT | Sell | 05:58:36 | 0.10751 | 0.107477747 | +0.0300% | 0.107562 | 0.107562259 | **-0.6011** | -0.3537 | **-0.6011** | **+0.0000** | manual (main: `strategic_review:Position already closed/filled per exchange`) |
| 5 | AXSUSDT | Buy | 05:35:14 | 1.3848 | 1.38521544 | -0.0300% | 1.382861 | 1.38278504 | **-0.2689** | -0.3242 | **-0.4258** | **+0.1570** | manual (main: `mode4_p9`) |
| 6 | DOGEUSDT | Sell | 05:35:05 | 0.10779 | 0.107757663 | +0.0300% | 0.107842 | 0.107842343 | **-0.1256** | -0.2031 | **-0.3453** | **+0.2198** | manual (main: `mode4_p9` likely) |
| 7 | RENDERUSDT | Buy | 05:06:49 | 1.7021 | 1.70261063 | -0.0300% | 1.703389 | 1.70338883 | **-0.0515** | +0.2534 | **-0.0515** | **+0.0000** | manual |
| 8 | SANDUSDT | Sell | 04:54:07 | 0.0711 | 0.07107867 | +0.0300% | 0.071131 | 0.071131333 | **-1.4518** | -0.8332 | **-1.4518** | **+0.0000** | manual |

## T.1.2 — Pattern analysis

### Pattern A — entry-price divergence (universal, deterministic)

Every Δentry is exactly **±0.03 %** = the configured `[exchange].slippage_pct = 0.03` in `shadow/config.toml`.

- **Buy side:** Shadow's entry > main's entry by 0.03 % (slippage works against the buyer).
- **Sell side:** Shadow's entry < main's entry by 0.03 % (slippage against the seller).

Mechanism: `OrderEngine.place_order` (`order_engine.py:188-193`) applies slippage to `last_price` to derive `fill_price`, and stores `fill_price` as `virtual_positions.entry_price`. Main project records what it ASKED for (the pre-slippage `last_price` returned in the order response or refetched from `MarketService.get_ticker` at order time).

This is not a bug *per se* — it's the simulation working as designed — but it means **`trade_intelligence.entry_price` is NOT equal to Shadow's `virtual_positions.entry_price`**. Any join on entry_price fails.

### Pattern B — exit-price divergence (path-dependent)

Three closes (rows 4, 7, 8) show **Δexit = 0 to 6 decimals** AND **Δpnl_usd = 0 to 4 decimals**. These have:
- `close_trigger = manual` on Shadow side
- main project's `closed_by` = `strategic_review:...` (row 4) or just `manual`/strategic-driven on rows 7, 8

For these, main project apparently *receives* Shadow's net_pnl_usd from the close response and stores it. The `T-pattern` here is the unified path.

Five closes (rows 1, 2, 3, 5, 6) show **non-zero Δpnl** of $0.16-0.24:
- Row 1: main `closed_by = time_decay_p_win_low`
- Row 2: main `closed_by = time_decay_p_win_low`
- Row 3: main `closed_by = mode4_p9`
- Row 5: main `closed_by = mode4_p9`
- Row 6: main `closed_by = mode4_p9` (inferred from `trade_log`)

For these, main project's close path computes its own `pnl_usd` from main-side recorded prices (= the pre-slippage values) and stores **its own number**, NOT Shadow's net_pnl_usd. The Δpnl ≈ `notional × 2 × slippage_pct + entry_fee + exit_fee = $277 × 2 × 0.0003 + $0.30 + $0.15 ≈ $0.62`. The observed range $0.16-0.24 matches roughly half of that — likely main project's close path includes the exit fee but not the entry fee, or uses a smaller slippage assumption.

### Pattern C — notional divergence (rows 2 and 3)

- Row 2 (MANAUSDT): main `position_size_usd = 184.58`, Shadow `notional_value = 276.95` → ratio 1.50× = **leverage = 3** (main records margin / leverage = 184.58 = 276.95 / 1.5? — actually `notional_value/leverage = 276.95/3 = 92.32` not matching. Most likely `position_size_usd` in main is the apex_final_size adjusted by another factor.)
- Row 3 (AXSUSDT): main `184.62`, Shadow `277.00` → same 1.50× ratio.

The two systems use different notational definitions for "position size".  Main's `trade_intelligence.position_size_usd = apex_final_size` (e.g., 184.58, 184.62 — these are explicitly stored in the row's `apex_final_size` column too). Shadow's `notional_value = qty * fill_price`. They're computing different quantities.

### Pattern D — qty alignment

Quantities match exactly (`quantity = qty` at trade-coordinator level). E.g.:
- ONDOUSDT: Shadow qty=1025.0 — same as the main-project order request.
- MANAUSDT: 3092.8 — same.
- DOGEUSDT (Sell): 4185.0 — same.

Quantity is the safe joining key. Entry / exit prices are NOT.

## T.1.3 — Telegram /performance reconciliation

Could not be captured live. The expected reconciliation:

- `Today's PnL` (per /performance via `DailyPnLManager.current_pnl_pct/usd`) is fed by Shadow's `total_realized_pnl` (from `account_service.get_wallet_balance() → ShadowAccountService → /api/balance`).
- Sum of `trade_intelligence.pnl_usd` over today's 8 closes = `−0.288 + −0.145 + −0.063 + −0.601 + −0.269 + −0.126 + −0.052 + −1.452 = −2.996`
- Sum of Shadow `virtual_positions.net_pnl_usd` over the same 8 = `−0.523 + −0.380 + −0.278 + −0.601 + −0.426 + −0.345 + −0.052 + −1.452 = −4.057`
- **Δ = +$1.06** on these 8 trades alone

So if `/performance` reports total realized loss based on Shadow's `total_realized_pnl`, it shows ~$4 lost; if it sums main's `trade_intelligence.pnl_usd` it shows ~$3 lost. Same trades, two different totals. **A $1+ daily-PnL gap on 8 trades** is the operator-visible symptom.

Note: Shadow's `virtual_wallet.total_realized_pnl = -2322.05` represents the lifetime sum (not just today). The same per-trade divergence pattern accumulates across 1190 trades — so Shadow's lifetime realized PnL and any main-project lifetime sum will be off by an unbounded factor (depends how many of the 1190 went through divergent close paths).
