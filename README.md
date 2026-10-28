# Agentic Job Seeker

A job-search agent that anyone can run for themselves: 24/7 on a small cloud server, controlled from an Android app,
driven by the resume you upload. Nothing about a particular person, role or city is built in; it all comes from your
profile and resume.

It started as the local `job-hub` MCP server and keeps all of its original tools — job discovery on LinkedIn,
Naukri, Indeed, Glassdoor and Google Jobs, resume-vs-JD scoring, Easy-Apply bots, HR/recruiter email discovery
and verification, JD-tailored application emails with your resume attached, and a fully guarded autopilot.

```
 Android app (Flutter)  ──HTTPS + bearer token──▶  Caddy (auto TLS)  ──▶  FastAPI  ──▶  job-hub tools
 dashboard · jobs · contacts                       on one EC2 instance        │          (bots, mailer, SQLite)
 runs (live logs) · all tools · settings                                   └─▶  /mcp  (Kiro / Claude can use it too)
```

| Folder | What it is |
| --- | --- |
| `backend/` | job-hub tools (`hub/`), vendored bots (`tools/`), FastAPI layer `hub/api.py`, Dockerfile, tests |
| `mobile/` | Flutter Android app (Material 3, Riverpod, go_router) |
| `deploy/` | `docker-compose.yml` + `Caddyfile` + `setup.sh`; `deploy/bootstrap-env.sh` (first-boot secrets from SSM Parameter Store); `deploy/aws/` least-privilege deployer policy |
| `.github/workflows/` | `ci.yml` (tests, Docker smoke test, APK build), and `deploy.yml` (CD over SSM). The AWS infrastructure lives in its own repo, [Agentic_job_seeker_Terraform](https://github.com/YashJadhav1023/Agentic_job_seeker_Terraform) |

## Safety model (unchanged from job-hub)

- Everything that applies or sends email is a **dry run by default**. Live actions additionally need
  `safety.confirmed_by_user: true` in your profile, and the app asks for confirmation.
- Daily caps, one email per company per 60 days, suppression list, bounce/unsubscribe handling, and a kill switch
  (`autopilot_stop`, a toggle on the Home screen). Emails may go out at any time by default; set
  `autopilot.send_window: [9, 19]` and/or `autopilot.send_days: [mon, tue, wed, thu, fri]` to restrict them.
- Up to 4 background tasks run at once (`JOBHUB_MAX_PARALLEL`). Tasks that use the same browser session
  (for example two LinkedIn runs, or the autopilot and a Naukri run) still wait for each other.
- The API refuses to start without a ≥32-char token; repeated bad tokens lock the client IP out for 15 minutes.
  Secrets (Gmail app password) are write-only through the API and stored with mode 600.
- Platform restrictions/captchas put that platform on cooldown and email outreach takes over.

## Resume, linked accounts and 24/7 runs

- **Resume.** Upload it in the app (Settings → Resume). The server keeps one live copy (plus the last 5 versions) and
  uses it everywhere: job scoring, the email attachment (sent under the file name you uploaded), LinkedIn Easy Apply
  and Indeed uploads. Naukri applies with the resume stored on your Naukri profile, so the upload dialog can also
  replace that one (`sync_resume`). `GET /api/v1/resume` / `resume_info` shows exactly which file is live.
- **Search follows the resume.** On upload, `search.skills` and contact fields still at placeholder values are filled
  from the resume. With `search.auto_from_resume: true` and an AI provider, the job titles and title filters are
  taken from it too (`resume_to_profile`).
- **Linked accounts.** Settings → Linked Accounts: LinkedIn, Naukri and Indeed are linked by signing in once in the
  remote browser (the session stays on your server; the app never sees the password) and can be disconnected again.
  Gmail is linked with its address plus a Google App Password; the login is tested before it is saved.
- **Always on.** `autopilot.schedule_every_hours` and `post_scan.schedule_every_hours` start the built-in scheduler
  (0 = off); the server then runs them around the clock. `scheduler_status` shows the last and next run.
- **Discovery.** LinkedIn and Naukri are searched with every `search.keywords` × `search.locations` pair, rotated across
  runs (`autopilot.max_search_queries` per run), any city name or `Remote`. Naukri reads a second result page when the
  first has too few new jobs. `autopilot.use_jobspy: true` adds JobSpy (no-login LinkedIn/Naukri/Indeed).

## AI hiring-post analysis (Kiro API key, Ollama or any OpenAI-compatible API)

`scan_hiring_posts` reads LinkedIn content search (and optionally your feed), skips posts it has seen before, and runs a
pipeline adapted from [job-search-agent](https://github.com/sanchittanwar7/job-search-agent): a regex gate drops
posts with no hiring intent, job seekers and announcements; an LLM then extracts company, role, location and minimum
experience and checks them against your profile; finally the profile's own rules are re-applied, so the model can
never widen the search. Several posts go into one request to keep cost down. Matches store published HR emails and
apply links, and can send a Telegram alert.

| `llm.provider` | Needs |
| --- | --- |
| `kiro` | `kiro-cli` in the image (`JOBHUB_INSTALL_KIRO=1` in `deploy/.env`, then rebuild) and a Kiro API key (Kiro Pro and above) saved in the app as `kiro_api_key` or `KIRO_API_KEY` in `deploy/.env`. It runs a tool-less agent with no MCP servers in an isolated folder, and post text is passed on stdin, so a post cannot make it run anything. |
| `ollama` | a reachable Ollama server (`llm.ollama_url`) and `llm.model`, e.g. `qwen3:8b` |
| `openai` | `llm.base_url`, `llm.model` and the `llm_api_key` secret (DeepSeek, OpenRouter, Groq, OpenAI ...) |
| `none` | nothing; the profile rules alone decide (previous behaviour) |

## Company career pages: reading jobs and filling the application form

`career_scan` finds open roles on company career pages (Firecrawl). `career_apply` (also run by the autopilot after the
platform bots and before the email step) opens each good job, follows its Apply button, fills the form from the profile
and resume, uploads the resume, and submits. It counts the job as applied only when a **new** confirmation appears on
the page.

It never guesses. It stops without submitting on a CAPTCHA, a login or sign-up wall, or any required field it cannot answer
from the profile and resume (it never invents employers, degrees, numbers or dates). Salary fields are filled only when
the unit is clear (LPA, monthly, per annum). Demographic questions get "prefer not to say", or stop the form if there is
no such option. Marketing opt-ins stay off. Hidden and off-screen fields are never touched. Free-text questions go to the
configured LLM (`llm.provider`); without one they stop the form. A job whose form cannot be completed stays eligible for
the email step, except when the form was submitted but unconfirmed (`form_unconfirmed`): then nothing else is sent.

Dry-run (the default) fills the form and saves a screenshot to `data/career_apply/` but never presses Submit. Live
submission needs `safety.confirmed_by_user: true`. Limits (`autopilot.career_apply_*` in the profile): 3 forms per run,
5 per day, one per company per 30 days, 2 attempts per job. Turn it off with `autopilot.career_apply: false`.

## API keys

Firecrawl, Kiro, the OpenAI-style LLM key and the Telegram token are read in one place (`hub/keys.py`): the server
environment first (filled from Parameter Store), then the app's secret files. `UNSET` and other placeholders count as
missing. A key the provider rejects (401/403) or that is out of credits (402, quota) is paused for 6 hours instead of being
retried on every run, you get one Telegram alert, and `hub_status` shows it under `key_cooldowns`. Saving a new key in the
app lifts the pause. Key values are masked in logs, run summaries and error text, and the vendored Naukri/Indeed/LinkedIn
bots run without any key, the Gmail app password or the API token in their environment.

## Naukri

`autopilot_run` discovers Naukri jobs read-only (it never clicks Apply there). The Naukri bot (`apply_jobs naukri`) clicks
Apply and fills the questionnaire, and logs an application only when Naukri shows a new confirmation. Jobs that say
"Apply on company site" are skipped (they are the career-form path), as are jobs you already applied to.

## The tools

`hub_status` `tracker_report` · `search_jobs` `multi_board_search` (JobSpy) `job_detail` `list_jobs`
`scan_hiring_posts` `hiring_post_queries` `career_scan` (Firecrawl) `career_apply` · `apply_jobs` `login_platform` `prepare_linkedin` `tailor_application`
`verify_resume_pdf` `guard_job_posting` · `autopilot_run` `autopilot_stop` `run_status` `cancel_run` ·
`find_contact` `find_hr_emails` `find_company_emails` `lead_finder_search` `mailscout_find_emails`
`verdict_email_tool` `verify_email` `add_hr_contact` `list_hr_contacts` `suppress_contact` ·
`compose_email` `send_email` `send_hr_emails` `generate_linkedin_pitch` ·
`resume_info` `resume_to_profile` `sync_resume` `unlink_platform` `scheduler_status`

The app reads the catalogue from `GET /api/v1/tools` and builds each tool's form from its JSON schema, so a tool
added on the server shows up in the app without an app update.

## Deploy on AWS (infrastructure is in a separate repo)

The AWS stack (EC2 + Elastic IP, security group with only 80/443 open, SSM-only IAM role, SSM Parameter Store, budget alarm)
is Terraform code in [Agentic_job_seeker_Terraform](https://github.com/YashJadhav1023/Agentic_job_seeker_Terraform), run from
its GitHub Actions (`plan` / `apply` / `destroy`). On first boot the instance clones this repo, runs `deploy/bootstrap-env.sh`
(generates the API token and passwords into Parameter Store, writes `deploy/.env`) and starts the stack.

1. In the Terraform repo: add secrets `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` and variable `BUDGET_EMAIL`, then run the workflow with `plan`, then `apply`.
2. In this repo add the same two AWS secrets (policy: `deploy/aws/deployer-policy.json`) and add yourself as a required reviewer on the `production` environment.
3. Read the API token: `aws ssm get-parameter --name /agentic-job-seeker/secret/JOBHUB_API_TOKEN --with-decryption --query Parameter.Value --output text`.
4. From then on every push to `main` that passes CI is deployed by the **Deploy** workflow.

Without your own domain the server URL is `https://<elastic-ip-with-dashes>.sslip.io` (free wildcard DNS, real Let's Encrypt certificate).

**Cost — read this.** AWS has no "free forever" option for an always-on server. Accounts created after
15 July 2025 get a Free plan with up to $200 of credits for 6 months; the eligible instance types
(`t3.micro`, `t3.small`, `t4g.*`, `c7i-flex.large`, `m7i-flex.large`) are only free while those credits last.
On a **Paid** plan with no credits you pay on-demand prices. ap-south-1 list prices (AWS Pricing API): `t4g.small` $0.0112/h ≈ $8.2/month, `t3.small` $0.0224/h ≈ $16.4/month, plus public IPv4 $0.005/h ≈ $3.6/month and 30 GB gp3 ≈ $2.5/month, so the default is roughly **$14–15/month** (check the [AWS Pricing Calculator](https://calculator.aws/) for your case). The budget alarm emails you when a charge is forecast. Remove everything with the Terraform repo workflow's `destroy` action.

Any other Linux VM works the same way: install Docker, clone, `cd deploy && ./setup.sh <domain> && docker compose up -d --build`.

## CI/CD (GitHub Actions)

- **CI** (`ci.yml`, every push/PR): backend `pytest`, Docker image build + `/health` smoke test,
  `flutter analyze` + `flutter test` + release APK (downloadable artifact; attached to a GitHub Release on `v*` tags).
- **Deploy** (`deploy.yml`, after CI passes on `main`): finds the instance by its `Project` tag and runs
  `git checkout <sha> && docker compose up -d --build` there through SSM Run Command, then checks `/health`.
  It skips itself until the AWS secrets and the stack exist.
- Optional APK signing secrets: `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`,
  `ANDROID_KEY_PASSWORD` (otherwise the APK is debug-signed, which installs fine for personal use).

## Using the app

1. Download `agentic-job-seeker.apk` from the latest CI run (Actions → CI → Artifacts) or a Release, and install it
   (allow "install unknown apps" for your browser/Files).
2. Settings → server URL + API token → **Save & test connection**.
3. Settings → upload your resume PDF (contact details and skills are read from it), check the profile (keywords,
   cities, CTC).
4. Settings → Linked Accounts: link Gmail (address + app password) and LinkedIn / Naukri / Indeed (**Link** opens the
   remote browser; sign in there). Sessions stay on your server, so the bots can apply for you.
5. Home → **Autopilot (dry run)**, then watch it under **Runs**. When the drafts look right, set
   `safety.confirmed_by_user: true` in the profile and run live.

## Local development

```bash
cd backend && uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q tests                      # resume-based tests need JOBHUB_PROFILE=<your profile.yaml>
JOBHUB_API_TOKEN=$(openssl rand -hex 32) .venv/bin/uvicorn api:create_app --factory --app-dir hub --port 8000
.venv/bin/python hub/server.py                           # same tools as a stdio MCP server

cd mobile && flutter pub get && flutter analyze && flutter test && flutter build apk
```

## Credits

[job-automation](https://github.com/Nawaz-B-04/job-automation) ·
[multi-platform-job-apply-bot](https://github.com/RayeesYousufGenAi/multi-platform-job-apply-bot) ·
[lead-finder](https://github.com/wasaybuilds/lead-finder) ·
[ai-job-search](https://github.com/MadsLorentzen/ai-job-search) ·
[JobSpy](https://github.com/speedyapply/JobSpy) · [MailScout](https://github.com/batuhanaky/mailscout).
Pinned upstream SHAs: `backend/tools.lock`. Use responsibly and within each job board's terms.


## WhatsApp (upstream Evolution Go, optional)

The `whatsapp` compose profile runs [Evolution Go](https://github.com/evolution-foundation/evolution-go) and its PostgreSQL next to the app
(`COMPOSE_PROFILES=whatsapp` is an SSM env parameter; `JOBHUB_EVOLUTION_KEY` and `EVOLUTION_DB_PASSWORD` are generated by `deploy/bootstrap-env.sh`).
It is not published to the internet. One-time setup:

1. Deploy, then on the server run `cd /opt/ajs/deploy && ./bootstrap-env.sh && docker compose up -d`.
2. From your computer: `aws ssm start-session --target <instance-id> --document-name AWS-StartPortForwardingSession --parameters '{"portNumber":["4000"],"localPortNumber":["4000"]}'`,
   open `http://localhost:4000/manager/login`, enter `http://localhost:4000` and the admin key
   (`aws ssm get-parameter --name /agentic-job-seeker/secret/JOBHUB_EVOLUTION_KEY --with-decryption --query Parameter.Value --output text`),
   and complete the license registration it shows (needed once; the API answers 503 until then).
3. In the app: WhatsApp > Link WhatsApp, scan the QR (WhatsApp > Linked devices). Use a separate number: Evolution Go is unofficial.
4. Upload a list (PDF, Word, Excel, CSV), edit the message, Preview, then Send. Sending is preview-only until the profile's live-actions switch is on
   **and** you confirm that the people expect your message; daily cap 20 (max 50), 40-110 s pauses, nobody twice in 30 days, "STOP" replies opt out for good.
