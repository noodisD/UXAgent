import { reactive } from 'vue'

export type Screen = 'wizard' | 'running' | 'results' | 'history'

// Module-level singleton so every screen shares navigation + the active run id.
const state = reactive({
  screen: 'wizard' as Screen,
  step: 1,           // wizard step 1..3
  runId: null as string | null,      // the run launched this session (drives Running screen)
  viewRunId: null as string | null,  // the run being viewed in Results (may be a history row)
})

export function useApp() {
  function goScreen(s: Screen) { state.screen = s }
  function goStep(n: number) { state.step = Math.min(3, Math.max(1, n)) }
  function nextStep() { if (state.step < 3) state.step++ }
  function prevStep() { if (state.step > 1) state.step-- }
  function newAudit() {
    state.runId = null
    state.viewRunId = null
    state.step = 1
    state.screen = 'wizard'
  }
  function openResults(runId: string) {
    state.viewRunId = runId
    state.screen = 'results'
  }
  return { state, goScreen, goStep, nextStep, prevStep, newAudit, openResults }
}
