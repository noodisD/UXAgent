import { ref } from 'vue'
import { buildRunPayload } from './useForm'
import type { WizardForm } from '../types'

const RUN_KEY = 'ux_active_run'

// Shared across screens: the run launched this session. Persisted so a page
// reload (or navigating away) can still return to the live run.
const runId = ref<string | null>(localStorage.getItem(RUN_KEY) || null)
const siteSlug = ref<string | null>(null)
const error = ref<string | null>(null)

export function useRun() {
  async function start(form: WizardForm): Promise<string> {
    error.value = null
    const payload = buildRunPayload(form)
    const res = await fetch('/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const text = await res.text()
      const msg = `Run failed: ${res.status} ${text}`
      error.value = msg
      throw new Error(msg)
    }
    const data = await res.json()
    runId.value = data.run_id
    siteSlug.value = data.site_slug
    try { localStorage.setItem(RUN_KEY, data.run_id) } catch { /* ignore */ }
    return data.run_id
  }
  function setRun(id: string) {
    runId.value = id
    try { localStorage.setItem(RUN_KEY, id) } catch { /* ignore */ }
  }
  function clearRun() {
    runId.value = null
    try { localStorage.removeItem(RUN_KEY) } catch { /* ignore */ }
  }
  return { runId, siteSlug, error, start, setRun, clearRun }
}
