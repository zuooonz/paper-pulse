import datetime as dt

import pytest

from api.routers.literature_search import LiteratureSearchRequest
from paper_library import time_utils
from scripts import literature_search


def test_recent_search_window_uses_configured_local_date(monkeypatch):
    monkeypatch.setenv("APP_TIMEZONE", "America/Los_Angeles")
    monkeypatch.setattr(
        time_utils,
        "utc_now",
        lambda: dt.datetime(2026, 1, 1, 2, tzinfo=dt.UTC),
    )

    start, end = literature_search._recent_date_window(30)

    assert end == dt.date(2025, 12, 31)
    assert start == dt.date(2025, 12, 1)
    assert literature_search._scholar_year_range((start, end)) == (2025, 2025)


def test_search_sources_use_the_same_precomputed_date_window(monkeypatch):
    start = dt.date(2025, 12, 1)
    end = dt.date(2025, 12, 31)
    requests = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class FakeHttp:
        def get(self, url, *, params, timeout):
            requests.append((url, params, timeout))
            if url.endswith("esearch.fcgi"):
                payload = {"esearchresult": {"idlist": [], "count": "0"}}
            else:
                payload = {"resultList": {"result": []}, "nextCursorMark": "*"}
            return FakeResponse(payload)

    monkeypatch.setattr(literature_search, "HTTP", FakeHttp())
    monkeypatch.setattr(literature_search, "NCBI_BASE_URL", "https://ncbi.example")
    monkeypatch.setattr(literature_search, "EUROPE_PMC_BASE_URL", "https://europe.example")

    literature_search.search_pmc(date_window=(start, end))
    literature_search.search_europe_pmc(date_window=(start, end), max_results=1)

    assert requests[0][1]["mindate"] == "2025/12/01"
    assert requests[0][1]["maxdate"] == "2025/12/31"
    assert "FIRST_PDATE:[2025-12-01 TO 2025-12-31]" in requests[1][1]["query"]


def test_literature_search_request_validates_days_dates_and_years():
    assert LiteratureSearchRequest().days == 60
    assert LiteratureSearchRequest(mode="recent", days=1095).days == 1095
    with pytest.raises(ValueError):
        LiteratureSearchRequest(mode="recent", days=1096)
    with pytest.raises(ValueError):
        LiteratureSearchRequest.model_validate({"mode": "recent", "months": 2})

    request = LiteratureSearchRequest(
        mode="range", date_from="2023-02-28", date_to="2026-02-28"
    )
    assert request.date_from == dt.date(2023, 2, 28)
    with pytest.raises(ValueError, match="起始日期"):
        LiteratureSearchRequest(
            mode="range", date_from="2026-01-02", date_to="2026-01-01"
        )
    with pytest.raises(ValueError, match="三年"):
        LiteratureSearchRequest(
            mode="range", date_from="2023-02-28", date_to="2026-03-01"
        )
    with pytest.raises(ValueError, match="起止年份"):
        LiteratureSearchRequest(mode="years")
