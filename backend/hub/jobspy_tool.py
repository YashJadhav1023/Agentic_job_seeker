"""Multi-board job discovery via JobSpy (speedyapply/JobSpy, PyPI: python-jobspy).

One call scrapes LinkedIn, Indeed, Naukri, Glassdoor and Google Jobs concurrently. Results are filtered with the
profile's title include/exclude rules and, with save=true, stored in the hub `jobs` table so the autopilot scores
and emails them like any other discovered job. Read-only towards the job boards (no login, no applications).
"""
from __future__ import annotations

import math

import autopilot
import core

SITES = ("linkedin", "indeed", "naukri", "glassdoor", "google")


def _clean(v):
    """pandas NaN/NaT/Timestamp -> JSON-friendly values."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def multi_board_search(a: dict, scrape=None) -> dict:
    """`scrape` is injectable for tests; defaults to jobspy.scrape_jobs."""
    if scrape is None:
        try:
            from jobspy import scrape_jobs as scrape
        except ImportError:
            return {"error": "python-jobspy is not installed (pip install python-jobspy)"}
    p = core.load_profile()
    sites = [s for s in (a.get("sites") or ["linkedin", "indeed", "naukri"]) if s in SITES]
    if not sites:
        return {"error": f"sites must be a subset of {list(SITES)}"}
    term = a.get("query") or p["search"]["keywords"][0]
    location = a.get("location") or p["search"]["locations"][0]
    limit = max(1, min(int(a.get("limit", 20)), 50))
    kw = dict(site_name=sites, search_term=term, location=location, results_wanted=limit,
              hours_old=int(a.get("hours_old", 72)), country_indeed="India",
              linkedin_fetch_description=bool(a.get("fetch_description", False)))
    if "google" in sites:
        kw["google_search_term"] = f"{term} jobs near {location} since yesterday"
    try:
        df = scrape(**kw)
    except Exception as e:  # noqa: BLE001 - surface board errors to the caller
        return {"error": f"jobspy failed: {type(e).__name__}: {str(e)[:300]}"}
    rows = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
    jobs, skipped, saved = [], 0, 0
    for r in rows:
        title = str(r.get("title") or "")
        if not autopilot.title_ok(p, title):
            skipped += 1
            continue
        j = {"site": r.get("site"), "title": title, "company": _clean(r.get("company")),
             "location": _clean(r.get("location")), "url": _clean(r.get("job_url")),
             "posted": _clean(r.get("date_posted")), "is_remote": _clean(r.get("is_remote")),
             "min_amount": _clean(r.get("min_amount")), "max_amount": _clean(r.get("max_amount")),
             "emails": _clean(r.get("emails"))}
        if a.get("save") and j["url"]:
            if autopilot.save_job({"source": f"jobspy-{j['site']}", "ext_id": str(_clean(r.get("id")) or ""),
                                   "url": j["url"], "title": title, "company": j["company"] or "",
                                   "location": j["location"] or "", "posted": str(j["posted"] or ""),
                                   "description": _clean(r.get("description")) or "", "apply_type": "unknown"}):
                saved += 1
        jobs.append(j)
    return {"query": term, "location": location, "sites": sites, "count": len(jobs),
            "filtered_out_by_title": skipped, "saved_to_hub": saved, "jobs": jobs}
