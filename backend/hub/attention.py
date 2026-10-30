"""Human help during unattended runs: logins (Workday, Deloitte, ...) and "are you human" checks (Naukri).

The bot never tries to get past such a check itself. Instead it:
  1. records a request (DB table `attention`) and pushes a phone notification (Telegram and/or ntfy) with a link;
  2. the link (signed, valid only while the request is open) opens the server's remote browser, where the bot's own
     window is waiting on the page that needs the user;
  3. the bot polls the page; once the login / check is gone it continues, else it gives up after the timeout.

Settings (profile.yaml):
  human_help: {enabled: true, wait_minutes: 15}
  notifications: {ntfy_topic: "<long random topic>", ntfy_server: "https://ntfy.sh", telegram_chat_id: ...}
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

import core

DEFAULTS = {"enabled": True, "wait_minutes": 15}
_TABLE = """CREATE TABLE IF NOT EXISTS attention(
  id INTEGER PRIMARY KEY, kind TEXT, site TEXT, url TEXT, job TEXT, reason TEXT, status TEXT DEFAULT 'open',
  created REAL, expires REAL, resolved_at TEXT, notified INTEGER DEFAULT 0)"""


def cfg(profile: dict | None = None) -> dict:
    p = profile if profile is not None else core.load_profile()
    c = dict(DEFAULTS)
    c.update((p or {}).get("human_help") or {})
    return c


def available(profile: dict | None = None) -> bool:
    """Human help works only with a display the remote browser shows (the server's Xvfb) and when it is switched on."""
    return bool(cfg(profile).get("enabled")) and bool(os.environ.get("DISPLAY"))


def _db():
    c = core.db()
    c.execute(_TABLE)
    return c


def _key() -> bytes:
    t = os.environ.get("JOBHUB_API_TOKEN", "")
    if len(t) < 32:
        raise RuntimeError("JOBHUB_API_TOKEN must be set (>= 32 chars)")
    return t.encode()


def sign(rid: int, expires: float) -> str:
    return hmac.new(_key(), f"attention|{rid}|{int(expires)}".encode(), hashlib.sha256).hexdigest()


def link_ok(rid: int, k: str) -> bool:
    """A notification link is valid only while its request is open and not expired."""
    r = get(rid)
    if not r or r["status"] != "open" or r["expires"] < time.time():
        return False
    try:
        return hmac.compare_digest(k or "", sign(rid, r["expires"]))
    except RuntimeError:
        return False


def open_link(rid: int) -> str:
    import browser_access
    r = get(rid)
    origin = browser_access.public_origin()
    if not r or not origin:
        return ""
    return f"{origin}/api/v1/attention/open?id={rid}&k={sign(rid, r['expires'])}"


def get(rid: int):
    with _db() as c:
        row = c.execute("SELECT * FROM attention WHERE id=?", (rid,)).fetchone()
    return dict(row) if row else None


def pending() -> list:
    now = time.time()
    with _db() as c:
        c.execute("UPDATE attention SET status='expired' WHERE status='open' AND expires<?", (now,))
        return [dict(r) for r in c.execute("SELECT * FROM attention WHERE status='open' ORDER BY id DESC")]


def resolve(rid: int, status: str = "done") -> bool:
    if status not in ("done", "skipped", "expired"):
        raise ValueError("status must be done, skipped or expired")
    with _db() as c:
        n = c.execute("UPDATE attention SET status=?, resolved_at=? WHERE id=? AND status='open'",
                      (status, core.now_iso(), rid)).rowcount
    return bool(n)


def request(kind: str, site: str, url: str, reason: str, job: str = "", profile: dict | None = None) -> int:
    """Open a request. kind: login | human_check. -> request id.

    The notification goes out here when this process holds the API token (our own runs); vendored bots never get the
    token, so for them the server's queue loop sends it (notify_pending) within a few seconds."""
    p = profile if profile is not None else core.load_profile()
    wm = cfg(p).get("wait_minutes")
    minutes = float(15 if wm is None else wm)
    now = time.time()
    with _db() as c:
        rid = c.execute("INSERT INTO attention(kind,site,url,job,reason,created,expires) VALUES(?,?,?,?,?,?,?)",
                        (kind, site, url[:500], job[:200], reason[:300], now, now + minutes * 60)).lastrowid
    if len(os.environ.get("JOBHUB_API_TOKEN", "")) >= 32:
        _notify(rid, p)
    return rid


def _notify(rid: int, profile: dict | None = None) -> bool:
    r = get(rid)
    if not r or r["status"] != "open" or r.get("notified"):
        return False
    with _db() as c:  # claim first: the queue loop and the run must not both send it
        if not c.execute("UPDATE attention SET notified=1 WHERE id=? AND notified=0", (rid,)).rowcount:
            return False
    p = profile if profile is not None else core.load_profile()
    minutes = max(1, int((r["expires"] - time.time()) // 60))
    what = {"login": f"Sign in to {r['site']}", "human_check": f"{r['site']} wants to check you are human"}.get(
        r["kind"], f"{r['site']} needs you")
    text = (f"{what} so the bot can continue" + (f" applying to {r['job']}" if r["job"] else "") +
            f". It waits {minutes} min. Open the link, finish the step in the browser, then leave it open.")
    try:
        import notify
        return bool(notify.push(p, "Job bot needs you: " + what, text, open_link(rid)))
    except Exception:  # noqa: BLE001 - a failed alert must never break the run
        return False


def notify_pending() -> int:
    """Send notifications for open requests made by processes that could not send them. -> how many were sent."""
    with _db() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM attention WHERE status='open' AND notified=0 AND expires>?",
                                       (time.time(),))]
    return sum(1 for rid in ids if _notify(rid))


def wait(rid: int, cleared, poll: float = 5.0, sleep=time.sleep, now=time.time) -> bool:
    """Block until `cleared()` is True (-> True, request done) or the request expires / is skipped (-> False)."""
    while True:
        r = get(rid)
        if not r or r["status"] in ("skipped", "expired"):
            return False
        try:
            if cleared():
                resolve(rid, "done")
                return True
        except Exception:  # noqa: BLE001 - the page may be navigating while the user works in it
            pass
        if now() >= r["expires"]:
            resolve(rid, "expired")
            return False
        sleep(poll)


def ask_and_wait(kind: str, site: str, url: str, reason: str, cleared, job: str = "", log=print,
                 profile: dict | None = None, sleep=time.sleep) -> bool:
    """One call for the bots. False at once when human help is not available here."""
    if not available(profile):
        return False
    rid = request(kind, site, url, reason, job, profile)
    log(f"  🙋 waiting for you ({kind} on {site}); notification sent, request #{rid}")
    ok = wait(rid, cleared, sleep=sleep)
    log(f"  {'✅ you finished it, continuing' if ok else '⌛ no help in time, skipping'} (request #{rid})")
    return ok
