import { onUnmounted, ref } from 'vue'
import { apiFetch } from '../api'

export type LiveMemory = { kind: string; content: string }
export type LiveStep = { step: number; description: string; duration_s: number | null; current: boolean }
export type LivePersona = {
  id: string
  name: string
  objective?: string
  step: number
  url: string
  title: string
  action: string
  observation: string
  stream: LiveMemory[]
  has_screenshot: boolean
  elapsed_seconds?: number | null
  last_activity_seconds?: number | null
  history?: LiveStep[]
  status?: 'running' | 'done' | 'error'
  finished?: boolean
}

export function useLive(pollMs = 1500) {
  const personas = ref<LivePersona[]>([])
  const error = ref<string | null>(null)
  let timer: number | null = null
  let runId: string | null = null

  async function fetchOnce() {
    const forRun = runId
    if (!forRun) return
    try {
      const res = await apiFetch(`/runs/${encodeURIComponent(forRun)}/live`, { headers: { 'Cache-Control': 'no-store' } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      // The run can change while this request is in flight; without this check
      // the previous run's personas overwrite the new one's.
      if (forRun !== runId) return
      personas.value = data.personas || []
      error.value = null
    } catch (e: any) {
      if (forRun !== runId) return
      error.value = e?.message ?? 'Failed to fetch live state'
    }
  }

  function start(id: string) {
    // Switching runs must clear the previous run's personas, otherwise the old
    // ones stay on screen until the first response for the new run arrives.
    if (runId !== id) {
      personas.value = []
      error.value = null
    }
    runId = id
    if (timer !== null) {
      fetchOnce()
      return
    }
    fetchOnce()
    timer = window.setInterval(fetchOnce, pollMs)
  }
  function stop() {
    if (timer !== null) { clearInterval(timer); timer = null }
  }

  onUnmounted(stop)
  return { personas, error, start, stop }
}
