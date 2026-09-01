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

      <!-- §6.1 scope: what produced these findings -->
      <section :id="anchor('scope')" class="sec">
        <h3 class="sec-title">{{ t('rep.scope') }}</h3>
        <div v-if="scopeRows.length" class="scope-grid">
          <div v-for="s in scopeRows" :key="s.label" class="scope-cell">
            <div class="scope-label mono">{{ s.label }}</div>
            <div class="scope-val mono">{{ s.value }}</div>
          </div>
        </div>
        <p v-else class="prose sm muted-prose">{{ t('rep.scope.missing') }}</p>
      </section>

      <!-- §6.1 methodology + limitations -->
      <section v-if="narrative.methodology_note" :id="anchor('methodology')" class="sec">
        <h3 class="sec-title">{{ t('rep.methodology') }}</h3>
        <p class="prose sm muted-prose">{{ narrative.methodology_note }}</p>
      </section>

      <!-- §6.1 persona x task -->
      <section v-if="personaTasks?.length" :id="anchor('personas')" class="sec">
        <h3 class="sec-title">{{ t('rep.personaTasks') }}</h3>
        <div class="pt-wrap">
          <table class="pt">
            <thead>
              <tr>
                <th>{{ t('rep.pt.persona') }}</th>
                <th>{{ t('rep.pt.task') }}</th>
                <th>{{ t('rep.pt.outcome') }}</th>
                <th class="num">{{ t('rep.pt.steps') }}</th>
                <th class="num">{{ t('rep.pt.backtracks') }}</th>
                <th class="num">{{ t('rep.pt.seq') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in personaTasks" :key="i">
                <td>
                  <div class="pt-name">{{ r.persona }}</div>
                  <div v-if="r.home_location" class="pt-home">{{ r.home_location }}</div>
                </td>
                <td class="pt-task">{{ r.task || '—' }}</td>
                <td>
                  <span class="pt-outcome" :style="{ color: outcomeColor(r.outcome) }">{{ outcomeLabel(outcomeOf(r.outcome)) }}</span>
                  <span
                    class="pt-src mono"
                    :class="{ measured: r.outcome_source === 'observed' }"
                    :title="r.outcome_source === 'observed' ? t('rep.pt.measuredHint') : t('rep.pt.declaredHint')"
                  >{{ r.outcome_source === 'observed' ? t('rep.pt.measured') : t('rep.pt.declared') }}</span>
                  <span
                    v-if="r.disagreement"
                    class="pt-src mono disagree"
                    :title="t('rep.pt.disagreeHint') + (r.declared_outcome ? ' → ' + outcomeLabel(outcomeOf(r.declared_outcome)) : '')"
                  >{{ t('rep.pt.disagree') }}</span>
                </td>
                <td class="num mono">{{ r.steps ?? '—' }}</td>
                <td class="num mono">{{ r.backtracks ?? '—' }}</td>
                <td class="num mono">
                  {{ r.seq ?? '—' }}
                  <span v-if="r.seq != null" class="pt-src mono" :title="t('rep.pt.syntheticHint')">{{ t('rep.pt.synthetic') }}</span>
                </td>
              </tr>
            </tbody>
          </table>
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
        <!-- §6.1 evidence-status legend: every claim says what backs it -->
        <div class="legend">
          <span v-for="l in evidenceLegend" :key="l.key" class="legend-item">
            <span class="legend-dot" :style="{ background: l.color }"></span>
            <span class="legend-name">{{ l.label }}</span>
            <span class="legend-desc">{{ l.desc }}</span>
          </span>
        </div>
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
                  <span v-if="it.verified === false" class="unverified-badge">{{ t('ev.unverified') }}</span>
                  <span class="issue-freq mono">{{ it.frequency }}</span>
                </div>
                <p class="issue-impact">{{ it.impact }}</p>
                <p class="issue-fix"><span class="fix-lead">{{ t('rep.recommendation') }}</span>{{ it.recommendation }}</p>
                <div v-for="(ex, ei) in (it.examples || [])" :key="ei" class="ev-block">
                  <a v-if="ex.screenshot" :href="evUrl(ex)" target="_blank">
                    <img class="ev-thumb" :src="evUrl(ex)" :alt="t('ev.step', { n: ex.step ?? 0 })" />
                  </a>
                  <div class="ev-meta">
                    <span class="ev-label mono">{{ t('ev.evidence') }} — {{ ex.persona_name }}, {{ t('ev.step', { n: ex.step ?? 0 }) }}</span>
                    <a class="ev-url" :href="ex.url" target="_blank">{{ ex.url }}</a>
                    <div v-if="ex.observation_quote" class="ev-quote">„{{ ex.observation_quote }}"</div>
                  </div>
                </div>
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

      <!-- §6.2 prioritization: severity x impact x confidence -->
      <section v-if="priorityRows.length" :id="anchor('prioritization')" class="sec">
        <h3 class="sec-title">{{ t('rep.prioritization') }}</h3>
        <p class="prose sm muted-prose">{{ t('rep.prioritization.note') }}</p>
        <div class="pt-wrap">
          <table class="pt">
            <thead>
              <tr>
                <th class="num">#</th>
                <th>{{ t('rep.pri.issue') }}</th>
                <th>{{ t('rep.pri.severity') }}</th>
                <th>{{ t('rep.pri.impact') }}</th>
                <th>{{ t('rep.pri.confidence') }}</th>
                <th>{{ t('rep.pri.evidence') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in priorityRows" :key="r.heuristic_id">
                <td class="num mono">{{ r.priority }}</td>
                <td><span class="issue-h mono">H{{ r.heuristic_id }}</span> {{ r.heuristic_name }}</td>
                <td><span :style="{ color: sevColorOf(r.severity) }">{{ sevLabel(sevOf(r.severity)) }}</span></td>
                <td><span class="pill" :class="r.impact">{{ t('rep.level.' + r.impact) }}</span></td>
                <td><span class="pill" :class="r.confidence">{{ t('rep.level.' + r.confidence) }}</span></td>
                <!-- the numbers behind `impact`, so the rule can be checked -->
                <td class="pri-ev mono">
                  {{ t('rep.pri.reach', { n: r.personas_affected, total: r.participant_count }) }}<template v-if="r.blocked"> · {{ t('rep.pri.blocked') }}</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- §6.1 sections that are deliberately out of scope -->
      <section :id="anchor('outofscope')" class="sec">
        <h3 class="sec-title">{{ t('rep.outOfScope') }}</h3>
        <div class="oos">
          <div v-for="o in outOfScope" :key="o.key" class="oos-item">
            <div class="oos-name">{{ o.label }}</div>
            <p class="prose sm muted-prose">{{ o.reason }}</p>
          </div>
        </div>
      </section>

      <!-- §6.1 appendix -->
      <section :id="anchor('appendix')" class="sec">
        <h3 class="sec-title">{{ t('rep.appendix') }}</h3>
        <div class="scope-grid">
          <div v-for="a in appendixRows" :key="a.label" class="scope-cell">
            <div class="scope-label mono">{{ a.label }}</div>
            <div class="scope-val mono">{{ a.value }}</div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { apiAssetUrl } from '../api'
import { SEV, scoreColor, pageSpeedColor, sevLabel, sevOf, outcomeLabel, outcomeOf, OUTCOME } from '../theme'
import { useApp } from '../composables/useApp'
import { useI18n } from '../i18n'
import type { AggIssueExample, AggReport, PersonaTaskRow, PriorityRow, RunMeta } from '../types'

const { t } = useI18n()
const { state: app } = useApp()
const props = defineProps<{
  report: AggReport | null | undefined
  meta?: RunMeta | null
  personaTasks?: PersonaTaskRow[]
}>()

// §6.1 scope. Null for runs made before it was recorded — say so rather than
// print a guess about which model produced the findings.
const scope = computed(() => props.meta?.scope ?? null)
const scopeRows = computed(() => {
  const rows: { label: string; value: string }[] = []
  const s = scope.value
  if (s) {
    rows.push({ label: t('rep.scope.provider'), value: s.provider ?? '—' })
    for (const [tier, model] of Object.entries(s.models ?? {})) {
      rows.push({ label: t('rep.scope.model.' + tier), value: model ?? '—' })
    }
    rows.push({ label: t('rep.scope.prompts'), value: s.prompts_version ?? '—' })
  }
  // The declared site purpose the judgements were calibrated against — the
  // reader needs to know a catalog was judged as a catalog, not a shop.
  const ctx = props.meta?.siteContext
  if (ctx && ctx.trim()) rows.push({ label: t('rep.scope.siteContext'), value: ctx })
  return rows
})

const personaTasks = computed(() => props.personaTasks ?? [])

// §6.2 matrix, computed backend-side (prioritization.py) — impact is derived
// from reach x task consequence, never asked of the model.
const priorityRows = computed<PriorityRow[]>(
  () => (props.report?.statistics?.prioritization as PriorityRow[]) ?? [],
)

const sevColorOf = (pl: string) => SEV[sevOf(pl)].color
const outcomeColor = (o: string | null) => OUTCOME[outcomeOf(o)].color

// §6.1 evidence-status legend. Each finding already carries these flags; the
// legend is what lets a reader tell an observation from an inference.
const evidenceLegend = computed(() => [
  { key: 'observation', color: '#1f9d57', label: t('rep.ev.observation'), desc: t('rep.ev.observation.d') },
  { key: 'inference', color: '#b5701a', label: t('rep.ev.inference'), desc: t('rep.ev.inference.d') },
  { key: 'needs', color: '#d83a34', label: t('rep.ev.needs'), desc: t('rep.ev.needs.d') },
])

// §6.1 lists these, and this tool does not produce them. An audit report that
// silently omits its own validation plan overclaims, so name them and say why.
const outOfScope = computed(() => [
  { key: 'benchmark', label: t('rep.oos.benchmark'), reason: t('rep.oos.benchmark.d') },
  { key: 'validation', label: t('rep.oos.validation'), reason: t('rep.oos.validation.d') },
])

const appendixRows = computed(() => [
  { label: t('rep.apx.task'), value: props.report?.task || props.meta?.intent || '—' },
  { label: t('rep.apx.site'), value: props.report?.website || props.meta?.startUrl || '—' },
  { label: t('rep.apx.personas'), value: String(props.meta?.personaCount ?? personaTasks.value.length) },
  { label: t('rep.apx.maxSteps'), value: String(props.meta?.maxSteps ?? '—') },
  { label: t('rep.apx.prompts'), value: scope.value?.prompts_version ?? '—' },
])

function evUrl(ex: AggIssueExample): string {
  // ex.screenshot is run-relative (<persona_dir>/screenshot/<file>.png), so
  // encode each segment individually — encoding the whole string would escape
  // the separators and break the path.
  const rel = String(ex.screenshot ?? '').split('/').map(encodeURIComponent).join('/')
  return apiAssetUrl(`/runs/${encodeURIComponent(app.viewRunId ?? '')}/${rel}`)
}

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

// §6.1 order: summary -> scope -> methodology -> persona x task -> findings ->
// accessibility/assessments -> prioritization -> validation -> appendix.
const sections = computed(() => [
  { id: 'summary', label: t('rep.toc.summary') },
  { id: 'scope', label: t('rep.toc.scope') },
  { id: 'methodology', label: t('rep.toc.methodology') },
  { id: 'personas', label: t('rep.toc.personas') },
  { id: 'strengths', label: t('rep.toc.strengths') },
  { id: 'issues', label: t('rep.toc.issues') },
  { id: 'assessments', label: t('rep.toc.assessments') },
  { id: 'recommendations', label: t('rep.toc.recommendations') },
  { id: 'prioritization', label: t('rep.toc.prioritization') },
  { id: 'diagnostics', label: t('rep.toc.diagnostics') },
  { id: 'outofscope', label: t('rep.toc.outOfScope') },
  { id: 'appendix', label: t('rep.toc.appendix') },
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

/* issue evidence */
.unverified-badge { font: 700 10.5px var(--mono); color: #8a6d00; background: #fff3cd; border: 1px solid #ffe08a; border-radius: 5px; padding: 1px 7px; white-space: nowrap; }
.ev-block { display: flex; gap: 12px; align-items: flex-start; margin-top: 9px; background: #fafbfc; border: 1px solid var(--border-soft); border-radius: 8px; padding: 10px; }
.ev-thumb { width: 130px; height: auto; border: 1px solid var(--border-soft); border-radius: 5px; display: block; }
.ev-meta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.ev-label { font-size: 11px; color: var(--muted-2); text-transform: uppercase; letter-spacing: .04em; }
.ev-url { font-size: 12px; color: var(--ac); word-break: break-all; text-decoration: none; }
.ev-url:hover { text-decoration: underline; }
.ev-quote { font-size: 12px; font-style: italic; color: var(--muted-2); line-height: 1.45; }

/* §6.1 scope + appendix */
.scope-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; }
.scope-cell { background: var(--surface-2, #f7f8fa); border: 1px solid var(--line, #e7e9ee); border-radius: 8px; padding: 10px 12px; }
.scope-label { font-size: 10px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted-2); margin-bottom: 4px; }
.scope-val { font-size: 12px; color: var(--ink-2); word-break: break-all; }

/* §6.1 persona x task + §6.2 prioritization tables */
.pt-wrap { overflow-x: auto; }
.pt { width: 100%; border-collapse: collapse; font-size: 13px; }
.pt th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted-2); font-weight: 700; padding: 6px 10px; border-bottom: 1px solid var(--line, #e7e9ee); white-space: nowrap; }
.pt td { padding: 9px 10px; border-bottom: 1px solid var(--line-soft, #f0f1f4); vertical-align: top; }
.pt th.num, .pt td.num { text-align: right; }
.pt-name { font-weight: 600; }
.pt-home { font-size: 11px; color: var(--muted-2); margin-top: 2px; }
.pt-task { color: var(--ink-2); max-width: 260px; }
.pt-outcome { font-weight: 600; }
/* provenance markers — small, but they are the difference between a claim and
   an observation, so they sit next to the value they qualify */
.pt-src { font-size: 9px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted-2);
  border: 1px solid var(--line, #e7e9ee); border-radius: 4px; padding: 1px 4px; margin-left: 6px; cursor: help; }
.pt-src.measured { color: #1f9d57; border-color: #bfe6cf; background: #f1faf4; }
.pt-src.disagree { color: #b5701a; border-color: #f0d9b0; background: #fdf3e3; }
.pri-ev { font-size: 11px; color: var(--muted-2); }
.pill { font-size: 11px; font-weight: 700; border-radius: 5px; padding: 2px 8px; white-space: nowrap; }
.pill.high { background: #fdeceb; color: #d83a34; }
.pill.medium { background: #fdf3e3; color: #b5701a; }
.pill.low { background: #eef0f3; color: #6b7280; }

/* §6.1 evidence-status legend */
.legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 0 0 14px; padding: 10px 12px;
  background: var(--surface-2, #f7f8fa); border: 1px solid var(--line, #e7e9ee); border-radius: 8px; }
.legend-item { display: flex; align-items: baseline; gap: 6px; font-size: 12px; }
.legend-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; transform: translateY(-1px); }
.legend-name { font-weight: 700; color: var(--ink-2); }
.legend-desc { color: var(--muted-2); }

/* §6.1 sections this tool deliberately does not produce */
.oos { display: flex; flex-direction: column; gap: 12px; }
.oos-item { border-left: 3px solid var(--line, #e7e9ee); padding-left: 12px; }
.oos-name { font-weight: 700; font-size: 13px; margin-bottom: 4px; }
</style>
