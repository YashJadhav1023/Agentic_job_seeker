# Agentic Job Seeker

Your personal job-search agent, running 24/7 on a small cloud server, controlled from an Android app.

It started as the local `job-hub` MCP server and keeps all of its **32 tools** — job discovery on LinkedIn,
Naukri, Indeed, Glassdoor and Google Jobs, resume-vs-JD scoring, Easy-Apply bots, HR/recruiter email discovery
and verification, JD-tailored application emails with your resume attached, and a fully guarded autopilot.

```
 Android app (Flutter)  ──HTTPS + bearer token──▶  Caddy (auto TLS)  ──▶  FastAPI  ──▶  job-hub tools
 dashboard · jobs · contacts                       on one EC2 instance        │          (bots, mailer, SQLite)
 runs (live logs) · all 32 tools · settings                                   └─▶  /mcp  (Kiro / Claude can use it too)
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
- Daily caps, one email per company per 60 days, suppression list, bounce/unsubscribe handling,
  send window 09–19 Mon–Sat, and a kill switch (`autopilot_stop`, a toggle on the Home screen).
- The API refuses to start without a ≥32-char token; repeated bad tokens lock the client IP out for 15 minutes.
  Secrets (Gmail app password) are write-only through the API and stored with mode 600.
- Platform restrictions/captchas put that platform on cooldown and email outreach takes over.

## The 32 tools

`hub_status` `tracker_report` · `search_jobs` `multi_board_search` (JobSpy) `job_detail` `list_jobs`
`scan_hiring_posts` `hiring_post_queries` · `apply_jobs` `login_platform` `prepare_linkedin` `tailor_application`
`verify_resume_pdf` `guard_job_posting` · `autopilot_run` `autopilot_stop` `run_status` `cancel_run` ·
`find_contact` `find_hr_emails` `find_company_emails` `lead_finder_search` `mailscout_find_emails`
`verdict_email_tool` `verify_email` `add_hr_contact` `list_hr_contacts` `suppress_contact` ·
`compose_email` `send_email` `send_hr_emails` `generate_linkedin_pitch`

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
On a **Paid** plan with no credits you pay on-demand prices. ap-south-1 list prices (AWS Pricing API): `t4g.small` $0.0112/h ≈ $8.2/month, `t3.small` $0.0224/h ≈ $16.4/month, plus public IPv4 $0.005/h ≈ $3.6/month and 30 GB gp3 ≈ $2.5/month, so the default is roughly **$14–15/month** (check the [AWS Pricing Calculator](https://calculator.aws/) for your case). The budget alarm emails you as soon as a charge is forecast. Delete everything with `aws cloudformation delete-stack --stack-name agentic-job-seeker`.

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
3. Settings → upload your resume PDF, edit the profile (name, keywords, cities, CTC), add the Gmail app password.
4. Home → **Log in** next to LinkedIn/Naukri/Indeed → **Open remote browser** and sign in there. The session stays
   on your server, so the bots can apply for you.
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
