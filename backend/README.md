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

Copy `.env` here (repo-root-relative to this directory) with the required
keys — see the "Environment gotchas" section of `../CLAUDE.md` for what each
one is for and which ones actually matter under the active `llm_provider`.

## Run

```bash
PYTHONIOENCODING=utf-8 uv run python -m simulated_web_agent.main.app
```

Serves the API on port 8000. The frontend's dev server proxies `/run`,
`/progress`, `/generate-example-persona`, and `/runs/*` to this port.

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
