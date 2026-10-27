"""Verdict Email Discovery, Verification, and Deliverability Engine.

Directly adapted from omm9846/verdict:
  - Discovery: Web evidence harvesting across 15 standard routes + pattern inference.
  - Verification: SMTP gate, enterprise gateway detection, catch-all detection,
    and HTTPS soft signals (Gravatar + Microsoft 365 GetCredentialType).
  - Domain Audit: MX, SPF, DMARC deliverability score and grade.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import secrets
import smtplib
import socket
import string
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import contacts
import dns.resolver

logger = logging.getLogger("verdict_tool")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
NOISE = ("example.", "gmail.com", "yahoo.", "hotmail.", "outlook.", "icloud.",
         "sentry.io", "wixpress", "domain.com", "email.com", "u003e")

PATHS = ["/", "/team", "/about", "/contact", "/ir", "/leadership", "/company",
         "/about-us", "/press", "/privacy", "/legal", "/terms", "/careers",
         "/foundation", "/impressum"]

ENTERPRISE_GATES = (
    "mimecast", "proofpoint", "barracuda", "pphosted", "messagelabs",
    "symantec", "fireeye", "trendmicro", "forcepoint", "spamtitan",
)

CONSUMER_PROVIDERS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "protonmail.com", "proton.me", "zoho.com",
}

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "throwawaymail.com", "yopmail.com", "sharklasers.com", "dispostable.com",
}

ROLE_LOCALS = {
    "info", "contact", "hello", "admin", "support", "sales", "help",
    "office", "team", "mail", "enquiries", "inquiries", "webmaster",
    "postmaster", "abuse", "security", "privacy", "legal", "billing",
    "noreply", "no-reply", "donotreply", "marketing", "press", "careers",
    "jobs", "hr", "finance", "service",
}


# ---------------------------------------------------------------- Discovery & Harvesting
def _fetch_page(url: str, timeout: int = 5) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text" in content_type or "html" in content_type:
                return resp.read().decode("utf-8", "ignore")
    except Exception:
        pass
    return ""


def harvest_domain(domain: str, max_pages: int = 15) -> Dict[str, int]:
    """Harvest public web evidence on domain across standard routes."""
    found: Dict[str, int] = {}

    def _add(addr: str):
        low = addr.lower().rstrip(".")
        if low.endswith("@" + domain) or low.endswith("." + domain):
            if not any(n in low for n in NOISE):
                found[low] = found.get(low, 0) + 1

    urls = []
    for pth in PATHS[:max_pages]:
        urls.append(f"https://{domain}{pth}")
        urls.append(f"https://www.{domain}{pth}")

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_fetch_page, u): u for u in urls}
        for fut in as_completed(futures):
            try:
                html = fut.result()
                if html:
                    for m in EMAIL_RE.findall(html):
                        _add(m)
            except Exception:
                pass

    return found


def local_pattern(local: str) -> str:
    tokens = re.split(r"[._\-]", local)
    if len(tokens) == 1:
        return "{first}"
    kinds = ["f" if len(t) == 1 else "w" for t in tokens]
    return ".".join(kinds)


def infer_candidates(domain: str, found_emails: Dict[str, int], person: str) -> Tuple[List[str], Dict[str, Any]]:
    parts = person.strip().split()
    if not parts:
        return [], {}
    first = re.sub(r"[^a-zA-Z]", "", parts[0]).lower()
    last = re.sub(r"[^a-zA-Z]", "", parts[-1]).lower() if len(parts) > 1 else ""

    patterns: Dict[str, int] = {}
    for em in found_emails:
        local = em.split("@")[0].lower()
        if local in ROLE_LOCALS:
            continue
        pat = local_pattern(local)
        patterns[pat] = patterns.get(pat, 0) + found_emails[em]

    ranked_patterns = sorted(patterns.items(), key=lambda kv: kv[1], reverse=True)
    inferred_cands = []

    if last:
        for pat, _ in ranked_patterns[:3]:
            if pat == "{first}":
                inferred_cands.append(f"{first}@{domain}")
            elif pat in ("f.w", "f.w.w"):
                inferred_cands.append(f"{first[0]}.{last}@{domain}")
            elif pat == "w.w":
                inferred_cands.append(f"{first}.{last}@{domain}")
            elif pat == "w":
                inferred_cands.append(f"{last}@{domain}")

        # Common defaults
        for fallback in [
            f"{first}.{last}@{domain}",
            f"{first[0]}{last}@{domain}",
            f"{first}@{domain}",
            f"{first[0]}.{last}@{domain}",
            f"{first}_{last}@{domain}",
        ]:
            if fallback not in inferred_cands:
                inferred_cands.append(fallback)
    else:
        inferred_cands.append(f"{first}@{domain}")

    return inferred_cands[:6], {"observed_patterns": patterns, "top_pattern": ranked_patterns[0][0] if ranked_patterns else "none"}


# ---------------------------------------------------------------- Verification
def check_gravatar(email: str) -> bool:
    try:
        md5_hash = hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()
        url = f"https://www.gravatar.com/avatar/{md5_hash}?d=404&s=1"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def check_m365(email: str, mx: str) -> Optional[bool]:
    if not mx or "mail.protection.outlook.com" not in mx.lower():
        return None
    domain = email.split("@")[-1]

    def _query(username: str):
        try:
            body = json.dumps({"Username": username}).encode("utf-8")
            req = urllib.request.Request(
                "https://login.microsoftonline.com/common/GetCredentialType",
                data=body, method="POST",
                headers={"Content-Type": "application/json; charset=UTF-8", "User-Agent": UA, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=4) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
                return data.get("IfExistsResult")
        except Exception:
            return None

    control = f"probe-ctrl-{secrets.token_hex(6)}@{domain}"
    if _query(control) != 1:  # Tenant masks existence
        return None

    res = _query(email)
    if res == 0:
        return True
    if res == 1:
        return False
    return None


def verify_email(email: str, mail_from: str = "probe@localhost") -> Dict[str, Any]:
    """Execute Verdict verification on a specific email address."""
    email = email.strip().lower()
    if "@" not in email:
        return {"email": email, "verdict": "DEAD", "detail": "Invalid syntax", "ships": False}

    local, domain = email.rsplit("@", 1)

    if domain in DISPOSABLE_DOMAINS:
        return {"email": email, "verdict": "RISKY", "detail": "Disposable domain", "ships": False}

    if local.split("+")[0] in ROLE_LOCALS:
        return {"email": email, "verdict": "RISKY", "detail": "Role / shared account", "ships": False}

    mx_list = contacts.mx_hosts(domain)
    if not mx_list:
        return {"email": email, "verdict": "DEAD", "detail": "No MX records", "ships": False}

    mx = mx_list[0]
    low_mx = mx.lower()

    if any(g in low_mx for g in ENTERPRISE_GATES):
        return {"email": email, "verdict": "RISKY", "mx": mx, "detail": f"Enterprise gateway ({low_mx})", "ships": False}

    # Soft signals: Gravatar
    if check_gravatar(email):
        return {"email": email, "verdict": "SEND", "mx": mx, "detail": "Gravatar confirmed (person avatar exists)", "ships": True}

    # Soft signals: M365
    m365_status = check_m365(email, mx)
    if m365_status is True:
        return {"email": email, "verdict": "SEND", "mx": mx, "detail": "Microsoft 365 confirmed (GetCredentialType)", "ships": True}
    elif m365_status is False:
        return {"email": email, "verdict": "DEAD", "mx": mx, "detail": "Microsoft 365 confirmed non-existent", "ships": False}

    # SMTP probe with catch-all detection
    probe_res = contacts.verify_email(email, mail_from=mail_from)
    verdict_map = {
        "valid": ("SEND", "Confirmed via SMTP RCPT TO", True),
        "catch_all": ("CATCHALL", "Domain is catch-all; unconfirmed mailbox", False),
        "invalid": ("DEAD", "Mailbox rejected by server", False),
        "no_mx": ("DEAD", "No mail server", False),
        "unknown": ("PLAUSIBLE" if contacts.is_personal(email) else "UNKNOWN", "Server blocked probe or timed out", False),
    }

    v, detail, ships = verdict_map.get(probe_res, ("UNKNOWN", "Unrecognized probe response", False))
    return {
        "email": email,
        "verdict": v,
        "mx": mx,
        "detail": detail,
        "ships": ships,
    }


# ---------------------------------------------------------------- Deliverability Audit
def audit_domain(domain: str) -> Dict[str, Any]:
    """Audit domain MX, SPF, DMARC deliverability posture."""
    domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0].removeprefix("www.")
    score = 0
    checks = {}

    # 1. MX Check (15 pts)
    mx_list = contacts.mx_hosts(domain)
    if mx_list:
        score += 15
        checks["mx"] = {"status": "pass", "records": mx_list[:3]}
    else:
        checks["mx"] = {"status": "fail", "detail": "No MX records found"}

    # 2. SPF Check (35 pts)
    try:
        answers = dns.resolver.resolve(domain, "TXT", lifetime=4)
        txts = [b"".join(r.strings).decode("utf-8", "ignore") for r in answers]
        spf_recs = [t for t in txts if t.lower().startswith("v=spf1")]
        if spf_recs:
            score += 35
            checks["spf"] = {"status": "pass", "record": spf_recs[0][:100]}
        else:
            checks["spf"] = {"status": "fail", "detail": "No SPF TXT record"}
    except Exception:
        checks["spf"] = {"status": "fail", "detail": "DNS lookup failed"}

    # 3. DMARC Check (35 pts)
    try:
        dmarc_ans = dns.resolver.resolve(f"_dmarc.{domain}", "TXT", lifetime=4)
        dmarc_txts = [b"".join(r.strings).decode("utf-8", "ignore") for r in dmarc_ans]
        if dmarc_txts:
            score += 35
            checks["dmarc"] = {"status": "pass", "record": dmarc_txts[0][:100]}
        else:
            checks["dmarc"] = {"status": "fail", "detail": "No _dmarc TXT record"}
    except Exception:
        checks["dmarc"] = {"status": "fail", "detail": "No DMARC record found"}

    # 4. Enterprise Gateway / Posture (15 pts)
    is_gated = any(any(g in m.lower() for g in ENTERPRISE_GATES) for m in mx_list)
    if not is_gated and mx_list:
        score += 15
        checks["gateway"] = {"status": "pass", "detail": "Standard deliverable MX configuration"}
    else:
        checks["gateway"] = {"status": "warn", "detail": "Behind enterprise gateway or no MX"}

    grade = "A" if score >= 85 else ("B" if score >= 65 else ("C" if score >= 40 else "F"))
    return {
        "success": True,
        "tool": "verdict",
        "domain": domain,
        "deliverability_score": score,
        "grade": grade,
        "checks": checks,
    }


# ---------------------------------------------------------------- Verdict Tool Dispatcher
def run_verdict(
    action: str = "discover",
    domain: str = "",
    email: str = "",
    person: str = "",
    mail_from: str = "probe@localhost",
) -> Dict[str, Any]:
    """Execute Verdict tool action (discover | verify | audit)."""
    action = (action or "discover").lower().strip()

    if action == "audit":
        if not domain and email:
            domain = email.split("@")[-1]
        if not domain:
            return {"success": False, "error": "domain is required for 'audit'"}
        return audit_domain(domain)

    if action == "verify":
        if not email:
            return {"success": False, "error": "email is required for 'verify'"}
        v_res = verify_email(email, mail_from=mail_from)
        v_res["success"] = True
        v_res["tool"] = "verdict"
        return v_res

    # action == "discover"
    if not domain and email:
        domain = email.split("@")[-1]
    if not domain:
        return {"success": False, "error": "domain is required for 'discover'"}

    domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0].removeprefix("www.")
    harvested = harvest_domain(domain)

    cands = []
    pattern_meta = {}
    if person:
        cands, pattern_meta = infer_candidates(domain, harvested, person)

    candidate_verdicts = []
    for cand in cands:
        candidate_verdicts.append(verify_email(cand, mail_from=mail_from))

    return {
        "success": True,
        "tool": "verdict",
        "domain": domain,
        "harvested_emails_count": len(harvested),
        "published_emails": sorted(harvested.keys()),
        "target_person": person,
        "inferred_candidates": candidate_verdicts,
        "patterns": pattern_meta,
    }
