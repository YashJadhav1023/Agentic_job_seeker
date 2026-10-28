"""Career-page scan: open roles on company career pages (via Firecrawl) feed the normal job pipeline.

For each company: find its careers page (Firecrawl /map), read the listed jobs (Firecrawl /scrape, JSON schema),
keep titles that pass the profile filter and save them as jobs(source='career', apply_type='external'). Scoring and the
email phase of autopilot then handle them like any other job. Needs JOBHUB_FIRECRAWL_API_KEY (Parameter Store secret).
"""
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests  # noqa: E402

import autopilot  # noqa: E402
import contacts  # noqa: E402
import core  # noqa: E402
import keys  # noqa: E402

API = "https://api.firecrawl.dev/v2"
CAREER_RX = re.compile(r"/(careers?|jobs?|join-us|work-with-us|openings?|opportunities)(/|$|\?)", re.I)
SCHEMA = {
    "type": "object",
    "properties": {"jobs": {"type": "array", "items": {
        "type": "object",
        "properties": {"title": {"type": "string"}, "location": {"type": "string"},
                       "url": {"type": "string"}, "description": {"type": "string"}},
        "required": ["title", "url"]}}},
    "required": ["jobs"],
}


class FirecrawlUnavailable(Exception):
    """The key is missing, rejected, out of credits or rate limited: stop the scan, do not retry in a loop."""


OutOfCredits = FirecrawlUnavailable  # kept for older callers/tests


def api_key() -> str:
    return keys.get("firecrawl")


def _post(path: str, body: dict, timeout: int = 90) -> dict:
    key = api_key()
    if not key:
        raise FirecrawlUnavailable("JOBHUB_FIRECRAWL_API_KEY is not set")
    cooling = keys.failed("firecrawl")
    if cooling:
        raise FirecrawlUnavailable(cooling)
    for attempt in range(3):
        r = requests.post(API + path, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
        kind = keys.classify(r.status_code, r.text[:300] if r.status_code >= 400 else "")
        if kind:
            keys.mark_failed("firecrawl", kind)
            raise FirecrawlUnavailable(keys.failed("firecrawl") or kind)
        if r.status_code == 429 or r.status_code >= 500:  # rate limit / provider hiccup: wait, then try again
            if attempt == 2:
                raise FirecrawlUnavailable(f"Firecrawl busy (HTTP {r.status_code})")
            time.sleep(min(int(r.headers.get("Retry-After", 0) or 0) or 5 * (attempt + 1), 30))
            continue
        r.raise_for_status()
        return r.json()
    raise FirecrawlUnavailable("Firecrawl unavailable")


def find_career_url(domain: str) -> str:
    data = _post("/map", {"url": f"https://{domain}", "search": "careers jobs openings", "limit": 15})
    urls = [(item.get("url") if isinstance(item, dict) else item) for item in (data.get("links") or [])]
    urls = [u for u in urls if u and CAREER_RX.search(u)]
    return min(urls, key=len) if urls else ""  # shortest path is the index page


def extract_jobs(url: str) -> list:
    data = _post("/scrape", {"url": url, "onlyMainContent": True, "timeout": 60000, "formats": [
        {"type": "json", "schema": SCHEMA,
         "prompt": "List every open job on this page with its title, location, absolute link and a short description."}]})
    return (((data.get("data") or {}).get("json")) or {}).get("jobs") or []


def scan_company(profile: dict, company: str, per_company: int = 10) -> int:
    """Save relevant open roles of one company (name or domain). Returns how many new jobs were saved."""
    domain = company if "." in company else contacts.resolve_domain(company)
    if not domain:
        return 0
    page = find_career_url(domain)
    if not page:
        return 0
    saved = 0
    for j in extract_jobs(page):
        title = (j.get("title") or "").strip()
        if not title or not autopilot.title_ok(profile, title):
            continue
        url = urljoin(page, (j.get("url") or "").strip())
        if autopilot.save_job({"source": "career", "url": url, "title": title,
                               "company": company if "." not in company else domain.split(".")[0].title(),
                               "location": j.get("location") or "", "description": j.get("description") or "",
                               "company_site": domain, "apply_type": "external"}):
            saved += 1
        if saved >= per_company:
            break
    return saved


def default_companies(limit: int) -> list:
    """Companies we already know (from HR contacts) whose career page has not been scanned yet."""
    with core.db() as c:
        rows = c.execute(
            "SELECT DISTINCT domain FROM hr_contacts WHERE domain NOT IN (SELECT company_site FROM jobs WHERE source='career') "
            "AND domain NOT LIKE '%gmail.%' AND domain NOT LIKE '%yahoo.%' AND domain NOT LIKE '%outlook.%' "
            "AND domain NOT LIKE '%hotmail.%' LIMIT ?", (limit,)).fetchall()
    return [r[0] for r in rows if r[0]]


def run(opts: dict) -> dict:
    if not api_key():
        return {"error": "JOBHUB_FIRECRAWL_API_KEY is not set"}
    if keys.failed("firecrawl"):
        return {"error": keys.failed("firecrawl")}
    p = core.load_profile()
    limit = int(opts.get("limit", 10))
    companies = opts.get("companies") or default_companies(limit)
    out = {"scanned": 0, "saved": 0, "failed": 0}
    for name in companies[:limit]:
        try:
            n = scan_company(p, name)
            out["scanned"] += 1
            out["saved"] += n
            print(f"[career] {name}: {n} new jobs", flush=True)
        except FirecrawlUnavailable as e:
            print(f"[career] stopped: {keys.redact(e)}", flush=True)
            out["stopped"] = keys.redact(e)
            break
        except Exception as e:  # noqa: BLE001 - one broken site must not stop the rest
            out["failed"] += 1
            print(f"[career] {name}: failed ({keys.redact(e)[:100]})", flush=True)
    return out


if __name__ == "__main__":
    print("SUMMARY " + json.dumps(run(json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}), indent=2), flush=True)
