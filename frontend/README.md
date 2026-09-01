# UXAgent dashboard

Vue 3 + TypeScript + Vite. Hand-written SFCs and `src/style.scss` — no component
library. Polish-first with an EN toggle (`src/i18n.ts`, default `pl`).

The backend it talks to lives in `../backend`; see the project `README.md` at the
repository root for the whole picture.

## Commands

```bash
npm install
npm run dev          # :5173, proxies the API paths to the backend on :8000
npm run typecheck    # must be clean before calling frontend work done
npm run build        # vue-tsc -b && vite build  ->  dist/
```

**Use `npm run typecheck`, not `npx vue-tsc --noEmit`.** `tsconfig.json` is a
solution file (`"files": []` plus project references), so the bare form resolves
zero input files and exits 0 — it reported success for months while the build
was failing. The script points at `tsconfig.app.json`, which is what `vue-tsc -b`
actually checks.

## Talking to the backend

`src/api.ts` is the only seam. Nothing else should call `fetch` directly.

It resolves the backend origin in this order:

1. `window.UXAGENT_API_BASE`, set by `public/config.js`
2. `VITE_API_BASE`, baked in at build time
3. empty — same origin, which is what the dev proxy relies on

`public/config.js` is a plain file that ships alongside the built assets and can
be edited **on the static host** after deployment, so moving the backend needs no
rebuild. It is deliberately empty in git: a live URL committed there would
publish an endpoint next to the code documenting its API.

`api.ts` also owns auth. The shared password is typed by the user and kept in
`localStorage`; `apiFetch` sends it as `X-API-Key`, and `apiAssetUrl` appends it
as `?key=` instead — a browser cannot attach a header to an `<img src>` or a
`target="_blank"` navigation, so screenshots and `report.html` need the query
form. A 401 anywhere clears the key and re-raises the gate.

There is no key or secret in this bundle by design. Anything baked in at build
time is readable by anyone who opens devtools.

## Deploying

`npm run build`, then upload the contents of `dist/` to the static host and set
the backend URL in `config.js` there.
