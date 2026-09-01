import { ref } from 'vue'
import { apiFetch } from '../api'
import type { RunDetail } from '../types'

// Module-level singleton so the App shell (header) and ResultsScreen share one payload.
const data = ref<RunDetail | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

// Request generation. `data` is a module-level singleton shared by the App
// header and ResultsScreen, and load() had no way to tell a stale response from
// a current one — two quick clicks in the history list could land run A's
// payload after run B's, rendering A's personas under B's header. Only the most
// recent call is allowed to write.
let generation = 0

async function load(runId: string) {
  const mine = ++generation
  loading.value = true
  error.value = null
  try {
    const res = await apiFetch(`/runs/${encodeURIComponent(runId)}`, {
      headers: { 'Cache-Control': 'no-store' },
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const payload = await res.json()
    if (mine !== generation) return
    data.value = payload
  } catch (e: any) {
    if (mine !== generation) return
    error.value = e?.message ?? 'Failed to load results'
    data.value = null
  } finally {
    if (mine === generation) loading.value = false
  }
}

export function useResults() {
  return { data, loading, error, load }
}
