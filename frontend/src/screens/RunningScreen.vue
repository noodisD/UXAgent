<template>
  <div class="running">
    <!-- hero banner -->
    <div class="hero">
      <div class="hero-slash"></div>
      <div class="hero-slash thin"></div>
      <div class="hero-body">
        <div class="hero-kicker mono">{{ t('run.heroKicker') }}</div>
        <div class="hero-title">{{ t('run.heroTitle') }}</div>
      </div>
    </div>

    <!-- progress header -->
    <div class="ux-card big">
      <div class="run-head">
        <div class="run-title">
          <span class="dot"></span>
          <span class="title-text">{{ label }}</span>
        </div>
        <div class="elapsed">
          <div class="elapsed-val mono">{{ elapsedStr }}</div>
          <div class="elapsed-l">{{ t('run.elapsed') }}</div>
        </div>
      </div>

      <div class="phases">
        <div v-for="(ph, i) in phaseRows" :key="ph.key" class="phase-item">
          <div class="phase-col">
            <div class="phase-circle" :class="{ done: ph.done, active: ph.active }">
              <span v-if="ph.done">✓</span><span v-else>{{ i + 1 }}</span>
            </div>
            <div class="phase-label" :class="{ on: ph.active }">{{ ph.label }}</div>
          </div>
          <div v-if="i < phaseRows.length - 1" class="phase-conn" :class="{ on: ph.done }"></div>
        </div>
      </div>

      <div class="bar"><div class="bar-fill" :style="{ width: pct + '%' }"></div></div>
      <div class="bar-meta">
        <span class="msg">{{ message }}</span>
        <span class="pct mono">{{ pct }}%</span>
      </div>
    </div>

    <!-- live agent detail -->
    <div v-if="livePersonas.length === 0" class="ux-card waiting">
      <span class="spinner"></span> {{ t('live.waiting') }}
    </div>

    <div v-for="p in livePersonas" :key="p.id" class="ux-card live-card">
      <div class="live-head">
        <div class="lh-left">
          <div class="lh-avatar mono">{{ initials(p.name) }}</div>
          <div class="lh-info">
            <div class="lh-name">{{ p.name }}</div>
            <div class="lh-url mono">
              <span class="step-chip">{{ t('live.step', { n: p.step }) }}</span>
              <span class="url-sep">·</span>
              <span class="lh-viewing">{{ t('live.viewing') }}:</span>
              <span class="lh-urltext">{{ p.url || t('live.noUrl') }}</span>
            </div>
            <div v-if="p.objective" class="lh-objective">{{ p.objective }}</div>
          </div>
        </div>
        <div class="lh-timers">
          <div v-if="p.status === 'done'" class="timer-chip mono done">✓ {{ t('live.done') }}</div>
          <div v-else-if="p.status === 'error'" class="timer-chip mono bad">✕ {{ t('live.sessionError') }}</div>
          <div v-if="p.elapsed_seconds != null" class="timer-chip mono">⏱ {{ fmtDur(p.elapsed_seconds) }}</div>
          <div v-if="p.last_activity_seconds != null && !p.finished" class="timer-chip mono" :class="staleClass(p.last_activity_seconds)">
            {{ t('live.lastActivity', { t: fmtDur(p.last_activity_seconds) }) }}
          </div>
        </div>
      </div>

      <div class="live-body">
        <!-- agent screen -->
        <div class="screen-col">
          <div class="screen-label mono">{{ t('live.screen') }}</div>
          <div class="screen-frame">
            <img v-if="p.has_screenshot" :src="shotUrl(p)" :alt="t('live.screen')" class="screen-img" />
            <div v-else class="screen-empty"><span class="spinner"></span></div>
          </div>
        </div>

        <!-- observation + action + reasoning -->
        <div class="detail-col">
          <div v-if="p.action" class="action-row">
            <span class="action-label mono">{{ t('live.lastAction') }}</span>
            <span class="action-text">{{ p.action }}</span>
          </div>

          <div class="block">
            <div class="block-label mono">{{ t('live.observation') }}</div>
            <div class="obs-text">{{ p.observation || '…' }}</div>
          </div>

          <div v-if="p.history && p.history.length" class="block">
            <div class="block-label mono">{{ t('live.history') }}</div>
            <div class="hist">
              <div v-for="h in [...p.history].reverse()" :key="h.step" class="hist-row" :class="{ current: h.current }">
                <span class="hist-step mono">{{ h.step }}</span>
                <span class="hist-desc">{{ h.description || '—' }}</span>
                <span v-if="h.duration_s != null" class="hist-dur mono" :class="durClass(h.duration_s)">
                  {{ h.current ? t('live.inProgress', { t: fmtDur(h.duration_s) }) : fmtDur(h.duration_s) }}
                </span>
              </div>
            </div>
          </div>

          <div class="block grow">
            <div class="block-label mono">{{ t('live.reasoning') }}</div>
            <div class="stream">
              <div v-for="(m, i) in p.stream" :key="i" class="mem" :style="{ opacity: streamOpacity(i) }">
                <span class="mem-kind mono" :class="m.kind">{{ t('live.kind.' + m.kind) }}</span>
                <span class="mem-content">{{ m.content }}</span>
              </div>
              <div v-if="p.stream.length === 0" class="muted sm">…</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="run-actions">
      <button class="btn-danger" @click="goNewAudit">{{ t('run.cancel') }}</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { apiAssetUrl, apiFetch } from '../api'
import { useProgress } from '../composables/useProgress'
import { useApp } from '../composables/useApp'
import { useRun } from '../composables/useRun'
import { useLive } from '../composables/useLive'
import { useI18n } from '../i18n'
import { initials } from '../theme'

const { state: app, openResults, newAudit } = useApp()
const { runId, setRun } = useRun()
const { data, start, stop } = useProgress(1200)
const { personas: livePersonas, start: startLive, stop: stopLive } = useLive(1500)
const { t } = useI18n()

const PHASE_KEYS = ['personas', 'agents', 'analysis', 'report'] as const
const PHASES = computed(() => PHASE_KEYS.map(k => ({ key: k, label: t('run.phase.' + k) })))

// Run timer anchored to the backend's elapsed_seconds (measured from the run's
// actual start), so it survives reloads/navigation. The local 1s tick only
// interpolates between /progress polls.
const elapsed = ref(0)
let timer: number | null = null
let serverElapsed: number | null = null
let serverElapsedAt = 0

const snap = computed(() => data.value as any)
const isActive = computed(() => snap.value && snap.value.status && snap.value.status !== 'idle')
const phase = computed(() => (isActive.value ? snap.value.phase : 'personas') || 'personas')
const status = computed(() => (isActive.value ? snap.value.status : 'running'))
const current = computed(() => (isActive.value ? snap.value.current : 0) || 0)
const total = computed(() => (isActive.value ? snap.value.total : 0) || 0)

const label = computed(() => t('run.phase.' + phase.value))
const message = computed(() => {
  if (status.value === 'done') return t('run.completeMsg')
  if (phase.value === 'agents') return t('run.agentsMsg', { current: current.value, total: total.value })
  return label.value + '…'
})

const pIdx = computed(() => Math.max(0, PHASES.value.findIndex(p => p.key === phase.value)))
const pct = computed(() => {
  if (status.value === 'done') return 100
  const per = 100 / PHASES.value.length
  const within = total.value > 0 ? Math.min(1, current.value / total.value) : 0
  return Math.min(99, Math.round(pIdx.value * per + within * per))
})
const phaseRows = computed(() => PHASES.value.map((p, i) => ({
  key: p.key, label: p.label, done: i < pIdx.value, active: i === pIdx.value && status.value !== 'done',
})))

function fmt(sec: number) {
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
const elapsedStr = computed(() => fmt(elapsed.value))

// The active run id, recovered from any source: the session ref, app state, or —
// crucially, when those are lost (reload / navigation / HMR) — the live /progress
// snapshot, which the backend stamps with the current run id.
const liveId = computed<string | null>(() => (snap.value && snap.value.run_id) || runId.value || app.runId || null)

function shotUrl(p: { id: string; step: number }) {
  return apiAssetUrl(`/runs/${encodeURIComponent(liveId.value || '')}/screenshot/${encodeURIComponent(p.id)}?t=${p.step}`)
}
function streamOpacity(i: number) { return Math.max(0.45, 1 - i * 0.08) }

// Whenever we learn (or relearn) the run id, persist it and (re)start live polling.
watch(liveId, (id) => {
  if (!id) return
  app.runId = id
  setRun(id)
  startLive(id)
}, { immediate: true })

// Tracked so onUnmounted can clear it. Untracked, clicking "Anuluj" within 900ms
// of completion yanked the user from the wizard back to Results.
let doneTimer: number | null = null
watch(status, (s) => {
  const id = liveId.value
  if (s !== 'done' || !id) return
  if (doneTimer !== null) clearTimeout(doneTimer)
  doneTimer = window.setTimeout(() => openResults(id), 900)
})

// Re-anchor the timer on every progress snapshot that carries elapsed_seconds.
watch(snap, (s) => {
  if (s && typeof s.elapsed_seconds === 'number') {
    serverElapsed = s.elapsed_seconds
    serverElapsedAt = Date.now()
    elapsed.value = Math.floor(s.elapsed_seconds)
  }
})

onMounted(() => {
  start()
  timer = window.setInterval(() => {
    if (serverElapsed !== null) {
      elapsed.value = Math.floor(serverElapsed + (Date.now() - serverElapsedAt) / 1000)
    } else {
      elapsed.value += 1
    }
  }, 1000)
})
onUnmounted(() => {
  stop(); stopLive()
  if (timer !== null) clearInterval(timer)
  if (doneTimer !== null) clearTimeout(doneTimer)
})

// --- live timers & anomaly hints ----------------------------------------
function fmtDur(s: number): string {
  if (s < 60) return `${Math.round(s)}s`
  const m = Math.floor(s / 60)
  return `${m}m ${String(Math.round(s % 60)).padStart(2, '0')}s`
}
// >2 min of silence is suspicious, >5 min is almost certainly a stall
function staleClass(s: number): string {
  return s > 300 ? 'bad' : s > 120 ? 'warn' : ''
}
// per-step duration: normal steps run seconds-to-a-minute; longer = anomaly
function durClass(s: number): string {
  return s > 300 ? 'bad' : s > 120 ? 'warn' : ''
}

async function goNewAudit() {
  stop(); stopLive()
  // Ask the backend to abort the run; navigate away regardless of outcome.
  // liveId, not runId.value || app.runId: those two are exactly what is lost on
  // the reload/HMR recovery path that liveId exists for, so the `if (id)` guard
  // below failed and NO cancel request was sent — the user navigated away
  // believing they had stopped a run that kept burning LLM budget.
  const id = liveId.value
  if (id) {
    try {
      const res = await apiFetch(`/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' })
      if (!res.ok) console.error(`[cancel] backend refused the cancel: HTTP ${res.status}`)
    } catch (e) {
      // Still navigate away — trapping the user helps nobody — but never
      // silently: if this call fails the run keeps going, burning LLM budget,
      // and the old empty `catch {}` made that completely invisible.
      console.error('[cancel] request failed; the run may still be running:', e)
    }
  }
  newAudit()
}
</script>

<style scoped lang="scss">
.running { width: 100%; padding: 28px 40px 60px; display: flex; flex-direction: column; gap: 18px; }

.hero { position: relative; overflow: hidden; background: var(--hero); border-radius: 16px; padding: 30px 32px; }
.hero-slash { position: absolute; top: -40%; right: -6%; width: 60%; height: 200%; background: var(--ac); transform: rotate(-25deg); opacity: .92; }
.hero-slash.thin { right: 14%; width: 10px; background: var(--surface); opacity: .14; }
.hero-body { position: relative; }
.hero-kicker { font: 600 11px/1 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: rgba(255,255,255,.6); margin-bottom: 12px; }
.hero-title { font-family: var(--expanded); font-weight: 700; font-size: 30px; line-height: 1.1; color: #fff; max-width: 460px; text-transform: uppercase; }
.ux-card.big { border-radius: 14px; padding: 28px; }
.run-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
.run-title { display: flex; align-items: center; gap: 12px; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: var(--ac); animation: uxpulse 1.3s infinite; }
.title-text { font-weight: 700; font-size: 17px; }
.elapsed { text-align: right; }
.elapsed-val { font: 700 22px var(--mono); }
.elapsed-l { font-size: 11px; color: #9aa0a8; }

.phases { display: flex; align-items: flex-start; margin-bottom: 8px; }
.phase-item { display: flex; align-items: flex-start; flex: 1; }
.phase-col { display: flex; flex-direction: column; align-items: center; gap: 9px; flex: none; width: 92px; }
.phase-circle { width: 38px; height: 38px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font: 700 14px var(--mono); background: #eceef1; color: var(--faint); }
.phase-circle.done { background: var(--ac); color: #fff; }
.phase-circle.active { background: var(--ac); color: #fff; box-shadow: 0 0 0 5px rgba(42,111,219,.16); }
.phase-label { font: 600 11.5px/1.3 var(--sans); text-align: center; color: var(--faint); }
.phase-label.on { color: var(--ink); font-weight: 700; }
.phase-conn { flex: 1; height: 3px; border-radius: 2px; margin: 18px -8px 0; background: #e6e8ec; }
.phase-conn.on { background: var(--ac); }
.bar { margin-top: 22px; height: 8px; background: #eef0f3; border-radius: 5px; overflow: hidden; }
.bar-fill { height: 100%; background: var(--ac); border-radius: 5px; transition: width .4s ease; }
.bar-meta { display: flex; justify-content: space-between; margin-top: 9px; }
.bar-meta .msg { font-size: 13px; color: var(--muted); }
.bar-meta .pct { font: 600 13px var(--mono); }

.waiting { display: flex; align-items: center; gap: 12px; color: var(--muted); font-size: 14px; }
.spinner { width: 14px; height: 14px; border: 2px solid var(--ac); border-right-color: transparent; border-radius: 50%; display: inline-block; animation: uxspin .7s linear infinite; }

.live-card { padding: 0; overflow: hidden; }
.live-head { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--border-soft); }
.lh-left { display: flex; align-items: center; gap: 12px; min-width: 0; }
.lh-avatar { width: 38px; height: 38px; border-radius: 50%; background: #eef3fc; color: var(--ac); display: flex; align-items: center; justify-content: center; font: 700 14px var(--mono); flex: none; }
.lh-name { font: 700 15px var(--sans); }
.lh-url { font-size: 12px; color: var(--muted-2); margin-top: 3px; display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.step-chip { background: var(--ac); color: #fff; padding: 2px 8px; border-radius: 20px; font: 600 11px var(--mono); }
.url-sep { color: var(--faint); }
.lh-viewing { color: var(--muted-2); }
.lh-urltext { color: var(--ac); }
.lh-objective { font-size: 12px; color: var(--muted-2); margin-top: 4px; font-style: italic; }

/* per-agent timers */
.lh-timers { display: flex; flex-direction: column; gap: 6px; align-items: flex-end; flex: none; }
.timer-chip { font: 600 11.5px var(--mono); padding: 4px 9px; border-radius: 6px; background: var(--surface-3); color: var(--text-3); white-space: nowrap; }
.timer-chip.warn { background: var(--warn-bg); color: var(--warn-text); }
.timer-chip.bad { background: var(--accent-tint); color: var(--ac); animation: uxpulse 1.3s infinite; }
.timer-chip.done { background: var(--ok-bg, #e6f6ec); color: var(--ok-text, #1a7f4b); }

/* step history */
.hist { display: flex; flex-direction: column; gap: 4px; max-height: 168px; overflow-y: auto; }
.hist-row { display: flex; align-items: baseline; gap: 9px; padding: 5px 8px; border-radius: 6px; background: var(--surface-2); border: 1px solid var(--border-2); }
.hist-row.current { border-color: var(--accent-tint-border); background: var(--accent-tint); }
.hist-step { font: 700 10.5px var(--mono); color: var(--text-faint); width: 18px; flex: none; text-align: right; }
.hist-desc { font-size: 12px; color: var(--text-2); line-height: 1.35; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hist-dur { font: 600 11px var(--mono); color: var(--text-muted); flex: none; }
.hist-dur.warn { color: var(--warn-text); }
.hist-dur.bad { color: var(--ac); font-weight: 700; }

.live-body { display: grid; grid-template-columns: 1.35fr 1fr; gap: 0; align-items: stretch; }
.screen-col { padding: 22px 24px; border-right: 1px solid var(--border-soft); }
.screen-label, .block-label { font: 600 10px var(--mono); letter-spacing: .05em; text-transform: uppercase; color: var(--muted-2); margin-bottom: 9px; }
.screen-frame { border: 1px solid var(--border); border-radius: 9px; overflow: hidden; background: #f1f2f4; aspect-ratio: 16 / 9; display: flex; align-items: center; justify-content: center; }
.screen-img { width: 100%; height: 100%; object-fit: contain; object-position: top center; display: block; }
.screen-empty { display: flex; align-items: center; justify-content: center; }

.detail-col { padding: 22px 24px; min-width: 0; display: flex; flex-direction: column; gap: 16px; }
.action-row { display: flex; gap: 10px; align-items: baseline; background: #fcfcfd; border: 1px solid var(--border-soft); border-radius: 8px; padding: 10px 12px; }
.action-label { font-size: 10px; letter-spacing: .05em; text-transform: uppercase; color: var(--muted-2); flex: none; }
.action-text { font-size: 13.5px; color: var(--ink); line-height: 1.4; }
.obs-text { font-size: 13px; line-height: 1.55; color: var(--ink-2); max-height: 150px; overflow-y: auto; background: #fcfcfd; border: 1px solid var(--border-soft); border-radius: 8px; padding: 11px 13px; }
.block.grow { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.stream { display: flex; flex-direction: column; gap: 9px; flex: 1; min-height: 140px; overflow-y: auto; }
.mem { display: flex; gap: 10px; align-items: flex-start; }
.mem-kind { font: 700 9.5px var(--mono); letter-spacing: .04em; text-transform: uppercase; padding: 3px 7px; border-radius: 5px; flex: none; margin-top: 1px; }
.mem-kind.plan { background: #eef3fc; color: var(--ac); }
.mem-kind.thought { background: #f1f2f4; color: #5b6470; }
.mem-kind.reflection { background: #eef6ee; color: #1f9d57; }
.mem-kind.action { background: #fbf2e3; color: #a8691a; }
.mem-content { font-size: 13px; line-height: 1.5; color: var(--ink-2); white-space: pre-wrap; }
.muted { color: var(--muted-2); } .muted.sm { font-size: 12.5px; }

.run-actions { display: flex; }

@media (max-width: 760px) {
  .live-body { grid-template-columns: 1fr; }
  .screen-col { border-right: none; border-bottom: 1px solid var(--border-soft); }
}
</style>
