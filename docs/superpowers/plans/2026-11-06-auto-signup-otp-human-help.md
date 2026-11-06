# Auto sign-up, in-app OTP/answers, profile-first questions, remote-browser session — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a company career page (reached directly, or redirected from Naukri, LinkedIn or a post) asks for an account, the bot creates it (or signs in with the saved one), verifies the email itself, asks the user only for a phone OTP / a missing answer (typed in the app) or a CAPTCHA (remote browser), and keeps the remote-browser session alive while the user works.

**Architecture:** `attention` gains a `value` so the app can send text back to a waiting bot (kinds `otp`, `question`). A new `site_accounts` module stores one generated password per host in the secrets volume. A new `verify_mail` module reads the verification code or link from Gmail over IMAP. `career_apply` gets an account step that runs before the existing hand-over, and its unresolved-field path asks the user through `attention` and saves the answer to `form_qa`. Runs stay headless on Moli; a job that hits a CAPTCHA is retried once in visible Chromium.

**Tech Stack:** Python 3.12, Playwright sync API, SQLite (`core.db()`), imaplib, FastAPI tools in `server.py`, Flutter/Riverpod app.

**Spec:** the user's answers in chat on 2026-11-06 (recorded in the brain, `handoff/current`):
1. Company portals, including pages reached via Naukri / LinkedIn / post redirects. No accounts on LinkedIn or Naukri themselves.
2. Generated password per site, stored for reuse.
3. The bot may tick "I agree to the terms".
4. OTP is typed in a box in the app.
5. Missing details: look in the profile first, then ask the user.
6. Remote browser (noVNC) session must not drop after ~2 minutes.
7. Save RAM: keep using Moli (headless, ~40 MB) wherever no visible window is needed.

## Global Constraints

- Never solve or bypass a CAPTCHA or bot check: CAPTCHAs always go to the user (`hand_over("human_check", ...)`).
- Never create accounts on job boards: skip the account step when the host matches `BOARD_RX`.
- Passwords never appear in logs, run output, DB rows, notifications or tool results; files under `core.SECRETS` are written with `os.open(..., 0o600)`.
- Dry runs never create accounts, submit sign-up forms or send OTPs.
- Existing tests stay green; Windows-only failures already on `main` (9 tests: queue locks, file perms, spawn) are reported, not fixed here.
- Backend: `cd backend; ..\.venv\Scripts\python.exe -m pytest -q tests -p no:cacheprovider`. Mobile: `cd mobile; flutter analyze; flutter test` (Flutter 3.47.8 per CI).

## Review Focus

- A sign-up form whose password rules reject the generated password (e.g. "no special characters", max 16): expect a second attempt with a letters+digits password of 14 chars, then hand over `login`.
- The verification email takes longer than the poll window or lands in spam: expect the bot to ask the user (`otp` kind) for the code instead of failing silently.
- The user answers an `otp`/`question` after the request expired: expect the app to show "expired" and the value to be ignored.
- The host already has a saved account but the page shows a sign-up form ("email already registered"): expect a switch to the sign-in form with the saved password.
- Two jobs on the same ATS host in one batch (e.g. two Workday tenants share `myworkdayjobs.com`): accounts are keyed by full hostname, so each tenant gets its own entry.

---

### Task 1: attention carries a value (otp / question) and honours "done"

**Files:**
- Modify: `backend/hub/attention.py` (table, `resolve`, `wait`, new `answer`, `wait_value`, `_notify` texts)
- Modify: `backend/hub/server.py` (`attention_resolve` schema + handler, `attention_list` output)
- Modify: `backend/vendor-patches/job-automation-hubguard.py` (`wait_for_human` loop)
- Test: `backend/tests/test_attention.py`, `backend/tests/test_naukri_bot.py`

**Interfaces:**
- Produces: `attention.answer(rid: int, value: str) -> bool` (stores value, status `done`, only while open and not expired; value capped at 200 chars).
- Produces: `attention.wait_value(rid: int, poll: float = 5.0, sleep=time.sleep, now=time.time) -> str | None` (value once answered, `None` on skip/expiry).
- Produces: `attention.ask_value(kind: str, site: str, url: str, reason: str, job: str = "", profile=None, sleep=time.sleep) -> str | None` (`request` + `wait_value`; `None` when help is off). Unlike `available()`, it does NOT need `DISPLAY` (no browser involved).
- Produces: tool `attention_resolve` accepts optional `value` (string); with a value it calls `answer`, otherwise `resolve`. `attention_list` adds `needs_value: bool` (kind in `otp`, `question`) and never returns `value`.

- [ ] **Step 1: Write failing tests** in `test_attention.py`:
  - `test_answer_is_returned_to_the_waiting_bot`: `rid = attention.request("otp", "Workday", "https://x", "Enter the code sent to your phone", profile=P)`; `attention.answer(rid, " 482913 ")`; `assert attention.wait_value(rid, sleep=lambda s: None) == "482913"`.
  - `test_answer_after_expiry_is_refused`: with `now` past `expires`, `attention.pending()` then `assert attention.answer(rid, "1") is False` and `wait_value` returns `None`.
  - `test_wait_returns_when_user_marks_done_even_if_page_check_lags`: `resolve(rid, "done")` then `wait(rid, cleared=lambda: False, sleep=lambda s: None)` returns `True` after one extra `cleared()` grace call.
  - `test_list_hides_value_and_flags_needs_value`: server tool output for an `otp` row has `needs_value is True` and no `value` key.
  - In `test_naukri_bot.py`: `test_wait_for_human_stops_when_done_but_never_clear` — `get` returns status `done`; `is_clear` always False; `wait_for_human` returns `True` within one poll (no infinite loop).
- [ ] **Step 2: Run** `pytest tests/test_attention.py tests/test_naukri_bot.py -q` — expect the new tests to FAIL (`answer`/`wait_value` missing, loop never ends — guard the test with `asyncio.wait_for(..., 2)`).
- [ ] **Step 3: Implement.** Add `value TEXT` via `ALTER TABLE attention ADD COLUMN value TEXT` guarded by `PRAGMA table_info` (existing DBs). `_notify` texts: `otp` → "Enter the code {site} sent you", `question` → "{site} asks: {reason}"; the link for these kinds is the app (no browser link: pass `""`). `wait`: when status is `done`, return `True` (user said it is finished). hubguard: treat `done` the same way.
- [ ] **Step 4: Run** the same tests — PASS; then the full backend suite.
- [ ] **Step 5: Commit** `feat(attention): let the app answer a waiting bot (otp, question) and honour done`.

### Task 2: per-site accounts in the secrets volume

**Files:**
- Create: `backend/hub/site_accounts.py`
- Test: `backend/tests/test_site_accounts.py`

**Interfaces:**
- Produces: `site_accounts.get(host: str) -> dict | None` → `{"email": str, "password": str, "created": iso}`.
- Produces: `site_accounts.create(host: str, email: str, simple: bool = False) -> str` → new password (16 chars with upper, lower, digit and one of `!@#$%`; `simple=True` → 14 chars letters+digits), saved before it is used.
- Produces: `site_accounts.hosts() -> list[str]` (names only, for a future app screen; no passwords).
- Storage: `core.SECRETS / "site_accounts.json"`, mode 0600, written atomically (temp file + `os.replace`). Host key = lowercase hostname without `www.`.

- [ ] **Step 1: Failing tests:** create→get round trip; `simple=True` has no symbols and length 14; generated password always contains each required class (100 samples); file mode 0600 (skip on Windows like `test_keys_and_career.py`); `repr`/`str` of the module's data never printed (`hosts()` returns names only); `www.Careers.Acme.com` and `careers.acme.com` map to one key.
- [ ] **Step 2: Run** — FAIL (module missing).
- [ ] **Step 3: Implement** with `secrets.choice` (never `random`).
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(accounts): store one generated password per career site in the secrets volume`.

### Task 3: read the verification code or link from Gmail

**Files:**
- Create: `backend/hub/verify_mail.py`
- Test: `backend/tests/test_verify_mail.py`

**Interfaces:**
- Produces: `verify_mail.extract(subject: str, body: str, host: str) -> dict` → `{"code": str|None, "link": str|None}`. Code: 4–8 digits next to a word like code/OTP/verification/one-time; link: first `https` URL whose host shares the registrable domain with `host` or a known ATS mail domain and whose path/query contains verify|confirm|activate|token.
- Produces: `verify_mail.wait_for(profile: dict, host: str, since: float, timeout: float = 180, poll: float = 10, imap_factory=None, sleep=time.sleep) -> dict` → same shape; `{}` on timeout or when Gmail is not configured. Uses the credentials pattern of `email_scanner.scan_inbox` (`hr_outreach.gmail_user`, `core.read_secret("gmail_app_password")`), `select("INBOX", readonly=True)`, then `[Gmail]/Spam` when nothing found, search `SINCE <date>` newest first, ignores messages dated before `since`.

- [ ] **Step 1: Failing tests:** `extract` on a Workday-style mail ("Your verification code is 482913") → code `482913`; on a Greenhouse-style mail with `https://boards.greenhouse.io/verify?token=abc` → that link; a body with a phone number and an order number but no code word → no code; a link to an unrelated domain → no link. `wait_for` with a fake IMAP object (class with `login/select/search/fetch/logout`) returning one message → result; with no gmail password → `{}` immediately.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement**; reuse the body-decoding helpers from `email_scanner.py` (import them; do not copy).
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(mail): find a career-site verification code or link in Gmail`.

### Task 4: account step in career_apply (sign up or sign in, verify, OTP, CAPTCHA)

**Files:**
- Create: `backend/hub/account_step.py` (keeps `career_apply.py` from growing; it is already >1200 lines)
- Modify: `backend/hub/career_apply.py` (`apply_one`: call the account step before `hand_over("login", ...)` in both places; pass `profile`/`dry`)
- Test: `backend/tests/test_account_step.py` (real Chromium + local HTML, same fixtures as `test_career_apply.py`: copy the `browser` fixture via a shared helper in `tests/conftest.py`)

**Interfaces:**
- Consumes: `site_accounts.get/create` (Task 2), `verify_mail.wait_for` (Task 3), `attention.ask_value` (Task 1).
- Produces: `account_step.kind(page) -> "signup" | "login" | None` — signup when a visible password field has a sibling confirm-password field, or the text matches `create (an |your )?account|sign ?up|register`; login when one password field and text matches `sign ?in|log ?in`; `None` otherwise.
- Produces: `account_step.run(page, F: dict, host: str, dry: bool, ask_value=attention.ask_value, wait_mail=verify_mail.wait_for, human=None) -> str` → `"ok" | "captcha" | "failed" | "skipped"`. Order: board host or dry → `"skipped"`; saved account → sign in; else sign up (email = `F["email"]`, names/phone from `F`, password from `site_accounts.create`, confirm field, tick every unchecked checkbox whose label matches terms|privacy|agree|consent but not marketing|newsletter|offers, submit); on a password-rule error message retry once with `simple=True`; "already registered/exists" → switch to sign in; then email verification via `wait_mail` (code → fill the code field; link → `page.goto(link)`); if a visible OTP/code field remains and no mail code came → `ask_value("otp", host, page.url, "Enter the code <host> sent to your phone/email")`; CAPTCHA (`_blockers(page)["challenge"]`) at any point → `"captcha"` (the caller hands it to the user).
- `apply_one` change: where it now calls `hand_over("login", ...)`, first call `account_step.run(...)`; on `"ok"` re-run `_reach_form` / continue the step loop; on `"captcha"` call `hand_over("human_check", ...)`; on `"failed"`/`"skipped"` keep today's `hand_over("login", ...)` path.

- [ ] **Step 1: Failing tests** with local pages:
  - `/signup.html` (email, password, confirm, terms checkbox, marketing checkbox, submit → `/verify.html` with a code field → `/ok.html` application form): `run` returns `"ok"`, terms box checked, marketing box unchecked, `site_accounts.get("127.0.0.1")` set, `ask_value` not called, fake `wait_mail` returned `{"code": "123456"}` and the code was typed.
  - Same page with fake `wait_mail` → `{}`: `ask_value` called once with kind `"otp"`, its value typed.
  - `/login.html` with a saved account: fills saved email/password, no `create` call.
  - `/signup.html` that shows "Password must not contain special characters" after the first submit: second submit uses a letters+digits password; stored password updated.
  - `/captcha-signup.html` (contains `<div class="g-recaptcha">`): returns `"captcha"`, nothing submitted.
  - `dry=True`: returns `"skipped"`, no request made (server access log empty for POST).
  - In `test_career_apply.py`: `test_login_wall_creates_account_then_applies` — `LOGIN_THEN_FORM`-style sign-up page leads to the form; `apply_one` status `applied`; `human` never called.
- [ ] **Step 2: Run** — FAIL (module missing).
- [ ] **Step 3: Implement** `account_step.py`; never log the password (log "account created for <host>").
- [ ] **Step 4: Run** `pytest tests/test_account_step.py tests/test_career_apply.py -q` — PASS; then the full suite.
- [ ] **Step 5: Commit** `feat(apply): create or sign in to career-site accounts and verify them by mail or in-app OTP`.

### Task 5: missing details — profile first, then ask the user, then remember

**Files:**
- Modify: `backend/hub/career_apply.py` (the `if bad:` block in the step loop)
- Modify: `backend/hub/form_qa.py` (new `remember(label, answer, company)` that inserts or updates the answered row by key)
- Test: `backend/tests/test_career_apply.py`

**Interfaces:**
- Consumes: `attention.ask_value("question", site, url, label_with_options, job)` (Task 1).
- Produces: `form_qa.remember(label: str, answer: str, company: str = "") -> None`.
- Behavior: profile and saved answers are already tried by `decide`. For each still-unresolved required field (not file/checkbox), ask the user (question text = label, plus `Options: a / b / c` when it is a choice), up to 3 questions per job; a reply is validated with `pick_option` for choices, saved with `form_qa.remember`, added to `F["qa"]`, and the step is re-planned. No reply → today's `unresolved` result (and `record_unanswered`).

- [ ] **Step 1: Failing tests** (pure, using the `field()` helper and a fake asker): an unresolved "Notice period (days)" text field gets the user's "30", is filled, and `form_qa.answers()[form_qa.key("Notice period (days)")] == "30"`; a select with options Yes/No and reply "yes" picks "Yes"; reply "maybe" for Yes/No → stays unresolved; a 4th question in one job is not asked.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** inside `apply_one` via a small helper `_ask_missing(descs, F, job, page, ask_value) -> bool` in `career_apply.py`.
- [ ] **Step 4: Run** — PASS; full suite.
- [ ] **Step 5: Commit** `feat(apply): ask the user for answers the profile lacks and remember them`.

### Task 6: app — answer box for otp / question

**Files:**
- Modify: `mobile/lib/ui/widgets/attention_banner.dart`
- Test: `mobile/test/attention_banner_test.dart`

**Interfaces:**
- Consumes: tool `attention_resolve` with `{"id": int, "value": String}`; `attention_list` rows with `needs_value`.
- Behavior: rows with `needs_value == true` show the reason, a `TextField` (numeric keyboard for `otp`, text for `question`), and a "Send" button that calls `attention_resolve(id, value)`; no "Open browser" button for these rows. Title: `otp` → "Code needed: {site}", `question` → "Question from {site}". Other kinds unchanged.

- [ ] **Step 1: Failing widget test:** pump `AttentionBanner` with a provider override returning one `otp` row; enter "482913"; tap "Send"; assert the fake client received `attention_resolve` with `{'id': 7, 'value': '482913'}`; assert "Open browser" is absent for that row.
- [ ] **Step 2: Run** `flutter test test/attention_banner_test.dart` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `flutter analyze` (no new issues) and `flutter test` — PASS.
- [ ] **Step 5: Commit** `feat(app): type an OTP or an answer for the bot right in the app`.

### Task 7: remote-browser session stays alive

**Files:**
- Modify: `backend/hub/browser_access.py` (`vnc_url`)
- Test: `backend/tests/test_api.py` (or the existing browser_access tests)

Evidence so far: no auth failures in the last 24 h (all `/internal/browser-auth` calls 204), cookie lives 30 min, x11vnc `-forever`, websockify and Caddy have no idle timeout. The only 2-minute value is `TICKET_TTL = 120` (link must be opened within 2 min). noVNC's `vnc.html` does not reconnect by default, so when the phone browser is backgrounded (e.g. switching to the app or SMS to read an OTP) the websocket drops and the page stays "Disconnected".

- [ ] **Step 1: Failing test:** `vnc_url("pw")` contains `reconnect=1` and `reconnect_delay=2000`, the password stays in the fragment.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** (append the two parameters to the query string).
- [ ] **Step 4: Run** — PASS. Manual check after deploy: open the remote browser on the phone, switch apps for 3 minutes, return: the screen reconnects.
- [ ] **Step 5: Commit** `fix(browser): reconnect the remote browser after the phone backgrounds it`.

### Task 8: stay on Moli unless a visible window is needed

**Files:**
- Modify: `backend/hub/career_apply.py` (`run_batch`)
- Test: `backend/tests/test_career_apply.py`

**Interfaces:**
- Behavior: `run_batch` always starts headless (`launch(p, headless=True)` → Moli when available). Jobs whose result is `captcha`, or `login` after a `failed` account step, are collected and, when `make_human(...)` is available and not dry, retried once in a visible persistent Chromium context (`.profiles/career`) with `human` set. OTP and questions never need the visible browser.
- [ ] **Step 1: Failing test** with an injected `launch` recorder: a batch of [form job, captcha job] → first pass headless for both, second pass headed only for the captcha job.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** (add an injectable `launch_fn=launch` parameter to `run_batch`).
- [ ] **Step 4: Run** — PASS; full suite.
- [ ] **Step 5: Commit** `perf(apply): run career forms on Moli and open Chromium only for a CAPTCHA`.

### Task 9: redirects from Naukri, LinkedIn and posts reach the account step

**Files:**
- Test: `backend/tests/test_keys_and_career.py`

- [ ] **Step 1: Tests:** jobs saved with `source` `jobspy-naukri`, `jobspy-linkedin` and `post`, `apply_type="external"` and a company `apply_url`, pass `career_apply.eligible` with `url` replaced by the company page; a job whose only URL is on `naukri.com` is not eligible (no account on the board).
- [ ] **Step 2: Run.** If they already pass, keep them as regression tests (no code change); if one fails, fix `eligible` minimally.
- [ ] **Step 3: Commit** `test(apply): company pages reached from Naukri, LinkedIn and posts are eligible`.

### Final verification

- [ ] Full backend suite; list every failure by name and compare with `main`.
- [ ] `flutter analyze` and `flutter test` in `mobile/`.
- [ ] OpenQodex review in a separate subagent (`npx -y openqodex review --agent`, then `--finalize`); answer every finding.
- [ ] Push the branch `fix/naukri-detail-rate-limit` (rename to `feat/auto-signup-otp`); the user merges. Deploy needs free disk (see brain): not part of this plan.
