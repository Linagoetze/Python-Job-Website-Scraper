"""Detail pages for tests that need Layer 5 to read them.

Since SP4c a page whose stripped text is under `MIN_READABLE_CHARS` is
"unreadable": the job is kept unchecked and a deferred location is left
unverified. The one-line pages most tests used to hand back are therefore
shells, so a test that wants a page *read* wraps its sentence in `posting`.
"""

from __future__ import annotations

from job_scraper.experience_filter import MIN_READABLE_CHARS

# Neutral boilerplate: no years, no PhD, no place and no hybrid wording, so it
# can never change what the sentence it is padding says.
_FILLER = (
    "About the team. We support our colleagues and partners with careful, "
    "well-documented work, and we value curiosity, clear writing and a habit "
    "of asking questions early. You will join a friendly group that meets "
    "weekly to share what it has learnt. "
)


def posting(text: str) -> str:
    """`text` followed by enough filler to count as a readable posting."""
    repeats = MIN_READABLE_CHARS // len(_FILLER) + 1
    return f"<p>{text}</p><p>{_FILLER * repeats}</p>"
