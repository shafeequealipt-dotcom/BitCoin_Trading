# Phase 0 — Bybit Demo Account Setup (Operator-Side)

**Status:** Operator action required.

This document captures the operator-side prerequisites for Phase 2 (adapter implementation) and Phase 6 (end-to-end testing). No code work is blocked on this until Phase 2.B integration tests; Phase 1 / 2.A / 2.B unit tests run without these credentials.

## Steps For The Operator

### 1. Bybit account
Existing Bybit account works. If new: register at bybit.com.

### 2. Enable Demo Trading
Bybit UI → Account → Demo Trading toggle ON. Verify a virtual balance is shown (typically Bybit grants $100k–$1M test USDT).

### 3. Generate Demo API Key
Bybit UI → API Management → "Create New Key" with **Demo Trading** scope.
- Permissions required: Read + Trade (NOT Withdraw).
- **CRITICAL:** Demo keys are SEPARATE from live keys. Generate them while Demo Trading is the active environment.

### 4. Store Credentials
Add to `.env` at `/home/inshadaliqbal786/trading-intelligence-mcp/.env`:
```
BYBIT_DEMO_API_KEY=<your demo api key>
BYBIT_DEMO_API_SECRET=<your demo api secret>
```
The adapter reads these via env-var resolution in `BybitDemoSettings`. **Never commit the `.env` file.**

### 5. Manual API Verification (curl)
Before Phase 2 integration tests, verify the demo API works manually:

```bash
# Set creds
export API_KEY="<your demo key>"
export API_SECRET="<your demo secret>"
export RECV_WINDOW=5000
export BASE_URL="https://api-demo.bybit.com"

# Get wallet balance (account?accountType=UNIFIED)
TS=$(date +%s%3N)
QS="accountType=UNIFIED"
SIGN_PAYLOAD="${TS}${API_KEY}${RECV_WINDOW}${QS}"
SIGN=$(echo -n "$SIGN_PAYLOAD" | openssl dgst -sha256 -hmac "$API_SECRET" -hex | awk '{print $NF}')
curl -s "${BASE_URL}/v5/account/wallet-balance?${QS}" \
  -H "X-BAPI-API-KEY: $API_KEY" \
  -H "X-BAPI-TIMESTAMP: $TS" \
  -H "X-BAPI-SIGN: $SIGN" \
  -H "X-BAPI-RECV-WINDOW: $RECV_WINDOW"
```

Verify each of these works:
- `GET /v5/account/wallet-balance?accountType=UNIFIED` → returns `total_equity`.
- `GET /v5/position/list?category=linear` → returns positions array.
- `POST /v5/order/create` (small BTCUSDT linear test order, qty=0.001).
- `GET /v5/order/realtime?category=linear&symbol=BTCUSDT` → confirm placed.
- `POST /v5/order/cancel` → cancel the test order.

### 6. Document Outcomes
Record any anomalies in `phase0_api_exploration.md`:
- Actual response shapes (any fields differing from V5 docs).
- Rate-limit headers observed (`X-Bapi-Limit-Status`, `X-Bapi-Limit`, `X-Bapi-Limit-Reset-Timestamp`).
- Error responses encountered + their numeric codes.

## API Reference

- Base URL: `https://api-demo.bybit.com`
- V5 API docs: https://bybit-exchange.github.io/docs/v5/intro
- Authentication: HMAC-SHA256 with `X-BAPI-API-KEY`, `X-BAPI-TIMESTAMP`, `X-BAPI-SIGN`, `X-BAPI-RECV-WINDOW` headers.
- Sign payload: `timestamp + api_key + recv_window + (body or query_string)`.
- Demo trading specifics: https://bybit-exchange.github.io/docs/v5/demo
