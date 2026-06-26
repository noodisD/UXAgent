<template>
  <div class="shell" :style="{ '--ac': accent }">
    <!-- top progress bar (active run) -->
    <div v-if="runActive" class="topbar"><div class="topbar-fill" :style="{ width: topPct + '%' }"></div></div>

    <!-- SIDEBAR -->
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark"><div class="brand-ring"></div></div>
        <div>
          <div class="brand-name">UXAgent</div>
          <div class="brand-tag mono">synthetic ux research</div>
        </div>
      </div>

      <nav class="nav">
        <button class="nav-btn" :class="{ on: app.screen === 'wizard' || app.screen === 'running' }" @click="newAudit">
          <span class="nav-ico box"></span> {{ t('nav.newAudit') }}
        </button>
        <button class="nav-btn" :class="{ on: app.screen === 'history' }" @click="goScreen('history')">
          <span class="nav-ico lines"></span> {{ t('nav.history') }}
        </button>
      </nav>

      <div class="side-foot">
        <button v-if="runActive" class="run-card" @click="goScreen('running')" :title="t('run.return')">
          <div class="rc-head"><span class="rc-dot"></span><span class="rc-label mono">{{ t('run.active') }}</span><span class="rc-go">→</span></div>
          <div class="rc-msg">{{ runLabel }}</div>
          <div class="rc-bar"><div class="rc-fill" :style="{ width: topPct + '%' }"></div></div>
        </button>
        <div class="lang">
          <button class="lang-btn" :class="{ on: locale === 'pl' }" @click="setLocale('pl')">PL</button>
          <button class="lang-btn" :class="{ on: locale === 'en' }" @click="setLocale('en')">EN</button>
        </div>
        <div class="version mono">v2.4 · {{ t('version.connected') }}<br>localhost:8000</div>
      </div>
    </aside>

    <!-- MAIN -->
    <main class="main">
      <header class="header">
        <div>
          <div class="h-title">{{ header.title }}</div>
          <div class="h-sub">{{ header.sub }}</div>
        </div>
        <div class="h-actions">
          <template v-if="app.screen === 'results'">
            <a class="btn" :href="reportUrl" target="_blank" rel="noopener">{{ t('btn.download') }}</a>
            <a class="btn btn-primary" :href="reportUrl" target="_blank" rel="noopener">{{ t('btn.openReport') }}</a>
          </template>
          <button v-else-if="app.screen === 'history'" class="btn btn-primary" @click="newAudit">{{ t('btn.newAudit') }}</button>
        </div>
      </header>

      <div class="content">
        <transition name="fade" mode="out-in">
          <WizardScreen v-if="app.screen === 'wizard'" key="wizard" />
          <RunningScreen v-else-if="app.screen === 'running'" key="running" />
          <ResultsScreen v-else-if="app.screen === 'results'" key="results" />
          <HistoryScreen v-else key="history" />
        </transition>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, provide } from 'vue'
import { FORM_KEY } from './types'
import { createWizardForm } from './composables/useForm'
import { useApp } from './composables/useApp'
import { useProgress } from './composables/useProgress'
import { useResults } from './composables/useResults'
import { useI18n } from './i18n'
import WizardScreen from './screens/WizardScreen.vue'
import RunningScreen from './screens/RunningScreen.vue'
import ResultsScreen from './screens/ResultsScreen.vue'
import HistoryScreen from './screens/HistoryScreen.vue'

const accent = '#2a6fdb'

const form = createWizardForm()
provide(FORM_KEY, form)

const { state: app, goScreen, newAudit } = useApp()
const { data: results } = useResults()
const { t, locale, setLocale } = useI18n()

// Lightweight progress read for the sidebar/topbar (RunningScreen drives its own polling).
const { data: progress, start } = useProgress(1500)
start()

const snap = computed(() => progress.value as any)
// Show the active-run card whenever a run is in progress and we're not already
// on the running screen — from any other screen it's the way back.
const runActive = computed(() => snap.value?.status === 'running' && app.screen !== 'running')
const runLabel = computed(() => {
  void locale.value
  const s = snap.value
  if (!s || !s.phase) return t('run.initialising')
  if (s.phase === 'agents') return t('run.agentsMsg', { current: s.current || 0, total: s.total || 0 })
  return t('run.phase.' + s.phase) + '…'
})
const topPct = computed(() => {
  const s = snap.value
  if (!s || s.status === 'idle') return 0
  if (s.status === 'done') return 100
  const phases = ['personas', 'agents', 'analysis', 'report']
  const idx = Math.max(0, phases.indexOf(s.phase))
  const per = 100 / phases.length
  const within = s.total > 0 ? Math.min(1, s.current / s.total) : 0
  return Math.min(99, Math.round(idx * per + within * per))
})

const reportUrl = computed(() => app.viewRunId ? `/runs/${encodeURIComponent(app.viewRunId)}/report.html` : '#')

const header = computed(() => {
  // reference locale so the header re-computes on language switch
  void locale.value
  switch (app.screen) {
    case 'running': return { title: t('header.running.title'), sub: t('header.running.sub') }
    case 'results': {
      const m = results.value?.meta
      return {
        title: m?.site || t('header.results.fallback'),
        sub: m ? `${m.personaCount} ${t('header.personas.suffix')} · ${m.date}${m.duration ? ' · ' + m.duration : ''}` : '',
      }
    }
    case 'history': return { title: t('header.history.title'), sub: t('header.history.sub') }
    default: return { title: t('header.wizard.title'), sub: t('header.wizard.sub') }
  }
})
</script>

<style scoped lang="scss">
.shell { display: flex; min-height: 100vh; background: var(--bg); color: var(--ink); }
.topbar { position: fixed; top: 0; left: 0; right: 0; height: 3px; z-index: 60; background: #e7e9ee; }
.topbar-fill { height: 100%; background: var(--ac); transition: width .35s ease; }

.sidebar { width: 240px; flex: none; background: #fff; border-right: 1px solid var(--border); display: flex; flex-direction: column; position: sticky; top: 0; height: 100vh; }
.brand { padding: 22px 20px 18px; display: flex; align-items: center; gap: 11px; border-bottom: 1px solid #f0f1f4; }
.brand-mark { width: 34px; height: 34px; border-radius: 9px; background: var(--ac); display: flex; align-items: center; justify-content: center; flex: none; }
.brand-ring { width: 13px; height: 13px; border: 2.5px solid #fff; border-radius: 50%; border-right-color: transparent; }
.brand-name { font-weight: 800; font-size: 16px; letter-spacing: -.01em; }
.brand-tag { font: 500 10px/1 var(--mono); color: #9aa0a8; letter-spacing: .04em; margin-top: 3px; }

.nav { padding: 14px 12px; display: flex; flex-direction: column; gap: 3px; }
.nav-btn { position: relative; display: flex; align-items: center; gap: 11px; width: 100%; text-align: left; padding: 10px 12px; border: none; background: transparent; border-radius: 8px; font: 600 14px var(--sans); color: var(--ink-2); cursor: pointer; }
.nav-btn:hover { background: #f5f6f8; }
.nav-btn.on::before { content: ''; position: absolute; left: 2px; top: 9px; bottom: 9px; width: 3px; border-radius: 2px; background: var(--ac); }
.nav-ico { width: 18px; height: 18px; border: 2px solid #9aa0a8; border-radius: 4px; flex: none; }
.nav-ico.lines { display: flex; flex-direction: column; justify-content: center; gap: 2px; padding: 3px; }
.nav-ico.lines::before, .nav-ico.lines::after { content: ''; height: 1.5px; background: #9aa0a8; border-radius: 1px; }

.side-foot { margin-top: auto; padding: 16px; }
.run-card { display: block; width: 100%; text-align: left; background: #f5f7fb; border: 1px solid #e7ebf3; border-radius: 10px; padding: 13px; cursor: pointer; transition: border-color .15s ease, background .15s ease; }
.run-card:hover { background: #eef3fc; border-color: #cfdcf5; }
.run-card:hover .rc-go { transform: translateX(2px); }
.rc-head { display: flex; align-items: center; gap: 7px; margin-bottom: 7px; }
.rc-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ac); animation: uxpulse 1.3s infinite; }
.rc-label { font: 600 11px/1 var(--mono); color: var(--ac); letter-spacing: .04em; }
.rc-go { margin-left: auto; color: var(--ac); font-weight: 700; transition: transform .15s ease; }
.rc-msg { font-size: 12.5px; color: var(--muted); line-height: 1.4; }
.rc-bar { height: 5px; background: #e2e7f0; border-radius: 3px; margin-top: 9px; overflow: hidden; }
.rc-fill { height: 100%; background: var(--ac); border-radius: 3px; }
.lang { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-top: 4px; }
.lang-btn { padding: 6px 14px; border: none; background: #fff; color: var(--muted-2); font: 600 12px var(--mono); cursor: pointer; }
.lang-btn:hover { background: #f5f6f8; }
.lang-btn.on { background: var(--ac); color: #fff; }
.version { font: 500 11px/1.5 var(--mono); color: var(--faint); margin-top: 14px; }

.main { flex: 1; min-width: 0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
.header { flex: none; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 20px 36px; border-bottom: 1px solid var(--border); background: rgba(247,248,250,.85); backdrop-filter: blur(8px); }
.h-title { font-size: 20px; font-weight: 700; letter-spacing: -.015em; }
.h-sub { font-size: 13px; color: var(--muted-2); margin-top: 3px; }
.h-actions { display: flex; gap: 10px; align-items: center; }
.h-actions .btn { text-decoration: none; }
.content { flex: 1; overflow-y: auto; }
</style>
