"""Brain-Awareness Addition 2 (2026-06-09) — book-tilt label boundaries.

Tests the pure, centralized-threshold label logic used by the ACCOUNT-section
book-tilt awareness line. Boundaries are operator-tunable
([brain].book_tilt_small_count, book_tilt_one_sided_ratio); these tests pin the
classification at the shipped defaults (small_count=2, one_sided_ratio=3.0).
"""

from src.brain.strategist import _book_tilt_label


class TestBookTiltLabel:
    def test_flat_or_small_books_read_balanced(self):
        # abs(long-short) <= small_count(2) -> balanced regardless of ratio
        assert _book_tilt_label(0, 0) == "balanced"
        assert _book_tilt_label(1, 0) == "balanced"
        assert _book_tilt_label(2, 0) == "balanced"
        assert _book_tilt_label(2, 2) == "balanced"
        assert _book_tilt_label(3, 2) == "balanced"
        assert _book_tilt_label(5, 4) == "balanced"

    def test_one_sided_reads_heavily_tilted(self):
        # minority zero OR ratio >= one_sided_ratio(3.0)
        assert _book_tilt_label(3, 0) == "heavily long-tilted"
        assert _book_tilt_label(0, 7) == "heavily short-tilted"
        assert _book_tilt_label(7, 1) == "heavily long-tilted"   # ratio 7
        assert _book_tilt_label(6, 2) == "heavily long-tilted"   # ratio 3.0 == threshold
        assert _book_tilt_label(1, 4) == "heavily short-tilted"  # ratio 4

    def test_moderate_imbalance_reads_leaning(self):
        # diff > small_count, minority > 0, ratio < one_sided_ratio
        assert _book_tilt_label(5, 2) == "long-leaning"   # diff 3, ratio 2.5
        assert _book_tilt_label(2, 5) == "short-leaning"

    def test_thresholds_are_honored(self):
        # Tightening the ratio flips a borderline book from heavy to leaning.
        assert _book_tilt_label(6, 2, small_count=2, one_sided_ratio=3.0) == "heavily long-tilted"
        assert _book_tilt_label(6, 2, small_count=2, one_sided_ratio=4.0) == "long-leaning"
        # Widening small_count absorbs a small imbalance into balanced.
        assert _book_tilt_label(3, 0, small_count=2) == "heavily long-tilted"
        assert _book_tilt_label(3, 0, small_count=3) == "balanced"
