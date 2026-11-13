// Backend API address — EDIT THIS FILE ON THE SERVER, no rebuild needed.
//
// Cloud Run service URLs are stable: they only change if the service is
// deleted and recreated, renamed, or moved to another region/project. So this
// normally gets set once. To point at a different backend (staging, or a
// custom domain later), change the line below on the server — nothing else.
//
// No trailing slash. Leave empty only if the API is on this same domain.
// Empty in git ON PURPOSE. A committed value here is used by `npm run dev` too,
// so baking the production URL in meant local development silently talked to
// the deployed backend — you could not test a backend change at all without
// noticing this line. Empty = same origin = the Vite dev proxy.
//
// Set it on the static host after uploading, e.g.
//   window.UXAGENT_API_BASE = "https://ux-agent-xxxxx.europe-central2.run.app";
window.UXAGENT_API_BASE = "";

// Hosting on https://csaapps.pl/apps/ (company Microsoft sign-in).
// On csaapps.pl the app switches to SSO by itself: no password screen; it gets a
// short-lived token from /auth/token.php and sends it as "Authorization: Bearer".
// The backend must then have AUTH_SECRET and ALLOWED_DOMAINS set, and CORS_ORIGINS
// must be exactly https://csaapps.pl.
//
// UXAGENT_AUTH = "csaapps" forces SSO mode on another host that proxies the same
// /auth/ endpoints. Leave unset everywhere else (local dev keeps the password gate).
//   window.UXAGENT_AUTH = "csaapps";
//
// UXAGENT_AUDIENCE is the app name registered with the csaapps administrator for
// the token (default "uxagent").
//   window.UXAGENT_AUDIENCE = "uxagent";
