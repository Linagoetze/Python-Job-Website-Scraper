"""Map `source.name` from YAML to extractor callables."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from job_scraper import JobRecord
from job_scraper.extractors import (
    against_malaria,
    asana,
    ashby,
    bearingpoint,
    breezy,
    coefficient,
    gfi_europe,
    giving_what_we_can,
    greenhouse,
    impactpool,
    jobsinlund,
    jpal,
    lever,
    mammut,
    niras,
    norrsken,
    oatly,
    personio,
    sida,
    smartrecruiters,
    successfactors_html,
    teamtailor,
    undp,
    unops,
    workable,
    workday,
)

ExtractorFn = Callable[[str, Callable[[str], str]], list[JobRecord]]

REGISTRY: dict[str, ExtractorFn] = {
    # --- Greenhouse ---
    "wrike": partial(greenhouse.extract, source_name="wrike"),
    "canonical": partial(greenhouse.extract, source_name="canonical"),
    "givewell": partial(greenhouse.extract, source_name="givewell"),
    "give_directly": partial(greenhouse.extract, source_name="give_directly"),
    "urban_sports_club": partial(greenhouse.extract, source_name="urban_sports_club"),
    "dimagi": partial(greenhouse.extract, source_name="dimagi"),
    # --- Ashby ---
    "kognity": partial(ashby.extract, source_name="kognity"),
    "strava": partial(ashby.extract, source_name="strava"),
    "monday_com": partial(ashby.extract, source_name="monday_com"),
    # --- Teamtailor ---
    "seven_perigee": partial(teamtailor.extract, source_name="seven_perigee"),
    "founders_pledge": partial(teamtailor.extract, source_name="founders_pledge"),
    "futurelearn": partial(teamtailor.extract, source_name="futurelearn"),
    "storytel": partial(teamtailor.extract, source_name="storytel"),
    "planted": partial(teamtailor.extract, source_name="planted"),
    "lifesum": partial(teamtailor.extract, source_name="lifesum"),
    "fjallraven": partial(teamtailor.extract, source_name="fjallraven"),
    # --- Lever API ---
    "wave": partial(lever.extract, source_name="wave", org_slug="waveapps"),
    # --- Workday (Playwright) ---
    "slack": partial(workday.extract, source_name="slack"),
    "busuu": partial(workday.extract, source_name="busuu"),
    "airbus": partial(workday.extract, source_name="airbus"),
    "path": partial(workday.extract, source_name="path"),
    "irc": partial(workday.extract, source_name="irc"),
    # --- Workable ---
    "nutrition_international": partial(workable.extract, source_name="nutrition_international"),
    "simprints": partial(workable.extract, source_name="simprints"),
    # --- Breezy HR ---
    "new_incentives": partial(breezy.extract, source_name="new_incentives"),
    # --- Playwright (site-specific) ---
    "norrsken": partial(norrsken.extract, source_name="norrsken"),
    "asana": partial(asana.extract, source_name="asana"),
    # --- Personio ---
    "outdooractive": partial(personio.extract, source_name="outdooractive"),
    # --- Custom HTML ---
    "coefficient_giving": partial(coefficient.extract, source_name="coefficient_giving"),
    "oatly": partial(oatly.extract, source_name="oatly"),
    "against_malaria_foundation": partial(
        against_malaria.extract, source_name="against_malaria_foundation"
    ),
    "mammut": partial(mammut.extract, source_name="mammut"),
    "unops": partial(unops.extract, source_name="unops"),
    "jpal": partial(jpal.extract, source_name="jpal"),
    "giving_what_we_can": partial(giving_what_we_can.extract, source_name="giving_what_we_can"),
    "gfi_europe": partial(gfi_europe.extract, source_name="gfi_europe"),
    # --- SAP SuccessFactors (HTML / static) ---
    "dsv": partial(
        successfactors_html.extract,
        source_name="dsv",
        page_step=10,
        base_search_url="https://jobs.dsv.com/search/",
    ),
    "novo_nordisk": partial(
        successfactors_html.extract,
        source_name="novo_nordisk",
        page_step=100,
        base_search_url="https://careers.novonordisk.com/search",
    ),
    "coloplast": partial(
        successfactors_html.extract,
        source_name="coloplast",
        page_step=25,
        base_search_url="https://careers.coloplast.com/search/",
    ),
    # --- SAP SuccessFactors (Playwright; the rendering fetcher comes from
    # sources.yaml's `strategy: dynamic`, not from here) ---
    #
    # ISS: AJAX infinite scroll. Tetra Pak: a career-site-builder front end that
    # fills itself from /services/recruiting — which their robots.txt disallows,
    # and WP10's check duly refused, leaving the source failing every run. The
    # postings are all on /search/, which robots.txt allows, so this reads the
    # page a visitor sees instead of the endpoint they asked crawlers to leave
    # alone. The location filter lives in the URL, as it did in the old module's
    # request body.
    "tetrapak": partial(
        successfactors_html.extract,
        source_name="tetrapak",
        page_step=10,
        base_search_url="https://jobs.tetrapak.com/search/?q=&locationsearch=Sweden",
    ),
    "iss": partial(
        successfactors_html.extract,
        source_name="iss",
        page_step=20,
        base_search_url="https://jobs.issworld.com/search/",
    ),
    # --- Jobsinnetwork ---
    "jobsinlund": partial(jobsinlund.extract, source_name="jobsinlund"),
    # --- Impactpool (NGO/UN aggregator) ---
    "impactpool": partial(impactpool.extract, source_name="impactpool"),
    # --- UNDP ---
    "undp": partial(undp.extract, source_name="undp"),
    # --- OECD (SmartRecruiters) ---
    "oecd": partial(smartrecruiters.extract, source_name="oecd", org_slug="OECD"),
    # --- Sida ---
    "sida": partial(sida.extract, source_name="sida"),
    # --- NIRAS (dynamic) ---
    "niras": partial(niras.extract, source_name="niras"),
    # --- BearingPoint Sweden ---
    "bearingpoint_sweden": partial(bearingpoint.extract, source_name="bearingpoint_sweden"),
    # --- Axis Communications (Workday, dynamic) ---
    "axis_comms": partial(workday.extract, source_name="axis_comms"),
}


def get_extractor(name: str) -> ExtractorFn | None:
    return REGISTRY.get(name)
