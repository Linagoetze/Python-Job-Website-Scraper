"""Guards that apply to the whole suite.

The rule the tests live by is that a test never touches the owner's real
`data/` directory. WP10 found the one hole in it: `run_pipeline` opened
`http_cache()` with no path, so every pipeline test read, wrote and — on the
way out of the block — pruned the live response cache. It emptied a 37 MB
cache during that session, and a populated one made the suite four times
slower.

`run_pipeline` now takes `cache_path`, but a parameter is only as good as the
next call site that remembers it. The guard therefore sits here: opening the
response cache without saying where it goes fails loudly, wherever the call
came from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from job_scraper import http as http_mod
from job_scraper import pipeline as pipeline_mod

_MESSAGE = (
    "A test opened http_cache() without a path, which resolves to the owner's "
    "live response cache at {path}. Pass an explicit path — "
    "run_pipeline(..., cache_path=tmp_path / 'http_cache.sqlite3')."
)


@pytest.fixture(autouse=True)
def never_the_live_response_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse the default cache path for the duration of every test."""
    real = http_mod.http_cache
    live = http_mod.default_http_cache_path()

    def guarded(path: Path | None = None, ttl: int = http_mod.DEFAULT_CACHE_TTL) -> Any:
        if path is None or Path(path) == live:
            raise AssertionError(_MESSAGE.format(path=live))
        return real(path=path, ttl=ttl)

    # Patched in both modules: pipeline.py imported the name directly, so
    # rebinding it on http alone would leave the call that caused all this
    # unguarded.
    monkeypatch.setattr(http_mod, "http_cache", guarded)
    monkeypatch.setattr(pipeline_mod, "http_cache", guarded)
