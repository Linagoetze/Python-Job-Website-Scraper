"""A check on rows a reader has returned, shared by readers and the probe."""

from __future__ import annotations

_ELLIPSIS = ".…"


def title_read_as_location(title: str, location: str) -> bool:
    """True if `location` is the title, or the title shortened with "..." (SP8).

    A page shortens a long title in its visible text, so a reader that takes the
    wrong element as the place returns the title's start with a trailing "...".
    A plain prefix without that mark is not flagged: a city can begin a title
    ("Stockholm Office Manager" in Stockholm). An empty location is a real
    state (WP8f, SP4f) and is never flagged.
    """
    location = location.strip()
    stem = location.rstrip(_ELLIPSIS).strip()
    if not stem:
        return False
    title = title.strip()
    if stem.casefold() == title.rstrip(_ELLIPSIS).strip().casefold():
        return True
    return stem != location and title.casefold().startswith(stem.casefold())
