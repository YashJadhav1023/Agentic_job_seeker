"""job-hub autopilot: discover -> score -> (platform apply) -> email fallback, with automated guards instead of prompts.

Flow of one run (all inside one background process, see server.autopilot_run):
  1. discover  LinkedIn (public pages via ai-job-search CLI) + Naukri (read-only Playwright) -> `jobs` table with full JD
  2. score     title filter, years-of-experience, skills overlap with the resume -> jobs.score / status scored|skip
  3. apply     optional Easy-Apply/Naukri bots (existing apply_jobs, own caps). If a bot log shows a restriction
               (captcha, limit, verification, session expired) the platform is put on cooldown.
  3b. forms    good jobs on company career pages: fill + submit the application form (career_apply.py). A form that
               cannot be completed safely (CAPTCHA, login, unanswerable required field) falls through to step 4.
  4. email     for good jobs: address in the JD (best) -> company site -> verified role inbox. A job is emailed when
               it names an address, or when its platform is restricted / it has no Easy Apply.
               The email is written from the JD + resume by composer.py and the resume PDF is attached.
Guards (no human prompt needed): data/STOP kill-switch, daily/run caps, one email per company window, suppression list,
bounce + unsubscribe handling, send window, recipient only from JD/site/verified inbox (never from free text args),
dry-run unless profile safety allows live actions.
"""
import imaplib, json, os, random, re, subprocess, sys, time
import email as emaillib
from datetime import datetime, timedelta
from pathlib import Path

import composer
import contacts
import core

STOP_FILE = core.ROOT / "data" / "STOP"
BUN = os.environ.get("JOBHUB_BUN") or str(core.ROOT / ".tools" / "node_modules" / ".bin" / "bun")
AIJS = core.ROOT / "ai-job-search"
LI_CLI = ".agents/skills/linkedin-search/cli/src/cli.ts"
LOC_MAP = {"pune": "Pune, Maharashtra, India", "mumbai": "Mumbai, Maharashtra, India", "hyderabad": "Hyderabad, Telangana, India",
           "bangalore": "Bengaluru, Karnataka, India", "bengaluru": "Bengaluru, Karnataka, India",
           "navi mumbai": "Navi Mumbai, Maharashtra, India", "thane": "Thane, Maharashtra, India"}
RESTRICT_RX = re.compile(r"restrict|unusual activity|security verification|captcha|verify (?:it'?s )?you|temporarily (?:paused|blocked)|"
                         r"too many requests|\b429\b|daily limit|easy apply limit|access denied|account.{0,20}(?:suspend|limit)|"
                         r"session (?:has )?expired|log ?in required|please log ?in", re.I)
DEFAULTS = {"min_score": 45, "max_discover": 30, "per_query": 8, "max_emails_per_run": 8, "send_window": None, "send_days": None,
            "apply_limits": {"naukri": 10, "linkedin": 5}, "platform_cooldown_hours": 24, "job_age_days": 7,
            "max_search_queries": 6, "career_apply": True, "career_apply_max_per_day": 5, "career_apply_max_per_run": 3,
            "career_apply_company_days": 30, "career_apply_retry_days": 3, "career_apply_max_attempts": 2,
            "scan_posts": False, "use_jobspy": False, "jobspy_sites": [], "schedule_every_hours": 0,
            "company_blocklist": ["jobgether"], "max_years_tolerance": 1, "polish_model": "", "max_lookups": 15}
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def cfg(profile: dict) -> dict:
    c = dict(DEFAULTS)
    c.update(profile.get("autopilot") or {})
    return c


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def stopped() -> bool:
    return STOP_FILE.exists()


# ---------------------------------------------------------------- platform state (restriction handling)
def platform_blocked(name: str) -> str:
    with core.db() as c:
        r = c.execute("SELECT blocked_until, reason FROM platform_state WHERE platform=?", (name,)).fetchone()
    if r and r["blocked_until"] > datetime.now().isoformat():
        return r["reason"] or "blocked"
    return ""


def block_platform(name: str, hours: float, reason: str) -> None:
    until = (datetime.now() + timedelta(hours=hours)).isoformat()
    with core.db() as c:
        c.execute("INSERT OR REPLACE INTO platform_state(platform,blocked_until,reason,at) VALUES(?,?,?,?)",
                  (name, until, reason[:200], core.now_iso()))
    log(f"platform '{name}' on cooldown until {until[:16]}: {reason[:100]}")


def restriction_in(text: str) -> str:
    m = RESTRICT_RX.search(text or "")
    return m.group(0) if m else ""


# ---------------------------------------------------------------- job store
def save_job(j: dict) -> bool:
    emails = contacts.extract_emails(j.get("description", ""))
    with core.db() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO jobs(source,ext_id,url,title,company,location,posted,description,company_site,recruiter,apply_type,"
            "jd_emails,status,found_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (j["source"], j.get("ext_id", ""), j["url"], j.get("title", ""), j.get("company", ""), j.get("location", ""),
             j.get("posted", ""), j.get("description", ""), j.get("company_site", ""), j.get("recruiter", ""),
             j.get("apply_type", "unknown"), json.dumps(emails), "new", core.now_iso(), core.now_iso()))
        inserted = cur.rowcount > 0
    for e in emails:
        dom = contacts.domain_of(e)
        if contacts.mx_hosts(dom):
            v = contacts.verify_email(e)
            with core.db() as c:
                c.execute(
                    "INSERT OR IGNORE INTO hr_contacts(email,name,role,company,domain,city,confidence,note,kind,found_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (e, j.get("recruiter", ""), j.get("title", ""), j.get("company", ""), dom, j.get("location", ""),
                     "high" if v in ("valid", "catch_all") else "medium",
                     f"job listing: {j.get('source')} - {j.get('title')}", "job", core.now_iso()))
    return inserted




def set_job(job_id: int, **kw) -> None:
    kw["updated_at"] = core.now_iso()
    cols = ", ".join(f"{k}=?" for k in kw)
    with core.db() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*kw.values(), job_id))


def known_urls() -> set:
    with core.db() as c:
        return {r[0] for r in c.execute("SELECT url FROM jobs")}


def _job_id(url: str) -> int:
    with core.db() as c:
        return c.execute("SELECT id FROM jobs WHERE url=?", (url,)).fetchone()[0]


# ---------------------------------------------------------------- discovery
def _bun(args: list, timeout: int = 75) -> str:
    r = subprocess.run([BUN, "run", LI_CLI] + args, cwd=str(AIJS), capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[:300])
    return r.stdout


def title_ok(profile: dict, title: str) -> bool:
    s = profile["search"]
    return bool(re.search(s["title_include"], title, re.I)) and not re.search(s["title_exclude"], title, re.I)


def search_pairs(profile: dict, c: dict, name: str) -> list:
    """This run's [keyword, location] pairs: every combination of search.keywords x search.locations, rotated so
    successive runs cover all of them while each run issues at most autopilot.max_search_queries searches."""
    s = profile["search"]
    kws = [k for k in s.get("keywords") or [] if k]
    locs = [l for l in s.get("locations") or [] if l and str(l).lower() != "all"] or [""]
    return core.rotate(name, [[k, l] for k in kws for l in locs], int(c.get("max_search_queries", 6)))


def linkedin_location(loc: str) -> str:
    """Full LinkedIn location for known Indian cities; any other value is passed through (LinkedIn resolves free text)."""
    return LOC_MAP.get(loc.strip().lower(), loc.strip())


def discover_linkedin(profile: dict, c: dict, budget: int) -> int:
    if platform_blocked("linkedin_search"):
        log("linkedin discovery on cooldown, skipped")
        return 0
    new, seen = 0, known_urls()
    for kw, raw_loc in search_pairs(profile, c, "linkedin_pairs"):
        loc = linkedin_location(raw_loc) if raw_loc else "Remote"
        if new >= budget or stopped():
            return new
        try:
            age = next((d for d in (1, 7, 14, 30) if int(c["job_age_days"]) <= d), 30)
            res = json.loads(_bun(["search", "-q", kw, "-l", loc, "--limit", str(c["per_query"]), "--jobage", str(age),
                                   "--format", "json"])).get("results", [])
        except Exception as e:  # noqa: BLE001
            if restriction_in(str(e)):
                block_platform("linkedin_search", 1, str(e))
                return new
            log(f"linkedin search failed ({kw}/{loc}): {str(e)[:100]}")
            continue
        for r in res:
            url = r["url"].split("?")[0]
            if new >= budget or url in seen or not title_ok(profile, r["title"]):
                continue
            try:
                time.sleep(random.uniform(3, 6))
                d = json.loads(_bun(["detail", r["id"], "--format", "json"]))
            except Exception as e:  # noqa: BLE001
                log(f"linkedin detail failed {r['id']}: {str(e)[:80]}")
                continue
            if save_job({"source": "linkedin", "ext_id": r["id"], "url": url, "title": r["title"], "company": r["company"],
                         "location": r.get("location", ""), "posted": r.get("date", ""),
                         "description": d.get("description", ""), "apply_type": "unknown"}):
                new += 1
                seen.add(url)
                if re.search(r"mid-senior|director|executive", d.get("seniority") or "", re.I):
                    set_job(_job_id(url), status="skip", reason=f"seniority: {d.get('seniority')}")
        time.sleep(4)
    return new


def discover_naukri(profile: dict, c: dict, budget: int) -> int:
    if platform_blocked("naukri_search"):
        log("naukri discovery on cooldown, skipped")
        return 0
    s = profile["search"]
    queries = [[kw, loc or "india"] for kw, loc in search_pairs(profile, c, "naukri_pairs")]
    nums = re.findall(r"\d+", str(profile["applicant"].get("experience_years", "0")))
    params = {"queries": queries, "per_query": c["per_query"], "max_total": budget, "skip_urls": sorted(known_urls()),
              "include": s["title_include"], "exclude": s["title_exclude"], "age": int(c["job_age_days"]),
              "experience": int(nums[0]) if nums else 0}
    try:
        r = subprocess.run([core.PY, str(core.ROOT / "hub" / "naukri_discover.py"), json.dumps(params)], capture_output=True,
                           text=True, timeout=1500, env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
                                                         "PLAYWRIGHT_BROWSERS_PATH": str(core.ROOT / ".browsers")})
    except subprocess.TimeoutExpired:
        log("naukri discovery timed out")
        return 0
    new = 0
    for line in r.stdout.splitlines():
        if line.startswith("BLOCKED"):
            block_platform("naukri_search", 6, line)
        elif line.startswith("JOB "):
            try:
                if save_job(json.loads(line[4:])):
                    new += 1
            except (json.JSONDecodeError, KeyError):
                continue
    log(f"naukri: {new} new jobs" + (f" ({r.stderr.strip().splitlines()[-1][:100]})" if r.stderr.strip() else ""))
    return new


def discover_jobspy(profile: dict, c: dict, budget: int) -> int:
    """No-login boards via JobSpy (LinkedIn guest API, Naukri, Indeed). Optional: autopilot.use_jobspy: true."""
    import jobspy_tool
    sites = c.get("jobspy_sites") or ["linkedin", "naukri", "indeed"]
    new = 0
    for kw, loc in search_pairs(profile, c, "jobspy_pairs"):
        if new >= budget or stopped():
            break
        r = jobspy_tool.multi_board_search({"query": kw, "location": loc or "India", "sites": sites, "save": True,
                                            "limit": c["per_query"], "hours_old": 24 * int(c["job_age_days"]),
                                            "fetch_description": True})
        if r.get("error"):
            log(f"jobspy {kw}/{loc}: {r['error'][:120]}")
            continue
        new += r.get("saved_to_hub", 0)
    log(f"jobspy: {new} new jobs")
    return new


# ---------------------------------------------------------------- scoring
def max_years(profile: dict, c: dict) -> int:
    nums = re.findall(r"\d+", str(profile["search"].get("experience_years_filter", "0-2")))
    return (int(nums[-1]) if nums else 2) + c["max_years_tolerance"]


def score_job(profile: dict, c: dict, rp: dict, job: dict) -> tuple:
    """-> (score 0-100, matched skills, reason). reason is set when the job is rejected."""
    title, jd = job["title"], job.get("description") or ""
    if not title_ok(profile, title):
        return 0, [], "title filter"
    if any(b.lower() in (job.get("company") or "").lower() for b in c["company_blocklist"]):
        return 0, [], "company blocklist"
    parsed = composer.parse_jd(title, job.get("company", ""), jd, profile)
    if parsed["min_years"] and parsed["min_years"] > max_years(profile, c):
        return 0, [], f"needs {parsed['min_years']}+ years"
    jd_skills = parsed["skills"] or composer.skills_in(title, profile)
    matched = [k for k in jd_skills if k in rp["skills"]]
    total = sum(composer.SKILL_WEIGHT.get(k, 1) for k in jd_skills) or 1
    overlap = sum(composer.SKILL_WEIGHT.get(k, 1) for k in matched) / total
    kws = [k for k in profile["search"].get("keywords") or [] if k]
    score = 60 * overlap + (20 if any(k.lower() in title.lower() for k in kws) else 12)
    if any(l.lower() in (job.get("location") or "").lower() for l in profile["search"]["locations"]):
        score += 10
    if len(jd) > 200:
        score += 10
    if len(matched) < 2:
        return min(score, 30), matched, "weak skill overlap"
    return round(score, 1), matched, ""


def score_new(profile: dict, c: dict) -> dict:
    rp = composer.resume_profile(profile)
    out = {"scored": 0, "skipped": 0}
    with core.db() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE status='new'")]
    for j in rows:
        sc, matched, reason = score_job(profile, c, rp, j)
        if reason or sc < c["min_score"]:
            set_job(j["id"], status="skip", score=sc, matched=json.dumps(matched), reason=reason or f"score {sc} < {c['min_score']}")
            out["skipped"] += 1
        else:
            set_job(j["id"], status="scored", score=sc, matched=json.dumps(matched), reason="")
            out["scored"] += 1
    return out


# ---------------------------------------------------------------- platform apply (existing bots) + restriction detection
def apply_platform(profile: dict, c: dict, platform: str, dry: bool) -> dict:
    if platform_blocked(platform):
        return {"platform": platform, "skipped": f"cooldown: {platform_blocked(platform)}"}
    import server  # lazy: server only imports core at module level, so there is no import cycle
    r = server.apply_jobs({"platform": platform, "dry_run": dry, "limit": c["apply_limits"].get(platform, 5)})
    if "error" in r:
        return {"platform": platform, "error": r["error"]}
    deadline = time.time() + 40 * 60
    st = {}
    while time.time() < deadline:
        st = core.run_status(r["run_id"], 400)
        if not st.get("running") and not st.get("queued"):  # a queued bot run starts when a slot frees up
            break
        time.sleep(10)
    text = "\n".join(st.get("log_tail", []))
    hit = restriction_in(text) or ("" if st.get("exit_code") in (0, None) else f"exit code {st.get('exit_code')}")
    if hit and not dry:
        block_platform(platform, c["platform_cooldown_hours"], hit)
    return {"platform": platform, "state": st.get("state"), "restriction": hit, "run_id": r["run_id"]}


# ---------------------------------------------------------------- company career-page application forms
def form_phase(profile: dict, c: dict, dry: bool, job_ids: list | None = None) -> dict:
    """Fill (and, when live actions are allowed, submit) the application form of good jobs on company career pages.
    Runs as its own process so a browser crash cannot take the autopilot down. Jobs whose form cannot be completed
    safely stay `scored` and are handled by the email phase."""
    if not c.get("career_apply", True):
        return {"skipped": "autopilot.career_apply is false"}
    import career_apply
    jobs, why = career_apply.eligible(profile, job_ids or None, None, dry)
    if not jobs:
        return {"skipped": why}
    try:
        r = subprocess.run([core.PY, str(core.ROOT / "hub" / "career_apply.py"),
                            json.dumps({"dry": dry, "limit": len(jobs), "job_ids": [j["id"] for j in jobs] if job_ids else None})],
                           capture_output=True, text=True, timeout=45 * 60, cwd=str(core.ROOT))
    except subprocess.TimeoutExpired:
        return {"error": "career form run timed out"}
    out = r.stdout
    i = out.rfind("SUMMARY ")
    try:
        return json.loads(out[i + 8:]) if i >= 0 else {"error": f"no summary (exit {r.returncode}): {(r.stderr or out)[-200:]}"}
    except json.JSONDecodeError:
        return {"error": f"unreadable summary (exit {r.returncode})"}


# ---------------------------------------------------------------- bounce / unsubscribe handling
UNSUB_RX = re.compile(r"\b(unsubscribe|remove me|do not contact|don'?t contact|stop (?:emailing|sending)|not interested)\b", re.I)


def classify_inbound(from_addr: str, subject: str, body: str, sent_to: set) -> tuple:
    """-> (action, address). action: bounce | unsubscribe | ''"""
    if re.search(r"mailer-daemon|postmaster", from_addr, re.I) or re.search(r"delivery status|undeliverable|returned mail|failure notice", subject, re.I):
        for e in contacts.extract_emails(body):
            if e in sent_to:
                return "bounce", e
        return "", ""
    f = (contacts.extract_emails(from_addr) or [""])[0]
    if f and f in sent_to and UNSUB_RX.search(body[:600]):
        return "unsubscribe", f
    return "", ""


def process_inbox(profile: dict) -> dict:
    """Read recent Gmail over IMAP (needs IMAP enabled + the app password); suppress bounced / opted-out addresses."""
    pw = core.read_secret("gmail_app_password")
    if not pw:
        return {"skipped": "no gmail app password"}
    with core.db() as c:
        sent_to = {r[0].lower() for r in c.execute("SELECT to_email FROM emails_sent WHERE result='sent'")}
    if not sent_to:
        return {"skipped": "nothing sent yet"}
    res = {"bounces": 0, "unsubscribes": 0}
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
        m.login(profile["hr_outreach"]["gmail_user"], pw.replace(" ", ""))
        m.select("INBOX", readonly=True)
        since = (datetime.now() - timedelta(days=10)).strftime("%d-%b-%Y")
        _, data = m.search(None, "SINCE", since)
        for num in data[0].split()[-200:]:
            _, msg = m.fetch(num, "(RFC822)")
            em = emaillib.message_from_bytes(msg[0][1])
            body = ""
            for part in em.walk():
                if part.get_content_type() == "text/plain":
                    body += (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
                elif part.get_content_type() == "message/delivery-status":
                    body += str(part.get_payload())
            act, addr = classify_inbound(em.get("From", ""), em.get("Subject", ""), body, sent_to)
            if act == "bounce":
                contacts.suppress(addr, "bounced")
                res["bounces"] += 1
            elif act == "unsubscribe":
                contacts.suppress(addr, "unsubscribe reply")
                contacts.suppress(contacts.domain_of(addr), "unsubscribe reply")
                res["unsubscribes"] += 1
        m.logout()
    except Exception as e:  # noqa: BLE001 - IMAP disabled etc.; never block the run
        res["error"] = f"{type(e).__name__}: {str(e)[:100]}"
    return res


# ---------------------------------------------------------------- email phase
def _window_hours(c: dict):
    w = c.get("send_window")
    if w in (None, "", "always", "any", [], False) or not isinstance(w, (list, tuple)) or len(w) != 2:
        return None
    lo, hi = int(w[0]) % 24, int(w[1])
    return None if (lo == 0 and hi >= 24) or lo == hi % 24 else (lo, hi % 24 if hi != 24 else 24)


def _window_days(c: dict):
    d = c.get("send_days")
    if not d:
        return None
    out = set()
    for x in d:
        s = str(x).strip().lower()[:3]
        out.add(int(s) % 7 if s.isdigit() else DAY_NAMES.index(s) if s in DAY_NAMES else -1)
    out.discard(-1)
    return out if out and len(out) < 7 else None


def in_send_window(c: dict, now: datetime | None = None) -> bool:
    """Default: always. Optional autopilot.send_window [start_hour, end_hour] (end exclusive, may wrap past midnight,
    e.g. [22, 6]) and autopilot.send_days (e.g. [mon, tue, wed, thu, fri] or 0-6, Monday = 0)."""
    now = now or datetime.now()
    hours, days = _window_hours(c), _window_days(c)
    if days is not None and now.weekday() not in days:
        return False
    if hours is None:
        return True
    lo, hi = hours
    return lo <= now.hour < hi if lo < hi else (now.hour >= lo or now.hour < hi)


def send_window_text(c: dict) -> str:
    hours, days = _window_hours(c), _window_days(c)
    h = "any time" if hours is None else f"{hours[0]:02d}:00-{hours[1]:02d}:00"
    d = "every day" if days is None else ",".join(DAY_NAMES[i] for i in sorted(days))
    return f"{h}, {d} (autopilot.send_window / send_days in the profile)"


def domain_recently_emailed(domain: str, days: int) -> bool:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with core.db() as c:
        return c.execute("SELECT 1 FROM emails_sent WHERE domain=? AND result='sent' AND sent_at>=?", (domain, cutoff)).fetchone() is not None


def pick_contact(profile: dict, c: dict, job: dict, restricted: bool) -> dict:
    """Best recipient for a job or {} - provenance is limited to JD text, the company site, or a verified role inbox."""
    minrank = core.CONF_RANK[profile["hr_outreach"]["min_confidence"]]
    needs_lookup = restricted or job["apply_type"] in ("external", "login", "unknown")
    jd_has_email = bool(json.loads(job.get("jd_emails") or "[]"))
    if not jd_has_email and not needs_lookup:
        return {}
    found = contacts.find_contacts(job["company"], job.get("description", ""), job.get("company_site", ""),
                                   profile["hr_outreach"]["gmail_user"], probe=needs_lookup)
    for ct in found:
        if core.CONF_RANK[ct["confidence"]] < minrank:
            continue
        if contacts.is_personal(ct["email"]) and ct["source"] != "jd":
            continue
        return ct
    return {}


def email_phase(profile: dict, c: dict, dry: bool, job_ids: list | None = None) -> dict:
    h = profile["hr_outreach"]
    out = {"emailed": 0, "dry_drafts": [], "no_contact": 0, "deferred": 0, "skipped_dupe": 0, "lookups": 0}
    live_ok, why = core.real_actions_allowed(profile)
    if not dry and not live_ok:
        return {"error": "live sending not allowed: " + why}
    if not dry:
        out["inbox"] = process_inbox(profile)
    remaining = h["max_emails_per_day"] - core.sent_today()
    per_run = max(c["max_emails_per_run"], len(job_ids)) if job_ids else c["max_emails_per_run"]  # picked jobs: daily cap still applies
    budget = min(per_run, remaining if not dry else per_run)
    with core.db() as db:
        if job_ids:
            q = ",".join("?" * len(job_ids))
            jobs = [dict(r) for r in db.execute(
                f"SELECT * FROM jobs WHERE id IN ({q}) AND status IN ('scored','queued','no_contact','error','skip') ORDER BY score DESC", job_ids)]
        else:
            jobs = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE status IN ('scored','queued') ORDER BY score DESC")]
    for j in jobs:
        if out["emailed"] + len(out["dry_drafts"]) >= budget or stopped():
            break
        restricted = bool(platform_blocked(j["source"]))
        if out["lookups"] >= c["max_lookups"] and not json.loads(j.get("jd_emails") or "[]"):
            out["deferred"] += 1  # contact lookup (web search + SMTP probes) is slow; continue next run
            continue
        out["lookups"] += 1
        ct = pick_contact(profile, c, j, restricted)
        if not ct:
            if json.loads(j.get("jd_emails") or "[]") or restricted or j["apply_type"] != "easy":
                set_job(j["id"], status="no_contact", reason="no verified contact")
                out["no_contact"] += 1
            continue
        dom = contacts.domain_of(ct["email"])
        with core.db() as cdb:
            cdb.execute(
                "INSERT OR IGNORE INTO hr_contacts(email,name,role,company,domain,city,confidence,note,kind,found_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (ct["email"], j.get("recruiter", ""), j.get("title", ""), j.get("company", ""), dom, j.get("location", ""),
                 ct["confidence"], f"autopilot lead for {j.get('title')} @ {j.get('company')}", ct.get("source", "site"), core.now_iso()))
        if domain_recently_emailed(dom, h["one_email_per_company_days"]):

            set_job(j["id"], status="skip", reason=f"already emailed {dom} within {h['one_email_per_company_days']}d")
            out["skipped_dupe"] += 1
            continue
        if not dry and not in_send_window(c):
            set_job(j["id"], status="queued", reason="outside send window", contact=ct["email"])
            out["deferred"] += 1
            continue
        mail = composer.compose(profile, j, source={"linkedin": "LinkedIn", "naukri": "Naukri"}.get(j["source"], j["source"]), recruiter=j.get("recruiter", ""), cold=ct["source"] != "jd")
        body = composer.polish(mail["body"], model=c["polish_model"]) if c["polish_model"] else mail["body"]
        if dry:
            out["dry_drafts"].append({"job": f"{j['title']} @ {j['company']}", "to": ct["email"], "confidence": ct["confidence"],
                                      "source": ct["source"], "subject": mail["subject"],
                                      "attach": profile["applicant"]["resume_path"], "body": body})
            set_job(j["id"], contact=ct["email"], reason="DRY-RUN draft")
            continue
        try:
            core.send_via_gmail(profile, ct["email"], mail["subject"], body, attach_resume=True)
            res = "sent"
        except Exception as e:  # noqa: BLE001
            res = f"error: {type(e).__name__}: {str(e)[:100]}"
        with core.db() as db:
            db.execute("INSERT INTO emails_sent(to_email,domain,subject,sent_at,result) VALUES(?,?,?,?,?)",
                       (ct["email"], dom, mail["subject"], core.now_iso(), res))
        set_job(j["id"], status="emailed" if res == "sent" else "error", contact=ct["email"], reason=res)
        log(f"{res}: {j['title']} @ {j['company']} -> {ct['email']} ({ct['source']})")
        if res == "sent":
            out["emailed"] += 1
            time.sleep(random.uniform(*h["send_delay_sec"]))
        elif "Authentication" in res or "password" in res.lower():
            out["error"] = res
            break
    return out


def sync_existing_job_contacts() -> int:
    added = 0
    with core.db() as c:
        rows = c.execute("SELECT id, source, title, company, location, recruiter, jd_emails, contact FROM jobs").fetchall()
    for j in rows:
        jd_emails = json.loads(j["jd_emails"] or "[]")
        for e in jd_emails:
            dom = contacts.domain_of(e)
            if contacts.mx_hosts(dom):
                v = contacts.verify_email(e)
                with core.db() as c:
                    cur = c.execute(
                        "INSERT OR IGNORE INTO hr_contacts(email,name,role,company,domain,city,confidence,note,kind,found_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (e, j["recruiter"] or "", j["title"] or "", j["company"] or "", dom, j["location"] or "",
                         "high" if v in ("valid", "catch_all") else "medium",
                         f"job listing: {j['source']} - {j['title']}", "job", core.now_iso()))
                    if cur.rowcount:
                        added += 1
        if j["contact"]:
            e = j["contact"]
            dom = contacts.domain_of(e)
            if contacts.mx_hosts(dom):
                v = contacts.verify_email(e)
                with core.db() as c:
                    cur = c.execute(
                        "INSERT OR IGNORE INTO hr_contacts(email,name,role,company,domain,city,confidence,note,kind,found_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (e, j["recruiter"] or "", j["title"] or "", j["company"] or "", dom, j["location"] or "",
                         "high" if v in ("valid", "catch_all") else "medium",
                         f"job contact: {j['source']} - {j['title']}", "job", core.now_iso()))
                    if cur.rowcount:
                        added += 1
    return added


# ---------------------------------------------------------------- entry point
def run(opts: dict) -> dict:
    p = core.load_profile()
    c = cfg(p)
    dry = opts.get("dry_run", p["safety"]["dry_run_default"])
    platforms = opts.get("platforms") or ["linkedin", "naukri"]
    summary = {"dry_run": dry, "platforms": platforms}
    if stopped():
        return {"stopped": f"kill-switch file exists: {STOP_FILE}"}
    if opts.get("discover", True):
        budget = opts.get("max_discover", c["max_discover"])
        found = {}
        if "linkedin" in platforms:
            found["linkedin"] = discover_linkedin(p, c, budget)
        if "naukri" in platforms and not stopped():
            found["naukri"] = discover_naukri(p, c, budget)
        if c.get("use_jobspy") and not stopped():
            found["jobspy"] = discover_jobspy(p, c, budget)
        summary["discovered"] = found
    if opts.get("posts", c.get("scan_posts")) and not stopped() and (core.ROOT / ".profiles" / "linkedin").exists():
        import asyncio
        import post_scanner  # lazy: needs Playwright
        try:
            summary["posts"] = asyncio.run(post_scanner.scan(post_scanner.queries_for_run(p)))
        except Exception as e:  # noqa: BLE001 - a post-scan failure must not stop the rest of the run
            summary["posts"] = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
    if opts.get("career", True) and not stopped():
        import career_scan  # lazy: career_scan imports this module
        if career_scan.api_key():
            summary["career"] = career_scan.run({"limit": opts.get("career_companies", 5)})
    summary["scoring"] = score_new(p, c)
    summary["synced_contacts"] = sync_existing_job_contacts()
    if opts.get("apply", True) and not stopped():
        summary["platform_apply"] = [apply_platform(p, c, pl, dry) for pl in platforms]
    job_ids = [int(x) for x in opts.get("job_ids") or []]
    if job_ids:  # the user picked these jobs: give them another chance even if an earlier run skipped them
        with core.db() as db:
            q = ",".join("?" * len(job_ids))
            db.execute(f"UPDATE jobs SET status='scored', reason='picked by user' "
                       f"WHERE id IN ({q}) AND status IN ('skip','no_contact','error','new')", job_ids)
    if opts.get("form", True) and not stopped():
        summary["career_apply"] = form_phase(p, c, dry, job_ids or None)
    if opts.get("email", True) and not stopped():
        summary["email"] = email_phase(p, c, dry, job_ids or None)
    return summary



if __name__ == "__main__":
    opts = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    res = run(opts)
    print("SUMMARY " + json.dumps(res, indent=2, default=str), flush=True)
