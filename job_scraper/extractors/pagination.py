"""Shared policy for extractors that walk a paginated listing.

Every paginated extractor here ends its walk the same way: it asks for the next
page and stops when that page yields no postings. That test cannot tell the two
things apart:

- *the listing ended* — there was nothing more to fetch; and
- *the page did not parse* — the site answered 200 with a body that held no
  postings, because a back end hiccupped, a selector drifted, or an edge cache
  served a shell.

The second is a data-loss bug wearing the first's clothes. J-PAL returned 9 of
about 44 postings this way and no one noticed until WP10's source-health
warning fired: `?page=1` came back a full-size 200 with no job nodes and no
pager, the walk read that as the end, and the run was quietly short.

The fix is not a cleverer emptiness test. It is that an extractor which *knows*
how much more there is — a pager listing the pages, an API reporting a total —
must say so out loud when a page comes back empty before that point. Raising
fails the source, which the pipeline already handles well: the run keeps the
source's stored jobs, refuses to delist them, and records the failure.

This module holds the shared *policy*, not a shared loop. What counts as "more
was promised" is different for every listing — a pager, `totalFound`,
`totalJobs` — and only the extractor knows it. What must not differ is what
happens next, so the exception type and the wording live here.

Note what this deliberately does not cover: an extractor whose only end signal
is "a short or empty page" has no promise to check against, so it cannot tell
the two cases apart at all. See `docs/REFACTOR-PLAN.md`, WP11, for which
extractors those are and why they were left alone rather than given a guess.
"""

from __future__ import annotations

from typing import NoReturn


class ShortWalkError(RuntimeError):
    """A page yielded no postings while the source said there were more."""


def short_walk(source_name: str, url: str, *, collected: int, promised: str) -> NoReturn:
    """Fail the source rather than return a walk that stopped early.

    *promised* is the evidence that there was more to come, phrased to complete
    the sentence "…, but {promised}" — for example "the pager runs to page 4".
    """
    raise ShortWalkError(
        f"{source_name}: {url} yielded no postings, but {promised}. "
        f"Refusing to return a short list of {collected} posting(s): a page that "
        "did not parse is not the end of the listing."
    )
