"""SENTINEL Portfolio Advisor — DeepSeek V3 portfolio risk assessment.

Every 5 minutes (offset from Claude's review cycle), DeepSeek V3 assesses
portfolio risk and recommends stop tightening. NEVER closes positions.

Recommendations are stored in-memory and consumed by the Watchdog on its
next tick to execute SL adjustments.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from src.core.logging import get_logger
from src.core.log_context import ctx
from src.tias.deepseek_client import DeepSeekClient, TIASAnalysisError

log = get_logger("sentinel")


ADVISOR_SYSTEM_PROMPT = """You are a portfolio risk advisor for a crypto futures trading system.
You are given the current portfolio state with all open positions and their PnL.
Your job is to recommend stop-loss tightening to protect profits and limit losses.

You CANNOT close positions. You can ONLY recommend tightening stop-losses.

For each position, analyze:
1. Is the current SL appropriate for the market conditions?
2. Should profits be protected by moving SL closer?
3. Is there a risk of reversal that warrants tighter stops?

Respond with a single valid JSON object:
{
  "portfolio_risk": "low|medium|high|critical",
  "assessment": "1-2 sentence summary of portfolio risk state",
  "recommendations": [
    {
      "symbol": "BTCUSDT",
      "action": "tighten_stop",
      "new_sl_pct_from_entry": 1.0,
      "urgency": "low|medium|high",
      "reason": "why this SL change"
    }
  ]
}

Rules:
- Only recommend tightening (moving SL closer to current price), never widening
- For profitable positions: recommend protecting at least 50% of unrealized profit
- For losing positions: only recommend tightening if loss exceeds -1%
- Empty recommendations array is fine if portfolio is healthy
- Be conservative: only recommend changes with clear reasoning
- Do NOT recommend "tighten_stop" unless the position has at least 0.50% unrealized profit
- A position with less than 0.50% profit is NOT "in profit" — that is within normal spread/noise
- Tightening stops on tiny gains guarantees the trade gets stopped out by market noise
- Only recommend tightening when there is MEANINGFUL profit to protect (>= 0.50%)
- For positions near breakeven (0% to 0.50%): leave the original SL intact — it needs room to work"""


@dataclass
class AdvisorRecommendation:
    """One stop-tightening recommendation from DeepSeek."""
    symbol: str
    new_sl_pct_from_entry: float
    urgency: str = "low"
    reason: str = ""


@dataclass
class AdvisorReport:
    """Full advisor report from one DeepSeek call."""
    portfolio_risk: str = "unknown"
    assessment: str = ""
    recommendations: list[AdvisorRecommendation] = field(default_factory=list)
    generated_at: float = 0.0
    response_time_ms: int = 0
    cost_usd: float = 0.0


class PortfolioAdvisor:
    """DeepSeek-powered portfolio risk advisor.

    Responsibilities:
    - Assess overall portfolio risk level
    - Recommend stop-loss tightening for individual positions
    - Track cost of DeepSeek calls

    NOT responsible for:
    - Closing positions (Watchdog/ProfitSniper/SL_TP handle this)
    - Opening positions (Claude strategic review handles this)
    - Individual trade analysis (TIAS handles this)
    """

    def __init__(self, client: DeepSeekClient, settings: Any) -> None:
        self._client = client
        self._settings = settings
        self._last_report: Optional[AdvisorReport] = None
        self._pending_recommendations: list[AdvisorRecommendation] = []
        self._total_calls: int = 0
        self._total_cost_usd: float = 0.0

    async def assess(self, portfolio_context: str) -> AdvisorReport:
        """Call DeepSeek with portfolio state, return recommendations.

        Args:
            portfolio_context: Formatted string with all position details.

        Returns:
            AdvisorReport with risk assessment and tightening recommendations.
        """
        # Phase 9: per-step timing (observability only — no behaviour change)
        _t_assess = time.time()
        _deepseek_ms = 0.0
        _parse_ms = 0.0
        try:
            _t = time.time()
            response = await self._client.analyze(
                system_prompt=ADVISOR_SYSTEM_PROMPT,
                user_prompt=portfolio_context,
                model=self._settings.advisor_model,
                temperature=self._settings.advisor_temperature,
                max_tokens=self._settings.advisor_max_tokens,
                timeout_seconds=self._settings.advisor_timeout_seconds,
            )
            _deepseek_ms = (time.time() - _t) * 1000

            _t = time.time()
            report = self._parse_response(response)
            _parse_ms = (time.time() - _t) * 1000

            self._last_report = report
            self._pending_recommendations = list(report.recommendations)
            self._total_calls += 1
            self._total_cost_usd += report.cost_usd

            _assess_el_ms = (time.time() - _t_assess) * 1000
            log.info(
                f"SENTINEL_ADVISOR | risk={report.portfolio_risk} "
                f"recs={len(report.recommendations)} "
                f"ms={report.response_time_ms} "
                f"cost=${report.cost_usd:.4f} "
                f"el={_assess_el_ms:.0f}ms deepseek={_deepseek_ms:.0f}ms parse={_parse_ms:.0f}ms | {ctx()}"
            )
            if _assess_el_ms > 10000:
                log.warning(
                    f"SENTINEL_ADVISOR_SLOW | el={_assess_el_ms:.0f}ms deepseek={_deepseek_ms:.0f}ms | {ctx()}"
                )
            return report

        except TIASAnalysisError as e:
            log.warning(
                f"SENTINEL_ADVISOR_FAIL | retryable={e.retryable} "
                f"err='{str(e)[:500]}' | {ctx()}"
            )
            return AdvisorReport(
                portfolio_risk="unknown",
                assessment=f"DeepSeek analysis failed: {str(e)[:100]}",
                generated_at=time.time(),
            )
        except Exception as e:
            log.error(f"SENTINEL_ADVISOR_ERR | err='{str(e)[:500]}' | {ctx()}")
            return AdvisorReport(
                portfolio_risk="unknown",
                assessment=f"Unexpected error: {str(e)[:100]}",
                generated_at=time.time(),
            )

    def _parse_response(self, response: Any) -> AdvisorReport:
        """Parse DeepSeek response into AdvisorReport."""
        data = response.content if response.content else {}  # DeepSeekResponse.content is a dict

        recs: list[AdvisorRecommendation] = []
        for r in data.get("recommendations", []):
            if r.get("action") == "tighten_stop" and r.get("symbol"):
                recs.append(AdvisorRecommendation(
                    symbol=r["symbol"],
                    new_sl_pct_from_entry=float(r.get("new_sl_pct_from_entry", 0)),
                    urgency=r.get("urgency", "low"),
                    reason=r.get("reason", "")[:200],
                ))

        return AdvisorReport(
            portfolio_risk=data.get("portfolio_risk", "unknown"),
            assessment=data.get("assessment", "")[:300],
            recommendations=recs,
            generated_at=time.time(),
            response_time_ms=response.response_time_ms,
            cost_usd=self._estimate_cost(response),
        )

    @staticmethod
    def _estimate_cost(response: Any) -> float:
        """Estimate USD cost from token usage (DeepSeek V3 pricing)."""
        input_cost = response.input_tokens * 0.27 / 1_000_000
        output_cost = response.output_tokens * 1.10 / 1_000_000
        return input_cost + output_cost

    def drain_recommendations(self) -> list[AdvisorRecommendation]:
        """Return and clear pending recommendations. Called by Watchdog each tick."""
        recs = self._pending_recommendations[:]
        self._pending_recommendations.clear()
        return recs

    @property
    def last_report(self) -> Optional[AdvisorReport]:
        """Most recent advisor report."""
        return self._last_report

    def get_stats(self) -> dict:
        """Usage statistics for monitoring."""
        return {
            "total_calls": self._total_calls,
            "total_cost_usd": round(self._total_cost_usd, 4),
            "last_risk": self._last_report.portfolio_risk if self._last_report else "none",
            "last_recs": len(self._last_report.recommendations) if self._last_report else 0,
        }

    async def close(self) -> None:
        """Clean up DeepSeek client session."""
        await self._client.close()
