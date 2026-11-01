# System Architecture and End-to-End Data Flow

**Project:** Agentic Job Seeker  
**Last Updated:** 2026-11-01  

---

## 1. System Overview

Agentic Job Seeker is an autonomous, privacy-focused agent designed to assist job seekers through intelligent job discovery, automated career portal application submissions, cold email outreach, and inbound recruiter reply handling.

### High-Level Topology

```
+-----------------------------------------------------------------------------------+
|                                  USER CLIENTS                                     |
|  - Flutter Mobile / Desktop App                                                  |
|  - Telegram Bot & Notifications (Human-in-the-Loop)                              |
|  - Remote Browser (noVNC via Caddy)                                              |
+-----------------------------------------------------------------------------------+
                                        | (HTTPS / WSS)
                                        v
+-----------------------------------------------------------------------------------+
|                             CADDY REVERSE PROXY                                   |
|  - Automatic Let's Encrypt TLS                                                   |
|  - Basic Auth on /browser/ (noVNC / websockify)                                  |
|  - Token Auth proxying to FastAPI Backend (:8000)                                |
+-----------------------------------------------------------------------------------+
                                        |
                                        v
+-----------------------------------------------------------------------------------+
|                           BACKEND SERVICE (FastAPI)                               |
|  - api.py: REST API & MCP (Model Context Protocol) endpoints                      |
|  - core.py: Task Queue, Concurrency Manager (MAX_PARALLEL), Profile Store        |
|  - keys.py: Secret Manager (Kiro, Groq, Ollama, Telegram, Gmail App Password)     |
|  - attention.py: Human-in-the-Loop Coordinator (Captchas, 2FA, Login walls)      |
|  - resume.py / composer.py: Resume Parsing and Fact Extraction                    |
|  - career_apply.py: Automated Form Filling (Greenhouse, Lever, Ashby, Workday)   |
|  - email_scanner.py: Recruiter Inbound Scanning & AI Reply Drafting               |
|  - scheduler.py: Periodic Job Discovery and Automation Engine                     |
+-----------------------------------------------------------------------------------+
              |                           |                           |
              v                           v                           v
+---------------------------+ +-----------------------+ +---------------------------+
|    BROWSER AUTOMATION     | |       AI ENGINES      | |     EXTERNAL CHANNELS     |
| - Moli Headless CDP       | | - Self-hosted Ollama  | | - Telegram Bot API        |
| - Playwright Chromium     | | - Groq / OpenAI Cloud | | - Gmail IMAP & SMTP       |
| - Virtual Display (Xvfb)  | |   (Fallback/Analysis) | | - WhatsApp (Evolution-Go) |
+---------------------------+ +-----------------------+ +---------------------------+
```

---

## 2. End-to-End Data Flow

### 2.1 Job Discovery & Matching
1. **Source Ingestion:**
   - Jobs are discovered via `ai-job-search` (LinkedIn guest search), `python-jobspy` (Indeed/Glassdoor), or company career scans.
2. **Title & Skill Filtering:**
   - `search.title_include` and `search.title_exclude` regex rules filter irrelevant roles.
   - Candidate resume (`resume.pdf`) is parsed using `pymupdf` / `pdftotext`, extracting primary skills and years of experience.
3. **Scoring:**
   - Each job description is scored against candidate profile facts and resume skills (0-100 score).

### 2.2 Career Portal Application Automation (`career_apply.py`)
1. **Target Identification:**
   - Detects if the job URL points directly to an ATS (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) or company career page.
2. **Candidate Facts Aggregation:**
   - Extracts structured facts from `profile.yaml`: Contact info, Compensation (Current/Expected CTC, Notice Period), Experience, Education, Work Authorization, Relocation.
   - Extracts missing attributes from parsed resume.
3. **Form Interaction & Safety:**
   - Form fields are detected via DOM tree inspection.
   - Required standard fields are populated deterministically.
   - Questions requiring text reasoning are submitted to the configured LLM (Ollama or Groq/OpenAI) with strict candidate fact boundaries.
4. **Human-in-the-Loop Interruption:**
   - If a CAPTCHA (reCAPTCHA, hCaptcha, Turnstile) or mandatory login wall is encountered, the automation **does not attempt to bypass it**.
   - Instead, `attention.py` creates a pending challenge record, generates an authenticated single-use remote browser URL, and sends a high-priority alert to the user's Telegram.
   - The user opens the remote browser, completes the human challenge, and the automation automatically resumes and verifies the submission.
5. **Confirmation & Verification:**
   - Detects post-submit confirmation message and registers the application in SQLite (`applications` table).

### 2.3 Recruiter Email Response Intelligence (`email_scanner.py`)
1. **Inbox Polling:**
   - Connects to candidate's Gmail via secure IMAP using stored app password.
   - Filters incoming threads matching previously contacted recruiters or job submissions.
2. **Classification:**
   - Classifies email intent: `interview_invite`, `info_request` (requesting CTC, notice period, availability), `rejection`, or `generic`.
3. **Draft Generation:**
   - For interview requests or information requests, the LLM drafts an appropriate, professional reply pre-filled with candidate facts (e.g. current notice period: 30 days, expected CTC, open slots).
4. **User Notification & Control:**
   - The user receives a Telegram alert with the recruiter summary.
   - The drafted response can be reviewed and confirmed via the mobile app or API before sending.
