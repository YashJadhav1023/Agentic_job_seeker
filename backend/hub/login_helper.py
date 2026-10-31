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
# Cookies that only exist once you are signed in (any one is enough). Indeed sets PPID (its login passport) after
# every sign-in method; Naukri's nauk_at lives one hour and is renewed from the one-year nauk_rt.
SIGNED_IN = {"linkedin": ("li_at",), "naukri": ("nauk_at", "nauk_rt"), "indeed": ("PPID", "SHOE")}
WAIT_MAX = 15 * 60
CHROME_EPOCH = 11644473600  # Chrome stores times as microseconds since 1601-01-01


def in_container() -> bool:
    if os.environ.get("JOBHUB_CHROME_SANDBOX") == "1":
        return False
    return Path("/.dockerenv").exists() or os.environ.get("JOBHUB_TRUST_PROXY") == "1"


def has_cookie(profile: Path, name, since: float = 0) -> bool:
    """True if any of `name` (a name or tuple of names) is stored and not expired. With `since` (unix time) the cookie
    must also have been written after it, so an old session does not count as a new sign-in."""
    names = (name,) if isinstance(name, str) else tuple(name)
    now_c = int((time.time() + CHROME_EPOCH) * 1_000_000)
    since_c = int((since + CHROME_EPOCH) * 1_000_000) if since else 0
    q = (f"SELECT 1 FROM cookies WHERE name IN ({','.join('?' * len(names))}) AND (expires_utc=0 OR expires_utc>?) "
         "AND MAX(creation_utc, last_update_utc)>=? LIMIT 1")
    for db in (profile / "Default" / "Network" / "Cookies", profile / "Default" / "Cookies"):
        if not db.exists():
            continue
        tmp = Path(tempfile.mkdtemp(prefix="ck-")) / "c.db"
        try:
            shutil.copyfile(db, tmp)  # Chrome keeps the live file locked
            con = sqlite3.connect(tmp)
            try:
                if con.execute(q, (*names, now_c, since_c)).fetchone() is not None:
                    return True
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
    args = [exe, f"--user-data-dir={d}", "--password-store=basic", "--no-first-run",
            "--no-default-browser-check", "--window-position=0,0", "--window-size=1280,880", "--new-window"]
    if in_container():
        # Docker's default seccomp profile blocks the user namespaces Chrome's sandbox needs, so Chrome aborts with
        # "No usable sandbox" and the remote browser shows an empty desktop. The container is the isolation boundary
        # here (same default Playwright uses when it launches Chromium).
        args += ["--no-sandbox", "--test-type"]
    proc = subprocess.Popen(args + [URLS[platform]], stdin=subprocess.DEVNULL)
    cookie, seen_at = SIGNED_IN.get(platform), None
    if cookie and has_cookie(d, cookie):
        print(f"you are already signed in to {platform}; sign in again in the window to refresh the session, "
              "or close it", flush=True)
    start_c = t0 - 2  # only a cookie written during THIS window counts as signing in (an old one closed it at once)
    while proc.poll() is None and time.time() - t0 < WAIT_MAX:
        time.sleep(2)
        if cookie and seen_at is None and has_cookie(d, cookie, since=start_c):
            seen_at = time.time()
            print(f"signed in to {platform} - saving the session", flush=True)
        if seen_at and time.time() - seen_at > 8:  # let Chrome flush the cookie store, then close the window
            proc.terminate()
            break
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    if time.time() - t0 < 5 and (proc.returncode not in (0, None) or not saved(d)):
        # An old saved session must not hide a crash: before, a Chrome abort still reported success.
        print("ERROR: Chrome closed immediately - the browser profile may be locked or the display is not running. "
              "Try again; if it repeats, check hub_status.chromium_installed.", flush=True)
        return 1
    if cookie and not has_cookie(d, cookie):
        print(f"ERROR: not signed in to {platform} (window closed or timed out before signing in). Try again.", flush=True)
        return 1
    if not saved(d):
        print("ERROR: no login session was saved (window closed before signing in?). Try again.", flush=True)
        return 1
    print("login session saved", flush=True)
    try:
        sys.path.insert(0, str(ROOT / "hub"))
        import moli_client
        state_file = ROOT / ".profiles" / f"{platform}_state.json"
        if state_file.exists():
            state_file.unlink()
        moli_client.get_storage_state_path(platform)
        print("moli session bridge updated", flush=True)
    except Exception as e:
        print(f"note: could not update moli session bridge: {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
