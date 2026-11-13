/**
 * The single seam between this app and the backend.
 *
 * Two things used to be implicit and are now explicit here:
 *
 *  1. **Origin.** Every call used to be a bare relative path, which only worked
 *     because Vite dev-proxies them. Once the dashboard is served from a static
 *     host and the API lives on GCP, those resolve against the static host.
 *     `VITE_API_BASE` supplies the absolute origin; empty (the default) keeps
 *     the dev proxy behaviour, so nothing about local work changes.
 *
 *  2. **Auth.** A shared key gates the API. Requests send it as a header — but
 *     a browser cannot put a header on an `<img src>` or a `target="_blank"`
 *     navigation, so those routes take it as `?key=` instead. That is the only
 *     reason `apiAssetUrl` exists separately from `apiUrl`.
 *
 * Centralising also gives one place to notice a 401 and re-prompt, instead of
 * five composables each turning it into an opaque "HTTP 401" string.
 */

import { currentSsoToken, getSsoToken, isRedirectingToLogin, isSsoMode, withTokenParam } from './sso'

/**
 * Backend origin, in priority order:
 *   1. window.UXAGENT_API_BASE from public/config.js — a plain file that can be
 *      edited on the host after deployment. The dashboard ships as static files
 *      to a server that cannot build anything, so without this a change of
 *      backend URL would mean a full rebuild.
 *   2. VITE_API_BASE baked in at build time.
 *   3. Empty — same origin, which is what the Vite dev proxy relies on.
 */
const RUNTIME_BASE =
  typeof window !== 'undefined' && typeof (window as any).UXAGENT_API_BASE === 'string'
    ? ((window as any).UXAGENT_API_BASE as string)
    : ''

const BASE = (RUNTIME_BASE || import.meta.env.VITE_API_BASE || '').replace(/\/+$/, '')

const KEY_STORAGE = 'uxagent_api_key'

/** Raised so callers can re-show the gate instead of a dead error screen. */
export class UnauthorizedError extends Error {
  constructor() {
    super('unauthorized')
    this.name = 'UnauthorizedError'
  }
}

type Listener = () => void
const unauthorizedListeners = new Set<Listener>()

/** App shell subscribes to re-show the password gate on any 401. */
export function onUnauthorized(fn: Listener): () => void {
  unauthorizedListeners.add(fn)
  return () => unauthorizedListeners.delete(fn)
}

export function getKey(): string {
  try { return localStorage.getItem(KEY_STORAGE) ?? '' } catch { return '' }
}

export function setKey(key: string): void {
  try { localStorage.setItem(KEY_STORAGE, key) } catch { /* private mode */ }
}

export function clearKey(): void {
  try { localStorage.removeItem(KEY_STORAGE) } catch { /* private mode */ }
}

/** Absolute URL for a backend path. */
export function apiUrl(path: string): string {
  return `${BASE}${path}`
}

/**
 * URL for a route the browser loads directly (screenshots, report.html).
 * Carries the key as a query parameter since no header can be attached.
 */
export function apiAssetUrl(path: string): string {
  const url = apiUrl(path)
  // SSO: same reasoning, with the short-lived token. Reading it here keeps
  // computed URLs reactive to token renewal.
  if (isSsoMode()) return withTokenParam(url, currentSsoToken())
  const key = getKey()
  if (!key) return url
  return `${url}${url.includes('?') ? '&' : '?'}key=${encodeURIComponent(key)}`
}

/** Where the UI is talking to — shown in the sidebar. */
export function apiOrigin(): string {
  if (BASE) return BASE.replace(/^https?:\/\//, '')
  return typeof location !== 'undefined' ? location.host : ''
}

/**
 * `fetch` against the backend: absolute URL, key header, and 401 handling.
 * Throws UnauthorizedError on 401 (after clearing the stored key) so callers
 * do not report an expired key as a generic network failure.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  if (isSsoMode()) return ssoFetch(path, init)
  const key = getKey()
  const headers = new Headers(init.headers ?? {})
  if (key) headers.set('X-API-Key', key)

  const res = await fetch(apiUrl(path), { ...init, headers })

  if (res.status === 401) {
    clearKey()
    unauthorizedListeners.forEach(fn => fn())
    throw new UnauthorizedError()
  }
  return res
}

/** One request with a Bearer token; a 401 gets one retry with a fresh token. */
async function ssoFetch(path: string, init: RequestInit): Promise<Response> {
  const attempt = async (force: boolean): Promise<Response | null> => {
    const token = await getSsoToken(force)
    if (!token) return null
    const headers = new Headers(init.headers ?? {})
    headers.set('Authorization', `Bearer ${token}`)
    return fetch(apiUrl(path), { ...init, headers })
  }
  let res = await attempt(false)
  if (res && res.status === 401) res = await attempt(true)
  if (!res || res.status === 401) {
    // Mid-redirect to the login page: no 'denied' flash.
    if (!isRedirectingToLogin()) unauthorizedListeners.forEach(fn => fn())
    throw new UnauthorizedError()
  }
  return res
}

/** Probe used by the gate to check a key before storing it. */
export async function verifyKey(key: string): Promise<boolean> {
  try {
    const res = await fetch(apiUrl('/progress'), {
      headers: { 'X-API-Key': key, 'Cache-Control': 'no-store' },
    })
    return res.ok
  } catch {
    return false // network/CORS failure — surfaced separately by the gate
  }
}
