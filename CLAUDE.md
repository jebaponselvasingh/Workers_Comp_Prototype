# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **single self-contained HTML file** (`Workers_Comp_Prototype.html`, ~1900 lines / ~560KB) that renders an interactive Workers' Compensation claims console for the US manufacturing sector. Everything — markup, CSS, ~100 synthetic claim records, all application logic — lives inline in that one file. There is **no build system, no package.json, no dependencies to install, and no tests**. The only external resource is a Google Fonts stylesheet loaded via CDN.

### Running / previewing
Open the file directly in a browser — `open Workers_Comp_Prototype.html` (macOS). No server or build step required. There is no lint or test command; changes are verified by reloading the page.

## Architecture

The whole app is three inline blocks in one file: a `<style>` block (design tokens as CSS variables in `:root`, then terse utility classes), the HTML skeleton of all screens/modals, and one large `<script>` (starts ~line 628).

### Data model
`ALL_CLAIMS` (line ~629) is a hardcoded JSON array of ~100 synthetic claim objects, derived from a dataset described in-app as `WC_Manufacturing_Claims_2026.xlsx` (10 employers such as Boeing, Caterpillar, GE, Toyota; 15 plants). Each claim carries dozens of fields: identity, injury/ICD-10, severity score, financials (reserve/paid/incurred), timeline dates, fraud indicators, and `cp*` fields (`cpNextActions`, `cpSimilarCase`, `cpReserveNote`, etc.) that pre-author the AI copilot's canned answers. After the array, a `forEach` pass derives prioritization flags (`siuReview`, `rtwBlocked`, `paymentDue`, `nextPaymentDate`).

### Auth & personas
There is no real auth. `pickRole()` → `doLogin()` sets globals `currentUser` / `currentRole` from a `name|role` dropdown value. `HANDLER_MAP` (line ~851) maps each named persona to the subset of `claimId`s they can see (`getMine()` filters `ALL_CLAIMS` by this). Three roles:
- **handler** — sees the handler view (`hvView`): claim queue + detail pane + copilot.
- **supervisor** / **analyst** — see the dashboard view (`svView`): KPI cards + donut charts across their caseload.

### Rendering
Plain vanilla JS with **no framework**. State lives in module-level globals (`selectedId`, `activeTab`, `cpTab`, `chatHistory`, `diaryNotes`, `meetingsStore`, `emailsStore`). UI is (re)built by string-template functions that assign `innerHTML`:
- `renderSV()` — supervisor/analyst dashboard.
- `renderQ()` / `buildQCard()` — handler's claim queue (with `priorityScore()` ordering).
- `renderDet()` — selected-claim detail pane; dispatches to per-tab/per-stage builders (`ovHTML`, `intakeOverviewHTML`, `treatmentOverviewHTML`, `settledOverviewHTML`, `billsHTML`, `injHTML`, `insightsHTML`, `docsHTML`, …).
- `renderCP()` / `renderChat()` — the copilot panel and its quick-action buttons (`QAS`).
Many detail fields are **inline-editable**: `editText`/`editSelect`/`updateClaimField` mutate the in-memory claim object and re-render (e.g. `updateSevScore`, `addInjury`, `computeBenefit`, `recalcSLA`).

### AI copilot (important)
`sendAI()` (line ~1586) `fetch`es `https://api.anthropic.com/v1/messages` directly from the browser — but **sends no API key**, so the call always fails and the code falls back to `offlineAnswer()` (line ~1533), a deterministic `switch` that returns pre-written, claim-specific text from the `cp*` fields. In practice the app is **always offline/demo mode**; treat `offlineAnswer` as the real copilot. (Calling a real model would require adding auth and CORS handling.)

### Persistence
**None.** All mutations (claim edits, chat history, diary notes, meetings, emails) are in-memory JS objects. Logout (`logoutBtn`) simply calls `location.reload()`, which discards everything. There is no `localStorage`/`sessionStorage`/backend.

## Conventions

- **Extreme abbreviation** is the house style, deliberate throughout. CSS classes and IDs are terse (`.kpi`, `.qa`, `.cp`, `.svs`, `#uav`, `#rbdg`); helper functions end in `HTML` when they return template strings (`ovHTML`, `injHTML`). Match this when adding code.
- CSS design tokens are two-letter variables in `:root` (`--ac` accent, `--tx` text, `--ok`/`--wn`/`--er` status colors). Reuse them rather than hardcoding hex.
- Editing means editing one file. There are no modules to split across; keep new state as globals near the existing ones (line ~873) and new render logic alongside its siblings.
