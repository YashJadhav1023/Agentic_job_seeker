"""Career-page scan with Firecrawl mocked: finds the careers page, keeps relevant titles, saves external jobs."""
import json

import pytest

import career_scan
import core


@pytest.fixture
def profile():
    return {"search": {"title_include": "devops|cloud|sre", "title_exclude": "senior|lead|manager"}}


def _fake_post(path, body, timeout=90):
    if path == "/map":
        return {"links": [{"url": "https://acme.com/about"}, {"url": "https://acme.com/company/careers/team"},
                          {"url": "https://acme.com/careers"}]}
    return {"data": {"json": {"jobs": [
        {"title": "DevOps Engineer", "location": "Pune", "url": "/careers/devops-1", "description": "Kubernetes, AWS"},
        {"title": "Senior DevOps Engineer", "url": "/careers/devops-2"},
        {"title": "Account Executive", "url": "/careers/ae-1"},
        {"title": "Cloud Engineer", "url": "https://acme.com/careers/cloud-1"}]}}}


def test_finds_shortest_careers_url(monkeypatch):
    monkeypatch.setattr(career_scan, "_post", _fake_post)
    assert career_scan.find_career_url("acme.com") == "https://acme.com/careers"


def test_scan_saves_only_relevant_external_jobs(monkeypatch, profile):
    monkeypatch.setattr(career_scan, "_post", _fake_post)
    assert career_scan.scan_company(profile, "acme.com") == 2
    with core.db() as c:
        rows = c.execute("SELECT title, url, source, apply_type, company_site FROM jobs ORDER BY title").fetchall()
    assert [(r["title"], r["url"]) for r in rows] == [
        ("Cloud Engineer", "https://acme.com/careers/cloud-1"),
        ("DevOps Engineer", "https://acme.com/careers/devops-1")]
    assert {r["source"] for r in rows} == {"career"} and {r["apply_type"] for r in rows} == {"external"}
    assert career_scan.scan_company(profile, "acme.com") == 0  # same jobs are not saved twice


def test_run_needs_a_key(monkeypatch):
    monkeypatch.delenv("JOBHUB_FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    assert "error" in career_scan.run({"companies": ["acme.com"]})


def test_run_stops_when_credits_run_out(monkeypatch):
    monkeypatch.setenv("JOBHUB_FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(core, "load_profile", lambda: {"search": {"title_include": "devops", "title_exclude": "senior"}})

    def broke(path, body, timeout=90):
        raise career_scan.OutOfCredits("Firecrawl credits exhausted")

    monkeypatch.setattr(career_scan, "_post", broke)
    out = career_scan.run({"companies": ["a.com", "b.com"]})
    assert out["scanned"] == 0 and "exhausted" in out["stopped"]


def test_tool_is_registered():
    import server
    assert "career_scan" in server.HANDLERS and "career_scan" in {t["name"] for t in server.tool_list()}
    json.dumps(server.tool_list())
