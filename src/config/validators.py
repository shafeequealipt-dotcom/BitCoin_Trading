"""Configuration validation: checks all settings for sanity before system startup.

Returns warnings for non-critical issues, raises ConfigError for blockers.
"""

import os
from typing import Any

from src.config.settings import Settings
from src.core.exceptions import ConfigError
from src.core.modes import ALL_VALID_MODES, MODE_BYBIT_DEMO, MODE_LIVE


def validate_config(settings: Settings) -> list[str]:
    """Validate the entire configuration and return warnings.

    Args:
        settings: Fully loaded Settings instance.

    Returns:
        List of warning messages (non-fatal issues).

    Raises:
        ConfigError: If a critical configuration error is found.
    """
    warnings: list[str] = []

    # --- General ---
    _validate_mode(settings, warnings)

    # --- Risk ---
    _validate_risk(settings, warnings)

    # --- Adaptive exit (Dynamic Adaptive Exit System) ---
    _validate_adaptive_exit(settings, warnings)

    # --- SL gateway (rate-limit / profit-lock cadence lever) ---
    _validate_sl_gateway(settings, warnings)

    # --- API Keys ---
    _validate_api_keys(settings, warnings)

    # --- File Paths ---
    _validate_paths(settings, warnings)

    # --- MCP ---
    _validate_mcp(settings, warnings)

    # --- Cross-section consistency ---
    _validate_consistency(settings, warnings)

    return warnings


def _validate_sl_gateway(settings: Settings, warnings: list[str]) -> None:
    """Validate the SL-gateway profit-lock cadence lever (Stage B Phase 1).

    The lever is clamped to ``<= rate_limit_seconds`` at read time, so a value
    above the base is a harmless no-op (warned). Zero/negative is a config error.
    Below the 5s sniper tick buys no new decisions and only risks redundant wire
    writes (warned). Default 30 (== base) is inert.
    """
    slg = getattr(settings, "sl_gateway", None)
    if slg is None:
        return
    base = getattr(slg, "rate_limit_seconds", 30)
    plrl = getattr(slg, "profit_lock_rate_limit_seconds", base)
    if plrl <= 0:
        raise ConfigError(
            f"sl_gateway.profit_lock_rate_limit_seconds must be > 0, got {plrl}"
        )
    if plrl > base:
        warnings.append(
            f"sl_gateway.profit_lock_rate_limit_seconds={plrl}s exceeds "
            f"rate_limit_seconds={base}s — clamped to the base window at read "
            f"time (the profit-lock lane can only go faster, never slower), so "
            f"this setting is a no-op."
        )
    elif 0 < plrl < 5:
        warnings.append(
            f"sl_gateway.profit_lock_rate_limit_seconds={plrl}s is below the 5s "
            f"sniper tick floor — no new placement decisions are produced faster "
            f"than the tick, so this only risks redundant exchange writes."
        )


def _validate_mode(settings: Settings, warnings: list[str]) -> None:
    """Validate trading mode."""
    mode = settings.general.mode
    if mode not in ALL_VALID_MODES:
        raise ConfigError(
            f"Invalid trading mode: '{mode}'. Must be one of {ALL_VALID_MODES}.",
            details={"mode": mode},
        )
    if mode == MODE_LIVE:
        warnings.append(
            "LIVE TRADING MODE ENABLED — real money at risk. "
            "Ensure all API keys are for mainnet."
        )
    if mode == MODE_LIVE and settings.bybit.testnet:
        warnings.append(
            "Mode is 'live' but Bybit testnet is enabled. "
            "This is contradictory — set bybit.testnet = false for live trading."
        )
    if mode == MODE_BYBIT_DEMO:
        if not settings.bybit_demo.enabled:
            warnings.append(
                "Mode is 'bybit_demo' but [bybit_demo].enabled is False. "
                "Set [bybit_demo].enabled = true and provide BYBIT_DEMO_API_KEY "
                "/ BYBIT_DEMO_API_SECRET in .env."
            )
        if not settings.bybit_demo.api_key or not settings.bybit_demo.api_secret:
            warnings.append(
                "Mode is 'bybit_demo' but credentials are missing. "
                "Set BYBIT_DEMO_API_KEY and BYBIT_DEMO_API_SECRET in .env."
            )


def _validate_risk(settings: Settings, warnings: list[str]) -> None:
    """Validate risk management parameters."""
    risk = settings.risk

    if risk.max_leverage < 1 or risk.max_leverage > 100:
        raise ConfigError(
            f"max_leverage must be between 1 and 100, got {risk.max_leverage}",
            details={"max_leverage": risk.max_leverage},
        )

    if not risk.mandatory_stop_loss:
        raise ConfigError(
            "mandatory_stop_loss cannot be disabled. This is a safety requirement.",
        )

    if risk.default_stop_loss_pct <= 0 or risk.default_stop_loss_pct > 50:
        raise ConfigError(
            f"default_stop_loss_pct must be between 0 and 50, got {risk.default_stop_loss_pct}",
        )
    if risk.default_stop_loss_pct > 10:
        warnings.append(
            f"default_stop_loss_pct={risk.default_stop_loss_pct}% is high. "
            f"Recommended range: 0.5% to 10%."
        )

    if risk.daily_loss_limit_pct <= 0 or risk.daily_loss_limit_pct > 100:
        raise ConfigError(
            f"daily_loss_limit_pct must be between 0 and 100, got {risk.daily_loss_limit_pct}",
        )

    if risk.max_position_size_pct <= 0 or risk.max_position_size_pct > 100:
        raise ConfigError(
            f"max_position_size_pct must be between 0 and 100, got {risk.max_position_size_pct}",
        )

    if risk.max_drawdown_pct <= 0 or risk.max_drawdown_pct > 100:
        raise ConfigError(
            f"max_drawdown_pct must be between 0 and 100, got {risk.max_drawdown_pct}",
        )

    if risk.max_total_exposure_pct <= 0 or risk.max_total_exposure_pct > 100:
        raise ConfigError(
            f"max_total_exposure_pct must be between 0 and 100, got {risk.max_total_exposure_pct}",
        )


def _validate_adaptive_exit(settings: Settings, warnings: list[str]) -> None:
    """Validate the Dynamic Adaptive Exit coefficients.

    Blockers (ConfigError) catch values that would break the bounded-formula
    contract; soft issues become warnings. All checks pass for the dormant
    default, so landing the section changes nothing.
    """
    ae = getattr(settings, "adaptive_exit", None)
    if ae is None:
        return

    if ae.round_trip_fee_pct <= 0 or ae.round_trip_fee_pct > 5:
        raise ConfigError(
            f"adaptive_exit.round_trip_fee_pct must be in (0, 5], got {ae.round_trip_fee_pct}",
        )
    if ae.fee_floor_buffer < 1.0:
        warnings.append(
            f"adaptive_exit.fee_floor_buffer={ae.fee_floor_buffer} is below 1.0 — "
            "a locked 'win' could fail to clear the round-trip fee."
        )
    for name in ("arm_r", "trail_r", "hard_stop_r"):
        val = float(getattr(ae, name, 0.0))
        if val <= 0:
            raise ConfigError(
                f"adaptive_exit.{name} must be positive, got {val}",
            )
    # Profit-scaled trail tightening (2026-06-26 give-back fix). The floor must be
    # a positive fraction no larger than trail_r so the effective coefficient stays
    # in (0, trail_r] — it can only tighten the trail, never widen it, and always
    # keeps a trail_r_floor×R buffer below the peak. floor == trail_r is the inert
    # ship default. The decay scale must be positive (it divides); the knee is a
    # non-negative R offset.
    trail_r_val = float(getattr(ae, "trail_r", 0.5))
    trail_r_floor = float(getattr(ae, "trail_r_floor", trail_r_val))
    if not (0.0 < trail_r_floor <= trail_r_val):
        raise ConfigError(
            f"adaptive_exit.trail_r_floor must be in (0, trail_r={trail_r_val}], "
            f"got {trail_r_floor}",
        )
    scale_r = float(getattr(ae, "trail_tighten_scale_r", 1.0))
    if scale_r <= 0:
        raise ConfigError(
            f"adaptive_exit.trail_tighten_scale_r must be positive, got {scale_r}",
        )
    knee_r = float(getattr(ae, "trail_tighten_knee_r", 1.0))
    if knee_r < 0:
        raise ConfigError(
            f"adaptive_exit.trail_tighten_knee_r must be >= 0, got {knee_r}",
        )
    alpha = float(getattr(ae, "r_smoothing_alpha", 0.3))
    if not (0.0 < alpha <= 1.0):
        raise ConfigError(
            f"adaptive_exit.r_smoothing_alpha must be in (0, 1] (1.0 = no "
            f"smoothing), got {alpha}",
        )
    rungs = list(getattr(ae, "rung_r", []) or [])
    if len(rungs) < 1:
        raise ConfigError("adaptive_exit.rung_r must list at least one rung multiple.")
    if any(b <= a for a, b in zip(rungs, rungs[1:])):
        raise ConfigError(
            f"adaptive_exit.rung_r must be strictly ascending, got {rungs}",
        )
    if not (0.0 < ae.dead_drifter_age_fraction < 1.0):
        raise ConfigError(
            f"adaptive_exit.dead_drifter_age_fraction must be in (0, 1), "
            f"got {ae.dead_drifter_age_fraction}",
        )
    # Cross-section: the hard stop is a BACKSTOP that must sit at/above (wider
    # than) the sacred cap so the cap stays the operative catastrophic floor and
    # fires first. Warn if the hard-stop FLOOR is tighter than the young cap,
    # because then the watchdog hard stop could preempt the cap on a quiet coin.
    cap_young = float(getattr(getattr(settings, "loss_cutting", None),
                              "cap_pct_of_notional_young", 2.5))
    if ae.hard_stop_min_pct < cap_young:
        warnings.append(
            f"adaptive_exit.hard_stop_min_pct={ae.hard_stop_min_pct}% is below the "
            f"sacred cap ({cap_young}% young) — the watchdog hard stop could fire "
            "before the cap on a quiet coin. Floor it at/above the cap."
        )


def _validate_api_keys(settings: Settings, warnings: list[str]) -> None:
    """Check that API keys are present for enabled features."""
    # Bybit always needed
    if not settings.bybit.api_key or not settings.bybit.api_secret:
        warnings.append(
            "Bybit API key/secret not set. Trading will not work. "
            "Set BYBIT_API_KEY and BYBIT_API_SECRET in .env"
        )

    # Finnhub
    if settings.finnhub.enabled and not settings.finnhub.api_key:
        warnings.append(
            "Finnhub is enabled but FINNHUB_API_KEY is not set. "
            "News features will not work."
        )

    # Reddit
    if settings.reddit.enabled:
        missing_reddit: list[str] = []
        if not settings.reddit.client_id:
            missing_reddit.append("REDDIT_CLIENT_ID")
        if not settings.reddit.client_secret:
            missing_reddit.append("REDDIT_CLIENT_SECRET")
        if missing_reddit:
            warnings.append(
                f"Reddit is enabled but missing: {', '.join(missing_reddit)}. "
                f"Sentiment analysis will not work."
            )

    # Brain / Claude
    if settings.brain.enabled and not settings.brain.api_key:
        warnings.append(
            "Brain is enabled but ANTHROPIC_API_KEY is not set. "
            "Claude Brain will not work."
        )

    # Telegram
    if settings.alerts.telegram_enabled:
        if not settings.alerts.bot_token or not settings.alerts.chat_id:
            warnings.append(
                "Telegram alerts enabled but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing."
            )


def _validate_paths(settings: Settings, warnings: list[str]) -> None:
    """Validate that required directories are writable."""
    log_dir = settings.general.log_dir
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError as e:
        raise ConfigError(
            f"Cannot create log directory '{log_dir}': {e}",
            details={"log_dir": log_dir},
        )

    db_dir = os.path.dirname(settings.database.path)
    if db_dir:
        try:
            os.makedirs(db_dir, exist_ok=True)
        except OSError as e:
            raise ConfigError(
                f"Cannot create database directory '{db_dir}': {e}",
                details={"db_dir": db_dir},
            )


def _validate_mcp(settings: Settings, warnings: list[str]) -> None:
    """Validate MCP transport settings."""
    transport = settings.mcp.transport
    if transport not in ("stdio", "sse"):
        raise ConfigError(
            f"Invalid MCP transport: '{transport}'. Must be 'stdio' or 'sse'.",
            details={"transport": transport},
        )

    if transport == "sse":
        port = settings.mcp.sse_port
        if port < 1 or port > 65535:
            raise ConfigError(
                f"Invalid SSE port: {port}. Must be 1-65535.",
                details={"port": port},
            )
        if settings.mcp.sse_auth_required and not settings.mcp.auth_token:
            warnings.append(
                "SSE transport with auth required but MCP_AUTH_TOKEN is not set."
            )


def _validate_consistency(settings: Settings, warnings: list[str]) -> None:
    """Cross-section consistency checks."""
    # Portfolio allocations should sum to ~100%
    if hasattr(settings, "portfolio"):
        p = settings.portfolio
        total = (
            p.proven_strategies_budget_pct
            + p.ai_strategies_budget_pct
            + p.trial_strategies_budget_pct
            + p.cash_reserve_pct
        )
        if abs(total - 100) > 2:
            warnings.append(
                f"Portfolio allocations sum to {total:.1f}%, expected ~100%"
            )

    # Strategy scan interval vs data interval
    if settings.strategy_engine.scan_interval_seconds < settings.workers.market_data_interval:
        warnings.append(
            "Strategy scans faster than market data updates — may use stale data"
        )

    # Brain cost estimate
    brain = settings.brain
    estimated_daily = brain.max_calls_per_hour * 0.004 * 24
    if estimated_daily > 5.0:
        warnings.append(
            f"Brain max calls/hour ({brain.max_calls_per_hour}) could cost ${estimated_daily:.2f}/day"
        )
