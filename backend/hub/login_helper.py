"""Open the bundled Chromium as a NORMAL browser (no automation flags, so Google sign-in works) on the platform's
profile dir so the USER can log in. Cookies stay in .profiles/<platform>; the hub never sees credentials.

The window closes by itself a few seconds after the sign-in cookie appears (LinkedIn, Naukri); for other sites, or if
you prefer, just close the window. The run fails with a clear message if Chrome cannot start (e.g. a locked profile)."""
import os, shutil, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".browsers"))
URLS = {"naukri": "https://www.naukri.com/nlogin/login", "indeed": "https://secure.indeed.com/auth?hl=en_IN&co=IN",
        "linkedin": "https://www.linkedin.com/login"}
# Cookie that only exists once you are signed in. Indeed has no stable one, so it relies on you closing the window.
SIGNED_IN = {"linkedin": "li_at", "naukri": "nauk_at"}
WAIT_MAX = 15 * 60


def has_cookie(profile: Path, name: str) -> bool:
    for db in (profile / "Default" / "Network" / "Cookies", profile / "Default" / "Cookies"):
        if not db.exists():
            continue
        tmp = Path(tempfile.mkdtemp(prefix="ck-")) / "c.db"
        try:
            shutil.copyfile(db, tmp)  # Chrome keeps the live file locked
            con = sqlite3.connect(tmp)
            try:
                return con.execute("SELECT 1 FROM cookies WHERE name=? LIMIT 1", (name,)).fetchone() is not None
            finally:
                con.close()
        except (OSError, sqlite3.Error):
            continue
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)
    return False


def saved(profile: Path) -> bool:
    return profile.exists() and any(profile.rglob("Cookies"))


def main(platform: str) -> int:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        exe = p.chromium.executable_path
    d = ROOT / ".profiles" / platform
    d.mkdir(parents=True, exist_ok=True)
    print(f"Log in to {platform} in the window; it closes by itself once you are signed in.", flush=True)
    t0 = time.time()
    proc = subprocess.Popen([exe, f"--user-data-dir={d}", "--password-store=basic", "--no-first-run",
                             "--no-default-browser-check", "--window-position=0,0", "--window-size=1280,880",
                             "--new-window", URLS[platform]], stdin=subprocess.DEVNULL)
    cookie, seen_at = SIGNED_IN.get(platform), None
    while proc.poll() is None and time.time() - t0 < WAIT_MAX:
        time.sleep(2)
        if cookie and seen_at is None and has_cookie(d, cookie):
            seen_at = time.time()
            print(f"signed in to {platform} - saving the session", flush=True)
        if seen_at and time.time() - seen_at > 8:  # let Chrome flush the cookie store, then close the window
            proc.terminate()
            break
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    if time.time() - t0 < 5 and not saved(d):
        print("ERROR: Chrome closed immediately - the browser profile may be locked or the display is not running. "
              "Try again; if it repeats, check hub_status.chromium_installed.", flush=True)
        return 1
    if not saved(d):
        print("ERROR: no login session was saved (window closed before signing in?). Try again.", flush=True)
        return 1
    print("login session saved", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
