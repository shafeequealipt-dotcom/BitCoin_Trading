"""Ranking helpers shared by the scanner cut and the brain cap.

Issue #2 fix (2026-05-27): the scanner's 50->15 cut and the brain's 15->10 cap
both ranked candidates by an "interestingness" score as the PRIMARY key, with
the tradeable-merit "opportunity" score only a tiebreak. Because the two scores
correlate only weakly, high-expected-value coins were silently dropped before
the brain ever saw them.

``reserve_slots_union`` fills the available slots by drawing ALTERNATELY from
the top-by-opportunity ordering and the top-by-interestingness ordering,
de-duplicated. This guarantees a high-opportunity coin is never dropped purely
for low interestingness (and vice versa) while preserving the slot count — it
re-ranks, it does not shrink the set.
"""
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")


def reserve_slots_union(
    items: Sequence[T],
    n: int,
    opp_key: Callable[[T], float],
    int_key: Callable[[T], float],
    opp_first: bool = True,
) -> tuple[list[T], int, int]:
    """Select up to ``n`` items via an opportunity/interestingness reserve.

    Draws alternately from the top-by-``opp_key`` and top-by-``int_key``
    orderings, skipping items already taken, until ``n`` are chosen or both
    orderings are exhausted. ``opp_first=True`` gives opportunity the first
    pick (it is the more trustworthy score today — interestingness is partly
    stub-fed per audit item E9).

    Pure and deterministic. Items are de-duplicated by identity (``id``), so the
    same object is never selected twice; this works for both hashable tuples and
    CoinPackage objects.

    Returns ``(selected, from_opportunity_count, from_interestingness_count)``.
    The two counts sum to ``len(selected)`` and let the caller log how the slots
    were split.
    """
    if n <= 0 or not items:
        return [], 0, 0

    by_opp = sorted(items, key=opp_key, reverse=True)
    by_int = sorted(items, key=int_key, reverse=True)

    # Index 0 = opportunity stream, index 1 = interestingness stream.
    streams = [iter(by_opp), iter(by_int)]
    counts = [0, 0]
    selected: list[T] = []
    seen: set[int] = set()
    exhausted = [False, False]
    # First turn picks from the preferred stream.
    turn = 0 if opp_first else 1

    while len(selected) < n and not all(exhausted):
        if not exhausted[turn % 2]:
            idx = turn % 2
            advanced = False
            for it in streams[idx]:
                if id(it) in seen:
                    continue
                seen.add(id(it))
                selected.append(it)
                counts[idx] += 1
                advanced = True
                break
            if not advanced:
                exhausted[idx] = True
        turn += 1

    return selected, counts[0], counts[1]
