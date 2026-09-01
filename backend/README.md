# UXAgent backend

Python/Flask API + Playwright agent pipeline. Pairs with `../frontend/`
(Vue/Vite dashboard) — see `../README.md` for the project overview and
`../HANDOFF.md` / `../METHODOLOGY_PLAN.md` for architecture and methodology.

## Setup

```bash
uv sync
uv run playwright install chromium
```

On a OneDrive-synced checkout, prefix `uv sync`/`uv add` with
`UV_LINK_MODE=copy` (hardlinks fail on OneDrive).

Create the environment file **in this directory** — `backend/.env`, not the
repository root. `agent/gpt.py` calls `load_dotenv()`, which resolves relative
to the process working directory, and that must be `backend/`; a root `.env` is
silently never loaded and every LLM call then 500s.

```bash
cp .env.example .env      # then fill it in
```

`.env.example` documents every variable. See the "Environment gotchas" section
of `../CLAUDE.md` for which ones actually matter under the active
`llm_provider`, and for `UXAGENT_API_KEY` / `CORS_ORIGINS`, which decide whether
a deployment is reachable and whether it is open to the internet.

## Run

```bash
PYTHONIOENCODING=utf-8 uv run python -m simulated_web_agent.main.app
```

Serves the API on port 8000. The frontend's dev server proxies `/run`,
`/progress`, `/generate-example-persona`, `/generate-audit-settings` and
`/runs/*` to this port.

## Test

```bash
uv run pytest -q
```

## Layout

- `src/simulated_web_agent/` — the package (`agent/` perceive-plan-act loop,
  `main/` Flask app + audit pipeline + reporting, `executor/` browser driver).
- `conf/` — Hydra config (`base.yaml`); resolved relative to this directory,
  so it must stay a sibling of `src/`.
- `tests/` — pytest suite.
- `runs/` — per-run trace output (gitignored, grows large — not meant to ship
  in a container image).
- `reaudit_run.py` / `reanalyze_report.py` / `rerender_report.py` /
  `resurvey_report.py` / `reaudit_report.py` — standalone repair scripts for
  re-running part of the pipeline on an existing run without redoing the
  browser sessions.
