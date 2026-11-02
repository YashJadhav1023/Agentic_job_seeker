# Implementation Plan: Profile Fix, Resume Sync, Scan Hiring Posts & UI Redesign

**Date:** 2026-11-02  
**Feature:** Candidate Profile Save Fix, AI Resume Sync, Post Scan Multi-Tag Inputs, Modern Theme Redesign & External Tools Integration  
**Status:** In Progress  

---

## 1. Problem Statement & Root Cause

### 1.1 Candidate Profile Save Type Cast Error
- **Error:** `Save failed: type 'String' is not a subtype of type 'Map<dynamic, dynamic>?' in type cast`
- **Root Cause:** In `profile_screen.dart`, methods `_load()` and `_buildProfileJson()` perform unsafe type casts such as:
  ```dart
  final loc = Map<String, dynamic>.from((app['location'] as Map?) ?? {});
  final edu = Map<String, dynamic>.from((app['education'] as Map?) ?? {});
  ```
  If `app['location']` or `app['education']` in `profile.yaml` is stored as a `String` (e.g. `location: "Pune, India"` or `education: "B.Tech Computer Science"`), Dart throws an uncaught `TypeError` at runtime.
- **Fix:** Introduce defensive helper functions `_safeMap(dynamic val)` and `_safeList(dynamic val)`. If `location` or `education` is a string, safely convert it into the expected map structure without throwing.

### 1.2 Resume Auto-Fill & Review
- Instead of requiring manual document upload picker, provide a direct **"Sync from Resume"** action in the Profile header.
- This invokes `resume_to_profile` with `apply: true` and `use_llm: true`, extracts candidate skills, experience years, headline, contact information, and target titles.
- Once synced, the screen reloads all form controllers, shows a status banner, and allows the user to manually review, edit, or fill any empty fields before saving.

### 1.3 Post Scan: Keywords & Locations Separation with Multi-Value Support
- Currently, `scan_hiring_posts` only has a single free-form `queries` field.
- **Requirement:**
  1. Separate into **Keywords** and **Locations** inputs.
  2. Support **multiple entries** for both (e.g. tag/chip interface where users can add multiple roles like `DevOps`, `SRE`, `Cloud Engineer`, and multiple cities like `Pune`, `Bangalore`, `Remote`).
  3. Update backend `hub/server.py` to accept `keywords` and `locations` arrays, automatically generating the `keywords × locations` query matrix for LinkedIn post scanning.

### 1.4 Application UI & Color Theme Redesign
- The existing dark palette has odd murky navy/gray contrast and harsh borders.
- Redesign using **Material 3 Expressive** and **WCAG 2.2** standards (`mobile-uiux-pro-max`):
  - Background: Obsidian Slate (`#090D16`)
  - Surface: Rich Slate (`#0F172A`)
  - Elevated Cards: Charcoal Slate (`#1E293B`)
  - Primary: Electric Indigo (`#6366F1`)
  - Secondary/Accent: Vibrant Cyan (`#06B6D4`)
  - High-contrast typography: Pure White (`#FFFFFF`) / Cool Silver (`#94A3B8`)
  - Polished form input decorations, rounded corners (12dp), and subtle borders (`#334155`).

### 1.5 Research & Integration of External Skills and Platforms
- **Mailflare (`hieunc229/mailflare`)**: Self-hosted email suite with AI agents and MCP. Plan integration with `yash.j.devops@gmail.com` for incoming HR email analysis and outbound applications.
- **Hirerizz (`hirerizz.com`)**: Resume fit and matching engine (Reel 1 analysis).
- **Wellfound (`wellfound.com`) + Apollo.io**: Direct startup discovery (11-50 employees) and outreach playbook (Reel 2 analysis).
- **LinkedIn Skills (`Jakeschincariol/linkedin-agent-skill` & `sergebulaev/linkedin-skills`)**: Humanizer and 21 hook formulas for custom cover letters and anti-detection.
- **Resume Tailoring Skill (`varunr89/resume-tailoring-skill`)**: Multi-job batching, conversational branching questions, and factual integrity.

---

## 2. Proposed Changes & Task Breakdown

### Task 1: Fix Profile Screen Cast Errors & Add Resume Sync
- **Files Modified:**
  - `mobile/lib/ui/screens/profile_screen.dart`
  - `mobile/lib/api/client.dart`
- **Actions:**
  1. Add `_safeMap` and `_safeList` helpers.
  2. Replace all unsafe `as Map?` casts.
  3. Add `syncResume()` method calling `requireClientFrom(ref).call('resume_to_profile', {'apply': true, 'replace_search': true, 'use_llm': true})`.
  4. Add a "Sync from Resume" action button in the profile header.
  5. Reload profile after sync and display detailed feedback.

### Task 2: Update Backend for Scan Hiring Posts Keywords & Locations
- **Files Modified:**
  - `backend/hub/server.py`
- **Actions:**
  1. Update `TOOLS["scan_hiring_posts"]` schema:
     - `keywords`: array of strings (e.g. `['devops', 'sre']`)
     - `locations`: array of strings (e.g. `['pune', 'bangalore', 'remote']`)
     - `queries`: optional manual override array
  2. In `def scan_hiring_posts(a)`:
     - If `keywords` or `locations` are provided, generate queries using the Cartesian product `keywords × locations` and pass to `JOBHUB_POST_QUERIES`.

### Task 3: Enhance Schema Form with Tag / Chip Multi-Input
- **Files Modified:**
  - `mobile/lib/ui/widgets/schema_form.dart`
- **Actions:**
  1. For string array inputs (`type == 'array'` with free strings), provide a modern chip/tag input:
     - Chips displayed for each item with delete icon.
     - Text field with "+" button, Enter/comma submission to add tags easily.
     - Preserves clean typing of multiple keywords and locations.

### Task 4: Modernize App Theme (Colors & UI Polish)
- **Files Modified:**
  - `mobile/lib/ui/theme.dart`
- **Actions:**
  1. Refactor color tokens to Deep Obsidian & Electric Indigo / Cyan.
  2. Update `InputDecorationTheme`, `CardThemeData`, `ElevatedButtonThemeData`, `ChipThemeData`.
  3. Ensure WCAG 2.2 AA contrast compliance.

### Task 5: External Tools & Strategy Architecture
- **Deliverable:**
  - Create architecture analysis document for Mailflare, Wellfound, Hirerizz, LinkedIn Agent Skills, and Resume Tailoring Skill.

---

## 3. Verification Plan
- Run `flutter analyze` on the mobile project.
- Run `pytest` on the backend suite (`test_always_on_generic.py`, `test_global_alarms.py`).
- Verify manual profile save and JSON generation logic.

