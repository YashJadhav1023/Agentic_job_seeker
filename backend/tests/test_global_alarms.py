"""Global search + apply, alarms, bulk post scan helpers, LinkedIn write, one-tap remote browser link."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hub"))
TOKEN = "t" * 40
os.environ["JOBHUB_API_TOKEN"] = TOKEN

import autopilot  # noqa: E402
import browser_access  # noqa: E402
import core  # noqa: E402
import globalsearch  # noqa: E402
import post_scanner  # noqa: E402
import scheduler  # noqa: E402
import server  # noqa: E402

H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def prof(tmp_path, monkeypatch):
    src = (Path(__file__).resolve().parent.parent / "profile.example.yaml").read_text()
    src = src.replace("/app/data/resume.pdf", str(tmp_path / "resume.pdf"))
    f = tmp_path / "profile.yaml"
    f.write_text(src)
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(core, "PROFILE_PATH", f)
    monkeypatch.setattr(globalsearch, "RESULTS", tmp_path / "results")
    return core.load_profile()


# ---------------------------------------------------------------- alarms
def test_alarm_validation_messages():
    assert "HH:MM" in scheduler.alarm_set({"time": "25:00", "tasks": ["bulk_email"]})["error"]
    assert "tasks" in scheduler.alarm_set({"time": "09:00", "tasks": ["nope"]})["error"]
    assert "day" in scheduler.alarm_set({"time": "09:00", "tasks": ["bulk_email"], "days": ["funday"]})["error"]
    assert "unknown alarm id" in scheduler.alarm_set({"id": "zzz", "time": "09:00", "tasks": ["bulk_email"]})["error"]


def test_alarm_create_update_delete():
    r = scheduler.alarm_set({"time": "9:5", "tasks": ["find_emails", "bulk_email"], "days": ["mon", "wed"]})
    al = r["alarm"]
    assert al["time"] == "09:05" and al["days"] == ["mon", "wed"] and al["enabled"] is True and r["next"]
    r2 = scheduler.alarm_set({"id": al["id"], "enabled": False})
    assert r2["alarm"]["enabled"] is False and r2["alarm"]["time"] == "09:05" and r2["next"] is None
    assert scheduler.alarm_delete({"id": al["id"]}) == {"deleted": al["id"]}
    assert scheduler.alarms() == []


def test_all_expands_to_the_five_daily_tasks():
    al = scheduler.alarm_set({"time": "08:00", "tasks": ["all"]})["alarm"]
    assert {"find_emails", "send_emails", "bulk_email", "linkedin_apply", "naukri_apply"} <= set(al["tasks"])
    assert "autopilot" not in al["tasks"]


def test_alarm_fires_once_a_day_within_the_catch_up_window(monkeypatch, prof):
    al = scheduler.alarm_set({"time": "09:30", "tasks": ["bulk_email"]})["alarm"]
    monkeypatch.setattr(autopilot, "stopped", lambda: False)
    started = []
    monkeypatch.setattr(scheduler, "run_alarm", lambda a: started.append(a["id"]) or [{"task": "bulk_email"}])
    day = datetime(2026, 10, 28)
    assert scheduler.tick(day.replace(hour=9, minute=29)) == {}                       # not yet
    assert "alarm:" + al["id"] in scheduler.tick(day.replace(hour=9, minute=30))      # fires
    assert scheduler.tick(day.replace(hour=9, minute=31)) == {}                       # not twice the same day
    assert scheduler.tick(datetime(2026, 10, 29, 9, 30)) != {}                        # next day again
    assert started == [al["id"], al["id"]]


def test_alarm_catch_up_and_expiry(monkeypatch, prof):
    scheduler.alarm_set({"time": "09:00", "tasks": ["bulk_email"]})
    assert len(scheduler.due_alarms(datetime(2026, 10, 28, 10, 20))) == 1             # server was down, back within 90 min
    assert scheduler.due_alarms(datetime(2026, 10, 28, 10, 31)) == []                 # too late: skip today


def test_alarm_days_filter(monkeypatch):
    scheduler.alarm_set({"time": "07:00", "tasks": ["bulk_email"], "days": ["sat", "sun"]})
    assert scheduler.due_alarms(datetime(2026, 10, 28, 7, 0)) == []                   # Wednesday
    assert len(scheduler.due_alarms(datetime(2026, 10, 31, 7, 0))) == 1               # Saturday


def test_kill_switch_stops_alarms(monkeypatch, prof):
    scheduler.alarm_set({"time": "09:30", "tasks": ["bulk_email"]})
    monkeypatch.setattr(autopilot, "stopped", lambda: True)
    monkeypatch.setattr(scheduler, "run_alarm", lambda a: pytest.fail("must not run"))
    out = scheduler.tick(datetime(2026, 10, 28, 9, 30))
    assert list(out.values())[0] == {"skipped": "kill-switch on"}


def test_run_alarm_starts_every_task_and_queues_overflow(monkeypatch, prof):
    called = []
    monkeypatch.setitem(server.HANDLERS, "find_hr_emails", lambda a: called.append(("find", a)) or {"run_id": "r1"})
    monkeypatch.setitem(server.HANDLERS, "apply_jobs", lambda a: called.append(("apply", a["platform"])) or {"run_id": "r2", "queued": True})
    monkeypatch.setitem(server.HANDLERS, "autopilot_run", lambda a: called.append(("auto", a)) or {"run_id": "r3"})
    spawned = []
    monkeypatch.setattr(core, "spawn", lambda kind, argv, *a, **k: spawned.append((kind, argv)) or {"run_id": "r4"})
    al = scheduler.alarm_set({"time": "08:00", "tasks": ["all"], "dry_run": True})["alarm"]
    res = scheduler.run_alarm(al)
    assert [r["task"] for r in res] == al["tasks"]
    assert any(r["queued"] for r in res) and all(r["run_id"] for r in res)
    assert ("apply", "linkedin") in called and ("apply", "naukri") in called
    assert any(c[0] == "auto" and c[1]["email"] and c[1]["discover"] is False and c[1]["dry_run"] is True for c in called)
    assert spawned and spawned[0][0] == "task-send_hr_emails" and "toolrun.py" in spawned[0][1][1]


def test_one_failing_task_does_not_stop_the_rest(monkeypatch, prof):
    def boom(a):
        raise RuntimeError("no chrome")
    monkeypatch.setitem(server.HANDLERS, "apply_jobs", boom)
    monkeypatch.setitem(server.HANDLERS, "find_hr_emails", lambda a: {"run_id": "ok"})
    res = scheduler.run_alarm({"id": "x", "tasks": ["linkedin_apply", "find_emails"], "bulk_limit": 5, "dry_run": None})
    assert "no chrome" in res[0]["error"] and res[1]["run_id"] == "ok"


# ---------------------------------------------------------------- global search
def _job(i, title="DevOps Engineer", site="linkedin", company=None):
    return {"site": site, "title": title, "company": company or f"Co{i}", "location": "Pune", "url": f"https://jobs.example/{site}/{i}"}


def test_global_search_saves_scores_and_ranks(prof, monkeypatch):
    calls = []

    def fake(a):
        calls.append(a)
        jobs = [_job(len(calls) * 10 + n) for n in range(2)]
        for j in jobs:
            autopilot.save_job({"source": "jobspy-linkedin", "ext_id": "", "url": j["url"], "title": j["title"], "company": j["company"],
                                "location": "Pune", "posted": "", "description": "Kubernetes Terraform Docker AWS CI/CD " * 10, "apply_type": "unknown"})
        return {"jobs": jobs, "filtered_out_by_title": 3}

    out = globalsearch.run({"sites": ["linkedin", "naukri"], "max_pairs": 2, "locations": ["Pune", "Mumbai"]}, search=fake,
                           log=lambda m: None)
    assert len(calls) == 2 and calls[0]["sites"] == ["linkedin", "naukri"] and calls[0]["save"] is True
    assert {c["location"] for c in calls} == {"Pune", "Mumbai"}   # every chosen city, keywords rotate
    assert out["found"] == 4
    res = json.loads(next(globalsearch.RESULTS.glob("*.json")).read_text())
    scores = [j["score"] for j in res["jobs"]]
    assert scores == sorted(scores, reverse=True) and all("id" in j for j in res["jobs"])


def test_global_search_auto_apply_hands_good_jobs_to_apply_step(prof, monkeypatch):
    def fake(a):
        j = _job(1)
        autopilot.save_job({"source": "jobspy-linkedin", "ext_id": "", "url": j["url"], "title": j["title"], "company": "Acme",
                            "location": "Pune", "posted": "", "description": "Kubernetes Terraform Docker AWS CI/CD Jenkins Linux " * 12,
                            "apply_type": "unknown"})
        return {"jobs": [j], "filtered_out_by_title": 0}

    seen = {}
    monkeypatch.setattr(server, "apply_to_jobs", lambda a: seen.update(a) or {"run_id": "sel-1", "note": "x"})
    monkeypatch.setattr(autopilot, "score_new", lambda p, c: (_ for _ in ()).throw(AssertionError) if False else _fake_score())
    out = globalsearch.run({"auto_apply": True, "dry_run": True, "max_pairs": 1}, search=fake, log=lambda m: None)
    assert seen["job_ids"] and seen["dry_run"] is True
    assert out["auto_apply"]["run_id"] == "sel-1"


def _fake_score():
    with core.db() as c:
        c.execute("UPDATE jobs SET status='scored', score=88, matched='[\"Kubernetes\"]' WHERE status='new'")
    return {"scored": 1, "skipped": 0}


def test_search_results_tool_reads_file_and_refreshes_status(prof, monkeypatch):
    globalsearch.RESULTS.mkdir(parents=True)
    autopilot.save_job({"source": "x", "ext_id": "", "url": "https://a.example/1", "title": "DevOps", "company": "A", "location": "",
                        "posted": "", "description": "", "apply_type": "unknown"})
    jid = autopilot._job_id("https://a.example/1")
    (globalsearch.RESULTS / "search-global-1.json").write_text(json.dumps({"jobs": [{"id": jid, "title": "DevOps", "status": "scored"}]}))
    monkeypatch.setattr(core, "ROOT", globalsearch.RESULTS.parent.parent)
    (core.ROOT / "data").mkdir(exist_ok=True)
    (core.ROOT / "data" / "search_results").mkdir(exist_ok=True)
    (core.ROOT / "data" / "search_results" / "search-global-1.json").write_text(
        json.dumps({"jobs": [{"id": jid, "title": "DevOps", "status": "scored"}]}))
    autopilot.set_job(jid, status="emailed", contact="hr@a.example")
    res = server.search_results({"run_id": "search-global-1"})
    assert res["jobs"][0]["status"] == "emailed" and res["jobs"][0]["contact"] == "hr@a.example"
    assert "error" in server.search_results({"run_id": "../../etc/passwd"})


# ---------------------------------------------------------------- apply to picked jobs
def test_apply_to_jobs_is_dry_run_by_default_and_queues_a_run(prof, monkeypatch):
    got = {}
    monkeypatch.setattr(core, "spawn", lambda kind, argv, cwd, env, dry, args: got.update(kind=kind, argv=argv, dry=dry, args=args) or {"run_id": "r"})
    r = server.apply_to_jobs({"job_ids": [3, 4]})
    assert r["run_id"] == "r" and got["dry"] is True and got["kind"] == "autopilot-selected"
    assert got["args"]["job_ids"] == [3, 4] and got["args"]["apply"] is False and got["args"]["discover"] is False


def test_apply_to_jobs_live_needs_the_safety_switch(prof):
    r = server.apply_to_jobs({"job_ids": [1], "dry_run": False})
    assert "blocked" in r["error"]
    assert "empty" in server.apply_to_jobs({"job_ids": []})["error"]


def test_picked_jobs_get_another_chance_and_are_emailed(prof, monkeypatch):
    autopilot.save_job({"source": "x", "ext_id": "", "url": "https://c.example/1", "title": "DevOps Engineer", "company": "Acme",
                        "location": "Pune", "posted": "", "description": "Kubernetes Docker Terraform AWS " * 20, "apply_type": "unknown"})
    jid = autopilot._job_id("https://c.example/1")
    autopilot.set_job(jid, status="skip", reason="score 30 < 45", score=30)
    monkeypatch.setattr(autopilot, "pick_contact", lambda *a, **k: {"email": "careers@acme.example", "confidence": "high", "source": "site"})
    monkeypatch.setattr(autopilot, "stopped", lambda: False)
    res = autopilot.run({"job_ids": [jid], "dry_run": True, "discover": False, "posts": False, "career": False, "apply": False, "form": False})
    assert res["email"]["dry_drafts"] and res["email"]["dry_drafts"][0]["to"] == "careers@acme.example"
    # a job that was not picked is not touched by the same run
    autopilot.save_job({"source": "x", "ext_id": "", "url": "https://c.example/2", "title": "DevOps Engineer", "company": "Other",
                        "location": "Pune", "posted": "", "description": "", "apply_type": "unknown"})
    other = autopilot._job_id("https://c.example/2")
    autopilot.set_job(other, status="skip")
    autopilot.run({"job_ids": [jid], "dry_run": True, "discover": False, "posts": False, "career": False, "apply": False, "form": False})
    with core.db() as c:
        assert c.execute("SELECT status FROM jobs WHERE id=?", (other,)).fetchone()[0] == "skip"


# ---------------------------------------------------------------- LinkedIn search can write
def test_linkedin_search_save_and_auto_apply(prof, monkeypatch):
    results = {"results": [{"id": "111", "title": "DevOps Engineer", "company": "Acme", "location": "Pune", "date": "1d",
                            "url": "https://www.linkedin.com/jobs/view/111"},
                           {"id": "112", "title": "Senior DevOps Lead", "company": "Big", "location": "Pune", "date": "1d",
                            "url": "https://www.linkedin.com/jobs/view/112"}]}

    def fake_bun(args, timeout=75):
        return json.dumps(results) if args[0] == "search" else "Kubernetes Terraform Docker AWS CI/CD Jenkins Linux " * 12

    monkeypatch.setattr(server, "_bun", fake_bun)
    plain = server.search_jobs({"query": "devops"})
    assert "saved_to_hub" not in plain and plain["kept_after_title_filter"] == 1
    seen = {}
    monkeypatch.setattr(server, "apply_to_jobs", lambda a: seen.update(a) or {"run_id": "sel"})
    out = server.search_jobs({"query": "devops", "auto_apply": True, "dry_run": True})
    assert out["saved_to_hub"] == 1 and out["jobs"][0]["id"]
    if out["jobs"][0]["status"] == "scored":
        assert seen["job_ids"] == [out["jobs"][0]["id"]] and out["auto_apply"]["run_id"] == "sel"
    else:
        assert "no job passed" in out["auto_apply"]["note"]


# ---------------------------------------------------------------- bulk post scan
def test_post_scan_defaults_are_bulk():
    assert post_scanner.DEFAULTS["scrolls"] >= 20 and post_scanner.DEFAULTS["max_posts_per_query"] >= 50


def test_post_auto_apply_saves_links_and_emails(prof, monkeypatch):
    calls = {}
    monkeypatch.setattr(server, "apply_to_jobs", lambda a: calls.setdefault("apply", a) and {"run_id": "sel"})
    monkeypatch.setattr(server, "send_hr_emails", lambda a: calls.setdefault("mail", a) and {"dry_run": True, "results": []})
    leads = [{"role": "DevOps Engineer", "company": "Acme", "location": "Pune", "links": ["https://acme.example/careers/1"],
              "emails": ["hr@acme.example"], "excerpt": "We are hiring", "found_at": core.now_iso()},
             {"role": "SRE", "links": [], "emails": []}]
    out = post_scanner.auto_apply(prof, leads, True, log=lambda m: None)
    assert calls["apply"]["dry_run"] is True and len(calls["apply"]["job_ids"]) == 1
    assert calls["mail"]["dry_run"] is True and out["jobs"]["run_id"] == "sel"
    with core.db() as c:
        row = c.execute("SELECT status, score FROM jobs WHERE url='https://acme.example/careers/1'").fetchone()
    assert row["status"] == "scored" and row["score"] >= 70


def test_scan_tool_passes_bulk_options_and_blocks_live_without_switch(prof, monkeypatch):
    got = {}
    monkeypatch.setattr(core, "spawn", lambda kind, argv, cwd, env, dry, args: got.update(env=env, dry=dry) or {"run_id": "p"})
    server.scan_hiring_posts({"max_posts": 120, "auto_apply": True, "dry_run": True})
    opts = json.loads(got["env"]["JOBHUB_POST_OPTS"])
    assert opts["max_posts"] == 120 and opts["auto_apply"] is True and opts["dry_run"] is True
    assert "blocked" in server.scan_hiring_posts({"auto_apply": True, "dry_run": False})["error"]


# ---------------------------------------------------------------- one-tap remote browser
@pytest.fixture
def client(monkeypatch):
    import api
    from fastapi.testclient import TestClient
    monkeypatch.setattr(api, "lockout", api._Lockout(max_fail=3, window=60, ban=60))
    monkeypatch.setenv("JOBHUB_VNC_PASSWORD", "vnc-pass-123")
    return TestClient(api.create_app(), base_url="https://hub.example")


def test_remote_browser_link_flow(client, monkeypatch):
    monkeypatch.setitem(server.HANDLERS, "login_platform", lambda a: {"run_id": "login-naukri-1"})
    assert client.post("/api/v1/browser/session", json={"platform": "naukri"}).status_code == 401           # needs the app token
    r = client.post("/api/v1/browser/session", json={"platform": "naukri"}, headers=H)
    assert r.status_code == 200 and r.json()["url"].startswith("https://hub.example/api/v1/browser/enter?t=")
    url = r.json()["url"].replace("https://hub.example", "")
    assert client.get("/internal/browser-auth").status_code == 401                                          # no cookie yet
    client.cookies.clear()
    e = client.get(url, follow_redirects=False)
    assert e.status_code == 303
    loc = e.headers["location"]
    assert loc.startswith("/browser/vnc.html") and "#password=vnc-pass-123" in loc and "password" not in loc.split("#")[0]
    assert "ajs_browser=" in e.headers["set-cookie"] and "Secure" in e.headers["set-cookie"] and "HttpOnly" in e.headers["set-cookie"]
    # Caddy's forward_auth passes the browser's Cookie header on (the browser sends it because Path=/browser)
    ck = e.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    assert "Path=/browser" in e.headers["set-cookie"]
    assert client.get("/internal/browser-auth", headers={"Cookie": f"ajs_browser={ck}"}).status_code == 204  # cookie accepted
    assert client.get(url, follow_redirects=False).status_code == 401                                        # link is single use


def test_remote_browser_rejects_bad_platform_forged_cookie_and_expired_ticket(client, monkeypatch):
    assert client.post("/api/v1/browser/session", json={"platform": "facebook"}, headers=H).status_code == 422
    assert client.get("/api/v1/browser/enter?t=guess", follow_redirects=False).status_code == 401
    assert client.get("/internal/browser-auth", headers={"Cookie": "ajs_browser=9999999999.deadbeef"}).status_code == 401
    t = browser_access.new_ticket("naukri")
    monkeypatch.setattr(browser_access.time, "time", lambda: 1e12)
    assert browser_access.use_ticket(t) is None


def test_queue_endpoint_and_runs_list_show_waiting_runs(client, monkeypatch):
    import sys as _s
    running = set()
    real = core.run_status

    def fake(rid, tail=1):
        r = real(rid, tail)
        if "error" not in r and not r.get("queued"):
            r["running"] = rid in running
        return r

    monkeypatch.setattr(core, "run_status", fake)
    for k in ("career", "hr-pune", "verify", "hr-chennai", "hr-kolkata", "hr-hyderabad"):
        running.add(core.spawn(k, [_s.executable, "-c", "pass"], core.ROOT, {}, True, {})["run_id"])
    waiting = core.spawn("hr-mumbai", [_s.executable, "-c", "pass"], core.ROOT, {}, True, {})
    q = client.get("/api/v1/queue", headers=H).json()
    assert q["max_parallel"] == 6 and len(q["running"]) == 6 and q["queued"][0]["run_id"] == waiting["run_id"]
    runs = client.get("/api/v1/runs", headers=H).json()["runs"]
    assert runs[0]["run_id"] == waiting["run_id"] and runs[0]["state"] == "queued"


def test_tracker_report_and_hub_status_have_the_new_sections(prof):
    t = server.tracker_report({})
    assert {"totals", "last_7_days", "jobs_by_status", "queue", "top_waiting_jobs"} <= set(t) and len(t["last_7_days"]) == 7
    assert "queued" in server.hub_status({})["runs"]


def test_signed_in_session_needs_the_sign_in_cookie(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(core, "ROOT", tmp_path)
    d = tmp_path / ".profiles" / "linkedin" / "Default"
    d.mkdir(parents=True)
    con = sqlite3.connect(d / "Cookies")
    con.execute("CREATE TABLE cookies(name TEXT, value TEXT, expires_utc INTEGER, creation_utc INTEGER, last_update_utc INTEGER)")
    con.execute("INSERT INTO cookies VALUES('JSESSIONID','x',0,1,1)")
    con.commit()
    assert server._has_session("linkedin") is False          # Chrome was opened but nobody signed in
    con.execute("INSERT INTO cookies VALUES('li_at','x',0,1,1)")
    con.commit()
    con.close()
    assert server._has_session("linkedin") is True
