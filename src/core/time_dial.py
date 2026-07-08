"""Time-Decay Master Dial — the continuous age driver for the Profit-Fetching
Exit System (see PROFIT_FETCHING_SYSTEM_MASTER_BLUEPRINT.md Part 4).

The trade's age in minutes is a continuous master dial. Every tunable value in
the exit engine is a smooth function of that age: it glides from a *young*
anchor (loose, patient) toward an *old* anchor (tight, protective) as the trade
ages from 0 minutes to its per-trade deadline (the brain's ``max_hold_minutes``).
A young trade is treated as roomy; an old trade hugs close; everything slides
between, with the transition naturally centred near the data's ~22-minute peak
when the deadline is the typical ~50 minutes.

Blueprint 4.4: start with a simple proportional (linear) glide; bend the curve
only if real data later justifies it. This module implements the simple linear
glide. All anchors live in ``ProfitFetchingSettings`` (config.toml
``[profit_fetching]``) so they can be tuned in one place against truthful PnL.

This module is pure and stateless — no I/O, no clock reads. The caller supplies
the trade's age and deadline (both in minutes); the dial returns the resolved
parameter values. That keeps it trivially testable and free of hidden coupling.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DialedParams:
    """The resolved value of every time-dialed parameter at a given age.

    ``safety_stop_pct`` is a constant pass-through (it is not age-dialed) so
    that every value the engine needs comes from a single object. The
    ``age_*`` fields are carried for observability (logged each cycle).
    """

    atr_multiple: float
    ladder_step_pct: float
    lock_offset_pct: float
    safety_stop_pct: float
    age_fraction: float
    age_minutes: float
    deadline_minutes: float


@dataclass(frozen=True)
class LossDialedParams:
    """The resolved value of every time-dialed LOSS-side parameter at an age.

    The companion of :class:`DialedParams` for the Loss-Cutting System. Each
    field glides from its ``*_young`` anchor toward its ``*_old`` anchor as the
    trade ages from 0 to its per-trade deadline (same linear glide, same clock).
    The volatility (ATR) dial is applied separately in the sniper by multiplying
    the dialed multiples (``atr_initial_multiple``, ``structure_buffer_atr``) by
    the effective ATR — exactly as the profit side multiplies ``atr_multiple``.

    ``cap_pct`` is the age-glided percent-of-notional for the sacred cap (the
    fixed dollar ceiling is applied in the sniper, not dialed). The spike-stop
    parameters are deliberately NOT here — the catastrophe stop is excluded from
    the time dial (blueprint Rule 8) and reads its constants straight from
    settings. The ``age_*`` fields are carried for observability.
    """

    cap_pct: float
    atr_initial_multiple: float
    structure_buffer_atr: float
    stall_min_age_fraction: float
    winprob_cut_threshold: float
    age_fraction: float
    age_minutes: float
    deadline_minutes: float


def _lerp(young: float, old: float, fraction: float) -> float:
    """Linear glide from the *young* anchor to the *old* anchor.

    ``fraction`` is expected to be clamped to ``[0, 1]`` by the caller. At
    fraction 0 this returns the young anchor; at 1 it returns the old anchor;
    proportional in between. Works correctly whether young > old (tightening,
    the usual case) or young < old.
    """
    return young + (old - young) * fraction


class TimeDial:
    """Resolves time-dialed parameters from a trade's age and deadline.

    Stateless: one instance is built from ``ProfitFetchingSettings`` at sniper
    construction and queried once per position per tick via :meth:`resolve`.
    """

    # Floor on the deadline so a zero/missing ``max_hold_minutes`` cannot cause
    # a divide-by-zero or collapse the glide to a step function. One minute is
    # below any real brain-assigned deadline, so it never affects live trades.
    _MIN_DEADLINE_MINUTES: float = 1.0

    def __init__(self, settings) -> None:
        # ``settings`` is a ProfitFetchingSettings instance. Typed loosely to
        # avoid importing the settings module here (keeps this module a leaf).
        self._s = settings

    @classmethod
    def _fraction(cls, age_minutes: float, deadline_minutes: float) -> float:
        """Age as a fraction of the deadline window, clamped to ``[0, 1]``.

        Past the deadline the fraction saturates at 1.0 — every value then sits
        at its tight *old* anchor, which is the blueprint's "tightened to
        maximum at the deadline" behaviour. The deadline does NOT close the
        trade here; that decision belongs to the exit logic.
        """
        deadline = max(deadline_minutes, cls._MIN_DEADLINE_MINUTES)
        fraction = age_minutes / deadline
        if fraction < 0.0:
            return 0.0
        if fraction > 1.0:
            return 1.0
        return fraction

    def resolve(self, age_minutes: float, deadline_minutes: float) -> DialedParams:
        """Return the dialed parameter values for a trade of the given age.

        Args:
            age_minutes: Minutes since the trade opened (>= 0).
            deadline_minutes: The trade's per-trade ``max_hold_minutes``.

        Returns:
            A :class:`DialedParams` with the ATR multiple, ladder step spacing,
            ladder lock offset, the constant safety-stop distance, and the age
            fraction for observability.
        """
        settings = self._s
        fraction = self._fraction(age_minutes, deadline_minutes)
        return DialedParams(
            atr_multiple=_lerp(
                settings.atr_multiple_young, settings.atr_multiple_old, fraction,
            ),
            ladder_step_pct=_lerp(
                settings.ladder_step_pct_young, settings.ladder_step_pct_old, fraction,
            ),
            lock_offset_pct=_lerp(
                settings.lock_offset_pct_young, settings.lock_offset_pct_old, fraction,
            ),
            safety_stop_pct=settings.safety_stop_pct,
            age_fraction=round(fraction, 4),
            age_minutes=round(age_minutes, 3),
            deadline_minutes=round(deadline_minutes, 3),
        )

    def resolve_loss(
        self, age_minutes: float, deadline_minutes: float,
    ) -> LossDialedParams:
        """Return the dialed LOSS-side parameter values for a trade's age.

        The loss-side companion of :meth:`resolve`. ``self._s`` here is a
        ``LossCuttingSettings`` instance (the loss dial is a separate TimeDial
        built from ``settings.loss_cutting``), so this reads the loss anchors.
        The same clamped ``[0, 1]`` age fraction drives every glide, keeping the
        loss and profit halves of the engine in phase on one master clock.

        Args:
            age_minutes: Minutes since the trade opened (>= 0).
            deadline_minutes: The trade's per-trade ``max_hold_minutes``.

        Returns:
            A :class:`LossDialedParams` with the age-glided cap percent, ATR
            initial-stop multiple, structure buffer, stall min-age fraction, and
            win-probability cut threshold, plus the age fraction for logging.
        """
        settings = self._s
        fraction = self._fraction(age_minutes, deadline_minutes)
        return LossDialedParams(
            cap_pct=_lerp(
                settings.cap_pct_of_notional_young,
                settings.cap_pct_of_notional_old,
                fraction,
            ),
            atr_initial_multiple=_lerp(
                settings.atr_initial_multiple_young,
                settings.atr_initial_multiple_old,
                fraction,
            ),
            structure_buffer_atr=_lerp(
                settings.structure_buffer_atr_young,
                settings.structure_buffer_atr_old,
                fraction,
            ),
            stall_min_age_fraction=_lerp(
                settings.stall_min_age_fraction_young,
                settings.stall_min_age_fraction_old,
                fraction,
            ),
            winprob_cut_threshold=_lerp(
                settings.winprob_cut_threshold_young,
                settings.winprob_cut_threshold_old,
                fraction,
            ),
            age_fraction=round(fraction, 4),
            age_minutes=round(age_minutes, 3),
            deadline_minutes=round(deadline_minutes, 3),
        )
