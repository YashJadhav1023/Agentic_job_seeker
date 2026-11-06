"""multi_board_search: Naukri job pages are never fetched one by one (Naukri answers 406 'rate limited' to them)."""
import core
import jobspy_tool

PROFILE = {"search": {"keywords": ["DevOps"], "locations": ["Pune"], "title_include": "devops", "title_exclude": "senior"}}


def _fake_scrape(calls):
    def scrape(**kw):
        calls.append(kw)
        return [{"site": s, "title": f"DevOps Engineer {s}", "job_url": f"https://{s}.example.com/1"} for s in kw["site_name"]]
    return scrape


def test_naukri_is_searched_without_per_job_detail_requests(monkeypatch):
    monkeypatch.setattr(core, "load_profile", lambda: PROFILE)
    calls = []
    res = jobspy_tool.multi_board_search(
        {"sites": ["linkedin", "indeed", "naukri"], "fetch_description": True}, scrape=_fake_scrape(calls))
    by_sites = {tuple(c["site_name"]): c["linkedin_fetch_description"] for c in calls}
    assert by_sites == {("linkedin", "indeed"): True, ("naukri",): False}
    assert sorted(j["site"] for j in res["jobs"]) == ["indeed", "linkedin", "naukri"]


def test_single_call_when_descriptions_are_not_requested(monkeypatch):
    monkeypatch.setattr(core, "load_profile", lambda: PROFILE)
    calls = []
    res = jobspy_tool.multi_board_search({"sites": ["linkedin", "naukri"]}, scrape=_fake_scrape(calls))
    assert [c["site_name"] for c in calls] == [["linkedin", "naukri"]]
    assert res["count"] == 2


def test_naukri_only_search_still_skips_detail_requests(monkeypatch):
    monkeypatch.setattr(core, "load_profile", lambda: PROFILE)
    calls = []
    jobspy_tool.multi_board_search({"sites": ["naukri"], "fetch_description": True}, scrape=_fake_scrape(calls))
    assert [(c["site_name"], c["linkedin_fetch_description"]) for c in calls] == [(["naukri"], False)]
