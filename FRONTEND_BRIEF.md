# UXAgent — Frontend Designer Brief

> **Historical — superseded.** This is the original design brief, kept for the
> reasoning behind the dashboard's shape. It no longer describes the code:
> it predates the Vertex/gemini move (it says OpenAI / AWS Bedrock), lists 2 of
> the 13 endpoints, states that the frontend only ever talks to its own origin
> (no longer true — see `frontend/src/api.ts` and `public/config.js`), and its
> "What's Missing" section describes screens that have since shipped. For the
> current picture read `CLAUDE.md` and `HANDOFF.md`.


## What the App Does

UXAgent is an **AI-powered UX research platform**. Instead of recruiting real human participants, it generates synthetic personas and sends AI agents (powered by an LLM) to browse a website autonomously. Each agent behaves like a real user — it perceives the page, forms a plan, takes actions, and reflects on what it experienced. After all sessions complete, the platform produces a full UX audit report: heuristic violations, accessibility issues, responsiveness problems, scroll behaviour, form engagement, and a post-session questionnaire.

**Primary users:** UX researchers, product teams, agencies running UX audits for clients.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Vue 3, TypeScript, Vite, Naive UI component library |
| Backend | Python / Flask, port 8000 |
| AI agents | LLM (OpenAI / AWS Bedrock) + Playwright browser automation |
| Communication | REST — `POST /run`, `GET /progress` |

The Vue dev server proxies `/run` and `/progress` to Flask on port 8000, so the frontend only ever talks to its own origin.

---

## Current Frontend — 3-Step Wizard

The existing UI is a **linear 3-step wizard** using Naive UI's `<n-steps>` component. Navigation state lives in a URL query param (`?step=1/2/3`). All form data is shared across steps via Vue `provide/inject` with a single `WizardForm` object, persisted to `localStorage`.

### Step 1 — Participant Recruitment
- URL of the website being tested
- Number of participants (synthetic personas to generate)
- Participant task — a plain-text instruction the agent tries to complete (e.g. "Find a mobile plan under 70 zł/month")
- Demographics — a list of named dimensions (e.g. "Age", "City", "Device type"), each with weighted choices. The LLM samples from these weights to generate diverse personas.
- Example persona — a freeform text block used as a style/format template for generated personas

### Step 2 — Survey Builder
- Visual question builder (no JSON editing needed)
- Supports: open-ended (short text) and multiple-choice questions
- One-click SUS button inserts the full 10-item System Usability Scale
- Serialises to a structured JSON object sent to the backend

### Step 3 — Review & Submit
- Summary view of all configuration
- Demographics shown as probability bars (weighted distribution)
- Questionnaire listed question by question
- "Confirm and Run" button fires `POST /run` and triggers the top progress bar

---

## What's Missing — Step 4: Results

**This is the most important missing screen.** A results/progress view does not exist yet. It needs to:

- Poll `GET /progress` every few seconds while the run is active
- Show which phase is running: `Generating personas → Running agents → Analysis → Report`
- Show counts: e.g. "Agent 2 of 3 complete"
- When finished: display the full results

### Results screen sections

| Section | Data source | Notes |
|---------|-------------|-------|
| Per-persona cards | `persona_card.json` | Name, objective, demographics |
| Objective reflection | `objective_reflection.md` | Narrative: did the persona succeed? Blockers, wins, priority fix |
| Heuristic findings | `heuristic_findings.json` | Severity (critical / serious / minor), Nielsen heuristic number, evidence, recommendation |
| First impression scores | `first_impression.json` | 1–5 scores across 5 dimensions (purpose clarity, navigation, CTA, visual hierarchy, trustworthiness) |
| Accessibility score | `accessibility_findings.json` | Overall 1–5 score + individual findings |
| Viewport / responsiveness | `viewport_findings.json` | Mobile / tablet horizontal overflow, small fonts |
| Survey results | `survey_result.json` | Per-question answers with reasoning |
| Aggregated report | `aggregated_report.html` | Pre-rendered HTML report — opens in new tab |

---

## API Reference

### `POST /run` — start a new analysis session

Accepts `application/json`. Returns `200 OK` when the **full run completes** — this is a long-running synchronous call (20–90+ minutes). Do not wait on it in the UI; submit and switch to polling `/progress`.

```json
{
  "total_personas": 3,
  "demographics": [
    {
      "name": "Age group",
      "choices": [
        { "name": "18–25", "weight": 2 },
        { "name": "55+",   "weight": 1 }
      ]
    }
  ],
  "general_intent": "Find and compare mobile plans",
  "start_url": "https://www.t-mobile.pl/",
  "max_steps": 25,
  "concurrency": 1,
  "headless": true,
  "questionnaire": {
    "questionnaire_id": "my_survey_v1",
    "title": "UX Survey",
    "questions": [
      {
        "id": "q1",
        "type": "multiple_choice",
        "prompt": "Was navigation easy? (1 = No, 5 = Yes)",
        "options": ["1", "2", "3", "4", "5"]
      },
      {
        "id": "q2",
        "type": "short_text",
        "prompt": "What frustrated you most?"
      }
    ]
  }
}
```

Required fields: `total_personas`, `demographics`, `general_intent`, `start_url`, `max_steps`, `questionnaire`.  
Optional: `concurrency` (default 4), `headless` (default true), `example_persona` (string).

---

### `GET /progress` — poll run status

Returns the current phase and progress counts.

```json
{
  "phase": "agents",
  "label": "Running agents",
  "current": 1,
  "total": 3,
  "message": "Running agents: 1/3"
}
```

**Phases in order:**

| Phase | Description |
|-------|-------------|
| `personas` | LLM generating synthetic user personas |
| `agents` | Playwright browser sessions running (one per persona) |
| `analysis` | Per-agent audit: heuristics, a11y, viewport, links, scroll, forms |
| `report` | Aggregated report generation |

When idle (no run started): `{ "status": "idle", "message": "No run started yet." }`

---

## Run Duration & UX Expectations

- A typical run with 3 personas at 25 steps takes **20–60 minutes**
- The user submits and walks away — the app must handle long async waits gracefully
- Recommended polling interval: every 3–5 seconds
- The top progress bar (`topProgressBar.vue`) already exists and should remain visible throughout the run

---

## Output File Structure

Every run creates a folder on the server:

```
runs/
└── <site-slug>/                    ← derived from URL (e.g. t-mobile.pl → tmobile)
    ├── aggregated_report.html      ← main deliverable (site-level)
    ├── aggregated_report.json
    ├── aggregated_report.md
    ├── pagespeed.json              ← Google PageSpeed Insights scores
    └── <YYYY-MM-DD_HH-MM-SS_xxxx>/  ← one folder per persona run
        ├── persona_card.json       ← name, objective, demographics
        ├── basic_info.json         ← raw persona + intent text
        ├── heuristic_findings.json ← Nielsen violations
        ├── first_impression.json   ← 5-second test scores
        ├── accessibility_findings.json
        ├── viewport_findings.json
        ├── scroll_summary.json
        ├── form_funnel.json
        ├── link_findings.json
        ├── survey_result.json
        ├── url_trace.json          ← step-by-step navigation log
        ├── ux_report.json          ← structured per-run report
        ├── ux_report.md
        ├── objective_reflection.md ← LLM narrative vs persona goal
        └── screenshot/             ← screenshot_N.png per step
```

---

## Agent Cognitive Loop (background context)

Each AI agent runs a fixed loop per step:

1. **Perceive** — reads a simplified version of the page's HTML
2. **Reflect** — extracts insights from recent memory
3. **Plan** — decides the next action based on persona goal
4. **Act** — clicks, types, or scrolls in the real browser

The agent runs for `max_steps` iterations, then the analysis phase begins automatically.

---

## Severity Classification

Heuristic findings use three severity levels:

| Severity | Polish label | Meaning |
|----------|-------------|---------|
| Critical | `krytyczny` | Blocks the user from completing their goal |
| Serious | `poważny` | Significantly impairs usability |
| Minor | `drobny` | Small friction or improvement opportunity |

---

## Nielsen's 10 Heuristics Reference

Each heuristic finding is tagged with a heuristic ID (1–10):

| ID | Name |
|----|------|
| 1 | Visibility of system status |
| 2 | Match between system and the real world |
| 3 | User control and freedom |
| 4 | Consistency and standards |
| 5 | Error prevention |
| 6 | Recognition rather than recall |
| 7 | Flexibility and efficiency of use |
| 8 | Aesthetic and minimalist design |
| 9 | Help users recognise, diagnose, and recover from errors |
| 10 | Help and documentation |

---

## Key Design Considerations

- **Long wait states** are the core UX challenge — the run takes tens of minutes; the UI must feel alive during this time
- **Results are rich** — each persona produces ~15 JSON files; the UI should surface the most important findings first, with progressive disclosure for details
- **The aggregated report** is the final deliverable — it should be easy to download or open from the results screen
- **Multiple past runs** may exist for the same site — consider whether the UI should show run history
- **The wizard form** is persisted to `localStorage` — users can return and continue where they left off
