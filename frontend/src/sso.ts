/**
 * Sign-in through the csaapps.pl session (SSO), used when the dashboard is
 * hosted under https://csaapps.pl/apps/. The Flask backend lives on Cloud Run
 * (another domain), so it cannot see the csaapps session cookie: the page asks
 * the same-origin /auth/token.php for a short-lived signed token and sends it
 * as `Authorization: Bearer`. We do this here rather than loading the site's
 * /js/auth.js because the api layer is TypeScript and must own retry and
 * asset-URL handling itself.
 *
 * Everywhere else (local dev, other hosts) `isSsoMode()` is false and none of
 * this runs.
 */
import { ref } from 'vue'

const RENEW_MARGIN_MS = 60_000
const LOGIN_LOOP_WINDOW_MS = 60_000
const LOOP_KEY = 'uxagent_sso_redirect'

/** Reactive so computed asset URLs pick up a renewed token. */
const tokenRef = ref('')
let expiresAtMs = 0
let inflight: Promise<string | null> | null = null
let renewTimer: ReturnType<typeof setTimeout> | undefined
let redirecting = false

export interface SsoTokenPayload { sub?: string; email?: string; name?: string; exp?: number }

/** True on csaapps.pl, or where public/config.js sets window.UXAGENT_AUTH = 'csaapps'. */
export function isSsoMode(): boolean {
  if (typeof window === 'undefined') return false
  const host = (window.location?.hostname ?? '').toLowerCase()
  if (host === 'csaapps.pl' || host.endsWith('.csaapps.pl')) return true
  return (window as any).UXAGENT_AUTH === 'csaapps'
}

export function ssoAudience(): string {
  const a = typeof window !== 'undefined' ? (window as any).UXAGENT_AUDIENCE : ''
  return typeof a === 'string' && a.trim() ? a.trim() : 'uxagent'
}

/** Reads the payload part of a token. Does NOT verify the signature (the backend does). */
export function decodeTokenPayload(token: string): SsoTokenPayload | null {
  try {
    const body = token.split('.')[0] ?? ''
    const b64 = body.replace(/-/g, '+').replace(/_/g, '/')
    const bin = atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4))
    const bytes = Uint8Array.from(bin, c => c.charCodeAt(0))
    const p = JSON.parse(new TextDecoder().decode(bytes))
    return p && typeof p === 'object' ? p : null
  } catch {
    return null
  }
}

/** Absolute expiry in ms from the payload's `exp` (unix seconds); 0 if unreadable. */
export function tokenExpiresAtMs(token: string): number {
  const exp = decodeTokenPayload(token)?.exp
  return typeof exp === 'number' && isFinite(exp) ? exp * 1000 : 0
}

/** Appends `token=` to a URL that a browser loads directly. */
export function withTokenParam(url: string, token: string): string {
  if (!token) return url
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
}

/** The cached token ('' before the first fetch). Reactive. */
export function currentSsoToken(): string {
  return tokenRef.value
}

export function ssoUser(): { email: string; name: string } | null {
  const p = tokenRef.value ? decodeTokenPayload(tokenRef.value) : null
  return p?.email ? { email: p.email, name: p.name ?? '' } : null
}

/** True while the page is being sent to the csaapps login. */
export function isRedirectingToLogin(): boolean {
  return redirecting
}

/**
 * Sends the browser to the csaapps login, at most once per minute: if a login
 * just happened and the token endpoint still says 401, the account is simply
 * not allowed and looping would hide that.
 */
function redirectToLogin(): void {
  let last = 0
  try { last = Number(sessionStorage.getItem(LOOP_KEY)) || 0 } catch { /* private mode */ }
  if (redirecting || Date.now() - last < LOGIN_LOOP_WINDOW_MS) return
  try { sessionStorage.setItem(LOOP_KEY, String(Date.now())) } catch { /* private mode */ }
  redirecting = true
  const back = encodeURIComponent(window.location.pathname + window.location.search)
  window.location.href = `/auth/login.php?return=${back}`
}

function dropToken(): void {
  tokenRef.value = ''
  expiresAtMs = 0
  if (renewTimer) clearTimeout(renewTimer)
}

function renewInBackground(): void {
  getSsoToken(true).catch(() => {
    // Transient failure: try again rather than leave rendered URLs to expire.
    if (renewTimer) clearTimeout(renewTimer)
    renewTimer = setTimeout(renewInBackground, 30_000)
  })
}

async function fetchToken(): Promise<string | null> {
  const res = await fetch(`/auth/token.php?aud=${encodeURIComponent(ssoAudience())}`, {
    credentials: 'include',
    headers: { Accept: 'application/json' },
  })
  if (res.status === 401) {
    dropToken()
    redirectToLogin()
    return null
  }
  if (!res.ok) throw new Error(`token endpoint HTTP ${res.status}`)
  const data = await res.json()
  const token = typeof data?.access_token === 'string' ? data.access_token : ''
  if (!token) throw new Error('token endpoint returned no access_token')
  tokenRef.value = token
  // An unreadable exp is treated as short-lived rather than eternal.
  expiresAtMs = tokenExpiresAtMs(token) || Date.now() + 5 * 60_000
  if (renewTimer) clearTimeout(renewTimer)
  // Renew in the background so asset URLs already rendered stay valid.
  const delay = Math.max(expiresAtMs - 2 * RENEW_MARGIN_MS - Date.now(), 30_000)
  renewTimer = setTimeout(renewInBackground, delay)
  return token
}

/**
 * A valid token, fetched on first use and then cached until one minute before
 * it expires. Concurrent callers share one request. Returns null when the
 * session is gone (the page is then being redirected to the login).
 */
export function getSsoToken(force = false): Promise<string | null> {
  if (!force && tokenRef.value && Date.now() < expiresAtMs - RENEW_MARGIN_MS) {
    return Promise.resolve(tokenRef.value)
  }
  if (!inflight) {
    inflight = fetchToken().finally(() => { inflight = null })
  }
  return inflight
}
