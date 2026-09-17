# Probe fixtures

Handwritten pages for `tests/test_source_probe.py`, for the cases no captured
page covers: a client-rendered shell, a careers page that embeds a hosted board,
a bespoke listing on no supported ATS, and a robots.txt that says no. The
employers are invented (Contoso, Fabrikam, Litware) and nothing here was
fetched from a live site.

Everything else the probe tests read is a real capture one directory up
(`kognity.html`, `storytel.html`, `busuu.html`, `path.html`,
`novo_nordisk*.html`, `dsv.html`, `givewell.json`), made by
`scripts/capture_fixtures.py`. The probe itself never writes here.
