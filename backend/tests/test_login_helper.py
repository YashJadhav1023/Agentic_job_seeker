import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hub"))
import login_helper  # noqa: E402


class FakeProc:
    def __init__(self, args, rc):
        self.args, self.returncode = args, rc

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        pass


def _run(monkeypatch, tmp_path, rc, in_container, saved_before):
    monkeypatch.setattr(login_helper, "ROOT", tmp_path)
    monkeypatch.setattr(login_helper, "in_container", lambda: in_container)
    pw = types.SimpleNamespace(chromium=types.SimpleNamespace(executable_path="/chrome"))

    class Ctx:
        def __enter__(self):
            return pw

        def __exit__(self, *a):
            return False
    monkeypatch.setitem(sys.modules, "playwright.sync_api", types.SimpleNamespace(sync_playwright=lambda: Ctx()))
    if saved_before:
        (tmp_path / ".profiles" / "indeed" / "Default").mkdir(parents=True)
        (tmp_path / ".profiles" / "indeed" / "Default" / "Cookies").write_bytes(b"x")
    seen = {}

    def popen(args, stdin=None):
        seen["args"] = args
        return FakeProc(args, rc)
    monkeypatch.setattr(login_helper.subprocess, "Popen", popen)
    return login_helper.main("indeed"), seen["args"]


def test_in_container_chrome_runs_without_the_sandbox_and_opens_the_login_page(monkeypatch, tmp_path):
    code, args = _run(monkeypatch, tmp_path, rc=0, in_container=True, saved_before=True)
    assert "--no-sandbox" in args and args[-1] == login_helper.URLS["indeed"]


def test_outside_a_container_the_sandbox_stays_on(monkeypatch, tmp_path):
    _, args = _run(monkeypatch, tmp_path, rc=0, in_container=False, saved_before=True)
    assert "--no-sandbox" not in args


def test_a_chrome_crash_is_reported_even_with_an_old_saved_session(monkeypatch, tmp_path):
    code, _ = _run(monkeypatch, tmp_path, rc=133, in_container=True, saved_before=True)
    assert code == 1


def _cookie_db(profile, rows):
    import sqlite3
    (profile / "Default" / "Network").mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(profile / "Default" / "Network" / "Cookies")
    c.execute("CREATE TABLE cookies(host_key TEXT, name TEXT, expires_utc INTEGER, creation_utc INTEGER, last_update_utc INTEGER)")
    c.executemany("INSERT INTO cookies VALUES(?,?,?,?,?)", rows)
    c.commit()
    c.close()


def _c(t):  # unix seconds -> Chrome time
    return int((t + login_helper.CHROME_EPOCH) * 1_000_000)


def test_signed_in_cookies_per_platform_respect_expiry_and_freshness(tmp_path):
    import time
    now = time.time()
    p = tmp_path / "indeed"
    _cookie_db(p, [(".indeed.com", "PPID", _c(now + 86400 * 30), _c(now - 3600), _c(now - 3600)),
                   (".indeed.com", "CTK", _c(now + 86400), _c(now), _c(now))])
    assert login_helper.has_cookie(p, login_helper.SIGNED_IN["indeed"])               # Indeed now has a sign-in cookie
    assert not login_helper.has_cookie(p, login_helper.SIGNED_IN["indeed"], since=now - 60)   # old session != new sign-in
    n = tmp_path / "naukri"
    _cookie_db(n, [(".naukri.com", "nauk_at", _c(now - 10), _c(now - 4000), _c(now - 4000)),         # 1 h token expired
                   (".naukri.com", "nauk_rt", _c(now + 86400 * 300), _c(now - 86400), _c(now - 5))])  # refresh token valid
    assert login_helper.has_cookie(n, login_helper.SIGNED_IN["naukri"])
    lk = tmp_path / "linkedin"
    _cookie_db(lk, [(".www.linkedin.com", "li_at", _c(now - 1), _c(now - 86400), _c(now - 86400))])
    assert not login_helper.has_cookie(lk, login_helper.SIGNED_IN["linkedin"])        # expired session is not "linked"


def test_window_closes_by_itself_after_a_new_indeed_sign_in(monkeypatch, tmp_path):
    import time
    monkeypatch.setattr(login_helper, "ROOT", tmp_path)
    monkeypatch.setattr(login_helper, "in_container", lambda: True)
    monkeypatch.setattr(login_helper.time, "sleep", lambda s: None)
    pw = types.SimpleNamespace(chromium=types.SimpleNamespace(executable_path="/chrome"))

    class Ctx:
        def __enter__(self):
            return pw

        def __exit__(self, *a):
            return False
    monkeypatch.setitem(sys.modules, "playwright.sync_api", types.SimpleNamespace(sync_playwright=lambda: Ctx()))
    prof = tmp_path / ".profiles" / "indeed"

    class Browser:
        returncode = None

        def __init__(self, *a, **k):
            self.polls = 0

        def poll(self):
            self.polls += 1
            if self.polls == 3:  # the user signs in while the window is open
                now = time.time()
                _cookie_db(prof, [(".indeed.com", "PPID", _c(now + 86400), _c(now), _c(now))])
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return 0
    clock = [time.time()]
    real = time.time
    monkeypatch.setattr(login_helper.time, "time", lambda: clock.__setitem__(0, clock[0] + 3) or clock[0])
    monkeypatch.setattr(login_helper.subprocess, "Popen", Browser)
    assert login_helper.main("indeed") == 0
    del real
