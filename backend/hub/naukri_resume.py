"""Replace the resume on the user's Naukri profile with the one uploaded to the hub.

Naukri's "Apply" sends the resume stored on the Naukri profile, not a file chosen per application, so without this
step Naukri applications go out with whatever resume was uploaded there last. Uses the saved Naukri session in a
hidden browser. Usage: naukri_resume.py <resume.pdf> <dry_run 0|1>
Exit codes: 0 ok, 2 not logged in / blocked, 3 upload control not found.
"""
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".browsers"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from naukri_discover import chrome_major  # noqa: E402

PROFILE_URL = "https://www.naukri.com/mnjuser/profile"


def main(resume: str, dry: bool) -> int:
    from playwright.sync_api import sync_playwright
    if not Path(resume).exists():
        print(f"resume not found: {resume}", flush=True)
        return 3
    ua = f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_major()}.0.0.0 Safari/537.36"
    import moli_client
    with sync_playwright() as p:
        ctx = moli_client.sync_moli_context(p, platform="naukri", user_agent=ua, viewport={"width": 1280, "height": 900})
        if not ctx:
            ctx = p.chromium.launch_persistent_context(
                str(ROOT / ".profiles" / "naukri"), headless=True, channel="chromium", user_agent=ua,
                args=["--disable-blink-features=AutomationControlled"], viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            if "Access Denied" in page.title():
                print("BLOCKED naukri access denied (datacenter IP?) - run this from a home network", flush=True)
                return 2
            if "nlogin" in page.url or "login" in page.url.lower():
                print("Naukri session expired - run login_platform('naukri')", flush=True)
                return 2
            inp = page.query_selector("input#attachCV") or page.query_selector("input[type='file'][id*='CV' i]") \
                or page.query_selector("input[type='file']")
            if not inp:
                print("resume upload control not found on the Naukri profile page (layout changed?)", flush=True)
                return 3
            if dry:
                print(f"DRY-RUN: would replace the Naukri profile resume with {Path(resume).name}", flush=True)
                return 0
            inp.set_input_files(resume)
            page.wait_for_timeout(6000)
            body = page.inner_text("body")[:20000].lower()
            ok = "success" in body or "uploaded" in body or Path(resume).name.lower()[:20] in body
            print(("Naukri resume updated: " if ok else "upload sent, confirmation not detected; check naukri.com: ")
                  + Path(resume).name, flush=True)
            return 0
        finally:
            ctx.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] != "0"))
