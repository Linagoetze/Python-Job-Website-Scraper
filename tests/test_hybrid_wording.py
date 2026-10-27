"""SP15: an offer of remote work "to some extent" counts as hybrid.

Every description here is invented. The Swedish and English cases mirror the
shape of the wording that SP12's follow-up found on one employer's postings
without reproducing it; the Danish and German ones are checked here rather than
assumed (SP15 step 3).
"""

from __future__ import annotations

import pytest

from job_scraper.experience_filter import apply_detail_filter
from job_scraper.filtering import (
    _HYBRID_CONFIRMED_REASON,
    _HYBRID_PENDING_REASON,
    build_hybrid_pattern,
)
from tests.pages import posting

PATTERN = build_hybrid_pattern({"conditional_location_keywords": ["hybrid"]})

# Offers of remote work for part of the time: the gate must read these as hybrid.
OFFERS = [
    # Swedish
    "Vi erbjuder möjlighet till distansarbete i viss utsträckning.",
    "Tjänsten kan delvis utföras på distans.",
    "Du kan arbeta hemifrån till viss del.",
    "Distansarbete är möjligt i viss mån.",
    # English
    "We offer the possibility of remote work to some extent.",
    "You can work remotely to a certain extent.",
    "Working from home is possible in part.",
    "Partial home working is available.",
    # Danish
    "Der er mulighed for fjernarbejde i et vist omfang.",
    "Du kan delvist arbejde hjemmefra.",
    # German
    "Homeoffice ist teilweise möglich.",
    "Sie können zum Teil im Homeoffice arbeiten.",
    "Mobiles Arbeiten ist in gewissem Umfang möglich.",
    # Run together without punctuation, as stored descriptions often are.
    "Bra att veta Tillträde snarast Vi erbjuder möjlighet till distansarbete i viss mån",
]

# Nothing here offers remote work for part of the time.
NOT_OFFERS = [
    # Names the office's city, says nothing of remote work.
    "The office is in Stockholm, five minutes from the central station.",
    "Vi sitter i moderna lokaler i centrala Stockholm.",
    # Rules remote work out, including with the extent words in the sentence.
    "Remote work is not possible in this role.",
    "We do not offer remote work, not even to some extent.",
    "Ingen möjlighet till distansarbete i viss utsträckning.",
    "Distansarbete är inte möjligt för den här tjänsten.",
    "Der er ingen mulighed for fjernarbejde, heller ikke delvist.",
    "Kein Homeoffice möglich, auch nicht teilweise.",
    "Kvalifikationer Erfarenhet Vi erbjuder ingen möjlighet till distansarbete i viss utsträckning",
    # The extent words and the remote words in different sentences.
    "Part of the role is stakeholder work. Our servers are managed remotely.",
    "Delvis ansvar för budgeten. Teamet sitter på kontoret.",
]


@pytest.mark.parametrize("text", OFFERS)
def test_an_offer_of_partial_remote_work_is_hybrid(text: str) -> None:
    assert PATTERN is not None and PATTERN.search(text)


@pytest.mark.parametrize("text", NOT_OFFERS)
def test_other_wording_stays_non_hybrid(text: str) -> None:
    assert PATTERN is not None and not PATTERN.search(text)


def test_the_word_hybrid_still_matches() -> None:
    assert PATTERN is not None
    assert PATTERN.search("A hybrid role.")
    assert PATTERN.search("Vi erbjuder hybridarbete.")


def test_unconfigured_gate_stays_inert() -> None:
    assert build_hybrid_pattern({"conditional_location_keywords": []}) is None
    assert build_hybrid_pattern({}) is None


def _pending_job() -> dict:
    return {
        "source_name": "test",
        "title": "Analyst",
        "location": "Stockholm",
        "detail_url": "https://example.com/job",
        "apply_url": "",
        "raw_snippet": "Analyst Stockholm",
        "matched_reasons": [_HYBRID_PENDING_REASON],
    }


@pytest.mark.parametrize(
    "text",
    [
        "Vi erbjuder möjlighet till distansarbete i viss utsträckning om det fungerar.",
        "We offer the possibility of remote work to some extent where it suits the team.",
    ],
)
def test_layer5_confirms_a_conditional_city_job(text: str) -> None:
    kept, excluded = apply_detail_filter(
        [_pending_job()], lambda _url: posting(f"<p>{text}</p>"), hybrid_pattern=PATTERN
    )
    assert len(kept) == 1 and not excluded
    assert kept[0]["matched_reasons"] == [_HYBRID_CONFIRMED_REASON]


@pytest.mark.parametrize("text", ["The office is in Stockholm.", "Remote work is not possible."])
def test_layer5_still_rejects_without_an_offer(text: str) -> None:
    kept, excluded = apply_detail_filter(
        [_pending_job()], lambda _url: posting(f"<p>{text}</p>"), hybrid_pattern=PATTERN
    )
    assert not kept and len(excluded) == 1
