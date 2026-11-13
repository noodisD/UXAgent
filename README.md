# UXAgent

AI-powered UX research platform. Instead of recruiting participants, it
generates synthetic personas and sends LLM-driven agents (Playwright) to use a
website autonomously. Each agent perceives the page, plans, acts and reflects —
then the platform produces a UX audit: Nielsen heuristic findings, a
5-second first-impression test, accessibility and viewport checks, scroll/form
behaviour, a post-session survey, and an aggregated narrative report.

Built for auditing Polish sites: the dashboard is Polish-first with an EN
toggle, and the generated report prose is Polish.

## Why findings are trustworthy

An early ground-truth check was sobering: a client team reviewed a 5-persona
report and judged only ~2 of 8 issues real. Most of the rest were the agent's
own difficulty written up as site defects. The pipeline now gates every claim
before it reaches a report:

- **Evidence resolution** — a finding's cited URL is replaced with the one the
  trace actually recorded, so claims can't drift to a page the agent never saw.
- **Harness attribution** — findings anchored on a stuck click loop or a
  perception artifact are flagged, not shipped as defects.
- **Site scoping** — anything observed off the audited domain is excluded (an
  agent once wandered to a competitor and filed 6 findings about it).
- **Claim verification** — each surviving claim is re-checked against the cited
  step's full observation and demoted if unsupported.
- **Root-cause clustering** — one bug filed under six different Nielsen
  heuristics is counted once, not six times.
- **Grounded task completion** — success/partial/failure is derived from what
  the trace shows (did the run reach the cart, the checkout?), not from the
  agent's self-rating. On a 20-persona run the two disagreed 14 times.

On the largest run to date (20 personas × 70 steps) these gates reduced 98 raw
findings to 28.

## Layout

```
backend/    Flask API + Playwright pipeline   → see backend/README.md
frontend/   Vue 3 + Vite dashboard
```

The two halves deploy independently — backend as a container, frontend as
static files.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js + npm
- Google Cloud project with Vertex AI enabled (the default LLM provider)

## Setup

**Backend**

```bash
cd backend
# UV_LINK_MODE=copy is required on OneDrive or any cloud-synced folder —
# uv's hardlinks fail there. Harmless everywhere else.
UV_LINK_MODE=copy uv sync
UV_LINK_MODE=copy uv run playwright install chromium
cp .env.example .env      # then fill it in
```

**Frontend**

```bash
cd frontend
npm install
```

## Running locally

Two terminals:

```bash
# PYTHONIOENCODING=utf-8 is required on Windows — the report prose is Polish.
cd backend && PYTHONIOENCODING=utf-8 uv run python -m simulated_web_agent.main.app   # :8000
```

```bash
cd frontend && npm run dev                                      # :5173
```

Open http://localhost:5173. In development Vite proxies the API paths to the
backend, so no extra configuration is needed.

## Configuration

Set in `backend/.env` locally; supplied as environment variables in deployment.

| Variable | Purpose |
|---|---|
| `VERTEXAI_PROJECT`, `VERTEXAI_LOCATION` | Vertex AI project and region |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a service-account JSON. **Local only** — on GCP the attached service account supplies credentials automatically |
| `GOOGLE_PAGESPEED_API_KEY` | Optional; enables the PageSpeed pass |
| `UXAGENT_API_KEY` | Dashboard password. **Unset means the API is open** — fine locally, never in a deployment |
| `CORS_ORIGINS` | Comma-separated origin allowlist, needed when the dashboard is served from another host (for csaapps.pl: exactly `https://csaapps.pl`) |
| `AUTH_SECRET` | csaapps.pl SSO: shared secret that signs the tokens from `/auth/token.php`. Cloud Run: `--set-secrets AUTH_SECRET=<app>-auth-secret:latest`. Additional to `UXAGENT_API_KEY`; a request needs either |
| `ALLOWED_DOMAINS` | Comma-separated e-mail domains accepted in SSO tokens. Default deny: with `AUTH_SECRET` set and this empty every token is rejected. Cloud Run: `--update-env-vars "^;^ALLOWED_DOMAINS=<d1>,<d2>"` |
| `HEADLESS` | `false` to watch the browser while agents run |

The provider is chosen by `llm_provider` in `backend/conf/base.yaml`; it is
`gemini` (via Vertex AI). The OpenAI/Anthropic/AWS keys are only read if you
switch it.

## Tests

```bash
cd backend  && uv run pytest          # 281 tests
cd frontend && npx vue-tsc --noEmit
```

## Deployment

`backend/Dockerfile` builds the API with Chromium included. The container runs
as a non-root user (Chromium's sandbox refuses to start as root) and honours the
`PORT` environment variable.

Two constraints follow from the architecture rather than preference: the audit
runs in a **background thread after the HTTP response returns**, so a serverless
host must keep the CPU allocated; and run state is held **in memory**, so it
must be a single instance/worker. Details are in the Dockerfile's deployment
notes.

Run artifacts are written to `backend/runs/` — several GB per large run, and
ephemeral inside a container. Mount durable storage before treating any report
as permanent.

## Attribution

Derived from [UXAgent](https://github.com/neuhai/UXAgent) (MIT) by Yuxuan Lu et
al., NEU-HAI — see [NOTICE](NOTICE) and [arXiv:2504.09407](https://arxiv.org/abs/2504.09407).
The agent loop, browser executor and memory system originate there; the audit
and reporting pipeline, dashboard and deployment setup were built on top.

Licensed under the MIT License — see [LICENSE](LICENSE).
