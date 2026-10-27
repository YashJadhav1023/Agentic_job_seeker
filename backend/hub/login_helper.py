"""Open the bundled Chromium as a NORMAL browser (no automation flags, so Google sign-in works) on the platform's
profile dir so the USER can log in. Cookies stay in .profiles/<platform>; the hub never sees credentials."""
import os, subprocess, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".browsers"))
URLS = {"naukri": "https://www.naukri.com/nlogin/login", "indeed": "https://secure.indeed.com/auth?hl=en_IN&co=IN",
        "linkedin": "https://www.linkedin.com/login"}
platform = sys.argv[1]
with sync_playwright() as p:
    exe = p.chromium.executable_path
d = ROOT / ".profiles" / platform
d.mkdir(parents=True, exist_ok=True)
print(f"Log in to {platform} in the window, then CLOSE the window.", flush=True)
subprocess.run([exe, f"--user-data-dir={d}", "--password-store=basic", "--no-first-run", "--no-default-browser-check",
                "--new-window", URLS[platform]])
print("login session saved", flush=True)
