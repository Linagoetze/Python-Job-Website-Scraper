"""SP1: two sources match when they are the same *board*, not the same host.

Half the supported ATS platforms put every customer on one hostname —
`sources.yaml` today has six employers on `job-boards.greenhouse.io`, three on
`jobs.ashbyhq.com` and two on `apply.workable.com`. A host match would report a
brand-new Greenhouse employer as already present, and in SP3 and SP7 as already
tombstoned, which is the expensive direction to be wrong in.
"""

from __future__ import annotations

import pytest

from job_scraper.urlutil import board_identity, same_board


class TestSharedHosts:
    """The cases the plan asked to be written down explicitly."""

    def test_two_greenhouse_boards_are_not_the_same_board(self) -> None:
        assert not same_board(
            "https://job-boards.greenhouse.io/canonical",
            "https://job-boards.greenhouse.io/dimagi",
        )

    def test_a_new_greenhouse_employer_does_not_match_an_existing_one(self) -> None:
        existing = [f"https://job-boards.greenhouse.io/{s}" for s in ("canonical", "givewell")]
        new = "https://job-boards.greenhouse.io/northwind"
        assert not any(same_board(new, u) for u in existing)

    @pytest.mark.parametrize(
        "host",
        ["jobs.ashbyhq.com", "apply.workable.com", "careers.smartrecruiters.com", "jobs.lever.co"],
    )
    def test_every_shared_ats_host_keeps_its_board_slug(self, host: str) -> None:
        assert board_identity(f"https://{host}/alpha") == f"{host}/alpha"
        assert not same_board(f"https://{host}/alpha", f"https://{host}/beta")

    def test_workday_separates_boards_within_one_tenant(self) -> None:
        """Chegg's Workday tenant hosts Busuu's board; a second brand there is not Busuu."""
        assert not same_board(
            "https://osv-chegg.wd5.myworkdayjobs.com/Busuu",
            "https://osv-chegg.wd5.myworkdayjobs.com/Chegg_External",
        )

    def test_a_workday_locale_segment_is_not_the_board(self) -> None:
        assert same_board(
            "https://path.wd1.myworkdayjobs.com/en-US/External",
            "https://path.wd1.myworkdayjobs.com/External",
        )


class TestTheSameBoardWrittenDifferently:
    @pytest.mark.parametrize(
        "variant",
        [
            "http://job-boards.greenhouse.io/canonical",
            "https://job-boards.greenhouse.io/canonical/",
            "https://www.job-boards.greenhouse.io/canonical",
            "  https://job-boards.greenhouse.io/canonical  ",
            "https://job-boards.greenhouse.io/CANONICAL",
            "https://job-boards.greenhouse.io/canonical/jobs/4567890",
            "job-boards.greenhouse.io/canonical",
        ],
        ids=["http", "trailing-slash", "www", "whitespace", "case", "deep-link", "no-scheme"],
    )
    def test_variants_match_the_canonical_form(self, variant: str) -> None:
        assert same_board("https://job-boards.greenhouse.io/canonical", variant)

    @pytest.mark.parametrize(
        "variant",
        [
            "http://careers.oatly.com/en-GB/jobs",
            "https://www.careers.oatly.com/",
            "https://careers.oatly.com/jobs/12345-some-slug",
        ],
    )
    def test_a_single_tenant_host_is_its_own_identity(self, variant: str) -> None:
        assert same_board("https://careers.oatly.com/en-GB/jobs", variant)

    def test_per_employer_subdomains_stay_distinct(self) -> None:
        """Teamtailor, Breezy and Personio give each employer a host of its own."""
        assert not same_board(
            "https://outdooractive.jobs.personio.de/", "https://someoneelse.jobs.personio.de/"
        )


class TestNotAUrl:
    @pytest.mark.parametrize("text", ["", "   ", "Northwind Health", "/careers"])
    def test_a_name_is_not_a_board(self, text: str) -> None:
        with pytest.raises(ValueError):
            board_identity(text)

    def test_same_board_says_no_rather_than_raising(self) -> None:
        assert not same_board("Northwind Health", "https://job-boards.greenhouse.io/northwind")
