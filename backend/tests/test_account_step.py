"""Account step on career sites: sign up (or sign in with the saved account), verify by mail or in-app OTP, CAPTCHA to the user.
Real Chromium against local pages, like test_career_apply.py."""
import functools
import http.server
import json
import threading

import pytest

import account_step
import career_apply as ca
import core
import site_accounts
from test_career_apply import browser  # noqa: F401 - shared real-Chromium fixture

F = {"email": "me@example.com", "first": "Asha", "last": "Rao", "name": "Asha Rao", "phone": "9876543210"}

SIGNUP = """<!doctype html><title>{title}</title><h1>{title}</h1><p>Create an account to apply</p>
<form onsubmit="event.preventDefault(); {check}
 sessionStorage.setItem('signup', JSON.stringify({{email: em.value, pw: pw.value, pw2: pw2.value, first: fn.value,
   terms: terms.checked, mkt: mkt.checked}})); location.href='{next}';">
 <label for=em>Email</label><input id=em type=email name=email>
 <label for=fn>First name</label><input id=fn name=firstName>
 <label for=ln>Last name</label><input id=ln name=lastName>
 <label for=pw>Password</label><input id=pw type=password name=password>
 <label for=pw2>Confirm password</label><input id=pw2 type=password name=confirmPassword>
 <input type=checkbox id=terms><label for=terms>I agree to the Terms of Use and Privacy Policy</label>
 <input type=checkbox id=mkt><label for=mkt>Send me job alerts and newsletters</label>
 {extra}<p id=err></p><button type=submit>Create Account</button></form>"""
STRICT = ("if (/[^A-Za-z0-9]/.test(pw.value)) { err.textContent = 'Password must not contain special characters'; return; }")
VERIFY = """<!doctype html><title>Verify</title><h1>Verify your email</h1><p>Enter the verification code we sent you.</p>
<label for=code>Verification code</label><input id=code name=verificationCode>
<button onclick="sessionStorage.setItem('code', code.value); location.href='/done.html'">Verify</button>"""
DONE = "<!doctype html><title>Ready</title><h1>Your account is ready</h1><a href='/apply.html'>Apply now</a>"
APPLY = """<!doctype html><title>Apply - DevOps Engineer</title><h1>DevOps Engineer</h1>
<form onsubmit="event.preventDefault(); document.body.innerHTML='<h1>Thank you for applying</h1>';">
 <label for=a1>First name *</label><input id=a1 name=first_name required>
 <label for=a2>Last name *</label><input id=a2 name=last_name required>
 <label for=a3>Email *</label><input id=a3 type=email name=email required>
 <label for=a4>Phone *</label><input id=a4 type=tel name=phone required>
 <button type=submit>Submit application</button></form>"""
LOGIN = """<!doctype html><title>Sign in</title><h1>Sign in</h1>
<label for=em>Email</label><input id=em type=email><label for=pw>Password</label><input id=pw type=password>
<button onclick="sessionStorage.setItem('login', JSON.stringify({email: em.value, pw: pw.value})); location.href='/done.html'">Sign In</button>
<a href='/signup.html'>Create an account</a>"""
CAPTCHA_EXTRA = '<div class="g-recaptcha" style="width:300px;height:80px;border:1px solid #ccc"></div>'


def _pages():
    return {"/signup.html": SIGNUP.format(title="Create your account", check="", next="/verify.html", extra=""),
            "/jobsignup.html": SIGNUP.format(title="DevOps Engineer", check="", next="/verify.html", extra=""),
            "/strict.html": SIGNUP.format(title="Create your account", check=STRICT, next="/done.html", extra=""),
            "/captcha.html": SIGNUP.format(title="Create your account", check="", next="/done.html", extra=CAPTCHA_EXTRA),
            "/verify.html": VERIFY, "/done.html": DONE, "/login.html": LOGIN, "/apply.html": APPLY}


@pytest.fixture
def site(tmp_path):
    served = tmp_path / "acct"
    served.mkdir()
    for path, html in _pages().items():
        (served / path.lstrip("/")).write_text(html)
    posts = []

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            posts.append(self.path)
            self.send_response(405)
            self.end_headers()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(served)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", posts
    srv.shutdown()


@pytest.fixture(autouse=True)
def secrets_dir(tmp_path, monkeypatch):
    d = tmp_path / "secrets"
    d.mkdir()
    monkeypatch.setattr(core, "SECRETS", d)


@pytest.fixture
def page(browser):  # noqa: F811
    ctx = browser.new_context()
    ctx.set_default_timeout(8000)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _go(page, url):
    page.goto(url)
    page.wait_for_load_state("domcontentloaded")


def _stored(page, key):
    return json.loads(page.evaluate(f"sessionStorage.getItem('{key}')") or "null")


class Asked:
    def __init__(self, value=None):
        self.value, self.calls = value, []

    def __call__(self, kind, site, url, reason, **k):
        self.calls.append((kind, site, reason))
        return self.value


def test_kind_tells_sign_up_from_sign_in(page, site):
    base, _ = site
    _go(page, base + "/signup.html")
    assert account_step.kind(page) == "signup"
    _go(page, base + "/login.html")
    assert account_step.kind(page) == "login"
    _go(page, base + "/done.html")
    assert account_step.kind(page) is None


def test_signs_up_ticks_terms_and_verifies_with_the_mailed_code(page, site):
    base, _ = site
    _go(page, base + "/signup.html")
    ask = Asked()
    st = account_step.run(page, F, "127.0.0.1", False, ask_value=ask, wait_mail=lambda *a, **k: {"code": "123456", "link": None})
    assert st == "ok"
    s = _stored(page, "signup")
    acct = site_accounts.get("127.0.0.1")
    assert s["email"] == "me@example.com" and s["first"] == "Asha"
    assert s["pw"] == s["pw2"] == acct["password"] and acct["email"] == "me@example.com"
    assert s["terms"] is True and s["mkt"] is False
    assert str(_stored(page, "code")) == "123456" and ask.calls == []


def test_asks_for_the_code_in_the_app_when_no_mail_arrives(page, site):
    base, _ = site
    _go(page, base + "/signup.html")
    ask = Asked("654321")
    st = account_step.run(page, F, "127.0.0.1", False, ask_value=ask, wait_mail=lambda *a, **k: {})
    assert st == "ok" and str(_stored(page, "code")) == "654321"
    assert [c[0] for c in ask.calls] == ["otp"]


def test_no_code_from_mail_or_user_fails(page, site):
    base, _ = site
    _go(page, base + "/signup.html")
    st = account_step.run(page, F, "127.0.0.1", False, ask_value=Asked(None), wait_mail=lambda *a, **k: {})
    assert st == "failed"


def test_signs_in_with_the_saved_account(page, site, monkeypatch):
    base, _ = site
    pw = site_accounts.create("127.0.0.1", "me@example.com")
    monkeypatch.setattr(site_accounts, "create", lambda *a, **k: pytest.fail("must not create a second account"))
    _go(page, base + "/login.html")
    st = account_step.run(page, F, "127.0.0.1", False, ask_value=Asked(), wait_mail=lambda *a, **k: {})
    assert st == "ok" and _stored(page, "login") == {"email": "me@example.com", "pw": pw}


def test_sign_in_page_without_account_goes_to_sign_up(page, site):
    base, _ = site
    _go(page, base + "/login.html")
    st = account_step.run(page, F, "127.0.0.1", False, ask_value=Asked(), wait_mail=lambda *a, **k: {"code": "1111", "link": None})
    assert st == "ok" and _stored(page, "signup")["pw"] == site_accounts.get("127.0.0.1")["password"]


def test_retries_with_a_simple_password_when_symbols_are_rejected(page, site):
    base, _ = site
    _go(page, base + "/strict.html")
    st = account_step.run(page, F, "127.0.0.1", False, ask_value=Asked(), wait_mail=lambda *a, **k: {})
    pw = site_accounts.get("127.0.0.1")["password"]
    assert st == "ok" and pw.isalnum() and _stored(page, "signup")["pw"] == pw


def test_captcha_goes_to_the_user_and_nothing_is_submitted(page, site):
    base, _ = site
    _go(page, base + "/captcha.html")
    st = account_step.run(page, F, "127.0.0.1", False, ask_value=Asked(), wait_mail=lambda *a, **k: {})
    assert st == "captcha" and _stored(page, "signup") is None and site_accounts.get("127.0.0.1") is None


def test_dry_run_and_job_boards_are_skipped(page, site):
    base, posts = site
    _go(page, base + "/signup.html")
    assert account_step.run(page, F, "127.0.0.1", True, ask_value=Asked(), wait_mail=lambda *a, **k: {}) == "skipped"
    assert account_step.run(page, F, "www.naukri.com", False, ask_value=Asked(), wait_mail=lambda *a, **k: {}) == "skipped"
    assert _stored(page, "signup") is None and posts == [] and site_accounts.hosts() == []


def test_apply_one_creates_the_account_then_applies(browser, site, tmp_path):  # noqa: F811
    base, _ = site
    calls = []

    def account(pg):
        calls.append(pg.url)
        return account_step.run(pg, F, "127.0.0.1", False, ask_value=Asked(), wait_mail=lambda *a, **k: {"code": "9", "link": None})
    human = []
    ctx = browser.new_context()
    ctx.set_default_timeout(8000)
    try:
        r = ca.apply_one(ctx, {"id": 3, "url": base + "/jobsignup.html", "title": "DevOps Engineer"}, {**F, "qa": {}}, False, None,
                         shot_dir=tmp_path / "s", step_pause_ms=50, human=lambda *a: human.append(a) or False, account=account)
    finally:
        ctx.close()
    assert calls and human == [] and site_accounts.get("127.0.0.1")
    assert r["status"] == "applied", r


def test_make_account_wires_the_app_and_gmail(monkeypatch):
    import attention
    import verify_mail
    seen = {}

    def fake_run(page, F_, host, dry, ask_value=None, wait_mail=None, log=print):
        seen.update(host=host, dry=dry, email=F_["email"])
        ask_value("otp", host, page.url, "code?")
        wait_mail(host, 1.0)
        return "ok"
    monkeypatch.setattr(account_step, "run", fake_run)
    monkeypatch.setattr(attention, "ask_value", lambda kind, site, url, reason, job="", profile=None, **k:
                        seen.update(ask=(kind, site, job)) or "1")
    monkeypatch.setattr(verify_mail, "wait_for", lambda profile, host, since, **k: seen.update(mail=(host, since)) or {})

    class Pg:
        url = "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/1"
    acc = ca.make_account({"x": 1}, F, {"title": "SRE", "company": "Acme"})
    assert acc(Pg()) == "ok"
    assert seen["host"] == "acme.wd5.myworkdayjobs.com" and seen["dry"] is False and seen["email"] == "me@example.com"
    assert seen["ask"] == ("otp", "acme.wd5.myworkdayjobs.com", "SRE @ Acme") and seen["mail"] == ("acme.wd5.myworkdayjobs.com", 1.0)
