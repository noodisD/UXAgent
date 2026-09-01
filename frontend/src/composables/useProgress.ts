import { ref, onUnmounted } from 'vue'
import { apiFetch } from '../api'

type Snapshot =
  | { status: 'idle'; message: string }
  | {
      run_id: string
      // 'cancelled' is emitted by app.py's cancel endpoint and passed through
      // by /progress. Omitting it forced both consumers to cast `as any`,
      // which defeated the union entirely and hid the gap.
      status: 'running' | 'done' | 'error' | 'cancelled'
      phase: string
      label: string
      current: number
      total: number
      message: string
    }

export function useProgress(pollMs = 1000) {
  const data = ref<Snapshot>({ status: 'idle', message: 'No run started yet.' })
  const error = ref<string | null>(null)
  let timer: number | null = null

  const fetchOnce = async () => {
    try {
      const res = await apiFetch('/progress', { headers: { 'Cache-Control': 'no-store' } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      data.value = await res.json()
      error.value = null
    } catch (e: any) {
      error.value = e?.message ?? 'Failed to fetch progress'
    }
  }

  const start = () => {
    if (timer !== null) return
    fetchOnce()
    timer = window.setInterval(fetchOnce, pollMs)
  }
  const stop = () => {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  onUnmounted(stop)
  return { data, error, start, stop, fetchOnce }
}
