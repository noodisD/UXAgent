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
          <span v-else class="failed">{{ r.status === 'running' ? t('hist.running') : t('hist.failed') }}</span>
        </div>
        <div><span v-if="r.status === 'complete'" class="crit mono" :style="{ color: r.critical > 0 ? '#d83a34' : '#1f9d57' }">{{ r.critical }}</span></div>
        <div><span v-if="r.status === 'complete'" class="score mono" :style="{ color: scoreColor(r.score) }">{{ r.score.toFixed(1) }}</span></div>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useHistory } from '../composables/useHistory'
import { useApp } from '../composables/useApp'
import { scoreColor } from '../theme'
import { useI18n } from '../i18n'
import type { RunHistoryRow } from '../types'

const { rows, loading, error, load } = useHistory()
const { openResults } = useApp()
const { t } = useI18n()

onMounted(load)

function open(id: string) { openResults(id) }

function dots(r: RunHistoryRow): string[] {
  const out: string[] = []
  for (let i = 0; i < r.success; i++) out.push('#1f9d57')
  for (let i = 0; i < r.partial; i++) out.push('#cf8420')
  for (let i = 0; i < r.failed; i++) out.push('#d83a34')
  return out
}
</script>

<style scoped lang="scss">
.history { max-width: 1000px; margin: 0 auto; padding: 28px 36px 60px; }
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
.crit { font: 700 13px var(--mono); }
.score { font: 700 15px var(--mono); }
</style>
