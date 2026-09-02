<template>
  <!-- Blank while probing so the gate doesn't flash for an already-authorised
       (or unauthenticated, i.e. local) backend. -->
  <div v-if="authState === 'checking'" class="boot"></div>

  <AuthGate v-else-if="authState === 'locked'" @unlocked="authState = 'open'" />

  <div v-else class="shell">
    <!-- top progress bar (active run) -->
    <div v-if="runActive" class="topbar"><div class="topbar-fill" :style="{ width: topPct + '%' }"></div></div>

    <!-- SIDEBAR -->
    <aside class="sidebar" :class="{ collapsed }">
      <div class="brand" :class="{ collapsed }">
        <div class="brand-mark"><div class="brand-slash"></div></div>
        <template v-if="!collapsed">
          <div class="brand-text">
            <div class="brand-name">UXAgent</div>
            <div class="brand-tag mono">{{ t('brand.tag') }}</div>
          </div>
          <button class="side-toggle" :title="t('side.collapse')" @click="collapsed = true">«</button>
        </template>
      </div>
      <button v-if="collapsed" class="side-toggle expand" :title="t('side.expand')" @click="collapsed = false">»</button>

      <nav class="nav" :class="{ collapsed }">
        <button class="nav-btn" :class="{ on: app.screen === 'wizard' || app.screen === 'running', collapsed }" :title="t('nav.newAudit')" @click="newAudit">
          <span class="nav-ico box"></span><span v-if="!collapsed">{{ t('nav.newAudit') }}</span>
        </button>
        <button class="nav-btn" :class="{ on: app.screen === 'history', collapsed }" :title="t('nav.history')" @click="goScreen('history')">
          <span class="nav-ico lines"></span><span v-if="!collapsed">{{ t('nav.history') }}</span>
        </button>
      </nav>

      <div v-if="!collapsed" class="side-foot">
        <button v-if="runActive" class="run-card" @click="goScreen('running')" :title="t('run.return')">
          <div class="rc-head"><span class="rc-dot"></span><span class="rc-label mono">{{ t('run.active') }}</span><span class="rc-go">→</span></div>
          <div class="rc-msg">{{ runLabel }}</div>
          <div class="rc-bar"><div class="rc-fill" :style="{ width: topPct + '%' }"></div></div>
        </button>
        <div class="lang">
          <button class="lang-btn" :class="{ on: locale === 'pl' }" @click="setLocale('pl')">PL</button>
          <button class="lang-btn" :class="{ on: locale === 'en' }" @click="setLocale('en')">EN</button>
        </div>
        <div class="version mono">v2.4 · {{ t('version.connected') }}<br>{{ apiOrigin() }}</div>
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
            <a class="btn" :href="reportPdfUrl">{{ t('btn.download') }}</a>
            <a class="btn btn-primary" :href="reportUrl" target="_blank" rel="noopener">{{ t('btn.openReport') }} ↗</a>
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
import { computed, onMounted, provide, ref, watch, watchEffect } from 'vue'
import { apiAssetUrl, apiOrigin, getKey, onUnauthorized, verifyKey } from './api'
import AuthGate from './components/AuthGate.vue'
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

const form = createWizardForm()
provide(FORM_KEY, form)

const { state: app, goScreen, newAudit } = useApp()
const { data: results } = useResults()
const { t, locale, setLocale } = useI18n()

// --- sidebar -----------------------------------------------------------
const collapsed = ref(localStorage.getItem('uxagent_sidebar') === 'collapsed')
watchEffect(() => { try { localStorage.setItem('uxagent_sidebar', collapsed.value ? 'collapsed' : 'expanded') } catch {} })

// Lightweight progress read for the sidebar/topbar (RunningScreen drives its own polling).
// Started only once auth resolves — see the watch below the gate.
const { data: progress, start, stop } = useProgress(1500)

const snap = computed(() => progress.value as any)
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

// Two containers for one report: the HTML is for reading in a tab, the PDF is
// what people send on. Chromium renders the PDF from this same HTML server-side
// (backend main/report_pdf.py), so they cannot drift apart.
const reportUrl = computed(() =>
  app.viewRunId ? apiAssetUrl(`/runs/${encodeURIComponent(app.viewRunId)}/report.html`) : '#')
const reportPdfUrl = computed(() =>
  app.viewRunId ? apiAssetUrl(`/runs/${encodeURIComponent(app.viewRunId)}/report.pdf`) : '#')

// --- auth gate ----------------------------------------------------------
// The backend decides whether a key is required: probing /progress returns 401
// only when UXAGENT_API_KEY is configured. So local development, where it is
// unset, never sees the gate — no build flag needed to tell the two apart.
const authState = ref<'checking' | 'locked' | 'open'>('checking')

onMounted(async () => {
  authState.value = (await verifyKey(getKey())) ? 'open' : 'locked'
})

// Poll only while unlocked. This used to start at setup, so on a key-protected
// backend every poll 401'd while authState was still 'checking' or 'locked' —
// and api.ts clears the stored key and fires the unauthorized listeners on each
// 401, producing a key-wipe storm at 1.5s intervals that raced the verifyKey
// probe above. It also kept polling forever, since onUnmounted in App's own
// scope never fires.
watch(authState, (s) => (s === 'open' ? start() : stop()), { immediate: true })

// Any 401 mid-session (key revoked or rotated) drops straight back to the gate
// rather than leaving a dead screen full of failed requests.
onUnauthorized(() => { authState.value = 'locked' })

const header = computed(() => {
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
.shell { display: flex; min-height: 100vh; background: var(--bg); color: var(--text); }
.boot { min-height: 100vh; background: var(--bg); }
.topbar { position: fixed; top: 0; left: 0; right: 0; height: 3px; z-index: 60; background: var(--border); }
.topbar-fill { height: 100%; background: var(--ac); transition: width .35s ease; }

.sidebar { width: 240px; flex: none; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; position: sticky; top: 0; height: 100vh; transition: width .2s ease; }
.sidebar.collapsed { width: 64px; }

.brand { padding: 22px 20px 18px; display: flex; align-items: center; gap: 11px; border-bottom: 1px solid var(--border-2); }
.brand.collapsed { padding: 18px 0; justify-content: center; }
.brand-mark { width: 34px; height: 34px; border-radius: 9px; background: #111318; display: flex; align-items: center; justify-content: center; flex: none; overflow: hidden; position: relative; }
.brand-slash { position: absolute; width: 170%; height: 8px; background: var(--ac); transform: rotate(-25deg); }
.brand-text { flex: 1; min-width: 0; }
.brand-name { font-family: var(--expanded); font-weight: 700; font-size: 16px; letter-spacing: .02em; text-transform: uppercase; }
.brand-tag { font: 500 10px/1 var(--mono); color: var(--text-muted); letter-spacing: .04em; margin-top: 3px; }
.side-toggle { width: 26px; height: 26px; flex: none; border: 1px solid var(--border); background: var(--surface); border-radius: 7px; color: var(--text-muted); cursor: pointer; font: 600 13px var(--mono); display: flex; align-items: center; justify-content: center; }
.side-toggle:hover { border-color: var(--ac); color: var(--ac); }
.side-toggle.expand { margin: 12px auto 0; width: 30px; height: 30px; }

.nav { padding: 14px 12px; display: flex; flex-direction: column; gap: 3px; }
.nav.collapsed { padding: 14px 8px; }
.nav-btn { position: relative; display: flex; align-items: center; gap: 11px; width: 100%; text-align: left; padding: 10px 12px; border: none; background: transparent; border-radius: 8px; font: 600 14px var(--sans); color: var(--text-2); cursor: pointer; }
.nav-btn.collapsed { justify-content: center; padding: 10px 0; }
.nav-btn:hover { background: var(--surface-3); }
.nav-btn.on::before { content: ''; position: absolute; left: 2px; top: 9px; bottom: 9px; width: 3px; border-radius: 2px; background: var(--ac); }
.nav-ico { width: 18px; height: 18px; border: 2px solid var(--text-muted); border-radius: 4px; flex: none; }
.nav-ico.lines { display: flex; flex-direction: column; justify-content: center; gap: 2px; padding: 3px; }
.nav-ico.lines::before, .nav-ico.lines::after { content: ''; height: 1.5px; background: var(--text-muted); border-radius: 1px; }

.side-foot { margin-top: auto; padding: 16px; display: flex; flex-direction: column; gap: 14px; }

.run-card { display: block; width: 100%; text-align: left; background: var(--accent-tint); border: 1px solid var(--accent-tint-border); border-radius: 10px; padding: 13px; cursor: pointer; transition: filter .15s ease; }
.run-card:hover { filter: brightness(.98); }
.run-card:hover .rc-go { transform: translateX(2px); }
.rc-head { display: flex; align-items: center; gap: 7px; margin-bottom: 7px; }
.rc-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ac); animation: uxpulse 1.3s infinite; }
.rc-label { font: 600 11px/1 var(--mono); color: var(--ac); letter-spacing: .04em; }
.rc-go { margin-left: auto; color: var(--ac); font-weight: 700; transition: transform .15s ease; }
.rc-msg { font-size: 12.5px; color: var(--text-2); line-height: 1.4; }
.rc-bar { height: 5px; background: rgba(255,255,255,.5); border-radius: 3px; margin-top: 9px; overflow: hidden; }
.rc-fill { height: 100%; background: var(--ac); border-radius: 3px; }

.lang { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; align-self: flex-start; }
.lang-btn { padding: 6px 14px; border: none; background: var(--surface); color: var(--text-muted); font: 600 12px var(--mono); cursor: pointer; }
.lang-btn:hover { background: var(--surface-3); }
.lang-btn.on { background: var(--ac); color: #fff; }
.version { font: 500 11px/1.5 var(--mono); color: var(--text-faint); }

.main { flex: 1; min-width: 0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
.header { flex: none; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 20px 36px; border-bottom: 1px solid var(--border); background: var(--bg-blur); backdrop-filter: blur(8px); }
.h-title { font-size: 20px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; font-family: var(--expanded); }
.h-sub { font-size: 13px; color: var(--text-muted); margin-top: 3px; }
.h-actions { display: flex; gap: 10px; align-items: center; }
.h-actions .btn { text-decoration: none; }
.content { flex: 1; overflow-y: auto; }
</style>
