# Probe fixtures

Handwritten pages for `tests/test_source_probe.py`, for the cases no captured
page covers: a client-rendered shell, a careers page that embeds a hosted board,
a bespoke listing on no supported ATS, and a robots.txt that says no. The
employers are invented (Contoso, Fabrikam, Litware) and nothing here was
fetched from a live site.

Everything else the probe tests read is a real capture one directory up
(`kognity.html`, `storytel.html`, `path.rendered.html` with `path*.json`,
`novo_nordisk*.html`, `dsv.html`, `givewell.json`), made by
`scripts/capture_fixtures.py`. `path.rendered.html` is path's rendered listing
from before SP3b moved Workday to its JSON walk, kept for the probe's page
rungs under a name the capture script does not overwrite. The probe itself
never writes here.
