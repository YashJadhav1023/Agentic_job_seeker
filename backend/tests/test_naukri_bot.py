"""Naukri bot helpers (vendored job-automation + our hubguard patch)."""
import asyncio
import importlib.util
from pathlib import Path


GUARD = Path(__file__).resolve().parent.parent / "vendor-patches" / "job-automation-hubguard.py"


def _guard(monkeypatch, headless="0"):
    monkeypatch.setenv("JOBHUB_HEADLESS", headless)
    monkeypatch.setenv("JOBHUB_HUB_DIR", str(Path(__file__).resolve().parent.parent / "hub"))
    spec = importlib.util.spec_from_file_location("hubguard_t", GUARD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_human_check_text(monkeypatch):
    g = _guard(monkeypatch)
    assert g.human_check("To continue your request please check the box to let us know you’re human")
    assert g.human_check("Access Denied\nYou don't have permission")
    assert not g.human_check("DevOps Engineer\nApply\n3-5 years\nThe applicant has access denied lists")


def test_unique_cards_keeps_order_drops_dupes_and_empty(monkeypatch):
    g = _guard(monkeypatch)
    rows = [{"title": "DevOps Engineer", "url": "https://www.naukri.com/job-listings-a-1?src=x", "company": "A "},
            {"title": "DevOps Engineer", "url": "https://www.naukri.com/job-listings-a-1?src=y"},
            {"title": "", "url": "https://www.naukri.com/job-listings-b-2"},
            {"title": "SRE", "url": ""},
            {"title": "Cloud Engineer", "url": "https://www.naukri.com/job-listings-c-3", "location": "Pune"}]
    out = g.unique_cards(rows)
    assert [c["title"] for c in out] == ["DevOps Engineer", "Cloud Engineer"]
    assert out[0]["company"] == "A" and out[1]["location"] == "Pune"


class _Page:
    url = "https://www.naukri.com/job-listings-x"


def test_wait_for_human_asks_and_continues(monkeypatch):
    import attention
    g = _guard(monkeypatch)
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(attention, "available", lambda profile=None: True)
    asked = []
    monkeypatch.setattr(attention, "request", lambda *a, **k: asked.append(a) or 7)
    states = iter([{"status": "open", "expires": 9e12}] * 3)
    monkeypatch.setattr(attention, "get", lambda rid: next(states))
    done = []
    monkeypatch.setattr(attention, "resolve", lambda rid, st="done": done.append((rid, st)) or True)
    seen = iter([False, True])

    async def clear():
        return next(seen)
    ok = asyncio.run(g.wait_for_human(_Page(), "Naukri", "human_check", "check box", clear, poll=0))
    assert ok and asked[0][0] == "human_check" and done == [(7, "done")]


def test_wait_for_human_off_when_headless(monkeypatch):
    g = _guard(monkeypatch, headless="1")

    async def clear():
        return True
    assert asyncio.run(g.wait_for_human(_Page(), "Naukri", "login", "x", clear, poll=0)) is False


def test_wait_for_human_gives_up_when_skipped(monkeypatch):
    import attention
    g = _guard(monkeypatch)
    monkeypatch.setattr(attention, "available", lambda profile=None: True)
    monkeypatch.setattr(attention, "request", lambda *a, **k: 3)
    monkeypatch.setattr(attention, "get", lambda rid: {"status": "skipped", "expires": 9e12})

    async def clear():
        return False
    assert asyncio.run(g.wait_for_human(_Page(), "Naukri", "login", "x", clear, poll=0)) is False


def test_apply_jobs_naukri_runs_headed_and_points_at_hub(monkeypatch, tmp_path):
    import core
    import server
    p = core.load_profile()
    p["safety"] = {"dry_run_default": True, "confirmed_by_user": False}
    p["browser"] = {"headless": True}
    monkeypatch.setattr(core, "load_profile", lambda: p)
    monkeypatch.setattr(server, "_has_session", lambda pl: True)
    monkeypatch.setattr(server, "_write_job_automation_config", lambda p, limit: None)
    monkeypatch.setenv("DISPLAY", ":99")
    got = {}
    monkeypatch.setattr(core, "spawn", lambda kind, argv, cwd, env, dry, args: got.update(env=env, kind=kind) or {"run_id": "r"})
    r = server.apply_jobs({"platform": "naukri", "dry_run": True})
    assert got["env"]["JOBHUB_HEADLESS"] == "0" and r["headless"] is False
    assert got["env"]["JOBHUB_HUB_DIR"].endswith("hub")


def test_wait_for_login_asks_user_when_session_expired(monkeypatch):
    g = _guard(monkeypatch)
    calls = []

    async def fake_wait(page, site, kind, reason, is_clear, job="", poll=5.0):
        calls.append((site, kind))
        page.url = "https://www.naukri.com/mnjuser/homepage"
        return await is_clear()
    monkeypatch.setattr(g, "wait_for_human", fake_wait)

    class P:
        url = "https://www.naukri.com/nlogin/login"

        async def goto(self, u, wait_until=None):
            pass

        async def wait_for_timeout(self, ms):
            pass
    asyncio.run(g.wait_for_login(P(), "https://www.naukri.com/mnjuser/homepage", lambda u: "mnjuser" in u))
    assert calls == [("Naukri", "login")]
