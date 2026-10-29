"""Scan LinkedIn for recent HIRING posts that fit the profile and publish an HR email or an application link.

Runs headless on the saved LinkedIn session. For each run:
  1. collect posts from LinkedIn content search (queries built from search.keywords x search.locations, rotated so
     successive runs cover every combination) and, if post_scan.include_feed is true, from the home feed;
  2. drop posts already seen in earlier runs (posts_seen table);
  3. analyse the rest with post_analyzer: regex gate -> LLM (kiro / ollama / openai, batched) -> profile hard rules,
     or profile rules alone when no LLM is configured;
  4. store published HR emails in hr_contacts (kind='post'; 'high' confidence when the post asks for CVs by email)
     and apply links in data/post_leads.jsonl, and send a Telegram alert per match if configured.
Nothing is emailed from here.
"""
import asyncio, hashlib, json, os, re, sys, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests  # noqa: E402

import contacts  # noqa: E402
import core  # noqa: E402
import notify  # noqa: E402
import post_analyzer  # noqa: E402

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/156.0.0.0 Safari/537.36")
ASKS_EMAIL = re.compile(
    r"(share|send|mail|email|drop|forward|reach\s*out|write\s*to)\w*\s+(your\s+|us\s+|the\s+)?(updated\s+)?(cv|resume|profile)s?|"
    r"(cv|resume|profile)s?\s+(to|at|on)\b|"
    r"email\s+(their\s+)?(cv|resume|profile)s?|"
    r"(write|reach\s*out)\s+to\s+us\s+at\b|"
    r"interested\s+candidates?\s+(can\s+)?(share|send|email|mail|drop|reach|write)|"
    r"drop\s+(your\s+)?profiles?\s+to\b",
    re.I,
)
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
SKIP_LINK = re.compile(r"linkedin\.com/(in|company|feed/hashtag|search|school|showcase|groups)|lnkd\.in/?$|^mailto:", re.I)
# hosts that are never a job application page (chat invites, social media, link shorteners we could not open)
NOT_JOB_HOST = re.compile(r"(^|\.)(whatsapp\.com|wa\.me|t\.me|telegram\.(me|org)|instagram\.com|facebook\.com|youtube\.com|"
                          r"youtu\.be|twitter\.com|x\.com|lnkd\.in|linkedin\.com|bit\.ly|tinyurl\.com)$", re.I)
NOT_A_DOMAIN = re.compile(r"^(b|m)\.?(tech|e|sc|com|ca|ba)$|^(mca|bca|mba)$", re.I)  # "B.Tech" in a post became a link


def clean_link(url: str, http=None) -> str:
    """Real target of a link in a post: unwrap linkedin.com/safety/go/?url=..., open lnkd.in short links, and return ""
    for links that cannot be an application page (WhatsApp channel, LinkedIn group, "http://B.Tech", ...)."""
    from urllib.parse import parse_qs, urlparse
    for _ in range(3):
        u = urlparse(url or "")
        host = (u.hostname or "").lower()
        if host.endswith("linkedin.com") and u.path.startswith("/safety/go"):
            url = (parse_qs(u.query).get("url") or [""])[0]
            continue
        if host == "lnkd.in" and len(u.path) > 2:
            try:
                r = (http or requests.get)(url, headers={"User-Agent": "Mozilla/5.0 Chrome/130"}, timeout=10, allow_redirects=True)
                m = re.search(r'<a[^>]+class="[^"]*artdeco-button[^"]*"[^>]+href="([^"]+)"', r.text or "")
                url = m.group(1) if m else (r.url if "lnkd.in" not in str(r.url) else "")
            except Exception:  # noqa: BLE001 - shortener unreachable: drop the link
                return ""
            continue
        break
    u = urlparse(url or "")
    host = (u.hostname or "").lower()
    if not url.startswith("http") or "." not in host or NOT_A_DOMAIN.match(host) or NOT_JOB_HOST.search(host):
        return ""
    return url
PERSONAL = ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com", "icloud.com", "proton.me")
DEFAULTS = {"max_queries": 12, "scrolls": 30, "max_posts_per_query": 80, "include_feed": False, "feed_scrolls": 12,
            "date_posted": "past-week",
            "extra_queries": [], "companies": ["all"], "alert_telegram": True}


def scan_cfg(p: dict) -> dict:
    c = dict(DEFAULTS)
    c.update(p.get("post_scan") or {})
    return c


def relevant(text: str, p: dict) -> tuple:
    """Rules-only verdict (no LLM): hiring gate + profile title / years / location rules."""
    ok, why = post_analyzer.gate(text)
    if not ok:
        return False, why
    ok, why = post_analyzer.rules_verdict(p, text)
    if not ok:
        return False, why
    if not post_analyzer.location_ok(p, text):
        return False, "location outside search.locations"
    return True, "ok"


def min_years(text: str):
    return post_analyzer.min_years(text)


def city_of(text: str, p: dict) -> str:
    for loc in p["search"].get("locations") or []:
        if re.search(r"(?<![\w])" + re.escape(str(loc)) + r"(?![\w])", text, re.I):
            return str(loc)
    return ""


def company_of(text: str, author: str, dom: str) -> str:
    for line in text.splitlines()[:6]:
        m = re.search(r"(?:at|company|organization|client|hiring for)\s*[:\-|]\s*([A-Za-z0-9&., ]{3,40})", line, re.I)
        if m:
            c = m.group(1).strip()
            if not re.search(r"\b(we|our|the|urgent|immediate)\b", c, re.I):
                return c
    if dom and dom not in PERSONAL:
        return dom.split(".")[0].title()
    return author or "Hiring Company"


def role_of(text: str, p: dict) -> str:
    for line in text.splitlines():
        m = re.search(r"(?:role|position|designation|hiring(?: for)?)\s*[:\-|]\s*(.{4,60})", line, re.I)
        if m:
            r = re.split(r"\s\d|[.|,;!]|\s-\s", m.group(1))[0]
            return re.sub(r"[^\w /&()+-]", "", r).strip()[:60]
    for kw in p["search"].get("keywords") or []:
        if re.search(re.escape(kw), text, re.I):
            return kw
    return (p["search"].get("keywords") or ["Open role"])[0]


def fingerprint(text: str) -> str:
    return hashlib.sha1(re.sub(r"\W+", " ", text[:400].lower()).strip().encode()).hexdigest()


def already_seen(fp: str) -> bool:
    with core.db() as c:
        return c.execute("SELECT 1 FROM posts_seen WHERE fp=?", (fp,)).fetchone() is not None


def mark_seen(fp: str, verdict: str) -> None:
    with core.db() as c:
        c.execute("INSERT OR IGNORE INTO posts_seen(fp,at,verdict) VALUES(?,?,?)", (fp, core.now_iso(), verdict[:200]))


EXTRACT_JS = r"""() => [...document.querySelectorAll('h2')].filter(h => h.innerText.trim() === 'Feed post').map(h => {
  let c = h.parentElement; while (c && c.innerText.length < 200) c = c.parentElement;
  if (!c) return null;
  const links = [...c.querySelectorAll('a[href]')].map(a => a.href);
  const author = (c.innerText.split('\n').map(s => s.trim()).filter(Boolean)[1] || '').slice(0, 80);
  return {text: c.innerText.slice(0, 4000), links: [...new Set(links)], author};
}).filter(Boolean)"""
EXPAND_JS = """() => { for (const b of document.querySelectorAll('button, span[role=button]'))
    if (/^(…\\s*)?(see )?more$/i.test(b.innerText.trim())) b.click(); }"""


COUNT_JS = "() => [...document.querySelectorAll('h2')].filter(h => h.innerText.trim() === 'Feed post').length"
# LinkedIn renders results inside an inner scroller and loads 3-4 more posts each time the LAST post is scrolled into
# view; wheel events over the window stop working after the first batch (that is why a scan used to see 2-12 posts).
SCROLL_JS = """() => { const hs = [...document.querySelectorAll('h2')].filter(h => h.innerText.trim() === 'Feed post');
  if (hs.length) hs[hs.length - 1].scrollIntoView({block: 'end'}); window.scrollBy(0, 3000); }"""


async def _collect(page, url: str, scrolls: int, label: str, max_posts: int = 80) -> list:
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(7000)
    if any(x in page.url for x in ("login", "authwall", "checkpoint")):
        raise PermissionError("LinkedIn session expired - run login_platform('linkedin')")
    last, stale = 0, 0
    for _ in range(max(1, scrolls)):
        n = await page.evaluate(COUNT_JS)
        if n >= max_posts:
            break
        stale = stale + 1 if n == last else 0
        if stale >= 4:  # nothing new after four more scrolls: the results are exhausted
            break
        last = n
        await page.evaluate(SCROLL_JS)
        await page.keyboard.press("End")
        await page.wait_for_timeout(1800)
    await page.evaluate(EXPAND_JS)
    await page.wait_for_timeout(1200)
    posts = (await page.evaluate(EXTRACT_JS))[:max_posts]
    for x in posts:
        x["source"] = label
    return posts


async def scan(queries, sc: dict | None = None):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(core.ROOT / ".browsers"))
    from playwright.async_api import async_playwright

    p = core.load_profile()
    sc = sc or scan_cfg(p)
    raw, seen_now = [], set()
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(core.ROOT / ".profiles" / "linkedin"), headless=True, channel="chromium", user_agent=UA,
            args=["--disable-blink-features=AutomationControlled"], viewport={"width": 1366, "height": 900})
        page = ctx.pages[0]
        ctx.on("page", lambda x: asyncio.ensure_future(x.close()) if x is not page else None)
        targets = [("feed", "https://www.linkedin.com/feed/", sc["feed_scrolls"])] if sc["include_feed"] else []
        targets += [(q, "https://www.linkedin.com/search/results/content/?keywords=" + urllib.parse.quote(q)
                     + f"&datePosted=%22{sc['date_posted']}%22&sortBy=%22date_posted%22", sc["scrolls"]) for q in queries]
        for label, url, scrolls in targets:
            try:
                posts = await _collect(page, url, scrolls, label, int(sc.get("max_posts_per_query", 80)))
            except PermissionError as e:
                print(str(e), flush=True)
                break
            except Exception as e:  # noqa: BLE001
                print(f"[{label}] error: {str(e)[:120]}", flush=True)
                continue
            new = 0
            for post in posts:
                fp = fingerprint(post["text"])
                if fp in seen_now or already_seen(fp):
                    continue
                seen_now.add(fp)
                post["fp"] = fp
                raw.append(post)
                new += 1
            print(f"[{label}] {len(posts)} posts, {new} not seen before", flush=True)
        await ctx.close()
    return process(p, raw, sc)


LAST_LEADS: list = []  # leads found by the latest process() call (read by auto_apply)


def process(p: dict, posts: list, sc: dict) -> dict:
    """Analyse collected posts and store contacts / leads. Separate from the browser part so it is testable."""
    lead_file = core.ROOT / "data" / "post_leads.jsonl"
    lead_file.parent.mkdir(parents=True, exist_ok=True)
    verdicts = post_analyzer.analyze(p, [x["text"] for x in posts], log=lambda m: print(m, flush=True))
    kept = stored = alerts = 0
    leads = []
    LAST_LEADS.clear()
    me = p["applicant"]["email"].lower()
    for post, v in zip(posts, verdicts):
        mark_seen(post.get("fp") or fingerprint(post["text"]), ("match " if v["match"] else "skip ") + v["reason"])
        if not v["match"]:
            continue
        kept += 1
        role = v.get("role") or role_of(post["text"], p)
        clean_text = contacts.deobfuscate(post["text"])
        mails = {m.lower().rstrip(".") for m in EMAIL.findall(clean_text)}
        mails |= {l[7:].split("?")[0].lower() for l in post.get("links", []) if l.lower().startswith("mailto:")}
        mails = {m for m in mails if not m.endswith((".png", ".jpg")) and m != me}
        asks = bool(ASKS_EMAIL.search(clean_text)) or bool(v.get("asks_email"))
        print(f"  ✔ match [{role}] via {v['via']} by {post.get('author', '')}: {v['reason'][:100]}", flush=True)
        src = f"linkedin post by {post.get('author', '')} ({post.get('source', '')})"
        stored_here = []
        for m in sorted(mails):
            dom = m.split("@")[1]
            if dom in PERSONAL and not asks:
                continue
            name = post.get("author", "").strip()
            company = v.get("company") or company_of(post["text"], name, dom)
            ver = contacts.verify_email(m)
            with core.db() as c:
                cur = c.execute(
                    "INSERT OR IGNORE INTO hr_contacts(email,name,role,company,domain,city,confidence,note,kind,found_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (m, name, role, company, dom, v.get("location") or city_of(post["text"], p),
                     "high" if (asks or ver in ("valid", "catch_all")) else "medium",
                     "public hiring post: " + src[:200], "post", core.now_iso()))
            if cur.rowcount:
                stored += 1
                stored_here.append(m)
                print(f"  ✉️  {m}  [{role}]  asks_for_resume={asks}", flush=True)
        ext = [l for l in post.get("links", []) if l.startswith("http") and not SKIP_LINK.search(l)]
        if ext or mails:
            lead = {"role": role, "company": v.get("company", ""), "location": v.get("location", ""), "author": post.get("author", ""),
                    "links": ext[:5], "emails": sorted(mails), "query": post.get("source", ""), "via": v["via"],
                    "reason": v["reason"], "found_at": core.now_iso(), "excerpt": post["text"][:400]}
            leads.append(lead)
            LAST_LEADS.append(lead)
            with open(lead_file, "a") as f:
                f.write(json.dumps(lead, ensure_ascii=False) + "\n")
        if sc.get("alert_telegram", True) and notify.post_alert(p, {**v, "role": role}, post.get("author", ""),
                                                                 post["text"], sorted(mails), ext):
            alerts += 1
    summary = {"analysed": len(posts), "matched": kept, "new_hr_emails": stored, "leads": len(leads), "alerts_sent": alerts,
               "by": sorted({v["via"] for v in verdicts}) if verdicts else []}
    print("DONE: " + json.dumps(summary), flush=True)
    return summary


def default_queries(p: dict) -> list:
    """Every keyword x location (+ a generic 'hiring <keyword>' query), plus post_scan.extra_queries."""
    s, sc = p["search"], scan_cfg(p)
    kws = [k for k in s.get("keywords") or [] if k]
    locs = [l for l in s.get("locations") or [] if l and str(l).lower() != "all"]
    qs = [f"hiring {k} {l}" for k in kws for l in locs] + [f"hiring {k}" for k in kws]
    return list(dict.fromkeys(list(sc.get("extra_queries") or []) + qs))


def queries_for_run(p: dict, sc: dict | None = None) -> list:
    return core.rotate("post_queries", default_queries(p), int((sc or scan_cfg(p))["max_queries"]))


def auto_apply(p: dict, leads: list, dry: bool, log=print) -> dict:
    """Act on what the scan found: email the HR addresses the posts published, and apply through their company links."""
    import autopilot
    import server  # lazy: server imports the other hub modules
    out = {"dry_run": dry}
    ids = []
    for lead in leads:
        link = next((u for u in (clean_link(l) for l in lead.get("links", []) if l.startswith("http")) if u), "")
        if not link:
            continue  # no application page in the post: the HR email below (if any) is the channel
        autopilot.save_job({"source": "post", "ext_id": "", "url": link, "title": lead.get("role") or "Hiring post",
                            "company": lead.get("company", ""), "location": lead.get("location", ""),
                            "posted": lead.get("found_at", ""), "description": lead.get("excerpt", ""), "apply_type": "external"})
        with core.db() as c:
            row = c.execute("SELECT id, status FROM jobs WHERE url=?", (link,)).fetchone()
            if row and row["status"] in ("new", "skip", "no_contact"):
                c.execute("UPDATE jobs SET status='scored', score=MAX(COALESCE(score,0),70), reason='matched a hiring post' WHERE id=?",
                          (row["id"],))
        if row:
            ids.append(row["id"])
    if ids:
        out["jobs"] = server.apply_to_jobs({"job_ids": ids[:10], "dry_run": dry})
        log(f"[auto-apply] {len(ids)} post links -> {out['jobs'].get('run_id') or out['jobs'].get('error')}")
    if any(l.get("emails") for l in leads):
        out["emails"] = server.send_hr_emails({"dry_run": dry, "limit": 10})
        log(f"[auto-apply] HR emails: {json.dumps(out['emails'], default=str)[:300]}")
    return out


if __name__ == "__main__":
    prof = core.load_profile()
    opts = json.loads(os.environ.get("JOBHUB_POST_OPTS") or "{}")
    cfg = scan_cfg(prof)
    for src, dst in (("max_posts", "max_posts_per_query"), ("scrolls", "scrolls"), ("max_queries", "max_queries")):
        if opts.get(src):
            cfg[dst] = int(opts[src])
    qs = json.loads(os.environ["JOBHUB_POST_QUERIES"]) if os.getenv("JOBHUB_POST_QUERIES") else queries_for_run(prof, cfg)
    summary = asyncio.run(scan(qs, cfg))
    if opts.get("auto_apply"):
        print("AUTO-APPLY " + json.dumps(auto_apply(prof, list(LAST_LEADS), bool(opts.get("dry_run", True))), default=str), flush=True)
