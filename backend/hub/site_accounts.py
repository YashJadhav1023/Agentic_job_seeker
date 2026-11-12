"""Accounts the bot creates on company career sites (Workday, Greenhouse, SuccessFactors, ...): one generated password per
host, stored in the secrets volume (0600) so the next application on the same site signs in instead of signing up.

Passwords never leave this module except to fill the site's own password field: no logs, no tool results, no DB rows."""
from __future__ import annotations

import json
import os
import secrets
import string
import sys
import tempfile
from pathlib import Path

import core

FILE = "site_accounts.json"
SYMBOLS = "!@#$%"


def _path() -> Path:
    return Path(core.SECRETS) / FILE


def _key(host: str) -> str:
    h = (host or "").strip().lower()
    return h[4:] if h.startswith("www.") else h


def _load() -> dict:
    f = _path()
    if not f.exists():
        return {}
    if sys.platform != "win32" and f.stat().st_mode & 0o077:
        raise PermissionError(f"{f} must be private (chmod 600)")
    try:
        return json.loads(f.read_text() or "{}")
    except ValueError:
        return {}


def _save(data: dict) -> None:
    f = _path()
    f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=".acct-")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, f)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _password(simple: bool) -> str:
    classes = [string.ascii_uppercase, string.ascii_lowercase, string.digits] + ([] if simple else [SYMBOLS])
    size = 14 if simple else 16
    alphabet = "".join(classes)
    chars = [secrets.choice(c) for c in classes] + [secrets.choice(alphabet) for _ in range(size - len(classes))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def get(host: str) -> dict | None:
    """-> {"email", "password", "created"} or None. A pending password (sign-up not confirmed) is not returned."""
    acct = _load().get(_key(host))
    return None if not acct or acct.get("pending") else acct


def create(host: str, email: str, simple: bool = False, pending: bool = False) -> str:
    """New password for `host` (saved before it is typed anywhere). simple=True: letters and digits only, 14 chars,
    for sites that reject symbols. pending=True: kept, but get() ignores it until confirm() (the sign-up went through)."""
    data = _load()
    pw = _password(simple)
    data[_key(host)] = {"email": email, "password": pw, "created": core.now_iso(), **({"pending": True} if pending else {})}
    _save(data)
    return pw


def confirm(host: str) -> None:
    """The site accepted the sign-up: later runs sign in with this password."""
    data = _load()
    acct = data.get(_key(host))
    if acct and acct.pop("pending", None):
        _save(data)


def hosts() -> list:
    """Sites with a saved account (names only)."""
    return sorted(_load())
