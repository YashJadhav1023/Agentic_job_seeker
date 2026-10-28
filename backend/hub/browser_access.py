"""Access to the remote browser (noVNC) used to sign in to LinkedIn / Naukri / Indeed from the phone.

Instead of a basic-auth prompt followed by a VNC password prompt, the app asks the API for a single-use link. Opening it
sets a short-lived signed cookie and forwards straight to the already-connected browser; Caddy checks the cookie with
forward_auth on every /browser/* request (including the websocket)."""
from __future__ import annotations

import hmac
import os
import secrets
import threading
import time

COOKIE = "ajs_browser"
COOKIE_TTL = 30 * 60
TICKET_TTL = 120
_tickets: dict = {}
_lock = threading.Lock()


def _key() -> bytes:
    t = os.environ.get("JOBHUB_API_TOKEN", "")
    if len(t) < 32:
        raise RuntimeError("JOBHUB_API_TOKEN must be set (>= 32 chars)")
    return t.encode()


def _sign(exp: int) -> str:
    return hmac.new(_key(), f"browser|{exp}".encode(), "sha256").hexdigest()


def cookie_value() -> str:
    exp = int(time.time()) + COOKIE_TTL
    return f"{exp}.{_sign(exp)}"


def cookie_ok(value: str) -> bool:
    try:
        exp_s, sig = value.split(".", 1)
        exp = int(exp_s)
    except ValueError:
        return False
    return exp > time.time() and hmac.compare_digest(sig, _sign(exp))


def new_ticket(platform: str) -> str:
    t = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        for k in [k for k, (exp, _) in _tickets.items() if exp < now]:
            _tickets.pop(k, None)
        _tickets[t] = (now + TICKET_TTL, platform)
    return t


def use_ticket(t: str):
    """Single use. -> platform or None."""
    with _lock:
        item = _tickets.pop(t, None)
    return item[1] if item and item[0] > time.time() else None


def vnc_url(password: str) -> str:
    """noVNC address with the VNC password in the URL *fragment*, which browsers never send to any server or log."""
    base = "/browser/vnc.html?path=browser/websockify&autoconnect=1&resize=scale"
    return base + (f"#password={password}" if password else "")


def public_origin() -> str:
    """https://host of the deployment, from JOBHUB_REMOTE_BROWSER_URL (set by docker-compose)."""
    from urllib.parse import urlparse
    u = urlparse(os.environ.get("JOBHUB_REMOTE_BROWSER_URL", ""))
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else ""
