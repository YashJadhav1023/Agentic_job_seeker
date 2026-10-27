"""Lead discovery pipeline: companies -> founders -> emails, using only free sources."""
import random
import re
import smtplib
import socket
import string
import time
from urllib.parse import urljoin, urlparse

import dns.resolver
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

# Domains that are directories/social/media, never a company's own site.
SKIP_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "clutch.co", "yelp.com", "upwork.com", "fiverr.com", "glassdoor.com", "indeed.com",
    "crunchbase.com", "designrush.com", "goodfirms.co", "themanifest.com", "sortlist.com",
    "wikipedia.org", "medium.com", "reddit.com", "quora.com", "forbes.com", "bbb.org",
    "yellowpages.com", "g2.com", "capterra.com", "zoominfo.com", "apollo.io", "rocketreach.co",
    "tiktok.com", "pinterest.com", "google.com", "bing.com", "duckduckgo.com", "amazon.com",
    "angel.co", "wellfound.com", "ycombinator.com", "producthunt.com", "tracxn.com",
    "expertise.com", "mapquest.com", "trustpilot.com", "semrush.com", "hubspot.com",
    "agencyspotter.com", "digitalagencynetwork.com", "topdevelopers.co", "techbehemoths.com",
    "builtin.com", "f6s.com", "pitchbook.com", "cbinsights.com", "owler.com", "dnb.com",
    "manta.com", "wix.com", "wordpress.com", "github.com", "gitlab.com", "apple.com",
}

ROLE_PATTERNS = {
    "founder": r"co-?\s?founder|founder|founding partner",
    "owner": r"\bowner\b|proprietor",
    "ceo": r"\bceo\b|chief executive|president|managing director|\bmd\b",
    "cto": r"\bcto\b|chief technology|vp,? engineering|head of engineering",
    "cmo": r"\bcmo\b|chief marketing|head of marketing|head of growth|vp,? marketing",
    "hr": r"\bhr\b|human resources|talent acquisition|talent partner|talent lead|recruit(?:er|ing|ment)"
          r"|people operations|people ops|head of people|chief people|people partner|hiring manager",
}
ROLE_SEARCH_WORDS = {"founder": "founder", "owner": "owner", "ceo": "CEO", "cto": "CTO", "cmo": "CMO",
                     "hr": 'recruiter OR "talent acquisition" OR HR'}
HR_PREFIXES = ("careers", "career", "jobs", "job", "hr", "recruit", "talent", "hiring", "people",
               "joinus", "join", "apply", "work", "resume", "cv")

GENERIC_PREFIXES = ("info", "hello", "contact", "sales", "support", "admin", "office", "team",
                    "hi", "enquiries", "inquiries", "careers", "jobs", "hr", "billing",
                    "marketing", "press", "media", "help", "service", "noreply", "no-reply")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_EMAIL_TAIL = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
SIZE_RE = re.compile(r"(\d{1,2},?\d{0,3})\s*(?:-|–|to)\s*(\d{1,2},?\d{0,3})\s+employees"
                     r"|(\d{1,2},?\d{3})\+\s*employees", re.I)
NAME_TOKEN = r"[A-Z][a-zA-Z'\-]+"

SIZE_BUCKETS = {
    "1-10": (1, 10), "11-50": (11, 50), "51-200": (51, 200), "201-500": (201, 500),
    "501-1000": (501, 1000), "1001-5000": (1001, 5000), "5001+": (5001, 10**7),
}


def root_domain(url):
    host = urlparse(url if "//" in url else "//" + url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def is_skipped(domain):
    return any(domain == d or domain.endswith("." + d) for d in SKIP_DOMAINS)


class Searcher:
    """DuckDuckGo wrapper with polite pacing and retry on rate limits."""

    def __init__(self, region="wt-wt", delay=2.5, log=print):
        self.region, self.delay, self.log = region, delay, log
        self._last = 0.0

    def text(self, query, max_results=20):
        for attempt in range(3):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                return DDGS().text(query, region=self.region, max_results=max_results) or []
            except Exception as e:  # ddgs raises on rate limit / no results
                if "No results" in str(e):
                    return []
                self.log(f"search hiccup ({e.__class__.__name__}), retrying…")
                time.sleep(5 * (attempt + 1))
        return []


# ---------- company size ----------

def parse_size(text):
    m = SIZE_RE.search(text or "")
    if not m:
        return None
    if m.group(3):
        lo = int(m.group(3).replace(",", ""))
        return (lo, 10**7)
    return (int(m.group(1).replace(",", "")), int(m.group(2).replace(",", "")))


def size_label(rng):
    if not rng:
        return ""
    lo, hi = rng
    return f"{lo:,}+" if hi >= 10**7 else f"{lo:,}-{hi:,}"


def size_matches(rng, wanted):
    """wanted: list of bucket keys. Overlap check."""
    if not wanted:
        return True
    if not rng:
        return None  # unknown
    lo, hi = rng
    for key in wanted:
        blo, bhi = SIZE_BUCKETS[key]
        if lo <= bhi and hi >= blo:
            return True
    return False


# ---------- discovery ----------

def location_str(country, state, city):
    return " ".join(p for p in (city, state, country) if p)


def discover_companies(s, industry, country, state, city, size_keys, limit, log):
    """Returns dict domain -> company info."""
    loc = location_str(country, state, city)
    companies = {}

    def add(domain, name, size=None, source="", snippet=""):
        if not domain or is_skipped(domain) or domain in companies:
            if domain in companies and size and not companies[domain]["size"]:
                companies[domain]["size"] = size
            return
        companies[domain] = {"domain": domain, "name": clean_company_name(name, domain),
                             "size": size, "source": source, "snippet": snippet[:300]}

    # Channel 1: LinkedIn company snippets (carry headcount + location).
    size_terms = [f'"{k} employees"' for k in size_keys] or [""]
    li_hits = []
    for st in size_terms:
        q = f'site:linkedin.com/company "{industry}" "{state or city or country}" {st}'.strip()
        log(f"searching: {q}")
        li_hits += s.text(q, max_results=30)
    for h in li_hits:
        if len(companies) >= limit * 2:
            break
        blob = f"{h.get('title', '')} {h.get('body', '')}"
        name = re.split(r"\s[|\-–]\s", h.get("title", ""))[0].strip()
        if not name or "/company/" not in h.get("href", ""):
            continue
        rng = parse_size(blob)
        domain = find_domain_for(s, name, loc, log)
        add(domain, name, rng, "linkedin", h.get("body", ""))

    # Channel 2: open web search for company sites.
    queries = [f"{industry} {loc}", f"top {industry} in {loc}", f"{industry} company {loc} founder",
               f"{industry} {loc} about us our team"]
    for q in queries:
        if len(companies) >= limit * 2:
            break
        log(f"searching: {q}")
        for h in s.text(q, max_results=25):
            add(root_domain(h.get("href", "")), h.get("title", ""), parse_size(h.get("body", "")),
                "web", h.get("body", ""))
    return companies


def find_domain_for(s, name, loc, log):
    for h in s.text(f'"{name}" {loc} official website', max_results=8):
        d = root_domain(h.get("href", ""))
        if d and not is_skipped(d):
            # sanity: some token of the company name should appear in domain or title
            toks = [t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", name)]
            hay = (d + " " + h.get("title", "")).lower()
            if not toks or any(t in hay for t in toks):
                return d
    return None


def clean_company_name(title, domain):
    t = re.split(r"\s[|\-–:·]\s|\s\|", title or "")[0].strip()
    if not t or len(t) > 60 or re.search(r"\b(top|best|\d+)\b", t, re.I):
        t = domain.split(".")[0].replace("-", " ").title()
    return t


# ---------- website crawl ----------

CRAWL_HINTS = ("about", "team", "our-team", "people", "leadership", "founder", "contact",
               "who-we-are", "company", "meet", "careers", "jobs", "join")
FALLBACK_PATHS = ["/about", "/about-us", "/team", "/our-team", "/contact", "/contact-us", "/careers"]


def fetch(url, timeout=10):
    try:
        r = HTTP.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            return r.url, r.text
    except requests.RequestException:
        pass
    return None, None


def crawl_site(domain, max_pages=8):
    """Return (pages_text, emails, final_domain)."""
    base_url, html = fetch(f"https://{domain}") or (None, None)
    if not html:
        base_url, html = fetch(f"http://{domain}")
    if not html:
        return [], set(), domain, None
    final_domain = root_domain(base_url)
    pages, emails, seen = [], set(), {base_url}
    queue = []

    def absorb(url, html_):
        soup = BeautifulSoup(html_, "html.parser")
        for a in soup.select('a[href^="mailto:"]'):
            emails.add(a["href"][7:].split("?")[0].strip().lower())
        for tag in soup(["script", "style", "noscript"]):
            if tag.name == "script" and tag.get("type") == "application/ld+json":
                pages.append(("ldjson", tag.get_text(" ")))
            tag.decompose()
        text = soup.get_text("\n")
        pages.append((url, text))
        for e in EMAIL_RE.findall(text):
            emails.add(e.lower())
        return soup

    site_name = site_name_from(html)
    soup = absorb(base_url, html)
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if root_domain(href) == final_domain and any(k in href.lower() for k in CRAWL_HINTS):
            queue.append(href.split("#")[0])
    queue += [urljoin(base_url, p) for p in FALLBACK_PATHS]
    for url in queue:
        if len(seen) >= max_pages:
            break
        if url in seen:
            continue
        seen.add(url)
        u, h = fetch(url, timeout=8)
        if h:
            absorb(u, h)
    emails = {e.strip(".") for e in emails
              if not e.endswith(BAD_EMAIL_TAIL) and "example" not in e and len(e) < 80}
    return pages, emails, final_domain, site_name


def site_name_from(html):
    soup = BeautifulSoup(html[:200000], "html.parser")
    for sel in ('meta[property="og:site_name"]', 'meta[name="application-name"]'):
        m = soup.select_one(sel)
        if m and m.get("content", "").strip():
            return m["content"].strip()[:60]
    return None


def extract_people(pages, role_keys):
    """Find 'Name — Founder' style mentions in site text."""
    role_re = "|".join(ROLE_PATTERNS[k] for k in role_keys)
    people = {}
    name = rf"({NAME_TOKEN}(?: {NAME_TOKEN}){{1,2}})"
    pats = [
        re.compile(rf"{name}\s*[\n,|\-–—:(]+\s*((?:[\w&,/ ]{{0,25}})?(?:{role_re})[\w&,/ ]{{0,25}})", re.I),
        re.compile(rf"((?:{role_re})[\w&,/ ]{{0,20}})\s*[\n,|\-–—:]+\s*{name}", re.I),
    ]
    for src, text in pages:
        if src == "ldjson":
            for m in re.finditer(r'"founders?"\s*:\s*\[?\s*\{[^}]*"name"\s*:\s*"([^"]+)"', text):
                people.setdefault(m.group(1).strip(), "Founder")
            continue
        text = re.sub(r"[ \t]+", " ", text)
        for i, p in enumerate(pats):
            for m in p.finditer(text):
                n, role = (m.group(1), m.group(2)) if i == 0 else (m.group(2), m.group(1))
                n = n.strip()
                if valid_person_name(n):
                    people.setdefault(n, " ".join(role.split())[:60])
    return people


STOP_WORDS = {"our", "the", "about", "team", "meet", "contact", "company", "agency", "digital",
              "marketing", "services", "home", "read", "more", "view", "learn", "and", "with",
              "founder", "owner", "ceo", "president", "director", "managing", "chief", "officer",
              "we", "us", "who", "is", "a", "an", "of", "for", "by", "in", "at", "to", "co",
              "inc", "llc", "ltd", "group", "studio", "solutions", "technologies", "media",
              "design", "web", "development", "software", "partner", "partners", "founded"}


def merge_people(a, b):
    """Merge name->role dicts, collapsing 'Nickie Rowley' / 'Nicole Rowley' style dupes by last name
    only when first initials match."""
    out = dict(a)
    for n, r in b.items():
        last, ini = n.split()[-1].lower(), n[0].lower()
        if not any(m.split()[-1].lower() == last and m[0].lower() == ini for m in out):
            out[n] = r
    return out


def valid_person_name(n):
    parts = n.split()
    if not 2 <= len(parts) <= 3:
        return False
    if any(p.lower() in STOP_WORDS for p in parts):
        return False
    return all(2 <= len(p) <= 20 for p in parts)


GENERIC_CO_WORDS = STOP_WORDS | {"seo", "agency", "firm", "consulting", "tech", "labs", "digital",
                                  "creative", "interactive", "online", "global", "the", "llc", "website", "websites",
                                  "complete", "best", "top", "leading", "professional", "affordable"}
PROFILE_SITES = ("linkedin.com/in/", "crunchbase.com/person", "zoominfo.com/p/", "rocketreach.co",
                 "theorg.com", "signalhire.com", "contactout.com")


def company_mentioned(company_name, domain, blob, loc_words=()):
    """Stricter than 'any token': need the distinctive part of the name or the domain itself."""
    blob_l = blob.lower()
    if domain in blob_l:
        return True
    stem = domain.split(".")[0].replace("-", "")
    if len(stem) >= 6 and stem in re.sub(r"[^a-z0-9]", "", blob_l):
        return True
    toks = [t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", company_name)
            if t.lower() not in GENERIC_CO_WORDS and t.lower() not in loc_words and not t.isdigit()]
    if sum(map(len, toks)) < 6:  # too short/generic to trust ("Talo"); domain was already checked
        return False
    return all(re.search(rf"\b{re.escape(t)}\b", blob_l) for t in toks)


def best_company_name(c, site_name, domain, industry, loc):
    """Prefer og:site_name; drop SEO-style titles that just restate industry + location."""
    noise = {w.lower() for part in (industry, *loc) for w in (part or "").split()}

    def generic(n):
        words = [w.lower() for w in re.findall(r"[A-Za-z]+", n or "")]
        return not words or all(w in noise or w in GENERIC_CO_WORDS for w in words) or len(n) > 45

    for cand in (site_name, c["name"]):
        if cand and not generic(cand):
            return re.split(r"\s[|\-–:·]\s", cand)[0].strip()
    return domain.split(".")[0].replace("-", " ").title()


def name_from_url(url):
    m = re.search(r"(?:/p/|/person/|/in/)([A-Za-z]+)[-_]([A-Za-z]+)(?:[/-]|$)", url)
    return f"{m.group(1).title()} {m.group(2).title()}" if m else None


def search_people(s, company_name, domain, loc, role_keys, log):
    loc_words = {w.lower() for w in loc.split()}
    """Find decision makers from public profile snippets (LinkedIn, Crunchbase, ZoomInfo…)."""
    role_words = ROLE_SEARCH_WORDS
    role_re = re.compile("|".join(ROLE_PATTERNS[k] for k in role_keys), re.I)
    found, urls = {}, {}
    queries = [f'site:linkedin.com/in "{company_name}" {" ".join(role_words[k] for k in role_keys)}',
               f'{company_name} {loc} {" ".join(role_words[k] for k in role_keys)}']
    for q in queries:
        log(f"searching: {q}")
        for h in s.text(q, max_results=12):
            title, body, href = h.get("title", ""), h.get("body", ""), h.get("href", "")
            if not company_mentioned(company_name, domain, f"{title} {body} {href}", loc_words):
                continue
            parts = [p.strip() for p in re.split(r"\s[-–|]\s|\.\.\.", title) if p.strip()]
            if not parts:
                continue
            cand = re.sub(r"\(.*?\)|,.*$", "", parts[0]).strip()
            rest = " ".join(parts[1:])
            if not valid_person_name(cand):
                # e.g. "Chief Executive Officer & Founder at X" with the name in the URL
                cand, rest = name_from_url(href) if any(p in href for p in PROFILE_SITES) else None, title
            role_m = role_re.search(rest) or (role_re.search(body) if cand else None)
            if cand and role_m and valid_person_name(cand):
                role = re.split(r"\s(?:at|@)\s", parts[1] if len(parts) > 1 and role_re.search(parts[1]) else role_m.group(0))[0]
                found.setdefault(cand, role.strip()[:60])
                if any(p in href for p in PROFILE_SITES):
                    urls.setdefault(cand, href)
        if found:
            break
    return found, urls


# ---------- email patterns + verification ----------

def ascii_name(s):
    return re.sub(r"[^a-z]", "", s.lower())


def candidate_emails(full_name, domain):
    parts = [ascii_name(p) for p in full_name.split() if ascii_name(p)]
    if len(parts) < 2:
        return []
    f, l = parts[0], parts[-1]
    pats = [f"{f}", f"{f}.{l}", f"{f}{l}", f"{f[0]}{l}", f"{f[0]}.{l}", f"{f}_{l}", f"{f}{l[0]}", f"{l}"]
    return [f"{p}@{domain}" for p in dict.fromkeys(pats)]


def infer_pattern(emails, domain, people):
    """If the site shows a personal email of a known person, learn the pattern."""
    for name in people:
        for cand, pat in zip(candidate_emails(name, domain), range(99)):
            if cand in emails:
                return pat
    return None


def classify_email(e, domain):
    local, _, dom = e.partition("@")
    if dom != domain and not dom.endswith("." + domain):
        return "other-domain"
    if local.startswith(HR_PREFIXES):
        return "hr"
    return "generic" if local.startswith(GENERIC_PREFIXES) else "personal"


_mx_cache = {}


def mx_host(domain):
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        ans = sorted(dns.resolver.resolve(domain, "MX", lifetime=6), key=lambda r: r.preference)
        host = str(ans[0].exchange).rstrip(".")
    except Exception:
        host = None
    _mx_cache[domain] = host
    return host


_smtp_ok = None  # None = untested, False = port 25 blocked on this network


def smtp_check(emails, domain):
    """Returns dict email -> 'valid'|'invalid'|'catch-all'|'unknown'."""
    global _smtp_ok
    out = {e: "unknown" for e in emails}
    host = mx_host(domain)
    if not host or _smtp_ok is False:
        return out
    try:
        srv = smtplib.SMTP(host, 25, timeout=8)
        srv.helo("mail.example.com")
        srv.mail("verify@example.com")
        _smtp_ok = True
        junk = "".join(random.choices(string.ascii_lowercase, k=14)) + "@" + domain
        if srv.rcpt(junk)[0] in (250, 251):
            srv.quit()
            return {e: "catch-all" for e in emails}
        for e in emails:
            code = srv.rcpt(e)[0]
            out[e] = "valid" if code in (250, 251) else ("invalid" if 500 <= code < 600 else "unknown")
        srv.quit()
    except (socket.timeout, OSError, smtplib.SMTPException):
        if _smtp_ok is None:
            _smtp_ok = False  # most home ISPs block outbound 25; stop trying
    return out


def smtp_status():
    return {None: "untested", True: "working", False: "blocked on this network"}[_smtp_ok]


# ---------- full pipeline ----------

def run_search(params, emit, log, should_stop):
    industry = params["industry"].strip()
    country, state, city = (params.get(k, "").strip() for k in ("country", "state", "city"))
    size_keys = params.get("sizes") or []
    strict_size = params.get("strict_size", False)
    roles = params.get("roles") or ["founder", "owner"]
    limit = int(params.get("limit", 20))
    verify = params.get("verify", True)
    skip = set(params.get("skip_domains", []))

    s = Searcher(region=params.get("region", "wt-wt"), log=log)
    companies = discover_companies(s, industry, country, state, city, size_keys, limit, log)
    log(f"found {len(companies)} candidate companies, checking each…")

    done = 0
    for c in companies.values():
        if should_stop() or done >= limit:
            break
        if c["domain"] in skip:
            continue
        ok = size_matches(c["size"], size_keys)
        if ok is False or (ok is None and strict_size):
            continue
        log(f"crawling {c['domain']}")
        pages, site_emails, domain, site_name = crawl_site(c["domain"])
        if not pages:
            log(f"  {c['domain']} unreachable, skipped")
            continue
        c["name"] = best_company_name(c, site_name, domain, industry, (country, state, city))
        if size_keys and not c["size"]:
            c["size"] = parse_size(" ".join(t for _, t in pages[:3]))
        people = extract_people(pages, roles)
        # decision makers and HR are searched separately: one long OR-query finds neither well
        li_urls = {}
        for group in ([r for r in roles if r != "hr"], [r for r in roles if r == "hr"]):
            if group:
                found, urls = search_people(s, c["name"], domain,
                                            location_str("", state, city) or country, group, log)
                people = merge_people(people, found)
                li_urls.update(urls)

        generic = sorted(e for e in site_emails if classify_email(e, domain) == "generic")
        hr_inbox = sorted(e for e in site_emails if classify_email(e, domain) == "hr")
        personal = [e for e in site_emails if classify_email(e, domain) == "personal"]
        pattern = infer_pattern(site_emails, domain, people)
        has_mx = bool(mx_host(domain))

        leads = []
        hr_re = re.compile(ROLE_PATTERNS["hr"], re.I)
        for name, role in list(people.items())[:6]:
            cands = candidate_emails(name, domain)
            first = ascii_name(name.split()[0])
            on_site = [e for e in personal if e in cands or e.split("@")[0] == first]
            if on_site:
                email, conf, note = on_site[0], "high", "published on website"
            elif pattern is not None and cands:
                email, conf, note = cands[pattern], "medium", "matches company email pattern"
            elif cands and verify and has_mx:
                res = smtp_check(cands, domain)
                valid = [e for e in cands if res[e] == "valid"]
                if valid:
                    email, conf, note = valid[0], "high", "mail server confirmed"
                elif all(v == "catch-all" for v in res.values()):
                    email, conf, note = cands[0], "low", "guess (domain accepts all)"
                else:
                    email, conf, note = cands[0], "low", "pattern guess, unverified"
            elif cands:
                email, conf, note = cands[0], "low", "pattern guess, unverified"
            else:
                continue
            leads.append({"name": name, "role": role, "email": email, "confidence": conf,
                          "note": note, "alternatives": ", ".join(e for e in cands[:4] if e != email),
                          "linkedin": li_urls.get(name, ""),
                          "kind": "hr" if hr_re.search(role) else "decision"})

        emit({
            "company": c["name"], "domain": domain, "size": size_label(c["size"]),
            "location": location_str(country, state, city), "industry": industry,
            "generic_emails": ", ".join(generic[:3]),
            "hr_emails": ", ".join(hr_inbox[:3]),
            "other_emails": ", ".join(sorted(set(personal) - {l["email"] for l in leads})[:3]),
            "has_mx": has_mx, "leads": leads,
        })
        done += 1
    log(f"done — {done} companies processed. SMTP check: {smtp_status()}")
