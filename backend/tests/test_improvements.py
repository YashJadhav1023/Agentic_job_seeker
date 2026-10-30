"""2026-10-30 improvements: form Q&A memory, freshness + high-match alerts, email patterns / catch-all, JD keyword gap."""


import autopilot
import career_apply as ca
import company_finder
import composer
import contacts
import core
import form_qa
import globalsearch
import notify
import server


def _field(label, kind="text", **k):
    d = {"kind": kind, "type": "text", "label": label, "name": "", "id": "", "autocomplete": "", "required": True, "value": "",
         "checked": False, "options": [], "maxlength": 0, "role": "", "i": "e1"}
    d.update(k)
    return d


# ---------------------------------------------------------------- form Q&A memory
def test_unknown_question_is_remembered_once_and_answer_reused():
    F = {"qa": form_qa.answers()}
    d = _field("Years of experience with ArgoCD? *")
    assert ca.decide(d, F, {})[0] == "unresolved"
    form_qa.record_unanswered(d["label"], [], "Acme")
    form_qa.record_unanswered(d["label"], [], "Beta")            # same question again: still one entry
    pend = form_qa.pending()
    assert len(pend) == 1 and pend[0]["seen"] == 2
    form_qa.save_answer(pend[0]["id"], "1")
    F = {"qa": form_qa.answers()}
    assert ca.decide(d, F, {})[:2] == ("fill", "1")
    assert ca.decide(_field("years of experience with  ArgoCD"), F, {})[:2] == ("fill", "1")  # normalised key


def test_saved_answer_must_match_an_option():
    form_qa.record_unanswered("Do you have a valid passport? *", ["Yes", "No"], "Acme")
    form_qa.save_answer(form_qa.pending()[0]["id"], "Yes")
    F = {"qa": form_qa.answers()}
    opts = [{"text": "Select", "value": "", "placeholder": True}, {"text": "Yes", "value": "y"}, {"text": "No", "value": "n"}]
    act, val, note = ca.decide(_field("Do you have a valid passport? *", "select", options=opts), F, {})
    assert (act, opts[val]["text"], note) == ("select", "Yes", "saved answer")


def test_server_tools_for_questions():
    form_qa.record_unanswered("Expected joining date *", [], "Acme")
    out = server.form_questions({})
    qid = out["questions"][0]["id"]
    assert server.form_answer({"id": qid, "answer": "Within 30 days"}) == {"saved": True}
    assert server.form_questions({})["questions"] == []
    assert server.form_questions({"all": True})["questions"][0]["answer"] == "Within 30 days"


# ---------------------------------------------------------------- freshness + high-match alerts
def test_freshness_defaults():
    assert autopilot.cfg({})["job_age_days"] == 1
    calls = []
    globalsearch.collect({"search": {"keywords": ["SRE"], "locations": ["Pune"]}}, {},
                         lambda a: calls.append(a) or {"jobs": []}, log=lambda m: None)
    assert calls[0]["hours_old"] == 24


def test_high_match_pushes_one_alert(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "push", lambda p, t, x, url="": sent.append((t, x, url)) or True)
    monkeypatch.setattr(composer, "resume_profile", lambda p: {"text": "", "skills": set(), "bullets": []})
    scores = {"https://j/1": 91, "https://j/2": 60}
    monkeypatch.setattr(autopilot, "score_job", lambda p, c, rp, j: (scores[j["url"]], ["AWS"], ""))
    for u in scores:
        autopilot.save_job({"source": "linkedin", "ext_id": "", "url": u, "title": "DevOps Engineer", "company": "Acme",
                            "location": "Pune", "posted": "", "description": "x", "apply_type": "unknown"})
    autopilot.score_new({}, autopilot.cfg({}))
    assert len(sent) == 1 and "91" in sent[0][0] and sent[0][2] == "https://j/1"
    with core.db() as c:
        c.execute("UPDATE jobs SET status='new'")
    autopilot.score_new({}, autopilot.cfg({}))
    assert len(sent) == 1                       # never twice for the same job


# ---------------------------------------------------------------- email patterns + catch-all
def test_startup_patterns_generated():
    got = {c["pattern"]: c["email"] for c in company_finder.generate_candidate_emails("Priya Sharma", "acme.io")}
    assert got["{first}"] == "priya@acme.io" and got["{first}{last[0]}"] == "priyas@acme.io"


def test_pattern_inference_recognises_first_plus_initial():
    assert company_finder.infer_email_pattern(["rahulk@acme.io", "priyas@acme.io"], "acme.io") == "{first}{last[0]}"
    assert company_finder.infer_email_pattern(["rahul@acme.io", "priya@acme.io"], "acme.io") == "{first}"


def test_catch_all_guess_is_never_auto_sent(monkeypatch):
    monkeypatch.setattr(contacts, "resolve_domain", lambda company, hint="": "acme.io")
    monkeypatch.setattr(contacts, "crawl_site_emails", lambda d, max_pages=4: [])
    monkeypatch.setattr(contacts, "verify_email", lambda e, mail_from="", use_cache=True: "catch_all")
    out = contacts.find_contacts("Acme", "")
    assert out and all(c["confidence"] == "low" for c in out)


# ---------------------------------------------------------------- JD keyword gap (never claimed in the email)
def test_compose_reports_missing_keywords_without_claiming_them(monkeypatch):
    monkeypatch.setattr(composer, "resume_profile", lambda p: {"text": "Terraform Kubernetes", "skills": {"Terraform", "Kubernetes"},
                                                              "bullets": [{"text": "Built AKS CI/CD with Terraform", "skills": ["Terraform"]}]})
    p = {"applicant": {"name": "Y J", "phone": "1", "email": "y@x.io"}, "experience": {}, "compensation": {}}
    out = composer.compose(p, {"title": "DevOps Engineer", "company": "Acme",
                               "description": "We need Terraform, Kubernetes and ArgoCD and Prometheus."})
    assert "Argo CD" in out["missing"] and "Prometheus" in out["missing"]
    assert "Argo CD" not in out["body"] and "ArgoCD" not in out["body"] and "Prometheus" not in out["body"]
    assert out["pitch"] and len(out["pitch"]) <= 3


def test_career_cover_note_uses_the_matched_keywords(monkeypatch):
    F = {"cover_note": "Hello.", "skills": "Terraform, Kubernetes, Docker"}
    note = ca.tailored_cover(F, {"title": "SRE", "company": "Acme", "description": "Kubernetes and Terraform at scale; Rust"})
    assert "Kubernetes" in note and "Terraform" in note and "Rust" not in note and "Acme" in note
