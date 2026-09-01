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

## 2. Repository layout

- Git remote `origin`: `https://github.com/robertkrivic/UX_AGENT.git`
- Work happens on **`feat/ux-methodology`**.

The tree is split into two independently deployable halves:

```
backend/    Flask API + Playwright pipeline  ->  container (Cloud Run)
frontend/   Vue 3 + Vite dashboard           ->  any static host
```

Top-level docs describe the whole project rather than either half, so they stay
at the root. **Every command below is prefixed with the half it runs in** —
there is no `pyproject.toml` at the repo root, so a bare `uv sync` fails.

---

## 3. First-time setup (new machine)

```bash
git clone https://github.com/robertkrivic/UX_AGENT.git
cd UX_AGENT
git checkout feat/ux-methodology

# Python env (uv). On OneDrive/cloud-synced folders UV_LINK_MODE=copy is
# mandatory — hardlinks fail there.
cd backend
UV_LINK_MODE=copy uv sync
UV_LINK_MODE=copy uv run playwright install chromium

# Secrets: backend/.env — NOT the repo root. gpt.py resolves it relative to the
# process cwd, which must be backend/, so a root .env is silently never loaded
# and every LLM call 500s.
cp .env.example .env      # then fill it in

# The provider in use is gemini via VERTEX AI (conf/base.yaml -> llm_provider),
# so the keys that matter are GOOGLE_APPLICATION_CREDENTIALS (a path to a
# service-account JSON file, which must be copied too), VERTEXAI_PROJECT and
# VERTEXAI_LOCATION. OPENAI_API_KEY and friends are dormant config.

# Frontend deps
cd ../frontend && npm install
```

---

## 4. Running

Two terminals:

```bash
# 1) backend (Flask, :8000)
cd backend && PYTHONIOENCODING=utf-8 uv run python -m simulated_web_agent.main.app

# 2) frontend (Vite, :5173)
cd frontend && npm run dev
```

`PYTHONIOENCODING=utf-8` is not optional on Windows — the report prose is Polish.

Open http://localhost:5173. The wizard launches a run; the live view streams the
agent; Results/Report/History read past runs. `backend/runs/` keeps three real
archived runs so those screens work without waiting for a fresh audit.

CLI (no UI): `cd backend && uv run python -m simulated_web_agent.main.run`, which
reads `conf/runConfig.yaml`. Note this is `main.run`, not `main` — the
`main/__main__.py` CLI takes `--persona/--intent/--start-url` flags and never
reads runConfig.yaml.

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

## 6. Where things stand

The dashboard, live view, AI persona generation, i18n, the audit/finding
pipeline and the grounded task-completion outcome are all shipped and on
`feat/ux-methodology`. `METHODOLOGY_PLAN.md` tracks which numbered sections of
the methodology have landed.

Deployed shape:
- backend → Google Cloud Run (container built from `backend/Dockerfile`)
- frontend → static host; `public/config.js` carries the backend URL and is
  edited on the server, so a backend move needs no rebuild.

## 7. Known issues

- **Cancel then re-run** used to hang the next run: the shared Playwright driver
  was left bound to a destroyed event loop. Fixed, but it is the failure mode to
  suspect first if a run ever starts and never steps.
- **`runs/` is ephemeral in the container.** Anything written there is lost on
  restart or redeploy. Mount a bucket before treating a cloud-produced report as
  durable.
- Cloud Run needs `--memory 4Gi --cpu 2 --no-cpu-throttling --min-instances 1
  --max-instances 1 --timeout 3600`. These are not tuning: run state is an
  in-memory registry and the audit runs in a background thread after the
  response returns. The Dockerfile's deployment-notes block has the full
  reasoning.

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
| Flask app + endpoints | `backend/src/simulated_web_agent/main/app.py` |
| Pipeline orchestration | `backend/src/simulated_web_agent/main/run.py` |
| Results/live/history reads | `backend/src/simulated_web_agent/main/results_api.py` |
| Trace I/O schema | `backend/src/simulated_web_agent/main/trace_store.py` |
| Per-agent analyses | `backend/src/simulated_web_agent/main/analysis_runner.py` |
| Aggregated report | `backend/src/simulated_web_agent/main/reporter.py` |
| Persona generation | `backend/src/simulated_web_agent/main/persona.py` |
| Agent cognition | `backend/src/simulated_web_agent/agent/agent.py` |
| Browser loop | `backend/src/simulated_web_agent/main/experiment.py` |
| Frontend shell | `frontend/src/App.vue` |
| Frontend screens | `frontend/src/screens/` |
| i18n | `frontend/src/i18n.ts` |
| Run config (CLI) | `backend/conf/runConfig.yaml`, `backend/conf/base.yaml` |
