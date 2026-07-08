"""Parses Claude's JSON response into structured BrainDecision.

Handles various response formats: clean JSON, markdown fences, surrounding text.
"""

import json
import re

from src.config.settings import Settings
from src.core.exceptions import DecisionParseError
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import BrainDecision, OrderType, WatchdogDecision
from src.core.utils import clamp, generate_id, now_utc

log = get_logger("brain")


class DecisionParser:
    """Parses Claude responses into BrainDecision dataclasses.

    Handles multiple JSON formats gracefully.
    """

    def parse(self, response_text: str) -> BrainDecision:
        """Parse Claude's response text into a BrainDecision.

        Tries multiple extraction strategies for JSON.

        Args:
            response_text: Raw text from Claude API.

        Returns:
            BrainDecision dataclass.

        Raises:
            DecisionParseError: If JSON cannot be extracted or parsed.
        """
        data, strategy = self._extract_json(response_text)
        return self._build_decision(data, strategy)

    def _extract_json(self, text: str) -> tuple[dict, str]:
        """Extract JSON from response text using multiple strategies.

        Phase 12.2 (lifecycle-logging-audit Gap 2.4-G4): returns the
        strategy that succeeded so PARSE_OK can carry it as a field
        (DEBUG markers were invisible at default INFO sink and the
        forensic value lives on the success line, not separate ones).
        """
        # Strategy 1: Direct parse
        try:
            result = json.loads(text.strip())
            return result, "direct"
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: Markdown code fences
        fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
        if fence_match:
            try:
                result = json.loads(fence_match.group(1).strip())
                return result, "fence"
            except (json.JSONDecodeError, ValueError):
                pass

        # Strategy 3: Find first { ... last }
        first_brace = text.find('{')
        last_brace = text.rfind('}')
        if first_brace != -1 and last_brace > first_brace:
            try:
                result = json.loads(text[first_brace:last_brace + 1])
                return result, "braces"
            except (json.JSONDecodeError, ValueError):
                pass

        log.error(f"PARSE_FAIL | raw='{text[:300]}' | {ctx()}")
        raise DecisionParseError(
            f"Could not extract JSON from Claude response",
            details={"raw_response": text[:500]},
        )

    def _build_decision(self, data: dict, strategy: str = "unknown") -> BrainDecision:
        """Build a BrainDecision from parsed JSON data."""
        action = str(data.get("action", "hold")).lower()
        symbol = str(data.get("symbol", "BTCUSDT"))
        confidence = clamp(float(data.get("confidence", 0.0)), 0.0, 1.0)

        order_type_raw = str(data.get("order_type", "market")).capitalize()
        try:
            order_type = OrderType(order_type_raw)
        except ValueError:
            order_type = OrderType.MARKET

        decision = BrainDecision(
            id=generate_id("brain"),
            action=action,
            symbol=symbol,
            confidence=confidence,
            order_type=order_type,
            reasoning=str(data.get("reasoning", "")),
            risk_notes=str(data.get("risk_notes", "")),
            created_at=now_utc(),
        )

        # Store extra fields as attributes for the executor
        decision._limit_price = data.get("limit_price")  # type: ignore[attr-defined]
        decision._qty_pct = clamp(float(data.get("qty_pct", 0)), 0, 100)  # type: ignore[attr-defined]
        decision._stop_loss = data.get("stop_loss")  # type: ignore[attr-defined]
        decision._take_profit = data.get("take_profit")  # type: ignore[attr-defined]
        decision._leverage = max(1, int(data.get("leverage", 1)))  # type: ignore[attr-defined]

        # Phase 12.2 (lifecycle-logging-audit Gaps 2.4-G1, 2.4-G4):
        # added strategy= field (formerly DEBUG-only PARSE_JSON markers);
        # deleted prose duplicate "Parsed decision: ...".
        log.info(
            f"PARSE_OK | act={action} sym={symbol} conf={confidence:.2f} "
            f"lev={decision._leverage} sl={decision._stop_loss} "
            f"tp={decision._take_profit} strategy={strategy} | {ctx()}"
        )
        return decision

    def parse_watchdog_decision(self, response_text: str) -> WatchdogDecision:
        """Parse a watchdog-specific response from Claude.

        Accepts watchdog actions: hold, tighten_stop, partial_close, full_close.

        Args:
            response_text: Raw text from Claude API.

        Returns:
            WatchdogDecision dataclass.

        Raises:
            DecisionParseError: If JSON cannot be extracted or parsed.
        """
        data, strategy = self._extract_json(response_text)

        valid_actions = {"hold", "tighten_stop", "partial_close", "full_close"}
        action = str(data.get("action", "hold")).lower()
        if action not in valid_actions:
            # Phase 12.2 (lifecycle-logging-audit Gap 2.4-G3): structured
            # tag replacing tag-less prose. Operators may grep these to
            # detect Claude schema drift.
            log.warning(
                f"PARSE_INVALID_WD_ACTION | received='{action}' "
                f"defaulted_to=hold | {ctx()}"
            )
            action = "hold"

        new_stop_loss = data.get("new_stop_loss")
        if new_stop_loss is not None:
            try:
                new_stop_loss = float(new_stop_loss)
            except (ValueError, TypeError):
                new_stop_loss = None

        decision = WatchdogDecision(
            id=generate_id("wdog"),
            action=action,
            symbol=str(data.get("symbol", "")),
            confidence=clamp(float(data.get("confidence", 0.5)), 0.0, 1.0),
            new_stop_loss=new_stop_loss,
            reasoning=str(data.get("reasoning", "")),
            risk_notes=str(data.get("risk_notes", "")),
            created_at=now_utc(),
        )

        # Phase 12.2 (lifecycle-logging-audit Gap 2.4-G2): structured
        # tag replacing tag-less prose.
        log.info(
            f"PARSE_OK_WD | act={action} sym={decision.symbol} "
            f"conf={decision.confidence:.2f} new_sl={new_stop_loss} "
            f"strategy={strategy} | {ctx()}"
        )
        return decision

    def validate_decision(self, decision: BrainDecision, settings: Settings) -> list[str]:
        """Validate a parsed decision against rules.

        Args:
            decision: Parsed BrainDecision.
            settings: Application settings.

        Returns:
            List of issues (empty = valid).
        """
        issues: list[str] = []

        if decision.action not in ("buy", "sell", "close", "hold"):
            issues.append(f"Invalid action: {decision.action}")

        from src.config.constants import SUPPORTED_SYMBOLS
        if decision.symbol not in SUPPORTED_SYMBOLS:
            issues.append(f"Unsupported symbol: {decision.symbol}")

        if decision.confidence < 0 or decision.confidence > 1:
            issues.append(f"Confidence out of range: {decision.confidence}")

        leverage = getattr(decision, "_leverage", 1)
        if leverage > settings.risk.max_leverage:
            issues.append(f"Leverage {leverage} exceeds max {settings.risk.max_leverage}")

        if decision.action in ("buy", "sell"):
            qty_pct = getattr(decision, "_qty_pct", 0)
            if qty_pct <= 0:
                issues.append("qty_pct is 0 for buy/sell action")
            sl = getattr(decision, "_stop_loss", None)
            if sl is None and settings.risk.mandatory_stop_loss:
                issues.append("Stop-loss is mandatory but not provided")

        return issues

    # ────────────────────────────────────────────────────────────────
    # Mid-Hold Trade Management Fix (Phase 3.3, 2026-05-19)
    # ────────────────────────────────────────────────────────────────
    #
    # Parse and validate the per-trade ``thesis_invalidation`` field that
    # the brain provides in each new_trades[] inner object (Phase 3.2 of
    # the CALL_A system prompt taught it). Approach C is primary: brain
    # states the criterion. When brain omits/returns invalid, the caller
    # falls back to Approach A — the watchdog uses the XRAY snapshot
    # captured at entry (passed as thesis_snapshot to save_thesis).
    #
    # Validation rules (designed to fail loudly on Rule 4 anti-patterns
    # like silently-dropped invalid criteria):
    #
    #   type must be in VALID_THESIS_INVALIDATION_TYPES.
    #   For price_close_above / price_close_below:
    #     value must be numeric and within VALID_PRICE_RANGE_PCT of
    #     entry_price. A criterion 200% above the entry price is a brain
    #     hallucination, not a usable monitor — fall back to Approach A.
    #   For signal: value must be in VALID_SIGNAL_KEYWORDS. Unknown
    #     keywords (e.g. brain inventing 'oversold_recovery') are
    #     rejected — the watchdog cannot monitor what it doesn't
    #     recognise.
    #   For none: value must be None. {"type":"none","value":42} is a
    #     contradiction, treated as invalid.

    VALID_THESIS_INVALIDATION_TYPES = (
        "price_close_above",
        "price_close_below",
        "signal",
        "none",
    )
    VALID_SIGNAL_KEYWORDS = (
        "ensemble_flip_to_strong_buy",
        "ensemble_flip_to_strong_sell",
        "regime_inverted",
        "mtf_alignment_broken",
    )
    # A criterion within +/- 50% of entry is the sanity range — anything
    # beyond is almost certainly a brain hallucination (transposed
    # decimal, wrong symbol, stale price). Operator-tunable in Phase
    # 3.10 if real trades produce wider-but-legitimate criteria.
    VALID_PRICE_RANGE_PCT = 50.0

    def parse_thesis_invalidation(
        self,
        trade_dict: dict,
        entry_price: float,
        symbol: str = "",
    ) -> tuple[str, str]:
        """Parse and validate one trade's ``thesis_invalidation`` field.

        Per IMPLEMENT_MIDHOLD doc Rule 16: Approach C primary, Approach A
        fallback. This method is the contract gate between the brain's
        raw response and the persisted thesis row.

        Args:
            trade_dict: Single trade dict from ``plan.new_trades[i]``.
                The ``thesis_invalidation`` field is read; other fields
                (symbol/direction/price) are ignored here.
            entry_price: Current entry price; used to sanity-check
                price-level criteria.
            symbol: Trade symbol; logged for grep-friendly context.

        Returns:
            Tuple ``(criterion_json, source_label)``.

            - ``criterion_json`` is a JSON-serialized string of the
              validated criterion dict, ready to pass directly to
              ``ThesisManager.save_thesis(thesis_invalidation=...)``.
              Empty string ('') when the brain omitted or provided an
              invalid criterion; in that case the caller must populate
              ``thesis_snapshot`` for Approach A monitoring.
            - ``source_label`` is ``'brain_stated'`` on parseable input;
              ``'heuristic_fallback'`` when fallback applies.

        Emits exactly one of three log events per call:
            BRAIN_THESIS_INVALIDATION_PARSED   - valid; source=brain_stated
            BRAIN_THESIS_INVALIDATION_MISSING  - field absent
            BRAIN_THESIS_INVALIDATION_INVALID  - present but malformed
        """
        sym_tag = symbol or trade_dict.get("symbol", "?")
        raw = trade_dict.get("thesis_invalidation")

        # ── Case 1: field absent → fallback ──
        if raw is None or raw == "":
            log.info(
                f"BRAIN_THESIS_INVALIDATION_MISSING | sym={sym_tag} "
                f"falling_back_to=heuristic_fallback | {ctx()}"
            )
            return "", "heuristic_fallback"

        # ── Case 2: field present but not a dict → invalid ──
        if not isinstance(raw, dict):
            log.warning(
                f"BRAIN_THESIS_INVALIDATION_INVALID | sym={sym_tag} "
                f"reason=not_a_dict type={type(raw).__name__} "
                f"falling_back_to=heuristic_fallback | {ctx()}"
            )
            return "", "heuristic_fallback"

        crit_type = raw.get("type")
        crit_value = raw.get("value")

        # ── Case 3: unknown type → invalid ──
        if crit_type not in self.VALID_THESIS_INVALIDATION_TYPES:
            log.warning(
                f"BRAIN_THESIS_INVALIDATION_INVALID | sym={sym_tag} "
                f"reason=unknown_type type={crit_type!r} "
                f"falling_back_to=heuristic_fallback | {ctx()}"
            )
            return "", "heuristic_fallback"

        # ── Case 4: 'none' type — value must be None ──
        if crit_type == "none":
            if crit_value is not None:
                log.warning(
                    f"BRAIN_THESIS_INVALIDATION_INVALID | sym={sym_tag} "
                    f"reason=none_with_value type=none value={crit_value!r} "
                    f"falling_back_to=heuristic_fallback | {ctx()}"
                )
                return "", "heuristic_fallback"
            # Brain explicitly stated "no specific criterion applies".
            # This IS a brain_stated answer — fall through to validated.

        # ── Case 5: 'signal' type — value must be known keyword ──
        elif crit_type == "signal":
            if not isinstance(crit_value, str) or crit_value not in self.VALID_SIGNAL_KEYWORDS:
                log.warning(
                    f"BRAIN_THESIS_INVALIDATION_INVALID | sym={sym_tag} "
                    f"reason=unknown_signal value={crit_value!r} "
                    f"falling_back_to=heuristic_fallback | {ctx()}"
                )
                return "", "heuristic_fallback"

        # ── Case 6: 'price_close_*' type — value must be numeric + sane ──
        else:  # price_close_above or price_close_below
            try:
                crit_value = float(crit_value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                log.warning(
                    f"BRAIN_THESIS_INVALIDATION_INVALID | sym={sym_tag} "
                    f"reason=price_not_numeric value={crit_value!r} "
                    f"falling_back_to=heuristic_fallback | {ctx()}"
                )
                return "", "heuristic_fallback"

            # Sanity: value must be within +/- VALID_PRICE_RANGE_PCT
            # of entry. A criterion ten times the entry price is a brain
            # error, not a usable monitor.
            if entry_price > 0:
                drift_pct = abs(crit_value - entry_price) / entry_price * 100.0
                if drift_pct > self.VALID_PRICE_RANGE_PCT:
                    log.warning(
                        f"BRAIN_THESIS_INVALIDATION_INVALID | sym={sym_tag} "
                        f"reason=price_out_of_range type={crit_type} "
                        f"value={crit_value:.6f} entry={entry_price:.6f} "
                        f"drift={drift_pct:.1f}% max={self.VALID_PRICE_RANGE_PCT:.1f}% "
                        f"falling_back_to=heuristic_fallback | {ctx()}"
                    )
                    return "", "heuristic_fallback"
            # Re-normalize crit_value into the JSON we persist (was a
            # str-encoded number in some upstream responses).
            raw = {"type": crit_type, "value": crit_value}

        # ── All checks passed: emit success log and return JSON ──
        criterion_json = json.dumps(raw)
        log.info(
            f"BRAIN_THESIS_INVALIDATION_PARSED | sym={sym_tag} "
            f"type={crit_type} value={crit_value!r} "
            f"json_chars={len(criterion_json)} | {ctx()}"
        )
        return criterion_json, "brain_stated"
