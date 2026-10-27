"""Company Email Finder & Verifier.

Combines the best open-source techniques from:
  1. batuhanaky/mailscout: prefix brute force, name variants generation, name normalization.
  2. omm9846/verdict: multi-page harvest (/team, /about, /contact, /careers, /press, etc.),
     pattern inference ({first}.{last}, {first[0]}{last}, etc.), M365 GetCredentialType HTTPS check,
     Gravatar HTTPS check, and enterprise gateway detection.
  3. wasaybuilds/lead-finder: HTML parsing (ld+json schema.org person extraction, mailto links),
     decision-maker & HR role classification.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import secrets
import string
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple

import contacts
import core
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("company_finder")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
HTTP_TIMEOUT = 8

# Key pages on company website where contact info is typically published
COMMON_PATHS = [
    "",
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/leadership",
    "/people",
    "/careers",
    "/jobs",
    "/press",
    "/media",
    "/privacy",
    "/legal",
]

# Standard company prefixes to check
COMMON_ROLE_PREFIXES = [
    "careers",
    "hr",
    "jobs",
    "recruitment",
    "talent",
    "hiring",
    "contact",
    "info",
    "hello",
    "support",
    "sales",
    "press",
    "media",
    "team",
    "office",
]

ENTERPRISE_GATES = (
    "mimecast",
    "proofpoint",
    "barracuda",
    "pphosted",
    "messagelabs",
    "symantec",
    "fireeye",
    "trendmicro",
    "forcepoint",
    "spamtitan",
)

ROLE_PATTERNS = {
    "founder": re.compile(r"\b(co-?founder|founder|founding partner)\b", re.I),
    "ceo": re.compile(r"\b(ceo|chief executive|managing director)\b", re.I),
    "cto": re.compile(r"\b(cto|chief technology|head of engineering|vp of engineering)\b", re.I),
    "hr": re.compile(r"\b(hr|human resources|talent|recruiter|recruitment|talent acquisition|people operations)\b", re.I),
}


# ---------------------------------------------------------------- HTTPS Soft Signals (Verdict inspired)
def check_gravatar(email: str) -> bool:
    """Return True if email has an active Gravatar profile (strong proof email exists without port 25)."""
    try:
        md5_hash = hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()
        url = f"https://www.gravatar.com/avatar/{md5_hash}?d=404&s=1"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=4) as resp:
            return resp.status == 200
    except Exception:
        return False


def check_microsoft365(email: str, mx_hosts: List[str]) -> Optional[bool]:
    """Check existence via M365 unauthenticated GetCredentialType endpoint over HTTPS.
    
    Uses a random control email on the same domain to unmask tenants that return 'exists' for all.
    Returns:
      True: Confirmed exists
      False: Confirmed does not exist
      None: Inconclusive / masked tenant / non-M365 domain
    """
    if not any("mail.protection.outlook.com" in h.lower() for h in (mx_hosts or [])):
        return None

    domain = email.split("@")[-1]

    def _query(username: str) -> Optional[int]:
        try:
            body = json.dumps({"Username": username}).encode("utf-8")
            req = urllib.request.Request(
                "https://login.microsoftonline.com/common/GetCredentialType",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json; charset=UTF-8",
                    "User-Agent": UA,
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
                # 0: exists, 1: does not exist
                return data.get("IfExistsResult")
        except Exception:
            return None

    # Control test: probe random non-existent address
    control = f"probe-ctrl-{secrets.token_hex(6)}@{domain}"
    ctrl_res = _query(control)
    if ctrl_res != 1:  # Tenant masks existence or query failed
        return None

    # Target test
    target_res = _query(email)
    if target_res == 0:
        return True
    if target_res == 1:
        return False
    return None


# ---------------------------------------------------------------- Web Extraction & Scraping
def fetch_page_content(url: str, timeout: int = HTTP_TIMEOUT) -> Tuple[str, str]:
    """Fetch URL returning (final_url, html_text)."""
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", ""):
            return resp.url, resp.text
    except Exception:
        pass
    return "", ""


def extract_schema_people(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract Person and Founder entities from schema.org JSON-LD blocks."""
    people = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                # Handle graph
                nodes = item.get("@graph", [item]) if "@graph" in item else [item]
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    ntype = str(node.get("@type", "")).lower()
                    if "person" in ntype:
                        name = node.get("name")
                        job = node.get("jobTitle") or node.get("description") or "Team Member"
                        email = node.get("email")
                        if name and isinstance(name, str):
                            people.append({"name": name.strip(), "role": str(job).strip(), "email": (email or "").strip().lower()})
                    elif "organization" in ntype:
                        founders = node.get("founder") or node.get("founders") or []
                        if isinstance(founders, dict):
                            founders = [founders]
                        for f in founders:
                            if isinstance(f, dict) and f.get("name"):
                                people.append({"name": f["name"].strip(), "role": "Founder", "email": f.get("email", "").strip().lower()})
        except Exception:
            continue
    return people


from concurrent.futures import ThreadPoolExecutor, as_completed

def harvest_website_emails(domain: str, max_pages: int = 10) -> Dict[str, Any]:
    """Crawl company website concurrently to extract published emails, mailto links, and people."""
    base_url = f"https://{domain}"
    final_url, html = fetch_page_content(base_url, timeout=5)
    if not html:
        base_url = f"http://{domain}"
        final_url, html = fetch_page_content(base_url, timeout=5)

    if not html:
        return {"published_emails": {}, "people": [], "crawled_urls": [], "domain": domain}

    parsed_domain = urllib.parse.urlparse(final_url or base_url).netloc.lower().removeprefix("www.")
    if parsed_domain:
        domain = parsed_domain

    published: Dict[str, List[str]] = {}  # email -> list of pages where found
    people: List[Dict[str, str]] = []
    crawled: Set[str] = set()

    pages_to_crawl = [base_url]
    for path in COMMON_PATHS:
        if path:
            pages_to_crawl.append(f"https://{domain}{path}")

    urls_to_fetch = [u for u in pages_to_crawl[:max_pages] if u not in crawled]

    def _process_page(url):
        u, content = fetch_page_content(url, timeout=5)
        return url, u, content

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_process_page, u): u for u in urls_to_fetch}
        for future in as_completed(futures):
            target_url = futures[future]
            crawled.add(target_url)
            try:
                target_url, u, content = future.result()
                if not content:
                    continue

                soup = BeautifulSoup(content, "html.parser")

                # 1. mailto: links
                for a in soup.select('a[href^="mailto:"]'):
                    href = a.get("href", "")
                    raw_e = href.replace("mailto:", "").split("?")[0].strip().lower()
                    clean_list = contacts.extract_emails(raw_e)
                    for ce in clean_list:
                        if contacts.domain_of(ce).endswith(domain):
                            published.setdefault(ce, []).append(u or target_url)

                # 2. Text body emails
                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                body_text = soup.get_text("\n")
                for ce in contacts.extract_emails(body_text):
                    if contacts.domain_of(ce).endswith(domain):
                        published.setdefault(ce, []).append(u or target_url)

                # 3. Schema.org people
                extracted = extract_schema_people(soup)
                for p in extracted:
                    if p["email"] and contacts.domain_of(p["email"]).endswith(domain):
                        published.setdefault(p["email"], []).append(f"{u or target_url} (ld+json)")
                    people.append(p)
            except Exception:
                continue

    return {
        "published_emails": published,
        "people": people,
        "crawled_urls": list(crawled),
        "domain": domain,
    }


# ---------------------------------------------------------------- Pattern Inference (Verdict style)
def infer_email_pattern(published_emails: List[str], domain: str) -> Optional[str]:
    """Infer common naming pattern from observed corporate emails ({first}.{last}, {first[0]}{last}, etc.)."""
    patterns: Dict[str, int] = {}
    for em in published_emails:
        if not em.endswith("@" + domain):
            continue
        local = em.split("@")[0].lower()
        if contacts.HR_LOCAL.search(local) or local in COMMON_ROLE_PREFIXES:
            continue

        if "." in local:
            parts = local.split(".")
            if len(parts) == 2:
                if len(parts[0]) == 1 and len(parts[1]) > 1:
                    patterns["{first[0]}.{last}"] = patterns.get("{first[0]}.{last}", 0) + 1
                elif len(parts[0]) > 1 and len(parts[1]) > 1:
                    patterns["{first}.{last}"] = patterns.get("{first}.{last}", 0) + 1
        elif "_" in local:
            patterns["{first}_{last}"] = patterns.get("{first}_{last}", 0) + 1
        else:
            # Single word: first name or first initial + last name
            patterns["{first}"] = patterns.get("{first}", 0) + 1

    if patterns:
        best = max(patterns.items(), key=lambda kv: kv[1])[0]
        return best
    return "{first}.{last}"  # standard industry default


def generate_candidate_emails(name: str, domain: str, pattern: Optional[str] = None) -> List[Dict[str, str]]:
    """Generate candidate addresses for a person based on naming patterns."""
    clean_name = re.sub(r"[^a-zA-Z\s]", "", name).strip().lower()
    parts = clean_name.split()
    if not parts:
        return []

    first = parts[0]
    last = parts[-1] if len(parts) > 1 else ""

    candidates = []
    if last:
        # Prioritize matching pattern
        pats = [
            ("{first}.{last}", f"{first}.{last}@{domain}"),
            ("{first[0]}{last}", f"{first[0]}{last}@{domain}"),
            ("{first}", f"{first}@{domain}"),
            ("{first}{last}", f"{first}{last}@{domain}"),
            ("{first[0]}.{last}", f"{first[0]}.{last}@{domain}"),
            ("{first}_{last}", f"{first}_{last}@{domain}"),
            ("{last}.{first}", f"{last}.{first}@{domain}"),
        ]
        if pattern:
            pats.sort(key=lambda item: 0 if item[0] == pattern else 1)
        for p_name, cand in pats:
            candidates.append({"email": cand, "pattern": p_name})
    else:
        candidates.append({"email": f"{first}@{domain}", "pattern": "{first}"})

    return candidates


# ---------------------------------------------------------------- Deliverability & Verification
def verify_candidate_email(
    email: str,
    mx_hosts: List[str],
    is_catchall: bool = False,
    probe_smtp: bool = True,
    mail_from: str = "verify@localhost",
) -> Dict[str, Any]:
    """Multi-tier email verification: MX, Catch-all, SMTP probe, and HTTPS soft-signals."""
    email = email.strip().lower()
    if not contacts.EMAIL_RE.fullmatch(email):
        return {"status": "bad_syntax", "detail": "Invalid syntax", "confirmed": False}

    if contacts.is_suppressed(email):
        return {"status": "suppressed", "detail": "On suppression/opt-out list", "confirmed": False}

    if not mx_hosts:
        return {"status": "no_mx", "detail": "Domain has no valid MX mail servers", "confirmed": False}

    # 1. HTTPS Soft Signals: check Gravatar
    if check_gravatar(email):
        return {
            "status": "valid",
            "detail": "Confirmed via Gravatar (HTTPS profile exists)",
            "confirmed": True,
            "signal": "gravatar",
        }

    # 2. HTTPS Soft Signals: check M365 GetCredentialType
    m365_result = check_microsoft365(email, mx_hosts)
    if m365_result is True:
        return {
            "status": "valid",
            "detail": "Confirmed via Microsoft 365 (GetCredentialType HTTPS)",
            "confirmed": True,
            "signal": "microsoft365",
        }
    elif m365_result is False:
        return {
            "status": "invalid",
            "detail": "Microsoft 365 indicates user does not exist",
            "confirmed": False,
            "signal": "microsoft365",
        }

    # 3. Catch-all domain check
    if is_catchall:
        return {
            "status": "catch_all",
            "detail": "Domain accepts all addresses (catch-all configuration)",
            "confirmed": False,
        }

    # 4. Built-in SMTP verification with hub caching
    if probe_smtp:
        res = contacts.verify_email(email, mail_from=mail_from)
        confirmed = res == "valid"
        return {"status": res, "detail": f"SMTP probe result: {res}", "confirmed": confirmed}

    return {"status": "unverified", "detail": "MX exists, probe skipped", "confirmed": False}


# ---------------------------------------------------------------- Main Orchestrator
def find_company_emails(
    company: str = "",
    domain: str = "",
    names: Optional[List[str]] = None,
    roles: Optional[List[str]] = None,
    check_prefixes: bool = True,
    crawl_depth: int = 8,
    verify: bool = True,
    limit: int = 20,
) -> Dict[str, Any]:
    """Find and verify company emails from web harvesting, prefix probing, and person patterns."""
    company = (company or "").strip()
    domain = (domain or "").strip().lower()

    # 1. Resolve domain if not given
    if not domain and company:
        domain = contacts.resolve_domain(company)

    if not domain:
        return {
            "success": False,
            "error": f"Could not determine website domain for company '{company}'. Please provide domain explicitly.",
            "emails": [],
        }

    # Strip prefixes like www.
    domain = domain.removeprefix("https://").removeprefix("http://").split("/")[0].removeprefix("www.")

    # 2. Check DNS / MX records
    mx_list = contacts.mx_hosts(domain)
    has_mx = bool(mx_list)

    is_gated = any(any(g in h.lower() for g in ENTERPRISE_GATES) for h in mx_list)

    # 3. Check Catch-All status
    is_catchall = False
    if has_mx and verify:
        rand_addr = f"probe-rnd-{secrets.token_hex(6)}@{domain}"
        probe_res = contacts.smtp_probe(rand_addr, "verify@localhost")
        if probe_res in ("catch_all", "valid"):
            is_catchall = True

    # 4. Harvest published emails from company website
    harvest = harvest_website_emails(domain, max_pages=max(crawl_depth, 3))
    published_dict = harvest.get("published_emails", {})
    site_people = harvest.get("people", [])

    inferred_pattern = infer_email_pattern(list(published_dict.keys()), domain)

    found_emails: List[Dict[str, Any]] = []
    seen_addresses: Set[str] = set()

    def add_result(
        email: str,
        category: str,
        confidence: str,
        source: str,
        role: str = "",
        name: str = "",
        verification: Optional[Dict[str, Any]] = None,
    ):
        em = email.strip().lower()
        if em in seen_addresses or contacts.is_suppressed(em):
            return
        seen_addresses.add(em)
        item = {
            "email": em,
            "type": category,
            "confidence": confidence,
            "source": source,
        }
        if name:
            item["name"] = name
        if role:
            item["role"] = role
        if verification:
            item["verification"] = verification.get("status")
            item["verification_detail"] = verification.get("detail")
            item["confirmed"] = verification.get("confirmed", False)
        found_emails.append(item)

    # Add all directly published emails from site (high confidence)
    for email_addr, sources in published_dict.items():
        v = verify_candidate_email(email_addr, mx_list, is_catchall, probe_smtp=verify) if verify else {"status": "published"}
        local_part = email_addr.split("@")[0].lower()
        is_hr = bool(contacts.HR_LOCAL.search(local_part))
        is_generic = local_part in COMMON_ROLE_PREFIXES
        role_label = "HR / Recruiting" if is_hr else ("Company Inbox" if is_generic else "Published Contact")
        add_result(
            email_addr,
            category="published",
            confidence="high",
            source=f"website ({', '.join(sources[:2])})",
            role=role_label,
            verification=v,
        )

    # 5. Process Names (from input or extracted site people) - Prioritize individual decision-makers & HR contacts
    target_names: List[Tuple[str, str]] = []
    if names:
        for n in names:
            if n.strip():
                target_names.append((n.strip(), "Specified Contact"))

    # Also include extracted people from schema.org or page content
    for p in site_people:
        pname = p.get("name", "")
        pemail = p.get("email", "")
        prole = p.get("role", "Team Member")
        if pemail and contacts.domain_of(pemail).endswith(domain):
            v = verify_candidate_email(pemail, mx_list, is_catchall, probe_smtp=verify) if verify else {"status": "published"}
            add_result(
                pemail,
                category="decision_maker",
                confidence="high",
                source="schema.org person",
                name=pname,
                role=prole,
                verification=v,
            )
        if pname and pname not in [tn[0] for tn in target_names]:
            target_names.append((pname, prole))

    for person_name, person_role in target_names:
        cands = generate_candidate_emails(person_name, domain, pattern=inferred_pattern)
        for cand in cands[:3]:  # Top candidates
            c_email = cand["email"]
            if c_email in seen_addresses:
                continue
            v = verify_candidate_email(c_email, mx_list, is_catchall, probe_smtp=verify) if verify else {"status": "pattern"}
            if v.get("confirmed") or (not verify):
                conf = "high" if v.get("confirmed") else "medium"
                add_result(
                    c_email,
                    category="decision_maker",
                    confidence=conf,
                    source=f"pattern ({cand['pattern']})",
                    name=person_name,
                    role=person_role,
                    verification=v,
                )
            elif v.get("status") == "valid":
                add_result(
                    c_email,
                    category="decision_maker",
                    confidence="high",
                    source=f"pattern ({cand['pattern']})",
                    name=person_name,
                    role=person_role,
                    verification=v,
                )
            elif is_catchall:
                add_result(
                    c_email,
                    category="decision_maker",
                    confidence="medium",
                    source=f"pattern ({cand['pattern']})",
                    name=person_name,
                    role=person_role,
                    verification=v,
                )
            elif v.get("status") not in ("invalid", "suppressed"):
                add_result(
                    c_email,
                    category="decision_maker",
                    confidence="low",
                    source=f"pattern ({cand['pattern']})",
                    name=person_name,
                    role=person_role,
                    verification=v,
                )

    # 6. Check role prefixes (careers, hr, jobs, contact, etc.)
    if check_prefixes and has_mx:
        prefixes_to_check = [f"{p}@{domain}" for p in COMMON_ROLE_PREFIXES if f"{p}@{domain}" not in seen_addresses]
        
        if verify:
            with ThreadPoolExecutor(max_workers=5) as executor:
                v_futures = {
                    executor.submit(verify_candidate_email, p_email, mx_list, is_catchall, True): p_email
                    for p_email in prefixes_to_check
                }
                for fut in as_completed(v_futures):
                    p_email = v_futures[fut]
                    try:
                        v = fut.result()
                    except Exception:
                        v = {"status": "error"}
                    local = p_email.split("@")[0]
                    if v.get("confirmed") or v.get("status") in ("valid", "catch_all"):
                        conf = "high" if v.get("confirmed") else ("medium" if v.get("status") == "catch_all" else "low")
                        add_result(
                            p_email,
                            category="role_inbox",
                            confidence=conf,
                            source="role_prefix_probe",
                            role=f"{local.title()} Inbox",
                            verification=v,
                        )
        else:
            for role_email in prefixes_to_check:
                local = role_email.split("@")[0]
                add_result(
                    role_email,
                    category="role_inbox",
                    confidence="medium",
                    source="role_prefix_generated",
                    role=f"{local.title()} Inbox",
                )

    # Sort results by confidence and category priority:
    # 1. High confidence confirmed / published / decision maker
    # 2. Medium confidence
    # 3. Low confidence
    def _rank_key(x):
        conf_score = {"high": 0, "medium": 1, "low": 2}.get(x.get("confidence", "low"), 3)
        confirmed_score = 0 if x.get("confirmed") else 1
        type_score = {"published": 0, "decision_maker": 1, "role_inbox": 2}.get(x.get("type"), 3)
        return (conf_score, confirmed_score, type_score)

    found_emails.sort(key=_rank_key)

    return {
        "success": True,
        "company": company or domain,
        "domain": domain,
        "has_mx": has_mx,
        "mx_hosts": mx_list[:3],
        "is_catchall": is_catchall,
        "is_enterprise_gated": is_gated,
        "inferred_pattern": inferred_pattern,
        "total_found": len(found_emails),
        "emails": found_emails[:limit],
    }
