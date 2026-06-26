import { onUnmounted, ref } from 'vue'

export type LiveMemory = { kind: string; content: string }
export type LivePersona = {
  id: string
  name: string
  step: number
  url: string
  title: string
  action: string
  observation: string
  stream: LiveMemory[]
  has_screenshot: boolean
}

export function useLive(pollMs = 1500) {
  const personas = ref<LivePersona[]>([])
  const error = ref<string | null>(null)
  let timer: number | null = null
  let runId: string | null = null

  async function fetchOnce() {
    if (!runId) return
    try {
      const res = await fetch(`/runs/${encodeURIComponent(runId)}/live`, { headers: { 'Cache-Control': 'no-store' } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      personas.value = data.personas || []
      error.value = null
    } catch (e: any) {
      error.value = e?.message ?? 'Failed to fetch live state'
    }
  }

  function start(id: string) {
    runId = id
    if (timer !== null) return
    fetchOnce()
    timer = window.setInterval(fetchOnce, pollMs)
  }
  function stop() {
    if (timer !== null) { clearInterval(timer); timer = null }
  }

  onUnmounted(stop)
  return { personas, error, start, stop }
}
