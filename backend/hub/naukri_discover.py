"""Read-only Naukri discovery: search result cards -> each job page -> JSON line per job (with full JD text).

Uses the saved Naukri browser profile (.profiles/naukri) in a hidden Chromium with the same UA settings the apply bot
uses (Naukri serves 'Access Denied' to a mismatching UA). It never clicks Apply.
Usage: naukri_discover.py '<json params>'   params: queries [[keyword, location],...], per_query, max_total, skip_urls, age,
       experience (candidate years), pages (result pages per query, default 2)
Output: one line per job:  JOB {json}
"""
import json, os, random, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".browsers"))
from playwright.sync_api import sync_playwright  # noqa: E402


def chrome_major() -> str:
    exe = next((ROOT / ".browsers").glob("chromium-*/chrome-linux*/chrome"), None)
    try:
        return re.search(r"(\d+)\.", subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=10).stdout).group(1)
    except Exception:  # noqa: BLE001
        return "156"


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def main(params):
    skip = set(params.get("skip_urls", []))
    if not skip:
        try:
            sys.path.insert(0, str(ROOT / "hub"))
            import core
            with core.db() as conn:
                skip = {r[0] for r in conn.execute("SELECT url FROM jobs WHERE source='naukri'")}
        except Exception as e:
            log(f"naukri_discover: note: could not query existing jobs from db: {e}")
    per_query, max_total, age = params.get("per_query", 8), params.get("max_total", 30), params.get("age", 7)
    experience = int(params.get("experience", 0))
    found = 0
    inc = re.compile(params["include"], re.I) if params.get("include") else None
    exc = re.compile(params["exclude"], re.I) if params.get("exclude") else None
    ua = f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_major()}.0.0.0 Safari/537.36"
    sys.path.insert(0, str(ROOT / "hub"))
    import moli_client
    with sync_playwright() as p:
        ctx = moli_client.sync_moli_context(p, platform="naukri", user_agent=ua)
        if not ctx:
            ctx = p.chromium.launch_persistent_context(
                str(ROOT / ".profiles" / "naukri"), headless=True, channel="chromium", user_agent=ua,
                args=["--disable-blink-features=AutomationControlled"], viewport={"width": 1280, "height": 800})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for kw, loc in params["queries"]:
            if found >= max_total:
                break
            slug = re.sub(r"[^a-z0-9]+", "-", kw.lower()).strip("-")
            lslug = re.sub(r"[^a-z0-9]+", "-", loc.lower()).strip("-")
            cards, blocked = [], False
            for pg in range(1, int(params.get("pages", 2)) + 1):  # page 2 only when page 1 had too few new jobs
                if len(cards) >= per_query:
                    break
                suffix = "" if pg == 1 else f"-{pg}"
                url = f"https://www.naukri.com/{slug}-jobs-in-{lslug}{suffix}?jobAge={age}&experience={experience}"
                try:
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_timeout(3500)
                    if "Access Denied" in page.title():
                        blocked = True
                        break
                    for c in page.query_selector_all("div.srp-jobtuple-wrapper, article.jobTuple")[:25]:
                        a = c.query_selector("a.title")
                        if not a or len(cards) >= per_query:
                            continue
                        t = a.inner_text().strip()
                        href = (a.get_attribute("href") or "").split("?")[0]
                        if not href or href in skip or any(x["url"] == href for x in cards):
                            continue  # known job: does not use up this query's budget
                        if (inc and not inc.search(t)) or (exc and exc.search(t)):
                            continue  # irrelevant title: do not even open the page
                        comp = c.query_selector("a.comp-name")
                        lo = c.query_selector("span.locWdth, li.location, span.loc")
                        cards.append({"url": href, "title": t, "company": comp.inner_text().strip() if comp else "",
                                      "location": lo.inner_text().strip() if lo else loc})
                except Exception as e:  # noqa: BLE001
                    log(f"search failed {kw}/{loc} p{pg}: {type(e).__name__}")
                    break
            if blocked:
                log("blocked: Naukri returned Access Denied")
                print("BLOCKED naukri access denied", flush=True)
                break
            log(f"{kw} / {loc}: {len(cards)} new cards")
            for card in cards:
                if found >= max_total:
                    break
                if not card["url"] or card["url"] in skip:
                    continue
                try:
                    page.wait_for_timeout(int(random.uniform(2500, 5000)))
                    page.goto(card["url"], wait_until="domcontentloaded")
                    page.wait_for_timeout(2500)
                    if "Access Denied" in page.title():
                        print("BLOCKED naukri access denied", flush=True)
                        ctx.close()
                        return
                    jd_el = page.query_selector("[class*='dang-inner-html']") or page.query_selector("[class*='job-desc']")
                    jd = jd_el.inner_text().strip() if jd_el else ""
                    site = page.query_selector("a[href^='http']:has-text('website')")
                    btns = " | ".join(b.inner_text().strip().lower() for b in page.query_selector_all("button, a.apply-button")
                                      if "apply" in (b.inner_text() or "").lower())
                    apply_type = ("external" if "company site" in btns else "easy" if "apply" in btns and "login" not in btns
                                  else "login" if "login" in btns else "unknown")
                    rec = page.query_selector("[class*='recruiter-name'], [class*='recruiter'] a")
                    card.update({"source": "naukri", "ext_id": card["url"].rsplit("-", 1)[-1], "description": jd,
                                 "company_site": site.get_attribute("href") if site else "", "apply_type": apply_type,
                                 "recruiter": rec.inner_text().strip() if rec else ""})
                    print("JOB " + json.dumps(card), flush=True)
                    found += 1
                except Exception as e:  # noqa: BLE001
                    log(f"detail failed {card['url'][:70]}: {type(e).__name__}")
        ctx.close()
    log(f"done: {found} jobs")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        payload = json.loads(sys.argv[1])
    else:
        payload = json.loads(sys.stdin.read())
    main(payload)
