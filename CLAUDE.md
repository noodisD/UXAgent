# CLAUDE.md — UXAgent project memory

Guidance for Claude Code when working in this repository. Read this first.

## What this project is

UXAgent is an **AI-powered UX research platform**. Instead of recruiting human
participants, it generates synthetic personas and sends LLM-driven agents
(Playwright browser automation) to autonomously use a website. Each agent
perceives the page, plans, acts, and reflects — then the platform produces a UX
audit: Nielsen heuristic findings, first-impression scores, accessibility,
viewport/responsiveness, scroll/form behaviour, a post-session survey, and an
aggregated narrative report. Built for a Polish team auditing Polish domains;
the UI is Polish-first with an EN toggle.

## Stack

- **Backend**: Python 3.12, Flask (`backend/src/simulated_web_agent/main/app.py`),
  async pipeline via `asyncio`, Playwright, LiteLLM (OpenAI / Anthropic / Gemini / AWS).
- **Frontend**: Vue 3 + TypeScript + Vite, in `frontend/`. Hand-written SFCs +
  `src/style.scss` — there is no component library (package.json declared
  `naive-ui` and `@vicons/*` for a long time with zero imports).
- **Packaging**: `uv` (`backend/pyproject.toml` + `backend/uv.lock`); editable
  install of `simulated_web_agent`.

## Repo layout

Split into two independently deployable halves (backend → GCP container,
frontend → static host):
```
backend/    Flask API + Playwright pipeline — see backend/README.md
frontend/   Vue/Vite dashboard (formerly experiment-ui/)
```
Top-level docs (this file, README.md, HANDOFF.md, METHODOLOGY_PLAN.md, LICENSE,
NOTICE) stay at the project root — they describe the whole project, not one half.

## Run commands

Backend (port 8000) — terminal 1:
```
cd backend && PYTHONIOENCODING=utf-8 uv run python -m simulated_web_agent.main.app
```
Frontend (port 5173) — terminal 2:
```
cd frontend && npm run dev
```
Open http://localhost:5173. Vite proxies `/run`, `/progress`,
`/generate-example-persona`, `/generate-audit-settings` and `/runs/*` to the
backend on 8000.

Type-check frontend: `cd frontend && npm run typecheck`.
**Not** `npx vue-tsc --noEmit`: `tsconfig.json` is a solution file (`"files": []`
plus project references), so the bare form resolves zero input files and exits 0.
It reported success for months while the build was broken. The npm script points
at `tsconfig.app.json`, which is what `vue-tsc -b` (and therefore `npm run build`)
actually checks.

Backend tests: `cd backend && uv run pytest` (in `backend/tests/`).

## Environment gotchas (important)

- **`.env` required** at `backend/`. Loaded via python-dotenv in `agent/gpt.py`;
  without it every LLM call 500s. `llm_provider: "gemini"` in `conf/base.yaml` is
  the only provider in use, and it routes through **Vertex AI**, not AI Studio —
  so the keys that actually matter are:
  - `GOOGLE_APPLICATION_CREDENTIALS` — **path to a service-account JSON file**.
    This is a file on disk, not a secret string: copying `.env` to another
    machine is not enough, the JSON has to come too or every call fails to auth.
  - `VERTEXAI_PROJECT`, `VERTEXAI_LOCATION`
  - `GOOGLE_PAGESPEED_API_KEY` for the pagespeed pass.

  `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / AWS keys are only
  read if you switch `llm_provider`; nothing uses them today.
- **`UXAGENT_API_KEY`** — the shared password the dashboard asks for, compared
  against the `X-API-Key` header. **Unset means every endpoint is open**, which
  is what keeps local dev and the test suite working — and what makes a public
  deployment without it a way for strangers to spend your LLM budget. `app.py`
  logs a loud warning at boot when it is unset.
- **`CORS_ORIGINS`** — comma-separated origin allowlist, needed once the
  dashboard is served from a different host than the API. Empty = same-origin.
- **`PORT`** — honoured by the Dockerfile's gunicorn CMD. `app.py`'s `__main__`
  block hardcodes 8000 and ignores it, so that path is dev-only.
- **OneDrive / cloud-synced folder**: `uv sync` / `uv add` must be prefixed with
  `UV_LINK_MODE=copy` (hardlinks fail on OneDrive). Plain `uv run` is fine.
- **Windows console encoding**: prefix runs with `PYTHONIOENCODING=utf-8` (Polish chars).
- **Playwright browser**: `UV_LINK_MODE=copy uv run playwright install chromium` once.

## Architecture — request flow

POST `/run` starts the pipeline in a **background thread** and returns
`{run_id, site_slug}` immediately (async model). An in-memory `RUNS` registry in
`app.py` tracks status/progress; `CURRENT_RUN_ID` is the active run.

Pipeline (`main/run.py` `run_async`): generate personas → `experiment_async`
(browser sessions, one per persona) → per-agent `run_all_analyses` → pagespeed →
`generate_aggregated_report`. Outputs to `runs/<run_id>/<persona_dir>/...` plus a
run-level `run_manifest.json` and `aggregated_report.{json,md,html}` (plus `.pdf`, rendered
lazily on first download).

### Endpoints (`main/app.py`) — all 14
- `GET /health` → unauthenticated liveness probe (the container platform's).
- `POST /run` → starts run, returns `{run_id, site_slug}` (202).
- `GET /progress[?run_id=]` → `{run_id, status, phase, current, total, label, message}`
  or `{status:"idle"}`. Phases: personas → agents → analysis → report.
  `status` is one of running | done | error | cancelled.
- `POST /runs/<run_id>/cancel` → cooperative cancel; sessions stop after the
  current step and queued personas never start.
- `POST /generate-example-persona` → LLM-generates one persona for the wizard.
- `POST /generate-audit-settings` → LLM-suggests wizard settings for a URL.
- `GET /runs` → run-history rows.
- `GET /runs/<run_id>` → full results: `{meta, personas[], report, comparison}`.
- `GET /runs/<run_id>/live` → per-persona live state `{step, url, action,
  observation, stream[]}` read incrementally from the trace during a run.
- `GET /runs/<run_id>/screenshot/<persona_id>` → latest screenshot PNG (no-cache).
- `GET /runs/<run_id>/screenshot/<persona_id>/<step>` → that step's screenshot.
- `GET /runs/<run_id>/<persona_id>/screenshot/<filename>` → a named screenshot.
- `GET /runs/<run_id>/report.html` → static aggregated report (to read in a tab).
- `GET /runs/<run_id>/report.pdf` → the same report as a PDF, sent as an
  attachment. Rendered from the HTML by Chromium (`main/report_pdf.py`) on
  first request and cached beside it, re-rendered when the HTML is newer — so
  the two containers cannot drift, and runs nobody downloads cost nothing.

### Auth model (`app.py`, `@before_request`)
`UXAGENT_API_KEY` unset → open. Set → every route needs `X-API-Key`, compared
with `hmac.compare_digest`. Two deliberate exemptions:
- `OPTIONS` (a CORS preflight carries no custom header) and `/health`.
- The browser-loaded routes — the three `/runs/.../screenshot/...` variants and
  `/runs/<id>/report.{html,pdf}` — also accept the key as `?key=`, because a
  browser cannot attach a header to an `<img src>`, a `target="_blank"`
  navigation, or a download. `frontend/src/api.ts` is the only place that knows this:
  `apiFetch` sends the header, `apiAssetUrl` appends the query parameter.

`main/results_api.py` owns reading the on-disk trace and mapping it to API
shapes. `main/trace_store.py` is the single I/O authority for the trace dir
schema (every `read_*`/`write_*`). Never construct trace paths outside TraceStore.

### Agent loop
`agent/agent.py`: perceive → reflect → plan → act, per step. Orchestrated by
`AgentPolicy.forward()` in `main/model.py`, driven by the browser loop in
`main/experiment.py` (`while steps_taken < max_steps`). Prompts in
`agent/shop_prompts/` and `audit_prompts/`.

### Per-run trace artifacts (under `runs/<run_id>/<persona_dir>/`)
`persona_card.json`, `basic_info.json`, `heuristic_findings.json`,
`first_impression.json`, `accessibility_findings.json`, `viewport_findings.json`,
`scroll_summary.json`, `form_funnel.json`, `link_findings.json`,
`survey_result.json`, `objective_reflection.json` (structured: summary/blockers/
wins/priorityFix/outcome), `iso_metrics.json`, `bias_audit.json`,
`cognitive_walkthrough.json`, `session_result.json`, `ux_report.{json,md}`,
`action_trace.json`, `observation_trace/`, `memory_trace.json`, `screenshot/`.

## Frontend structure (`frontend/src/`)

- `App.vue` — shell (sidebar, header, screen router, language switch, active-run card).
- `screens/WizardScreen.vue` — 3-step new-audit wizard (recruitment, survey, review)
  with the "✨ Generate with AI" example-persona button.
- `screens/RunningScreen.vue` — **live** view: phase tracker + per-persona live screen
  (screenshot), current URL/step, observation, and reasoning stream. Recovers the
  active `run_id` from `/progress` so navigation/reload reconnect automatically.
- `screens/ResultsScreen.vue` — tabs: Report / Overview / Personas / Findings / Survey.
- `screens/ReportView.vue` — interactive aggregated report (in-app, not just the HTML).
- `screens/HistoryScreen.vue` — past runs.
- `composables/` — `useApp` (nav state), `useRun` (start/persist run_id), `useProgress`,
  `useLive`, `useResults`, `useHistory`, `useForm`.
- `i18n.ts` — reactive `locale` (default `pl`) + `t()` + pl/en dictionaries.
- `theme.ts` — colours, the normalisers `outcomeOf`/`sevOf` (every raw backend
  string goes through one before it is used as a map key) and the locale-aware
  `sevLabel`/`outcomeLabel`/`heuristicName`.
- `api.ts` — **the only seam to the backend.** Owns the absolute origin
  (`window.UXAGENT_API_BASE` from `public/config.js`, else `VITE_API_BASE`, else
  same-origin), the `X-API-Key` header, the `?key=` variant for browser-loaded
  assets, and central 401 handling. Nothing else should call `fetch` directly.
- `components/AuthGate.vue` — the password gate shown when a key is required.
- `public/config.js` — backend URL, editable on the static host without a
  rebuild. Deliberately empty in git.

## Conventions

- Work happens on `feat/ux-methodology`. See `HANDOFF.md` for orientation.
- TDD where practical (`tests/`). `npm run typecheck` must pass before declaring
  frontend done — and it must be that script, not a bare `vue-tsc --noEmit`,
  for the reason given under Run commands.
- Dynamic content (persona narratives, findings, report prose) is produced in Polish
  by the LLM; only UI chrome is translated via `i18n.ts`.
- Add a new analysis pass via the recipe at the top of `main/analysis_runner.py`.

### Prompt examples must not pre-fill a judgement field

Never put a concrete value in a prompt's JSON example for a field the model is
meant to *decide* — a score, rating, confidence or severity. It copies it, and
the field silently stops measuring anything. This has bitten three times:

| prompt | example said | what shipped |
|---|---|---|
| `first_impression` | `[4,3,2,4,4]` → `3.4` | 71% of 38 scans returned exactly that — including a telecom site |
| `heuristic_evaluate` | `"confidence": "high"` | 56 of 59 findings "high"; `"low"` never used once |
| `accessibility_summarize` | `"overall_score": 3` | 62% of 35 scans returned exactly 3 |

Use `<int 1-5, ...>` placeholders instead, and give the scale per-level anchors
(a competent page is a 3; a 5 must be earned). De-anchoring alone is not enough
— without calibration the model drifts from a floor to a ceiling of generic
praise. `tests/test_prompt_anchors.py` guards this; sample data ("11px", a URL)
and *varied* rubric examples are fine — `memory_importance` shows 9/3/1 and its
output stays well spread.

Related: a judgement the trace can answer should not be asked of the LLM at all.
`confidence` is capped by `main/confidence.py` against `verified` (evidence.py)
and `harness_suspect` (attribution.py) — the model may lower it, never raise it.

### LLM tiers: what `model=` actually selects

`llm_provider: "gemini"` is the only provider in use (`conf/base.yaml`). `gpt.py`
is a legacy filename, not OpenAI; the openai/aws/anthropic router entries are
dormant config. Under gemini the `model=` argument to `async_chat` resolves to:

| `model=` | actual model | used by |
|---|---|---|
| `"lite"` | `gemini-3.5-flash-lite` | `agent.py` perceive (high-volume) |
| `"small"` | `gemini-3.8-flash` | most passes |
| `"large"` | `gemini-3.8-flash` | **identical to `small` — buys nothing** |
| `"report"` | `gemini-3.1-pro-preview` | audit passes and `reporter.py` aggregate synthesis |

All four are called on Vertex's **`global` endpoint**, set per model in
`gpt.py` (`vertex_location`), not through `VERTEXAI_LOCATION`: the 3.x models
answer 404 in `us-central1`. Pro has no GA 3.x yet — `gemini-3.1-pro` is a 404,
only `-preview` exists. The 2.5 family these replaced shuts down 2026-10-16.
Memory embeddings still use `text-embedding-004`, which still answers on Vertex.

`small` and `large` pick different *routers*, but both routers map `gemini` to
the same model, so the distinction is a no-op here — it only means something for
providers we don't use. Do not "upgrade a pass to the large tier" and expect a
better answer; I did, and claimed it fixed anchoring when the prompt rewrite had
done all the work. **`"report"` is the only real quality lever.**

**The audit phase runs on `"report"` (the Pro tier)**: first impression,
heuristic evaluation, accessibility, cognitive walkthrough, bias audit. These
are judgement calls, and they cost one call per persona — unlike the agent loop,
which pays per step. Pro is ~2.3x slower per call; that is affordable here and
nowhere else.

**Thinking is on everywhere, including where the config says it is off.** The
flash entries set `"thinking": {"type": "disabled"}`, which is Anthropic syntax
that litellm silently drops for Vertex — measured: 935 of 972 completion tokens
were reasoning on a one-sentence prompt. What actually works on Vertex Gemini is
`reasoning_effort="disable"` or `thinkingConfig={"thinkingBudget": 0}` (both
verified to zero it out). So every flash call in the agent loop is paying for
reasoning tokens that nobody asked for. Do not "enable thinking" via
`enable_thinking=True` — there is no `gemini_thinking` route, so it raises;
that is why `agent.py:316` has it commented out.

### Decide in code, narrate with the LLM

"Does this image have alt text?" is decidable, so `main/a11y_checks.py` decides
it and the accessibility prompt only narrates and scores the candidates. Pasting
raw evidence in and hoping the model finds the issue is how the pass ended up
with a 1.66M-char (~416k token) prompt that failed near the context limit.

The same split runs live-DOM checks in `main/viewport_scan.py` (tap targets,
contrast, drag) — they need computed style and layout, so they cannot come off
the aria tree. If you add a check there, encode the standard's exceptions too:
skipping the SC 2.5.8 spacing exception turned 4 real tap-target failures on
pepco.pl into 27 reported ones. And keep normative separate from advisory —
24×24 is WCAG, 48×48 is guidance; merging them asserts a failure that isn't one.

When a check aggregates, the numbers must survive the trip into prose:
- collapse repeats (a carousel is one defect ×40, not 40 defects);
- cite the step the number is *true of* — citing first-sighting while reporting
  the worst page's count made the narrator write "1436 images on step 0" when
  step 0 had 40. Two true numbers, one fabricated claim;
- pass step→URL explicitly. `store.visited_urls()` de-duplicates and drops the
  mapping, so the model guessed the URL — and `resolve_all` only repairs the
  `url` field, never the prose.
