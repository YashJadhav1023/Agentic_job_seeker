# Apply pipeline fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Jobs found by any path (boards, Naukri, career sites, hiring posts) actually get applied to; when a site needs
a human (login, "are you human" check) the run pauses, the user gets a phone notification with a link to the remote
browser, and the run continues once the user is done.

**Evidence (server logs, 2026-10-30):** Naukri job pages show "check the box to let us know you're human" (0 applies,
logged as "No apply button"; the bot also only tried the first card per search because card handles go stale after
navigation). career_apply 0/49 live successes: 27 "page does not mention the job title" (Workday SPA not rendered yet,
Google Forms links), login walls (Workday, Deloitte), unmatched Degree/Branch/Gender selects; caps 3/run 5/day send
the rest to email. Email cap 50/day reached by 09:00 (134 deferred). Global search UI takes one city.

**Decision:** no CAPTCHA/bot-check bypass. Human checks and logins are handed to the user (pause + notify + resume).

## Files
- `backend/hub/attention.py` (new): human-help requests (DB table), notify, signed open link, wait loop.
- `backend/hub/notify.py`: `push()` = Telegram + ntfy (open-source push, profile `notifications.ntfy_topic`).
- `backend/hub/api.py`: `GET /api/v1/attention/open?id&k` (signed link -> browser cookie -> noVNC).
- `backend/hub/server.py`: tools `attention_list`, `attention_resolve`; global_search passes `locations`;
  apply_jobs runs Naukri headed when human help is on.
- `backend/tools/job-automation/platforms/naukri.py` + `utils/hubguard.py`: collect cards first, detect human
  check / login, ask the user and wait.
- `backend/hub/career_apply.py`: headed browser when human help is on; login/captcha -> ask + wait + continue; per-host
  saved sessions; render wait before the title check; form hosts (Google Forms etc.) skip the title check;
  degree/branch fuzzy options; gender from profile; caps 10/run 40/day.
- `backend/hub/globalsearch.py`: explicit locations always all searched.
- `mobile/lib/ui/screens/global_search_screen.dart`: city chips (add any city), auto-apply on by default.
- `mobile/lib/ui/screens/home_screen.dart`: scan-posts button applies; "needs you" banner (attention_list).

## Tasks (each: failing test -> code -> green)
1. attention + notify.push + api open link (tests: request/list/resolve, signed link expiry, wait returns on check).
2. naukri: `collect_cards` pure parse + `human_check(text)` (tests in tests/test_naukri_bot.py).
3. career_apply: degree/branch/gender decide(), form-host title skip, render wait helper, caps defaults, login -> ask.
4. globalsearch locations + server passthrough (tests in test_global_alarms.py style).
5. Flutter: chips + defaults + banner; `flutter analyze` + `flutter test`.
6. Full pytest, OpenQodex review (separate subagent), deploy, server profile caps (email 100, forms 10/40).
