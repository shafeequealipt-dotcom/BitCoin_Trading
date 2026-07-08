"""Layer 1D briefing-pack pipeline (Phase 3+ of the rewrite).

Modules in this package replace the legacy 5-criterion exclusion gate
in ``scanner_worker.py`` with a state-characterizer / labeller / ranker
/ briefing-builder chain. Each module is pure (no IO, no decisions) so
unit tests can exercise the chain without a live cycle.

Phase ownership:
    Phase 3 — ``state_labeler``    (this commit)
    Phase 4 — ``interestingness``  (continuous score)
    Phase 5 — ``coin_state``, ``state_characterizer``, ``mtf_bias``,
              ``risk_envelope``, ``briefing_builder``
"""
