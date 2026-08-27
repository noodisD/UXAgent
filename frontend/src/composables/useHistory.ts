import { ref } from 'vue'
import { apiFetch } from '../api'
import type { RunHistoryRow } from '../types'

export function useHistory() {
  const rows = ref<RunHistoryRow[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load() {
    loading.value = true
    error.value = null
    try {
      const res = await apiFetch('/runs', { headers: { 'Cache-Control': 'no-store' } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      rows.value = await res.json()
    } catch (e: any) {
      error.value = e?.message ?? 'Failed to load history'
      rows.value = []
    } finally {
      loading.value = false
    }
  }

  return { rows, loading, error, load }
}
