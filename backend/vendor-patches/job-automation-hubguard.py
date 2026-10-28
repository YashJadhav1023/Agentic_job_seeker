"""job-hub guard: title relevance + dry-run, driven by env set by the hub adapter."""
import os, re

def _rx(name, default):
    v = os.getenv(name, default)
    return re.compile(v, re.I) if v else None

INCLUDE = _rx("JOBHUB_TITLE_INCLUDE", "")
EXCLUDE = _rx("JOBHUB_TITLE_EXCLUDE", "")
DRY_RUN = os.getenv("JOBHUB_DRY_RUN", "1") == "1"

def title_ok(title: str) -> bool:
    t = title or ""
    if EXCLUDE and EXCLUDE.search(t):
        return False
    return bool(INCLUDE.search(t)) if INCLUDE else True


import asyncio, pathlib

def PROFILE_DIR(platform: str) -> str:
    base = pathlib.Path(os.getenv("JOBHUB_PROFILES", str(pathlib.Path.home() / "Personal/job-hub/.profiles")))
    p = base / platform
    p.mkdir(parents=True, exist_ok=True)
    return str(p)

HEADLESS = os.getenv("JOBHUB_HEADLESS", "1") == "1"


async def single_page(context):
    """Reuse the tab a persistent context already opens (new_page() would add a 2nd tab) and auto-close any
    tab/popup a site opens later (e.g. 'apply on company site'), so the run stays on one page."""
    page = context.pages[0] if context.pages else await context.new_page()

    def _close(p):
        if p is not page:
            print(f"  🗙 closed extra tab: {p.url[:80]}", flush=True)
            asyncio.ensure_future(p.close())
    context.on("page", _close)
    return page


# One evaluate() call fills EVERY empty field at once. React-safe: uses the native value setter and fires
# input/change events so the site's form state picks the values up.
_FILL_JS = r"""
(cfg) => {
  const {rules, cover, selectPrefs} = cfg;
  const labelOf = (el) => {
    const parts = [];
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) parts.push(l.innerText); }
    const wrap = el.closest('label'); if (wrap) parts.push(wrap.innerText);
    parts.push(el.getAttribute('aria-label') || '', el.placeholder || '', el.name || '');
    return parts.join(' ').toLowerCase();
  };
  const setVal = (el, v) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    for (const t of ['input', 'change', 'blur']) el.dispatchEvent(new Event(t, {bubbles: true}));
  };
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const filled = [];
  for (const el of document.querySelectorAll("input[type='text'], input[type='number'], input[type='tel'], input:not([type])")) {
    if (!visible(el) || el.disabled || el.readOnly || el.value) continue;
    const lab = labelOf(el);
    const hit = rules.find(([keys]) => keys.some(k => lab.includes(k)));
    if (hit) { setVal(el, hit[1]); filled.push(lab.trim().slice(0, 40) || '(field)'); }
  }
  if (cover) {
    const ta = [...document.querySelectorAll('textarea')].find(t => visible(t) && !t.value);
    if (ta) { setVal(ta, cover); filled.push('cover letter'); }
  }
  for (const sel of document.querySelectorAll('select')) {
    if (!visible(sel) || sel.selectedIndex > 0) continue;
    const opt = [...sel.options].find(o => selectPrefs.includes(o.text.trim().toLowerCase()));
    if (opt) { setVal(sel, opt.value); filled.push('select: ' + opt.text.trim()); }
  }
  return filled;
}
"""


async def fill_all(target, rules, cover=None,
                   select_prefs=("yes", "immediately", "0", "india", "bachelor")):
    """rules: [([keywords...], value), ...] - first keyword hit in the field's label/placeholder wins."""
    try:
        filled = await target.evaluate(_FILL_JS, {"rules": [[list(k), str(v)] for k, v in rules],
                                                 "cover": cover, "selectPrefs": list(select_prefs)})
        if filled:
            print(f"  ✍️  filled {len(filled)} fields at once: {', '.join(filled)}", flush=True)
        return filled
    except Exception as e:
        print(f"  ⚠️ Form fill error: {e}", flush=True)
        return []


async def wait_for_login(page, url, is_logged_in, timeout=600):
    """Open url; if not logged in, wait (up to timeout s) for the USER to log in manually in the visible window.
    Headless runs cannot be logged into, so they stop immediately and point at login_platform."""
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    waited = 0
    while not is_logged_in(page.url):
        if HEADLESS:
            raise RuntimeError("saved session expired or missing - run login_platform, then re-run apply_jobs")
        if waited == 0:
            print("🔐 Not logged in. Log in manually in the Chrome window (Google/OTP are fine); waiting...", flush=True)
        if waited >= timeout:
            raise RuntimeError("login timeout - run login_platform first")
        await asyncio.sleep(5); waited += 5
    print("✅ Logged-in session found", flush=True)


# ---------------------------------------------------------------- application confirmation (no false "applied")
# A click on Apply is NOT an application: Naukri may open a questionnaire, send you to the company site, or reject it.
# A job counts as applied only when a confirmation shows up that was not on the page before the click.
APPLIED_RX = re.compile(r"successfully applied|applied successfully|you have (?:already )?applied|already applied|"
                        r"application (?:has been |was )?(?:sent|submitted|received)|applied to\b", re.I)
EXTERNAL_RX = re.compile(r"(?:apply|continue|view) (?:on|at|in) (?:the )?(?:company|employer|recruiter)(?:'s)? (?:site|website|page)|"
                         r"company site", re.I)


def new_text(before: str, after: str) -> str:
    """Lines present in `after` that were not in `before` (the page's reaction to our click)."""
    seen = {ln.strip() for ln in (before or "").splitlines()}
    return "\n".join(ln.strip() for ln in (after or "").splitlines() if ln.strip() and ln.strip() not in seen)


def confirmed_applied(before: str, after: str, url_after: str = "", applied_marker: bool = False) -> bool:
    """True when the page shows a NEW confirmation after the click. JD text that merely contains 'applied' does not count
    because it was already there before the click."""
    if applied_marker:
        return True
    if re.search(r"applyconfirm|saveapply|/myapply/|applied=true|success", url_after or "", re.I):
        return True
    return bool(APPLIED_RX.search(new_text(before, after)))


def external_only(button_texts) -> bool:
    """The job can only be applied to on the company's own site (nothing we can submit on this platform)."""
    texts = [(t or "").strip().lower() for t in button_texts]
    plain_apply = any(t in ("apply", "apply now", "easy apply") for t in texts)
    return any(EXTERNAL_RX.search(t) for t in texts) and not plain_apply
