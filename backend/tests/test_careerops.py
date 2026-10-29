"""career-ops scanner with the node runner and HTTP mocked; post link cleanup; education given as one line of text."""
import json
import subprocess
from types import SimpleNamespace

import pytest

import ats_directory
import career_apply
import careerops
import core
import post_scanner


@pytest.fixture
def prof():
    return {"search": {"title_include": "devops|cloud|sre|site reliability|platform", "title_exclude": "senior|lead|manager",
                       "keywords": ["DevOps Engineer", "Site Reliability Engineer"]}}


def _ok(status=200, url=""):
    return lambda u, **k: SimpleNamespace(status_code=status, url=url or u, close=lambda: None)


RESULTS = [{"name": "Acme", "provider": "greenhouse", "error": None, "jobs": [
    {"title": "DevOps Engineer", "url": "https://job-boards.greenhouse.io/acme/jobs/1111", "location": "Pune",
     "description": "AWS, Kubernetes", "postedAt": "2026-10-20T00:00:00Z", "externalId": "1111"},
    {"title": "Senior DevOps Engineer", "url": "https://job-boards.greenhouse.io/acme/jobs/2222"},
    {"title": "Account Executive", "url": "https://job-boards.greenhouse.io/acme/jobs/3333"},
    {"title": "Site Reliability Engineer", "url": "https://job-boards.greenhouse.io/acme/jobs/4444"}]},
    {"name": "Gone", "provider": None, "jobs": [], "error": "HTTP 404 Not Found"}]


def test_scan_boards_uses_the_stdin_stdout_contract(monkeypatch):
    seen = {}

    def run(cmd, input, **k):
        seen["cmd"], seen["input"] = cmd, json.loads(input)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"results": RESULTS}), stderr="")

    monkeypatch.setattr(careerops, "runtime", lambda: ["/usr/bin/node"])
    out = careerops.scan_boards([{"name": "Acme", "careers_url": "https://job-boards.greenhouse.io/acme"}], 50, run=run)
    assert out == RESULTS
    assert seen["cmd"][1].endswith("run_scan.mjs") and seen["input"]["max_jobs"] == 50
    assert careerops.scan_boards([], run=run) == []


def test_scan_boards_reports_runner_failures(monkeypatch):
    monkeypatch.setattr(careerops, "runtime", lambda: ["node"])
    bad = lambda *a, **k: SimpleNamespace(returncode=0, stdout="warning: not json", stderr="")  # noqa: E731
    with pytest.raises(careerops.CareerOpsUnavailable):
        careerops.scan_boards([{"name": "A", "careers_url": "x"}], run=bad)

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("node", 1)
    with pytest.raises(careerops.CareerOpsUnavailable):
        careerops.scan_boards([{"name": "A", "careers_url": "x"}], run=slow)


def test_title_gate_needs_profile_filter_and_keyword_match(prof):
    assert careerops.title_matches(prof, "DevOps Engineer")
    assert careerops.title_matches(prof, "Site Reliability Engineer II")
    assert not careerops.title_matches(prof, "Senior DevOps Engineer")      # profile exclude
    assert not careerops.title_matches(prof, "Cloud Sales Specialist")      # passes include, no keyword match
    assert not careerops.title_matches(prof, "")


def test_liveness():
    assert careerops.is_live("https://x.io/jobs/1234", http=_ok(200)) is True
    assert careerops.is_live("https://x.io/jobs/1234", http=_ok(404)) is False
    assert careerops.is_live("https://boards.greenhouse.io/figma/jobs/99999", http=_ok(200, "https://www.figma.com/careers/")) is False
    assert careerops.is_live("https://x.io/jobs/1234", http=_ok(403)) is None   # bot wall is not proof of a dead job
    assert careerops.is_live("not a url") is False


def test_save_results_keeps_live_matching_roles_once(prof):
    boards = {"Acme": {"provider": "greenhouse", "url": "https://job-boards.greenhouse.io/acme", "domain": "acme.io"}}
    logs = []
    out = careerops.save_results(prof, RESULTS, boards, http=_ok(200), log=logs.append)
    assert out == {"saved": 2, "matched": 2, "dead": 0, "errors": 1}
    with core.db() as c:
        rows = [dict(r) for r in c.execute("SELECT title, source, apply_type, apply_url, company, company_site, posted FROM jobs ORDER BY title")]
    assert [r["title"] for r in rows] == ["DevOps Engineer", "Site Reliability Engineer"]
    assert {r["source"] for r in rows} == {"careerops"} and {r["apply_type"] for r in rows} == {"external"}
    assert rows[0]["apply_url"] == "https://job-boards.greenhouse.io/acme/jobs/1111" and rows[0]["company_site"] == "acme.io"
    assert rows[0]["posted"] == "2026-10-20"
    assert careerops.save_results(prof, RESULTS, boards, http=_ok(200), log=logs.append)["saved"] == 0
    assert core.kv_get("careerops:board:" + ats_directory.norm("Gone")).get("url") is None  # 404 board forgotten


def test_dead_links_are_not_saved(prof):
    out = careerops.save_results(prof, RESULTS[:1], {}, http=_ok(404), log=lambda m: None)
    assert out["saved"] == 0 and out["dead"] == 2


def test_discover_prefers_directory_then_probe(monkeypatch):
    monkeypatch.setattr(ats_directory, "lookup", lambda name, slugs=None: [("workday", "https://x.wd1.myworkdayjobs.com/a"),
                                                                           ("lever", "https://jobs.lever.co/acme")])
    assert careerops.discover("Acme")["url"] == "https://jobs.lever.co/acme"
    monkeypatch.setattr(ats_directory, "lookup", lambda name, slugs=None: [])
    monkeypatch.setattr(careerops.career_resolve, "ats_jobs", lambda s, http=None: ("ashby", [{}]) if s == "acmerobotics" else ("", []))
    assert careerops.discover("Acme Robotics Pvt Ltd")["url"] == "https://jobs.ashbyhq.com/acmerobotics"
    assert careerops.discover("Acme Robotics", probe=False) == {}


def test_full_name_slugs_drop_the_bare_first_word():
    assert "capital" not in careerops._full_name_slugs("Capital One")
    assert "anthropic" in careerops._full_name_slugs("Anthropic")


def test_run_end_to_end(monkeypatch, prof):
    monkeypatch.setattr(careerops, "runtime", lambda: ["node"])
    monkeypatch.setattr(careerops, "board_for", lambda n, d="": {"provider": "greenhouse", "url": f"https://job-boards.greenhouse.io/{n.lower()}"})
    monkeypatch.setattr(careerops, "scan_boards", lambda entries, max_jobs=300: RESULTS)
    monkeypatch.setattr(careerops, "is_live", lambda url, http=None: True)
    out = careerops.run({"companies": ["Acme", "Gone"], "profile": prof}, log=lambda m: None)
    assert out["boards"] == 2 and out["saved"] == 2 and out["errors"] == 1


def test_run_without_a_js_runtime(monkeypatch):
    monkeypatch.setattr(careerops, "runtime", lambda: [])
    assert "error" in careerops.run({"companies": ["Acme"]})


# ---------------------------------------------------------------- post links
def test_clean_link_unwraps_and_drops_non_job_links():
    wrap = "https://www.linkedin.com/safety/go/?url=https%3A%2F%2Facme.io%2Fcareers%2F12&urlhash=x"
    assert post_scanner.clean_link(wrap) == "https://acme.io/careers/12"
    assert post_scanner.clean_link("https://www.linkedin.com/safety/go/?url=http%3A%2F%2FB%2ETech&urlhash=4dZl") == ""
    page = '<a class="artdeco-button" href="https://whatsapp.com/channel/abc">Go</a>'
    short = lambda u, **k: SimpleNamespace(text=page, url=u)  # noqa: E731
    assert post_scanner.clean_link("https://lnkd.in/gTvM2vfi", http=short) == ""
    page2 = '<a class="artdeco-button artdeco-button--primary" href="https://jobs.lever.co/acme/123">Go</a>'
    assert post_scanner.clean_link("https://lnkd.in/abc", http=lambda u, **k: SimpleNamespace(text=page2, url=u)) == \
        "https://jobs.lever.co/acme/123"


# ---------------------------------------------------------------- education as text (resume import)
def test_facts_accept_education_as_one_line():
    f = career_apply.facts({"applicant": {"name": "Yash Jadhav", "location": None, "education":
                            "B.Tech Computer Science & Engineering, V.V.P Institute of Technology, Solapur (2022-2025), CGPA 6.84"},
                            "experience": None, "compensation": None})
    assert f["degree_level"] == "Bachelor's" and f["degree"].startswith("B.Tech")
    assert f["university"] == "V.V.P Institute of Technology" and f["grad_year"] == "2025" and f["gpa"] == "6.84"
    assert career_apply.facts({"applicant": {"education": {"degree": "BE"}}})["degree"] == "BE"
    assert career_apply.facts({"applicant": {}})["degree"] == ""


# ---------------------------------------------------------------- freehire (open catalogue of company career-page jobs)
FH = {"data": [
    {"title": "DevOps Engineer", "company": "Comscore, Inc.", "location": "IND - Pune, India", "source": "workday",
     "url": "https://comscore.wd5.myworkdayjobs.com/External/job/IND---Pune-India/DevOps_R1234?utm_source=freehire.me",
     "description": "Kubernetes", "posted_at": "2026-10-28T10:00:00Z", "closed_at": None, "external_id": "R1234",
     "cities": ["Pune"], "work_mode": "onsite"},
    {"title": "DevOps Engineer", "company": "TransUnion", "location": "Chennai", "source": "workday", "cities": ["Chennai"],
     "url": "https://transunion.wd5.myworkdayjobs.com/x/job/Chennai/DevOps_R9?utm_source=freehire.me", "closed_at": None},
    {"title": "Senior DevOps Engineer", "company": "Acme", "location": "Pune", "cities": ["Pune"],
     "url": "https://jobs.lever.co/acme/1", "closed_at": None},
    {"title": "Site Reliability Engineer", "company": "Gone", "location": "Bengaluru", "cities": ["Bengaluru"],
     "url": "https://jobs.lever.co/gone/2", "closed_at": "2026-10-20T00:00:00Z"},
    {"title": "Site Reliability Engineer", "company": "Teikametrics", "location": "Bangalore / Remote", "cities": [],
     "work_mode": "remote", "url": "https://job-boards.greenhouse.io/teikametrics/jobs/4361177009", "closed_at": None}]}


@pytest.fixture
def fh_prof(prof):
    return {**prof, "search": {**prof["search"], "locations": ["Pune", "Bangalore", "Remote"]}}


def test_freehire_search_asks_for_company_sources_only():
    seen = {}

    def get(url, params=None, **k):
        seen.update(url=url, params=params)
        return SimpleNamespace(status_code=200, json=lambda: FH)
    jobs = careerops.freehire_search("DevOps Engineer", http=get)
    assert seen["url"].endswith("/api/v1/agent/jobs/search")
    assert seen["params"]["q"] == "DevOps Engineer" and seen["params"]["source_type"] == "ats"
    assert seen["params"]["countries"] == "in"
    assert len(jobs) == 5
    assert careerops.freehire_search("x", http=lambda u, **k: SimpleNamespace(status_code=503, json=lambda: {})) == []


def test_location_filter_uses_profile_cities_and_aliases(fh_prof):
    assert careerops.location_ok(fh_prof, FH["data"][0])          # Pune
    assert not careerops.location_ok(fh_prof, FH["data"][1])      # Chennai
    assert careerops.location_ok(fh_prof, FH["data"][4])          # remote / Bangalore
    assert careerops.location_ok({"search": {"locations": []}}, FH["data"][1])  # no cities set: keep all


def test_freehire_run_saves_open_matching_company_jobs(monkeypatch, fh_prof):
    monkeypatch.setattr(careerops, "freehire_search", lambda q, **k: FH["data"])
    out = careerops.freehire_run(fh_prof, http=_ok(200), log=lambda m: None)
    assert out["saved"] == 2 and out["closed"] == 1
    with core.db() as c:
        rows = [dict(r) for r in c.execute("SELECT title, company, source, url, apply_url, posted FROM jobs ORDER BY company")]
    assert [r["company"] for r in rows] == ["Comscore, Inc.", "Teikametrics"]
    assert {r["source"] for r in rows} == {"freehire"}
    assert "utm_source" not in rows[0]["url"] and rows[0]["apply_url"] == rows[0]["url"]
    assert rows[0]["posted"] == "2026-10-28"
    assert careerops.freehire_run(fh_prof, http=_ok(200), log=lambda m: None)["saved"] == 0  # no duplicates


def test_run_includes_freehire_unless_turned_off(monkeypatch, prof):
    monkeypatch.setattr(careerops, "runtime", lambda: ["node"])
    monkeypatch.setattr(careerops, "freehire_run", lambda p, **k: {"saved": 3})
    out = careerops.run({"companies": [], "profile": prof}, log=lambda m: None)
    assert out["freehire"] == {"saved": 3}
    assert "freehire" not in careerops.run({"companies": [], "profile": prof, "freehire": False}, log=lambda m: None)


def test_freehire_url_drops_tracking_anywhere_and_respects_stop(monkeypatch, fh_prof):
    assert careerops.untrack("https://a.io/j/1?utm_source=freehire.me") == "https://a.io/j/1"
    assert careerops.untrack("https://a.io/j/1?utm_source=freehire.me&gh_jid=9") == "https://a.io/j/1?gh_jid=9"
    assert careerops.untrack("https://a.io/j/1?gh_jid=9&utm_source=freehire.me&x=1") == "https://a.io/j/1?gh_jid=9&x=1"
    monkeypatch.setattr(careerops, "freehire_search", lambda q, **k: FH["data"])
    monkeypatch.setattr(careerops.autopilot, "stopped", lambda: True)
    assert careerops.freehire_run(fh_prof, http=_ok(200), log=lambda m: None)["saved"] == 0
