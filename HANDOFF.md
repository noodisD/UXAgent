# UXAgent — Project Handoff

This document is everything a new owner (or another Claude Code profile) needs to
pick up, run, and continue this project. Pair it with `CLAUDE.md` (agent-facing
project memory) and `FRONTEND_BRIEF.md` (the original design brief).

---

## 1. What it is

UXAgent runs **synthetic UX research**: it generates AI personas and sends
LLM-driven Playwright agents to autonomously use a target website, then produces a
full UX audit (Nielsen heuristics, first impression, accessibility, viewport,
scroll/forms, survey) and an aggregated narrative report. The web app is a Vue
dashboard; the engine is a Python/Flask + Playwright pipeline. Built for a Polish
team auditing Polish e-commerce/telecom sites; UI is Polish-first with EN toggle.

---

## 2. Repository & branch story (read this)

- Git remote: `https://github.com/noodisD/UX-AI-Agent.git`
- **`master`** — earlier line of work (audit engine + reporting). Does **not**
  contain the Vue frontend.
- **`post-pepco`** — branched from commit `502d8fc` (28 May 2026), the baseline that
  still has the `experiment-ui/` Vue frontend. **All current dashboard work lives
  here.** This is the branch to continue on.

This working copy is a **git worktree** of `post-pepco`. On a new machine you don't
need worktrees — just clone and `git checkout post-pepco`.

> If pushing/cloning: ensure the post-pepco branch with this session's work has been
> committed and pushed (see §7). Otherwise the new profile will get the old baseline.

---

## 3. First-time setup (new machine)

```bash
git clone https://github.com/noodisD/UX-AI-Agent.git
cd UX-AI-Agent
git checkout post-pepco

# Python env (uv). On OneDrive/cloud folders prefix with UV_LINK_MODE=copy.
uv sync
uv run playwright install chromium

# Secrets — create .env at repo root:
#   OPENAI_API_KEY=...
#   ANTHROPIC_API_KEY=...        (optional)
#   GEMINI_API_KEY=...           (optional, cheapest)
#   GOOGLE_PAGESPEED_API_KEY=... (optional, enables PageSpeed)
#   HEADLESS=true
# Provider chosen in conf/base.yaml -> llm_provider.

# Frontend deps
cd experiment-ui && npm install
```

---

## 4. Running

Two terminals from the repo root:

```bash
# 1) backend (Flask, :8000)
PYTHONIOENCODING=utf-8 uv run python -m simulated_web_agent.main.app

# 2) frontend (Vite, :5173)
cd experiment-ui && npm run dev
```

Open http://localhost:5173. The wizard launches a run; the live view streams the
agent; Results/Report/History read past runs. A seeded demo run exists under
`runs/2026-05-15_16-00-00_seed/` so the Results/Report/History screens work without
waiting for a real run.

CLI (no UI) is also possible: `uv run python -m simulated_web_agent.main` using
`conf/runConfig.yaml`.

---

## 5. Architecture (short)

- **Async run model**: `POST /run` spawns a background thread, returns `{run_id}`
  immediately; an in-memory `RUNS` registry + `CURRENT_RUN_ID` track progress.
- **Pipeline** (`main/run.py`): personas → browser sessions (`experiment.py`) →
  per-agent analyses (`analysis_runner.py`) → pagespeed → aggregated report
  (`reporter.py`). Output under `runs/<run_id>/<persona>/` + `run_manifest.json`.
- **TraceStore** (`main/trace_store.py`) is the single authority for the trace-dir
  file schema. **results_api.py** maps the trace to API shapes.
- **Agent cognition** (`agent/agent.py`): perceive → reflect → plan → act per step.
- See `CLAUDE.md` for the full endpoint list and file map.

---

## 6. What was built this session (on top of post-pepco)

Backend:
- Ported `persona_card.json` + `objective_reflection` (structured JSON: summary,
  blockers, wins, priorityFix, outcome) from master into post-pepco.
- Run grouping: persona dirs nested under a `runs/<run_id>/` group + `run_manifest.json`.
- Async run model + `RUNS` registry in `app.py`.
- New read endpoints: `/runs`, `/runs/<id>`, `/runs/<id>/report.html`,
  `/runs/<id>/live`, `/runs/<id>/screenshot/<persona>`.
- `/runs/<id>` now also returns the full aggregated narrative (`report`).
- `POST /generate-example-persona` — LLM persona generator (`persona.py`).
- `results_api.py` — new module for results/live/history reads.

Frontend (`experiment-ui/src/`):
- Full dashboard rebuilt to the handoff design: `App.vue` shell + `screens/`
  (Wizard, Running, Results, ReportView, History) + composables.
- **Live agent view**: real-time screenshot, current URL/step, observation, and
  reasoning stream; auto-recovers `run_id` from `/progress` on nav/reload.
- **Interactive in-app Report** tab (renders the aggregated narrative, not just the
  static HTML) with section nav, collapsible severity groups, recommendations.
- **AI "Generate example persona"** button in the wizard.
- **Polish/English i18n** (`i18n.ts`, default Polish) with sidebar switcher; theme
  labels (severity/outcome/heuristics) are locale-aware.

---

## 7. Transfer checklist

- [ ] Commit this session's work on `post-pepco` (large, currently uncommitted —
      new `results_api.py`, `experiment-ui/src/screens/`, composables, `i18n.ts`,
      `theme.ts`, plus edits across `app.py`, `run.py`, `persona.py`, etc.).
- [ ] Push `post-pepco` to the remote.
- [ ] Recreate `.env` on the target (secrets are **not** committed — `.gitignore`d).
- [ ] On target: `git checkout post-pepco`, `uv sync`, `playwright install chromium`,
      `cd experiment-ui && npm install`.
- [ ] `runs/` outputs are local artifacts; the seed run can be copied for demos but
      isn't required.

---

## 8. Known issues / next steps

- Elapsed timer on the Running screen restarts from 0 after navigation/reload
  (backend doesn't report elapsed). Optional: persist run start time.
- After a full reload the app lands on the wizard; the active-run sidebar card
  returns you to the live view. Optional: auto-restore the running screen.
- Run registry is in-memory (single process); a server restart forgets active runs
  (finished runs are still readable from disk). A DB/persistent registry would
  harden multi-run history and concurrency.
- LLM output language is Polish (prompt-driven). For English-domain audits, the
  run prompts would need a language switch threaded through `run_async`.
- `/runs/<id>/live` polls every 1.5s (snapshot). True token streaming would need
  SSE from the agent loop.

---

## 9. Key files index

| Area | File |
|------|------|
| Flask app + endpoints | `src/simulated_web_agent/main/app.py` |
| Pipeline orchestration | `src/simulated_web_agent/main/run.py` |
| Results/live/history reads | `src/simulated_web_agent/main/results_api.py` |
| Trace I/O schema | `src/simulated_web_agent/main/trace_store.py` |
| Per-agent analyses | `src/simulated_web_agent/main/analysis_runner.py` |
| Aggregated report | `src/simulated_web_agent/main/reporter.py` |
| Persona generation | `src/simulated_web_agent/main/persona.py` |
| Agent cognition | `src/simulated_web_agent/agent/agent.py` |
| Browser loop | `src/simulated_web_agent/main/experiment.py` |
| Frontend shell | `experiment-ui/src/App.vue` |
| Frontend screens | `experiment-ui/src/screens/` |
| i18n | `experiment-ui/src/i18n.ts` |
| Run config (CLI) | `conf/runConfig.yaml`, `conf/base.yaml` |
