"""Career-page form filling: decision rules (pure) and the browser flow against local fixture sites.

Browser tests run only when a Chromium is available (JOBHUB_CHROME, backend/.browsers, or Playwright's own install)."""
import functools
import http.server
import os
import threading
from pathlib import Path

import pytest

import career_apply as ca

ROOT = Path(__file__).resolve().parent.parent

PROFILE = {
    "applicant": {"name": "Asha Rao", "email": "asha@example.com", "phone": "+919876543210", "experience_years": "2",
                  "willing_to_relocate": "Yes", "headline": "DevOps Engineer", "linkedin": "https://linkedin.com/in/asha",
                  "location": {"city": "Pune", "state": "Maharashtra", "country": "India"},
                  "education": {"highest_level": "Bachelor's", "degree": "B.Tech", "university": "PICT", "graduation_year": "2024"}},
    "experience": {"current_company": "Beta Corp", "current_title": "DevOps Engineer"},
    "compensation": {"expected_ctc_inr": 600000, "current_ctc_inr": 0, "notice_period_days": 30},
    "search": {"skills": ["Kubernetes", "AWS"]},
}


def field(label, kind="text", **k):
    d = {"kind": kind, "type": k.pop("type", "text"), "label": label, "name": "", "id": "", "autocomplete": "", "required": True,
         "value": "", "checked": False, "options": [], "maxlength": 0, "role": "", "i": "e1"}
    d.update(k)
    return d


YESNO = [{"i": "o1", "text": "Yes"}, {"i": "o2", "text": "No"}]
SEL_YESNO = [{"text": "Select", "value": "", "placeholder": True}, {"text": "Yes", "value": "y"}, {"text": "No", "value": "n"}]


@pytest.fixture
def F(tmp_path):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    p = dict(PROFILE)
    p["applicant"] = dict(PROFILE["applicant"], resume_path=str(pdf))
    return ca.facts(p, "Worked at Beta Corp on Kubernetes")


# ---------------------------------------------------------------- pure decision rules
@pytest.mark.parametrize("label,expected", [
    ("First name *", ("fill", "Asha")), ("Last Name", ("fill", "Rao")), ("Email address", ("fill", "asha@example.com")),
    ("Full name", ("fill", "Asha Rao")), ("LinkedIn profile", ("fill", "https://linkedin.com/in/asha")),
    ("Current company", ("fill", "Beta Corp")), ("Expected CTC (LPA)", ("fill", "6")),
    ("Expected CTC (INR per annum)", ("fill", "600000")), ("Expected monthly salary (INR)", ("fill", "50000")),
    ("Notice period (days)", ("fill", "30")), ("Total experience in years", ("fill", "2")),
    ("Current CTC in LPA", ("fill", "0")), ("Graduation year", ("fill", "2024")),
])
def test_text_fields_from_profile(F, label, expected):
    act, val, _ = ca.decide(field(label), F, {})
    assert (act, val) == expected


def test_money_without_a_unit_is_not_guessed(F):
    assert ca.decide(field("Expected CTC"), F, {})[0] == "unresolved"
    assert ca.decide(field("Expected CTC", required=False), F, {})[0] == "skip"


def test_skill_specific_experience_is_not_answered_with_total_years(F):
    assert ca.decide(field("Years of experience with Terraform"), F, {})[0] == "unresolved"
    asked = []
    act, val, note = ca.decide(field("Years of experience with Terraform"), F, {}, ask=lambda q, o, k: asked.append(q) or "1")
    assert (act, val, note) == ("fill", "1", "llm") and asked


def test_identity_facts_never_go_to_the_model(F):
    p = {"applicant": {"name": "Asha Rao"}}
    F2 = ca.facts(p)
    called = []
    act, _, _ = ca.decide(field("Phone number", type="tel"), F2, {}, ask=lambda *a: called.append(a) or "123")
    assert act == "unresolved" and not called


def test_yes_no_questions(F):
    assert ca.decide(field("Are you legally authorized to work in India?", "radio", options=YESNO), F, {}) == ("choose", 0, "yes/no")
    assert ca.decide(field("Do you require visa sponsorship?", "select", options=SEL_YESNO), F, {}) == ("select", 2, "yes/no")
    assert ca.decide(field("Are you willing to relocate?", "radio", options=YESNO), F, {})[:2] == ("choose", 0)
    assert ca.decide(field("Are you 18 years or older?", "radio", options=YESNO), F, {})[:2] == ("choose", 0)
    # worked here before: only "yes" when the resume mentions the company
    assert ca.decide(field("Have you previously worked for us?", "radio", options=YESNO), F, {"company": "Beta Corp"})[:2] == ("choose", 0)
    assert ca.decide(field("Have you previously worked for us?", "radio", options=YESNO), F, {"company": "Acme"})[:2] == ("choose", 1)


def test_demographic_questions_decline_or_stay_unresolved(F):
    sel = [{"text": "Select", "value": "", "placeholder": True}, {"text": "Male", "value": "m"}, {"text": "Female", "value": "f"},
           {"text": "I prefer not to say", "value": "x"}]
    assert ca.decide(field("Gender", "select", options=sel), F, {}) == ("select", 3, "declined to self-identify")
    no_decline = sel[:3]
    assert ca.decide(field("Gender", "select", options=no_decline), F, {})[0] == "unresolved"
    assert ca.decide(field("Gender", "select", options=no_decline, required=False), F, {})[0] == "skip"
    assert ca.decide(field("Date of birth"), F, {})[0] == "unresolved"


def test_checkboxes(F):
    assert ca.decide(field("I agree to the privacy policy", "checkbox"), F, {})[0] == "check"
    assert ca.decide(field("Send me job alerts and our newsletter", "checkbox"), F, {})[0] == "skip"
    assert ca.decide(field("Do you own a car?", "checkbox"), F, {})[0] == "unresolved"


def test_select_options_matched(F):
    notice = [{"text": "Select", "value": "", "placeholder": True}, {"text": "Immediate", "value": "0"},
              {"text": "15 days", "value": "15"}, {"text": "30 days", "value": "30"}, {"text": "60 days", "value": "60"}]
    assert ca.decide(field("Notice period", "select", options=notice), F, {}) == ("select", 3, "notice")
    exp = [{"text": "Select", "value": "", "placeholder": True}, {"text": "0-1 years", "value": "a"}, {"text": "1-3 years", "value": "b"},
           {"text": "3-5 years", "value": "c"}, {"text": "5+ years", "value": "d"}]
    assert ca.decide(field("Total experience", "select", options=exp), F, {}) == ("select", 2, "years")
    hear = [{"text": "Select", "value": "", "placeholder": True}, {"text": "Friend", "value": "f"}, {"text": "LinkedIn", "value": "l"}]
    assert ca.decide(field("How did you hear about us?", "select", options=hear), F, {}) == ("select", 2, "source")


def test_prefilled_fields_are_left_alone(F):
    assert ca.decide(field("Email", value="x@y.com"), F, {})[0] == "skip"
    assert ca.decide(field("Authorized?", "radio", options=YESNO, checked=True), F, {})[0] == "skip"


def test_llm_answer_must_be_one_of_the_options(F):
    opts = [{"text": "Select", "value": "", "placeholder": True}, {"text": "Alpha", "value": "a"}, {"text": "Beta", "value": "b"}]
    assert ca.decide(field("Preferred team", "select", options=opts), F, {}, ask=lambda q, o, k: "Beta") == ("select", 2, "llm")
    assert ca.decide(field("Preferred team", "select", options=opts), F, {}, ask=lambda q, o, k: "Gamma")[0] == "unresolved"
    assert ca.decide(field("Preferred team", "select", options=opts), F, {}, ask=lambda q, o, k: None)[0] == "unresolved"


def test_cover_letter_defaults_to_a_profile_note(F):
    act, val, _ = ca.decide(field("Why do you want to join us?", "textarea"), F, {})
    assert act == "fill" and "Asha Rao" in val and "2 years" in val


def test_title_matches():
    assert ca.title_matches("DevOps Engineer", "Apply: DevOps Engineer - Pune")
    assert not ca.title_matches("Site Reliability Engineer", "Marketing Manager opening")


def test_phone_respects_maxlength(F):
    act, val, _ = ca.decide(field("Phone", type="tel", maxlength=10), F, {})
    assert (act, val) == ("fill", "9876543210")


# ---------------------------------------------------------------- browser flow against local fixture sites
def _chrome():
    cands = [os.environ.get("JOBHUB_CHROME", "")]
    cands += [str(p) for p in (ROOT / ".browsers").glob("chromium-*/chrome-linux*/chrome")]
    cands += [str(p) for p in (Path.home() / "Personal/job-hub/.browsers").glob("chromium-*/chrome-linux*/chrome")]
    return next((c for c in cands if c and Path(c).exists()), "")


APPLY_FORM = """<!doctype html><title>Apply - DevOps Engineer</title><body><h1>DevOps Engineer</h1>
<form id=f onsubmit="event.preventDefault(); {on_submit}">
 <label for=fn>First name *</label><input id=fn name=first_name required>
 <label for=ln>Last name *</label><input id=ln name=last_name required>
 <label for=em>Email *</label><input id=em type=email name=email required>
 <label for=ph>Phone *</label><input id=ph type=tel name=phone required>
 <label for=cv>Resume/CV *</label><input id=cv type=file name=resume required>
 <label for=ex>Total experience (years) *</label><select id=ex name=exp required><option value="">Select</option><option>0-1 years</option><option>1-3 years</option><option>3-5 years</option></select>
 <fieldset><legend>Are you legally authorized to work in India? *</legend>
   <label><input type=radio name=auth value=yes required> Yes</label><label><input type=radio name=auth value=no> No</label></fieldset>
 <label for=g>Gender</label><select id=g name=gender><option value="">Select</option><option>Male</option><option>Female</option><option>Prefer not to say</option></select>
 <label><input type=checkbox id=pp name=privacy required> I agree to the privacy policy</label>
 <label><input type=checkbox id=nl name=news> Send me the newsletter</label>
 <input name=website_url style="position:absolute;left:-9999px" tabindex=-1 autocomplete=off>
 <input type=text name=trap style="display:none">
 {extra}
 <button type=submit>Submit application</button>
</form><div id=out></div></body>"""
THANKS = "document.body.innerHTML='<h2>Thank you! Your application has been submitted.</h2>'"


def pages(**over):
    p = {
        "/ok.html": APPLY_FORM.format(on_submit=THANKS, extra=""),
        "/silent.html": APPLY_FORM.format(on_submit="document.getElementById('out').textContent=''", extra=""),
        "/job.html": "<title>DevOps Engineer</title><h1>DevOps Engineer</h1><p>Join our Pune team.</p><a href='/ok.html'>Apply Now</a>",
        "/embed.html": "<title>DevOps Engineer</title><h1>DevOps Engineer</h1><iframe src='/ok.html' width=900 height=1200></iframe>",
        "/list.html": "<title>Jobs</title><h1>Open roles: DevOps Engineer</h1><a href='/ok.html'>Apply</a> <a href='/silent.html'>Apply</a>",
        "/closed.html": "<title>DevOps Engineer</title><h1>DevOps Engineer</h1><p>This position has been filled.</p>",
        "/captcha.html": APPLY_FORM.format(on_submit=THANKS, extra="<div class='g-recaptcha' style='width:300px;height:80px'>captcha</div>"),
        "/login.html": "<title>DevOps Engineer</title><h1>DevOps Engineer</h1><p>Sign in to apply</p><input type=password><button>Sign in</button>",
        "/extra.html": APPLY_FORM.format(on_submit=THANKS, extra="<label for=q>Describe your biggest outage *</label><textarea id=q name=q required></textarea>"),
        "/multi.html": """<title>DevOps Engineer</title><h1>DevOps Engineer</h1>
<div id=s1><input name=first_name placeholder="First name" required><input name=last_name placeholder="Last name" required>
<input type=email name=email placeholder="Email" required><button type=button onclick="s1.hidden=true;s2.hidden=false">Next</button></div>
<div id=s2 hidden><input type=tel name=phone placeholder="Phone" required><input type=file name=cv aria-label="Resume">
<button type=button onclick="document.body.innerHTML='<p>Thanks for applying!</p>'">Submit</button></div>""",
    }
    p.update(over)
    return p


@pytest.fixture
def site(tmp_path):
    served = tmp_path / "www"
    served.mkdir()
    for path, html in pages().items():
        (served / path.lstrip("/")).write_text(html)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def browser():
    exe = _chrome()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True, executable_path=exe) if exe else p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001 - no browser installed on this machine
            pytest.skip(f"no Chromium available ({type(e).__name__}); run `playwright install chromium` or set JOBHUB_CHROME")
        yield b
        b.close()


def run(browser, F, tmp_path, url, dry=False, ask=None, title="DevOps Engineer"):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.set_default_timeout(8000)
    try:
        return ca.apply_one(ctx, {"id": 7, "url": url, "title": title, "company": "Acme"}, F, dry, ask, shot_dir=tmp_path / "shots",
                            step_pause_ms=100)
    finally:
        ctx.close()


def test_applies_through_the_apply_button(browser, site, F, tmp_path):
    r = run(browser, F, tmp_path, site + "/job.html")
    assert r["status"] == "applied", r
    assert r["filled"] >= 9 and Path(r["screenshot"]).exists()


def test_form_inside_an_iframe(browser, site, F, tmp_path):
    assert run(browser, F, tmp_path, site + "/embed.html")["status"] == "applied"


def test_dry_run_fills_but_does_not_submit(browser, site, F, tmp_path):
    ctx = browser.new_context()
    page_seen = {}

    def on_req(req):
        if req.method == "POST":
            page_seen["post"] = req.url
    ctx.on("request", on_req)
    r = ca.apply_one(ctx, {"id": 1, "url": site + "/ok.html", "title": "DevOps Engineer"}, F, True, None, shot_dir=tmp_path / "s",
                     step_pause_ms=100)
    ctx.close()
    assert r["status"] == "dry_filled" and r["filled"] >= 9 and Path(r["screenshot"]).exists() and not page_seen


def test_values_land_in_the_right_fields_and_honeypots_stay_empty(browser, site, F, tmp_path):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(site + "/silent.html")
    scope, descs = ca._find_form(page)
    labels = {d["name"] for d in descs}
    assert "website_url" not in labels and "trap" not in labels  # off-screen / display:none honeypots are invisible to the filler
    ctx.close()
    r = run(browser, F, tmp_path, site + "/silent.html")
    assert r["status"] == "unconfirmed" and "no confirmation" in r["reason"]


def test_fills_expected_values(browser, site, F, tmp_path):
    ctx = browser.new_context()
    ctx.set_default_timeout(8000)
    page = ctx.new_page()
    page.goto(site + "/silent.html")
    scope, descs = ca._find_form(page)
    for d in descs:
        act, val, _ = ca.decide(d, F, {})
        if act not in ("skip", "unresolved"):
            assert ca._apply_action(scope, d, act, val)
    got = page.evaluate("""() => { const q = n => document.querySelector('[name=' + n + ']');
      return {first: q('first_name').value, last: q('last_name').value, email: q('email').value, phone: q('phone').value,
        exp: q('exp').value, auth: document.querySelector('input[name=auth]:checked')?.value, gender: q('gender').value,
        privacy: q('privacy').checked, news: q('news').checked, trap: q('trap').value, site: q('website_url').value,
        file: q('resume').files.length}; }""")
    ctx.close()
    assert got == {"first": "Asha", "last": "Rao", "email": "asha@example.com", "phone": "+919876543210", "exp": "1-3 years",
                   "auth": "yes", "gender": "Prefer not to say", "privacy": True, "news": False, "trap": "", "site": "", "file": 1}


def test_stops_on_captcha_without_submitting(browser, site, F, tmp_path):
    r = run(browser, F, tmp_path, site + "/captcha.html")
    assert r["status"] == "captcha"


def test_stops_at_a_login_wall(browser, site, F, tmp_path):
    assert run(browser, F, tmp_path, site + "/login.html")["status"] == "login"


def test_closed_job(browser, site, F, tmp_path):
    assert run(browser, F, tmp_path, site + "/closed.html")["status"] == "closed"


def test_job_list_is_not_treated_as_one_job(browser, site, F, tmp_path):
    r = run(browser, F, tmp_path, site + "/list.html")
    assert r["status"] == "no_form" and "job list" in r["reason"]


def test_wrong_page_for_the_title(browser, site, F, tmp_path):
    r = run(browser, F, tmp_path, site + "/closed.html".replace("closed", "job"), title="Quantum Chef")
    assert r["status"] == "no_form" and "title" in r["reason"]


def test_required_unknown_question_aborts_without_a_model(browser, site, F, tmp_path, monkeypatch):
    served_extra = site + "/extra.html"
    r = run(browser, F, tmp_path, served_extra, ask=None)
    assert r["status"] == "unresolved" and "outage" in r["reason"].lower()


def test_required_unknown_question_answered_by_model(browser, site, F, tmp_path):
    r = run(browser, F, tmp_path, site + "/extra.html", ask=lambda q, o, k: "I restored a failed cluster in 20 minutes.")
    assert r["status"] == "applied", r


def test_multi_step_form(browser, site, F, tmp_path):
    r = run(browser, F, tmp_path, site + "/multi.html")
    assert r["status"] == "applied" and r["steps"] == 2, r


# ---------------------------------------------------------------- 2026-10-30: fixes from live failures
DEGREES = [{"text": "Choose", "value": "", "placeholder": True}, {"text": "BE", "value": "be"}, {"text": "B.Tech", "value": "bt"},
           {"text": "M.Tech", "value": "mt"}]
BRANCHES = [{"text": "Choose", "value": "", "placeholder": True}, {"text": "Computer Engineering", "value": "ce"},
            {"text": "Information Technology", "value": "it"}, {"text": "Computer Science & Engineering", "value": "cse"},
            {"text": "Mechanical", "value": "me"}]


def test_degree_option_matched_from_a_long_degree(F):
    F["degree"] = "B.Tech Computer Science"
    act, val, _ = ca.decide(field("Degree", "select", options=DEGREES), F, {})
    assert (act, DEGREES[val]["text"]) == ("select", "B.Tech")
    F["degree"] = "Bachelor of Engineering (Computer)"
    act, val, _ = ca.decide(field("Degree", "select", options=DEGREES), F, {})
    assert DEGREES[val]["text"] == "BE"


def test_branch_from_the_degree_text(F):
    F["degree"] = "B.Tech Computer Science"
    act, val, _ = ca.decide(field("Branch", "select", options=BRANCHES), F, {})
    assert (act, BRANCHES[val]["text"]) == ("select", "Computer Science & Engineering")
    act, val, _ = ca.decide(field("Specialization / Stream *", "text"), F, {})
    assert (act, val) == ("fill", "Computer Science")


def test_gender_from_profile_else_declined(F):
    opts = [{"text": "Select", "value": "", "placeholder": True}, {"text": "Male", "value": "m"}, {"text": "Female", "value": "f"},
            {"text": "Prefer not to say", "value": "x"}]
    F["gender"] = "Male"
    act, val, _ = ca.decide(field("Gender *", "select", options=opts), F, {})
    assert (act, opts[val]["text"]) == ("select", "Male")
    F["gender"] = ""
    act, val, _ = ca.decide(field("Gender *", "select", options=opts), F, {})
    assert opts[val]["text"] == "Prefer not to say"
    F["gender"] = "Female"
    assert ca.decide(field("Gender *", "text"), F, {})[:2] == ("fill", "Female")
    act, val, _ = ca.decide(field("Race / ethnicity *", "select", options=opts), F, {})   # only gender comes from the profile
    assert opts[val]["text"] == "Prefer not to say"


def test_facts_carry_gender():
    p = {**PROFILE, "applicant": {**PROFILE["applicant"], "gender": "Male"}}
    assert ca.facts(p)["gender"] == "Male"


def test_form_hosts_and_success_text():
    assert ca.is_form_host("https://forms.gle/PuMo3EkPyaj19ntGA")
    assert ca.is_form_host("https://docs.google.com/forms/d/e/1FAI/viewform")
    assert ca.is_form_host("https://acme.typeform.com/to/x") and not ca.is_form_host("https://acme.com/careers")
    assert ca.SUCCESS_RX.search("Your response has been recorded.")


def test_caps_defaults_raised():
    import autopilot
    c = autopilot.cfg({})
    assert c["career_apply_max_per_run"] == 10 and c["career_apply_max_per_day"] == 40


SPA = """<title>Workday</title><body><div id=root>Loading...</div><script>
setTimeout(() => { root.innerHTML = "<h1>DevOps Engineer</h1><a href='/ok.html'>Apply</a>"; }, 2500);</script></body>"""
LOGIN_THEN_FORM = """<title>DevOps Engineer</title><h1>DevOps Engineer</h1><div id=w><p>Sign in to apply</p><input type=password id=pw>
<button onclick="location.href='/ok.html'">Sign in</button></div>"""


@pytest.fixture
def site2(tmp_path):
    served = tmp_path / "www2"
    served.mkdir()
    for path, html in {**pages(), "/spa.html": SPA, "/login2.html": LOGIN_THEN_FORM}.items():
        (served / path.lstrip("/")).write_text(html)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_waits_for_a_slow_single_page_app_before_the_title_check(browser, site2, F, tmp_path):
    r = run(browser, F, tmp_path, site2 + "/spa.html")
    assert r["status"] == "applied", r


def test_login_wall_is_handed_to_the_user_then_continues(browser, site2, F, tmp_path):
    asked = []

    def human(page, kind, reason):
        asked.append(kind)
        page.click("button")                 # what the user does in the remote browser
        page.wait_for_load_state("domcontentloaded")
        return True
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.set_default_timeout(8000)
    try:
        r = ca.apply_one(ctx, {"id": 8, "url": site2 + "/login2.html", "title": "DevOps Engineer", "company": "Acme"}, F, False,
                         None, shot_dir=tmp_path / "s", step_pause_ms=100, human=human)
    finally:
        ctx.close()
    assert asked == ["login"] and r["status"] == "applied", r


def test_login_wall_without_help_still_stops(browser, site2, F, tmp_path):
    ctx = browser.new_context()
    try:
        r = ca.apply_one(ctx, {"id": 9, "url": site2 + "/login2.html", "title": "DevOps Engineer"}, F, False, None,
                         shot_dir=tmp_path / "s", step_pause_ms=100, human=lambda page, kind, reason: False)
    finally:
        ctx.close()
    assert r["status"] == "login"


def test_make_human_only_when_help_is_available(monkeypatch):
    import attention
    monkeypatch.setattr(attention, "available", lambda profile=None: False)
    assert ca.make_human({}, {}) is None
    monkeypatch.setattr(attention, "available", lambda profile=None: True)
    calls = []
    monkeypatch.setattr(attention, "ask_and_wait", lambda kind, site, url, reason, cleared, **k: calls.append((kind, site)) or True)

    class Pg:
        url = "https://kyndryl.wd5.myworkdayjobs.com/x/job/1"

        def wait_for_timeout(self, ms):
            pass
    h = ca.make_human({}, {"title": "SRE", "company": "Kyndryl"})
    assert h(Pg(), "login", "needs an account") and calls == [("login", "Workday")]
