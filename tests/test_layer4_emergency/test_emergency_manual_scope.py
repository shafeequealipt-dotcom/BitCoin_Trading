"""Layer 4 Realignment Phase 3.1 — emergency_manual scope regression guard.

The ``emergency_manual`` close-reason is reserved for OPERATOR-initiated
emergency closes (Telegram dashboard / control buttons → ``LayerManager.
emergency_close_all`` at src/core/layer_manager.py:625). It must never
be set by any worker, risk module, or strategy code path; doing so
would create a backdoor that bypasses every Layer 4 protection.

This test grep-walks the source tree and asserts there is exactly one
setter for ``emergency_manual``: the LayerManager method. Any new
emission point added by a future commit will fail this test loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _iter_source_files() -> list[Path]:
    """Yield .py files under src/ that should be scanned. Excludes
    backups, archived snapshots, and __pycache__ directories."""
    src_dir = REPO / "src"
    return [
        p for p in src_dir.rglob("*.py")
        if "__pycache__" not in p.parts
        and "backups" not in p.parts
    ]


def test_emergency_manual_setter_is_operator_only() -> None:
    """Exactly one source file under src/ may set the ``emergency_manual``
    close-reason: src/core/layer_manager.py (the operator-initiated
    emergency-close-all path)."""
    pattern = re.compile(r'set_close_reason\([^)]*"emergency_manual"|'
                         r'"emergency_manual"[^)]*set_close_reason')
    setters: list[tuple[Path, int, str]] = []
    for src_file in _iter_source_files():
        for n, line in enumerate(src_file.read_text().splitlines(), 1):
            if pattern.search(line):
                setters.append((src_file, n, line.strip()))

    assert len(setters) == 1, (
        f"Expected exactly 1 emergency_manual setter; found {len(setters)}: "
        f"{[(str(p.relative_to(REPO)), n) for p, n, _ in setters]}"
    )
    src_file, line_no, _ = setters[0]
    rel_path = str(src_file.relative_to(REPO))
    assert rel_path == "src/core/layer_manager.py", (
        f"emergency_manual must be set only in src/core/layer_manager.py; "
        f"found in {rel_path}:{line_no}"
    )


def test_emergency_manual_string_appears_in_layer_manager_only() -> None:
    """A weaker regression check: the literal string ``emergency_manual``
    appears under src/ ONLY in src/core/layer_manager.py. Any other
    occurrence is suspicious — even a comment or constant — because it
    suggests a code path is reasoning about the reason elsewhere."""
    occurrences: list[tuple[Path, int]] = []
    for src_file in _iter_source_files():
        for n, line in enumerate(src_file.read_text().splitlines(), 1):
            if "emergency_manual" in line:
                occurrences.append((src_file, n))

    files_seen = {p.relative_to(REPO).parts for p, _ in occurrences}
    expected = {("src", "core", "layer_manager.py")}
    assert files_seen == expected, (
        f"emergency_manual string should only appear in src/core/"
        f"layer_manager.py; found in: "
        f"{[str(Path(*p)) for p in sorted(files_seen)]}"
    )
