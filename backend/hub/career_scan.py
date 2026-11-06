"""Career-page scan: open roles on company career pages (via Jina Reader) feed the normal job pipeline.

For each company: find its careers page (Jina Reader link discovery), read the listed jobs (Jina Reader markdown + LLM extraction),
keep titles that pass the profile filter and save them as jobs(source='career', apply_type='external'). Scoring and the
email phase of autopilot then handle them like any other job. Uses JOBHUB_JINA_API_KEY (Parameter Store secret or .secrets).
"""
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests  # noqa: E402

import autopilot  # noqa: E402
import contacts  # noqa: E402
import core  # noqa: E402
import keys  # noqa: E402
import llm  # noqa: E402

API = "https://r.jina.ai"
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


class ScannerUnavailable(Exception):
    """The key is missing, rejected, out of credits or rate limited: stop the scan, do not retry in a loop."""


FirecrawlUnavailable = ScannerUnavailable
OutOfCredits = ScannerUnavailable  # kept for backward compatibility


def api_key() -> str:
    return keys.get("jina") or keys.get("firecrawl")


def _get(target_url: str, json_format: bool = False, timeout: int = 40):
    key = api_key()
    headers = {
        "Accept": "application/json" if json_format else "text/plain",
        "X-Timeout": "30",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    cooling = keys.failed("jina")
    if cooling:
        raise ScannerUnavailable(cooling)

    url = f"{API}/{target_url}" if not target_url.startswith(API) else target_url
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
        except Exception as e:
            if attempt == 2:
                raise ScannerUnavailable(f"Scanner connection error: {str(e)[:100]}") from e
            time.sleep(2 * (attempt + 1))
            continue

        kind = keys.classify(r.status_code, r.text[:300] if r.status_code >= 400 else "")
        if kind:
            keys.mark_failed("jina", kind)
            raise ScannerUnavailable(keys.failed("jina") or kind)
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == 2:
                raise ScannerUnavailable(f"Scanner busy (HTTP {r.status_code})")
            time.sleep(min(int(r.headers.get("Retry-After", 0) or 0) or 3 * (attempt + 1), 20))
            continue
        r.raise_for_status()
        return r.json() if json_format else r.text
    raise ScannerUnavailable("Scanner unavailable")


def find_career_url(domain: str) -> str:
    target = f"https://{domain}" if not domain.startswith("http") else domain
    try:
        data = _get(target, json_format=True)
    except Exception:
        return ""
    content = data.get("data", {}).get("content", "") if isinstance(data, dict) else str(data)
    # Find all http/https links in the markdown
    found_urls = re.findall(r'https?://[^\s)"]+', content)
    # Capture relative markdown links like [Careers](/careers)
    for rel in re.findall(r'\[[^\]]*\]\((/[^\s)]+)\)', content):
        found_urls.append(urljoin(target, rel))
    # Also check structured links if Jina returned them
    if isinstance(data, dict):
        links_dict = data.get("data", {}).get("links") or {}
        if isinstance(links_dict, dict):
            for lk in links_dict.values():
                if isinstance(lk, str):
                    found_urls.append(urljoin(target, lk) if lk.startswith("/") else lk)

    # Exclude external third-party social domains that match /jobs or /careers
    exclude_domains = ("twitter.com", "x.com", "linkedin.com", "facebook.com", "instagram.com", "youtube.com")
    clean_domain = domain.lower().replace("https://", "").replace("http://", "").split("/")[0]
    urls = []
    for u in set(found_urls):
        if not u or not CAREER_RX.search(u):
            continue
        u_domain = urlparse(u).netloc.lower()
        if any(ex in u_domain for ex in exclude_domains) and clean_domain not in u_domain:
            continue
        urls.append(u)
    return min(urls, key=len) if urls else ""  # shortest path is the index page


def extract_jobs(url: str) -> list:
    try:
        markdown = _get(url, json_format=False)
    except Exception:
        return []
    if not markdown or len(markdown) < 50:
        return []

    # Use existing LLM layer to extract structured jobs from markdown
    prompt = (
        "Extract every open job position listed on this careers page into a JSON object.\n"
        'Return this exact JSON shape: {"jobs": [{"title": "...", "location": "...", "url": "...", "description": "..."}]}\n'
        "Include all job titles and their links. If location or description is missing, use empty string.\n\n"
        f"DATA:\n{markdown[:25000]}"
    )
    try:
        res = llm.chat_json(prompt)
        if isinstance(res, dict) and "jobs" in res and isinstance(res["jobs"], list):
            return res["jobs"]
    except Exception:
        pass

    # Heuristic fallback: extract markdown links that look like job titles
    jobs = []
    seen = set()
    for m in re.finditer(r'\[([^\]\n]{4,80})\]\((https?://[^\s)]+)\)', markdown):
        title, link = m.group(1).strip(), m.group(2).strip()
        t_lower = title.lower()
        if any(w in t_lower for w in ("engineer", "developer", "manager", "lead", "designer", "architect", "analyst", "intern", "specialist")):
            if link not in seen:
                seen.add(link)
                jobs.append({"title": title, "url": link, "location": "", "description": ""})
    return jobs


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
        return {"error": "JOBHUB_JINA_API_KEY is not set"}
    if keys.failed("jina"):
        return {"error": keys.failed("jina")}
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
        except ScannerUnavailable as e:
            print(f"[career] stopped: {keys.redact(e)}", flush=True)
            out["stopped"] = keys.redact(e)
            break
        except Exception as e:  # noqa: BLE001 - one broken site must not stop the rest
            out["failed"] += 1
            print(f"[career] {name}: failed ({keys.redact(e)[:100]})", flush=True)
    return out


if __name__ == "__main__":
    print("SUMMARY " + json.dumps(run(json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}), indent=2), flush=True)
