"""The uploaded resume is the single source of truth for scoring, email attachments and the apply bots.

* `save_upload()` writes it to applicant.resume_path (atomically), keeps the last few versions in data/resumes/,
  remembers the original filename (used as the email attachment name) and clears the text cache.
* `info()` tells the app exactly which file the server is using (size, hash, upload time, first line, skills).
* `suggest_profile()` reads the resume and proposes profile values: contact details, skills and, with an LLM,
  the job titles to search for. `apply_suggestion()` merges them so job search follows the uploaded resume.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

import core

HISTORY_KEEP = 5
PLACEHOLDERS = {"your name", "you@example.com", "+91xxxxxxxxxx", "https://linkedin.com/in/your-handle",
                "https://github.com/your-handle", "you@gmail.com", ""}


def history_dir() -> Path:
    d = core.ROOT / "data" / "resumes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or "resume").stem).strip("._") or "resume"
    return stem[:60] + ".pdf"


def save_upload(profile: dict, data: bytes, filename: str) -> dict:
    import composer
    dest = Path(profile["applicant"]["resume_path"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".upload")
    tmp.write_bytes(data)
    tmp.replace(dest)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    (history_dir() / f"{ts}_{_safe_name(filename)}").write_bytes(data)
    for old in sorted(history_dir().glob("*.pdf"))[:-HISTORY_KEEP]:
        old.unlink(missing_ok=True)
    core.kv_set("resume:meta", {"original_filename": _safe_name(filename), "uploaded_at": core.now_iso(),
                                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    composer._cache.clear()
    return info(profile)


def attachment_name(profile: dict) -> str:
    meta = core.kv_get("resume:meta") or {}
    if meta.get("original_filename"):
        return meta["original_filename"]
    name = re.sub(r"[^A-Za-z0-9]+", "_", profile["applicant"].get("name") or "").strip("_")
    return f"{name}_Resume.pdf" if name and name.lower() != "your_name" else "Resume.pdf"


def info(profile: dict) -> dict:
    import composer
    p = Path(profile["applicant"]["resume_path"])
    if not p.exists():
        return {"exists": False, "path": str(p), "hint": "upload your resume PDF in Settings > Resume"}
    data = p.read_bytes()
    text = composer.resume_text(str(p))
    meta = core.kv_get("resume:meta") or {}
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    return {"exists": True, "path": str(p), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()[:16],
            "modified": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            "uploaded_at": meta.get("uploaded_at"), "original_filename": meta.get("original_filename"),
            "attachment_name": attachment_name(profile), "first_line": first[:120], "text_chars": len(text),
            "readable": len(text) > 200, "skills": composer.skills_in(text, profile),
            "history": [f.name for f in sorted(history_dir().glob("*.pdf"), reverse=True)]}


# ---------------------------------------------------------------- resume -> profile
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"\+?\d[\d\s-]{8,14}\d")


def section_skills(text: str) -> list:
    """Items listed under a 'Skills' heading (comma / pipe / bullet separated), no LLM needed."""
    out, on = [], False
    for line in text.splitlines():
        s = line.strip()
        if re.fullmatch(r"(?:technical\s+|key\s+|core\s+)?skills(?:\s*(?:&|and)\s*\w+)?:?", s, re.I):
            on = True
            continue
        if on and s and s.isupper() and len(s) > 4:
            break
        if on and s:
            body = s.split(":", 1)[1] if ":" in s and len(s.split(":", 1)[0]) < 40 else s
            for item in re.split(r"[,|•·;/]| - ", body):
                item = item.strip(" .\t")
                if 2 <= len(item) <= 30 and not re.search(r"\d{4}", item):
                    out.append(item)
    return list(dict.fromkeys(out))[:60]


def suggest_profile(profile: dict, use_llm: bool = True) -> dict:
    import composer
    import llm
    text = composer.resume_text(profile["applicant"]["resume_path"])
    if not text.strip():
        return {"error": "resume has no readable text (scanned image PDF?)"}
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    sugg = {"applicant": {}, "search": {}, "via": "rules"}
    if lines and len(lines[0]) <= 40 and not EMAIL.search(lines[0]):
        sugg["applicant"]["name"] = lines[0].title() if lines[0].isupper() else lines[0]
    if (m := EMAIL.search(text)):
        sugg["applicant"]["email"] = m.group(0)
    if (m := PHONE.search(text)):
        sugg["applicant"]["phone"] = re.sub(r"[\s-]", "", m.group(0))
    if (m := re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w-]+", text, re.I)):
        sugg["applicant"]["linkedin"] = "https://" + re.sub(r"^https?://", "", m.group(0))
    if (m := re.search(r"(?:https?://)?github\.com/[\w-]+", text, re.I)):
        sugg["applicant"]["github"] = "https://" + re.sub(r"^https?://", "", m.group(0))
    sugg["search"]["skills"] = section_skills(text)
    c = llm.cfg(profile)
    if use_llm and llm.available(c)[0]:
        prompt = ("Read this resume and return ONE JSON object: {\"headline\": str, \"experience_years\": number, "
                  "\"current_location\": str|null, \"skills\": [str, max 40], \"job_titles\": [5-10 job titles this person "
                  "should search for, matching their level], \"title_keywords\": [6-15 lowercase single words or short phrases "
                  "that appear in suitable job titles], \"exclude_title_words\": [lowercase seniority words to exclude, "
                  "e.g. senior, lead, manager, only if the person is junior]}.\n\nDATA:\n<resume>\n" + text[:8000] + "\n</resume>")
        try:
            out = llm.chat_json(prompt, c)
            if isinstance(out, dict):
                sugg["via"] = f"llm:{c['provider']}"
                if out.get("headline"):
                    sugg["applicant"]["headline"] = str(out["headline"])[:120]
                if isinstance(out.get("experience_years"), (int, float)):
                    sugg["applicant"]["experience_years"] = str(out["experience_years"])
                if out.get("current_location"):
                    sugg["applicant"]["current_location"] = str(out["current_location"])[:60]
                skills = [str(s)[:40] for s in out.get("skills") or [] if s]
                sugg["search"]["skills"] = list(dict.fromkeys(skills + sugg["search"]["skills"]))[:60]
                titles = [str(t)[:60] for t in out.get("job_titles") or [] if t][:10]
                if titles:
                    sugg["search"]["keywords"] = titles
                words = [re.escape(str(w).lower())[:40] for w in out.get("title_keywords") or [] if w][:15]
                if words:
                    sugg["search"]["title_include"] = "|".join(words)
                excl = [str(w).lower() for w in out.get("exclude_title_words") or [] if w][:15]
                if excl:
                    sugg["search"]["title_exclude"] = "|".join(r"\b" + re.escape(w) + r"\b" for w in excl)
        except llm.LLMError as e:
            sugg["llm_error"] = str(e)[:200]
    return sugg


def apply_suggestion(profile: dict, sugg: dict, replace_search: bool) -> dict:
    """Merge a suggestion into the profile dict (returns what changed). Contact fields are only filled when they still
    hold the example placeholders; search.skills is always refreshed; keywords / title rules only with replace_search."""
    changed = {}
    a = profile.setdefault("applicant", {})
    for k, v in (sugg.get("applicant") or {}).items():
        if v and str(a.get(k, "")).strip().lower() in PLACEHOLDERS and str(a.get(k, "")) != str(v):
            a[k] = v
            changed[f"applicant.{k}"] = v
    s = profile.setdefault("search", {})
    ss = sugg.get("search") or {}
    if ss.get("skills") and ss["skills"] != s.get("skills"):
        s["skills"] = ss["skills"]
        changed["search.skills"] = len(ss["skills"])
    if replace_search:
        for k in ("keywords", "title_include", "title_exclude"):
            if ss.get(k) and ss[k] != s.get(k):
                s[k] = ss[k]
                changed[f"search.{k}"] = ss[k]
    return changed


def write_profile(profile: dict) -> None:
    import yaml
    target = core.PROFILE_PATH
    if target.name == "profile.example.yaml":  # never overwrite the template
        target = target.with_name("profile.yaml")
    tmp = target.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(profile, sort_keys=False, allow_unicode=True))
    tmp.replace(target)
    core.PROFILE_PATH = target
