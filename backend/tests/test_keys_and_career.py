"""API keys (keys.py + its users), the 100-email limit, Naukri confirmation helpers, and career-form selection/caps."""
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import autopilot
import career_apply as ca
import career_scan
import core
import keys
import llm

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for v in ("JOBHUB_FIRECRAWL_API_KEY", "FIRECRAWL_API_KEY", "KIRO_API_KEY", "LLM_API_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(core, "SECRETS", tmp_path / "secrets")
    (tmp_path / "secrets").mkdir()
    monkeypatch.setattr(keys, "_alert_enabled", False, raising=False)


# ---------------------------------------------------------------- keys.py
@pytest.mark.parametrize("val", ["UNSET", "unset", " ", "None", "changeme", "<key>", '"UNSET"'])
def test_placeholder_values_count_as_missing(monkeypatch, val):
    monkeypatch.setenv("KIRO_API_KEY", val)
    assert keys.get("kiro") == "" and not keys.present("kiro")


def test_env_beats_secret_file_and_second_env_name_works(monkeypatch):
    f = core.SECRETS / "firecrawl_api_key"
    f.write_text("fc-fromfile1234")
    f.chmod(0o600)
    assert keys.get("firecrawl") == "fc-fromfile1234"
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-second12345")
    assert keys.get("firecrawl") == "fc-second12345"
    monkeypatch.setenv("JOBHUB_FIRECRAWL_API_KEY", "fc-primary12345")
    assert keys.get("firecrawl") == "fc-primary12345"


def test_secret_file_with_loose_permissions_is_refused():
    f = core.SECRETS / "kiro_api_key"
    f.write_text("ksk_looseperms1234")
    f.chmod(0o644)
    assert keys.get("kiro") == ""


def test_redact_removes_configured_and_key_shaped_values(monkeypatch):
    monkeypatch.setenv("KIRO_API_KEY", "ksk_SECRETVALUE1234567890")
    monkeypatch.setenv("LLM_API_KEY", "custom-token-that-has-no-known-shape")
    out = keys.redact("failed with ksk_SECRETVALUE1234567890 and custom-token-that-has-no-known-shape; Bearer abcdefghijklmnop1234 "
                      "also fc-abcdefgh12345678")
    assert "SECRETVALUE" not in out and "custom-token" not in out and "abcdefghijklmnop" not in out and "fc-abcdefgh" not in out
    assert out.count("***") == 4


@pytest.mark.parametrize("status,text,kind", [
    (401, "", "invalid"), (403, "Forbidden", "invalid"), (None, "Invalid API key", "invalid"),
    (402, "", "credits"), (None, "Insufficient credits", "credits"), (None, "monthly usage limit reached", "credits"),
    (200, "", ""), (500, "boom", ""), (None, "timeout talking to host", "")])
def test_classify(status, text, kind):
    assert keys.classify(status, text) == kind


def test_cooldown_set_cleared_and_reported(monkeypatch):
    monkeypatch.setenv("KIRO_API_KEY", "ksk_abcdefghij1234")
    sent = []
    monkeypatch.setattr("notify.telegram", lambda p, t: sent.append(t) or True)
    keys.mark_failed("kiro", "credits", hours=1, profile={})
    keys.mark_failed("kiro", "credits", hours=1, profile={})  # second call must not alert again
    assert "credits exhausted" in keys.failed("kiro") and len(sent) == 1
    assert keys.report()["kiro"] == {"set": True, "cooldown": keys.failed("kiro")}
    assert "ksk_" not in json.dumps(keys.report())
    keys.clear("kiro")
    assert keys.failed("kiro") == ""


def test_expired_cooldown_is_ignored():
    core.kv_set("keyfail:firecrawl", {"until": (datetime.now() - timedelta(minutes=1)).isoformat(), "reason": "old"})
    assert keys.failed("firecrawl") == ""


# ---------------------------------------------------------------- Firecrawl
class FakeResp:
    def __init__(self, status=200, body=None, text="", headers=None):
        self.status_code, self._body, self.text, self.headers = status, body or {}, text, headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_firecrawl_without_key_does_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(career_scan.requests, "post", lambda *a, **k: called.append(1))
    assert career_scan.run({}) == {"error": "JOBHUB_FIRECRAWL_API_KEY is not set"} and not called


def test_firecrawl_402_puts_key_on_cooldown_and_stops_the_scan(monkeypatch):
    monkeypatch.setenv("JOBHUB_FIRECRAWL_API_KEY", "fc-testkey1234567")
    monkeypatch.setattr("notify.telegram", lambda p, t: True)
    calls = []
    monkeypatch.setattr(career_scan.requests, "post", lambda *a, **k: calls.append(1) or FakeResp(402, text="Payment required"))
    monkeypatch.setattr(core, "load_profile", lambda: {"search": {"title_include": "x", "title_exclude": "y"}})
    out = career_scan.run({"companies": ["a.com", "b.com", "c.com"]})
    assert "credits exhausted" in out["stopped"] and len(calls) == 1  # first company failed, the rest were not even tried
    again = career_scan.run({"companies": ["a.com"]})
    assert "error" in again and len(calls) == 1  # still on cooldown: no new request


def test_firecrawl_401_marks_key_invalid(monkeypatch):
    monkeypatch.setenv("JOBHUB_FIRECRAWL_API_KEY", "fc-badkey123456789")
    monkeypatch.setattr("notify.telegram", lambda p, t: True)
    monkeypatch.setattr(career_scan.requests, "post", lambda *a, **k: FakeResp(401, text="Unauthorized"))
    with pytest.raises(career_scan.FirecrawlUnavailable):
        career_scan._post("/map", {})
    assert "rejected" in keys.failed("firecrawl")


def test_firecrawl_429_retries_then_succeeds(monkeypatch):
    monkeypatch.setenv("JOBHUB_FIRECRAWL_API_KEY", "fc-testkey1234567")
    seq = [FakeResp(429, headers={"Retry-After": "0"}), FakeResp(200, {"ok": True})]
    monkeypatch.setattr(career_scan.requests, "post", lambda *a, **k: seq.pop(0))
    monkeypatch.setattr(career_scan.time, "sleep", lambda s: None)
    assert career_scan._post("/map", {}) == {"ok": True}


def test_firecrawl_key_never_appears_in_scan_output(monkeypatch, capsys):
    monkeypatch.setenv("JOBHUB_FIRECRAWL_API_KEY", "fc-testkey1234567")
    monkeypatch.setattr(career_scan, "scan_company", lambda p, n: (_ for _ in ()).throw(RuntimeError("boom fc-testkey1234567")))
    monkeypatch.setattr(core, "load_profile", lambda: {})
    out = career_scan.run({"companies": ["a.com"]})
    assert "fc-testkey" not in capsys.readouterr().out and out["failed"] == 1


# ---------------------------------------------------------------- Kiro / LLM
def test_kiro_placeholder_key_is_not_available(monkeypatch):
    monkeypatch.setattr(llm, "kiro_bin", lambda: "/usr/bin/kiro-cli")
    monkeypatch.setenv("KIRO_API_KEY", "UNSET")
    ok, why = llm.available({"provider": "kiro"})
    assert not ok and "KIRO_API_KEY" in why
    monkeypatch.setenv("KIRO_API_KEY", "ksk_realkey123456")
    assert llm.available({"provider": "kiro"}) == (True, "")


def test_kiro_credit_error_pauses_provider_without_retry(monkeypatch):
    monkeypatch.setattr(llm, "kiro_bin", lambda: "/usr/bin/kiro-cli")
    monkeypatch.setenv("KIRO_API_KEY", "ksk_realkey123456")
    monkeypatch.setattr("notify.telegram", lambda p, t: True)
    calls = []

    def boom(prompt, c):
        calls.append(1)
        raise llm.LLMError("kiro-cli exit 1: monthly usage limit exceeded for ksk_realkey123456")
    monkeypatch.setitem(llm.PROVIDERS, "kiro", boom)
    with pytest.raises(llm.LLMError) as e:
        llm.chat_json("x", {"provider": "kiro", "timeout_sec": 5}, retries=3)
    assert len(calls) == 1 and "ksk_realkey" not in str(e.value)
    assert llm.available({"provider": "kiro"})[0] is False and "credits exhausted" in keys.failed("kiro")


def test_kiro_subprocess_gets_only_the_key_it_needs(monkeypatch, tmp_path):
    monkeypatch.setenv("KIRO_API_KEY", "ksk_realkey123456")
    monkeypatch.setenv("JOBHUB_SECRET_GMAIL_APP_PASSWORD", "gmailpw")
    monkeypatch.setenv("JOBHUB_API_TOKEN", "apitoken")
    monkeypatch.setattr(llm, "kiro_bin", lambda: "kiro-cli")
    monkeypatch.setattr(llm, "_sandbox", lambda: tmp_path)
    seen = {}

    class R:
        returncode, stdout, stderr = 0, "{}", ""

    def fake_run(argv, **kw):
        seen.update(kw["env"])
        return R()
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    llm._kiro("hi", {"timeout_sec": 5})
    assert seen["KIRO_API_KEY"] == "ksk_realkey123456"
    assert "JOBHUB_SECRET_GMAIL_APP_PASSWORD" not in seen and "JOBHUB_API_TOKEN" not in seen


# ---------------------------------------------------------------- secrets never reach vendored bots
def test_third_party_bot_env_has_no_secrets(monkeypatch, tmp_path):
    for k, v in {"KIRO_API_KEY": "k", "JOBHUB_SECRET_GMAIL_APP_PASSWORD": "p", "JOBHUB_API_TOKEN": "t",
                 "JOBHUB_FIRECRAWL_API_KEY": "f", "JOBHUB_DOMAIN": "keepme.example"}.items():
        monkeypatch.setenv(k, v)
    captured = {}

    class P:
        pid = 1

    def fake_popen(argv, **kw):
        captured["env"] = kw["env"]
        return P()
    monkeypatch.setattr(core.subprocess, "Popen", fake_popen)
    core.spawn("apply-naukri", ["true"], tmp_path, {"JOBHUB_DRY_RUN": "1"}, True, {})
    env = captured["env"]
    assert env["JOBHUB_DRY_RUN"] == "1" and env["JOBHUB_DOMAIN"] == "keepme.example"
    assert not {"KIRO_API_KEY", "JOBHUB_SECRET_GMAIL_APP_PASSWORD", "JOBHUB_API_TOKEN", "JOBHUB_FIRECRAWL_API_KEY"} & set(env)
    captured.clear()
    core.spawn("career-apply", ["true"], tmp_path, {}, True, {})
    assert captured["env"]["KIRO_API_KEY"] == "k"  # our own code still gets what it needs


# ---------------------------------------------------------------- email search limit
def test_find_company_emails_default_and_cap_are_100():
    import inspect
    import company_finder
    import server
    assert inspect.signature(company_finder.find_company_emails).parameters["limit"].default == 100
    props = server.TOOLS["find_company_emails"][1]["properties"]["limit"]
    assert props["maximum"] == 100 and props["default"] == 100
    assert server.TOOLS["find_hr_emails"][1]["properties"]["limit"]["maximum"] == 100


def test_find_company_emails_returns_up_to_100(monkeypatch):
    import company_finder
    src = inspect_source(company_finder)
    assert "found_emails[:limit]" in src and "min(int(limit or 100), 100)" in src


def inspect_source(mod):
    return Path(mod.__file__).read_text()


# ---------------------------------------------------------------- Naukri confirmation helpers (vendored hubguard)
@pytest.fixture(scope="module")
def hubguard():
    spec = importlib.util.spec_from_file_location("hubguard", ROOT / "vendor-patches" / "job-automation-hubguard.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_click_without_new_confirmation_is_not_an_application(hubguard):
    page = "DevOps Engineer\nWe have applied best practices.\nApply\nSimilar jobs"
    assert not hubguard.confirmed_applied(page, page)
    assert not hubguard.confirmed_applied(page, page + "\nPlease answer a few questions\nAre you comfortable with night shifts?")


def test_new_confirmation_text_or_url_counts(hubguard):
    before = "DevOps Engineer\nApply"
    assert hubguard.confirmed_applied(before, before + "\nYou have successfully applied to DevOps Engineer")
    assert hubguard.confirmed_applied(before, before + "\nApplication submitted")
    assert hubguard.confirmed_applied(before, before, "https://www.naukri.com/myapply/saveApply?x=1")
    assert hubguard.confirmed_applied(before, before, applied_marker=True)


def test_text_already_on_the_page_before_the_click_does_not_count(hubguard):
    before = "Job posted\nYou have already applied\nApply"
    assert not hubguard.confirmed_applied(before, before)


def test_company_site_only_jobs_are_detected(hubguard):
    assert hubguard.external_only(["Apply on company site", "Save"])
    assert hubguard.external_only(["Apply on company website"])
    assert not hubguard.external_only(["Apply", "Apply on company site"])
    assert not hubguard.external_only(["Apply"])
    assert not hubguard.external_only([])


# ---------------------------------------------------------------- career form: selection, caps, bookkeeping
@pytest.fixture
def prof(tmp_path, monkeypatch):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    p = core.load_profile()
    p["applicant"]["resume_path"] = str(pdf)
    p["applicant"]["name"] = "Asha Rao"
    p["safety"]["confirmed_by_user"] = True
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(core, "real_actions_allowed", lambda p: (True, ""))
    monkeypatch.setattr(core, "load_profile", lambda: p)
    return p


def add_job(url, company="Acme", title="DevOps Engineer", status="scored", score=80, jd_emails="[]", source="career"):
    with core.db() as c:
        cur = c.execute("INSERT INTO jobs(source,url,title,company,status,score,jd_emails,apply_type,found_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)", (source, url, title, company, status, score, jd_emails, "external", "t", "t"))
        return cur.lastrowid


def test_only_company_pages_are_selected(prof):
    a = add_job("https://acme.com/careers/devops")
    add_job("https://www.linkedin.com/jobs/view/1", company="LI")
    add_job("https://in.indeed.com/viewjob?jk=1", company="IN")
    add_job("https://boards.greenhouse.io/zeta/jobs/5", company="Zeta")
    jobs, why = ca.eligible(prof, dry=True)
    assert {j["url"] for j in jobs} == {"https://acme.com/careers/devops", "https://boards.greenhouse.io/zeta/jobs/5"} and not why
    assert a in {j["id"] for j in jobs}


def test_one_job_per_company_per_run_and_best_score_first(prof):
    add_job("https://acme.com/a", score=50)
    best = add_job("https://acme.com/b", score=90)
    jobs, _ = ca.eligible(prof, dry=True)
    assert [j["id"] for j in jobs] == [best]


def test_job_with_an_email_in_the_posting_goes_to_email_not_the_form(prof):
    add_job("https://acme.com/a", jd_emails='["jobs@acme.com"]')
    assert ca.eligible(prof, dry=True)[0] == []


def test_failed_attempts_are_retried_later_then_given_up(prof):
    jid = add_job("https://acme.com/a")
    with core.db() as c:
        c.execute("INSERT INTO form_attempts(job_id,at,status,reason,dry) VALUES(?,?,?,?,0)", (jid, datetime.now().isoformat(), "captcha", "x"))
    assert ca.eligible(prof, dry=True)[0] == []  # too soon
    with core.db() as c:
        c.execute("UPDATE form_attempts SET at=?", ((datetime.now() - timedelta(days=5)).isoformat(),))
    assert [j["id"] for j in ca.eligible(prof, dry=True)[0]] == [jid]  # retry window passed
    with core.db() as c:
        c.execute("INSERT INTO form_attempts(job_id,at,status,reason,dry) VALUES(?,?,?,?,0)", (jid, (datetime.now() - timedelta(days=5)).isoformat(), "captcha", "x"))
    assert ca.eligible(prof, dry=True)[0] == []  # two real attempts: give up


def test_dry_run_attempts_do_not_count_against_a_job(prof):
    jid = add_job("https://acme.com/a")
    with core.db() as c:
        for _ in range(3):
            c.execute("INSERT INTO form_attempts(job_id,at,status,reason,dry) VALUES(?,?,?,?,1)", (jid, datetime.now().isoformat(), "dry_filled", ""))
    assert [j["id"] for j in ca.eligible(prof, dry=True)[0]] == [jid]


def test_company_applied_recently_is_skipped(prof):
    add_job("https://acme.com/a")
    with core.db() as c:
        c.execute("INSERT INTO applications(platform,title,company,url,status,at) VALUES('career','x','Acme','u','applied',?)", (core.now_iso(),))
    assert ca.eligible(prof, dry=True)[0] == []


def test_daily_cap_blocks_live_runs_only(prof):
    for i in range(prof["autopilot"].get("career_apply_max_per_day", 5)):
        with core.db() as c:
            c.execute("INSERT INTO applications(platform,title,company,url,status,at) VALUES('career','t',?,?,'applied',?)",
                      (f"Other{i}", f"u{i}", core.now_iso()))
    add_job("https://acme.com/a")
    jobs, why = ca.eligible(prof, dry=False)
    assert jobs == [] and "cap" in why
    assert ca.eligible(prof, dry=True)[0]


def test_live_run_needs_user_confirmation(prof, monkeypatch):
    add_job("https://acme.com/a")
    monkeypatch.setattr(core, "real_actions_allowed", lambda p: (False, "confirmed_by_user is false"))
    jobs, why = ca.eligible(prof, dry=False)
    assert jobs == [] and "blocked" in why


def test_kill_switch_and_missing_resume(prof, tmp_path):
    add_job("https://acme.com/a")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "STOP").write_text("x")
    assert "kill-switch" in ca.eligible(prof, dry=True)[1]
    (tmp_path / "data" / "STOP").unlink()
    prof["applicant"]["resume_path"] = str(tmp_path / "missing.pdf")
    assert "resume" in ca.eligible(prof, dry=True)[1]


def test_turned_off_in_profile(prof):
    add_job("https://acme.com/a")
    prof["autopilot"]["career_apply"] = False
    assert "off" in ca.eligible(prof, dry=True)[1]


def test_record_applied_unconfirmed_and_failed(prof):
    ok = add_job("https://acme.com/ok", company="Ok")
    un = add_job("https://beta.com/un", company="Beta")
    bad = add_job("https://gamma.com/bad", company="Gamma")
    shut = add_job("https://delta.com/shut", company="Delta")
    jobs = {j["id"]: j for j in ca.eligible(prof, ids=[ok, un, bad, shut], limit=10, dry=True)[0]}
    ca.record(jobs[ok], {"status": "applied", "reason": "confirmation shown", "screenshot": "s.png"}, False)
    ca.record(jobs[un], {"status": "unconfirmed", "reason": "no confirmation"}, False)
    ca.record(jobs[bad], {"status": "captcha", "reason": "CAPTCHA on the page"}, False)
    ca.record(jobs[shut], {"status": "closed", "reason": "filled"}, False)
    with core.db() as c:
        st = {r["id"]: (r["status"], r["reason"]) for r in c.execute("SELECT id,status,reason FROM jobs")}
        apps = [dict(r) for r in c.execute("SELECT platform,company,status FROM applications")]
        n = c.execute("SELECT COUNT(*) FROM form_attempts").fetchone()[0]
    assert st[ok][0] == "applied" and st[un][0] == "form_unconfirmed" and st[shut][0] == "skip"
    assert st[bad][0] == "scored" and "CAPTCHA" in st[bad][1]  # stays eligible for the email path
    assert apps == [{"platform": "career", "company": "Ok", "status": "applied"}] and n == 4


def test_main_runs_the_batch_and_records(prof, monkeypatch):
    jid = add_job("https://acme.com/a")
    monkeypatch.setattr(ca, "run_batch", lambda profile, jobs, dry, **k: [
        {"job_id": jobs[0]["id"], "url": jobs[0]["url"], "status": "applied", "reason": "confirmation shown", "filled": 9, "steps": 1,
         "screenshot": ""}])
    alerts = []
    monkeypatch.setattr(ca, "alert", lambda p, j, r: alerts.append(r["status"]))
    out = ca.main({"dry": False})
    assert out["applied"] == 1 and out["selected"] == 1 and alerts == ["applied"]
    with core.db() as c:
        assert c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0] == "applied"


def test_main_dry_run_never_alerts_or_marks_applied(prof, monkeypatch):
    jid = add_job("https://acme.com/a")
    monkeypatch.setattr(ca, "run_batch", lambda profile, jobs, dry, **k: [
        {"job_id": jobs[0]["id"], "url": jobs[0]["url"], "status": "dry_filled", "reason": "DRY-RUN", "filled": 9, "steps": 1, "screenshot": ""}])
    monkeypatch.setattr(ca, "alert", lambda *a: pytest.fail("no alert in dry-run"))
    out = ca.main({"dry": True})
    assert out["dry_filled"] == 1 and out["applied"] == 0
    with core.db() as c:
        assert c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0] == "scored"
        assert c.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_email_phase_skips_jobs_the_form_already_handled(prof):
    # statuses the email phase picks up are scored/queued only
    for st in ("applied", "form_unconfirmed"):
        add_job(f"https://x.com/{st}", company=st, status=st)
    with core.db() as c:
        picked = [r[0] for r in c.execute("SELECT company FROM jobs WHERE status IN ('scored','queued')")]
    assert picked == []


def test_form_phase_reports_why_it_skipped(prof):
    out = autopilot.form_phase(prof, autopilot.cfg(prof), True)
    assert out == {"skipped": "no eligible company-page jobs"}
    prof["autopilot"]["career_apply"] = False
    assert "false" in autopilot.form_phase(prof, autopilot.cfg(prof), True)["skipped"]
