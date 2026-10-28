# career-ops (vendored subset)

Source: https://github.com/career-ops-hq/career-ops (commit a6d7526, 2026-10-28), MIT licence (see `LICENSE`),
Copyright (c) 2026 Santiago Fernández de Valderrama.

Included, unmodified: the public ATS provider modules (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee,
Personio, Teamtailor, Breezy, BambooHR, JazzHR) and the helper modules they import (`providers/_*.mjs`, `lib/`,
`url-key.mjs`, `user-agent.mjs`). They read each company's own public job-board API, need no login and no AI tokens.

Not part of upstream: `run_scan.mjs` (a small runner written for Agentic Job Seeker).

The "career-ops" name and logo are covered by upstream's trademark policy (TRADEMARK.md); this project only credits it.
Upstream never submits applications; submitting here is done by Agentic Job Seeker's own guarded apply step.
