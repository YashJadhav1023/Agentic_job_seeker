"""Find a board job (LinkedIn / Indeed / Naukri / Google) on the company's OWN careers site, so the form filler can apply
there instead of stopping at "no eligible company-page jobs".

Order, cheapest first:
  1. the direct apply link the board already gave us (jobs.apply_url, from JobSpy's job_url_direct);
  2. the company's public ATS job board: Greenhouse, Lever, Ashby, SmartRecruiters and Workable have free JSON APIs keyed
     by a company slug (same providers career-ops scans, see vendor/career-ops/NOTICE.md);
  3. the company's careers page found and read through Firecrawl (only when JOBHUB_FIRECRAWL_API_KEY is set).

A role is accepted only when its title matches the board title closely (TITLE_MIN), so we never apply to a different job.
The result is stored on the job (apply_url, company_site) and the reason says how it was found.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests  # noqa: E402

import core  # noqa: E402

TIMEOUT = 12
TITLE_MIN = 0.6
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}
SUFFIX_RX = re.compile(r"\b(private|pvt|limited|ltd|llp|llc|inc|corp(oration)?|technologies|technology|solutions|software|"
                       r"services|systems|india|global|labs?|co)\b\.?", re.I)
STOP_WORDS = {"i", "ii", "iii", "the", "and", "of", "for", "a", "an", "to", "in", "at", "with", "-", "&", "/", "remote", "hybrid", "onsite"}
SYNONYMS = {"sr": "senior", "jr": "junior", "engg": "engineer", "eng": "engineer", "dev": "developer", "ops": "operations"}
SENIORITY = {"senior", "lead", "principal", "staff", "distinguished", "manager", "director", "head", "vp", "architect", "chief",
             "intern", "internship", "trainee"}


# ---------------------------------------------------------------- title matching
def _tokens(title: str) -> set:
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", (title or "").lower())
    out = set()
    for w in re.findall(r"[a-z0-9+#]+", t):
        w = SYNONYMS.get(w, w)
        if w not in STOP_WORDS:
            out.add(w)
    return out


def title_similarity(a: str, b: str) -> float:
    """Share of the board title's words found in the candidate title (Jaccard-weighted toward the board title).
    0 when the candidate adds a seniority level the board title does not have (Lead / Principal / Manager ...)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb or (tb & SENIORITY) - ta:
        return 0.0
    inter = len(ta & tb)
    return 0.5 * inter / len(ta) + 0.5 * inter / len(ta | tb)


def best_match(title: str, jobs: list, location: str = "") -> dict:
    loc = (location or "").split(",")[0].strip().lower()
    scored = []
    for j in jobs:
        s = title_similarity(title, j.get("title", ""))
        if s >= TITLE_MIN:
            if loc and loc in str(j.get("location", "")).lower():
                s += 0.05
            scored.append((s, j))
    return max(scored, key=lambda x: x[0])[1] if scored else {}


# ---------------------------------------------------------------- public ATS boards
def slugs(company: str, domain: str = "") -> list:
    name = SUFFIX_RX.sub(" ", (company or "").lower())
    words = re.findall(r"[a-z0-9]+", name)
    out = []
    for s in ("".join(words), "-".join(words), words[0] if words else "", (domain or "").split(".")[0]):
        if s and len(s) >= 3 and s not in out:
            out.append(s)
    return out[:4]


def _get(url: str, http=None):
    r = (http or requests.get)(url, headers=UA, timeout=TIMEOUT)
    return r.json() if r.status_code == 200 else None


def ats_jobs(slug: str, http=None) -> tuple:
    """-> (provider, [{title, url, location}]) for the first ATS that knows this slug."""
    probes = [
        ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
         lambda d: [{"title": j.get("title"), "url": j.get("absolute_url"), "location": (j.get("location") or {}).get("name", "")}
                    for j in (d or {}).get("jobs", [])]),
        ("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json",
         lambda d: [{"title": j.get("text"), "url": j.get("applyUrl") or j.get("hostedUrl"),
                     "location": (j.get("categories") or {}).get("location", "")} for j in (d if isinstance(d, list) else [])]),
        ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
         lambda d: [{"title": j.get("title"), "url": j.get("applyUrl") or j.get("jobUrl"), "location": j.get("location", "")}
                    for j in (d or {}).get("jobs", [])]),
        ("smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
         lambda d: [{"title": j.get("name"), "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                     "location": (j.get("location") or {}).get("city", "")} for j in (d or {}).get("content", [])]),
        ("workable", f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
         lambda d: [{"title": j.get("title"), "url": j.get("url") or j.get("application_url"), "location": j.get("city", "")}
                    for j in (d or {}).get("jobs", [])]),
    ]
    for name, url, parse in probes:
        try:
            d = _get(url, http)
        except (requests.RequestException, ValueError):
            continue
        jobs = [j for j in (parse(d) if d is not None else []) if j.get("title") and str(j.get("url") or "").startswith("http")]
        if jobs:
            return name, jobs
    return "", []


# ---------------------------------------------------------------- web search for the role on the company's own site
ATS_HOST_RX = re.compile(r"(^|\.)(myworkdayjobs\.com|greenhouse\.io|lever\.co|smartrecruiters\.com|ashbyhq\.com|workable\.com|"
                         r"darwinbox\.in|successfactors\.(com|eu)|taleo\.net|icims\.com|oraclecloud\.com|jobvite\.com|"
                         r"bamboohr\.com|breezy\.hr|recruitee\.com|keka\.com|zohorecruit\.(com|in)|freshteam\.com|"
                         r"eightfold\.ai|phenompeople\.com|avature\.net|teamtailor\.com|personio\.(de|com))$", re.I)
JOBPAGE_RX = re.compile(r"(job|career|position|opening|requisition|vacanc|apply)", re.I)


def _company_words(company: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", SUFFIX_RX.sub(" ", (company or "").lower())) if len(w) >= 3}


def owned_by(url: str, company: str) -> bool:
    """True when the page belongs to the company: its own domain (amazon.jobs, careers.statestreet.com), or a known ATS
    with the company in the host or path (jobs.lever.co/acme). Aggregators that copy the posting are rejected."""
    from urllib.parse import urlparse
    u = urlparse(url)
    host, path = (u.hostname or "").lower(), u.path.lower()
    words = _company_words(company)
    if not words:
        return False
    joined = "".join(sorted(words, key=len, reverse=True)[:1])
    labels = re.split(r"[.\-]", host)
    if ATS_HOST_RX.search(host):
        return any(w in host or w in path for w in words)
    # own domain: the registrable label (e.g. "statestreet" in careers.statestreet.com) is built from the company name
    name = re.sub(r"[^a-z0-9]", "", SUFFIX_RX.sub(" ", company.lower()))
    return any(lbl and len(lbl) >= 3 and (lbl == joined or lbl == name or (lbl in name and len(lbl) >= 5) or name.startswith(lbl) and len(lbl) >= 4)
               for lbl in labels[:-1])


def search_role(company: str, title: str, location: str = "", search=None) -> dict:
    """Web search (DuckDuckGo, free) for this role on the company's career site or ATS. -> {title, url} or {}."""
    import career_apply
    if search is None:
        from ddgs import DDGS

        def search(q):
            return DDGS().text(q, max_results=10)
    city = (location or "").split(",")[0].strip()
    for q in (f'"{company}" "{title}" careers apply', f"{company} {title} {city} job apply"):
        try:
            hits = list(search(q) or [])
        except Exception:  # noqa: BLE001 - search rate-limited: give up quietly, email path still runs
            return {}
        cands = []
        for h in hits:
            url, name = h.get("href") or "", (h.get("title") or "").split(" | ")[0].split(" - ")[0]
            if not url or not career_apply.is_company_page(url) or not JOBPAGE_RX.search(url) or not owned_by(url, company):
                continue
            cands.append({"title": h.get("title") or name, "url": url, "location": h.get("body") or ""})
        hit = best_match(title, cands, location)
        if hit:
            return hit
    return {}


# ---------------------------------------------------------------- resolve one job
def resolve(job: dict, use_firecrawl: bool = True, http=None, domain_of=None, firecrawl=None, search=None) -> dict:
    """-> {"apply_url", "how", "domain"} or {"why": "..."} when the role cannot be found on the company site."""
    import career_apply
    if job.get("apply_url") and career_apply.is_company_page(job["apply_url"]):
        return {"apply_url": job["apply_url"], "how": "direct apply link from the job board"}
    if career_apply.is_company_page(job.get("url", "")):
        return {"apply_url": job["url"], "how": "already on the company site"}
    company, title = (job.get("company") or "").strip(), job.get("title") or ""
    if not company:
        return {"why": "no company name"}
    domain = job.get("company_site") or ""
    if domain and "//" in domain:
        from urllib.parse import urlparse
        domain = urlparse(domain).netloc.removeprefix("www.")
    ats_note = ""
    for s in slugs(company, domain):
        provider, jobs = ats_jobs(s, http)
        if jobs:
            hit = best_match(title, jobs, job.get("location", ""))
            if hit:
                return {"apply_url": hit["url"], "how": f"{provider} board '{s}': {hit['title']}", "domain": domain}
            ats_note = f"{provider} board '{s}' has {len(jobs)} roles, none matching '{title}'"
            break
    if search is not False:
        hit = search_role(company, title, job.get("location", ""), search=search)
        if hit:
            return {"apply_url": hit["url"], "how": f"web search: {hit['title'][:80]}", "domain": domain}
    if ats_note:
        return {"why": ats_note}
    if not domain:
        try:
            import contacts
            domain = (domain_of or contacts.resolve_domain)(company)
        except Exception:  # noqa: BLE001 - search can rate-limit; treat as not found
            domain = ""
    if not domain:
        return {"why": "company website not found"}
    if not use_firecrawl:
        return {"why": "no public ATS board; careers page scan is off", "domain": domain}
    try:
        import career_scan
        fc = firecrawl or career_scan
        if not career_scan.api_key() and firecrawl is None:
            return {"why": "no public ATS board and no Firecrawl key to read the careers page", "domain": domain}
        page = fc.find_career_url(domain)
        if not page:
            return {"why": f"no careers page found on {domain}", "domain": domain}
        listed = [{**j, "url": urljoin(page, j.get("url") or "")} for j in fc.extract_jobs(page)]
        hit = best_match(title, listed, job.get("location", ""))
        if hit:
            return {"apply_url": hit["url"], "how": f"careers page {page}: {hit['title']}", "domain": domain}
        return {"why": f"careers page {page} lists no role matching '{title}'", "domain": domain}
    except Exception as e:  # noqa: BLE001 - Firecrawl out of credits / site broken: fall back to email
        return {"why": f"careers page scan failed: {str(e)[:120]}", "domain": domain}


def resolve_phase(job_ids: list | None = None, limit: int = 15, use_firecrawl: bool = True, log=print) -> dict:
    """Resolve company apply links for good board jobs that have none yet. Stores apply_url / company_site."""
    import autopilot
    with core.db() as db:
        if job_ids:
            q = ",".join("?" * len(job_ids))
            rows = [dict(r) for r in db.execute(f"SELECT * FROM jobs WHERE id IN ({q}) ORDER BY score DESC", job_ids)]
        else:
            rows = [dict(r) for r in db.execute(
                "SELECT * FROM jobs WHERE status IN ('scored','queued') AND COALESCE(apply_url,'')='' "
                "AND COALESCE(resolve_note,'')='' ORDER BY score DESC LIMIT ?", (limit,))]
    out = {"checked": 0, "found": 0, "not_found": 0}
    for j in rows[:max(limit, len(job_ids or []))]:
        if autopilot.stopped():
            break
        out["checked"] += 1
        r = resolve(j, use_firecrawl=use_firecrawl)
        upd = {"resolve_note": (r.get("how") or r.get("why") or "")[:200]}
        if r.get("domain") and not j.get("company_site"):
            upd["company_site"] = r["domain"]
        if r.get("apply_url"):
            upd["apply_url"] = r["apply_url"]
            out["found"] += 1
            log(f"[resolve] {j.get('title')} @ {j.get('company')}: {r['how']}")
        else:
            out["not_found"] += 1
            log(f"[resolve] {j.get('title')} @ {j.get('company')}: {r.get('why')} - will use email")
        autopilot.set_job(j["id"], **upd)
    return out
