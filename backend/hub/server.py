#!/usr/bin/env python3
"""job-hub: one stdio MCP server routing to local open-source job tools.

Backends (vendored in ./tools, pinned SHAs in tools.lock):
  - job-automation          -> Naukri + Indeed auto-apply   (patched: title filter, dry-run, system Chrome)
  - multi-platform-job-apply-bot -> LinkedIn/Foundit/Glassdoor (assisted: needs your manual login)
  - lead-finder             -> HR / recruiter email discovery
  - hub mailer              -> Gmail SMTP outreach from profile hr_outreach.gmail_user
Everything real (submitting applications, sending mail) defaults to DRY-RUN.
"""
import json, os, random, re, sys, threading, time, traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import autopilot  # noqa: E402
import composer  # noqa: E402
import contacts  # noqa: E402
import keys  # noqa: E402
import llm  # noqa: E402
import resume  # noqa: E402
import scheduler  # noqa: E402

SERVER = {"name": "agentic-job-seeker", "version": "1.0.0"}


def _s(desc, **props):
    return {"type": "object", "properties": props, "additionalProperties": False, "description": desc}


TOOLS = {
    "hub_status": ("Readiness report: profile, missing fields, credentials present, daily counters, safety gate. Call first.",
                   _s("")),
    "login_platform": ("Open a visible Chrome so YOU log in to naukri|indeed|linkedin (Google/OTP fine). Saves only the browser "
                       "session in .profiles/ - the hub never sees your password. Close the window when done.",
                       _s("", platform={"type": "string", "enum": ["naukri", "indeed", "linkedin"]})),
    "apply_jobs": ("Search + auto-apply on naukri|indeed|linkedin (Easy Apply) using profile.yaml and your saved login session. "
                   "DRY-RUN by default (logs what it would apply to). Runs in background; poll run_status.",
                   _s("", platform={"type": "string", "enum": ["naukri", "indeed", "linkedin"]},
                      limit={"type": "integer", "minimum": 1, "maximum": 30},
                      dry_run={"type": "boolean", "default": True},
                      headless={"type": "boolean", "default": True,
                                "description": "Run hidden in the background (default). false = show the browser."})),
    "search_jobs": ("Discover jobs via LinkedIn's PUBLIC job pages (no login) using the ai-job-search CLI; results are filtered "
                    "by profile title include/exclude (drops Senior/Lead etc.). Searching is read-only. save=true stores and scores the "
                    "matches in the hub; auto_apply=true also applies to the good ones (company form, else a tailored email), "
                    "DRY-RUN unless dry_run=false and the profile allows live actions.",
                    _s("", query={"type": "string", "description": "e.g. 'devops engineer'; default: profile keywords[0]"},
                       location={"type": "string", "description": "any city or Remote (default: first profile location)"},
                       days={"type": "integer", "enum": [1, 7, 14, 30]}, limit={"type": "integer", "minimum": 1, "maximum": 30},
                       save={"type": "boolean", "default": False}, auto_apply={"type": "boolean", "default": False},
                       apply_limit={"type": "integer", "minimum": 1, "maximum": 30, "default": 10}, dry_run={"type": "boolean"})),
    "job_detail": ("Full description of a LinkedIn job id/url from search_jobs (for fit check / tailoring).",
                   _s("", id={"type": "string"})),
    "tailor_application": ("Return the ai-job-search workspace + exact workflow files an agent should follow to evaluate fit and "
                           "draft a tailored CV/cover letter (/apply) for a job. Does not call an LLM itself.",
                           _s("", job_url_or_id={"type": "string"})),
    "prepare_linkedin": ("Write config for the assisted LinkedIn/Foundit/Glassdoor bot and return the terminal command. "
                         "You must log in manually in the browser it opens.", _s("")),
    "scan_hiring_posts": ("Scan recent LinkedIn HIRING POSTS (saved session, hidden browser) for roles that fit the profile. "
                          "Posts pass a regex gate, then an LLM (profile llm.provider: kiro with KIRO_API_KEY, ollama, openai) "
                          "extracts company/role/location and checks them against your preferences; profile rules are used "
                          "when no LLM is set. Stores published HR emails as contacts and apply links in data/post_leads.jsonl, "
                          "optional Telegram alert. BULK: scrolls every results page until it stops growing (dozens of posts per query, many queries). "
                          "auto_apply=true also emails the published HR addresses and applies through the posts' company links "
                          "(DRY-RUN unless dry_run=false and the profile allows live actions). Runs in background; poll run_status.",
                          _s("", queries={"type": "array", "items": {"type": "string"},
                                          "description": "optional LinkedIn content-search queries; default rotates through keywords x locations"},
                             max_posts={"type": "integer", "minimum": 5, "maximum": 300, "default": 80,
                                        "description": "stop scrolling a query after this many posts"},
                             scrolls={"type": "integer", "minimum": 3, "maximum": 60, "default": 30},
                             max_queries={"type": "integer", "minimum": 1, "maximum": 30, "default": 12},
                             auto_apply={"type": "boolean", "default": False}, dry_run={"type": "boolean"})),
    "career_scan": ("Read open roles from company CAREER PAGES with Firecrawl (find the careers page, extract the jobs, keep "
                    "title-filtered ones) and add them to the job pipeline for scoring + email. Default: known companies not "
                    "scanned yet. Runs in background; poll run_status. Sends nothing.",
                    _s("", companies={"type": "array", "items": {"type": "string"},
                                      "description": "company names or domains; default = known HR-contact domains"},
                       limit={"type": "integer", "minimum": 1, "maximum": 20, "default": 10})),
    "career_apply": ("Fill in and submit the APPLICATION FORM of jobs on company career pages (headless browser): opens the job, "
                     "follows Apply, fills fields from profile.yaml + resume, uploads the resume, answers free-text questions "
                     "with the LLM, then submits and requires a confirmation. Stops without submitting on CAPTCHA, login or any "
                     "required question it cannot answer. DRY-RUN by default (fills, screenshots, never submits). "
                     "Background; poll run_status.",
                     _s("", job_ids={"type": "array", "items": {"type": "integer"}, "description": "job ids from list_jobs; "
                                     "default = best eligible career-page jobs"},
                        limit={"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                        dry_run={"type": "boolean", "default": True})),
    "hiring_post_queries": ("Ready-made search queries to find public LinkedIn/web HIRING POSTS that include an application "
                            "email. Run them with a web search tool, then store hits with add_hr_contact.",
                            _s("", city={"type": "string"})),
    "add_hr_contact": ("Store an HR/recruiter email found in a public hiring post (source_url required). Stored as high confidence "
                       "only if the post explicitly asks candidates to email their resume.",
                       _s("", email={"type": "string"}, company={"type": "string"}, source_url={"type": "string"},
                          role={"type": "string"}, city={"type": "string"}, post_asks_for_resume={"type": "boolean"})),
    "find_hr_emails": ("Discover HR/recruiter emails at companies in a target city (public sources + MX checks). "
                       "Stored in hub DB. Background; poll run_status.",
                       _s("", city={"type": "string", "description": "a city from hr_outreach.cities in the profile"},
                          industry={"type": "string"},
                          limit={"type": "integer", "minimum": 1, "maximum": 100, "default": 100,
                                 "description": "companies to check"})),
    "list_hr_contacts": ("List stored HR contacts.", _s("", min_confidence={"type": "string", "enum": ["low", "medium", "high"]},
                                                        status={"type": "string"}, limit={"type": "integer"})),
    "send_hr_emails": ("Email the best eligible HR contacts from your Gmail (profile hr_outreach.gmail_user) with resume attached. DRY-RUN by "
                       "default (returns the drafts). Respects daily cap, one-per-company window, min confidence.",
                       _s("", limit={"type": "integer", "minimum": 1, "maximum": 20}, dry_run={"type": "boolean", "default": True})),
    "send_email": ("Send one custom email from the Gmail account. DRY-RUN by default.",
                   _s("", to={"type": "string"}, subject={"type": "string"}, body={"type": "string"},
                      attach_resume={"type": "boolean", "default": True}, dry_run={"type": "boolean", "default": True})),
    "run_status": ("Status + log tail for a background run.", _s("", run_id={"type": "string"}, tail={"type": "integer"})),
    "tracker_report": ("Counts of applications, HR contacts, emails sent; recent activity. Use to write brain notes.", _s("")),
    "autopilot_run": ("END-TO-END: discover jobs on LinkedIn + Naukri (with full JD) -> score vs resume -> optional Easy-Apply bots -> "
                      "for good jobs find the poster's/company's email (JD, company site, verified role inbox) and send a JD-tailored "
                      "professional email with the resume attached. If a platform shows a restriction it goes on cooldown and email "
                      "takes over. Automated guards instead of prompts (caps, dedupe, suppression, send window, data/STOP). "
                      "Dry-run unless profile.yaml has dry_run_default:false and confirmed_by_user:true. Background; poll run_status.",
                      _s("", platforms={"type": "array", "items": {"type": "string", "enum": ["linkedin", "naukri"]}},
                         dry_run={"type": "boolean"}, discover={"type": "boolean", "default": True},
                         apply={"type": "boolean", "default": True, "description": "also run the Easy-Apply/Naukri bots"},
                         email={"type": "boolean", "default": True}, max_discover={"type": "integer", "minimum": 1, "maximum": 80})),
    "list_jobs": ("Jobs discovered by the autopilot with score, matched skills, status, chosen contact.",
                  _s("", status={"type": "string"}, min_score={"type": "number"}, limit={"type": "integer", "minimum": 1, "maximum": 100})),
    "find_contact": ("Find the best email to apply to for a company / job (address in the JD, company site, or SMTP-verified role "
                     "inbox like careers@). Returns candidates with confidence and how each was found.",
                     _s("", company={"type": "string"}, jd_text={"type": "string"}, website={"type": "string"},
                        job_id={"type": "integer", "description": "use a job from list_jobs instead of company/jd_text"},
                        probe={"type": "boolean", "default": True})),
    "verify_email": ("Check an address: syntax, MX, SMTP mailbox probe (or Reacher if REACHER_URL set). "
                     "Result: valid | catch_all | invalid | unknown | no_mx.", _s("", email={"type": "string"})),
    "compose_email": ("Draft the professional application email for a job (job_id from list_jobs, or title+company+jd_text) from the "
                      "JD and your resume. Returns subject/body; the resume PDF is attached automatically on send. Does not send.",
                      _s("", job_id={"type": "integer"}, title={"type": "string"}, company={"type": "string"}, jd_text={"type": "string"},
                         source={"type": "string"}, cold={"type": "boolean", "default": False})),
    "generate_linkedin_pitch": ("Generate a short, tailored LinkedIn connection pitch (max 300 chars) for the candidate, from the profile, resume and JD.",
                                _s("", job_id={"type": "integer"}, title={"type": "string"}, company={"type": "string"}, jd_text={"type": "string"},
                                   recruiter={"type": "string"})),
    "cancel_run": ("Stop a background run (apply / autopilot / find_hr_emails).", _s("", run_id={"type": "string"})),
    "autopilot_stop": ("Kill-switch: on=true creates data/STOP so every autopilot stage stops at its next check; on=false removes it.",
                       _s("", on={"type": "boolean"})),
    "suppress_contact": ("Never email this address or domain again (opt-out / bounce).",
                         _s("", email_or_domain={"type": "string"}, reason={"type": "string"})),
    "guard_job_posting": ("Sanitize a job description and detect prompt-injections, hidden blocks, zero-width characters, or homoglyphs (ai-job-search posting_guard).",
                          _s("", jd_text={"type": "string"}, mode={"type": "string", "enum": ["text", "html"], "default": "text"})),
    "verify_resume_pdf": ("Verify candidate resume PDF for ATS text extraction, expected page count, and required skill keywords (ai-job-search verify_pdf).",
                          _s("", resume_path={"type": "string"}, expected_pages={"type": "integer", "default": 1},
                             required_keywords={"type": "array", "items": {"type": "string"}})),
    "find_company_emails": ("Discover and verify company email addresses using multi-source open-source discovery "
                            "(crawling team/about/contact/career pages, ld+json extraction, common prefix probing, "
                            "and pattern inference for decision-makers) with multi-tier verification "
                            "(DNS/MX, catch-all, SMTP probe, and HTTPS soft-signals via M365 and Gravatar). "
                            "Provide either company name or domain, and optional person names.",
                            _s("", company={"type": "string", "description": "e.g. 'Stripe', 'Acme'"},
                               domain={"type": "string", "description": "e.g. 'stripe.com' (resolved automatically if omitted)"},
                               names={"type": "array", "items": {"type": "string"},
                                      "description": "optional person names to find email variants for, e.g. ['Patrick Collison']"},
                               roles={"type": "array", "items": {"type": "string"},
                                      "description": "optional roles to target, e.g. ['founder', 'hr', 'engineering']"},
                               check_prefixes={"type": "boolean", "default": True,
                                               "description": "check standard role inboxes (careers@, hr@, contact@, etc.)"},
                               crawl_depth={"type": "integer", "minimum": 1, "maximum": 15, "default": 8,
                                            "description": "max pages to crawl on company website"},
                               verify={"type": "boolean", "default": True,
                                       "description": "verify deliverability via MX, SMTP, M365, and Gravatar"},
                               limit={"type": "integer", "minimum": 1, "maximum": 100, "default": 100,
                                      "description": "max email addresses returned"})),
    "mailscout_find_emails": ("Find business emails and test deliverability using the MailScout library (batuhanaky/mailscout). "
                              "Generates common role prefixes and person name variants with SMTP deliverability checks.",
                              _s("", domain={"type": "string", "description": "target domain, e.g. 'stripe.com'"},
                                 names={"type": "array", "items": {"type": "string"},
                                        "description": "optional person names, e.g. ['Patrick Collison']"},
                                 check_prefixes={"type": "boolean", "default": True},
                                 check_variants={"type": "boolean", "default": True},
                                 check_catchall={"type": "boolean", "default": True},
                                 normalize={"type": "boolean", "default": True},
                                 smtp_timeout={"type": "integer", "default": 2})),
    "verdict_email_tool": ("Discover, verify, and audit company emails using the Verdict engine (omm9846/verdict). "
                           "Features 15-route public web harvesting, pattern inference, RFC 3463 SMTP gate, "
                           "enterprise gateway checks, HTTPS soft-signals (Gravatar + M365 GetCredentialType), and DNS audit.",
                           _s("", action={"type": "string", "enum": ["discover", "verify", "audit"], "default": "discover"},
                              domain={"type": "string", "description": "e.g. 'stripe.com'"},
                              email={"type": "string", "description": "target address for 'verify' action"},
                              person={"type": "string", "description": "person name for 'discover' pattern inference"},
                              mail_from={"type": "string", "description": "sender address for probe"})),
    "lead_finder_search": ("Discover company leads, decision makers, and HR contacts using Lead Finder (wasaybuilds/lead-finder). "
                           "Crawls company sites (About, Team, Careers, Contact, ld+json schema.org), extracts founders/CEOs/HR, "
                           "infers company email patterns, and categorizes into personal, HR, and generic inboxes.",
                           _s("", domain={"type": "string", "description": "target company domain to crawl directly (e.g. 'stripe.com')"},
                              company={"type": "string", "description": "target company name (optional if domain given)"},
                              industry={"type": "string", "description": "industry for company discovery (e.g. 'fintech', 'devops')"},
                              city={"type": "string", "description": "city for company discovery (e.g. 'Pune', 'Bangalore')"},
                              country={"type": "string", "description": "country for company discovery (e.g. 'India', 'United States')"},
                              roles={"type": "array", "items": {"type": "string", "enum": ["founder", "owner", "ceo", "cto", "cmo", "hr"]},
                                     "default": ["founder", "ceo", "hr"]},
                              limit={"type": "integer", "minimum": 1, "maximum": 15, "default": 5},
                              verify={"type": "boolean", "default": True})),
    "multi_board_search": ("Discover jobs on LinkedIn, Indeed, Naukri, Glassdoor and Google Jobs in ONE call via JobSpy "
                           "(speedyapply/JobSpy). Filtered by profile title rules; save=true stores them in the hub so the "
                           "autopilot scores/emails them. No login, read-only towards the boards.",
                           _s("", query={"type": "string", "description": "default: profile keywords[0]"},
                              location={"type": "string", "description": "default: profile locations[0]"},
                              sites={"type": "array", "items": {"type": "string",
                                     "enum": ["linkedin", "indeed", "naukri", "glassdoor", "google"]}},
                              hours_old={"type": "integer", "minimum": 1, "maximum": 720, "default": 72},
                              limit={"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                              fetch_description={"type": "boolean", "default": False},
                              save={"type": "boolean", "default": False})),
    "resume_info": ("Which resume the server is using right now: file name, size, hash, upload time, first line, skills found.",
                    _s("")),
    "resume_to_profile": ("Read the uploaded resume and update the profile from it: contact fields still at placeholder "
                          "values, search.skills, and (replace_search=true, needs an LLM) the job titles and title rules "
                          "used to search. Preview unless apply=true.",
                          _s("", apply={"type": "boolean", "default": False}, replace_search={"type": "boolean", "default": False},
                             use_llm={"type": "boolean", "default": True})),
    "sync_resume": ("Replace the resume on your Naukri profile with the uploaded one (Naukri applies with the resume stored "
                    "on your Naukri profile). DRY-RUN by default. Background; poll run_status.",
                    _s("", dry_run={"type": "boolean"})),
    "unlink_platform": ("Remove a saved LinkedIn / Naukri / Indeed login session from the server.",
                        _s("", platform={"type": "string", "enum": ["linkedin", "naukri", "indeed"]})),
    "scheduler_status": ("Built-in 24/7 scheduler: interval, last and next run for the autopilot and the post scan "
                         "(autopilot.schedule_every_hours / post_scan.schedule_every_hours in the profile).", _s("")),
    "global_search": ("GLOBAL SEARCH: every profile keyword x location (or your own query) across LinkedIn, Indeed, Naukri and Google Jobs; "
                      "matches are filtered by your title rules, saved, scored against the resume and ranked. Read the ranked list with "
                      "search_results(run_id). auto_apply=true then applies to the good ones (company form, else a tailored email to the "
                      "verified contact) within your caps. Background run.",
                      _s("", query={"type": "string", "description": "one job title; default: all profile keywords"},
                         location={"type": "string", "description": "one city or Remote; default: all profile locations"},
                         sites={"type": "array", "items": {"type": "string", "enum": ["linkedin", "indeed", "naukri", "glassdoor", "google"]}},
                         hours_old={"type": "integer", "minimum": 1, "maximum": 720, "default": 168},
                         per_search={"type": "integer", "minimum": 5, "maximum": 50, "default": 20},
                         auto_apply={"type": "boolean", "default": False},
                         apply_limit={"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
                         dry_run={"type": "boolean"})),
    "search_results": ("Ranked jobs found by a finished global_search run (score, matched skills, status, apply link).",
                       _s("", run_id={"type": "string"})),
    "apply_to_jobs": ("Apply to jobs you picked (ids from search_results / list_jobs): fills the company career-page form when the job is on "
                      "the company site, otherwise finds the verified contact and sends a JD-tailored email with your resume. DRY-RUN unless "
                      "dry_run=false and the profile allows live actions; daily caps, send window and suppression list always apply. Background run.",
                      _s("", job_ids={"type": "array", "items": {"type": "integer"}}, dry_run={"type": "boolean"})),
    "alarm_list": ("Alarms: clock times at which the server runs tasks by itself (find HR emails, send emails, bulk email, LinkedIn "
                   "apply, Naukri apply, post scan, global search, autopilot), with next and last run.", _s("")),
    "alarm_set": ("Create or change an alarm (like a phone alarm). time HH:MM 24h server-local; days empty = every day; tasks any of "
                  "find_emails, send_emails, bulk_email, linkedin_apply, naukri_apply, scan_posts, global_search, autopilot or ['all']. "
                  "Tasks start together and wait in the run queue (3 at a time). dry_run unset = follow the profile safety setting.",
                  _s("", id={"type": "string", "description": "omit to create a new alarm"}, label={"type": "string"},
                     time={"type": "string", "description": "HH:MM"},
                     days={"type": "array", "items": {"type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}},
                     tasks={"type": "array", "items": {"type": "string", "enum": ["find_emails", "send_emails", "bulk_email", "linkedin_apply",
                                                                                 "naukri_apply", "scan_posts", "global_search", "autopilot", "all"]}},
                     enabled={"type": "boolean"}, dry_run={"type": "boolean"}, auto_apply={"type": "boolean"},
                     bulk_limit={"type": "integer", "minimum": 1, "maximum": 100})),
    "alarm_delete": ("Delete an alarm.", _s("", id={"type": "string"})),
    "alarm_run_now": ("Run an alarm's tasks right now (id), or an ad-hoc list (tasks=[...] or ['all']). Tasks queue 3 at a time.",
                      _s("", id={"type": "string"}, tasks={"type": "array", "items": {"type": "string"}}, dry_run={"type": "boolean"},
                         auto_apply={"type": "boolean"}, bulk_limit={"type": "integer", "minimum": 1, "maximum": 100})),
    "whatsapp_status": ("WhatsApp: is the gateway running, is the number linked, settings, contact counts, sent today and the daily cap.", _s("")),
    "whatsapp_link": ("Link your WhatsApp number: returns a QR code (data URL) to scan from the phone (Linked devices). Call again until "
                      "state is linked. With phone (the WhatsApp number, with country code) it returns an 8-character pairing code to "
                      "type in WhatsApp > Linked devices > Link with phone number instead (for when the app and WhatsApp are on the "
                      "same phone); then poll whatsapp_status.",
                      _s("", phone={"type": "string", "description": "WhatsApp number to link with a code instead of a QR"})),
    "whatsapp_unlink": ("Log the linked WhatsApp number out of the gateway.", _s("")),
    "whatsapp_settings": ("Message template ({name} {candidate} {role} {company}), daily cap (max 50), pause range, default country code, and "
                          "recipients_expect_my_message (must be true before any live send).",
                          _s("", template={"type": "string"}, daily_cap={"type": "integer", "minimum": 1, "maximum": 50},
                             delay_sec={"type": "array", "items": {"type": "integer"}}, default_country_code={"type": "string"},
                             recipients_expect_my_message={"type": "boolean"})),
    "whatsapp_import_numbers": ("Add phone numbers to the WhatsApp list from pasted text (one per line, optionally with a name). For a PDF, Word, "
                                "Excel or CSV file use the upload in the app. Nothing is sent by importing.",
                                _s("", text={"type": "string"}, company={"type": "string"}, default_country_code={"type": "string"})),
    "whatsapp_contacts": ("The WhatsApp list with status (new, sent, replied, optout, no_whatsapp, failed).",
                          _s("", status={"type": "string"}, limit={"type": "integer", "minimum": 1, "maximum": 500})),
    "whatsapp_send": ("Send each new number a personal message with your resume attached. DRY-RUN (preview) unless dry_run=false, and live also needs "
                      "the profile's live-actions switch and recipients_expect_my_message=true. Daily cap, pauses and 30-day no-repeat always apply. Background run.",
                      _s("", limit={"type": "integer", "minimum": 1, "maximum": 50}, dry_run={"type": "boolean"})),
    "queue_status": ("Run queue: what is running now, what is waiting (FIFO) and the limit (3 runs at once).", _s("")),
}
READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
READ_NET = dict(READ, openWorldHint=True)
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
ACT = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}  # applies / sends
ANNOT = {"hub_status": READ, "list_hr_contacts": READ, "run_status": READ, "tracker_report": READ, "hiring_post_queries": READ,
         "list_jobs": READ, "compose_email": READ, "generate_linkedin_pitch": READ, "tailor_application": READ, "search_jobs": dict(WRITE, openWorldHint=True), "job_detail": READ_NET,
         "find_contact": READ_NET, "verify_email": READ_NET, "guard_job_posting": READ, "verify_resume_pdf": READ,
         "find_company_emails": READ_NET, "mailscout_find_emails": READ_NET, "verdict_email_tool": READ_NET, "lead_finder_search": READ_NET, "multi_board_search": READ_NET,
         "login_platform": WRITE, "prepare_linkedin": WRITE, "add_hr_contact": WRITE,
         "find_hr_emails": dict(WRITE, openWorldHint=True), "career_scan": dict(WRITE, openWorldHint=True), "career_apply": ACT, "apply_jobs": ACT, "send_email": ACT, "send_hr_emails": ACT,
         "autopilot_run": ACT, "cancel_run": dict(WRITE, idempotentHint=True), "autopilot_stop": dict(WRITE, idempotentHint=True),
         "suppress_contact": dict(WRITE, idempotentHint=True), "resume_info": READ, "scheduler_status": READ,
         "resume_to_profile": WRITE, "sync_resume": ACT, "unlink_platform": dict(WRITE, destructiveHint=True),
         "global_search": dict(WRITE, openWorldHint=True), "search_results": READ, "apply_to_jobs": ACT, "queue_status": READ,
         "whatsapp_status": READ_NET, "whatsapp_link": dict(WRITE, openWorldHint=True), "whatsapp_unlink": dict(WRITE, destructiveHint=True),
         "whatsapp_settings": dict(WRITE, idempotentHint=True), "whatsapp_import_numbers": WRITE, "whatsapp_contacts": READ, "whatsapp_send": ACT,
         "alarm_list": READ, "alarm_set": dict(WRITE, idempotentHint=True), "alarm_delete": dict(WRITE, destructiveHint=True),
         "alarm_run_now": ACT}

REQUIRED = {"apply_jobs": ["platform"], "run_status": ["run_id"], "send_email": ["to", "subject", "body"],
            "find_hr_emails": ["city"], "login_platform": ["platform"], "add_hr_contact": ["email", "source_url"], "job_detail": ["id"], "tailor_application": ["job_url_or_id"],
            "verify_email": ["email"], "guard_job_posting": ["jd_text"], "cancel_run": ["run_id"], "autopilot_stop": ["on"], "suppress_contact": ["email_or_domain"],
            "mailscout_find_emails": ["domain"], "unlink_platform": ["platform"],
            "search_results": ["run_id"], "apply_to_jobs": ["job_ids"], "alarm_delete": ["id"]}


def tool_list():
    out = []
    for n, (d, sch) in TOOLS.items():
        sch = dict(sch)
        sch.pop("description", None)
        if n in REQUIRED:
            sch["required"] = REQUIRED[n]
        out.append({"name": n, "description": d, "inputSchema": sch, "annotations": ANNOT.get(n, WRITE)})
    return out


# ---------------------------------------------------------------- tools
def _has_session(pl):
    """True when the saved browser profile holds a signed-in session. An empty profile (Chrome opened, nobody signed in)
    also has a Cookies file, so LinkedIn and Naukri are judged by their sign-in cookie."""
    import login_helper
    d = core.ROOT / ".profiles" / pl
    if not d.exists():
        return False
    name = login_helper.SIGNED_IN.get(pl)
    return login_helper.has_cookie(d, name) if name else any(d.rglob("Cookies"))


def login_platform(a):
    """Open the sign-in browser on the server. Returns a single-use link (remote_browser_url) that opens it directly."""
    import browser_access
    pl = a["platform"]
    with core.db() as c:
        recent = [r["id"] for r in c.execute("SELECT id FROM runs WHERE kind=? ORDER BY started DESC LIMIT 3", ("login-" + pl,))]
    live = next((i for i in recent if core.run_status(i, 1).get("running")), None)
    if live:  # tapped twice: keep the open window instead of queueing a second one behind it
        r = {"run_id": live, "reused": True}
    else:
        r = core.spawn("login-" + pl, [core.PY, str(core.ROOT / "hub" / "login_helper.py"), pl], core.ROOT,
                       {"DISPLAY": os.environ.get("DISPLAY", ":0"), "PLAYWRIGHT_BROWSERS_PATH": str(core.ROOT / ".browsers")}, True, a)
    origin = browser_access.public_origin()
    if origin:
        r["remote_browser_url"] = f"{origin}/api/v1/browser/enter?t={browser_access.new_ticket(pl)}"
        r["note"] = (f"Open the link (valid {browser_access.TICKET_TTL // 60} min, single use) and sign in to {pl}. "
                     "The window closes by itself once you are signed in; then the account shows as linked.")
    else:
        r["note"] = "A Chrome window opened - log in there (Google sign-in works), then close the window."
    if r.get("queued"):
        r["note"] = "Another run is using this browser profile; the sign-in window opens when it finishes. " + r["note"]
    return r


def hub_status(_):
    p = core.load_profile()
    ok, why = core.real_actions_allowed(p)
    chrome = any((core.ROOT / ".browsers").glob("chromium-*/chrome-linux*/chrome"))
    return {
        "candidate": p["applicant"]["name"],
        "target_ctc": {"min": p["compensation"].get("min_ctc_inr"), "expected": p["compensation"].get("expected_ctc_inr")},
        "locations": p["search"]["locations"], "keywords": (p["search"].get("keywords") or [])[:4],
        "dry_run_default": p["safety"]["dry_run_default"],
        "real_actions_allowed": ok, "real_actions_blocked_reason": why,
        "missing_profile_fields": core.missing_profile_fields(p),
        "credentials": {
            "gmail_app_password": bool(core.read_secret("gmail_app_password")),
            "gmail_user": p["hr_outreach"].get("gmail_user", ""),
            "kiro_api_key": keys.present("kiro"),
            "llm_api_key": keys.present("llm"),
            "telegram_bot_token": keys.present("telegram"),
            "firecrawl_api_key": keys.present("firecrawl"),
        },
        "key_cooldowns": {k: v["cooldown"] for k, v in keys.report().items() if v["cooldown"]},
        "resume": resume.info(p),
        "llm": llm.status(p),
        "scheduler": scheduler.status(),
        "send_window": autopilot.send_window_text(autopilot.cfg(p)),
        "runs": {"running": core.running_runs(), "max_parallel": core.MAX_PARALLEL,
                 "queued": [q["run_id"] for q in core.queue_state()["queued"]]},
        "login_sessions_saved": {pl: _has_session(pl) for pl in ("naukri", "indeed", "linkedin")},
        "chromium_installed": chrome,
        "autopilot": {"stopped": autopilot.stopped(), "cooldowns": {k: autopilot.platform_blocked(k) for k in
                      ("linkedin", "naukri", "linkedin_search", "naukri_search") if autopilot.platform_blocked(k)},
                      "reacher_configured": bool(contacts._reacher_url())},
        "today": {"applications": core.applied_today(), "emails_sent": core.sent_today(),
                  "caps": {"applications": p["limits"]["max_applies_per_day_total"],
                           "emails": p["hr_outreach"]["max_emails_per_day"]}},
    }


def cover_note(p) -> str:
    """applicant.cover_note from the profile, else a short note built from the profile (no hardcoded person)."""
    a = p["applicant"]
    if a.get("cover_note"):
        return str(a["cover_note"]).strip()
    me = composer._identity(p)
    kws = (p["search"].get("keywords") or ["a suitable"])[:2]
    extra = []
    if (p.get("compensation") or {}).get("notice_period_days") == 0:
        extra.append("an immediate joiner")
    if str(a.get("willing_to_relocate", "")).lower().startswith("y"):
        extra.append("open to relocation")
    return (f"I am a {me} looking for a {' / '.join(kws)} role." + (f" I am {' and '.join(extra)}." if extra else "")
            + " My resume is attached.")


def _location(p) -> dict:
    """{city, state, country, display} from applicant.location (dict) or applicant.current_location (free text)."""
    a = p["applicant"]
    loc = a.get("location") if isinstance(a.get("location"), dict) else {}
    city = loc.get("city") or (a.get("current_location") or "").split(",")[0].strip()
    state, country = loc.get("state", ""), loc.get("country", "")
    if not state and city.lower() in CITY_MAP:
        state, country = CITY_MAP[city.lower()][0], country or "India"
    display = ", ".join(x for x in (city, state, country) if x)
    return {"city": city, "state": state, "country": country, "display": display or a.get("current_location", "")}


def _write_job_automation_config(p, limit):
    import yaml
    a, c, s = p["applicant"], p["compensation"], p["search"]
    cfg = {
        "keywords": s["keywords"],
        "filters": {"locations": [l.replace(" ", "-") for l in s["locations"]], "experience": s["experience_years_filter"],
                    "job_type": ["Full Time"]},
        "resume_path": a["resume_path"],
        "applicant": {"name": a["name"], "email": a["email"], "phone": a["phone"],
                      "expected_ctc": str(c["expected_ctc_inr"]), "current_ctc": str(c["current_ctc_inr"]),
                      "notice_period": str(c["notice_period_days"]), "experience_years": a["experience_years"],
                      "willing_to_relocate": a["willing_to_relocate"], "current_location": a["current_location"]},
        "cover_note": cover_note(p),
        "limits": {"max_per_platform": limit, "delay_between_jobs": p["limits"]["delay_between_jobs_sec"],
                   "delay_between_searches": p["limits"]["delay_between_searches_sec"]},
    }
    (core.TOOLS / "job-automation" / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))


def apply_jobs(a):
    p = core.load_profile()
    platform = a["platform"]
    dry = a.get("dry_run", p["safety"]["dry_run_default"])
    if not _has_session(platform):
        return {"error": f"no saved {platform} login session - call login_platform('{platform}') and log in first"}
    remaining = p["limits"]["max_applies_per_day_total"] - core.applied_today()
    if not dry:
        ok, why = core.real_actions_allowed(p)
        if not ok:
            return {"error": "real applications blocked: " + why, "hint": "run with dry_run=true to preview"}
        if remaining <= 0:
            return {"error": "daily application cap reached"}
    limit = max(1, min(a.get("limit", p["limits"]["max_applies_per_run_per_platform"]),
                       p["limits"]["max_applies_per_run_per_platform"], remaining if not dry else 30))
    headless = bool(a.get("headless", (p.get("browser") or {}).get("headless", True)))
    env = {"PYTHONUNBUFFERED": "1", "JOBHUB_DRY_RUN": "1" if dry else "0", "JOBHUB_TITLE_INCLUDE": p["search"]["title_include"],
           "JOBHUB_TITLE_EXCLUDE": p["search"]["title_exclude"], "JOBHUB_HEADLESS": "1" if headless else "0",
           "JOBHUB_PROFILES": str(core.ROOT / ".profiles"), "JOBHUB_LINKEDIN_PROFILE": str(core.ROOT / ".profiles" / "linkedin"),
           "DISPLAY": os.environ.get("DISPLAY", ":0"),
           "PLAYWRIGHT_BROWSERS_PATH": str(core.ROOT / ".browsers"),
           "JOBHUB_CHROME": str(next((core.ROOT / ".browsers").glob("chromium-*/chrome-linux*/chrome"), ""))}
    if platform == "linkedin":
        prepare_linkedin({}, limit=limit)
        argv, cwd = [core.PY, str(core.ROOT / "hub" / "linkedin_runner.py")], core.ROOT
    else:
        _write_job_automation_config(p, limit)
        argv, cwd = [core.PY, "main.py", "--platform", platform], core.TOOLS / "job-automation"
    r = core.spawn("apply-" + platform, argv, cwd, env, dry, {"platform": platform, "limit": limit})
    r["headless"] = headless
    r["note"] = (("Runs hidden in the background; if the saved session has expired the run stops - call login_platform. "
                  if headless else "A visible Chrome window opens (captchas/OTP: complete them yourself). ")
                 + ("DRY-RUN: nothing is submitted." if dry else f"LIVE: up to {limit} applications."))
    return r


AIJS = core.ROOT / "ai-job-search"
BUN = os.environ.get("JOBHUB_BUN") or str(core.ROOT / ".tools" / "node_modules" / ".bin" / "bun")
LI_CLI = ".agents/skills/linkedin-search/cli/src/cli.ts"
LOC_MAP = {"pune": "Pune, Maharashtra, India", "mumbai": "Mumbai, Maharashtra, India", "hyderabad": "Hyderabad, Telangana, India",
           "bangalore": "Bengaluru, Karnataka, India", "bengaluru": "Bengaluru, Karnataka, India",
           "navi mumbai": "Navi Mumbai, Maharashtra, India", "thane": "Thane, Maharashtra, India"}


_bun = autopilot._bun


def search_jobs(a):
    """LinkedIn public job search. save=true stores and scores the matches; auto_apply=true also applies to the good ones."""
    import re
    p = core.load_profile()
    s = p["search"]
    raw = (a.get("location") or (s.get("locations") or ["Remote"])[0]).strip()
    loc = autopilot.linkedin_location(raw)
    args = ["search", "-q", a.get("query") or s["keywords"][0], "-l", loc, "--limit", str(a.get("limit", 15)), "--format", "json"]
    if a.get("days"):
        args += ["--jobage", str(a["days"])]
    res = json.loads(_bun(args)).get("results", [])
    inc, exc = re.compile(s["title_include"], re.I), re.compile(s["title_exclude"], re.I)
    keep = [r for r in res if inc.search(r["title"]) and not exc.search(r["title"])]
    out = {"location": loc, "fetched": len(res), "kept_after_title_filter": len(keep),
           "jobs": [{k: r[k] for k in ("id", "title", "company", "location", "date", "url")} for r in keep],
           "note": "Salary is rarely listed; check job_detail."}
    if not (a.get("save") or a.get("auto_apply")):
        return out
    c = autopilot.cfg(p)
    saved = 0
    for r in keep:  # the job text is what scoring and the tailored email are built from
        try:
            jd = _bun(["detail", str(r["id"]), "--format", "plain"])[:6000] if saved < int(a.get("detail_limit", 8)) else ""
        except Exception:  # noqa: BLE001 - a missing description only lowers the score
            jd = ""
        if autopilot.save_job({"source": "linkedin", "ext_id": str(r["id"]), "url": r["url"], "title": r["title"],
                               "company": r.get("company", ""), "location": r.get("location", ""), "posted": r.get("date", ""),
                               "description": jd, "apply_type": "unknown"}):
            saved += 1
    out["scoring"] = autopilot.score_new(p, c)
    out["saved_to_hub"] = saved
    with core.db() as db:
        q = ",".join("?" * len(keep)) or "''"
        rows = [dict(x) for x in db.execute(
            f"SELECT id,title,company,score,status,reason,url FROM jobs WHERE url IN ({q}) ORDER BY score DESC", [r["url"] for r in keep])]
    out["jobs"] = rows
    if a.get("auto_apply"):
        good = [r["id"] for r in rows if r["status"] in ("scored", "queued")][: int(a.get("apply_limit", 10))]
        out["auto_apply"] = apply_to_jobs({"job_ids": good, "dry_run": a.get("dry_run")}) if good else {"note": "no job passed the score threshold"}
    return out


def global_search(a):
    opts = {k: a[k] for k in ("query", "location", "sites", "hours_old", "per_search", "auto_apply", "apply_limit", "dry_run") if k in a}
    env = {"PYTHONUNBUFFERED": "1"}
    r = core.spawn("search-global", [core.PY, str(core.ROOT / "hub" / "globalsearch.py"), json.dumps(opts)], core.ROOT, env, True, opts)
    r["note"] = ("Searching all boards. Open the results with search_results(run_id) when the run is finished."
                 + (" The good matches are then applied to (dry-run unless you turned that off)." if a.get("auto_apply") else ""))
    return r


def search_results(a):
    rid = re.sub(r"[^A-Za-z0-9._-]", "", str(a["run_id"]))
    f = core.ROOT / "data" / "search_results" / f"{rid}.json"
    if not f.exists():
        st = core.run_status(rid, 5)
        return {"error": "no results yet", "state": st.get("state"), "queued": st.get("queued", False)} if "error" not in st else st
    out = json.loads(f.read_text())
    ids = [j["id"] for j in out.get("jobs", [])]
    if ids:  # statuses move on after the search (emailed / applied / skipped)
        with core.db() as db:
            now = {r["id"]: dict(r) for r in db.execute(
                f"SELECT id,status,reason,contact FROM jobs WHERE id IN ({','.join('?' * len(ids))})", ids)}
        for j in out["jobs"]:
            j.update({k: v for k, v in now.get(j["id"], {}).items() if k != "id"})
    out["run_id"] = rid
    return out


def apply_to_jobs(a):
    """Apply to chosen jobs: company career-page form, else a tailored email to the verified contact."""
    ids = [int(x) for x in a.get("job_ids") or []]
    if not ids:
        return {"error": "job_ids is empty"}
    p = core.load_profile()
    dry = a.get("dry_run")
    dry = p["safety"]["dry_run_default"] if dry is None else bool(dry)
    if not dry:
        ok, why = core.real_actions_allowed(p)
        if not ok:
            return {"error": "live applications blocked: " + why, "hint": "run with dry_run=true to preview drafts"}
        if autopilot.stopped():
            return {"error": f"kill-switch active ({autopilot.STOP_FILE}); autopilot_stop(on=false) to clear"}
    opts = {"job_ids": ids, "dry_run": dry, "discover": False, "posts": False, "career": False, "careerops": False, "apply": False,
            "form": True, "email": True}
    env = {"PYTHONUNBUFFERED": "1", "DISPLAY": os.environ.get("DISPLAY", ":0"),
           "PLAYWRIGHT_BROWSERS_PATH": str(core.ROOT / ".browsers")}
    r = core.spawn("autopilot-selected", [core.PY, str(core.ROOT / "hub" / "autopilot.py"), json.dumps(opts)], core.ROOT, env, dry, opts)
    r["jobs"] = len(ids)
    r["note"] = ("DRY-RUN: forms are filled and emails drafted, nothing is submitted or sent."
                 if dry else f"LIVE: applying to {len(ids)} job(s) within your daily caps.")
    return r


def whatsapp_status(_):
    import whatsapp
    return whatsapp.status()


def whatsapp_link(a):
    import whatsapp
    return whatsapp.link(phone=str((a or {}).get("phone") or ""))


def whatsapp_unlink(_):
    import whatsapp
    return whatsapp.unlink()


def whatsapp_settings(a):
    import whatsapp
    try:
        return whatsapp.save_settings({k: a.get(k) for k in whatsapp.DEFAULTS if k in a}) if a else whatsapp.settings()
    except (ValueError, TypeError) as e:
        return {"error": str(e)}


def whatsapp_import_numbers(a):
    import whatsapp
    return whatsapp.import_numbers(a.get("text", ""), "text", a.get("default_country_code"), a.get("company", ""))


def whatsapp_contacts(a):
    import whatsapp
    rows = whatsapp.list_contacts(a.get("status", ""), a.get("limit", 100))
    return {"count": len(rows), "counts": whatsapp.counts(), "contacts": rows}


def whatsapp_send(a):
    import whatsapp
    p = core.load_profile()
    dry = a.get("dry_run")
    dry = True if dry is None else bool(dry)   # WhatsApp is always preview unless asked for explicitly
    limit = int(a.get("limit", 10))
    if dry:
        return whatsapp.preview(min(limit, 10))
    s = whatsapp.settings()
    ok, why = core.real_actions_allowed(p)
    if not ok:
        return {"error": "live sending blocked: " + why, "hint": "preview with dry_run=true"}
    if not s["recipients_expect_my_message"]:
        return {"error": "confirm first: set recipients_expect_my_message=true in whatsapp_settings"}
    r = core.spawn("task-whatsapp_send", [core.PY, str(core.ROOT / "hub" / "whatsapp.py"), "send", json.dumps({"limit": limit})],
                   core.ROOT, {"PYTHONUNBUFFERED": "1"}, False, {"limit": limit})
    r["note"] = f"LIVE: sending up to {min(limit, s['daily_cap'])} messages with pauses of {s['delay_sec'][0]}-{s['delay_sec'][1]} s."
    return r


def queue_status(_):
    return core.queue_state()


def alarm_list(_):
    st = scheduler.status()
    return {"alarms": st["alarms"], "tasks": scheduler.TASKS, "server_time": datetime.now().isoformat(timespec="minutes")}


def alarm_set(a):
    return scheduler.alarm_set(a)


def alarm_delete(a):
    return scheduler.alarm_delete(a)


def alarm_run_now(a):
    return scheduler.alarm_run_now(a)


def job_detail(a):
    jid = str(a["id"]).strip()
    m = re.fullmatch(r"(?:https?://[\w.]*linkedin\.com/jobs/view/[\w\-]*?)?(\d{6,14})/?", jid.split("?")[0])
    if not m:
        return {"error": "id must be a LinkedIn job id (digits) or a linkedin.com/jobs/view/... url"}
    return {"detail": _bun(["detail", m.group(1), "--format", "plain"])[:6000]}


def tailor_application(a):
    import shutil
    return {"workspace": str(AIJS), "job": a["job_url_or_id"],
            "profile_files": [str(AIJS / "CLAUDE.md"), str(AIJS / ".claude/skills/job-application-assistant/01-candidate-profile.md")],
            "workflow": str(AIJS / ".claude/commands/apply.md"),
            "master_cv": str(next(iter(sorted((AIJS / "cv").glob("*master*.tex"))), AIJS / "cv")),
            "master_cover_letter": str(next(iter(sorted((AIJS / "cover_letters").glob("*master*.tex"))), AIJS / "cover_letters")),
            "how": "Open the workspace in your agent (Kiro/Antigravity/Cline), read AGENTS.md, then follow apply.md with this job "
                   "as the argument. Drafts are LaTeX; PDF compile needs pdflatex/tectonic.",
            "latex_installed": bool(shutil.which("pdflatex") or shutil.which("tectonic"))}


def prepare_linkedin(_, limit=None):
    p = core.load_profile()
    a, c, s = p["applicant"], p["compensation"], p["search"]
    first, _, last = a["name"].partition(" ")
    loc = _location(p)
    edu = a.get("education") if isinstance(a.get("education"), dict) else {}
    yrs = float(re.findall(r"\d+(?:\.\d+)?", str(a.get("experience_years", "0")))[0]) if re.findall(r"\d", str(a.get("experience_years", "0"))) else 0
    levels = ["Internship", "Entry Level", "Associate"] if yrs < 2 else ["Associate", "Mid-Senior Level"] if yrs < 8 else ["Mid-Senior Level", "Director"]
    note = cover_note(p)
    cfg = f'''# generated by job-hub from profile.yaml - no passwords here; log in manually when the browser opens
PERSONAL = {{"first_name": {first!r}, "last_name": {last!r}, "email": {a["email"]!r}, "phone": {a["phone"]!r},
  "location": {loc["display"]!r}, "city": {loc["city"]!r}, "state": {loc["state"]!r}, "country": {loc["country"]!r}, "zip_code": "",
  "linkedin_url": {a["linkedin"]!r}, "resume_path": {a["resume_path"]!r}, "gender": "", "ethnicity": "", "age": "",
  "veteran": "No", "disability": "No"}}
LINKEDIN = {{"email": {a["email"]!r}, "password": ""}}
INDEED = {{"email": {a["email"]!r}, "password": ""}}
NAUKRI = {{"email": {a["email"]!r}, "password": ""}}
GLASSDOOR = {{"email": {a["email"]!r}, "password": ""}}
FOUNDIT = {{"email": {a["email"]!r}, "password": ""}}
SEARCH = {{"keywords": {s["keywords"][:6]!r}, "location": {(loc["country"] or loc["display"] or "Worldwide")!r}, "remote_only": False, "easy_apply_only": True,
  "max_applications": {limit or p["limits"]["max_applies_per_run_per_platform"]}, "experience_level": {levels!r},
  "date_posted": "past week"}}
COMPANY_BLOCKLIST = {p.get("autopilot", {}).get("company_blocklist", [])!r}
CONCURRENT_TABS = False

MAX_CONCURRENT_JOBS = 1
WORK_AUTH = {{"authorized_to_work": "Yes", "require_sponsorship": "No", "notice_period": {str(c["notice_period_days"])!r},
  "willing_to_relocate": {("Yes" if str(a.get("willing_to_relocate", "")).lower().startswith("y") else "No")!r}, "remote_work": "Yes", "us_timezone": "No"}}
EDUCATION = {{"highest_level": {edu.get("highest_level", "Bachelor's")!r}, "degree": {edu.get("degree", "")!r},
  "university": {edu.get("university", "")!r}, "graduation_year": {str(edu.get("graduation_year", ""))!r}, "gpa": {str(edu.get("gpa", ""))!r}}}
EXPERIENCE = {{"total_years": {a["experience_years"]!r}, "current_company": {(p.get("experience") or {}).get("current_company", "")!r}, "current_title": {(p.get("experience") or {}).get("current_title", "")!r},
  "current_salary": {str(c["current_ctc_inr"])!r}, "expected_salary": {str(c["expected_ctc_inr"])!r}, "currency": "INR"}}
SAVED_ANSWERS = {{"english": "Yes", "speak english": "Yes", "highest education": {edu.get("highest_level", "Bachelor's")!r}, "degree": {edu.get("highest_level", "Bachelor's")!r},
  "years of experience": {a["experience_years"]!r}, "notice period": {str(c["notice_period_days"])!r},
  "expected ctc": {str(c["expected_ctc_inr"])!r}, "expected salary": {str(c["expected_ctc_inr"])!r},
  "current ctc": {str(c["current_ctc_inr"])!r}, "relocate": {("Yes" if str(a.get("willing_to_relocate", "")).lower().startswith("y") else "No")!r}, "authorized": "Yes", "work authorization": "Yes",
  "sponsorship": "No", "visa": "No", "disability": "No", "veteran": "No",
  "cover letter": {(note + " - " + a["name"])!r}}}
'''
    d = core.TOOLS / "multi-platform-job-apply-bot"
    (d / "config.py").write_text(cfg)
    return {"config_written": str(d / "config.py"),
            "run_in_terminal": f"cd {d} && {core.PY} main.py   # choose 1=LinkedIn or 5=Foundit, then log in manually and press ENTER",
            "note": "Assisted mode: this bot has no dry-run and asks you to log in, so the hub does not launch it unattended. "
                    "Empty CTC/notice answers were written because profile.yaml has them unset - fill them first."}


CITY_MAP = {"pune": ("Maharashtra", "Pune"), "mumbai": ("Maharashtra", "Mumbai"), "hyderabad": ("Telangana", "Hyderabad"),
            "bengaluru": ("Karnataka", "Bengaluru"), "bangalore": ("Karnataka", "Bengaluru")}


def find_hr_emails(a):
    p = core.load_profile()
    h = p["hr_outreach"]
    key = a["city"].strip().lower()
    known = {str(x.get("city", "")).lower(): x for x in h.get("cities") or [] if isinstance(x, dict)}
    if key in known:
        state, city, country = known[key].get("state", ""), known[key]["city"], known[key].get("country", "India")
    elif key in CITY_MAP:
        (state, city), country = CITY_MAP[key], "India"
    else:
        return {"error": f"add {a['city']!r} to hr_outreach.cities in the profile ({{country, state, city}}) first"}
    key = re.sub(r"[^a-z0-9]+", "-", key)
    params = {"industry": a.get("industry") or h["industries"][0], "country": country, "state": state, "city": city,
              "sizes": h["company_sizes"], "roles": ["hr"], "limit": max(1, min(int(a.get("limit", 100)), 100)), "verify": True}
    r = core.spawn("hr-" + key, [core.PY, str(core.ROOT / "hub" / "hr_runner.py"), json.dumps(params)],
                   core.ROOT, {}, True, params)
    r["note"] = "Collects only publicly published HR inboxes and pattern-inferred person emails (confidence-labelled)."
    return r


def scan_hiring_posts(a):
    """Bulk scan of LinkedIn hiring posts. auto_apply=true also emails the published HR addresses and applies through
    the posts' company links (dry-run unless dry_run=false and the profile allows live actions)."""
    p = core.load_profile()
    env = {"PYTHONUNBUFFERED": "1", "PLAYWRIGHT_BROWSERS_PATH": str(core.ROOT / ".browsers")}
    if a.get("queries"):
        env["JOBHUB_POST_QUERIES"] = json.dumps(a["queries"])
    opts = {k: a[k] for k in ("max_posts", "scrolls", "max_queries", "auto_apply") if k in a}
    dry = a.get("dry_run")
    dry = p["safety"]["dry_run_default"] if dry is None else bool(dry)
    if a.get("auto_apply") and not dry:
        ok, why = core.real_actions_allowed(p)
        if not ok:
            return {"error": "live auto-apply blocked: " + why, "hint": "run with dry_run=true to preview"}
    opts["dry_run"] = dry
    env["JOBHUB_POST_OPTS"] = json.dumps(opts)
    r = core.spawn("posts", [core.PY, str(core.ROOT / "hub" / "post_scanner.py")], core.ROOT, env, dry,
                   {"queries": a.get("queries", "default"), **opts})
    r["note"] = ("Scanning hiring posts in bulk" + (" and applying to matches" + (" (DRY-RUN)" if dry else " (LIVE)")
                                                      if a.get("auto_apply") else " (nothing is sent)") + ".")
    return r


def career_scan(a):
    return core.spawn("career", [core.PY, str(core.ROOT / "hub" / "career_scan.py"), json.dumps(a)], core.ROOT,
                      {"PYTHONUNBUFFERED": "1"}, True, a)


def career_apply(a):
    p = core.load_profile()
    dry = a.get("dry_run", p["safety"]["dry_run_default"])
    params = {"dry": bool(dry), "job_ids": a.get("job_ids"), "limit": a.get("limit", 3)}
    env = {"PYTHONUNBUFFERED": "1", "PLAYWRIGHT_BROWSERS_PATH": str(core.ROOT / ".browsers")}
    r = core.spawn("career-apply", [core.PY, str(core.ROOT / "hub" / "career_apply.py"), json.dumps(params)], core.ROOT, env, bool(dry),
                   params)
    r["note"] = "DRY-RUN: forms are filled and screenshotted, nothing is submitted." if dry else "LIVE: submits application forms."
    return r


def hiring_post_queries(a):
    p = core.load_profile()
    cities = [a["city"]] if a.get("city") else [l for l in p["search"]["locations"] if str(l).lower() != "all"][:4]
    qs = []
    for c in cities or [""]:
        for role in (p["search"].get("keywords") or [])[:4]:
            qs.append(f'site:linkedin.com/posts hiring "{role}" {c} "email" OR "send your resume" OR "share your cv"'.replace("  ", " "))
    return {"queries": qs, "how": "Run each with a web search tool. Only store an email via add_hr_contact when the post itself "
            "invites applications to that address. Skip senior/10+ yrs roles. Max a few queries per session (low volume).",
            "target": ", ".join((p["search"].get("keywords") or [])[:4])}


def add_hr_contact(a):
    import re
    e = a["email"].strip().lower()
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", e):
        return {"error": "invalid email"}
    domain = e.split("@")[1]
    if domain in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com") and not a.get("post_asks_for_resume"):
        return {"error": "personal-mailbox address stored only when the post explicitly asks candidates to email it (post_asks_for_resume=true)"}
    conf = "high" if a.get("post_asks_for_resume") else "medium"
    with core.db() as c:
        cur = c.execute("INSERT OR IGNORE INTO hr_contacts(email,name,role,company,domain,city,confidence,note,kind,found_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)", (e, "", a.get("role", "Hiring post"), a.get("company", ""), domain,
                        a.get("city", ""), conf, "public hiring post: " + a["source_url"][:200], "post", core.now_iso()))
    return {"stored": bool(cur.rowcount), "email": e, "confidence": conf}


def list_hr_contacts(a):
    rank = core.CONF_RANK[a.get("min_confidence", "low")]
    with core.db() as c:
        rows = [dict(r) for r in c.execute("SELECT email,name,role,company,city,confidence,note,kind,status FROM hr_contacts "
                                           "WHERE (?='' OR status=?) ORDER BY id DESC LIMIT ?",
                                           (a.get("status", ""), a.get("status", ""), a.get("limit", 50)))]
    rows = [r for r in rows if core.CONF_RANK.get(r["confidence"], 0) >= rank]
    return {"count": len(rows), "contacts": rows}


def send_hr_emails(a):
    p = core.load_profile()
    dry = a.get("dry_run", p["safety"]["dry_run_default"])
    h = p["hr_outreach"]
    remaining = h["max_emails_per_day"] - core.sent_today()
    if not dry:
        ok, why = core.real_actions_allowed(p)
        if not ok:
            return {"error": "sending blocked: " + why, "hint": "use dry_run=true to preview drafts"}
        if not autopilot.in_send_window(autopilot.cfg(p)):
            return {"error": "outside send window: " + autopilot.send_window_text(autopilot.cfg(p))}
        if remaining <= 0:
            return {"error": "daily email cap reached"}
    n = min(a.get("limit", 5), max(remaining, 0) if not dry else a.get("limit", 5))
    contacts = core.eligible_contacts(p, n)
    if not contacts:
        return {"result": "no eligible contacts (run find_hr_emails first, or lower min_confidence in profile.yaml)"}
    out = []
    for i, ct in enumerate(contacts):
        role_title = ct.get("role") or (p["search"].get("keywords") or ["open position"])[0]
        mail = composer.compose(p, {"title": role_title, "company": ct.get("company", ""), "description": ""},
                                recruiter=ct.get("name", ""), cold=True)
        subject, body = mail["subject"], mail["body"]
        if dry:
            out.append({"to": ct["email"], "company": ct["company"], "confidence": ct["confidence"], "subject": subject,
                        "body_preview": body[:400]})
            continue
        try:
            core.send_via_gmail(p, ct["email"], subject, body)
            res = "sent"
        except Exception as e:  # noqa: BLE001
            res = "error: " + type(e).__name__ + ": " + str(e)[:120]
        with core.db() as c:
            c.execute("INSERT INTO emails_sent(to_email,domain,subject,sent_at,result) VALUES(?,?,?,?,?)",
                      (ct["email"], ct["domain"], subject, core.now_iso(), res))
            c.execute("UPDATE hr_contacts SET status=? WHERE email=?", ("emailed" if res == "sent" else "error", ct["email"]))
        out.append({"to": ct["email"], "company": ct["company"], "result": res})
        if res != "sent" and "password" in res.lower():
            break
        if i < len(contacts) - 1:
            lo, hi = h["send_delay_sec"]
            time.sleep(random.uniform(lo, hi))
    return {"dry_run": dry, "results": out}


def send_email(a):
    p = core.load_profile()
    dry = a.get("dry_run", p["safety"]["dry_run_default"])
    if dry:
        return {"dry_run": True, "from": p["hr_outreach"]["gmail_user"], "to": a["to"], "subject": a["subject"],
                "attach_resume": a.get("attach_resume", True)}
    ok, why = core.real_actions_allowed(p)
    if not ok:
        return {"error": "sending blocked: " + why}
    if not autopilot.in_send_window(autopilot.cfg(p)):
        return {"error": "outside send window: " + autopilot.send_window_text(autopilot.cfg(p))}
    if core.sent_today() >= p["hr_outreach"]["max_emails_per_day"]:
        return {"error": "daily email cap reached"}
    if contacts.is_suppressed(a["to"]):
        return {"error": f"recipient {a['to']} is suppressed (opt-out / bounce)"}
    dom = a["to"].split("@")[-1].lower()
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=p["hr_outreach"]["one_email_per_company_days"])).isoformat()
    with core.db() as c:
        if c.execute("SELECT 1 FROM emails_sent WHERE domain=? AND result='sent' AND sent_at>=?", (dom, cutoff)).fetchone():
            return {"error": f"domain {dom} already emailed within {p['hr_outreach']['one_email_per_company_days']} days"}
    core.send_via_gmail(p, a["to"], a["subject"], a["body"], a.get("attach_resume", True))
    with core.db() as c:
        c.execute("INSERT INTO emails_sent(to_email,domain,subject,sent_at,result) VALUES(?,?,?,?,?)",
                  (a["to"], dom, a["subject"], core.now_iso(), "sent"))
    return {"sent": True, "to": a["to"]}



def run_status(a):
    return core.run_status(a["run_id"], a.get("tail", 40))


def tracker_report(_):
    """Everything in one report: today's counters, 7-day trend, jobs by stage, contacts, recent emails and runs."""
    from datetime import timedelta
    core.sync_applications()
    days = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    with core.db() as c:
        hr = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM hr_contacts GROUP BY status")}
        sent = [dict(r) for r in c.execute("SELECT to_email,subject,sent_at,result FROM emails_sent ORDER BY id DESC LIMIT 10")]
        runs = [dict(r) for r in c.execute("SELECT id,kind,started,dry_run FROM runs ORDER BY started DESC LIMIT 10")]
        jobs = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
        by_src = {r["source"]: r["n"] for r in c.execute("SELECT source, COUNT(*) n FROM jobs GROUP BY source")}
        apps = {r["platform"]: r["n"] for r in c.execute("SELECT platform, COUNT(*) n FROM applications WHERE status='applied' GROUP BY platform")}
        mails_by_day = {r["d"]: r["n"] for r in c.execute(
            "SELECT substr(sent_at,1,10) d, COUNT(*) n FROM emails_sent WHERE result='sent' GROUP BY d")}
        apps_by_day = {r["d"]: r["n"] for r in c.execute(
            "SELECT substr(at,1,10) d, COUNT(*) n FROM applications WHERE status='applied' GROUP BY d")}
        total_sent = c.execute("SELECT COUNT(*) FROM emails_sent WHERE result='sent'").fetchone()[0]
        failed = c.execute("SELECT COUNT(*) FROM emails_sent WHERE result!='sent'").fetchone()[0]
        top = [dict(r) for r in c.execute(
            "SELECT id,title,company,score,status FROM jobs WHERE status IN ('scored','queued') ORDER BY score DESC LIMIT 5")]
    return {"applications_today": core.applied_today(), "emails_sent_today": core.sent_today(),
            "totals": {"emails_sent": total_sent, "emails_failed": failed, "applications": sum(apps.values()),
                       "jobs_found": sum(jobs.values()), "hr_contacts": sum(hr.values())},
            "applications_by_platform": apps, "jobs_by_status": jobs, "jobs_by_source": by_src,
            "last_7_days": [{"day": d, "emails": mails_by_day.get(d, 0), "applications": apps_by_day.get(d, 0)} for d in days],
            "hr_contacts_by_status": hr, "top_waiting_jobs": top, "recent_emails": sent, "recent_runs": runs,
            "queue": core.queue_state(), "brain_hint": "Write this summary to brain note projects/job-hub-progress (no secrets)."}


def autopilot_run(a):
    p = core.load_profile()
    dry = a.get("dry_run", p["safety"]["dry_run_default"])
    if not dry:
        ok, why = core.real_actions_allowed(p)
        if not ok:
            return {"error": "live autopilot blocked: " + why, "hint": "call with dry_run=true to preview drafts"}
        if autopilot.stopped():
            return {"error": f"kill-switch active ({autopilot.STOP_FILE}); autopilot_stop(on=false) to clear"}
    opts = {k: a[k] for k in ("platforms", "discover", "apply", "email", "max_discover") if k in a}
    opts["dry_run"] = dry
    env = {"PYTHONUNBUFFERED": "1", "DISPLAY": os.environ.get("DISPLAY", ":0"),
           "PLAYWRIGHT_BROWSERS_PATH": str(core.ROOT / ".browsers")}
    r = core.spawn("autopilot", [core.PY, str(core.ROOT / "hub" / "autopilot.py"), json.dumps(opts)], core.ROOT, env, dry, opts)
    r["note"] = ("DRY-RUN: discovers + scores for real, drafts emails, sends/applies nothing." if dry else
                 "LIVE: will email real contacts and run the apply bots within caps. Stop with autopilot_stop(on=true) or cancel_run.")
    return r


def list_jobs(a):
    q, args = "SELECT id,source,title,company,location,score,matched,status,reason,contact,url FROM jobs WHERE 1=1", []
    if a.get("status"):
        q += " AND status=?"
        args.append(a["status"])
    if a.get("min_score") is not None:
        q += " AND score>=?"
        args.append(a["min_score"])
    q += " ORDER BY score DESC, id DESC LIMIT ?"
    args.append(a.get("limit", 30))
    with core.db() as c:
        rows = [dict(r) for r in c.execute(q, args)]
        counts = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    for r in rows:
        r["matched"] = json.loads(r["matched"] or "[]")
    return {"counts": counts, "jobs": rows}


def _job(job_id):
    with core.db() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(r) if r else None


def find_contact(a):
    p = core.load_profile()
    mail_from = p["hr_outreach"]["gmail_user"]
    if a.get("job_id") is not None:
        j = _job(a["job_id"])
        if not j:
            return {"error": f"unknown job_id {a['job_id']}"}
        company, jd, site = j["company"], j["description"] or "", j["company_site"] or ""
    else:
        company, jd, site = a.get("company", ""), a.get("jd_text", ""), a.get("website", "")
    if not company and not jd:
        return {"error": "give company, jd_text or job_id"}
    return {"company": company, "candidates": contacts.find_contacts(company, jd, site, mail_from, probe=a.get("probe", True)),
            "note": "high = in the JD or published on the company site; medium = role inbox that passed an SMTP probe; "
                    "low = unconfirmed (never auto-sent)."}


def verify_email(a):
    p = core.load_profile()
    return {"email": a["email"], "result": contacts.verify_email(a["email"], p["hr_outreach"]["gmail_user"]),
            "suppressed": contacts.is_suppressed(a["email"]) if "@" in a["email"] else False}


def compose_email(a):
    p = core.load_profile()
    if a.get("job_id") is not None:
        j = _job(a["job_id"])
        if not j:
            return {"error": f"unknown job_id {a['job_id']}"}
        src, rec = {"linkedin": "LinkedIn", "naukri": "Naukri"}.get(j["source"], j["source"]), j.get("recruiter", "")
    else:
        j = {"title": a.get("title", ""), "company": a.get("company", ""), "description": a.get("jd_text", "")}
        src, rec = a.get("source", ""), ""
        if not (j["title"] or j["description"]):
            return {"error": "give job_id, or title + jd_text"}
    m = composer.compose(p, j, source=src, recruiter=rec, cold=a.get("cold", False))
    return {"subject": m["subject"], "body": m["body"], "matched_skills": m["matched"],
            "attachment": p["applicant"]["resume_path"], "note": "Not sent. The resume PDF is attached automatically when sent."}


def generate_linkedin_pitch(a):
    p = core.load_profile()
    if a.get("job_id") is not None:
        j = _job(a["job_id"])
        if not j:
            return {"error": f"unknown job_id {a['job_id']}"}
        role, co, jd, rec = j.get("title", ""), j.get("company", ""), j.get("description", ""), j.get("recruiter", "")
    else:
        role = a.get("title", "")
        co = a.get("company", "")
        jd = a.get("jd_text", "")
        rec = a.get("recruiter", "")
    pitch = composer.compose_linkedin_pitch(p, jd_text=jd, role=role, company=co, recruiter=rec)
    return {"pitch": pitch, "length": len(pitch), "max_allowed": 300, "candidate": p["applicant"]["name"]}


def cancel_run(a):

    return core.cancel_run(a["run_id"])


def autopilot_stop(a):
    if a["on"]:
        autopilot.STOP_FILE.write_text(core.now_iso())
    else:
        autopilot.STOP_FILE.unlink(missing_ok=True)
    return {"stopped": autopilot.stopped()}


def suppress_contact(a):
    contacts.suppress(a["email_or_domain"], a.get("reason", "manual"))
    return {"suppressed": a["email_or_domain"].lower()}


def guard_job_posting(a):
    import sys
    aijs_tools = AIJS / "tools"
    if str(aijs_tools) not in sys.path:
        sys.path.insert(0, str(aijs_tools))
    import posting_guard
    mode = a.get("mode", "text")
    clean, findings = posting_guard.guard(a["jd_text"], mode)
    return {"verdict": posting_guard.verdict(findings), "clean_text": clean, "findings": findings,
            "has_warnings": bool(findings.get("instruction_like") or findings.get("invisible_flagged"))}


def verify_resume_pdf(a):
    import sys
    from pathlib import Path
    aijs_tools = AIJS / "tools"
    if str(aijs_tools) not in sys.path:
        sys.path.insert(0, str(aijs_tools))
    import verify_pdf
    p = core.load_profile()
    r_path = Path(a.get("resume_path") or p["applicant"]["resume_path"])
    if not r_path.exists():
        return {"error": f"resume not found at {r_path}"}
    if a.get("required_keywords"):
        req_words = a["required_keywords"]
    else:  # words the resume itself contains (exact case): checks the PDF text layer without assuming a profession
        text = composer.resume_text(str(r_path))
        cands = [w for w in (p["applicant"].get("name") or "").split()] + list(p["search"].get("skills") or []) + list(composer.SKILLS)
        req_words = [w for w in dict.fromkeys(cands) if len(w) > 2 and w in text][:4]
    exp_pages = a.get("expected_pages", 1)
    try:
        text, pages, extractor = verify_pdf.extract_text_layer(r_path)
        verify_pdf.verify_pdf(r_path, expected_pages=exp_pages, required_text=req_words)
        return {"valid": True, "pages": pages, "chars": len(text), "extractor": extractor,
                "verified_keywords": req_words, "resume_path": str(r_path)}
    except Exception as e:
        return {"valid": False, "error": str(e), "resume_path": str(r_path)}


def find_company_emails(a):
    import company_finder
    return company_finder.find_company_emails(
        company=a.get("company", ""),
        domain=a.get("domain", ""),
        names=a.get("names"),
        roles=a.get("roles"),
        check_prefixes=a.get("check_prefixes", True),
        crawl_depth=a.get("crawl_depth", 8),
        verify=a.get("verify", True),
        limit=a.get("limit", 100),
    )


def mailscout_find_emails(a):
    import mailscout_tool
    return mailscout_tool.run_mailscout(
        domain=a["domain"],
        names=a.get("names"),
        check_prefixes=a.get("check_prefixes", True),
        check_variants=a.get("check_variants", True),
        check_catchall=a.get("check_catchall", True),
        normalize=a.get("normalize", True),
        smtp_timeout=a.get("smtp_timeout", 2),
        num_threads=a.get("num_threads", 5),
    )


def verdict_email_tool(a):
    import verdict_tool
    return verdict_tool.run_verdict(
        action=a.get("action", "discover"),
        domain=a.get("domain", ""),
        email=a.get("email", ""),
        person=a.get("person", ""),
        mail_from=a.get("mail_from", "probe@localhost"),
    )


def lead_finder_search(a):
    import lead_finder_tool
    return lead_finder_tool.run_lead_finder(
        domain=a.get("domain", ""),
        company=a.get("company", ""),
        industry=a.get("industry", ""),
        city=a.get("city", ""),
        country=a.get("country", ""),
        roles=a.get("roles"),
        company_sizes=a.get("company_sizes"),
        limit=a.get("limit", 5),
        verify=a.get("verify", True),
    )


def multi_board_search(a):
    import jobspy_tool
    return jobspy_tool.multi_board_search(a)


def resume_info(_):
    return resume.info(core.load_profile())


def resume_to_profile(a):
    """Read the uploaded resume and update the profile so job search follows it."""
    p = core.load_profile()
    sugg = resume.suggest_profile(p, use_llm=a.get("use_llm", True))
    if sugg.get("error"):
        return sugg
    if not a.get("apply", False):
        return {"suggestion": sugg, "applied": False,
                "note": "Preview only. Call again with apply=true (and replace_search=true to also replace search keywords)."}
    changed = resume.apply_suggestion(p, sugg, replace_search=a.get("replace_search", False))
    if changed:
        resume.write_profile(p)
    return {"applied": True, "changed": changed, "via": sugg.get("via"), "llm_error": sugg.get("llm_error")}


def sync_resume(a):
    """Push the uploaded resume to the Naukri profile (Naukri applies with the resume stored there)."""
    import shutil
    import tempfile
    p = core.load_profile()
    src = Path(p["applicant"]["resume_path"])
    if not src.exists():
        return {"error": "no resume uploaded yet"}
    if not _has_session("naukri"):
        return {"error": "Naukri is not linked - call login_platform('naukri') first"}
    dry = a.get("dry_run", p["safety"]["dry_run_default"])
    tmpd = Path(tempfile.mkdtemp(prefix="resume-", dir=str(core.ROOT / "data")))
    named = tmpd / resume.attachment_name(p)
    shutil.copyfile(src, named)  # Naukri shows the uploaded file name to recruiters
    r = core.spawn("naukri-resume", [core.PY, str(core.ROOT / "hub" / "naukri_resume.py"), str(named), "1" if dry else "0"],
                   core.ROOT, {"PYTHONUNBUFFERED": "1", "PLAYWRIGHT_BROWSERS_PATH": str(core.ROOT / ".browsers")}, dry,
                   {"platform": "naukri", "file": named.name})
    r["note"] = ("DRY-RUN: only checks the Naukri profile page." if dry else
                 f"Replacing the resume on your Naukri profile with {named.name}.") + \
        " LinkedIn Easy Apply and Indeed upload the hub resume on every application."
    return r


def unlink_platform(a):
    """Forget a saved login session (LinkedIn / Naukri / Indeed) on the server."""
    import shutil
    pl = a["platform"]
    if pl not in ("linkedin", "naukri", "indeed"):
        return {"error": "platform must be linkedin, naukri or indeed"}
    if core.running_conflict("apply-" + pl):
        return {"error": f"a {pl} run is active; cancel it first"}
    d = core.ROOT / ".profiles" / pl
    shutil.rmtree(d, ignore_errors=True)
    return {"unlinked": pl, "session_saved": _has_session(pl)}


def scheduler_status(_):
    return scheduler.status()


HANDLERS = {k: globals()[k] for k in TOOLS}


# ---------------------------------------------------------------- JSON-RPC over stdio
_out_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=6)  # a slow tool (e.g. 75s search_jobs) must not block ping/tools/list


def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg["error" if error else "result"] = error or result
    with _out_lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def call_tool(id_, name, args):
    try:
        res = HANDLERS[name](args)
        reply(id_, {"content": [{"type": "text", "text": json.dumps(res, indent=2, default=str)}],
                    "isError": "error" in res if isinstance(res, dict) else False})
    except KeyError as e:  # missing required argument
        reply(id_, {"content": [{"type": "text", "text": f"missing argument: {e}"}], "isError": True})
    except Exception as e:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        reply(id_, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True})


def handle(req):
    m, id_ = req.get("method"), req.get("id")
    if m == "initialize":
        pv = (req.get("params") or {}).get("protocolVersion", "2024-11-05")
        return reply(id_, {"protocolVersion": pv, "capabilities": {"tools": {}}, "serverInfo": SERVER})
    if m == "ping":
        return reply(id_, {})
    if m == "tools/list":
        return reply(id_, {"tools": tool_list()})
    if m == "tools/call":
        prm = req.get("params") or {}
        name, args = prm.get("name"), prm.get("arguments") or {}
        if name not in HANDLERS:
            return reply(id_, error={"code": -32602, "message": f"unknown tool {name}"})
        bad = [r for r in REQUIRED.get(name, []) if r not in args]
        if bad:
            return reply(id_, {"content": [{"type": "text", "text": f"missing required argument(s): {', '.join(bad)}"}], "isError": True})
        _pool.submit(call_tool, id_, name, args)
        return None
    if id_ is not None:
        reply(id_, error={"code": -32601, "message": f"method not found: {m}"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except json.JSONDecodeError:
            reply(None, error={"code": -32700, "message": "parse error"})
    _pool.shutdown(wait=True)


if __name__ == "__main__":
    main()
