"""Board job -> the same role on the company's own careers site (career_resolve), and the hand-off to the form filler."""
import json

import autopilot
import career_apply
import career_resolve as cr
import core


class R:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status

    def json(self):
        return self._b


def fake_http(boards: dict):
    """boards: {url_prefix: body}. Anything else is a 404, like an ATS that does not know the slug."""
    calls = []

    def get(url, headers=None, timeout=None):
        calls.append(url)
        for prefix, body in boards.items():
            if url.startswith(prefix):
                return R(body)
        return R({}, 404)
    get.calls = calls
    return get


GH = {"jobs": [{"title": "Senior Data Scientist", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "location": {"name": "Pune"}},
               {"title": "DevOps Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2", "location": {"name": "Pune, India"}},
               {"title": "DevOps Engineer II", "absolute_url": "https://boards.greenhouse.io/acme/jobs/3", "location": {"name": "Austin"}}]}


def board_job(**kw):
    j = {"id": 1, "url": "https://www.linkedin.com/jobs/view/123", "title": "DevOps Engineer", "company": "Acme Technologies Pvt Ltd",
         "location": "Pune, Maharashtra", "apply_url": "", "company_site": ""}
    j.update(kw)
    return j


def test_title_matching_rejects_different_roles():
    assert cr.title_similarity("DevOps Engineer", "DevOps Engineer (Remote)") >= cr.TITLE_MIN
    assert cr.title_similarity("Sr. DevOps Engineer", "Senior DevOps Engineer") >= cr.TITLE_MIN
    assert cr.title_similarity("DevOps Engineer", "Senior Data Scientist") < cr.TITLE_MIN
    assert cr.title_similarity("Cloud Engineer", "Sales Executive") == 0
    assert cr.title_similarity("DevOps Engineer", "Distinguished Engineer, Core DevOps") == 0     # seniority added
    assert cr.title_similarity("Site Reliability Engineer", "Lead Software Engineer - Site Reliability") == 0


def test_slugs_strip_company_suffixes():
    assert cr.slugs("Acme Technologies Pvt Ltd") == ["acme"]
    assert cr.slugs("Blue Yonder India", "blueyonder.com") == ["blueyonder", "blue-yonder", "blue"]


def test_finds_the_role_on_the_public_greenhouse_board_and_prefers_the_same_city():
    http = fake_http({"https://boards-api.greenhouse.io/v1/boards/acme/": GH})
    r = cr.resolve(board_job(), http=http, use_firecrawl=False)
    assert r["apply_url"] == "https://boards.greenhouse.io/acme/jobs/2" and "greenhouse" in r["how"]


def test_lever_board_and_direct_link_from_the_board_win():
    lever = [{"text": "DevOps Engineer", "applyUrl": "https://jobs.lever.co/acme/x/apply", "categories": {"location": "Pune"}}]
    http = fake_http({"https://api.lever.co/v0/postings/acme": lever})
    assert cr.resolve(board_job(), http=http, use_firecrawl=False)["apply_url"] == "https://jobs.lever.co/acme/x/apply"
    direct = cr.resolve(board_job(apply_url="https://careers.acme.com/jobs/77"), http=fake_http({}))
    assert direct["apply_url"] == "https://careers.acme.com/jobs/77" and "direct" in direct["how"]
    board = cr.resolve(board_job(apply_url="https://www.indeed.com/applystart?x=1"), http=fake_http({}), use_firecrawl=False,
                       domain_of=lambda c: "")
    assert "apply_url" not in board  # a board link is not a company page


def test_web_search_accepts_only_the_companys_own_page():
    hits = [{"href": "https://jobfound.org/job/acme-is-hiring-devops-engineer", "title": "DevOps Engineer - Acme"},
            {"href": "https://careers.acme.com/job/R-1/DevOps-Engineer", "title": "DevOps Engineer | Acme Careers"},
            {"href": "https://careers.acme.com/job/R-2/Lead-DevOps-Engineer", "title": "Lead DevOps Engineer | Acme"}]
    r = cr.resolve(board_job(), http=fake_http({}), use_firecrawl=False, domain_of=lambda c: "", search=lambda q: hits)
    assert r["apply_url"] == "https://careers.acme.com/job/R-1/DevOps-Engineer" and r["how"].startswith("web search")
    only_copies = [hits[0], {"href": "https://devsunite.com/jobs/acme-devops-engineer", "title": "DevOps Engineer"}]
    assert "apply_url" not in cr.resolve(board_job(), http=fake_http({}), use_firecrawl=False, domain_of=lambda c: "",
                                         search=lambda q: only_copies)


def test_no_matching_role_means_no_apply_link():
    http = fake_http({"https://boards-api.greenhouse.io/v1/boards/acme/": GH})
    r = cr.resolve(board_job(title="Chief Financial Officer"), http=http, search=False)
    assert "apply_url" not in r and "none matching" in r["why"]


def test_falls_back_to_the_careers_page_through_firecrawl():
    class FC:
        def find_career_url(self, domain):
            return f"https://{domain}/careers"

        def extract_jobs(self, page):
            return [{"title": "Marketing Lead", "url": "/careers/9"}, {"title": "DevOps Engineer", "url": "/careers/42"}]
    r = cr.resolve(board_job(), http=fake_http({}), domain_of=lambda c: "acme.com", firecrawl=FC())
    assert r["apply_url"] == "https://acme.com/careers/42" and r["domain"] == "acme.com"


def test_resolved_link_is_what_the_form_filler_opens(tmp_path, monkeypatch):
    p = core.load_profile()
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    p["applicant"]["resume_path"] = str(pdf)
    autopilot.save_job({"source": "jobspy-linkedin", "url": "https://www.linkedin.com/jobs/view/5", "title": "DevOps Engineer",
                        "company": "Acme", "apply_url": ""})
    with core.db() as db:
        jid = db.execute("SELECT id FROM jobs").fetchone()[0]
        db.execute("UPDATE jobs SET status='scored', score=80 WHERE id=?", (jid,))
    assert career_apply.eligible(p, [jid])[0] == []                     # board link only: nothing to fill
    monkeypatch.setattr(cr, "ats_jobs", lambda slug, http=None: ("greenhouse", GH["jobs"] and
                        [{"title": "DevOps Engineer", "url": "https://boards.greenhouse.io/acme/jobs/2", "location": "Pune"}]))
    out = cr.resolve_phase([jid], log=lambda *_: None)
    assert out == {"checked": 1, "found": 1, "not_found": 0}
    jobs, _ = career_apply.eligible(p, [jid])
    assert jobs[0]["url"] == "https://boards.greenhouse.io/acme/jobs/2" and jobs[0]["board_url"].startswith("https://www.linkedin.com")


def test_queued_job_reuses_its_contact_without_a_new_lookup():
    autopilot.save_job({"source": "jobspy-indeed", "url": "https://in.indeed.com/viewjob?jk=1", "title": "DevOps Engineer", "company": "Acme"})
    with core.db() as db:
        jid = db.execute("SELECT id FROM jobs").fetchone()[0]
        db.execute("UPDATE jobs SET status='queued', contact='hr@acme.com' WHERE id=?", (jid,))
        db.execute("INSERT INTO hr_contacts(email,domain,confidence,kind) VALUES('hr@acme.com','acme.com','high','site')")
        job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())
    assert autopilot.known_contact(job) == {"email": "hr@acme.com", "confidence": "high", "source": "site"}
    with core.db() as db:
        db.execute("INSERT INTO suppression(key,reason,at) VALUES('hr@acme.com','bounce','now')")
    assert autopilot.known_contact(job) == {}
    assert autopilot.known_contact({**job, "status": "scored"}) == {}


def test_old_database_gets_the_new_columns(tmp_db):
    import sqlite3
    c = sqlite3.connect(core.DB_PATH)
    c.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY, source TEXT, ext_id TEXT, url TEXT UNIQUE, title TEXT, company TEXT, "
              "location TEXT, posted TEXT, description TEXT, company_site TEXT, recruiter TEXT, apply_type TEXT, jd_emails TEXT, "
              "score REAL, matched TEXT, status TEXT DEFAULT 'new', reason TEXT, contact TEXT, found_at TEXT, updated_at TEXT)")
    c.commit()
    c.close()
    with core.db() as db:
        cols = {r[1] for r in db.execute("PRAGMA table_info(jobs)")}
    assert {"apply_url", "resolve_note"} <= cols
    json.dumps(sorted(cols))
