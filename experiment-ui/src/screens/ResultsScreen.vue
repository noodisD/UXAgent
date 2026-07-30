<template>
  <div class="results">
    <div v-if="loading" class="state">{{ t('results.loading') }}</div>
    <div v-else-if="error" class="state err">{{ t('results.error', { msg: error }) }}</div>
    <template v-else-if="data">
      <!-- tabs -->
      <div class="tabs">
        <button v-for="t in tabs" :key="t.key" class="tab" :class="{ on: tab === t.key }" @click="tab = t.key">
          {{ t.label }}
          <span v-if="t.count !== null" class="tab-count mono" :class="{ on: tab === t.key }">{{ t.count }}</span>
        </button>
      </div>

      <div class="tab-body">
        <!-- REPORT -->
        <ReportView v-if="tab === 'report'" :report="data.report" :meta="data.meta" :persona-tasks="data.personaTasks" />

        <!-- OVERVIEW -->
        <div v-else-if="tab === 'overview'" class="col">
          <div class="verdict">
            <div class="verdict-icon">⚠</div>
            <div>
              <div class="verdict-title">{{ verdict }}</div>
              <div class="verdict-body">{{ verdictBody }}</div>
            </div>
          </div>

          <!-- vs previous run -->
          <div v-if="comparison" class="ux-card">
            <div class="cmp-head">{{ t('cmp.title') }} <span class="cmp-date mono">· {{ comparison.vsDate }}</span></div>
            <div class="cmp-metrics">
              <div v-for="m in comparison.metrics" :key="m.label" class="cmp-metric">
                <div class="cmp-label mono">{{ m.label }}</div>
                <div class="cmp-vals">
                  <span class="cmp-now mono">{{ m.now }}</span>
                  <span class="cmp-prev mono" :style="{ color: m.trend === 'up' || m.trend === 'better' ? 'var(--success-text)' : 'var(--ac)' }">
                    {{ m.trend === 'up' || m.trend === 'better' ? '▲' : '▼' }} {{ t('cmp.from') }} {{ m.prev }}
                  </span>
                </div>
              </div>
            </div>
            <div class="cmp-cols">
              <div>
                <div class="cmp-col-h mono" style="color: var(--ac)">{{ t('cmp.new') }} ({{ comparison.fresh.length }})</div>
                <div v-for="(x, i) in comparison.fresh" :key="i" class="cmp-item"><span style="color: var(--ac)">+</span>{{ x }}</div>
              </div>
              <div>
                <div class="cmp-col-h mono" style="color: var(--warn-text)">{{ t('cmp.persisting') }} ({{ comparison.persisting.length }})</div>
                <div v-for="(x, i) in comparison.persisting" :key="i" class="cmp-item"><span style="color: var(--warn-text)">=</span>{{ x }}</div>
              </div>
              <div>
                <div class="cmp-col-h mono" style="color: var(--success-text)">{{ t('cmp.resolved') }} ({{ comparison.resolved.length }})</div>
                <div v-for="(x, i) in comparison.resolved" :key="i" class="cmp-item"><span style="color: var(--success-text)">✓</span>{{ x }}</div>
              </div>
            </div>
          </div>

          <div class="score-cards">
            <div v-for="s in scoreCards" :key="s.label" class="score-card">
              <div class="score-label mono">{{ s.label }}</div>
              <div class="score-val mono" :style="{ color: s.color }">{{ s.value }}<span class="unit">{{ s.unit }}</span></div>
              <div class="score-sub">{{ s.sub }}</div>
            </div>
          </div>

          <div class="grid-13">
            <div class="ux-card r13">
              <div class="ct">{{ t('ov.sevTitle') }}</div>
              <div class="col gap14">
                <div v-for="b in sevBars" :key="b.label">
                  <div class="sev-head"><span :style="{ color: b.color }" class="sev-name">{{ b.label }}
                    <span class="sev-pl mono">/ {{ b.pl }}</span></span><span class="sev-count mono">{{ b.count }}</span></div>
                  <div class="sev-track"><div class="sev-fill" :style="{ background: b.color, width: b.pct + '%' }"></div></div>
                </div>
                <div class="ps-row">
                  <span class="ps-label">{{ t('ov.pagespeed') }}</span>
                  <span v-if="psPerf !== null" class="ps-val mono" :style="{ color: psColor }">{{ psPerf }}<span class="ps-unit">/100</span></span>
                  <span v-else class="ps-na mono">n/a</span>
                </div>
              </div>
            </div>

            <div class="ux-card">
              <div class="ct">{{ t('ov.topFixes') }}</div>
              <div class="col gap12">
                <div v-for="(fx, i) in topFixes" :key="i" class="fix-row">
                  <span class="fix-tag mono" :style="{ background: fx.chipBg, color: fx.chipFg }">{{ fx.label }}</span>
                  <span class="fix-text">{{ fx.text }}</span>
                </div>
                <div v-if="topFixes.length === 0" class="muted">{{ t('ov.noFindings') }}</div>
              </div>
            </div>
          </div>

          <div class="ux-card">
            <div class="row-between ctrow">
              <div class="ct nomb">{{ t('ov.perPersona') }}</div>
              <button class="link" @click="tab = 'personas'">{{ t('ov.viewAll') }}</button>
            </div>
            <div class="persona-strip">
              <button v-for="(p, i) in personas" :key="p.id" class="strip-card" @click="openPersona(i)">
                <div class="strip-head">
                  <div class="strip-avatar mono">{{ initials(p.name) }}</div>
                  <div><div class="strip-name">{{ p.name }}</div><div class="strip-meta mono">{{ p.metaParts.join(' · ') }}</div></div>
                </div>
                <div class="outcome-pill" :style="{ background: oc(p.outcome).bg, color: oc(p.outcome).color }">
                  <span class="pdot"></span>{{ outcomeLabel(outcomeOf(p.outcome)) }}</div>
                <div class="strip-sum">{{ t('ov.personaSummary', { crit: critCount(p), total: p.heuristics.length }) }}</div>
              </button>
            </div>
          </div>
        </div>

        <!-- PERSONAS -->
        <div v-else-if="tab === 'personas'" class="personas-layout" :class="{ 'list-hidden': plistHidden }">
          <div v-if="!plistHidden" class="persona-list">
            <div class="plist-head">
              <span class="plist-title mono">{{ t('pd.personasList') }}</span>
              <button class="plist-toggle" :title="t('pd.hideList')" @click="plistHidden = true">«</button>
            </div>
            <button v-for="(p, i) in personas" :key="p.id" class="plist-item" :class="{ sel: i === selPersona }" @click="selPersona = i">
              <div class="plist-avatar mono" :class="{ sel: i === selPersona }">{{ initials(p.name) }}</div>
              <div class="plist-info"><div class="plist-name">{{ p.name }}</div>
                <div class="plist-outcome" :style="{ color: oc(p.outcome).color }">{{ outcomeLabel(outcomeOf(p.outcome)) }}</div></div>
            </button>
          </div>

          <div v-if="detail" class="persona-detail">
            <button v-if="plistHidden" class="plist-show" @click="plistHidden = false">» {{ t('pd.showList') }}</button>
            <div class="ux-card">
              <div class="detail-head">
                <div class="dh-left">
                  <div class="dh-avatar mono">{{ initials(detail.name) }}</div>
                  <div><div class="dh-name">{{ detail.name }}</div>
                    <div class="dh-meta mono">{{ detail.metaParts.join(' · ') }}<span v-if="detail.steps"> · {{ detail.steps }} {{ t('pd.steps') }}</span><span v-if="detail.duration"> · {{ detail.duration }}</span></div></div>
                </div>
                <div class="dh-outcome">
                  <div class="outcome-pill big" :style="{ background: oc(detail.outcome).bg, color: oc(detail.outcome).color }"><span class="pdot"></span>{{ outcomeLabel(outcomeOf(detail.outcome)) }}</div>
                  <div class="dh-outcome-tags">
                    <span
                      class="src-chip mono"
                      :class="{ measured: detail.outcomeSource === 'observed' }"
                      :title="detail.outcomeSource === 'observed' ? t('rep.pt.measuredHint') : t('rep.pt.declaredHint')"
                    >{{ detail.outcomeSource === 'observed' ? t('rep.pt.measured') : t('rep.pt.declared') }}</span>
                    <span
                      v-if="detail.outcomeDisagreement"
                      class="src-chip mono disagree"
                      :title="t('rep.pt.disagreeHint') + (detail.declaredOutcome ? ' → ' + outcomeLabel(outcomeOf(detail.declaredOutcome)) : '')"
                    >{{ t('rep.pt.disagree') }}</span>
                  </div>
                </div>
              </div>
              <div class="objective-box">
                <span class="ux-label">{{ t('pd.objective') }}</span>
                <div class="obj-text">{{ detail.objective }}</div>
                <div v-if="detail.homeLocation" class="home-loc">
                  <span class="home-ico">⌂</span>
                  <span class="home-label">{{ t('pd.homeLocation') }}:</span>
                  <span class="home-val">{{ detail.homeLocation }}</span>
                </div>
              </div>
            </div>

            <div class="ux-card">
              <div class="ct">{{ t('pd.reflection') }}</div>
              <div class="refl-summary">{{ detail.reflection.summary || '—' }}</div>
              <div class="grid2 mb16">
                <div>
                  <div class="refl-label red">{{ t('pd.blockers') }}</div>
                  <div class="col gap8">
                    <div v-for="(b, i) in detail.reflection.blockers" :key="i" class="bullet"><span class="b-red">▸</span>{{ b }}</div>
                    <div v-if="!detail.reflection.blockers.length" class="muted sm">{{ t('pd.none') }}</div>
                  </div>
                </div>
                <div>
                  <div class="refl-label green">{{ t('pd.wins') }}</div>
                  <div class="col gap8">
                    <div v-for="(w, i) in detail.reflection.wins" :key="i" class="bullet"><span class="b-green">▸</span>{{ w }}</div>
                    <div v-if="!detail.reflection.wins.length" class="muted sm">{{ t('pd.none') }}</div>
                  </div>
                </div>
              </div>
              <div v-if="detail.reflection.priorityFix" class="priority-fix">
                <span class="ux-label pf-label">{{ t('pd.priorityFix') }}</span>
                <div class="pf-text">{{ detail.reflection.priorityFix }}</div>
              </div>
            </div>

            <!-- ISO 9241-11: effectiveness / efficiency / satisfaction (§1.1) -->
            <div v-if="detail.iso" class="ux-card">
              <div class="ct">{{ t('pd.iso') }}</div>
              <div class="iso-grid">
                <div class="iso-col">
                  <div class="iso-label mono">{{ t('pd.iso.effectiveness') }}</div>
                  <div class="iso-main" :style="{ color: oc(detail.outcome).color }">
                    {{ t('iso.success.' + detail.iso.effectiveness.task_success) }}
                  </div>
                  <div class="iso-sub">{{ t('pd.iso.endedReason') }}: {{ t('iso.ended.' + (detail.iso.effectiveness.ended_reason || 'unknown')) }}</div>
                  <div v-if="detail.iso.effectiveness.critical_error" class="iso-warn">{{ t('pd.iso.criticalError') }}</div>
                </div>
                <div class="iso-col">
                  <div class="iso-label mono">{{ t('pd.iso.efficiency') }}</div>
                  <div class="iso-main mono">{{ detail.iso.efficiency.step_count ?? '—' }} <span class="iso-unit">{{ t('pd.steps') }}</span></div>
                  <div class="iso-sub">{{ t('pd.iso.backtracks') }}: {{ detail.iso.efficiency.backtracks }}</div>
                  <div v-if="detail.iso.efficiency.loop_detected" class="iso-warn">{{ t('pd.iso.loopDetected') }}</div>
                </div>
                <div class="iso-col">
                  <div class="iso-label mono">{{ t('pd.iso.satisfaction') }}</div>
                  <div class="iso-main mono">
                    {{ detail.iso.satisfaction.synthetic_seq_1_7 ?? detail.iso.satisfaction.synthetic_score_1_5 ?? '—' }}
                    <span class="iso-unit">{{ detail.iso.satisfaction.synthetic_seq_1_7 ? 'SEQ /7' : '/5' }}</span>
                  </div>
                  <div class="iso-sub">{{ t('pd.iso.frustration') }}: {{ detail.iso.satisfaction.frustration_signals }}</div>
                  <div class="iso-synth">{{ t('pd.iso.synthetic') }}</div>
                </div>
              </div>
            </div>

            <!-- bias audit (§11.4) -->
            <div v-if="detail.biasAudit && detail.biasAudit.flags && detail.biasAudit.flags.length" class="ux-card bias-card">
              <div class="ct">{{ t('pd.bias') }}</div>
              <div class="bias-summary">{{ detail.biasAudit.summary }}</div>
              <div class="col gap8 mt14">
                <div v-for="(f, i) in detail.biasAudit.flags" :key="i" class="bias-flag">
                  <span class="bias-type mono">{{ f.issue_type }}</span>
                  <span class="bias-expl">{{ f.explanation }}</span>
                </div>
              </div>
            </div>

            <div class="grid2">
              <div class="ux-card">
                <div class="ct">{{ t('pd.firstImpression') }}</div>
                <div class="col gap11">
                  <div v-for="f in fiRows" :key="f.label" class="fi-row">
                    <div class="fi-label">{{ f.label }}</div>
                    <div class="fi-track"><div class="fi-fill" :style="{ width: f.pct + '%' }"></div></div>
                    <div class="fi-score mono">{{ f.score ?? '—' }}/5</div>
                  </div>
                </div>
              </div>
              <div class="ux-card">
                <div class="row-between"><div class="ct nomb">{{ t('pd.accessibility') }}</div>
                  <div class="a11y-score mono" :style="{ color: scoreColor(detail.accessibility.score || 0) }">{{ detail.accessibility.score ?? '—' }}<span class="of5">/5</span></div></div>
                <div class="col gap8 mt14">
                  <div v-for="(a, i) in detail.accessibility.findings" :key="i" class="bullet sm">
                    <span class="b-grey">·</span>{{ typeof a === 'string' ? a : a.detail }}
                    <template v-if="typeof a !== 'string'">
                      <span v-if="!a.verified" class="unverified-badge">{{ t('ev.unverified') }}</span>
                      <a v-else-if="a.url" class="ev-url sm" :href="a.url" target="_blank">{{ t('ev.page') }}</a>
                    </template>
                  </div>
                </div>
              </div>
            </div>

            <div class="ux-card">
              <div class="ct">{{ t('pd.heuristics', { n: detail.heuristics.length }) }}</div>
              <div class="col gap11">
                <div v-for="(h, i) in detail.heuristics" :key="i" class="heur" :style="{ borderLeftColor: SEV[h.severity].color }">
                  <div class="heur-head">
                    <span class="heur-tag mono" :style="{ background: SEV[h.severity].chipBg, color: SEV[h.severity].chipFg }">{{ sevLabel(h.severity) }}</span>
                    <span v-if="h.severityScore !== undefined" class="sev-score mono">{{ h.severityScore }}</span>
                    <span class="heur-name">H{{ h.id }} · {{ heuristicName(h.id) }}</span>
                    <span v-if="exclusionOf(h)" class="verify-chip mono" :title="t('excl.' + exclusionOf(h) + '.hint')">{{ t('excl.' + exclusionOf(h)) }}</span>
                    <span v-else-if="h.confidence" class="conf-chip mono" :class="h.confidence" :title="h.confidenceCappedBy ? t('conf.capped.' + h.confidenceCappedBy) : undefined">{{ t('conf.' + h.confidence) }}</span>
                    <span v-if="!h.verified" class="unverified-badge">{{ t('ev.unverified') }}</span>
                  </div>
                  <div class="heur-ev">{{ h.evidence }}</div>
                  <div class="heur-fix"><span class="fix-lead">{{ t('pd.fix') }}</span>{{ h.recommendation }}</div>
                  <div v-if="h.verified" class="ev-block">
                    <a v-if="h.hasScreenshot" :href="shotUrl(detail.id, h.step)" target="_blank">
                      <img class="ev-thumb" :src="shotUrl(detail.id, h.step)" :alt="t('ev.step', { n: h.step })" />
                    </a>
                    <div class="ev-meta">
                      <span class="step-chip mono">{{ t('ev.step', { n: h.step }) }}</span>
                      <a class="ev-url" :href="h.url" target="_blank">{{ h.url }}</a>
                      <div v-if="h.observationQuote" class="ev-quote">„{{ h.observationQuote }}"</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="ux-card">
              <div class="ct">{{ t('pd.survey') }}</div>
              <div class="col gap14">
                <div v-for="(s, i) in detail.survey" :key="i" class="psurvey-item">
                  <div class="psurvey-head">
                    <span class="psurvey-num mono">{{ String(i + 1).padStart(2, '0') }}</span>
                    <span class="psurvey-q">{{ s.q }}</span>
                    <span class="psurvey-answer mono">{{ s.answer }}</span>
                  </div>
                  <div v-if="s.reasoning" class="psurvey-reason">{{ s.reasoning }}</div>
                </div>
                <div v-if="!detail.survey.length" class="muted sm">{{ t('pd.noSurvey') }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- FINDINGS -->
        <div v-else-if="tab === 'findings'">
          <div class="sev-chips">
            <button v-for="c in sevChips" :key="c.key" class="chip" :class="{ on: sevFilter === c.key }" @click="sevFilter = c.key">
              {{ c.label }}<span class="chip-count mono">{{ c.count }}</span>
            </button>
            <button class="btn small csv-btn" @click="exportCSV">⤓ {{ t('find.exportCsv') }}</button>
          </div>
          <div class="col gap11">
            <div v-for="(h, i) in filteredFindings" :key="i" class="finding" :style="{ borderLeftColor: SEV[h.severity].color }">
              <div class="finding-head">
                <span class="heur-tag mono" :style="{ background: SEV[h.severity].chipBg, color: SEV[h.severity].chipFg }">{{ sevLabel(h.severity) }}</span>
                <span v-if="h.severityScore !== undefined" class="sev-score mono">{{ h.severityScore }}</span>
                <span class="heur-name">H{{ h.id }} · {{ heuristicName(h.id) }}</span>
                <span v-if="h.harnessSuspect" class="verify-chip mono" :title="t('pd.needsVerify.hint')">{{ t('pd.needsVerify') }}</span>
                <span v-else-if="h.confidence" class="conf-chip mono" :class="h.confidence" :title="h.confidenceCappedBy ? t('conf.capped.' + h.confidenceCappedBy) : undefined">{{ t('conf.' + h.confidence) }}</span>
                <span class="finding-persona mono">{{ h.persona }}</span>
                <button class="copy-btn" :class="{ copied: copiedIdx === i }" @click="copyFinding(h, i)">
                  {{ copiedIdx === i ? t('find.copied') : t('find.copy') }}
                </button>
              </div>
              <div class="heur-ev">{{ h.evidence }}</div>
              <div class="heur-fix"><span class="fix-lead">{{ t('find.recommendation') }}</span>{{ h.recommendation }}</div>
              <div v-if="h.verified" class="ev-block">
                <a v-if="h.hasScreenshot" :href="shotUrl(h.personaId, h.step)" target="_blank">
                  <img class="ev-thumb" :src="shotUrl(h.personaId, h.step)" :alt="t('ev.step', { n: h.step })" />
                </a>
                <div class="ev-meta">
                  <span class="step-chip mono">{{ t('ev.step', { n: h.step }) }}</span>
                  <a class="ev-url" :href="h.url" target="_blank">{{ h.url }}</a>
                  <div v-if="h.observationQuote" class="ev-quote">„{{ h.observationQuote }}"</div>
                </div>
              </div>
              <div v-else class="ev-none"><span class="unverified-badge">{{ t('ev.unverified') }}</span></div>
            </div>
            <div v-if="filteredFindings.length === 0" class="muted">{{ t('find.empty') }}</div>
          </div>
        </div>

        <!-- SURVEY -->
        <div v-else-if="tab === 'survey'" class="col">
          <div v-for="(q, qi) in surveyRows" :key="qi" class="ux-card">
            <div class="survey-q">
              <span class="q-num mono">{{ String(qi + 1).padStart(2, '0') }}</span>
              <span class="survey-prompt">{{ q.prompt }}</span>
              <span v-if="q.isScale" class="survey-avg mono">{{ t('survey.avgN', { x: q.avg, m: q.max }) }}</span>
              <span v-else class="survey-tag mono">{{ t('survey.open') }}</span>
            </div>
            <template v-if="q.isScale">
              <div class="dist">
                <div v-for="d in q.dist" :key="d.v" class="dist-bar-col">
                  <span class="dist-c mono">{{ d.count || '' }}</span>
                  <div class="dist-bar2" :style="{ height: d.height + '%' }"></div>
                  <span class="dist-v mono">{{ d.v }}</span>
                </div>
              </div>
              <div class="scale-track"><span class="scale-marker" :style="{ left: q.markerPct + '%' }"></span></div>
              <div class="scale-anchors">
                <span>{{ q.min }} · {{ q.lowLabel }}</span>
                <span>{{ q.highLabel }} · {{ q.max }}</span>
              </div>
              <div class="scale-n mono">n = {{ q.n }}</div>
            </template>
            <div v-else class="col gap8">
              <div v-for="(a, ai) in q.answers" :key="ai" class="open-quote">„{{ a }}"</div>
              <div v-if="!q.answers.length" class="muted sm">—</div>
            </div>
            <div v-if="surveySummaries[qi]" class="ai-summary">
              <div class="ai-badge mono">AI</div>
              <div class="ai-body">
                <div class="ai-label mono">{{ t('survey.aiSummary') }}</div>
                <div class="ai-text">{{ surveySummaries[qi] }}</div>
              </div>
            </div>
          </div>
          <div v-if="surveyRows.length === 0" class="muted">{{ t('survey.empty') }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useResults } from '../composables/useResults'
import { useApp } from '../composables/useApp'
import { SEV, OUTCOME, SEV_ORDER, initials, scoreColor, pageSpeedColor, outcomeOf, sevLabel, outcomeLabel, heuristicName, type Severity } from '../theme'
import type { HeuristicFinding, PersonaResult } from '../types'
import ReportView from './ReportView.vue'
import { useI18n } from '../i18n'

const { state: app } = useApp()
const { data, loading, error, load } = useResults()
const { t } = useI18n()

const tab = ref<'overview' | 'personas' | 'findings' | 'survey' | 'report'>('overview')
const selPersona = ref(0)
const sevFilter = ref<'all' | Severity>('all')

const oc = (o: string) => OUTCOME[outcomeOf(o)]

onMounted(() => { if (app.viewRunId) load(app.viewRunId) })
watch(() => app.viewRunId, (id) => { if (id) { tab.value = 'overview'; selPersona.value = 0; load(id) } })

const personas = computed<PersonaResult[]>(() => data.value?.personas ?? [])
const meta = computed(() => data.value?.meta)
const n = computed(() => personas.value.length || 1)

const succ = computed(() => personas.value.filter(p => p.outcome === 'success').length)
const part = computed(() => personas.value.filter(p => p.outcome === 'partial').length)
const fail = computed(() => personas.value.filter(p => p.outcome === 'failed').length)

function fiAvg(p: PersonaResult): number {
  const vals = Object.values(p.firstImpression).filter((v): v is number => typeof v === 'number')
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0
}

// Why a finding is held back from the confirmed report (drives the chip). One
// of the three gates: off-site page, claim unsupported by the trace, or a
// harness interaction failure. null = a confirmed finding.
function exclusionOf(h: any): 'offSite' | 'unsupported' | 'harness' | null {
  if (h?.offSite) return 'offSite'
  if (h?.claimVerdict === 'unsupported') return 'unsupported'
  if (h?.harnessSuspect) return 'harness'
  return null
}
const avgFI = computed(() => personas.value.length ? personas.value.reduce((a, p) => a + fiAvg(p), 0) / n.value : 0)
const avgA11y = computed(() => {
  const vals = personas.value.map(p => p.accessibility.score).filter((v): v is number => typeof v === 'number')
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0
})

const counts = computed(() => {
  let critical = 0, serious = 0, minor = 0
  personas.value.forEach(p => p.heuristics.forEach(h => {
    if (h.severity === 'critical') critical++
    else if (h.severity === 'serious') serious++
    else minor++
  }))
  return { critical, serious, minor, total: critical + serious + minor }
})

const critCount = (p: PersonaResult) => p.heuristics.filter(h => h.severity === 'critical').length

const psPerf = computed(() => meta.value?.pagespeed?.performance ?? null)
const psColor = computed(() => psPerf.value !== null ? pageSpeedColor(psPerf.value) : '#aeb4bd')

const scoreCards = computed(() => [
  { label: t('ov.card.success'), value: `${succ.value}/${n.value}`, unit: '', sub: t('ov.card.success.sub', { part: part.value, fail: fail.value }),
    color: succ.value === n.value ? '#1f9d57' : fail.value > 0 ? '#d83a34' : '#cf8420' },
  { label: t('ov.card.firstImpression'), value: avgFI.value.toFixed(1), unit: '/5', sub: t('ov.card.fi.sub'), color: scoreColor(avgFI.value) },
  { label: t('ov.card.accessibility'), value: avgA11y.value.toFixed(1), unit: '/5', sub: t('ov.card.a11y.sub'), color: scoreColor(avgA11y.value) },
  { label: t('ov.card.findings'), value: String(counts.value.total), unit: '', sub: t('ov.card.findings.sub', { crit: counts.value.critical }), color: '#1c2024' },
  { label: t('ov.card.pagespeed'), value: psPerf.value === null ? '—' : String(psPerf.value), unit: psPerf.value === null ? '' : '/100', sub: t('ov.card.pagespeed.sub'), color: psColor.value },
])

const sevBars = computed(() => {
  const maxSev = Math.max(counts.value.critical, counts.value.serious, counts.value.minor, 1)
  return (['critical', 'serious', 'minor'] as Severity[]).map(k => ({
    label: sevLabel(k), pl: SEV[k].pl, color: SEV[k].color,
    count: counts.value[k], pct: Math.round((counts.value[k] / maxSev) * 100),
  }))
})

const flatFindings = computed(() => {
  const out: (HeuristicFinding & { persona: string; personaId: string })[] = []
  personas.value.forEach(p => p.heuristics.forEach(h => out.push({ ...h, persona: p.name, personaId: p.id })))
  out.sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity])
  return out
})

function shotUrl(personaId: string, step: number | null): string {
  return `/runs/${app.viewRunId}/screenshot/${personaId}/${step ?? 0}`
}

// Backend-supplied extras (present once the run detail includes them)
const comparison = computed(() => (data.value as any)?.comparison ?? null)
const surveySummaries = computed<string[]>(() => (data.value as any)?.report?.survey_summaries ?? [])

// --- findings toolbox (design: copy finding + CSV export) ---------------
const plistHidden = ref(false)
const copiedIdx = ref(-1)
let copyTimer: number | undefined
function copyFinding(h: HeuristicFinding & { persona: string }, i: number) {
  const txt = `[${h.severity.toUpperCase()}] H${h.id} — ${heuristicName(h.id)}\n` +
    `Persona: ${h.persona}\nEvidence (step ${h.step} · ${h.url}): ${h.evidence}\nRecommendation: ${h.recommendation}`
  try { navigator.clipboard.writeText(txt) } catch {}
  copiedIdx.value = i
  clearTimeout(copyTimer)
  copyTimer = window.setTimeout(() => { copiedIdx.value = -1 }, 1600)
}

function exportCSV() {
  const esc = (v: unknown) => '"' + String(v ?? '').replace(/"/g, '""') + '"'
  const rows = [['severity', 'heuristic_id', 'heuristic', 'persona', 'step', 'url', 'evidence', 'recommendation', 'verified'].join(',')]
  flatFindings.value.forEach(h => rows.push(
    [h.severity, h.id, heuristicName(h.id), h.persona, h.step ?? '', h.url, h.evidence, h.recommendation, h.verified].map(esc).join(',')
  ))
  const blob = new Blob(['﻿' + rows.join('\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `uxagent-findings-${app.viewRunId ?? 'run'}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}
const topFixes = computed(() => flatFindings.value.slice(0, 4).map(h => ({
  label: sevLabel(h.severity), chipBg: SEV[h.severity].chipBg, chipFg: SEV[h.severity].chipFg, text: h.recommendation,
})))

const verdict = computed(() => succ.value === n.value ? t('ov.verdict.all')
  : fail.value > 0 ? t('ov.verdict.fail') : t('ov.verdict.friction'))
const verdictBody = computed(() => t('ov.verdictBody', {
  succ: succ.value, n: n.value, part: part.value, fail: fail.value,
  crit: counts.value.critical, ser: counts.value.serious, min: counts.value.minor,
}))

const detail = computed(() => personas.value[selPersona.value])
const fiRows = computed(() => {
  const p = detail.value
  if (!p) return []
  const dims: [keyof PersonaResult['firstImpression'], string][] = [
    ['purpose', t('fi.purpose')], ['navigation', t('fi.navigation')], ['cta', t('fi.cta')],
    ['hierarchy', t('fi.hierarchy')], ['trust', t('fi.trust')],
  ]
  return dims.map(([k, label]) => {
    const score = p.firstImpression[k]
    return { label, score, pct: typeof score === 'number' ? (score / 5) * 100 : 0 }
  })
})

const sevChips = computed(() => ([
  { key: 'all' as const, label: t('find.all'), count: counts.value.total },
  { key: 'critical' as const, label: sevLabel('critical'), count: counts.value.critical },
  { key: 'serious' as const, label: sevLabel('serious'), count: counts.value.serious },
  { key: 'minor' as const, label: sevLabel('minor'), count: counts.value.minor },
]))
const filteredFindings = computed(() => sevFilter.value === 'all'
  ? flatFindings.value : flatFindings.value.filter(h => h.severity === sevFilter.value))

type SurveyRow = {
  prompt: string; isScale: boolean; min: number; max: number; n: number
  lowLabel: string; highLabel: string; avg: string; markerPct: number
  dist: { v: number; count: number; height: number }[]; answers: string[]
}

const surveyRows = computed<SurveyRow[]>(() => {
  // Preferred: the backend's enriched, questionnaire-ordered survey stats
  // (real prompts, correct per-question scale + anchors, response distribution,
  // open answers) — the same source the static report uses.
  const qs = (data.value as any)?.report?.statistics?.survey?.questions
  if (Array.isArray(qs) && qs.length) {
    return qs.map((q: any): SurveyRow => {
      if (q.kind === 'scale') {
        const cs = (q.distribution || []).map((d: any) => ({ v: d.value, count: d.count }))
        const maxC = Math.max(...cs.map((d: any) => d.count), 1)
        const lo = q.min ?? 1, hi = q.max ?? 5
        const a = typeof q.avg === 'number' ? q.avg : null
        return {
          prompt: q.prompt, isScale: true, min: lo, max: hi, n: q.n ?? 0,
          lowLabel: q.low_label ?? '', highLabel: q.high_label ?? '',
          avg: a != null ? a.toFixed(2).replace('.', ',') : '—',
          markerPct: (a != null && hi > lo) ? Math.round(((a - lo) / (hi - lo)) * 100) : 0,
          dist: cs.map((d: any) => ({ v: d.v, count: d.count, height: Math.round((d.count / maxC) * 100) })),
          answers: [],
        }
      }
      return { prompt: q.prompt, isScale: false, min: 1, max: 5, n: 0,
               lowLabel: '', highLabel: '', avg: '—', markerPct: 0, dist: [],
               answers: (q.answers || []) as string[] }
    })
  }
  // Legacy fallback (runs generated before enriched survey stats): per-persona.
  if (!personas.value.length) return []
  const qCount = personas.value[0].survey.length
  return Array.from({ length: qCount }, (_, qi): SurveyRow => {
    const prompt = personas.value[0].survey[qi]?.q ?? ''
    const answers = personas.value.map(p => p.survey[qi]?.answer ?? '').filter(a => a !== '')
    const numeric = answers.length > 0 && answers.every(a => /^[1-5]$/.test(a))
    let dist: { v: number; count: number; height: number }[] = []
    let avg = '—', markerPct = 0
    if (numeric) {
      const cs = [1, 2, 3, 4, 5].map(v => answers.filter(a => +a === v).length)
      const maxC = Math.max(...cs, 1)
      dist = [1, 2, 3, 4, 5].map((v, k) => ({ v, count: cs[k], height: Math.round((cs[k] / maxC) * 100) }))
      const m = answers.reduce((s, a) => s + +a, 0) / answers.length
      avg = m.toFixed(2).replace('.', ','); markerPct = Math.round(((m - 1) / 4) * 100)
    }
    return { prompt, isScale: numeric, min: 1, max: 5, n: answers.length,
             lowLabel: 'zdecydowanie nie', highLabel: 'zdecydowanie tak',
             avg, markerPct, dist, answers: numeric ? [] : answers }
  })
})

const tabs = computed(() => [
  { key: 'overview', label: t('tab.overview'), count: null as number | null },
  { key: 'personas', label: t('tab.personas'), count: personas.value.length },
  { key: 'findings', label: t('tab.findings'), count: counts.value.total },
  { key: 'survey', label: t('tab.survey'), count: null as number | null },
  { key: 'report', label: t('tab.report'), count: null as number | null },
])

function openPersona(i: number) { selPersona.value = i; tab.value = 'personas' }
</script>

<style scoped lang="scss">
.results { width: 100%; }
.state { width: 100%; padding: 60px 36px; color: var(--muted-2); }
.state.err { color: #b5564f; }
.tabs { padding: 0 36px; border-bottom: 1px solid var(--border); display: flex; gap: 4px; background: #fff; }
.tab { padding: 14px 16px; border: none; background: transparent; font: 600 14px var(--sans); cursor: pointer; border-bottom: 2.5px solid transparent; margin-bottom: -1px; color: var(--muted-2); }
.tab.on { color: var(--ac); border-bottom-color: var(--ac); }
.tab-count { font-size: 11px; margin-left: 7px; padding: 2px 7px; border-radius: 20px; background: #f1f2f4; color: #9aa0a8; }
.tab-count.on { background: #eef3fc; color: var(--ac); }
.tab-body { width: 100%; padding: 28px 36px 60px; }
.col { display: flex; flex-direction: column; gap: 18px; }
.gap14 { gap: 14px; } .gap12 { gap: 12px; } .gap11 { gap: 11px; } .gap8 { gap: 8px; } .gap7 { gap: 7px; }
.mb16 { margin-bottom: 16px; } .mt14 { margin-top: 14px; } .mt16 { margin-top: 16px; }
.ct { font-weight: 700; font-size: 15px; margin-bottom: 16px; }
.ct.nomb { margin-bottom: 0; }
.ctrow { margin-bottom: 16px; }
.row-between { display: flex; align-items: center; justify-content: space-between; }
.muted { color: var(--muted-2); font-size: 13px; }
.muted.sm { font-size: 12.5px; }
.link { background: transparent; border: none; color: var(--ac); font: 600 13px var(--sans); cursor: pointer; }

.verdict { background: #fff; border: 1px solid var(--border); border-radius: 13px; padding: 24px; display: flex; gap: 18px; align-items: flex-start; }
.verdict-icon { width: 46px; height: 46px; border-radius: 11px; background: #fbf2e3; display: flex; align-items: center; justify-content: center; flex: none; font-size: 22px; }
.verdict-title { font-weight: 700; font-size: 17px; margin-bottom: 5px; }
.verdict-body { font-size: 14px; color: var(--muted); line-height: 1.55; max-width: 780px; }

.score-cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }
.score-card { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 18px; }
.score-label { font: 600 10px/1.2 var(--mono); letter-spacing: .05em; text-transform: uppercase; color: #9aa0a8; margin-bottom: 12px; min-height: 24px; }
.score-val { font: 700 30px var(--mono); letter-spacing: -.02em; }
.unit { font-size: 15px; color: var(--faint); font-weight: 500; }
.score-sub { font-size: 12px; color: var(--muted-2); margin-top: 6px; }

.grid-13 { display: grid; grid-template-columns: 1.3fr 1fr; gap: 18px; }
.r13 .sev-head { display: flex; justify-content: space-between; margin-bottom: 6px; }
.sev-name { font: 600 13px var(--sans); }
.sev-pl { font-size: 12px; color: var(--faint); }
.sev-count { font: 700 13px var(--mono); }
.sev-track { height: 9px; background: #f1f2f4; border-radius: 5px; overflow: hidden; }
.sev-fill { height: 100%; border-radius: 5px; }
.ps-row { border-top: 1px solid #f0f1f4; padding-top: 14px; margin-top: 2px; display: flex; justify-content: space-between; align-items: center; }
.ps-label { font-size: 13px; color: var(--muted-2); }
.ps-val { font: 700 18px var(--mono); }
.ps-unit { font-size: 12px; color: var(--faint); }
.ps-na { color: var(--faint); font-size: 13px; }
.fix-row { display: flex; gap: 11px; align-items: flex-start; }
.fix-tag { font: 700 11px/1 var(--mono); padding: 5px 7px; border-radius: 5px; flex: none; }
.fix-text { font-size: 13px; color: var(--ink-2); line-height: 1.45; }

.persona-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.strip-card { text-align: left; background: #fcfcfd; border: 1px solid var(--border-soft); border-radius: 11px; padding: 16px; cursor: pointer; }
.strip-card:hover { border-color: #cfd6e0; background: #fff; }
.strip-head { display: flex; align-items: center; gap: 10px; margin-bottom: 11px; }
.strip-avatar { width: 36px; height: 36px; border-radius: 50%; background: #eef1f6; display: flex; align-items: center; justify-content: center; font: 700 14px var(--mono); color: var(--muted); }
.strip-name { font: 600 14px var(--sans); }
.strip-meta { font-size: 11px; color: #9aa0a8; }
.outcome-pill { display: inline-flex; align-items: center; gap: 6px; font: 600 11.5px var(--sans); padding: 5px 10px; border-radius: 20px; }
.outcome-pill.big { font-size: 13px; padding: 8px 14px; gap: 7px; }
.pdot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.dh-outcome { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
.dh-outcome-tags { display: flex; gap: 5px; }
.src-chip { font-size: 9px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted-2);
  border: 1px solid var(--line, #e7e9ee); border-radius: 4px; padding: 1px 5px; cursor: help; }
.src-chip.measured { color: #1f9d57; border-color: #bfe6cf; background: #f1faf4; }
.src-chip.disagree { color: #b5701a; border-color: #f0d9b0; background: #fdf3e3; }
.strip-sum { font-size: 12.5px; color: var(--muted-2); margin-top: 11px; }

.personas-layout { display: grid; grid-template-columns: 230px 1fr; gap: 22px; transition: grid-template-columns .2s ease; }
.personas-layout.list-hidden { grid-template-columns: 1fr; }
.persona-list { display: flex; flex-direction: column; gap: 8px; }
.plist-head { display: flex; align-items: center; justify-content: space-between; padding: 0 2px 2px; }
.plist-title { font: 600 10px/1 var(--mono); letter-spacing: .06em; text-transform: uppercase; color: var(--text-muted); }
.plist-toggle { width: 24px; height: 24px; border: 1px solid var(--border); background: var(--surface); border-radius: 6px; color: var(--text-muted); cursor: pointer; font: 600 12px var(--mono); display: flex; align-items: center; justify-content: center; }
.plist-toggle:hover { border-color: var(--ac); color: var(--ac); }
.plist-show { align-self: flex-start; display: inline-flex; align-items: center; gap: 8px; padding: 8px 13px; border: 1px solid var(--border-input); background: var(--surface); border-radius: 8px; color: var(--text-3); font: 600 12.5px var(--sans); cursor: pointer; }
.plist-show:hover { border-color: var(--ac); color: var(--ac); }
.plist-item { text-align: left; background: #fff; border: 1px solid var(--border); border-radius: 11px; padding: 14px; cursor: pointer; display: flex; align-items: center; gap: 11px; }
.plist-item.sel { border-color: var(--ac); box-shadow: 0 0 0 3px rgba(42,111,219,.1); }
.plist-avatar { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font: 700 13px var(--mono); flex: none; background: #eef1f6; color: var(--muted); }
.plist-avatar.sel { background: var(--ac); color: #fff; }
.plist-name { font: 600 13.5px var(--sans); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.plist-outcome { font: 600 10.5px var(--sans); margin-top: 3px; }
.persona-detail { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.detail-head { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 12px 16px; margin-bottom: 16px; }
.dh-left { display: flex; gap: 14px; align-items: center; flex: 1; min-width: 220px; }
.dh-avatar { width: 48px; height: 48px; border-radius: 50%; background: #eef1f6; display: flex; align-items: center; justify-content: center; font: 700 17px var(--mono); color: var(--muted); flex: none; }
.dh-name { font-weight: 700; font-size: 18px; }
.dh-meta { font: 500 12.5px var(--mono); color: var(--muted-2); margin-top: 4px; line-height: 1.5; }
.objective-box { background: #fcfcfd; border: 1px solid var(--border-soft); border-radius: 9px; padding: 13px 15px; }
.home-loc { display: flex; align-items: center; gap: 7px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border-soft); font-size: 13px; }
.home-ico { color: var(--ac); font-size: 14px; }
.home-label { color: var(--muted-2); }
.home-val { color: var(--ink); font-weight: 600; }
.obj-text { font-size: 14px; color: var(--ink-2); margin-top: 5px; }
.refl-summary { font-size: 14px; color: var(--ink-2); line-height: 1.6; margin-bottom: 18px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.refl-label { font: 600 11px var(--mono); letter-spacing: .05em; text-transform: uppercase; margin-bottom: 9px; }
.refl-label.red { color: #b5564f; } .refl-label.green { color: #1f9d57; }
.bullet { display: flex; gap: 8px; font-size: 13px; color: var(--muted); line-height: 1.45; }
.bullet.sm { font-size: 12.5px; }
.b-red { color: #d83a34; flex: none; } .b-green { color: #1f9d57; flex: none; } .b-grey { color: #9aa0a8; flex: none; }
.priority-fix { background: #fbf6ee; border: 1px solid #f0e6d4; border-radius: 9px; padding: 13px 15px; }
.pf-label { color: #b5701a; }
.pf-text { font-size: 13.5px; color: var(--ink-2); margin-top: 5px; line-height: 1.5; }
.fi-row { display: flex; align-items: center; gap: 12px; }
.fi-label { width: 110px; font-size: 12.5px; color: var(--muted); flex: none; }
.fi-track { flex: 1; height: 7px; background: #f1f2f4; border-radius: 4px; overflow: hidden; }
.fi-fill { height: 100%; border-radius: 4px; background: var(--ac); }
.fi-score { font: 700 12.5px var(--mono); width: 30px; text-align: right; }
.a11y-score { font: 700 17px var(--mono); }
.of5 { font-size: 12px; color: var(--faint); }
.vp-row { display: flex; gap: 9px; align-items: baseline; }
.vp-tag { font: 600 9.5px/1 var(--mono); padding: 4px 6px; border-radius: 4px; flex: none; }
.vp-issue { font-size: 12.5px; color: var(--muted); line-height: 1.4; }
.heur, .finding { border: 1px solid var(--border-soft); border-left: 3px solid; border-radius: 9px; padding: 14px 16px; }
.finding { background: #fff; border-color: var(--border); border-left-width: 3px; padding: 16px 18px; }
.heur-head, .finding-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.heur-tag { font: 700 10px/1 var(--mono); letter-spacing: .04em; text-transform: uppercase; padding: 5px 8px; border-radius: 5px; }
.heur-name { font: 600 13.5px var(--sans); }
/* NN/g 0–4 numeric severity */
.sev-score { font: 700 10px var(--mono); background: #eef1f6; color: var(--muted); border-radius: 4px; padding: 3px 6px; }
/* harness-suspect findings must never read as confirmed site defects */
.verify-chip { font: 700 9.5px var(--mono); letter-spacing: .04em; text-transform: uppercase; background: #fbf2e3; color: #a8691a; border: 1px solid #f0e6d4; border-radius: 5px; padding: 3px 7px; cursor: help; }
.conf-chip { font: 700 9.5px var(--mono); letter-spacing: .04em; text-transform: uppercase; border-radius: 5px; padding: 3px 7px; }
.conf-chip.high { background: #e9f6ee; color: #1f9d57; }
.conf-chip.medium { background: #f1f2f4; color: #5b6470; }
.conf-chip.low { background: #fbf2e3; color: #a8691a; }

/* ISO 9241-11 panel */
.iso-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.iso-col { background: #fcfcfd; border: 1px solid var(--border-soft); border-radius: 9px; padding: 14px; }
.iso-label { font-size: 10px; letter-spacing: .05em; text-transform: uppercase; color: var(--muted-2); margin-bottom: 8px; }
.iso-main { font: 700 18px var(--sans); }
.iso-main.mono { font-family: var(--mono); }
.iso-unit { font-size: 12px; color: var(--faint); font-weight: 500; }
.iso-sub { font-size: 12px; color: var(--muted); margin-top: 6px; }
.iso-warn { font: 600 11px var(--mono); color: #a8691a; margin-top: 6px; }
.iso-synth { font: 500 10px var(--mono); color: var(--faint); margin-top: 6px; }

/* bias audit */
.bias-card { border-color: #f0e6d4; }
.bias-summary { font-size: 13.5px; color: var(--ink-2); line-height: 1.5; }
.bias-flag { display: flex; gap: 10px; align-items: flex-start; background: #fbf6ee; border: 1px solid #f0e6d4; border-radius: 8px; padding: 10px 12px; }
.bias-type { font: 700 9.5px var(--mono); text-transform: uppercase; color: #a8691a; flex: none; margin-top: 2px; }
.bias-expl { font-size: 13px; color: var(--ink-2); line-height: 1.45; }
.finding-persona { margin-left: auto; font-size: 11.5px; color: #9aa0a8; }
.heur-ev { font-size: 13px; color: var(--muted); line-height: 1.5; margin-bottom: 8px; }
.heur-fix { font-size: 13px; color: var(--ink-2); line-height: 1.5; }
.fix-lead { color: var(--ac); font-weight: 600; }

/* per-persona survey answers */
.psurvey-item { border: 1px solid var(--border-soft); border-radius: 9px; padding: 13px 15px; }
.psurvey-head { display: flex; align-items: baseline; gap: 10px; }
.psurvey-num { font: 700 11px var(--mono); color: var(--faint); flex: none; }
.psurvey-q { font-weight: 600; font-size: 13.5px; flex: 1; line-height: 1.45; }
.psurvey-answer { font: 700 12.5px var(--mono); color: var(--ac); background: var(--accent-tint, #fdecec); padding: 4px 10px; border-radius: 6px; flex: none; }
.psurvey-reason { margin-top: 8px; padding-left: 26px; font-size: 12.5px; color: var(--muted); line-height: 1.55; }

.sev-chips { display: flex; gap: 8px; margin-bottom: 18px; }
.chip { display: inline-flex; align-items: center; gap: 8px; padding: 9px 15px; border-radius: 8px; font: 600 13px var(--sans); cursor: pointer; background: #fff; color: var(--muted); border: 1px solid #e0e3e9; }
.chip.on { background: #1c2024; color: #fff; border-color: #1c2024; }
.chip-count { font: 700 11px var(--mono); color: var(--faint); }
.chip.on .chip-count { color: #fff; opacity: .7; }

.survey-q { display: flex; align-items: flex-start; gap: 11px; margin-bottom: 18px; }
.q-num { font: 700 12px var(--mono); color: var(--faint); }
.survey-prompt { font-weight: 700; font-size: 15.5px; flex: 1; }
.survey-avg { font: 600 12px var(--mono); color: var(--muted); background: #f1f2f4; padding: 5px 10px; border-radius: 6px; flex: none; }
.dist { display: flex; align-items: flex-end; gap: 10px; height: 120px; padding: 0 8px; }
.dist-bar-col { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 8px; height: 100%; justify-content: flex-end; }
.dist-c { font: 700 13px var(--mono); color: var(--muted); }
.dist-bar2 { width: 100%; max-width: 54px; border-radius: 6px 6px 0 0; background: var(--ac); min-height: 4px; }
.dist-v { font: 600 12px var(--mono); color: #9aa0a8; }
.survey-tag { font: 600 10px var(--mono); text-transform: uppercase; letter-spacing: .04em; color: var(--muted-2); border: 1px solid var(--border); border-radius: 4px; padding: 3px 7px; flex: none; }
.scale-track { position: relative; height: 5px; border-radius: 3px; margin-top: 14px; background: linear-gradient(90deg, #f0b6b6, #f5e6a8 50%, #a9dcc0); }
.scale-marker { position: absolute; top: 50%; width: 13px; height: 13px; border-radius: 50%; background: var(--text); border: 2px solid var(--surface); box-shadow: 0 1px 3px rgba(0,0,0,.3); transform: translate(-50%, -50%); }
.scale-anchors { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted-2); margin-top: 8px; }
.scale-n { font: 11px var(--mono); color: var(--faint); margin-top: 8px; }
.open-quote { font-size: 13.5px; color: var(--text-2); background: var(--surface-3); border-left: 3px solid var(--border); padding: 9px 13px; border-radius: 0 6px 6px 0; line-height: 1.5; }
.response { background: #fcfcfd; border: 1px solid var(--border-soft); border-radius: 9px; padding: 13px 15px; }
.resp-answer { font-size: 13.5px; color: var(--ink); line-height: 1.5; margin-bottom: 6px; }
.resp-persona { font: 500 11.5px var(--mono); color: #9aa0a8; }

/* finding evidence */
.unverified-badge { font: 700 10.5px var(--mono); color: #8a6d00; background: #fff3cd; border: 1px solid #ffe08a; border-radius: 5px; padding: 1px 7px; white-space: nowrap; }
.ev-block { display: flex; gap: 12px; align-items: flex-start; margin-top: 9px; background: #fafbfc; border: 1px solid var(--border-soft); border-radius: 8px; padding: 10px; }
.ev-thumb { width: 130px; height: auto; border: 1px solid var(--border-soft); border-radius: 5px; display: block; }
.ev-meta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.ev-url { font-size: 12px; color: var(--ac); word-break: break-all; text-decoration: none; }
.ev-url:hover { text-decoration: underline; }
.ev-url.sm { margin-left: 6px; }
.ev-quote { font-size: 12px; font-style: italic; color: var(--muted-2); line-height: 1.45; }
.ev-none { margin-top: 8px; }
.step-chip { font-size: 11px; background: var(--accent-tint); color: var(--ac); border-radius: 5px; padding: 1px 7px; }

/* comparison vs previous run */
.cmp-head { font-weight: 700; font-size: 15px; margin-bottom: 16px; }
.cmp-date { font: 500 12.5px var(--mono); color: var(--text-muted); }
.cmp-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 18px; }
.cmp-metric { background: var(--surface-2); border: 1px solid var(--border-2); border-radius: 10px; padding: 14px; }
.cmp-label { font: 600 10px/1.2 var(--mono); letter-spacing: .05em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 9px; }
.cmp-vals { display: flex; align-items: baseline; gap: 8px; }
.cmp-now { font: 700 20px var(--mono); }
.cmp-prev { font: 600 12px var(--mono); }
.cmp-cols { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
.cmp-col-h { font: 600 10.5px var(--mono); letter-spacing: .05em; text-transform: uppercase; margin-bottom: 8px; }
.cmp-item { display: flex; gap: 7px; font-size: 12.5px; color: var(--text-3); line-height: 1.45; margin-bottom: 7px; }

/* AI survey summary */
.ai-summary { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border-2); display: flex; gap: 12px; align-items: flex-start; }
.ai-badge { width: 26px; height: 26px; border-radius: 7px; background: var(--accent-tint); flex: none; display: flex; align-items: center; justify-content: center; font: 700 11px var(--mono); color: var(--ac); }
.ai-body { flex: 1; min-width: 0; }
.ai-label { font: 600 10px/1 var(--mono); letter-spacing: .06em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 7px; }
.ai-text { font-size: 13.5px; color: var(--text-2); line-height: 1.6; }

/* findings toolbox */
.csv-btn { margin-left: auto; }
.copy-btn { margin-left: 8px; padding: 5px 11px; border-radius: 6px; border: 1px solid var(--border-input); background: transparent; font: 600 11.5px var(--sans); color: var(--text-3); cursor: pointer; flex: none; }
.copy-btn:hover { border-color: var(--ac); color: var(--ac); }
.copy-btn.copied { border-color: var(--success-text); color: var(--success-text); }
</style>
