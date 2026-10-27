"""Lead Finder tool wrapper.

Directly utilizes wasaybuilds/lead-finder (tools/lead-finder/finder.py).
Performs company discovery, deep site crawling (About, Team, Careers, Contact,
ld+json schema.org), decision maker & HR role extraction, and email synthesis.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import contacts
import core

logger = logging.getLogger("lead_finder_tool")

LEAD_FINDER_DIR = core.ROOT / "tools" / "lead-finder"
if str(LEAD_FINDER_DIR) not in sys.path:
    sys.path.insert(0, str(LEAD_FINDER_DIR))

try:
    import finder
except ImportError:
    finder = None


def run_lead_finder(
    domain: str = "",
    company: str = "",
    industry: str = "",
    city: str = "",
    country: str = "",
    roles: Optional[List[str]] = None,
    company_sizes: Optional[List[str]] = None,
    limit: int = 5,
    verify: bool = True,
) -> Dict[str, Any]:
    """Execute Lead Finder discovery or direct company domain crawl."""
    if finder is None:
        return {"success": False, "error": "Could not import finder from tools/lead-finder"}

    roles = roles or ["founder", "ceo", "hr"]
    limit = max(1, min(limit, 15))

    # Mode A: Direct company domain / name crawl
    if domain or company:
        target_domain = domain
        if not target_domain and company:
            target_domain = contacts.resolve_domain(company)
        if not target_domain:
            return {"success": False, "error": f"Could not resolve domain for '{company}'"}

        target_domain = target_domain.removeprefix("https://").removeprefix("http://").split("/")[0].removeprefix("www.")

        pages, site_emails, final_dom, site_name = finder.crawl_site(target_domain, max_pages=8)
        if not pages:
            return {
                "success": False,
                "domain": target_domain,
                "error": f"Website {target_domain} was unreachable.",
            }

        resolved_company = company or site_name or target_domain.split(".")[0].title()
        people = finder.extract_people(pages, roles)

        # Classify emails found directly on the site
        generic = sorted(e for e in site_emails if finder.classify_email(e, target_domain) == "generic")
        hr_inbox = sorted(e for e in site_emails if finder.classify_email(e, target_domain) == "hr")
        personal = [e for e in site_emails if finder.classify_email(e, target_domain) == "personal"]

        pattern = finder.infer_pattern(site_emails, target_domain, people)
        has_mx = bool(finder.mx_host(target_domain))

        leads = []
        for name, role in list(people.items())[:limit]:
            cands = finder.candidate_emails(name, target_domain)
            first = finder.ascii_name(name.split()[0])
            on_site = [e for e in personal if e in cands or e.split("@")[0] == first]

            if on_site:
                email, conf, note = on_site[0], "high", "published on website"
            elif pattern is not None and cands:
                email, conf, note = cands[pattern], "medium", "matches company email pattern"
            elif cands and verify and has_mx:
                res = finder.smtp_check(cands, target_domain)
                valid = [e for e in cands if res.get(e) == "valid"]
                if valid:
                    email, conf, note = valid[0], "high", "mail server confirmed"
                elif all(v == "catch-all" for v in res.values()):
                    email, conf, note = cands[0], "low", "guess (domain accepts all)"
                else:
                    email, conf, note = cands[0], "low", "pattern guess, unverified"
            elif cands:
                email, conf, note = cands[0], "low", "pattern guess"
            else:
                continue

            leads.append({
                "name": name,
                "role": role,
                "email": email,
                "confidence": conf,
                "note": note,
                "alternatives": [e for e in cands[:3] if e != email],
                "suppressed": contacts.is_suppressed(email),
            })

        return {
            "success": True,
            "tool": "lead-finder",
            "company": resolved_company,
            "domain": target_domain,
            "has_mx": has_mx,
            "inferred_pattern": pattern,
            "hr_inboxes": hr_inbox,
            "generic_inboxes": generic,
            "decision_makers_and_leads": leads,
            "total_leads": len(leads),
        }

    # Mode B: Industry + City discovery
    if not industry:
        return {"success": False, "error": "Either 'domain', 'company', or 'industry' is required."}

    search_params = {
        "industry": industry,
        "country": country,
        "city": city,
        "roles": roles,
        "sizes": company_sizes or [],
        "limit": limit,
        "verify": verify,
    }

    results = []
    logs = []

    def _emit(row):
        results.append(row)

    def _log(msg):
        logs.append(msg)

    finder.run_search(search_params, emit=_emit, log=_log, should_stop=lambda: len(results) >= limit)

    return {
        "success": True,
        "tool": "lead-finder",
        "industry": industry,
        "location": f"{city}, {country}".strip(", "),
        "total_companies_found": len(results),
        "companies": results,
    }
