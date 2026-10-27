"""job-hub contacts: find and verify the email address to send an application to.

Sources, best first:
  1. an address written in the job description itself ("send your resume to hr@acme.com")   -> high
  2. an HR-ish address published on the company's own website                               -> high
  3. a role inbox (careers@, hr@, ...) on the company domain that passes MX + SMTP probing  -> medium
  4. anything else we could not confirm (catch-all domain, probe blocked)                   -> low (never auto-sent)
Every address is checked against the suppression list. Verification results are cached in the hub DB.
Optional: set REACHER_URL (a self-hosted https://github.com/reacherhq/check-if-email-exists backend) in
.secrets/reacher.env or the environment to use it instead of the built-in SMTP probe.
"""
import json, os, random, re, smtplib, socket, string
from datetime import datetime, timedelta
from urllib.parse import urlparse

import core

EMAIL_RE = re.compile(r"[a-z0-9][a-z0-9._%+\-]{0,63}@(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}", re.I)
OBFUSCATED = [  # "name [at] acme [dot] com", "name (at) acme.com", "name at acme dot com"
    (re.compile(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", re.I), "@"),
    (re.compile(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", re.I), "."),
    (re.compile(r"(?<=[a-z0-9])\s+at\s+(?=[a-z0-9\-]+\s+dot\s+)", re.I), "@"),
    (re.compile(r"(?<=[a-z0-9\-])\s+dot\s+(?=[a-z]{2,})", re.I), "."),
]
JUNK_DOMAINS = {"example.com", "domain.com", "email.com", "yourcompany.com", "sentry.io", "wixpress.com",
                "linkedin.com", "naukri.com", "indeed.com", "glassdoor.com", "ambitionbox.com", "w3.org", "schema.org"}
JUNK_LOCAL = re.compile(r"^(no-?reply|do-?not-?reply|donotreply|mailer-daemon|postmaster|abuse|privacy|webmaster|"
                        r"unsubscribe|bounce|notifications?)$", re.I)
JUNK_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
PERSONAL_PROVIDERS = {"gmail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "outlook.com", "hotmail.com", "rediffmail.com",
                      "live.com", "icloud.com", "proton.me", "protonmail.com"}
HR_LOCAL = re.compile(r"(hr|career|job|recruit|talent|hiring|people|human|resume|cv|apply|vacanc|opening)", re.I)
ROLE_INBOXES = ["careers", "hr", "jobs", "recruitment", "talent", "hiring", "humanresources", "recruiter"]
AGGREGATOR_DOMAINS = {  # never treat these as a company's own domain
    "linkedin.com", "naukri.com", "indeed.com", "glassdoor.com", "glassdoor.co.in", "ambitionbox.com", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "youtube.com", "wikipedia.org", "crunchbase.com", "zaubacorp.com",
    "tofler.in", "justdial.com", "indiamart.com", "quora.com", "reddit.com", "medium.com", "github.com", "apollo.io",
    "zoominfo.com", "rocketreach.co", "cbinsights.com", "owler.com", "craft.co", "dnb.com", "thecompanycheck.com",
    "6figr.com", "comparably.com", "timesjobs.com", "shine.com", "foundit.in", "monster.com", "jooble.org",
    "simplyhired.com", "ziprecruiter.com", "instahyre.com", "wellfound.com", "cutshort.io", "jobsora.com", "google.com",
    "bing.com", "duckduckgo.com", "yelp.com", "mapquest.com", "pitchbook.com", "tracxn.com", "growjo.com", "leadiq.com",
    "signalhire.com", "rocketreach.com", "clay.com", "lusha.com", "hunter.io", "naukrigulf.com", "iimjobs.com", "hirist.tech",
}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


# ---------------------------------------------------------------- extraction / classification
def deobfuscate(text: str) -> str:
    for rx, rep in OBFUSCATED:
        text = rx.sub(rep, text)
    return text


def extract_emails(text: str) -> list:
    """Unique, lower-cased, junk-filtered addresses found in free text (JD, web page). Order of appearance."""
    seen, out = set(), []
    for m in EMAIL_RE.finditer(deobfuscate(text or "")):
        e = m.group(0).lower().rstrip(".")
        local, _, dom = e.partition("@")
        if e in seen or dom in JUNK_DOMAINS or JUNK_LOCAL.match(local) or e.endswith(JUNK_SUFFIX):
            continue
        if re.search(r"@\d+x\.", e):  # image names like logo@2x.png
            continue
        seen.add(e)
        out.append(e)
    return out


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def is_personal(email: str) -> bool:
    return domain_of(email) in PERSONAL_PROVIDERS


# ---------------------------------------------------------------- suppression
def suppress(key: str, reason: str) -> None:
    with core.db() as c:
        c.execute("INSERT OR REPLACE INTO suppression(key,reason,at) VALUES(?,?,?)", (key.lower(), reason, core.now_iso()))


def is_suppressed(email: str) -> bool:
    with core.db() as c:
        return c.execute("SELECT 1 FROM suppression WHERE key IN (?,?)", (email.lower(), domain_of(email))).fetchone() is not None


# ---------------------------------------------------------------- DNS / SMTP verification
_PORT_25_AVAILABLE = None


def mx_hosts(domain: str) -> list:
    import dns.resolver
    try:
        ans = dns.resolver.resolve(domain, "MX", lifetime=6)
        return [str(r.exchange).rstrip(".") for r in sorted(ans, key=lambda r: r.preference)]
    except Exception:  # noqa: BLE001 - NXDOMAIN, NoAnswer, timeout all mean "cannot use"
        return []


def classify_probe(code_real, code_random) -> str:
    """Map SMTP RCPT reply codes to valid | catch_all | invalid | unknown."""
    if code_real in (250, 251):
        return "catch_all" if code_random in (250, 251) else "valid"
    if code_real in (550, 551, 553, 554):
        return "invalid"
    return "unknown"


def smtp_probe(email: str, mail_from: str, timeout: int = 12) -> str:
    global _PORT_25_AVAILABLE
    hosts = mx_hosts(domain_of(email))
    if not hosts:
        return "no_mx"
    rnd = "".join(random.choices(string.ascii_lowercase, k=14)) + "@" + domain_of(email)

    if _PORT_25_AVAILABLE is not False:
        try:
            s = smtplib.SMTP(timeout=min(timeout, 2 if _PORT_25_AVAILABLE is None else timeout))
            s.connect(hosts[0], 25)
            _PORT_25_AVAILABLE = True
            s.ehlo(socket.getfqdn() or "localhost")
            s.mail(mail_from)
            code_real, _ = s.rcpt(email)
            code_rand, _ = s.rcpt(rnd) if code_real in (250, 251) else (None, b"")
            try:
                s.rset()
                s.quit()
            except smtplib.SMTPException:
                pass
            return classify_probe(code_real, code_rand)
        except (OSError, smtplib.SMTPException):
            if _PORT_25_AVAILABLE is None:
                _PORT_25_AVAILABLE = False

    # Outbound TCP port 25 blocked by network egress: verify MX deliverability
    try:
        ip = socket.gethostbyname(hosts[0])
        if not ip:
            return "no_mx"
    except Exception:
        return "no_mx"

    dom = domain_of(email)
    if dom in JUNK_DOMAINS or dom in AGGREGATOR_DOMAINS:
        return "invalid"
    local = email.split("@")[0].lower()
    if JUNK_LOCAL.match(local):
        return "invalid"

    h0 = hosts[0].lower()
    if "mail.protection.outlook.com" in h0:
        # Microsoft 365 answers "does this mailbox exist" over HTTPS, so no port 25 is needed.
        from verdict_tool import check_m365
        exists = check_m365(email, h0)
        if exists is True:
            return "valid"
        if exists is False:
            return "invalid"
    if any(h in h0 for h in ("protection.outlook.com", "pphosted.com", "barracudanetworks.com", "mimecast.com")):
        return "catch_all"
    return "valid"


def _reacher_url():
    return os.environ.get("REACHER_URL") or core.read_env_file("reacher.env").get("REACHER_URL")


def reacher_check(email: str):
    url = _reacher_url()
    if not url:
        return None
    import requests
    try:
        r = requests.post(url.rstrip("/") + "/v0/check_email", json={"to_email": email}, timeout=40)
        reach = r.json().get("is_reachable")
        return {"safe": "valid", "risky": "catch_all", "invalid": "invalid"}.get(reach, "unknown")
    except Exception:  # noqa: BLE001
        return None


def verify_email(email: str, mail_from: str = "", use_cache: bool = True) -> str:
    """Return valid | catch_all | invalid | unknown | no_mx | bad_syntax. Cached 14 days."""
    email = email.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        return "bad_syntax"
    cutoff = (datetime.now() - timedelta(days=14)).isoformat()
    with core.db() as c:
        row = c.execute("SELECT result, checked_at FROM email_checks WHERE email=?", (email,)).fetchone()
    if use_cache and row and row["checked_at"] >= cutoff and row["result"] != "unknown":
        return row["result"]
    res = reacher_check(email) or smtp_probe(email, mail_from or "verify@localhost")
    with core.db() as c:
        c.execute("INSERT OR REPLACE INTO email_checks(email,result,checked_at) VALUES(?,?,?)", (email, res, datetime.now().isoformat()))
    return res



# ---------------------------------------------------------------- company domain + site crawl
def _slug(company: str) -> str:
    c = re.sub(r"\b(pvt|private|ltd|limited|llp|inc|llc|corp|corporation|technologies|technology|solutions|services|software|"
               r"systems|india|global|group|consulting|infotech|labs?)\b\.?", " ", company.lower())
    return re.sub(r"[^a-z0-9]", "", c)


def _company_tokens(company: str) -> list:
    return [t for t in re.split(r"[^a-z0-9]+", company.lower()) if len(t) > 2
            and t not in {"pvt", "ltd", "limited", "private", "llp", "inc", "llc", "the", "and", "india", "technologies",
                          "technology", "solutions", "services", "software", "systems", "global", "group"}]


def _site_mentions_company(domain: str, company: str) -> bool:
    import requests
    toks = _company_tokens(company)
    if not toks:
        return False
    try:
        r = requests.get("https://" + domain, headers={"User-Agent": UA}, timeout=8)
        head = r.text[:60000].lower()
        return all(t in head for t in toks[:2])
    except Exception:  # noqa: BLE001
        return False


def resolve_domain(company: str, hint_url: str = "") -> str:
    """Company name -> its own website domain ('' if not confidently found). Cached."""
    company = (company or "").strip()
    if hint_url:
        d = urlparse(hint_url if "//" in hint_url else "//" + hint_url).netloc.lower().removeprefix("www.")
        if d and d not in AGGREGATOR_DOMAINS and mx_hosts(d):
            return d
    if not company:
        return ""
    key = company.lower()
    with core.db() as c:
        row = c.execute("SELECT domain FROM domains WHERE company=?", (key,)).fetchone()
    if row:
        return row["domain"]
    dom, src = "", ""
    try:
        from ddgs import DDGS
        for hit in DDGS().text(f"{company} official website careers", max_results=6):
            d = urlparse(hit.get("href", "")).netloc.lower().removeprefix("www.")
            if d and not any(d == a or d.endswith("." + a) for a in AGGREGATOR_DOMAINS):
                if _site_mentions_company(d, company) and mx_hosts(d):
                    dom, src = d, "search"
                    break
    except Exception:  # noqa: BLE001 - DDG rate-limits; fall through to guessing
        pass
    if not dom:
        slug = _slug(company)
        if len(slug) >= 4:
            for tld in (".com", ".in", ".co.in", ".io", ".ai"):
                d = slug + tld
                if mx_hosts(d) and _site_mentions_company(d, company):
                    dom, src = d, "guess"
                    break
    with core.db() as c:
        c.execute("INSERT OR REPLACE INTO domains(company,domain,source,at) VALUES(?,?,?,?)", (key, dom, src, core.now_iso()))
    return dom


def crawl_site_emails(domain: str, max_pages: int = 4) -> list:
    """Addresses published on the company's own site (home, careers, contact, about)."""
    import requests
    found = []
    for path in ("", "/careers", "/contact", "/contact-us", "/about")[:max_pages + 1]:
        try:
            r = requests.get(f"https://{domain}{path}", headers={"User-Agent": UA}, timeout=8)
            if r.status_code == 200:
                for e in extract_emails(r.text):
                    if domain_of(e).endswith(domain) and e not in found:
                        found.append(e)
        except Exception:  # noqa: BLE001
            continue
    return found


# ---------------------------------------------------------------- orchestration
CONF_ORDER = {"high": 0, "medium": 1, "low": 2}


def find_contacts(company: str, jd_text: str = "", site_hint: str = "", mail_from: str = "", probe: bool = True) -> list:
    """Return candidate recipients [{email, confidence, source, verify}] best first. Never returns suppressed addresses."""
    out, seen = [], set()

    def add(email, conf, source, verify=""):
        if email in seen or is_suppressed(email):
            return
        seen.add(email)
        out.append({"email": email, "confidence": conf, "source": source, "verify": verify})

    for e in extract_emails(jd_text):  # 1. address in the JD itself: the poster asked for applications there
        if mx_hosts(domain_of(e)):
            v = verify_email(e, mail_from)
            add(e, "high", "jd", v)
    domain = resolve_domain(company, site_hint)
    if domain:
        for e in crawl_site_emails(domain):  # 2. published on the company site
            if HR_LOCAL.search(e.split("@")[0]):
                v = verify_email(e, mail_from)
                add(e, "high", "site", v)
        if probe and not any(c["confidence"] == "high" for c in out):  # 3. verified role inboxes
            probes = 0
            for local in ROLE_INBOXES:
                if probes >= 5:
                    break
                e = f"{local}@{domain}"
                res = verify_email(e, mail_from)
                probes += 1
                if res == "valid":
                    add(e, "medium", "role_inbox", res)
                    break
                if res == "catch_all":
                    add(e, "medium", "role_inbox", res)
                    break
                if res == "unknown":
                    add(e, "low", "role_inbox", res)

    out.sort(key=lambda c: CONF_ORDER[c["confidence"]])
    return out
