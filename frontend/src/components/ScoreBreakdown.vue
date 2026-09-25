<template>
  <!-- The dashboard's copy of reporter_html._render_score_breakdown ("Jak
       obliczono ocenę"): same lines, same order — start, each deduction with its
       basis, the total, the final score (with the floor when it applied), what
       was left out, the rules. Labels and bases are Polish data from
       scoring.py, as in the report; only the chrome is translated. -->
  <div class="sb">
    <table class="sb-table">
      <tbody>
        <tr class="sb-start">
          <td>{{ t('rep.sb.start') }}</td>
          <td></td>
          <td class="sb-amt mono">{{ f1(b.start) }}</td>
        </tr>
        <tr v-for="(d, i) in deductions" :key="i">
          <td>
            <span v-if="kindLabel(d.kind)" class="sb-kind mono">{{ kindLabel(d.kind) }}</span>
            <span class="sb-label">{{ d.label }}</span>
          </td>
          <td class="sb-basis">{{ d.basis }}</td>
          <td class="sb-amt mono">{{ signed(d.amount) }}</td>
        </tr>
        <tr class="sb-sum">
          <td>{{ t('rep.sb.sum') }}</td>
          <td></td>
          <td class="sb-amt mono">−{{ f2(b.total_deduction) }}</td>
        </tr>
        <tr class="sb-final">
          <td>{{ t('rep.sb.final') }}</td>
          <td class="sb-basis">
            {{ t('rep.sb.finalBasis', { start: f1(b.start), total: f2(b.total_deduction), raw: f2(b.raw) }) }}<template
              v-if="b.floored">{{ t('rep.sb.floored', { floor: floorText }) }}</template>
          </td>
          <td class="sb-amt mono" :style="{ color: scoreColor(b.score) }">{{ f1(b.score) }}</td>
        </tr>
      </tbody>
    </table>

    <template v-if="notCounted.length">
      <div class="sb-left-h mono">{{ t('rep.sb.notCounted') }}</div>
      <ul class="sb-left">
        <li v-for="(n, i) in notCounted" :key="i"><b>{{ n.label }}</b> — {{ n.reason }}</li>
      </ul>
    </template>

    <p v-if="rulesNote" class="sb-rules">{{ rulesNote }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { scoreColor } from '../theme'
import { useI18n } from '../i18n'
import type { ScoreBreakdown } from '../types'

const props = defineProps<{ breakdown: ScoreBreakdown }>()
const { t, locale } = useI18n()

const b = computed(() => props.breakdown)
const deductions = computed(() => (b.value.deductions ?? []).filter(d => d && typeof d === 'object'))
const notCounted = computed(() => (b.value.not_counted ?? []).filter(n => n && typeof n === 'object'))
// scoring.FLOOR, as the breakdown recorded it (every stored breakdown carries
// it; the report prints the same constant, as "1,0" in its Polish sentence).
const floorText = computed(() => {
  const s = f1(typeof b.value.floor === 'number' ? b.value.floor : 1)
  return locale.value === 'pl' ? s.replace('.', ',') : s
})

// reporter_html._DEDUCTION_KIND_PL. An unknown kind gets no tag there, so it
// gets none here either (t() would otherwise print the raw key).
const KINDS = ['issue', 'task', 'accessibility', 'performance', 'adjustment']
function kindLabel(kind: string | undefined): string {
  return kind && KINDS.includes(kind) ? t('rep.sb.kind.' + kind) : ''
}

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const f1 = (v: unknown) => num(v).toFixed(1)
const f2 = (v: unknown) => num(v).toFixed(2)
// Same sign rule as the report: a negative amount gives points back.
const signed = (v: unknown) => (num(v) >= 0 ? '−' : '+') + Math.abs(num(v)).toFixed(2)

// The report fills the rule note from rules via Python str.format, so a whole
// float prints as "1.0"; String(1.0) in JS would print "1".
function pyNum(v: unknown): string {
  if (typeof v !== 'number') return String(v ?? '')
  return Number.isInteger(v) ? v.toFixed(1) : String(v)
}
const rulesNote = computed(() => {
  const issue = b.value.rules?.issue
  const task = b.value.rules?.task
  if (!issue || !task) return ''
  return t('rep.sb.rules', {
    k: pyNum(issue['krytyczny']), p: pyNum(issue['poważny']), d: pyNum(issue['drobny']),
    tf: pyNum(task['failed']), tp: pyNum(task['partial']),
  })
})
</script>

<style scoped lang="scss">
.sb { background: #fff; border: 1px solid var(--border); border-radius: 11px; padding: 16px 18px; }
.sb-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.sb-table td { padding: 8px 10px; border-bottom: 1px solid var(--border-2); vertical-align: top; color: var(--ink-2); }
.sb-kind { display: inline-block; font-size: 9.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted-2);
  border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; margin-right: 7px; }
.sb-label { font-weight: 600; }
.sb-basis { color: var(--muted); font-size: 12.5px; line-height: 1.5; }
.sb-amt { text-align: right; white-space: nowrap; font-weight: 700; width: 70px; }
.sb-start td, .sb-sum td { font-weight: 700; background: var(--surface-2, #f7f8fa); }
.sb-final td { font-weight: 700; border-bottom: none; }
.sb-final .sb-amt { font-size: 17px; }
.sb-final .sb-basis { font-weight: 400; }
.sb-left-h { font: 600 10px var(--mono); letter-spacing: .05em; text-transform: uppercase; color: var(--muted-2); margin: 16px 0 8px; }
.sb-left { margin: 0; padding-left: 18px; font-size: 13px; color: var(--muted); line-height: 1.55; }
.sb-left b { color: var(--ink-2); }
.sb-rules { margin: 14px 0 0; font-size: 12px; color: var(--muted-2); line-height: 1.55; }
</style>
