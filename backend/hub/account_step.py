"""Get past a company career site's account wall by itself: sign in with the account saved for that site, or create one
(profile email + a generated password, terms ticked, marketing left off), then verify it with the code or link the site
emails. Only what a person must do goes to the user: a phone/email code that never arrives by mail (typed in the app)
and CAPTCHAs (remote browser, by the caller). Never on job boards, never in dry runs."""
from __future__ import annotations

import re
import time

import site_accounts

FIELDS_JS = r"""
() => {
  const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  document.querySelectorAll('[data-ajsa]').forEach(e => e.removeAttribute('data-ajsa'));
  const out = []; let n = 0;
  for (const el of document.querySelectorAll('input,select,textarea')) {
    const t = (el.type || '').toLowerCase();
    if (!vis(el) || ['hidden','submit','button','image','reset','file'].includes(t)) continue;
    let lab = '';
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) lab = l.innerText; }
    if (!lab && el.closest('label')) lab = el.closest('label').innerText;
    lab = lab || el.getAttribute('aria-label') || el.placeholder || '';
    el.setAttribute('data-ajsa', 'a' + (++n));
    out.push({i: 'a' + n, type: t, name: el.name || '', id: el.id || '', ac: el.autocomplete || '',
              label: lab.replace(/\s+/g, ' ').trim(), checked: !!el.checked});
  }
  return out;
}
"""

SIGNUP_BTN_RX = re.compile(r"create( an| your)? account|sign ?up|register|join|get started", re.I)
SIGNIN_BTN_RX = re.compile(r"sign ?in|log ?in|continue|next", re.I)
VERIFY_BTN_RX = re.compile(r"verify|confirm|submit|continue|next|activate", re.I)
SIGNUP_LINK_RX = re.compile(r"create (an |your )?account|sign ?up|register|new user|join now", re.I)
SIGNIN_LINK_RX = re.compile(r"sign ?in|log ?in|already have an account", re.I)
PW_RULE_RX = re.compile(r"password (must|should|cannot|can't|may not|needs?)|special characters?|invalid password|password (is )?(too|not)",
                        re.I)
EXISTS_RX = re.compile(r"already (exists|registered|in use|have an account)|account with (this|that) e-?mail", re.I)
VERIFY_TEXT_RX = re.compile(r"verify your (e-?mail|account)|check your (e-?mail|inbox)|we(?:'ve| have)? sent|enter the (code|otp)", re.I)
CODE_RX = re.compile(r"\b(code|otp|passcode|pin)\b|verif|one.?time", re.I)
NOT_CODE_RX = re.compile(r"country|zip|postal|area|promo|referr|coupon|invite", re.I)


def _blob(f: dict) -> str:
    return " ".join([f["name"], f["id"], f["ac"], f["label"]]).lower()


def _role(f: dict) -> str:
    b, t = _blob(f), f["type"]
    if t == "password":
        return "password"
    if t == "checkbox":
        return "checkbox"
    if CODE_RX.search(b) and not NOT_CODE_RX.search(b) and t != "email":
        return "code"
    if t == "email" or re.search(r"e-?mail|user ?name", b):
        return "email"
    if re.search(r"first.?name|given", b):
        return "first"
    if re.search(r"last.?name|surname|family", b):
        return "last"
    if t == "tel" or re.search(r"phone|mobile", b):
        return "phone"
    if re.search(r"(^|\s)name($|\s)|full.?name", b):
        return "name"
    return "other"


def _scope(page, want: str = "password"):
    """-> (scope, fields) of the first frame holding a field with role `want`, else (None, [])."""
    import career_apply as ca
    for sc in ca._scopes(page):
        try:
            fields = sc.evaluate(FIELDS_JS)
        except Exception:  # noqa: BLE001 - frame navigated away
            continue
        if any(_role(f) == want for f in fields):
            return sc, fields
    return None, []


def _blockers(page) -> dict:
    import career_apply as ca
    return ca._blockers(page)


def _buttons(scope, rx=None, links: bool = False) -> list:
    import career_apply as ca
    out = [b for b in ca._buttons(scope) if not b["disabled"] and (b["tag"] == "a") == links]
    return [b for b in out if rx.search(b["text"])] if rx else out


def _click(scope, b) -> None:
    import career_apply as ca
    ca._click_button(scope, b)


def _settle(page) -> None:
    import career_apply as ca
    ca.settle(page, 4000, 500)


def _fill(scope, f: dict, value: str) -> None:
    scope.locator(f'[data-ajsa="{f["i"]}"]').first.fill(str(value), timeout=6000)


def _submit(scope, rx) -> bool:
    btns = _buttons(scope, rx) or _buttons(scope)
    if not btns:
        return False
    _click(scope, btns[0])
    return True


def kind(page) -> str | None:
    """'signup' (two password fields, or a create-account button), 'login' (one password field), or None."""
    sc, fields = _scope(page)
    if sc is None:
        return None
    if sum(_role(f) == "password" for f in fields) >= 2 or _buttons(sc, SIGNUP_BTN_RX):
        return "signup"
    return "login"


def _follow_link(page, rx) -> bool:
    import career_apply as ca
    for sc in ca._scopes(page):
        links = _buttons(sc, rx, links=True) + _buttons(sc, rx)
        if links:
            _click(sc, links[0])
            _settle(page)
            return True
    return False


def _sign_up(page, F: dict, host: str, log) -> str:
    import career_apply as ca
    sc, fields = _scope(page)
    pw = site_accounts.create(host, F.get("email", ""))
    for f in fields:
        role = _role(f)
        if role == "password":
            _fill(sc, f, pw)
        elif role in ("email", "first", "last", "phone", "name") and F.get(role):
            _fill(sc, f, F[role])
        elif role == "checkbox" and not f["checked"] and ca.CONSENT_RX.search(f["label"]) and not ca.MARKETING_RX.search(f["label"]):
            sc.locator(f'[data-ajsa="{f["i"]}"]').first.check(timeout=6000, force=True)
    if not _submit(sc, SIGNUP_BTN_RX):
        return "failed"
    _settle(page)
    b = _blockers(page)
    if b["password"] and PW_RULE_RX.search(b["body"]):  # the site rejects symbols / length: one retry, letters + digits
        sc, fields = _scope(page)
        if sc is None:
            return "failed"
        pw = site_accounts.create(host, F.get("email", ""), simple=True)
        for f in fields:
            if _role(f) == "password":
                _fill(sc, f, pw)
        _submit(sc, SIGNUP_BTN_RX)
        _settle(page)
        b = _blockers(page)
    if b["password"] and EXISTS_RX.search(b["body"]):
        return "failed"  # an account we do not know the password of: the user signs in
    log(f"  🔑 created an account on {host}")
    return "ok"


def _sign_in(page, acct: dict) -> str:
    sc, fields = _scope(page)
    if sc is None:
        return "failed"
    for f in fields:
        role = _role(f)
        if role == "password":
            _fill(sc, f, acct["password"])
        elif role == "email":
            _fill(sc, f, acct["email"])
    if not _submit(sc, SIGNIN_BTN_RX):
        return "failed"
    _settle(page)
    return "ok"


def _verify(page, host: str, since: float, ask_value, wait_mail) -> str:
    sc, fields = _scope(page, "code")
    if sc is None and not VERIFY_TEXT_RX.search(_blockers(page)["body"]):
        return "ok"  # nothing to verify
    mail = wait_mail(host, since) or {}
    if mail.get("link"):
        page.goto(mail["link"], wait_until="domcontentloaded")
        _settle(page)
        sc, fields = _scope(page, "code")
        if sc is None:
            return "ok"
    if sc is None:
        return "failed"  # "check your inbox" but no mail came
    code = mail.get("code") or ask_value("otp", host, page.url, f"Enter the code {host} sent to your email or phone")
    if not code:
        return "failed"
    _fill(sc, next(f for f in fields if _role(f) == "code"), code)
    _submit(sc, VERIFY_BTN_RX)
    _settle(page)
    return "ok"


def run(page, F: dict, host: str, dry: bool, ask_value=None, wait_mail=None, log=print) -> str:
    """-> 'ok' (past the wall: look for the form again) | 'captcha' (caller hands it to the user) | 'failed' | 'skipped'.

    ask_value(kind, site, url, reason) -> str|None (attention.ask_value); wait_mail(host, since) -> {"code","link"}."""
    import career_apply as ca
    if dry or not host or ca.BOARD_RX.search(host):
        return "skipped"
    if _blockers(page)["challenge"]:
        return "captcha"
    since = time.time()
    k = kind(page)
    acct = site_accounts.get(host)
    if k is None:
        return "failed"
    if acct and acct.get("password"):
        if k == "signup" and not (_follow_link(page, SIGNIN_LINK_RX) and kind(page) == "login"):
            return "failed"
        st = _sign_in(page, acct)
    else:
        if k == "login" and not (_follow_link(page, SIGNUP_LINK_RX) and kind(page) == "signup"):
            return "captcha" if _blockers(page)["challenge"] else "failed"
        if _blockers(page)["challenge"]:
            return "captcha"
        st = _sign_up(page, F, host, log)
    if st != "ok":
        return st
    if _blockers(page)["challenge"]:
        return "captcha"
    st = _verify(page, host, since, ask_value or (lambda *a, **k: None), wait_mail or (lambda *a, **k: {}))
    if st != "ok":
        return st
    return "failed" if _blockers(page)["password"] else "ok"
