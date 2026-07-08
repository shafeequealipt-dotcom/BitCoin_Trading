"""TIAS category taxonomy — the single source of truth for DeepSeek trade verdicts.

This module is the *semantic contract* for the ``ds_category`` field that the TIAS
analyzer writes (phase 15) and that the APEX optimizer, the brain's "lessons from
recent trades" block, and the dashboards consume (phase 16 and beyond). It defines,
in ONE authoritative place:

  * the canonical set of category names (membership),
  * a one-line definition for each (meaning), and
  * which categories are failures versus successes (the failure/success tag).

It is a leaf module: it imports nothing from the rest of ``src.tias`` (or anywhere
else in the project), so the prompt builder, the analyzer, and the repository can
all import it without any circular dependency.

Created 2026-05-25 to close the "no semantic contract" root cause behind two
coupled learning-loop defects: the unfiltered "common issues" list shown to the
optimizer (issue #2) and the undefined, unvalidated category enum that made labels
inconsistent and inverted CORRECT_TRADE_BAD_LUCK onto wins (issue #3).

Forward-only: defining the categories here changes only how FUTURE analyses are
labeled. Historical rows keep their existing labels and are never rewritten.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical definitions — the ONE place each category's meaning lives.
# Insertion order is preserved (dict) for stable prompt rendering.
# Operator-approved 2026-05-25.
# ---------------------------------------------------------------------------

CATEGORY_DEFINITIONS: dict[str, str] = {
    # --- Success categories (a WINNING trade; pick exactly one) -------------
    "CORRECT_ENTRY": (
        "WIN where the entry direction and timing were the primary reason the "
        "trade worked. This is the DEFAULT label for a winning trade."
    ),
    "CORRECT_EXIT": (
        "WIN where exit management (hitting the target, trailing, or de-risking "
        "at the right moment) was the decisive factor in the result. Use only "
        "when exit management -- not the entry -- was what made the win."
    ),
    # --- Failure categories (a LOSING or clearly sub-optimal trade) ---------
    "ENTRY_TOO_EARLY": (
        "Entered before the setup confirmed; price moved against the position "
        "before the thesis could play out."
    ),
    "ENTRY_TOO_LATE": (
        "Entered after most of the move was already over, leaving poor remaining "
        "reward; the trade stalled or reversed."
    ),
    "EXIT_TOO_EARLY": (
        "Closed a winning trade before it captured the available move; left "
        "significant profit on the table."
    ),
    "EXIT_TOO_LATE": (
        "Held past the optimal exit; gave back gains or turned a winner into a "
        "loss."
    ),
    "REGIME_MISMATCH": (
        "The trade direction or strategy was wrong for the prevailing market "
        "regime."
    ),
    "INDICATOR_CONFLICT": (
        "Entered on conflicting signals; the indicators disagreed and the weaker "
        "thesis lost."
    ),
    "OVERLEVERAGE": (
        "Leverage was too high for the coin's volatility; a normal adverse move "
        "caused an outsized loss."
    ),
    "UNDERSIZE": (
        "Position was too small relative to the setup's quality; a correct trade "
        "captured too little dollar profit."
    ),
    "STOP_TOO_TIGHT": (
        "Stop was placed inside normal noise and was hit before the intended "
        "move occurred."
    ),
    "STOP_TOO_WIDE": (
        "Stop was placed too far away, producing a larger loss than the setup "
        "warranted."
    ),
    "SIGNAL_NOISE": (
        "The entry signal was noise rather than a real edge; no coherent thesis."
    ),
    "TREND_REVERSAL": (
        "An unexpected trend reversal moved against the position after entry."
    ),
    "NEWS_DRIVEN": (
        "The outcome was driven by a news or event shock rather than the "
        "technical setup."
    ),
    "MOMENTUM_FADE": (
        "Momentum faded after entry; the expected continuation stalled."
    ),
    "LIQUIDITY_TRAP": (
        "Caught by a liquidity sweep or stop-hunt that reversed against the "
        "position."
    ),
    "CORRECT_TRADE_BAD_LUCK": (
        "The decision (direction, entry, sizing, stop) was correct given the "
        "information available, but the trade LOST to variance or an "
        "unforeseeable event. This is a LOSS category: never apply it to a "
        "winning trade."
    ),
}

# Success-versus-failure tagging — the dimension the taxonomy previously lacked.
# A winning trade is exactly one of SUCCESS_CATEGORIES; everything else is a
# failure (including CORRECT_TRADE_BAD_LUCK, which is a correct-decision LOSS).
SUCCESS_CATEGORIES: frozenset[str] = frozenset({"CORRECT_ENTRY", "CORRECT_EXIT"})
ALL_CATEGORIES: frozenset[str] = frozenset(CATEGORY_DEFINITIONS.keys())
FAILURE_CATEGORIES: frozenset[str] = ALL_CATEGORIES - SUCCESS_CATEGORIES

# Tie-break guidance so wins stop fragmenting across the two success buckets.
TIE_BREAK_NOTE: str = (
    "For a WINNING trade choose CORRECT_ENTRY by default; use CORRECT_EXIT only "
    "when exit management (target / trailing / de-risk) -- not the entry -- was "
    "the decisive factor. Never label a winning trade CORRECT_TRADE_BAD_LUCK; "
    "that category is exclusively for correct-decision LOSSES."
)

# One-line summary for the boot sentinel so the running system can announce that
# the contract is live (see src/workers/manager.py TIAS wiring).
CONTRACT_SUMMARY: str = (
    f"total={len(ALL_CATEGORIES)} success={len(SUCCESS_CATEGORIES)} "
    f"failure={len(FAILURE_CATEGORIES)}"
)


def render_definitions_block() -> str:
    """Render the category definitions as a text block for the analysis prompt.

    Produces a heading, one line per category in canonical order tagged as
    ``(success)`` or ``(failure)``, then the win tie-break note. This is the text
    the DeepSeek analyzer prompt injects so the model knows what each category
    means and how to choose between the overlapping "correct" buckets.

    Returns:
        A multi-line string ready to append to the analysis system prompt.
    """
    lines = ["CATEGORY DEFINITIONS (choose the single best-fitting category):"]
    for name, definition in CATEGORY_DEFINITIONS.items():
        tag = "success" if name in SUCCESS_CATEGORIES else "failure"
        lines.append(f"- {name} ({tag}): {definition}")
    lines.append("")
    lines.append(TIE_BREAK_NOTE)
    return "\n".join(lines)


def normalize_category(raw: object) -> tuple[str | None, str]:
    """Normalize a model-returned category against the canonical set.

    Cleaning upper-cases, strips, and converts spaces and hyphens to underscores.
    No data is dropped on an unknown value: the cleaned string is returned so the
    caller can store it and log a sentinel, keeping drift observable rather than
    silently discarded.

    Args:
        raw: The category value as returned by the model (any type; usually str).

    Returns:
        A ``(value, status)`` tuple where status is one of:
          * ``"ok"``         -- already a canonical member, returned unchanged.
          * ``"normalized"`` -- recognised after cleaning case/whitespace.
          * ``"invalid"``    -- not in the set; the cleaned value (or ``None`` for
            empty input) is returned and the caller is expected to log it.
    """
    if raw is None:
        return None, "invalid"
    text = str(raw).strip()
    if not text:
        return None, "invalid"
    cleaned = text.upper().replace(" ", "_").replace("-", "_")
    if cleaned in ALL_CATEGORIES:
        return cleaned, ("ok" if cleaned == text else "normalized")
    return cleaned, "invalid"


def is_failure(category: str | None) -> bool:
    """Return True if the category is a tagged failure category.

    Used by consumers that want to reason about failure-versus-success by
    category meaning rather than by raw outcome. Note that the issue #2 query
    filters by ``win = 0`` (outcome), which agrees with this set by construction
    because the success categories never occur on losing trades.
    """
    return category in FAILURE_CATEGORIES
