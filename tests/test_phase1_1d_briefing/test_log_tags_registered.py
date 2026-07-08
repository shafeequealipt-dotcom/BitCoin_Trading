"""Phase 1 of the 1D briefing rewrite — verify BRIEFING_* tags exist.

Single question this test answers: "Did Phase 1 register every log tag
the later phases will emit?" Each tag is a string constant; the test
confirms presence + value-equals-name (the project-wide convention).
"""

from src.core import log_tags


def test_briefing_tags_registered() -> None:
    expected = (
        "BRIEFING_BUILD_START",
        "BRIEFING_BUILD_DONE",
        "BRIEFING_RANK",
        "BRIEFING_STATE_LABEL",
        "BRIEFING_INTERESTINGNESS",
        "BRIEFING_AB_COMPARE",
        "SCANNER_LABELED",
        "SCANNER_BRIEFING_SUMMARY",
    )
    for name in expected:
        assert hasattr(log_tags, name), f"missing constant {name}"
        # Convention: tag value equals its symbol name so log lines and
        # grep queries match exactly.
        assert getattr(log_tags, name) == name, (
            f"{name} must equal its symbol name"
        )
