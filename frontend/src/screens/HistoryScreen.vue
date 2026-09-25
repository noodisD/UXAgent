<template>
  <div class="history">
    <div v-if="loading" class="state">{{ t('hist.loading') }}</div>
    <div v-else-if="error" class="state err">{{ t('hist.error', { msg: error }) }}</div>
    <div v-else-if="rows.length === 0" class="state">{{ t('hist.empty') }}</div>
    <div v-else class="table">
      <div class="thead mono">
        <div>{{ t('hist.col.site') }}</div><div>{{ t('hist.col.date') }}</div><div>{{ t('hist.col.personas') }}</div><div>{{ t('hist.col.outcomes') }}</div><div>{{ t('hist.col.critical') }}</div><div>{{ t('hist.col.score') }}</div>
      </div>
      <button v-for="r in rows" :key="r.id" class="trow" @click="open(r.id)">
        <div class="site">
          <div class="fav mono">{{ r.site.slice(0, 2).toUpperCase() }}</div>
          <div class="site-name-wrap">
            <div class="site-name">{{ r.site || '—' }}</div>
            <span v-if="r.current" class="latest mono">{{ t('hist.latest') }}</span>
          </div>
        </div>
        <div class="date mono">{{ r.date }}</div>
        <div class="personas mono">{{ r.personas }}</div>
        <div>
          <div v-if="r.status === 'complete'" class="dots">
            <span v-for="(d, i) in dots(r)" :key="i" class="dot" :style="{ background: d }"></span>
          </div>
          <span v-else class="failed" :class="{ neutral: r.status === 'cancelled' }">{{ statusLabel(r.status) }}</span>
        </div>
        <div><span v-if="r.status === 'complete'" class="crit mono" :style="{ color: r.critical > 0 ? '#d83a34' : '#1f9d57' }">{{ r.critical }}</span></div>
        <div>
          <template v-if="r.status === 'complete'">
            <span v-if="uxScore(r.score) !== null" class="score mono" :style="{ color: scoreColor(uxScore(r.score)!) }">{{ uxScore(r.score)!.toFixed(1) }}</span>
            <span v-else class="score none mono" :title="t('hist.noScore')">—</span>
          </template>
        </div>
      </button>
    </div>

    <!-- failed run modal -->
    <div v-if="failedRun" class="modal-veil" @click="failedRun = null">
      <div class="modal" @click.stop>
        <div class="fr-head">
          <div class="fr-title-wrap">
            <div class="fr-icon">!</div>
            <div>
              <div class="fr-title">{{ t('fr.title', { site: failedRun.site }) }}</div>
              <div class="fr-sub mono">{{ failedRun.date }}<template v-if="failedRun.phase"> · {{ t('fr.during') }}: {{ failedRun.phase }}</template></div>
            </div>
          </div>
          <button class="fr-close" @click="failedRun = null">×</button>
        </div>
        <div v-if="failedRun.failedAt" class="fr-point">
          <div class="fr-label mono">{{ t('fr.point') }}</div>
          <div class="fr-point-text mono">{{ failedRun.failedAt }}</div>
        </div>
        <div v-if="failedRun.error" class="fr-error">{{ failedRun.error }}</div>
        <div v-if="failedRun.suggestion" class="fr-suggestion">
          <span class="fr-label mono warn">{{ t('fr.suggestion') }}</span>
          <div class="fr-sugg-text">{{ failedRun.suggestion }}</div>
        </div>
        <div class="fr-actions">
          <button class="btn" @click="failedRun = null">{{ t('fr.close') }}</button>
          <button class="btn btn-primary" @click="retryRun">{{ t('fr.retry') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useHistory } from '../composables/useHistory'
import { useApp } from '../composables/useApp'
// uxScore: the row's score is the report's own (results_api._report_score) and
// null when the run has none — it used to print a red 0.0 for those.
import { scoreColor, uxScore } from '../theme'
import { useI18n } from '../i18n'
import type { RunHistoryRow } from '../types'

const { rows, loading, error, load } = useHistory()
const { openResults, newAudit } = useApp()
const { t } = useI18n()

type FailedRunInfo = { site: string; date: string; phase?: string; failedAt?: string; error?: string; suggestion?: string }
const failedRun = ref<FailedRunInfo | null>(null)

onMounted(load)

// A run the user cancelled is not a failure. The backend passes "cancelled"
// through unchanged (results_api maps only done -> complete), but the template
// treated everything that was not complete/running as failed — so cancelling
// your own run reported "Analiza nieudana" and opened the failure modal with
// "no details", because manifest.failure is only written on the error path.
function statusLabel(status: string): string {
  if (status === 'running') return t('hist.running')
  if (status === 'cancelled') return t('hist.cancelled')
  return t('hist.failed')
}

function open(id: string) {
  const r = rows.value.find(x => x.id === id) as any
  if (r && r.status === 'cancelled') return   // nothing to show, and not an error
  if (r && r.status !== 'complete' && r.status !== 'running') {
    // Failed run: show diagnostics instead of empty results.
    failedRun.value = {
      site: r.site, date: r.date,
      phase: r.failure?.phase, failedAt: r.failure?.failedAt,
      error: r.failure?.error || t('fr.noDetails'), suggestion: r.failure?.suggestion,
    }
    return
  }
  openResults(id)
}

function retryRun() { failedRun.value = null; newAudit() }

function dots(r: RunHistoryRow): string[] {
  const out: string[] = []
  for (let i = 0; i < r.success; i++) out.push('#1f9d57')
  for (let i = 0; i < r.partial; i++) out.push('#cf8420')
  for (let i = 0; i < r.failed; i++) out.push('#d83a34')
  return out
}
</script>

<style scoped lang="scss">
.history { width: 100%; padding: 28px 36px 60px; }
.state { padding: 60px 0; color: var(--muted-2); }
.state.err { color: #b5564f; }
.table { background: #fff; border: 1px solid var(--border); border-radius: 13px; overflow: hidden; }
.thead, .trow { display: grid; grid-template-columns: 1.4fr 1fr .8fr 1.3fr .8fr .7fr; gap: 14px; align-items: center; }
.thead { padding: 14px 22px; border-bottom: 1px solid #eef0f3; background: #fcfcfd; font: 600 10px/1 var(--mono); letter-spacing: .06em; text-transform: uppercase; color: #9aa0a8; }
.trow { width: 100%; text-align: left; padding: 16px 22px; border: none; border-bottom: 1px solid #f3f4f6; background: transparent; cursor: pointer; }
.trow:hover { background: #fafbfc; }
.site { display: flex; align-items: center; gap: 10px; min-width: 0; }
.fav { width: 30px; height: 30px; border-radius: 7px; background: #eef1f6; display: flex; align-items: center; justify-content: center; font: 700 11px var(--mono); color: var(--muted); flex: none; }
.site-name { font: 600 14px var(--sans); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.latest { font-size: 9.5px; color: var(--ac); }
.date { font: 500 13px var(--mono); color: var(--muted); }
.personas { font: 600 14px var(--mono); }
.dots { display: flex; gap: 5px; align-items: center; }
.dot { width: 8px; height: 8px; border-radius: 9px; display: inline-block; }
.failed { font: 600 12px var(--sans); color: #b5564f; }
/* A cancelled run is a user decision, not an error — do not colour it as one. */
.failed.neutral { color: var(--muted-2); }
.crit { font: 700 13px var(--mono); }
.score { font: 700 15px var(--mono); }
.score.none { color: var(--faint); cursor: help; }

/* failed run modal */
.modal-veil { position: fixed; inset: 0; background: rgba(0,0,0,.55); z-index: 80; display: flex; align-items: center; justify-content: center; padding: 24px; }
.modal { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; max-width: 560px; width: 100%; padding: 28px; box-shadow: 0 24px 64px rgba(0,0,0,.3); }
.fr-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 6px; }
.fr-title-wrap { display: flex; align-items: center; gap: 12px; }
.fr-icon { width: 40px; height: 40px; border-radius: 10px; background: var(--accent-tint); display: flex; align-items: center; justify-content: center; font: 700 18px var(--sans); color: var(--ac); flex: none; }
.fr-title { font-weight: 700; font-size: 17px; }
.fr-sub { font: 500 12px var(--mono); color: var(--text-muted); margin-top: 3px; }
.fr-close { width: 30px; height: 30px; border: 1px solid var(--border); background: var(--surface); border-radius: 8px; color: var(--text-muted); cursor: pointer; font-size: 15px; flex: none; }
.fr-close:hover { border-color: var(--ac); color: var(--ac); }
.fr-label { font: 600 10px/1 var(--mono); letter-spacing: .06em; text-transform: uppercase; color: var(--text-faint); }
.fr-label.warn { color: var(--warn-text); }
.fr-point { background: var(--surface-2); border: 1px solid var(--border-2); border-radius: 9px; padding: 13px 15px; margin: 16px 0; }
.fr-point-text { font: 500 13px var(--mono); color: var(--text-2); margin-top: 7px; }
.fr-error { font-size: 13.5px; color: var(--text-3); line-height: 1.55; margin-bottom: 14px; }
.fr-suggestion { background: var(--warn-bg); border: 1px solid var(--warn-border); border-radius: 9px; padding: 12px 15px; margin-bottom: 20px; }
.fr-sugg-text { font-size: 13px; color: var(--text-2); margin-top: 5px; line-height: 1.5; }
.fr-actions { display: flex; gap: 10px; justify-content: flex-end; }
</style>
