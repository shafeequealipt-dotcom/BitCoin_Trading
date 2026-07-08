# Phase 1.6 — Telegram Dashboard Anatomy

Investigation content lives in **[`phase1_synthesis.md`](phase1_synthesis.md) Section 6**.

## What's covered there

- `_build_keyboard` button-rendering pattern (lines 550-651 of `dashboard_handler.py`)
- Existing live-bybit / shadow switch buttons (lines 578-594) — preserved untouched
- Confirmation handlers at lines 1490-1547 calling `transformer.switch_to()` directly
- Pattern matching at line 2473: `^dash_` regex catches all `dash_*` callbacks (including new ones)
- Accessibility convention at lines 1-17: operator is blind, screen reader, full-word labels required
- Bot architecture: `InteractiveTelegramBot` at `src/telegram/bot.py:33`, runs as worker task within trading-workers systemd unit
- Phase 5 additions: new "Switch to Bybit Demo" button + 4 new callbacks + Exchange status line

See `src/telegram/handlers/dashboard_handler.py` and `src/telegram/bot.py`.
