"""Hiring-post analysis: regex gate -> (batched) LLM extraction + preference match -> profile hard rules.

Pipeline adapted from sanchittanwar7/job-search-agent (regex gate, then an LLM that extracts company / role / location
and decides whether the post matches the user's preferences), with three changes:
  * one LLM call per batch of posts instead of two calls per post (Kiro and cloud APIs bill per request);
  * the user's hard rules from profile.yaml (title include/exclude, max years, locations) are re-checked on the LLM
    output, so a model mistake can never widen the search beyond what the profile allows;
  * when no LLM is configured or the call fails, the same rules run on the raw text (the previous behaviour).

Everything here is profile-driven: nothing assumes a particular role, city or country.
"""
from __future__ import annotations

import re

import llm

# A post must show hiring intent to be considered at all (cheap, < 1 ms).
HIRING_INTENT = [re.compile(p, re.I) for p in (
    r"\b(?:we'?re|we\s+are|now|actively|urgently|immediately)\s+hiring\b", r"\bhiring\b",
    r"\b(?:looking|searching)\s+for\s+(?:an?\s+)?(?:talented|skilled|experienced|passionate|junior|senior|fresher)?",
    r"\bjoin\s+(?:our|the|my)\s+team\b", r"\b(?:open|new)\s+(?:role|position|vacanc|opening|req)",
    r"\bjob\s+(?:opening|post(?:ing)?|opportunit)", r"\bvacanc(?:y|ies)\b", r"\bopenings?\b",
    r"\bimmediate\s+joiners?\b", r"\bwalk-?in\b", r"\b(?:share|send|drop|mail)\s+(?:your\s+)?(?:updated\s+)?(?:cv|resume)",
    r"\binterested\s+candidates?\b", r"\bpositions?\s+(?:open|available)\b")]
# First-person job seekers, new-job announcements and referral requests are not vacancies.
NOT_VACANCY = re.compile(
    r"referral request|#?open\s*to\s*work|\bi\s+am\s+looking\s+for\s+(?:a\s+)?(?:new\s+)?(?:job|role|opportunit)|"
    r"\bi'?m\s+looking\s+for\s+(?:a\s+)?(?:new\s+)?(?:job|role|opportunit)|seeking\s+(?:a\s+)?(?:new\s+)?(?:job|role|opportunit)|"
    r"please\s+refer\s+me|\bi'?ve\s+joined\b|\bi\s+have\s+joined\b|\bexcited\s+to\s+(?:announce\s+(?:that\s+)?i|join)\b|"
    r"\bstarting\s+(?:a\s+)?new\s+(?:position|role|job)\b", re.I)
SCAM = re.compile(r"work from home opportunit|100% work from home|earn from home|registration fee|joining fee|"
                  r"pay(?:ment)? (?:for|to get) (?:the )?job|investment required", re.I)
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)", re.I)
REMOTE = re.compile(r"\b(remote|work from home|wfh|anywhere|pan[\s-]?india)\b", re.I)


def gate(text: str) -> tuple:
    if NOT_VACANCY.search(text or ""):
        return False, "candidate / announcement post"
    if SCAM.search(text or ""):
        return False, "spam / fee scheme"
    if not any(rx.search(text or "") for rx in HIRING_INTENT):
        return False, "no hiring intent"
    return True, ""


def min_years(text: str):
    """Smallest minimum-experience figure stated, 0 for fresher posts, None if not stated."""
    if re.search(r"\bfreshers?\b|\b0\s*(?:-|–|to)\s*\d+\s*(?:years?|yrs?)", text or "", re.I):
        return 0
    mins = [int(m.group(1)) for m in YEARS.finditer(text or "") if int(m.group(1)) <= 30]
    return min(mins) if mins else None


def max_years_allowed(profile: dict) -> int:
    nums = re.findall(r"\d+", str(profile["search"].get("experience_years_filter", "")))
    tol = int((profile.get("autopilot") or {}).get("max_years_tolerance", 1))
    return (int(nums[-1]) if nums else 30) + tol


def _locations(profile: dict) -> list:
    return [str(l).strip() for l in profile["search"].get("locations") or [] if str(l).strip()]


def location_ok(profile: dict, text: str) -> bool:
    """True when the text names one of the profile locations, a remote variant, or no location at all."""
    locs = _locations(profile)
    if not locs or any(l.lower() == "all" for l in locs):
        return True
    t = text or ""
    aliases = {"bangalore": "bengaluru", "bengaluru": "bangalore", "gurgaon": "gurugram", "gurugram": "gurgaon",
               "bombay": "mumbai", "mumbai": "bombay"}
    for l in locs:
        for name in {l.lower(), aliases.get(l.lower(), l.lower())}:
            if re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", t, re.I):
                return True
    if REMOTE.search(t) and any(re.match(r"remote|anywhere", l, re.I) for l in locs):
        return True
    return False


def rules_verdict(profile: dict, text: str, role: str = "") -> tuple:
    """Profile hard rules. `role` is the extracted title when an LLM produced one, else the whole text is used."""
    s = profile["search"]
    head = (role or (text or "")[:3000])
    if not re.search(s["title_include"], head, re.I):
        return False, "role not in search.title_include"
    if s.get("title_exclude") and re.search(s["title_exclude"], role or head[:400], re.I):
        return False, "role matches search.title_exclude"
    my = min_years(text)
    if my is not None and my > max_years_allowed(profile):
        return False, f"needs {my}+ years"
    return True, "ok"


# ---------------------------------------------------------------- LLM stage
def _prefs(profile: dict) -> str:
    s, ps = profile["search"], profile.get("post_scan") or {}
    companies = ps.get("companies") or ["all"]
    return "\n".join([
        f"- target roles: {', '.join(s.get('keywords') or []) or 'any'}",
        f"- acceptable locations: {', '.join(_locations(profile)) or 'any'} (a remote role or no stated location is acceptable)",
        f"- candidate experience: {profile['applicant'].get('experience_years', '?')} years; reject posts that require more than "
        f"{max_years_allowed(profile)} years",
        f"- reject seniority/titles matching this regex: {s.get('title_exclude') or '(none)'}",
        f"- companies: {', '.join(companies)}",
    ])


def build_prompt(profile: dict, texts: list) -> str:
    data = "\n".join(f"<post i=\"{i}\">\n{t[:1500]}\n</post>" for i, t in enumerate(texts))
    return (
        "Classify each LinkedIn post below for a job seeker.\n\n"
        "For every post return an object: {\"i\": <int>, \"is_job_posting\": <bool>, \"company\": <str|null>, "
        "\"role\": <str|null>, \"location\": <str|null>, \"min_years\": <int|null>, \"asks_resume_by_email\": <bool>, "
        "\"match\": <bool>, \"reason\": <short string>}.\n"
        "is_job_posting is true ONLY when a company or recruiter advertises an open vacancy others can apply to. It is false "
        "for people looking for work, people announcing their own new job, referral requests, advice, courses, ads and "
        "paid-placement schemes.\n"
        "role is the advertised job title (normalise Sr. -> Senior, Jr. -> Junior). location: normalise remote/WFH to "
        "\"Remote\". min_years: the minimum experience the post asks for (0 for freshers).\n"
        "match is true only if is_job_posting is true AND the post satisfies ALL of these preferences:\n"
        f"{_prefs(profile)}\n\n"
        "Reply with ONLY a JSON array with one object per post, in order.\n\nDATA:\n" + data)


def _llm_batch(profile: dict, texts: list, c: dict) -> list:
    out = llm.chat_json(build_prompt(profile, texts), c)
    if isinstance(out, dict):
        out = out.get("results") or out.get("posts") or [out]
    by_i = {}
    for o in out if isinstance(out, list) else []:
        if isinstance(o, dict) and isinstance(o.get("i"), int):
            by_i[o["i"]] = o
    return [by_i.get(i) for i in range(len(texts))]


def _str(v) -> str:
    return v.strip()[:120] if isinstance(v, str) else ""


def analyze(profile: dict, texts: list, log=print) -> list:
    """-> one verdict dict per text: {match, reason, via, company, role, location, min_years, asks_email}."""
    c = llm.cfg(profile)
    use_llm, why = llm.available(c)
    results = [None] * len(texts)
    todo = []
    for i, t in enumerate(texts):
        ok, reason = gate(t)
        if not ok:
            results[i] = {"match": False, "reason": reason, "via": "gate"}
        else:
            todo.append(i)
    if use_llm and todo:
        bs = max(1, int(c.get("batch_size", 8)))
        for k in range(0, len(todo), bs):
            idx = todo[k:k + bs]
            try:
                verdicts = _llm_batch(profile, [texts[i] for i in idx], c)
            except llm.LLMError as e:
                log(f"  llm ({c['provider']}) failed, using rules for {len(idx)} posts: {str(e)[:120]}")
                verdicts = [None] * len(idx)
            for i, v in zip(idx, verdicts):
                if not v:
                    continue
                role, years = _str(v.get("role")), v.get("min_years")
                r = {"via": f"llm:{c['provider']}", "company": _str(v.get("company")), "role": role,
                     "location": _str(v.get("location")), "min_years": years if isinstance(years, int) else None,
                     "asks_email": bool(v.get("asks_resume_by_email"))}
                if not v.get("is_job_posting"):
                    r.update(match=False, reason="llm: not a job posting")
                elif not v.get("match"):
                    r.update(match=False, reason="llm: " + _str(v.get("reason")))
                else:
                    # hard rules always win over the model
                    ok, reason = rules_verdict(profile, texts[i], role)
                    if ok and r["min_years"] is not None and r["min_years"] > max_years_allowed(profile):
                        ok, reason = False, f"needs {r['min_years']}+ years"
                    if ok and r["location"] and not location_ok(profile, r["location"]) and not location_ok(profile, texts[i]):
                        ok, reason = False, "location outside search.locations"
                    r.update(match=ok, reason=("llm: " + _str(v.get("reason"))) if ok else reason)
                results[i] = r
    elif todo and not use_llm:
        log(f"  llm off ({why}); using profile rules")
    for i in todo:
        if results[i] is None:
            t = texts[i]
            ok, reason = rules_verdict(profile, t)
            if ok and not location_ok(profile, t):
                ok, reason = False, "location outside search.locations"
            results[i] = {"match": ok, "reason": reason, "via": "rules", "min_years": min_years(t)}
    return results
