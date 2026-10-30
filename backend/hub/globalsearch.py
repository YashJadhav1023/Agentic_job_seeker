"""Global job search: every profile keyword x location across LinkedIn, Indeed, Naukri, Glassdoor and Google Jobs.

Runs as a background run (python hub/globalsearch.py '<json options>'):
  1. JobSpy scrapes each (keyword, location) pair on the chosen boards (no login, read-only);
  2. matches are filtered by the profile's title rules, saved to the hub `jobs` table and scored against the resume;
  3. the ranked list is written to data/search_results/<run id>.json (read it with the `search_results` tool);
  4. with auto_apply=true the good jobs are handed to the apply step (career-page form, then a JD-tailored email to the
     verified contact), which honours dry-run, the safety switch, daily caps and the suppression list.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autopilot  # noqa: E402
import core  # noqa: E402

DEFAULT_SITES = ["linkedin", "indeed", "naukri", "google"]
RESULTS = core.ROOT / "data" / "search_results"
MAX_PAIRS = 6


def _locations(p: dict, opts: dict) -> list:
    raw = opts.get("locations") or ([opts["location"]] if opts.get("location") else None) or p["search"].get("locations") or []
    out, seen = [], set()
    for item in raw:
        for l in str(item).split(","):
            l = l.strip()
            if l and l.lower() != "all" and l.lower() not in seen:
                seen.add(l.lower())
                out.append(l)
    return out


def pairs(p: dict, opts: dict) -> list:
    """(keyword, city) searches for this run. Every chosen city is searched in every run; when there are more pairs
    than max_pairs the keywords rotate across runs (one keyword per city per run at minimum)."""
    s = p["search"]
    kws = [k for k in (opts.get("queries") or ([opts["query"]] if opts.get("query") else None) or s.get("keywords") or []) if k]
    locs = _locations(p, opts) or ["India"]
    limit = int(opts.get("max_pairs", MAX_PAIRS))
    if len(kws) * len(locs) <= limit or not kws:
        return [(k, l) for k in kws for l in locs]
    per_city = max(1, limit // len(locs))
    picked = core.rotate("global_search_keywords", kws, per_city)
    return [(k, l) for k in picked for l in locs]


def collect(p: dict, opts: dict, search, log=print) -> list:
    """Run the searches. `search` is jobspy_tool.multi_board_search (injectable for tests). -> urls found, in order."""
    sites = opts.get("sites") or DEFAULT_SITES
    urls, seen = [], set()
    for kw, loc in pairs(p, opts):
        log(f"[search] {kw!r} in {loc!r} on {', '.join(sites)}")
        res = search({"query": kw, "location": loc, "sites": sites, "limit": int(opts.get("per_search", 20)),
                      "hours_old": int(opts.get("hours_old", 168)), "save": True,
                      "fetch_description": bool(opts.get("fetch_description", True))})
        if res.get("error"):
            log(f"[search] {kw!r} {loc!r}: {res['error']}")
            continue
        n = 0
        for j in res.get("jobs", []):
            if j.get("url") and j["url"] not in seen:
                seen.add(j["url"])
                urls.append(j["url"])
                n += 1
        log(f"[search] {kw!r} {loc!r}: {n} new matches ({res.get('filtered_out_by_title', 0)} dropped by title rules)")
    return urls


def ranked(urls: list) -> list:
    if not urls:
        return []
    q = ",".join("?" * len(urls))
    with core.db() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT id,source,title,company,location,posted,score,matched,status,reason,contact,url,apply_type,jd_emails "
            f"FROM jobs WHERE url IN ({q})", urls)]
    for r in rows:
        r["matched"] = json.loads(r["matched"] or "[]")
        r["emails"] = json.loads(r.pop("jd_emails") or "[]")
        r["score"] = r["score"] or 0
    rows.sort(key=lambda r: (-(r["score"] or 0), r["id"]))
    return rows


def run(opts: dict, search=None, log=print) -> dict:
    p = core.load_profile()
    c = autopilot.cfg(p)
    if search is None:
        import jobspy_tool
        search = jobspy_tool.multi_board_search
    urls = collect(p, opts, search, log)
    log(f"[score] scoring {len(urls)} jobs against the resume")
    scoring = autopilot.score_new(p, c)
    jobs = ranked(urls)
    good = [j for j in jobs if j["status"] in ("scored", "queued") and (j["score"] or 0) >= float(opts.get("min_score", c["min_score"]))]
    out = {"found": len(jobs), "good": len(good), "scoring": scoring, "sites": opts.get("sites") or DEFAULT_SITES,
           "jobs": jobs, "finished": core.now_iso()}
    rid = os.environ.get("JOBHUB_RUN_ID") or f"search-{int(time.time())}"
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{rid}.json").write_text(json.dumps(out, default=str))
    log(f"[done] {len(jobs)} jobs, {len(good)} good matches (results saved)")
    if opts.get("auto_apply") and good and not autopilot.stopped():
        import server  # lazy: server imports this package's siblings at import time
        ids = [j["id"] for j in good[: int(opts.get("apply_limit", 10))]]
        res = server.apply_to_jobs({"job_ids": ids, "dry_run": opts.get("dry_run")})
        out["auto_apply"] = res
        log(f"[apply] {res.get('note') or res.get('error')} run={res.get('run_id')}")
        (RESULTS / f"{rid}.json").write_text(json.dumps(out, default=str))
    return {k: v for k, v in out.items() if k != "jobs"}


if __name__ == "__main__":
    print("SUMMARY " + json.dumps(run(json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}), default=str), flush=True)
