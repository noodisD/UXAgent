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

## Hosting on csaapps.pl / Hosting na csaapps.pl

**PL.** Na `https://csaapps.pl/apps/` aplikacja sama przełącza się na SSO
Microsoft (`src/sso.ts`): `index.html` ładuje wtedy `/js/guard.js` jako pierwszy
skrypt (tylko na hostach csaapps.pl; przy wymuszonym `UXAGENT_AUTH` działa sam token i przekierowanie do logowania), nie ma ekranu z hasłem, a `api.ts` pobiera token z `/auth/token.php?aud=uxagent`
i wysyła `Authorization: Bearer`. Linki do raportów i zrzutów dostają `?token=`.
Poza csaapps.pl (lokalnie, inne hosty) nic się nie zmienia. Opcjonalnie w
`config.js`: `UXAGENT_AUTH = "csaapps"`, `UXAGENT_AUDIENCE`. Audience trzeba
zarejestrować u administratora csaapps.pl, a backend potrzebuje `AUTH_SECRET`,
`ALLOWED_DOMAINS` i `CORS_ORIGINS=https://csaapps.pl`. Sekretów nie wpisujemy do repo.

**EN.** Under `https://csaapps.pl/apps/` the app switches to Microsoft SSO by
itself (`src/sso.ts`): `index.html` loads `/js/guard.js` as the first script (only on csaapps.pl hosts; with a forced `UXAGENT_AUTH` only the token and login redirect apply),
there is no password screen, and `api.ts` fetches a token from
`/auth/token.php?aud=uxagent` and sends `Authorization: Bearer`. Report and
screenshot links carry `?token=`. Anywhere else (local, other hosts) nothing
changes. Optional in `config.js`: `UXAGENT_AUTH = "csaapps"`, `UXAGENT_AUDIENCE`.
The audience must be registered with the csaapps.pl administrator; the backend
needs `AUTH_SECRET`, `ALLOWED_DOMAINS` and `CORS_ORIGINS=https://csaapps.pl`.
Never commit secrets.

## Deploying

`npm run build`, then upload the contents of `dist/` to the static host and set
the backend URL in `config.js` there.
