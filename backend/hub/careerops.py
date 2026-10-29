"""career-ops phase 1: zero-token scan of company job boards (ATS) into the job pipeline.

For each company: find its public ATS board (bundled directory in hub/ats_directory.py, then the free slug probes in
career_resolve.py), read every open role with the vendored career-ops providers (vendor/career-ops/run_scan.mjs, run by
node or bun), keep roles whose title passes the profile filter AND matches a search keyword closely
(career_resolve.TITLE_MIN), drop dead links, and save them as jobs(source='careerops', apply_type='external') with
apply_url set, so scoring, career_apply and the email phase handle them like any other job. No API key, no LLM tokens.
career-ops never submits anything; applying stays with career_apply / email.
"""
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests  # noqa: E402

import ats_directory  # noqa: E402
import autopilot  # noqa: E402
import career_resolve  # noqa: E402
import core  # noqa: E402

RUNNER = Path(__file__).resolve().parent.parent / "vendor" / "career-ops" / "run_scan.mjs"
SOURCE = "careerops"
# providers run_scan.mjs can read; other directory entries (workday, oracle, icims, ...) are not scanned here
SUPPORTED = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee", "teamtailor", "bamboohr",
             "breezy", "personio", "jazzhr")
BOARD_URL = {"greenhouse": "https://job-boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
             "ashby": "https://jobs.ashbyhq.com/{}", "smartrecruiters": "https://careers.smartrecruiters.com/{}",
             "workable": "https://apply.workable.com/{}"}
RESCAN_DAYS = 3
NO_BOARD_DAYS = 14


class CareerOpsUnavailable(Exception):
    """No JS runtime, or the runner crashed / printed something that is not JSON."""


# ---------------------------------------------------------------- runner
def runtime() -> list:
    """Command that runs the .mjs runner: node (dev machines) or bun (the container ships bun)."""
    for exe in ("node", "bun"):
        path = shutil.which(exe)
        if path:
            return [path]
    return []


def scan_boards(entries: list, max_jobs: int = 300, timeout: int = 180, run=None) -> list:
    """entries [{name, careers_url}] -> results [{name, provider, jobs, error}] from run_scan.mjs."""
    if not entries:
        return []
    cmd = runtime()
    if not cmd and run is None:
        raise CareerOpsUnavailable("neither node nor bun is installed")
    payload = json.dumps({"entries": entries, "max_jobs": max_jobs})
    try:
        p = (run or subprocess.run)(cmd + [str(RUNNER)], input=payload, capture_output=True, text=True,
                                    timeout=timeout, cwd=str(RUNNER.parent))
    except subprocess.TimeoutExpired as e:
        raise CareerOpsUnavailable(f"runner timed out after {timeout}s") from e
    if p.returncode != 0:
        raise CareerOpsUnavailable(f"runner exited {p.returncode}: {(p.stderr or '').strip()[:200]}")
    try:
        return json.loads(p.stdout or "{}").get("results") or []
    except ValueError as e:
        raise CareerOpsUnavailable(f"runner output is not JSON: {(p.stdout or '')[:120]}") from e


# ---------------------------------------------------------------- board discovery
def _full_name_slugs(company: str, domain: str = "") -> list:
    """career_resolve.slugs without the bare first word: "Capital One" must not match the board of "Capital"."""
    words = re.findall(r"[a-z0-9]+", career_resolve.SUFFIX_RX.sub(" ", (company or "").lower()))
    first = words[0] if len(words) > 1 else None
    return [s for s in career_resolve.slugs(company, domain) if s != first]


def discover(company: str, domain: str = "", http=None, probe: bool = True) -> dict:
    """-> {"provider", "url", "how"} for the company's scannable ATS board, or {} when none is known."""
    slugs = _full_name_slugs(company, domain)
    for ats, url in ats_directory.lookup(company, slugs):
        if ats in SUPPORTED:
            return {"provider": ats, "url": url, "how": "directory"}
    if probe:
        for s in slugs:
            provider, jobs = career_resolve.ats_jobs(s, http)
            if provider in BOARD_URL:
                return {"provider": provider, "url": BOARD_URL[provider].format(s), "how": f"probe '{s}' ({len(jobs)} roles)"}
    return {}


def board_for(company: str, domain: str = "", http=None) -> dict:
    """discover() with a kv cache, so companies without a board are not probed again for NO_BOARD_DAYS."""
    key = "careerops:board:" + ats_directory.norm(company or domain)
    cached = core.kv_get(key)
    if cached and (cached.get("url") or cached.get("at", "") > (datetime.now() - timedelta(days=NO_BOARD_DAYS)).isoformat()):
        return cached if cached.get("url") else {}
    hit = discover(company, domain, http)
    core.kv_set(key, {**hit, "at": datetime.now().isoformat()})
    return hit


# ---------------------------------------------------------------- filters
def title_matches(profile: dict, title: str) -> bool:
    """Profile include/exclude filter, then a close match to one search keyword (same TITLE_MIN as career_resolve)."""
    title = (title or "").strip()
    if not title or not autopilot.title_ok(profile, title):
        return False
    kws = [k for k in (profile.get("search") or {}).get("keywords") or [] if k]
    return not kws or any(career_resolve.title_similarity(k, title) >= career_resolve.TITLE_MIN for k in kws)


def is_live(url: str, http=None, timeout: int = 8):
    """True live, False dead (4xx/5xx, or a job URL that redirects to a page without the job id), None unknown."""
    if not str(url or "").startswith("http"):
        return False
    get = http or requests.get
    try:
        r = get(url, headers=career_resolve.UA, timeout=timeout, allow_redirects=True, stream=True)
        r.close()
    except requests.RequestException:
        return None
    if r.status_code in (404, 410) or 500 <= r.status_code < 600:
        return False
    if r.status_code >= 400:
        return None  # 401/403/429: bot protection, not proof that the job is gone
    job_id = next((p for p in reversed(url.rstrip("/").split("/")) if re.search(r"\d{4,}|[0-9a-f]{8}-", p)), "")
    final = str(getattr(r, "url", "") or url)
    return not (job_id and job_id not in final)


# ---------------------------------------------------------------- scan + save
def save_results(profile: dict, results: list, boards: dict, per_company: int = 10, http=None, log=print) -> dict:
    out = {"saved": 0, "matched": 0, "dead": 0, "errors": 0}
    for res in results:
        name, board = res.get("name") or "", boards.get(res.get("name") or "", {})
        if res.get("error"):
            out["errors"] += 1
            log(f"[careerops] {name}: {res['error'][:120]}")
            if re.search(r"\b(404|410)\b|no supported job board", res["error"]):  # stale directory entry: forget it
                core.kv_set("careerops:board:" + ats_directory.norm(name), {"at": datetime.now().isoformat()})
            continue
        saved = 0
        for j in res.get("jobs") or []:
            if saved >= per_company:
                break
            if not (j.get("url") and j.get("title")):
                continue
            if not title_matches(profile, j.get("title", "")):
                continue
            out["matched"] += 1
            if is_live(j["url"], http) is False:
                out["dead"] += 1
                continue
            if autopilot.save_job({"source": SOURCE, "ext_id": str(j.get("externalId") or ""), "url": j["url"],
                                   "title": j["title"].strip(), "company": name, "location": j.get("location") or "",
                                   "posted": str(j.get("postedAt") or "")[:10], "description": j.get("description") or "",
                                   "company_site": board.get("domain", ""), "apply_type": "external", "apply_url": j["url"]}):
                saved += 1
        out["saved"] += saved
        log(f"[careerops] {name} ({res.get('provider')}): {len(res.get('jobs') or [])} roles, {saved} new matching jobs saved")
    return out


def default_companies(limit: int) -> list:
    """Companies we already know (from jobs and HR contacts), rotated so each run scans a few new ones."""
    with core.db() as c:
        rows = c.execute(
            "SELECT company, MAX(company_site) FROM jobs WHERE COALESCE(company,'')<>'' AND source<>? GROUP BY LOWER(company) "
            "UNION SELECT company, MAX(domain) FROM hr_contacts WHERE COALESCE(company,'')<>'' GROUP BY LOWER(company)",
            (SOURCE,)).fetchall()
    seen, items = set(), []
    for name, dom in rows:
        k = ats_directory.norm(name)
        if k and k not in seen:
            seen.add(k)
            items.append([name, dom or ""])
    return core.rotate("careerops_companies", items, limit) if items else []


def run(opts: dict, log=print) -> dict:
    if not runtime():
        return {"error": "career-ops scanner needs node or bun"}
    p = opts.get("profile") or core.load_profile()
    limit = int(opts.get("limit", 10))
    companies = [[c, ""] if isinstance(c, str) else list(c) for c in (opts.get("companies") or [])] or default_companies(limit)
    cutoff = (datetime.now() - timedelta(days=RESCAN_DAYS)).isoformat()
    boards, entries, out = {}, [], {"companies": 0, "boards": 0, "no_board": 0, "recent": 0}
    for name, dom in companies[:limit]:
        if autopilot.stopped():
            break
        out["companies"] += 1
        if "." in name and not dom:
            name, dom = name.split(".")[0].title(), name
        board = board_for(name, dom)
        if not board:
            out["no_board"] += 1
            continue
        last = core.kv_get("careerops:scanned:" + board["url"], "")
        if last > cutoff and not opts.get("companies"):
            out["recent"] += 1
            continue
        boards[name] = {**board, "domain": dom}
        entries.append({"name": name, "careers_url": board["url"]})
    out["boards"] = len(entries)
    try:
        results = scan_boards(entries, int(opts.get("max_jobs", 300)))
    except CareerOpsUnavailable as e:
        return {**out, "error": str(e)}
    for e in entries:
        core.kv_set("careerops:scanned:" + e["careers_url"], datetime.now().isoformat())
    out.update(save_results(p, results, boards, int(opts.get("per_company", 10)), log=log))
    return out


if __name__ == "__main__":
    print("SUMMARY " + json.dumps(run(json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}), indent=2), flush=True)
