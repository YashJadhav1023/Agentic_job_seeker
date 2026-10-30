"""job-hub composer: read a job description, match it to the resume, write a professional application email.

Deterministic on purpose: every claim in the email is lifted from the resume (bullets are quoted from the PDF text)
or from profile.yaml, so it cannot invent experience. An optional local LLM (Ollama) may polish the wording, but its
output is discarded unless it adds no new numbers, links or addresses (see `polish`).
The resume PDF is always attached by core.send_via_gmail.
"""
import json, re, subprocess
from pathlib import Path

# canonical skill -> regex of spellings. Used on both the resume and the JD.
SKILLS = {
    "Kubernetes": r"kubernetes|\bk8s\b|\baks\b|\beks\b|\bgke\b", "Docker": r"docker|container(?:s|ization|isation)?",
    "Terraform": r"terraform|\bopentofu\b", "Ansible": r"ansible", "Helm": r"\bhelm\b", "Jenkins": r"jenkins",
    "Azure DevOps": r"azure devops|\bado\b|azure pipelines", "GitHub Actions": r"github actions",
    "GitLab CI": r"gitlab(?: ci)?", "CI/CD": r"ci\s*/?\s*cd|continuous (?:integration|delivery|deployment)|pipelines?",
    "Azure": r"\bazure\b", "AWS": r"\baws\b|amazon web services|\bec2\b|\bs3\b", "GCP": r"\bgcp\b|google cloud",
    "Linux": r"\blinux\b|ubuntu|rhel|red hat", "Bash": r"\bbash\b|shell script", "Python": r"\bpython\b",
    "Git": r"\bgit\b", "Prometheus": r"prometheus", "Grafana": r"grafana", "OpenTelemetry": r"opentelemetry|\botel\b",
    "Monitoring & Observability": r"monitoring|observability|application insights|azure monitor|cloudwatch|log analytics|\bkql\b",
    "ELK": r"\belk\b|elasticsearch|kibana|logstash", "Argo CD": r"argo\s*cd|gitops", "Nginx": r"nginx",
    "IaC": r"infrastructure as code|\biac\b|arm templates?|bicep|cloudformation",
    "Key Vault / Secrets": r"key vault|secrets? manage|vault\b|ssm parameter", "Security": r"devsecops|trivy|sonarqube|vulnerabilit|\biam\b|rbac",
    "FinOps": r"finops|cost optimi[sz]ation|cost reduction", "Networking": r"networking|tcp/ip|subnet|vlan|\bvpn\b|firewall|\bvpc\b|ccna|\bdns\b",
    "SRE": r"\bsre\b|site reliability|incident|on-?call|\bslo\b", "Redis": r"redis", "PostgreSQL": r"postgres(?:ql)?",
    "Serverless": r"serverless|azure functions|lambda", "Microservices": r"microservices?", "Helm Charts": r"helm chart",
}
SKILL_RX = {k: re.compile(v, re.I) for k, v in SKILLS.items()}
SKILL_WEIGHT = {"Kubernetes": 3, "Terraform": 3, "Azure": 3, "AWS": 3, "CI/CD": 2, "Docker": 2, "Azure DevOps": 2,
                "GitHub Actions": 2, "Jenkins": 2, "Ansible": 2, "Linux": 2, "Python": 2, "Monitoring & Observability": 2, "Prometheus": 2, "Grafana": 2}
GENERIC = {"CI/CD", "Monitoring & Observability", "IaC", "Security", "Networking", "SRE", "Microservices", "Serverless", "FinOps"}
BAD_SUBJECT = re.compile(r"[\r\n]")
_cache = {}


# ---------------------------------------------------------------- resume
def resume_text(path: str) -> str:
    p = Path(path)
    key = (str(p), p.stat().st_mtime if p.exists() else 0, p.stat().st_size if p.exists() else 0)
    if key not in _cache:
        text = ""
        # 1. Try pymupdf (pure Python, installed via requirements.txt, zero external binary dependency)
        try:
            import pymupdf
            doc = pymupdf.open(str(p))
            text = "\n".join(page.get_text() for page in doc)
        except Exception:
            try:
                import fitz
                doc = fitz.open(str(p))
                text = "\n".join(page.get_text() for page in doc)
            except Exception:
                pass

        # 2. Subprocess pdftotext fallback if pymupdf was unavailable
        if not text.strip():
            try:
                r = subprocess.run(["pdftotext", str(p), "-"], capture_output=True, text=True, timeout=30)
                text = r.stdout if r.returncode == 0 else ""
            except Exception:
                pass

        _cache[key] = text
    return _cache[key]


_custom_rx: dict = {}


def custom_skills(profile: dict | None) -> dict:
    """search.skills from the profile (filled from the resume) as {name: regex}, so scoring works for any field,
    not only the built-in cloud/DevOps vocabulary."""
    out = {}
    for s in ((profile or {}).get("search") or {}).get("skills") or []:
        name = str(s).strip()
        if not name or name in SKILL_RX or len(name) > 40:
            continue
        if name not in _custom_rx:
            _custom_rx[name] = re.compile(r"(?<![\w])" + re.escape(name).replace(r"\ ", r"\s+") + r"(?![\w])", re.I)
        out[name] = _custom_rx[name]
    return out


def skills_in(text: str, profile: dict | None = None) -> list:
    rx = {**SKILL_RX, **custom_skills(profile)}
    return [k for k, r in rx.items() if r.search(text or "")]


HEADINGS = ("PROFESSIONAL", "TECHNICAL", "FEATURED", "EDUCATION", "CERTIFICATIONS")


BULLET = ("•", "●", "▪", "■", "◦", "–", "- ", "* ", "➢", "✓")


def resume_bullets(text: str, profile: dict | None = None) -> list:
    """[{text, skills}] - each resume bullet, label prefix dropped ('Keyless CI/CD Automation: Built ...' -> 'Built ...')."""
    bullets, cur = [], None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(BULLET):
            if cur:
                bullets.append(cur)
            cur = s.lstrip("•●▪■◦–-*➢✓ ").strip()
        elif cur is not None and s and not s.upper().startswith(HEADINGS) and not re.match(r".+\|.+", s) and len(cur) < 400:
            cur += " " + s
        else:
            if cur:
                bullets.append(cur)
            cur = None
    if cur:
        bullets.append(cur)
    out = []
    for b in bullets:
        if len(b) < 40:
            continue
        head, sep, rest = b.partition(": ")
        if sep and len(head) < 48 and rest[:1].isupper():
            b = rest
        out.append({"text": b.strip(), "skills": skills_in(b, profile)})
    return out


def resume_profile(profile: dict) -> dict:
    t = resume_text(profile["applicant"]["resume_path"])
    return {"text": t, "skills": set(skills_in(t, profile)), "bullets": resume_bullets(t, profile)}


def sanitize_posting(text: str) -> tuple:
    """Sanitize job posting text using ai-job-search posting_guard."""
    try:
        import sys
        from pathlib import Path
        aijs_tools = Path(__file__).resolve().parent.parent / "ai-job-search" / "tools"
        if str(aijs_tools) not in sys.path:
            sys.path.insert(0, str(aijs_tools))
        import posting_guard
        return posting_guard.guard(text or "", "text")
    except Exception:
        return text or "", {}


# ---------------------------------------------------------------- JD parsing
def parse_jd(title: str, company: str, jd: str, profile: dict | None = None) -> dict:
    jd = jd or ""
    clean_jd, _ = sanitize_posting(jd)
    jd = clean_jd
    skills = skills_in(jd, profile)
    yrs = None
    m = re.search(r"(\d{1,2})\s*(?:\+|-|–|to)?\s*(\d{1,2})?\s*(?:\+)?\s*(?:years|yrs)", jd, re.I)
    if m:
        yrs = int(m.group(1))
    subj = None
    m = re.search(r"(?:subject(?:\s+line)?|mention|quote|title)[^\"“”'\n]{0,50}[\"“']([^\"”'\n]{3,90})[\"”']", jd, re.I)
    if m:
        subj = m.group(1).strip()
    ref = re.search(r"\b(?:job\s*(?:id|code)|req(?:uisition)?\s*(?:id|no\.?|number)|ref(?:erence)?\s*(?:no\.?|number|id|code))\s*[:#\-]\s*([A-Z0-9][A-Z0-9\-_/]{2,20})", jd, re.I)
    return {"title": (title or "the open position").strip(), "company": (company or "").strip(), "skills": skills,
            "min_years": yrs, "required_subject": subj, "ref": ref.group(1) if ref else None}


# ---------------------------------------------------------------- composing
def _pick_bullets(rp: dict, jd_skills: list, n: int = 3) -> list:
    want = set(jd_skills)
    scored = []
    for i, b in enumerate(rp["bullets"]):
        s = sum(SKILL_WEIGHT.get(k, 1) for k in b["skills"] if k in want)
        scored.append((s, -i, b["text"]))
    scored.sort(reverse=True)
    picked = [t for s, _, t in scored if s > 0][:n]
    return picked or [b["text"] for b in rp["bullets"][:2]]


def _join(items: list) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _short(bullet: str, limit: int = 230) -> str:
    if len(bullet) <= limit:
        return bullet
    cut = bullet[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "..."


def compose(profile: dict, job: dict, source: str = "", recruiter: str = "", cold: bool = False) -> dict:
    """Build {subject, body, matched, bullets} for a job dict (title, company, description). Pure text, no sending."""
    a = profile["applicant"]
    rp = resume_profile(profile)
    jd = parse_jd(job.get("title", ""), job.get("company", ""), job.get("description", ""), profile)
    matched = [s for s in jd["skills"] if s in rp["skills"]]
    matched.sort(key=lambda k: -SKILL_WEIGHT.get(k, 1))
    top = matched[:3]
    bullets = [_short(b) for b in _pick_bullets(rp, matched)]
    exp = profile.get("experience") or {}
    cur_title = exp.get("current_title") or ""
    cur_co = (exp.get("current_company") or "").rstrip(".")
    name_first = (recruiter or "").split(" ")[0]
    greet = f"Dear {name_first}," if name_first.isalpha() else "Dear Hiring Team,"
    where = f" as advertised on {source}" if source else ""
    role = jd["title"]
    co = jd["company"] or "your organisation"
    label = exp.get("role_label") or a.get("headline") or cur_title or "professional"
    label = label.split("|")[0].strip()
    article = "an" if label[:1].lower() in "aeiou" else "a"
    intro = (f"I am writing to apply for the {role} position at {co}{where}. I am {article} {label}"
             + (f" based in {a['current_location']}" if a.get("current_location") else "")
             + (f", currently working as {cur_title} at {cur_co}" if cur_co and cur_title and cur_co.lower() != "your company" else "") + ".")
    fit = (f" Your requirements around {_join(top)} match the work I do." if top else
           " The role aligns closely with the experience described in my resume.")
    lines = [greet, "", intro + fit, "", "Relevant experience from my resume:"]
    lines += [f"- {b}" for b in bullets]
    covered = {k for b in rp["bullets"] if _short(b["text"]) in bullets for k in b["skills"]} | set(top)
    extra = [s for s in matched if s not in covered and s not in GENERIC][:4]
    if extra:
        lines += ["", f"My toolset also includes {_join(extra)}, which your description mentions."]
    c = profile.get("compensation", {})
    avail = []
    if c.get("notice_period_days") == 0:
        avail.append("I can join immediately")
    if str(a.get("willing_to_relocate", "")).lower().startswith("y"):
        avail.append("I am open to relocation")
    if avail:
        lines += ["", " and ".join(avail) + "."]
    lines += ["", "My resume is attached for your review"
              + (f" (LinkedIn: {a['linkedin']}" + (f", GitHub: {a['github']})" if a.get("github") else ")") if a.get("linkedin") else "") + ".",
              f"I would welcome the opportunity to discuss how I can contribute to {co}."]
    if cold:
        lines += ["", "If I have reached the wrong contact, please let me know and I will not follow up."]
    lines += ["", "Thank you for your time and consideration.", "", "Kind regards,", a["name"], f"{a['phone']} | {a['email']}"]
    body = "\n".join(lines)

    if jd["required_subject"] and not BAD_SUBJECT.search(jd["required_subject"]):
        subject = jd["required_subject"]
    else:
        tag = f" ({', '.join(top)})" if top else ""
        ref = f" [{jd['ref']}]" if jd["ref"] else ""
        subject = f"Application for {role}{ref} - {a['name']}{tag}"[:150]
    missing = [s for s in jd["skills"] if s not in rp["skills"]]
    return {"subject": subject, "body": body, "matched": matched, "bullets": bullets, "jd": jd,
            "pitch": bullets[:3],      # 3 resume bullets that best match this JD (quoted from the resume)
            "missing": missing}        # JD keywords NOT in the resume: shown to you, never claimed in the email


# ---------------------------------------------------------------- optional local-LLM polish (Ollama), fact-guarded
def _facts(s: str) -> set:
    return set(re.findall(r"\d[\d,.%+]*", s)) | set(re.findall(r"https?://\S+|[\w.+-]+@[\w-]+\.[\w.]+", s))


def polish_ok(original: str, polished: str) -> bool:
    """Accept an LLM rewrite only if it invents no numbers/links/addresses and keeps a similar length."""
    if not polished or not (0.7 * len(original) <= len(polished) <= 1.3 * len(original)):
        return False
    return _facts(polished) <= _facts(original)


def polish(body: str, url: str = "http://127.0.0.1:11434", model: str = "") -> str:
    if not model:
        return body
    import requests
    try:
        r = requests.post(url + "/api/generate", timeout=90, json={
            "model": model, "stream": False,
            "prompt": "Improve the tone and flow of this job application email. Keep every fact, number, name and link "
                      "exactly as is; add nothing new; keep the same structure and bullet list. Return only the email body.\n\n" + body})
        out = (r.json().get("response") or "").strip()
        return out if polish_ok(body, out) else body
    except Exception:  # noqa: BLE001 - Ollama not running: keep the deterministic version
        return body


def _identity(profile: dict) -> str:
    """'<title> at <company>' or the profile headline, e.g. 'Data Analyst at Acme' - nothing hardcoded."""
    exp, a = profile.get("experience") or {}, profile.get("applicant") or {}
    title = exp.get("current_title") or exp.get("role_label") or a.get("headline") or ""
    co = (exp.get("current_company") or "").rstrip(".")
    if co and co.lower() != "your company" and title:
        return f"{title.split('/')[0].strip()} at {co}"
    return title.split("|")[0].strip() or "professional"


def compose_linkedin_pitch(profile: dict, jd_text: str = "", role: str = "", company: str = "", recruiter: str = "") -> str:
    """Short, tailored LinkedIn connection note (<= 300 chars) built from the profile and the resume skills the JD asks for."""
    name_first = (recruiter or "").strip().split(" ")[0]
    greet = f"Hi {name_first}," if name_first.isalpha() else "Hello,"
    role_str = role.strip() or ((profile.get("search") or {}).get("keywords") or ["this"])[0]
    co_str = f" at {company.strip()}" if company else ""
    try:
        rp_skills = resume_profile(profile)["skills"]
    except Exception:  # noqa: BLE001 - no resume yet
        rp_skills = set()
    jd_sk = skills_in(jd_text, profile) if jd_text else []
    top = [k for k in jd_sk if k in rp_skills][:3] or sorted(rp_skills, key=lambda k: -SKILL_WEIGHT.get(k, 1))[:3]
    skills = f" working with {_join(top)}" if top else ""
    c = profile.get("compensation") or {}
    join = " I can join immediately and" if c.get("notice_period_days") == 0 else ""
    me = _identity(profile)
    pitch = (f"{greet} I came across the {role_str} role{co_str}. I'm a {me}{skills}.{join} would love to connect "
             f"to discuss how I can contribute.").replace(". would", ". I would")
    if len(pitch) > 300:
        pitch = f"{greet} Saw the {role_str} opening{co_str}. I'm a {me}{skills}. Would love to connect."
    if len(pitch) > 300:
        pitch = pitch[:297] + "..."
    return pitch
