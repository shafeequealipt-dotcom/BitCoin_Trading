"""Complete exception hierarchy for the Trading Intelligence MCP system.

Every exception carries a message, optional details dict, and auto-set timestamp.
All exceptions inherit from TradingMCPError for unified error handling.
"""

from datetime import datetime, timezone


class TradingMCPError(Exception):
    """Base exception for all Trading Intelligence MCP errors."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now(timezone.utc)
        super().__init__(message)

    def __str__(self) -> str:
        base = f"[{self.timestamp.isoformat()}] {self.__class__.__name__}: {self.message}"
        if self.details:
            base += f" | details={self.details}"
        return base


# --- Config ---

class ConfigError(TradingMCPError):
    """Configuration loading or validation error."""


# --- Auth ---

class AuthenticationError(TradingMCPError):
    """API authentication failure."""


# --- Trading ---

class TradingError(TradingMCPError):
    """Base for all trading-related errors."""


class OrderError(TradingError):
    """Error placing or managing an order."""


class InsufficientBalanceError(OrderError):
    """Account balance too low for the requested order."""


class InvalidOrderError(OrderError):
    """Order parameters are invalid (bad qty, price, symbol)."""


class OrderRejectedError(OrderError):
    """Exchange rejected the order."""


class DuplicateOrderLinkIdError(OrderError):
    """Exchange rejected an order because its orderLinkId already exists.

    This is a desirable failure mode under idempotent retry: it confirms a
    prior attempt with the same client-generated ``orderLinkId`` was already
    accepted by the exchange. Callers should recover the canonical order
    via ``get_open_orders``/``get_order_history`` and treat the operation
    as successful rather than re-issuing the order.
    """


class Layer3DisabledError(OrderError):
    """OrderService refused a ``layer3_entry`` while Layer 3 is OFF.

    Phase 2 (Layer 3 enforcement). Layer 4 actions (``layer4_close``,
    ``layer4_sl``) intentionally bypass this gate — they manage existing
    positions and remain active even when new entries are paused. Only
    ``purpose="layer3_entry"`` (and operator-facing entries via
    ``telegram_manual`` / ``mcp_tool`` without ``force=True``) are gated.
    """


class Layer3RaceError(OrderError):
    """Layer 3 state changed between LayerSnapshot capture and OrderService entry.

    Phase 2 (Layer 3 enforcement). The capture-and-pass pattern protects
    against a Claude directive being dispatched while L3 was on, then L3
    flipping off mid-call (operator emergency stop, watchdog cascade).
    Re-checking at OrderService entry against the captured snapshot
    catches the race and aborts the placement.
    """


class Layer3BootNotReadyError(OrderError):
    """OrderService rejected an entry-side placement because the LayerManager
    has not been attached yet (boot ordering window).

    Phase 1 (post-Layer-1 fix). The original gate logged a single warning
    and proceeded for ALL purposes when ``layer_manager is None`` —
    including ``layer3_entry``, ``telegram_manual``, and ``mcp_tool`` for
    which there is no legitimate pre-attach call site. Layer 4 management
    purposes (``layer4_close``, ``layer4_sl``) continue to bypass the
    gate during boot — they MUST execute even pre-attach to keep stop-loss
    and emergency-close paths active. See
    ``dev_notes/phase0_post_layer1_fixes/issue_2_fail_open_gate.md``.
    """


class PositionError(TradingError):
    """Error managing a position."""


class ClosingInProgressError(PositionError):
    """A close for this symbol is already in flight.

    Raised by ``_PositionProxy.close_position`` (the single close chokepoint
    every mode routes through; the in-flight set is owned by the Transformer)
    when a second cutter tries to close a symbol that is already being closed in
    an overlapping async slice (e.g. the loss-cutting cap force-close racing the
    watchdog hard-stop). It subclasses :class:`PositionError` so existing
    ``except PositionError`` / ``except Exception`` close handlers treat it as
    "could not close this tick" and skip booking — the coordinator's
    ``on_trade_closed`` double-close guard remains the booking-side backstop.
    Deliberately NOT retried.
    """


class RateLimitError(TradingError):
    """API rate limit exceeded."""


# --- Data ---

class DataError(TradingMCPError):
    """Base for data-related errors."""


class MarketDataError(DataError):
    """Error fetching or processing market data."""


class DatabaseError(DataError):
    """Database query or connection error."""


class APIError(DataError):
    """Base for external API errors."""


class BybitAPIError(APIError):
    """Bybit API returned an error response."""


class GroundTruthUnavailableError(APIError):
    """Raised by exchange adapters when an unrecoverable API failure
    prevents confirmation of exchange state (open positions, wallet
    balance, etc.).

    Issue I1 of the five-critical-fixes series (F-26 TIMESTAMP_FAIL):
    distinguishes "exchange confirms zero positions" (a valid empty
    state) from "exchange did not respond conclusively, state unknown"
    (which must NOT be interpreted as zero). Callers — primarily the
    position watchdog — catch this exception and preserve their
    last-known state instead of running the close-detection pass.

    This is a typed sentinel exception, NOT a generic API failure:
    only paths where downstream phantom-action is a real risk should
    raise it. Generic API failures continue to return list/None
    sentinels per the existing adapter contract.

    Per-adapter callers MUST catch this exception explicitly:

        try:
            positions = await self.position_service.get_positions()
        except GroundTruthUnavailableError:
            log.warning("WD_GROUND_TRUTH_UNKNOWN | ...")
            return  # skip close-detection for this tick
    """


class FinnhubError(APIError):
    """Finnhub API error."""


class RedditError(APIError):
    """Reddit/PRAW API error."""


# --- Intelligence ---

class IntelligenceError(TradingMCPError):
    """Base for analysis and intelligence errors."""


class SentimentError(IntelligenceError):
    """Error in sentiment analysis."""


class SignalError(IntelligenceError):
    """Error generating or processing a trading signal."""


# --- Workers ---

class WorkerError(TradingMCPError):
    """Base for background worker errors."""


class WorkerStartError(WorkerError):
    """Worker failed to start."""


class WorkerCrashError(WorkerError):
    """Worker crashed during operation."""


# --- Brain ---

class BrainError(TradingMCPError):
    """Base for Claude Brain errors."""


class ClaudeAPIError(BrainError):
    """Claude API call failed."""


class CredentialRefreshError(BrainError):
    """OAuth credential refresh failed inside the pre-flight margin.

    Phase 3 (Brain credentials). Raised by ``ClaudeCodeClient.call`` when
    the access token expires within ``credential_refresh_margin_seconds``
    AND ``_try_token_refresh`` exhausts its retry budget without
    refreshing. The call is aborted before spawning the subprocess to
    prevent the silent 300-s hang observed when the CLI tries to refresh
    on-demand mid-call.
    """


class DecisionParseError(BrainError):
    """Could not parse Claude's trading decision."""


class ExecutionError(BrainError):
    """Error executing a brain-generated trading decision."""


# --- Risk ---

class RiskError(TradingMCPError):
    """Base for risk management errors."""


class RiskLimitExceededError(RiskError):
    """A risk limit was breached (position size, exposure, etc.)."""


class MaxDrawdownError(RiskError):
    """Maximum drawdown threshold exceeded — emergency stop."""


class DailyLossLimitError(RiskError):
    """Daily loss limit hit — trading halted for the day."""
