import { ref } from 'vue'
import type { RunDetail } from '../types'

// Module-level singleton so the App shell (header) and ResultsScreen share one payload.
const data = ref<RunDetail | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function load(runId: string) {
  loading.value = true
  error.value = null
  try {
    const res = await fetch(`/runs/${encodeURIComponent(runId)}`, {
      headers: { 'Cache-Control': 'no-store' },
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    data.value = await res.json()
  } catch (e: any) {
    error.value = e?.message ?? 'Failed to load results'
    data.value = null
  } finally {
    loading.value = false
  }
}

export function useResults() {
  return { data, loading, error, load }
}
