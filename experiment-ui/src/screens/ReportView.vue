<template>
  <div v-if="!report || !narrative" class="empty">
    {{ t('rep.empty') }}
  </div>

  <div v-else class="report">
    <!-- in-page section nav -->
    <nav class="toc">
      <button v-for="s in sections" :key="s.id" class="toc-link" :class="{ on: activeSection === s.id }" @click="scrollTo(s.id)">
        {{ s.label }}
      </button>
    </nav>

    <div class="report-body" ref="bodyRef">
      <!-- hero -->
      <section :id="anchor('summary')" class="sec">
        <div class="hero">
          <div class="hero-score">
            <div class="hero-num mono" :style="{ color: scoreColor(narrative.overall_ux_score || 0) }">
              {{ fmtScore(narrative.overall_ux_score) }}<span class="hero-of">/5</span>
            </div>
            <div class="hero-label mono">{{ t('rep.overallScore') }}</div>
          </div>
          <div class="hero-text">
            <div class="hero-title">{{ t('rep.execSummary') }}</div>
            <p class="prose">{{ narrative.executive_summary || '—' }}</p>
            <p v-if="narrative.score_rationale" class="prose muted-prose">{{ narrative.score_rationale }}</p>
          </div>
        </div>
      </section>

      <!-- strengths -->
      <section v-if="narrative.key_strengths?.length" :id="anchor('strengths')" class="sec">
        <h3 class="sec-title">{{ t('rep.strengths') }}</h3>
        <div class="strength-list">
          <div v-for="(s, i) in narrative.key_strengths" :key="i" class="strength">
            <span class="check">✓</span><span>{{ s }}</span>
          </div>
        </div>
      </section>

      <!-- issues -->
      <section :id="anchor('issues')" class="sec">
        <h3 class="sec-title">{{ t('rep.issuesBySeverity') }}</h3>
        <div class="issue-groups">
          <div v-for="g in issueGroups" :key="g.key" class="issue-group">
            <button class="group-head" @click="toggle(g.key)">
              <span class="group-dot" :style="{ background: g.color }"></span>
              <span class="group-name">{{ g.label }}</span>
              <span class="group-count mono">{{ g.items.length }}</span>
              <span class="caret" :class="{ open: open[g.key] }">▸</span>
            </button>
            <div v-if="open[g.key]" class="group-body">
              <div v-if="g.items.length === 0" class="muted sm pad">{{ t('rep.noIssues', { label: g.label.toLowerCase() }) }}</div>
              <div v-for="(it, i) in g.items" :key="i" class="issue" :style="{ borderLeftColor: g.color }">
                <div class="issue-head">
                  <span class="issue-h mono">H{{ it.heuristic_id }}</span>
                  <span class="issue-name">{{ it.heuristic_name }}</span>
                  <span class="issue-freq mono">{{ it.frequency }}</span>
                </div>
                <p class="issue-impact">{{ it.impact }}</p>
                <p class="issue-fix"><span class="fix-lead">{{ t('rep.recommendation') }}</span>{{ it.recommendation }}</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- assessments -->
      <section :id="anchor('assessments')" class="sec">
        <h3 class="sec-title">{{ t('rep.assessments') }}</h3>
        <div class="assess-grid">
          <div v-for="a in assessments" :key="a.label" class="assess-card">
            <div class="assess-label mono">{{ a.label }}</div>
            <p class="prose sm">{{ a.text || '—' }}</p>
          </div>
        </div>
      </section>

      <!-- priority recommendations -->
      <section v-if="narrative.priority_recommendations?.length" :id="anchor('recommendations')" class="sec">
        <h3 class="sec-title">{{ t('rep.recommendations') }}</h3>
        <div class="rec-list">
          <div v-for="(r, i) in narrative.priority_recommendations" :key="i" class="rec">
            <div class="rec-num mono">{{ r.priority ?? i + 1 }}</div>
            <div class="rec-body">
              <div class="rec-area">{{ r.area }}</div>
              <p class="rec-action">{{ r.action }}</p>
              <p v-if="r.expected_impact" class="rec-impact"><span class="impact-lead">{{ t('rep.expectedImpact') }}</span>{{ r.expected_impact }}</p>
            </div>
          </div>
        </div>
      </section>

      <!-- diagnostics -->
      <section :id="anchor('diagnostics')" class="sec">
        <h3 class="sec-title">{{ t('rep.diagnostics') }}</h3>
        <div class="diag-grid">
          <div v-for="d in diagnostics" :key="d.label" class="diag-card">
            <div class="diag-val mono" :style="d.color ? { color: d.color } : {}">{{ d.value }}</div>
            <div class="diag-label">{{ d.label }}</div>
          </div>
        </div>
      </section>

      <!-- methodology -->
      <section v-if="narrative.methodology_note" class="sec">
        <div class="methodology">
          <span class="ux-label">{{ t('rep.methodology') }}</span>
          <p class="prose sm muted-prose">{{ narrative.methodology_note }}</p>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { SEV, scoreColor, pageSpeedColor, sevLabel } from '../theme'
import { useI18n } from '../i18n'
import type { AggReport } from '../types'

const { t } = useI18n()
const props = defineProps<{ report: AggReport | null | undefined }>()

const report = computed(() => props.report)
const narrative = computed(() => report.value?.narrative)

const open = reactive<Record<string, boolean>>({ critical: true, serious: true, minor: false })
function toggle(k: string) { open[k] = !open[k] }

const issueGroups = computed(() => {
  const n = narrative.value
  return [
    { key: 'critical', label: sevLabel('critical'), color: SEV.critical.color, items: n?.critical_issues ?? [] },
    { key: 'serious', label: sevLabel('serious'), color: SEV.serious.color, items: n?.serious_issues ?? [] },
    { key: 'minor', label: sevLabel('minor'), color: SEV.minor.color, items: n?.minor_issues ?? [] },
  ]
})

const assessments = computed(() => {
  const n = narrative.value
  return [
    { label: t('rep.assess.fi'), text: n?.first_impression_assessment },
    { label: t('rep.assess.a11y'), text: n?.accessibility_assessment },
    { label: t('rep.assess.resp'), text: n?.responsiveness_assessment },
  ]
})

const diagnostics = computed(() => {
  const r = report.value
  const ps = r?.pagespeed
  const perf = ps && typeof ps === 'object'
    ? (ps.desktop?.performance_score ?? ps.mobile?.performance_score ?? ps.performance ?? null)
    : null
  const scroll = r?.scroll || {}
  const links = r?.links || {}
  const forms = r?.forms || {}
  const out: { label: string; value: string; color?: string }[] = []
  out.push({ label: t('rep.diag.pagespeed'), value: perf === null || perf === undefined ? 'n/a' : `${perf}/100`, color: perf != null ? pageSpeedColor(perf) : undefined })
  if (scroll.avg_max_depth_pct != null) out.push({ label: t('rep.diag.scroll'), value: `${scroll.avg_max_depth_pct}%` })
  if (links.total_links_checked != null) out.push({ label: t('rep.diag.links'), value: `${links.unique_broken_urls ?? 0} / ${links.total_links_checked}` })
  if (forms.total_sessions != null) out.push({ label: t('rep.diag.forms'), value: `${forms.sessions_engaged_form ?? 0} / ${forms.total_sessions}` })
  return out
})

const sections = computed(() => [
  { id: 'summary', label: t('rep.toc.summary') },
  { id: 'strengths', label: t('rep.toc.strengths') },
  { id: 'issues', label: t('rep.toc.issues') },
  { id: 'assessments', label: t('rep.toc.assessments') },
  { id: 'recommendations', label: t('rep.toc.recommendations') },
  { id: 'diagnostics', label: t('rep.toc.diagnostics') },
])
const activeSection = ref('summary')
const anchor = (id: string) => `report-sec-${id}`
function scrollTo(id: string) {
  activeSection.value = id
  document.getElementById(anchor(id))?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function fmtScore(v: number | undefined) { return typeof v === 'number' ? v.toFixed(1) : '—' }
</script>

<style scoped lang="scss">
.empty { color: var(--muted-2); font-size: 14px; padding: 40px 0; }
.report { display: grid; grid-template-columns: 170px 1fr; gap: 28px; align-items: start; }
.toc { position: sticky; top: 16px; display: flex; flex-direction: column; gap: 2px; }
.toc-link { text-align: left; background: transparent; border: none; padding: 8px 12px; border-radius: 7px; font: 600 13px var(--sans); color: var(--muted-2); cursor: pointer; }
.toc-link:hover { background: #f1f2f4; color: var(--ink-2); }
.toc-link.on { background: #eef3fc; color: var(--ac); }
.report-body { display: flex; flex-direction: column; gap: 26px; min-width: 0; }
.sec { scroll-margin-top: 16px; }
.sec-title { font-weight: 700; font-size: 16px; margin: 0 0 14px; }
.prose { font-size: 14px; line-height: 1.65; color: var(--ink-2); margin: 0 0 10px; }
.prose.sm { font-size: 13px; }
.muted-prose { color: var(--muted); }

.hero { display: flex; gap: 22px; background: #fff; border: 1px solid var(--border); border-radius: 13px; padding: 24px; align-items: flex-start; }
.hero-score { flex: none; text-align: center; padding: 6px 18px 0 0; border-right: 1px solid #f0f1f4; }
.hero-num { font: 800 46px var(--mono); letter-spacing: -.02em; }
.hero-of { font-size: 18px; color: var(--faint); font-weight: 500; }
.hero-label { font-size: 10px; color: var(--muted-2); letter-spacing: .05em; text-transform: uppercase; margin-top: 4px; }
.hero-text { min-width: 0; }
.hero-title { font-weight: 700; font-size: 15px; margin-bottom: 8px; }

.strength-list { display: flex; flex-direction: column; gap: 10px; }
.strength { display: flex; gap: 11px; align-items: flex-start; background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 13px 15px; font-size: 13.5px; color: var(--ink-2); line-height: 1.5; }
.check { color: #1f9d57; font-weight: 700; flex: none; }

.issue-groups { display: flex; flex-direction: column; gap: 10px; }
.issue-group { background: #fff; border: 1px solid var(--border); border-radius: 11px; overflow: hidden; }
.group-head { width: 100%; display: flex; align-items: center; gap: 11px; padding: 14px 16px; background: transparent; border: none; cursor: pointer; }
.group-dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
.group-name { font: 700 14px var(--sans); }
.group-count { font: 700 12px var(--mono); color: var(--muted-2); background: #f1f2f4; padding: 2px 8px; border-radius: 20px; }
.caret { margin-left: auto; color: var(--faint); transition: transform .15s ease; }
.caret.open { transform: rotate(90deg); }
.group-body { padding: 0 14px 14px; display: flex; flex-direction: column; gap: 10px; }
.pad { padding: 6px 2px; }
.issue { border: 1px solid var(--border-soft); border-left: 3px solid; border-radius: 9px; padding: 13px 15px; }
.issue-head { display: flex; align-items: center; gap: 10px; margin-bottom: 7px; }
.issue-h { font: 700 11px var(--mono); color: var(--ac); background: #eef3fc; padding: 3px 7px; border-radius: 5px; }
.issue-name { font: 600 13.5px var(--sans); }
.issue-freq { margin-left: auto; font-size: 11.5px; color: var(--muted-2); }
.issue-impact { font-size: 13px; color: var(--muted); line-height: 1.5; margin: 0 0 7px; }
.issue-fix { font-size: 13px; color: var(--ink-2); line-height: 1.5; margin: 0; }
.fix-lead, .impact-lead { color: var(--ac); font-weight: 600; }

.assess-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.assess-card { background: #fff; border: 1px solid var(--border); border-radius: 11px; padding: 16px; }
.assess-label { font: 600 10px var(--mono); letter-spacing: .05em; text-transform: uppercase; color: var(--muted-2); margin-bottom: 9px; }

.rec-list { display: flex; flex-direction: column; gap: 10px; }
.rec { display: flex; gap: 14px; background: #fff; border: 1px solid var(--border); border-radius: 11px; padding: 16px; align-items: flex-start; }
.rec-num { width: 30px; height: 30px; border-radius: 50%; background: var(--ac); color: #fff; display: flex; align-items: center; justify-content: center; font: 700 14px var(--mono); flex: none; }
.rec-area { font: 700 14px var(--sans); margin-bottom: 5px; }
.rec-action { font-size: 13.5px; color: var(--ink-2); line-height: 1.55; margin: 0 0 7px; }
.rec-impact { font-size: 12.5px; color: var(--muted); line-height: 1.5; margin: 0; }

.diag-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
.diag-card { background: #fff; border: 1px solid var(--border); border-radius: 11px; padding: 18px; }
.diag-val { font: 700 22px var(--mono); }
.diag-label { font-size: 12px; color: var(--muted-2); margin-top: 6px; }

.methodology { background: #fcfcfd; border: 1px solid var(--border-soft); border-radius: 10px; padding: 16px; }
.muted { color: var(--muted-2); } .muted.sm { font-size: 12.5px; }
</style>
