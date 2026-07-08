"""Observability orchestration — sinks/relays that subscribe to log events.

Modules here register loguru sinks at boot to translate structured log
tags into out-of-band signals (Telegram alerts, future Slack/PagerDuty).
The adapter and orchestrator code stays log-only; this layer turns
operationally significant events into operator-visible alerts.
"""

from src.observability.bybit_demo_alert_relay import BybitDemoAlertRelay

__all__ = ["BybitDemoAlertRelay"]
