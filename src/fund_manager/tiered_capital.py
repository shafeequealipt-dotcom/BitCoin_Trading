"""Tiered Capital Manager — progressive trust system.

Tier 1: equity < 2x starting -> 20% usable (conservative)
Tier 2: equity 2x-4x starting -> 30% usable (proven growth)
Tier 3: equity > 4x starting -> 40% usable (strong track record)

User can override the percentage via Telegram.
Starting equity is locked and persisted.
"""

from dataclasses import dataclass
from src.core.logging import get_logger
from src.core.log_context import ctx

log = get_logger("tiered_capital")


@dataclass
class FundLimits:
    """Current trading limits based on tier and equity."""
    total_equity: float
    starting_equity: float
    tier: int
    tier_pct: float
    usable_capital: float
    currently_deployed: float
    available_for_trades: float
    max_single_trade: float
    max_positions: int
    user_override_pct: float | None

    def to_prompt_text(self) -> str:
        tier_names = {1: "CONSERVATIVE (unproven)", 2: "MODERATE (proven growth)", 3: "AGGRESSIVE (strong track record)"}
        return (
            f"FUND RULES (non-negotiable):\n"
            f"  Total equity: ${self.total_equity:,.0f}\n"
            f"  Starting equity: ${self.starting_equity:,.0f}\n"
            f"  Growth: {((self.total_equity / self.starting_equity - 1) * 100):+.1f}%\n"
            f"  Tier: {self.tier} — {tier_names.get(self.tier, 'UNKNOWN')}\n"
            f"  Capital allocation: {self.tier_pct * 100:.0f}% of equity\n"
            f"  Usable capital: ${self.usable_capital:,.0f}\n"
            f"  Currently deployed: ${self.currently_deployed:,.0f}\n"
            f"  Available for new trades: ${self.available_for_trades:,.0f}\n"
            f"  Max single trade: ${self.max_single_trade:,.0f}\n"
            f"  Max positions: {self.max_positions}\n"
            f"  Size your trades within available capital."
        )

    def to_telegram_text(self) -> str:
        tier_names = {1: "Tier 1 (20%)", 2: "Tier 2 (30%)", 3: "Tier 3 (40%)"}
        override = f"\nUser override: {int(self.user_override_pct * 100)}%" if self.user_override_pct else ""
        return (
            f"<b>Capital status</b>\n\n"
            f"<b>Equity:</b> ${self.total_equity:,.0f}\n"
            f"<b>Start:</b> ${self.starting_equity:,.0f}\n"
            f"<b>Growth:</b> {((self.total_equity / self.starting_equity - 1) * 100):+.1f}%\n"
            f"<b>Tier:</b> {tier_names.get(self.tier, '?')}\n"
            f"<b>Usable:</b> ${self.usable_capital:,.0f} ({self.tier_pct * 100:.0f}%)\n"
            f"<b>Deployed:</b> ${self.currently_deployed:,.0f}\n"
            f"<b>Available:</b> ${self.available_for_trades:,.0f}\n"
            f"<b>Max trade:</b> ${self.max_single_trade:,.0f}\n"
            f"<b>Max positions:</b> {self.max_positions}"
            f"{override}"
        )


class TieredCapitalManager:
    """Manages the progressive capital tier system."""

    SINGLE_TRADE_PCT = 0.25
    MIN_USABLE = 25.0

    def __init__(self, db, starting_equity: float = 168000.0):
        self.db = db
        self._starting_equity = starting_equity
        self._user_override_pct: float | None = None
        self._last_tier = 0
        self._initialized = False

    async def initialize(self) -> None:
        """Load starting equity from DB. Lock it if not already set."""
        try:
            row = await self.db.fetch_one(
                "SELECT value FROM fund_manager_state WHERE key = 'starting_equity'"
            )
            if row:
                self._starting_equity = float(row["value"])
                log.info("Starting equity loaded: ${eq:,.0f}", eq=self._starting_equity)
            else:
                await self.db.execute(
                    "INSERT OR REPLACE INTO fund_manager_state (key, value) VALUES ('starting_equity', ?)",
                    (str(self._starting_equity),),
                )
                # DatabaseManager auto-commits
                log.info("Starting equity LOCKED: ${eq:,.0f}", eq=self._starting_equity)

            row = await self.db.fetch_one(
                "SELECT value FROM fund_manager_state WHERE key = 'capital_override_pct'"
            )
            if row and row["value"]:
                self._user_override_pct = float(row["value"])
                log.info("User capital override: {pct:.0f}%", pct=self._user_override_pct * 100)

            self._initialized = True
        except Exception as e:
            log.error("Failed to initialize tiered capital: {err}", err=str(e))
            self._initialized = True

    # Sniper-Latency-Size Fix Phase 3C (2026-05-07) — capital tier
    # hysteresis bands. The legacy tier table fired transitions on
    # every equity sample at the boundary (ratio==2.00 or ratio==4.00),
    # so a portfolio fluctuating around a boundary oscillated between
    # tiers within minutes and produced wildly different sizing
    # decisions for the same opportunity. The hysteresis adds a 5%
    # buffer band: promotion requires sustained crossing above the
    # boundary, demotion requires sustained crossing below — and once
    # in a tier the position resists movement until the buffer is
    # crossed in the opposite direction.
    _TIER_PROMOTE_RATIOS: tuple[float, float] = (2.05, 4.10)  # 5% above 2.0/4.0
    _TIER_DEMOTE_RATIOS: tuple[float, float] = (1.95, 3.90)   # 5% below 2.0/4.0

    def _resolve_tier_with_hysteresis(self, ratio: float) -> int:
        """Apply hysteresis bands so tier transitions only fire on
        sustained crossing of the buffered boundary, not on every
        sample at the raw 2.0/4.0 thresholds.

        Behaviour:
        - Cold-start (``self._last_tier == 0``): pick from raw bands so
          the first sample lands in the tier matching current equity.
        - In a tier: only promote when ratio crosses the upper buffer
          (e.g. ratio >= 2.05 to leave Tier 1); only demote when ratio
          crosses the lower buffer (e.g. ratio <= 1.95 to leave Tier 2).
        - When a transition is BLOCKED by the buffer, emit
          ``TIER_TRANSITION_BLOCKED`` so operators can see hysteresis
          in action without it being silent.

        Side effect: updates ``self._last_tier`` so the next sample
        can compare against the freshly-resolved value. (Legacy
        behaviour stored the tier in ``get_limits`` for logging only;
        with hysteresis the resolver itself owns the state because
        ``get_tier`` is the only call that runs in every sizing path.)
        """
        promote_t1_to_t2, promote_t2_to_t3 = self._TIER_PROMOTE_RATIOS
        demote_t2_to_t1, demote_t3_to_t2 = self._TIER_DEMOTE_RATIOS

        if self._last_tier == 0:
            # Cold start — pick directly from raw bands. Subsequent
            # samples will be hysteresis-gated.
            if ratio >= 4.0:
                resolved = 3
            elif ratio >= 2.0:
                resolved = 2
            else:
                resolved = 1
            self._last_tier = resolved
            return resolved

        # Compute what the raw (non-hysteresis) tier WOULD be so we can
        # detect attempted transitions and surface those that the
        # buffer blocks. The raw tier is what the legacy code returned.
        if ratio >= 4.0:
            raw_tier = 3
        elif ratio >= 2.0:
            raw_tier = 2
        else:
            raw_tier = 1

        last = self._last_tier
        new_tier = last  # default: no transition

        if last == 1:
            # Promotion requires ratio >= 2.05 (5% above 2.0).
            if ratio >= promote_t1_to_t2:
                new_tier = 3 if ratio >= promote_t2_to_t3 else 2
        elif last == 2:
            if ratio >= promote_t2_to_t3:
                new_tier = 3
            elif ratio <= demote_t2_to_t1:
                new_tier = 1
        elif last == 3:
            if ratio <= demote_t3_to_t2:
                new_tier = 2 if ratio > demote_t2_to_t1 else 1

        # Surface blocked transitions for observability. The raw tier
        # would have moved but hysteresis kept us where we are.
        if raw_tier != last and new_tier == last:
            log.info(
                f"TIER_TRANSITION_BLOCKED | from={last} to_raw={raw_tier} "
                f"ratio={ratio:.4f} promote_t1t2={promote_t1_to_t2} "
                f"promote_t2t3={promote_t2_to_t3} "
                f"demote_t2t1={demote_t2_to_t1} "
                f"demote_t3t2={demote_t3_to_t2} | {ctx()}"
            )
        # Surface allowed transitions as well so the legacy TIER UP /
        # TIER DOWN log behaviour is preserved (the equivalent log in
        # get_limits is now redundant since the resolver owns the
        # transition decision).
        elif new_tier != last:
            direction = "UP" if new_tier > last else "DOWN"
            log.warning(
                f"TIER {direction} | from={last} to={new_tier} "
                f"ratio={ratio:.4f} | {ctx()}"
            )

        self._last_tier = new_tier
        return new_tier

    def get_tier(self, current_equity: float) -> tuple[int, float, int]:
        if self._user_override_pct is not None:
            pct = self._user_override_pct
            max_pos = max(2, int(pct * 20))
            tier = 1 if current_equity < self._starting_equity * 2 else (
                   2 if current_equity < self._starting_equity * 4 else 3)
            return tier, pct, max_pos

        ratio = current_equity / self._starting_equity if self._starting_equity > 0 else 1.0
        # Phase 3C — hysteresis-aware tier resolution. Replaces the
        # raw if/elif/else table so transitions don't oscillate at
        # boundaries.
        tier = self._resolve_tier_with_hysteresis(ratio)
        if tier == 3:
            return 3, 0.40, 8
        if tier == 2:
            return 2, 0.30, 6
        return 1, 0.20, 4

    def get_limits(self, current_equity: float, currently_deployed: float = 0.0) -> FundLimits:
        tier, pct, max_pos = self.get_tier(current_equity)
        usable = max(self.MIN_USABLE, current_equity * pct)
        available = max(0, usable - currently_deployed)
        max_single = usable * self.SINGLE_TRADE_PCT

        # Phase 3C (2026-05-07) — TIER UP/DOWN logging now lives in
        # ``_resolve_tier_with_hysteresis`` so transitions are logged
        # exactly once per actual change rather than every sample.
        # ``self._last_tier`` is also owned by the resolver.
        tier_names = {1: "CONSERVATIVE", 2: "MODERATE", 3: "AGGRESSIVE"}
        log.info(f"CAPITAL_TIER | eq={current_equity:.2f} | tier={tier_names.get(tier, 'UNKNOWN')} | alloc={pct * 100:.0f}% | max_single_trade={max_single:.2f} | {ctx()}")

        return FundLimits(
            total_equity=current_equity,
            starting_equity=self._starting_equity,
            tier=tier, tier_pct=pct,
            usable_capital=usable,
            currently_deployed=currently_deployed,
            available_for_trades=available,
            max_single_trade=max_single,
            max_positions=max_pos,
            user_override_pct=self._user_override_pct,
        )

    async def set_user_override(self, pct: float | None) -> None:
        if pct is not None:
            pct = max(0.10, min(0.50, pct))
        self._user_override_pct = pct
        try:
            if pct is not None:
                await self.db.execute(
                    "INSERT OR REPLACE INTO fund_manager_state (key, value) VALUES ('capital_override_pct', ?)",
                    (str(pct),),
                )
            else:
                await self.db.execute(
                    "DELETE FROM fund_manager_state WHERE key = 'capital_override_pct'"
                )
            await self.db.commit()
        except Exception as e:
            log.error("Failed to save capital override: {err}", err=str(e))

    def validate_trade_size(self, trade_size_usd: float, current_equity: float,
                            currently_deployed: float = 0.0) -> tuple[bool, float, str]:
        limits = self.get_limits(current_equity, currently_deployed)
        if trade_size_usd > limits.available_for_trades:
            adjusted = limits.available_for_trades
            return True, adjusted, f"Capped from ${trade_size_usd:.0f} to ${adjusted:.0f} (available limit)"
        if trade_size_usd > limits.max_single_trade:
            adjusted = limits.max_single_trade
            return True, adjusted, f"Capped from ${trade_size_usd:.0f} to ${adjusted:.0f} (max single trade)"
        if trade_size_usd <= 0:
            return False, 0, "Trade size is zero or negative"
        return True, trade_size_usd, "within limits"
