"""Assert every get_logger("name") string in src/ is routed in COMPONENT_ROUTING.

Guards against the bug class where a new submodule calls
``get_logger("new_name")`` without adding "new_name" to
``src/core/logging.py::COMPONENT_ROUTING``. An unrouted component falls
through ``_default_filter`` and lands in ``general.log`` — invisible to any
verification script that greps ``workers.log``. VOL_PROFILE / KLINES_CLEANUP /
M4_TRAIL were all hidden this way before the routing table was fixed.

A failing assertion names the exact orphan component(s) so the fix is a
one-line addition to COMPONENT_ROUTING.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.core.logging import COMPONENT_ROUTING

_SRC = Path(__file__).resolve().parents[1] / "src"

# Matches ``get_logger("name")`` with whitespace-tolerance. Captures the name.
# We intentionally accept only double-quoted string literals because that is
# the project's sole convention (grep confirms zero single-quoted usages).
_PATTERN = re.compile(r'get_logger\(\s*"(\w+)"\s*\)')


def _scan_components() -> set[str]:
    """Return every string literal passed to ``get_logger("...")`` under src/."""
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # Binary/unreadable — skip rather than fail the test on I/O oddities.
            continue
        found.update(_PATTERN.findall(text))
    return found


def test_every_get_logger_component_is_routed() -> None:
    """No get_logger("...") string may be absent from COMPONENT_ROUTING."""
    used = _scan_components()
    routed = set(COMPONENT_ROUTING.keys())
    orphans = sorted(used - routed)
    assert not orphans, (
        'get_logger("...") components missing from COMPONENT_ROUTING — '
        "these would leak silently to general.log:\n  "
        + "\n  ".join(orphans)
        + "\n\nAdd each one to src/core/logging.py::COMPONENT_ROUTING "
        "with an appropriate .log target."
    )


def test_component_routing_targets_are_valid() -> None:
    """Each route must be a bare .log filename (setup_logging prepends log_dir)."""
    for component, target in COMPONENT_ROUTING.items():
        assert target.endswith(".log"), (
            f"{component!r} routes to {target!r} — must end with .log"
        )
        assert "/" not in target and "\\" not in target, (
            f"{component!r} routes to {target!r} — must be a bare filename "
            "(setup_logging handles the directory)"
        )


def test_scan_finds_known_components() -> None:
    """Sanity: the scanner MUST find at least the core components.

    If this ever fails, the regex/scan has regressed — not COMPONENT_ROUTING.
    """
    used = _scan_components()
    for required in ("worker", "brain", "mcp", "strategist"):
        assert required in used, (
            f"Scanner failed to find get_logger({required!r}) in src/ — "
            "the regex or directory layout has changed."
        )
