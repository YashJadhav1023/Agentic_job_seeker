---
inclusion: always
---

# Coding guidelines (adapted from multica-ai/andrej-karpathy-skills, CLAUDE.md)

These bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think before coding
- State assumptions explicitly. If uncertain, ask.
- If several interpretations exist, present them; do not pick silently.
- If a simpler approach exists, say so and push back when warranted.
- If something is unclear, stop, name what is confusing, and ask.

## 2. Simplicity first
- Minimum code that solves the problem. Nothing speculative.
- No features, abstractions, flexibility or configurability that was not requested.
- No error handling for impossible scenarios.
- If 200 lines could be 50, rewrite it.

## 3. Surgical changes
- Touch only what you must; clean up only your own mess.
- Do not "improve" adjacent code, comments or formatting; match existing style.
- Mention unrelated dead code instead of deleting it.
- Remove imports/variables that YOUR change made unused. Every changed line must trace to the request.

## 4. Goal-driven execution
- Turn tasks into verifiable goals: bug fix = failing test first, then pass; refactor = tests green before and after.
- For multi-step work, state a short plan with a verify step for each.

## Project rules that follow from this
- `main` deploys automatically to the live AWS server. Work on a feature branch and merge only when `pytest` (backend) and `flutter analyze` + `flutter test` (mobile) pass.
- Never put keys in code or in the brain notes. Secrets live in AWS Parameter Store under `/agentic-job-seeker/secret/` and reach the container as environment variables.
- Daily volume caps are the owner's decision (profile `limits` / `hr_outreach`). Never bypass a site's bot protection, captcha or rate limit; the cooldown logic in `hub/autopilot.py` is deliberate.
