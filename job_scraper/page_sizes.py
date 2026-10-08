"""What a page of postings usually holds, and the rule built on it (SP7).

Two modules need the same list: `probe`, which suspects a reader that returns
exactly one of these from a listing with a pager, and the run summary, which
suspects a source that returns the same one on every run. It lives here so
neither imports the other and the list is written once.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

# Page sizes listings tend to come in. A reader that returns exactly one of
# these from a listing with a pager has probably read one page.
TYPICAL_PAGE_SIZES = frozenset({10, 12, 15, 16, 18, 20, 24, 25, 30, 36, 40, 48, 50, 60, 100})

# How many successful runs in a row must agree before one page is suspected.
# Five, the number the package named, kept because the store gave no reason to
# move it (docs/DECISIONS.md, SP7): the four sources that read one page were
# at 20 in every one of 27 runs, so any N up to 27 catches them, and a larger N
# only delays the warning by that many runs. A smaller one, 3 or 4, warns for
# sources whose boards simply held a round number for a few days.
ONE_PAGE_RUNS = 5


def constant_page_size(
    recent_counts: Sequence[int],
    *,
    runs: int = ONE_PAGE_RUNS,
    sizes: Iterable[int] = TYPICAL_PAGE_SIZES,
) -> int | None:
    """The page size a source has returned on each of its last *runs* runs, or None.

    *recent_counts* are row counts from successful runs, newest first, and may
    be longer than *runs*; only the newest *runs* are read. Fewer than *runs*
    counts is not enough evidence to say anything. The counts must all be equal
    and the number must be one of *sizes*: a board that holds seven postings for
    five runs is a small board, not a page.

    Platform-blind on purpose. A reader that walks and checks a stated total
    still goes through here, so this is also an independent check on the guard
    (the owner's decision, SP7).
    """
    window = list(recent_counts[:runs])
    if len(window) < runs or len(set(window)) != 1:
        return None
    return window[0] if window[0] in set(sizes) else None
