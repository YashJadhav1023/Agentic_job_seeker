"""MailScout email finder tool wrapper.

Directly utilizes batuhanaky/mailscout (installed via PyPI: mailscout).
Generates name variants, common role prefixes, checks catch-all domains,
and validates email deliverability over SMTP.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import contacts

logger = logging.getLogger("mailscout_tool")


def run_mailscout(
    domain: str,
    names: Optional[List[str]] = None,
    check_prefixes: bool = True,
    check_variants: bool = True,
    check_catchall: bool = True,
    normalize: bool = True,
    smtp_timeout: int = 2,
    num_threads: int = 5,
) -> Dict[str, Any]:
    """Execute MailScout email discovery on a domain."""
    try:
        from mailscout import Scout
    except ImportError:
        return {
            "success": False,
            "error": "mailscout package is not installed. Please install with 'pip install mailscout'.",
        }

    domain = (domain or "").strip().lower()
    if not domain:
        return {"success": False, "error": "domain is required"}

    domain = domain.removeprefix("https://").removeprefix("http://").split("/")[0].removeprefix("www.")

    # Check MX records
    mx_list = contacts.mx_hosts(domain)
    if not mx_list:
        return {
            "success": False,
            "domain": domain,
            "error": f"Domain '{domain}' has no valid MX records and cannot receive mail.",
            "valid_emails": [],
        }

    scout = Scout(
        check_variants=check_variants,
        check_prefixes=check_prefixes,
        check_catchall=check_catchall,
        normalize=normalize,
        num_threads=num_threads,
        smtp_timeout=max(1, min(smtp_timeout, 10)),
    )

    is_catchall = False
    if check_catchall:
        try:
            is_catchall = scout.check_email_catchall(domain)
        except Exception as e:
            logger.warning("Catchall check failed: %s", e)

    valid_emails = []
    generated_variants = []
    generated_prefixes = []

    # 1. Names variants
    if names:
        try:
            for n in names:
                parts = n.strip().split()
                if parts:
                    vars_ = scout.generate_email_variants(parts, domain, normalize=normalize)
                    generated_variants.extend(vars_)
            if not is_catchall:
                valid_emails = scout.find_valid_emails(domain, names)
        except Exception as e:
            logger.warning("Name variant check error: %s", e)

    # 2. Prefixes
    if check_prefixes:
        try:
            generated_prefixes = scout.generate_prefixes(domain)
            if not names and not is_catchall:
                valid_emails = scout.find_valid_emails(domain)
        except Exception as e:
            logger.warning("Prefix check error: %s", e)

    # Format result list
    email_items = []
    for em in valid_emails:
        email_items.append({
            "email": em,
            "deliverable": True,
            "verification": "smtp_valid",
            "suppressed": contacts.is_suppressed(em),
        })

    return {
        "success": True,
        "tool": "mailscout",
        "domain": domain,
        "mx_hosts": mx_list[:3],
        "is_catchall": is_catchall,
        "valid_emails": email_items,
        "total_valid": len(valid_emails),
        "generated_prefixes_count": len(generated_prefixes),
        "generated_variants_count": len(generated_variants),
        "sample_variants": generated_variants[:12],
        "sample_prefixes": generated_prefixes[:12],
    }
