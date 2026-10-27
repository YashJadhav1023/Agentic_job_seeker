"""Scan LinkedIn content search for recent HIRING posts that publish an HR email or an application link.

Runs headless on the saved LinkedIn session. Keeps only posts that
  * are hiring posts (not candidates asking for referrals / open-to-work),
  * match the profile title filter (DevOps / Cloud / SRE ...) and not the exclude filter (Senior, Lead ...),
  * accept the candidate's experience level (fresher, or a minimum of <= MAX_MIN_YEARS years).
Emails go into hr_contacts (kind='post', confidence 'high' when the post asks for resumes/CVs by email).
Apply links go into data/post_leads.jsonl for review. Nothing is sent from here.
"""
import asyncio, json, os, re, sys, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import contacts  # noqa: E402
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(core.ROOT / ".browsers"))
from playwright.async_api import async_playwright  # noqa: E402


MAX_MIN_YEARS = 2
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/156.0.0.0 Safari/537.36")
HIRING = re.compile(r"\b(we'?re hiring|we are hiring|hiring|job opening|openings?|vacanc|immediate joiners?|walk-?in|"
                    r"looking for (an? )?(devops|cloud|sre|aws|azure))", re.I)
NOT_HIRING = re.compile(r"referral request|open to work|#opentowork|i am looking for|i'm looking for|seeking (a )?(new )?"
                        r"(job|role|opportunit)|please refer me|my profile", re.I)
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
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)", re.I)
SKIP_LINK = re.compile(r"linkedin\.com/(in|company|feed/hashtag|search|school|showcase|groups)|lnkd\.in/?$|^mailto:", re.I)


def min_years(text: str):
    """Smallest minimum-experience figure stated in the post, or 0 for fresher posts, or None if not stated."""
    if re.search(r"\bfreshers?\b|\b0\s*(-|–|to)\s*\d\s*(years?|yrs?)", text, re.I):
        return 0
    mins = [int(m.group(1)) for m in YEARS.finditer(text) if int(m.group(1)) <= 30]
    return min(mins) if mins else None


def relevant(text: str, p: dict) -> tuple:
    head = text[:3000]
    if NOT_HIRING.search(text):
        return False, "candidate post"
    if not HIRING.search(text):
        return False, "not a hiring post"
    inc, exc = p["search"]["title_include"], p["search"]["title_exclude"]
    if not re.search(inc, head, re.I):
        return False, "role not relevant"
    if re.search(exc, head, re.I):
        return False, "seniority excluded"
    loc = re.search(r"\b(india|pune|mumbai|navi mumbai|thane|hyderabad|bengaluru|bangalore|pan india|remote)\b", text, re.I)
    abroad = re.search(r"\b(usa|u\.s\.|united states|uk|london|sweden|stockholm|germany|dubai|uae|singapore|vietnam|"
                       r"canada|australia|w2|c2c|us it recruiter)\b", text, re.I)
    if abroad and not re.search(r"\bindia\b", text, re.I):
        return False, "outside India"
    if not loc:
        return False, "location not stated"
    if re.search(r"work from home opportunit|100% work from home|earn from home|registration fee|business development", text, re.I):
        return False, "spam / WFH scheme"
    if re.search(r"\b(trainer|training program|course|internship fee|salesforce|copado|integration developer)\b", head, re.I):
        return False, "not a target role"
    my = min_years(text)
    if my is not None and my > MAX_MIN_YEARS:
        return False, f"needs {my}+ yrs"
    return True, "ok"


def city_of(text: str) -> str:
    m = re.search(r"\b(pune|mumbai|navi mumbai|thane|hyderabad|bengaluru|bangalore|remote)\b", text, re.I)
    if m:
        c = m.group(1).title()
        return "Bangalore" if c == "Bengaluru" else c
    return "Pune"


def company_of(text: str, author: str, dom: str) -> str:
    for line in text.splitlines()[:6]:
        m = re.search(r"(?:at|company|organization|client|hiring for)\s*[:\-|]\s*([A-Za-z0-9&., ]{3,40})", line, re.I)
        if m:
            c = m.group(1).strip()
            if not re.search(r"\b(we|our|the|urgent|immediate)\b", c, re.I):
                return c
    if dom and dom not in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com"):
        return dom.split(".")[0].title()
    return author or "Hiring Company"


def role_of(text: str) -> str:
    for line in text.splitlines():
        m = re.search(r"(?:role|position|designation|hiring(?: for)?)\s*[:\-|]\s*(.{4,60})", line, re.I)
        if m:
            r = re.split(r"\s\d|[.|,;!]|\s-\s", m.group(1))[0]
            return re.sub(r"[^\w /&()+-]", "", r).strip()[:60]
    m = re.search(r"((?:junior |associate |sr\.? )?(?:aws |azure |gcp |cloud )?(?:devops|cloud|site reliability|sre|"
                  r"kubernetes|platform|infrastructure)\s*(?:engineer|administrator|admin|intern)?)", text, re.I)
    return m.group(1).strip().title() if m else "DevOps / Cloud Engineer"



EXTRACT_JS = r"""() => [...document.querySelectorAll('h2')].filter(h => h.innerText.trim() === 'Feed post').map(h => {
  let c = h.parentElement; while (c && c.innerText.length < 200) c = c.parentElement;
  if (!c) return null;
  const links = [...c.querySelectorAll('a[href]')].map(a => a.href);
  const author = (c.innerText.split('\n').map(s => s.trim()).filter(Boolean)[1] || '').slice(0, 80);
  return {text: c.innerText.slice(0, 4000), links: [...new Set(links)], author};
}).filter(Boolean)"""


async def scan(queries, per_query_scrolls=5):
    p = core.load_profile()
    seen, kept, stored, leads = set(), 0, 0, []
    lead_file = core.ROOT / "data" / "post_leads.jsonl"
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(core.ROOT / ".profiles" / "linkedin"), headless=True, channel="chromium", user_agent=UA,
            args=["--disable-blink-features=AutomationControlled"], viewport={"width": 1366, "height": 900})
        page = ctx.pages[0]
        ctx.on("page", lambda x: asyncio.ensure_future(x.close()) if x is not page else None)
        for q in queries:
            url = ("https://www.linkedin.com/search/results/content/?keywords=" + urllib.parse.quote(q)
                   + "&datePosted=%22past-week%22&sortBy=%22date_posted%22")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(7000)
                if any(x in page.url for x in ("login", "authwall", "checkpoint")):
                    print("LinkedIn session expired - run login_platform('linkedin')", flush=True)
                    break
                for _ in range(per_query_scrolls):
                    await page.mouse.wheel(0, 2500); await page.wait_for_timeout(1300)
                await page.evaluate("""() => { for (const b of document.querySelectorAll('button, span[role=button]'))
                    if (/^(…\\s*)?(see )?more$/i.test(b.innerText.trim())) b.click(); }""")
                await page.wait_for_timeout(1200)
                posts = await page.evaluate(EXTRACT_JS)
            except Exception as e:
                print(f"[{q}] search error: {str(e)[:120]}", flush=True)
                continue
            new = 0
            for post in posts:
                key = post["text"][:300]
                if key in seen:
                    continue
                seen.add(key); new += 1
                ok, why = relevant(post["text"], p)
                if not ok:
                    continue
                kept += 1
                print(f"  ✔ relevant [{role_of(post['text'])}] by {post['author']}: "
                      + re.sub(r"\s+", " ", post["text"][:220]), flush=True)
                role = role_of(post["text"])
                clean_text = contacts.deobfuscate(post["text"])
                mails = {m.lower().rstrip(".") for m in EMAIL.findall(clean_text)}
                mails |= {l[7:].split("?")[0].lower() for l in post["links"] if l.lower().startswith("mailto:")}
                mails = {m for m in mails if not m.endswith((".png", ".jpg")) and m != p["applicant"]["email"].lower()}
                asks = bool(ASKS_EMAIL.search(clean_text))
                src = f"linkedin post by {post['author']} (search: {q})"
                for m in mails:
                    dom = m.split("@")[1]
                    personal = dom in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com")
                    if personal and not asks:
                        continue
                    name = post.get("author", "").strip()
                    city = city_of(post["text"])
                    company = company_of(post["text"], name, dom)
                    v = contacts.verify_email(m)
                    with core.db() as c:
                        cur = c.execute(
                            "INSERT OR IGNORE INTO hr_contacts(email,name,role,company,domain,city,confidence,note,kind,found_at)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (m, name, role, company, dom, city,
                             "high" if (asks or v in ("valid", "catch_all")) else "medium",
                             "public hiring post: " + src[:200], "post", core.now_iso()))
                    if cur.rowcount:
                        stored += 1
                        print(f"  ✉️  {m}  [{role}]  asks_for_resume={asks}  ({name})", flush=True)
                ext = [l for l in post["links"] if l.startswith("http") and not SKIP_LINK.search(l)]
                if ext:
                    lead = {"role": role, "author": post["author"], "links": ext[:5], "emails": sorted(mails),
                            "query": q, "found_at": core.now_iso(), "excerpt": post["text"][:400]}
                    leads.append(lead)
                    with open(lead_file, "a") as f:
                        f.write(json.dumps(lead, ensure_ascii=False) + "\n")
                    print(f"  🔗 apply link: {ext[0][:100]}  [{role}]", flush=True)
            print(f"[{q}] {len(posts)} posts, {new} new", flush=True)
        await ctx.close()
    print(f"DONE: {len(seen)} posts scanned, {kept} relevant, {stored} new HR emails stored, {len(leads)} apply-link leads "
          f"(saved to {lead_file})", flush=True)


def default_queries(p):
    roles = [
        "devops engineer", "cloud engineer", "aws devops", "azure devops",
        "site reliability engineer", "platform engineer", "cloud support engineer",
        "kubernetes engineer", "junior devops", "devops fresher"
    ]
    cities = [c.lower() for c in p["search"]["locations"]] + ["remote"]
    qs = [f"hiring {r} {c} email resume" for r in roles[:5] for c in cities[:4]]
    qs += [f"hiring {r} share cv" for r in roles[:6]]
    qs += [f"hiring {r} send resume" for r in roles[:6]]
    qs += [f"hiring {r} email cv {c}" for r in roles[:4] for c in cities[:3]]
    return qs



if __name__ == "__main__":
    prof = core.load_profile()
    qs = json.loads(os.environ["JOBHUB_POST_QUERIES"]) if os.getenv("JOBHUB_POST_QUERIES") else default_queries(prof)
    asyncio.run(scan(qs))
