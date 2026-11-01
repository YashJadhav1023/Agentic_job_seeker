# Open-Source Software & Dependency Audit

**Project:** Agentic Job Seeker  
**Date:** 2026-11-01  
**Auditor:** Senior AI & DevOps Engineering Team

---

## 1. Executive Summary

Agentic Job Seeker integrates an intelligent backend (FastAPI, SQLite, Playwright/Moli) with a modern cross-platform Flutter frontend and containerized micro-services (Ollama, Caddy, Evolution-Go).

This audit evaluates all external open-source packages, vendored repositories, and container images to identify licensing compatibility, security posture, operational risks, and recommendations for retention or replacement.

---

## 2. Component Inventory

| Component / Library | Origin / Repository | Purpose | License | Retention Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **Moli** | `lexmount/moli` | Lightweight headless browser with Chrome DevTools Protocol (~40MB RAM vs Chromium ~400MB) | Apache-2.0 / MIT | **Retain & Prioritize**. Excellent resource savings on small cloud VMs. |
| **Playwright** | `microsoft/playwright` | Browser automation framework for career site forms & interactive logins | Apache-2.0 | **Retain**. Core automation engine with reliable selector engine. |
| **python-jobspy** | `cullenwatson/JobSpy` | Scrapes LinkedIn, Indeed, Glassdoor without logins/credentials | MIT | **Retain**. Safe, guest-mode discovery fallback. |
| **mailscout** | `mailscout` (PyPI) | Fast MX and SMTP email deliverability & contact verification | MIT | **Retain**. Lightweight local verification before outreach. |
| **ai-job-search** | `MadsLorentzen/ai-job-search` | LinkedIn public search CLI running via Bun (no auth required) | MIT | **Retain**. Pinned in `tools.lock`. |
| **job-automation** | `Nawaz-B-04/job-automation` | Platform-specific form filling automation (patched with hubguard) | MIT | **Retain with Isolation**. Pinned in `tools.lock`. |
| **multi-platform-job-apply-bot** | `RayeesYousufGenAi` | Secondary scraper/apply bot | MIT | **Retain with Isolation**. Pinned in `tools.lock`. |
| **lead-finder** | `wasaybuilds/lead-finder` | HR contact & lead discovery utility | MIT | **Retain with Isolation**. Pinned in `tools.lock`. |
| **Ollama** | `ollama/ollama` | Self-hosted local LLM engine (Qwen, DeepSeek, Llama) | MIT | **Retain & Primary LLM option**. Zero external API cost. |
| **Evolution-Go** | `evoapicloud/evolution-go` | Headless WhatsApp client gateway | Apache-2.0 / GPL | **Retain**. Containerized and isolated on compose network. |
| **Caddy** | `caddyserver/caddy` | Automatic HTTPS reverse proxy and VNC websocket gateway | Apache-2.0 | **Retain**. High security, automated Let's Encrypt certificates. |
| **FastAPI / Uvicorn** | `tiangolo/fastapi` | High-performance Python async backend API and MCP host | MIT | **Retain**. Core framework. |
| **Flutter / Riverpod** | `flutter/flutter` | Reactive state management and cross-platform UI | BSD-3-Clause | **Retain**. Core UI framework. |

---

## 3. Security, License & Isolation Analysis

1. **License Compatibility:** All core components use permissive licenses (MIT, Apache-2.0, BSD-3-Clause). No copyleft viral GPL licenses affect our proprietary backend or mobile application code.
2. **Credential Isolation:**
   - Vendored bots in `THIRD_PARTY_KINDS` are strictly filtered in `launcher.py` / `core.py`: sensitive environment variables (`JOBHUB_API_TOKEN`, secrets, API keys) are stripped before spawning subprocesses.
3. **Resource Protections:**
   - Playwright and Chromium processes are constrained using `shm_size: 1gb` and process-level timeouts (bounded execution) to prevent memory exhaustion on cloud VMs.
4. **Network Hardening:**
   - Caddy acts as the sole public gateway. Internal services (FastAPI on 8000, noVNC on 6080, Ollama on 11434, Evolution on 4000) are unexposed to the public internet.

---

## 4. Recommendations & Upgrades

1. **Dynamic LLM Engine:** Support dual-layer AI routing (Ollama self-hosted local model as primary, Groq / OpenAI-compatible endpoint as fast fallback). Avoid locking the system into a single provider.
2. **Career Site Form Intelligence:** Rather than adopting heavyweight proprietary RPA tools, expand `career_apply.py` with structured field normalization (Candidate Facts dictionary + ATS heuristics) for Greenhouse, Lever, Ashby, and Workday.
3. **Email Intelligence:** Use Python's built-in `imaplib` / `email` modules combined with local/hosted LLM classification rather than adding heavy external mail services.
