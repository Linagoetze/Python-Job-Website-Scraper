# Fixtures

Captured by `scripts/capture_fixtures.py` from the live sites, except where a
page is named below as invented.

## Invented pages

- `nutrition_international.second_page.json` (SP13) is **invented**: one
  posting (`0000INVENT1`, an "invented second page" title), `total: 11`, no
  `nextPage`. Workable's captured first page, `nutrition_international.json`,
  is real and says `total: 11` beside ten results and a token, but no live
  board here holds more than ten, so there was no real second page to capture.
  `tests/fixture_cases.py` (`INVENTED_PAGES`) replays it after the captured
  page. Its name is not one `capture_fixtures._page_of` reads as a page of the
  source (`name.pN.ext`), so a re-capture neither overwrites nor deletes it.
  Do not scrape another Workable employer to replace it.

`probe/` holds hand-written pages of its own; see its README.
