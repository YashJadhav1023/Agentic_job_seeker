import time

import pytest

import attention
import notify


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("JOBHUB_API_TOKEN", "t" * 40)
    monkeypatch.setenv("JOBHUB_REMOTE_BROWSER_URL", "https://jobs.example.com/browser/vnc.html")
    monkeypatch.setenv("DISPLAY", ":99")


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "push", lambda p, title, text, url="": calls.append((title, text, url)) or True)
    return calls


P = {"human_help": {"enabled": True, "wait_minutes": 1}, "notifications": {}}


def test_request_notifies_with_signed_link(sent):
    rid = attention.request("login", "Workday", "https://x.wd5.myworkdayjobs.com/a", "needs an account", "SRE @ X", P)
    assert [r["id"] for r in attention.pending()] == [rid]
    title, text, url = sent[0]
    assert "Workday" in title and "SRE @ X" in text
    assert url.startswith("https://jobs.example.com/api/v1/attention/open?id=")
    k = url.split("k=")[1]
    assert attention.link_ok(rid, k)
    assert not attention.link_ok(rid, "0" * 64)


def test_link_dies_when_resolved_or_expired(sent):
    rid = attention.request("human_check", "Naukri", "https://naukri.com/j", "check box", profile=P)
    k = sent[0][2].split("k=")[1]
    assert attention.resolve(rid, "done")
    assert not attention.link_ok(rid, k)
    rid2 = attention.request("login", "Deloitte", "https://d", "login", profile={"human_help": {"wait_minutes": 0}})
    time.sleep(0.01)
    assert attention.pending() == []
    assert attention.get(rid2)["status"] == "expired"


def test_wait_returns_true_when_cleared(sent):
    rid = attention.request("login", "Workday", "u", "r", profile=P)
    seen = iter([False, False, True])
    assert attention.wait(rid, lambda: next(seen), sleep=lambda s: None)
    assert attention.get(rid)["status"] == "done"


def test_wait_gives_up_on_timeout_or_skip(sent):
    rid = attention.request("login", "Workday", "u", "r", profile=P)
    clock = iter([time.time(), time.time() + 3600])
    assert not attention.wait(rid, lambda: False, sleep=lambda s: None, now=lambda: next(clock))
    assert attention.get(rid)["status"] == "expired"
    rid2 = attention.request("login", "Workday", "u", "r", profile=P)
    attention.resolve(rid2, "skipped")
    assert not attention.wait(rid2, lambda: True, sleep=lambda s: None)


def test_not_available_without_display_or_when_off(monkeypatch, sent):
    monkeypatch.delenv("DISPLAY")
    assert not attention.ask_and_wait("login", "W", "u", "r", lambda: True, profile=P, log=lambda m: None)
    monkeypatch.setenv("DISPLAY", ":99")
    assert not attention.available({"human_help": {"enabled": False}})
    assert sent == []


def test_push_sends_ntfy_with_click_link(monkeypatch):
    reqs = []

    class R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout=0: reqs.append(req) or R())
    p = {"notifications": {"ntfy_topic": "ajs-abc123def456ghi789"}}
    assert notify.push(p, "Job bot needs you", "Sign in to Workday", "https://x/open?id=1&k=z")
    req = reqs[0]
    assert req.full_url == "https://ntfy.sh/ajs-abc123def456ghi789"
    hdrs = {k.lower(): v for k, v in req.header_items()}
    assert hdrs["click"] == "https://x/open?id=1&k=z" and hdrs["priority"] == "high"
    assert b"Sign in to Workday" in req.data


def test_push_is_noop_without_channels():
    assert notify.push({"notifications": {}}, "t", "x") is False


def test_api_open_link_sets_cookie_and_redirects(monkeypatch, sent):
    from fastapi.testclient import TestClient
    import api
    rid = attention.request("login", "Workday", "u", "r", profile=P)
    k = sent[0][2].split("k=")[1]
    c = TestClient(api.create_app())
    r = c.get(f"/api/v1/attention/open?id={rid}&k={k}", follow_redirects=False)
    assert r.status_code == 303 and "/browser/vnc.html" in r.headers["location"]
    assert "ajs_browser=" in r.headers.get("set-cookie", "")
    assert c.get(f"/api/v1/attention/open?id={rid}&k=bad", follow_redirects=False).status_code == 403


def test_server_tools_list_and_resolve(sent):
    import server
    rid = attention.request("login", "Workday", "u", "r", "SRE @ X", P)
    out = server.attention_list({})
    assert out["requests"][0]["id"] == rid and out["requests"][0]["open_url"].startswith("https://jobs.example.com/")
    assert server.attention_resolve({"id": rid, "status": "skipped"}) == {"closed": True}
    assert server.attention_list({})["requests"] == []


def test_request_without_token_is_notified_by_the_server_sweep(monkeypatch, sent):
    monkeypatch.delenv("JOBHUB_API_TOKEN")
    rid = attention.request("human_check", "Naukri", "u", "check box", profile=P)   # a vendored bot: no token
    assert sent == []
    monkeypatch.setenv("JOBHUB_API_TOKEN", "t" * 40)
    monkeypatch.setattr(attention.core, "load_profile", lambda: P)
    assert attention.notify_pending() == 1 and "Naukri" in sent[0][0]
    assert attention.notify_pending() == 0          # sent once only
    assert attention.get(rid)["notified"] == 1
