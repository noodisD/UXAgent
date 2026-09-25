<template>
  <div class="wizard">
    <!-- stepper -->
    <div class="stepper">
      <div v-for="(st, i) in steps" :key="st.n" class="step-item">
        <button class="step-btn" @click="goStep(st.n)">
          <span class="circle" :class="{ done: st.done, active: st.active }">
            <span v-if="st.done">✓</span><span v-else>{{ st.n }}</span>
          </span>
          <span class="step-label" :class="{ dim: !st.done && !st.active }">{{ st.label }}</span>
        </button>
        <span v-if="i < steps.length - 1" class="connector" :class="{ on: st.done }"></span>
      </div>
    </div>

    <!-- STEP 1 -->
    <div v-if="app.step === 1" class="grid-2col">
    <div class="col">
      <div class="ux-card">
        <div class="row-between">
          <div>
            <div class="card-title">{{ t('w1.title') }}</div>
            <div class="card-sub">{{ t('w1.sub') }}</div>
          </div>
          <button class="btn small sus gen-btn" :disabled="fillLoading || !form.experiment.startUrl" @click="fillSettings">
            <span v-if="fillLoading" class="spinner"></span>
            {{ fillLoading ? t('w1.filling') : t('w1.fillWithAI') }}
          </button>
        </div>
        <div v-if="fillError" class="gen-err">{{ fillError }}</div>
        <div class="grid-url">
          <div>
            <label class="ux-label">{{ t('w1.url') }}</label>
            <input class="ux-input mono" v-model="form.experiment.startUrl" placeholder="https://example.com" />
            <div v-if="urlError" class="url-err">⚠ {{ t('w1.urlInvalid') }}</div>
            <div v-else-if="urlDomain" class="url-chip">
              <span class="url-fav mono">{{ urlDomain.slice(0, 2).toUpperCase() }}</span>
              <span class="url-dom mono">{{ urlDomain }}</span>
            </div>
          </div>
          <div>
            <label class="ux-label">{{ t('w1.participants') }}</label>
            <div class="stepper-num">
              <button @click="dec">–</button>
              <input class="num" type="number" :min="MIN_PERSONAS" :max="MAX_PERSONAS"
                :value="form.persona.numSubjects" @change="onPersonaCount" />
              <button @click="inc">+</button>
            </div>
          </div>
        </div>
        <div class="mt16">
          <label class="ux-label">{{ t('w1.task') }}</label>
          <textarea class="ux-input" rows="2" v-model="form.persona.generalIntent"
            :placeholder="t('w1.task.ph')"></textarea>
        </div>
        <div class="mt16">
          <label class="ux-label">{{ t('w1.siteContext') }} <span class="opt">{{ t('w1.optional') }}</span></label>
          <select class="ux-input" :value="presetKey" @change="onPresetChange">
            <option value="">{{ t('w2.preset.choose') }}</option>
            <option v-for="k in presetKeys" :key="k" :value="k">{{ t('w2.preset.' + k) }}</option>
          </select>
        </div>
        <div class="grid-url mt16">
          <div>
            <label class="ux-label">{{ t('w1.maxSteps') }}</label>
            <input class="ux-input mono" type="number" min="1" v-model.number="form.experiment.maxSteps" />
          </div>
          <div>
            <label class="ux-label">{{ t('w1.concurrency') }}</label>
            <input class="ux-input mono" type="number" min="1" v-model.number="form.experiment.concurrency" />
          </div>
        </div>
        <div class="mt16">
          <label class="ux-label">{{ t('w1.device') }}</label>
          <div class="device-pick">
            <button v-for="d in deviceChoices" :key="d.value" type="button"
                    class="device-btn" :class="{ on: form.experiment.device === d.value }"
                    @click="form.experiment.device = d.value">
              <span class="device-ico">{{ d.icon }}</span>{{ d.label }}
            </button>
          </div>
          <div class="device-note">{{ t('w1.device.note') }}</div>
        </div>
      </div>

      <!-- demographics -->
      <div class="ux-card">
        <div class="row-between">
          <div class="card-title">{{ t('w1.demographics') }}</div>
          <span class="hint mono">{{ t('w1.demographics.tag') }}</span>
        </div>
        <div class="card-sub">{{ t('w1.demographics.sub') }}</div>
        <div class="col gap14">
          <div v-for="(d, i) in form.persona.demographics" :key="i" class="demo-card">
            <div class="demo-head">
              <input class="ux-input demo-name" v-model="d.name" />
              <button class="icon-del" @click="removeDemo(i)">×</button>
            </div>
            <div class="col gap7">
              <div v-for="(c, j) in d.choices" :key="j" class="choice">
                <input class="ux-input choice-name" v-model="c.name" />
                <div class="wt">
                  <span class="wt-label mono">wt</span>
                  <input class="wt-input mono" type="number" min="0" v-model.number="c.weight" />
                </div>
                <span class="pct mono">{{ pct(d, c) }}%</span>
                <button class="icon-x" @click="removeChoice(d, j)">×</button>
              </div>
            </div>
            <button class="link-btn" @click="addChoice(d)">{{ t('w1.addOption') }}</button>
          </div>
        </div>
        <button class="add-dim" @click="addDemo">{{ t('w1.addDimension') }}</button>
      </div>
    </div>

    <!-- right sticky column -->
    <div class="col sticky-col">
      <!-- example persona -->
      <div class="ux-card">
        <div class="row-between">
          <div class="card-title">{{ t('w1.example') }} <span class="opt">{{ t('w1.optional') }}</span></div>
          <button class="btn small sus gen-btn" :disabled="genLoading" @click="generatePersona">
            <span v-if="genLoading" class="spinner"></span>
            {{ genLoading ? t('w1.generating') : t('w1.generate') }}
          </button>
        </div>
        <div class="card-sub">{{ t('w1.example.sub') }}</div>
        <textarea class="ux-input mono ta-mono" rows="8" v-model="form.persona.examplePersona"
          :placeholder="t('w1.example.ph')"></textarea>
        <div v-if="genError" class="gen-err">{{ genError }}</div>
      </div>

      <!-- run summary -->
      <div class="ux-card">
        <div class="card-title sm">{{ t('w1.summary') }}</div>
        <div class="sum-rows">
          <div class="sum-row"><span>{{ t('w3.personas') }}</span><span class="sum-val mono">{{ form.persona.numSubjects }}</span></div>
          <div class="sum-row"><span>{{ t('w1.demographics') }}</span><span class="sum-val mono">{{ form.persona.demographics.length }}</span></div>
          <div class="sum-row"><span>{{ t('w1.maxSteps') }}</span><span class="sum-val mono">{{ form.experiment.maxSteps }}</span></div>
        </div>
        <div class="sum-note">{{ t('w1.summaryNote', { n: form.experiment.maxSteps }) }}</div>
      </div>
    </div>
    </div>

    <!-- STEP 2: PERSONAS -->
    <div v-else-if="app.step === 2" class="col">
      <div class="ux-card">
        <div class="card-title">{{ t('w2p.title') }}</div>
        <div class="card-sub">{{ t('w2p.sub') }}</div>
        <div class="col gap14">
          <div v-for="(slot, i) in form.persona.slots" :key="i" class="demo-card">
            <div class="row-between">
              <div class="slot-title">{{ t('w2p.persona', { n: i + 1 }) }}</div>
              <div class="mode-toggle">
                <button class="btn small" :class="{ 'mode-on': slot.mode === 'auto' }"
                  @click="slot.mode = 'auto'">{{ t('w2p.auto') }}</button>
                <button class="btn small" :class="{ 'mode-on': slot.mode === 'manual' }"
                  @click="slot.mode = 'manual'">{{ t('w2p.manual') }}</button>
              </div>
            </div>
            <div v-if="slot.mode === 'auto'" class="slot-hint">{{ t('w2p.autoHint') }}</div>
            <template v-else>
              <div class="row-between mt10">
                <div class="slot-hint">{{ t('w2p.nameHint') }}</div>
                <button class="btn small sus gen-btn" :disabled="slotGenLoading === i"
                  @click="generateSlotPersona(i)">
                  <span v-if="slotGenLoading === i" class="spinner"></span>
                  {{ slotGenLoading === i ? t('w1.generating') : t('w1.generate') }}
                </button>
              </div>
              <textarea class="ux-input mono ta-mono mt10" rows="10" v-model="slot.description"
                :placeholder="t('w2p.manualPh')"></textarea>
            </template>
          </div>
        </div>
        <div v-if="slotError" class="gen-err">{{ slotError }}</div>
      </div>
    </div>

    <!-- STEP 3: SURVEY -->
    <div v-else-if="app.step === 3" class="col">
      <div class="ux-card">
        <div class="row-between gap16 mb18">
          <div>
            <div class="card-title">{{ t('w2.title') }}</div>
            <div class="card-sub nomb">{{ t('w2.sub') }}</div>
          </div>
          <div class="q-actions">
            <span class="q-count mono">{{ t('w2.count', { n: questions.length }) }}</span>
            <button class="btn small" @click="addOpen">{{ t('w2.addOpen') }}</button>
            <button class="btn small" @click="addMC">{{ t('w2.addMC') }}</button>
            <button class="btn small sus" @click="insertSUS">{{ t('w2.insertSUS') }}</button>
          </div>
        </div>

        <!-- category preset selector: loads a purpose-calibrated question set -->
        <div class="preset-row">
          <label class="preset-label mono">{{ t('w2.preset.label') }}</label>
          <select class="preset-select" :value="presetKey" @change="onPresetChange">
            <option value="">{{ t('w2.preset.choose') }}</option>
            <option v-for="k in presetKeys" :key="k" :value="k">{{ t('w2.preset.' + k) }}</option>
          </select>
          <span class="preset-hint">{{ t('w2.preset.hint') }}</span>
        </div>

        <div v-if="questions.length === 0" class="empty">{{ t('w2.empty') }}</div>

        <div class="col gap12">
          <div v-for="(q, i) in questions" :key="i" class="q-card"
            :ref="el => setQuestionRef(el, i)" :class="{ 'just-added': addedFrom !== null && i >= addedFrom }">
            <div class="q-head">
              <span class="q-num mono">{{ String(i + 1).padStart(2, '0') }}</span>
              <span class="badge mono" :class="q.type === 'multiple_choice' ? 'mc' : 'tx'">
                {{ q.type === 'multiple_choice' ? t('w2.typeMC') : t('w2.typeOpen') }}
              </span>
              <input class="ux-input q-prompt" v-model="q.prompt" />
              <button class="icon-del" @click="removeQuestion(i)">×</button>
            </div>
            <!-- `q.options` is optional on the Q type and independent of `q.type`,
                 so the type check is on the field itself, not on the kind. -->
            <div v-if="q.type === 'multiple_choice' && q.options" class="q-options">
              <div v-for="(_o, j) in q.options" :key="j" class="q-opt">
                <span class="radio"></span>
                <input class="ux-input opt-input" v-model="q.options[j]" />
                <button class="icon-x" @click="q.options.splice(j, 1)">×</button>
              </div>
              <button class="link-btn ml23" @click="q.options.push('New option')">{{ t('w1.addOption') }}</button>
            </div>
            <div v-else class="q-textph">{{ t('w2.optionPh') }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- STEP 4: REVIEW -->
    <div v-else class="col">
      <div class="ux-card">
        <div class="card-title mb18">{{ t('w3.title') }}</div>
        <div class="grid2">
          <div class="mini">
            <div class="ux-label">{{ t('w3.target') }}</div>
            <div class="mono target-url">{{ form.experiment.startUrl || '—' }}</div>
            <div class="target-intent">{{ form.persona.generalIntent }}</div>
            <div v-if="form.experiment.siteContext" class="target-intent muted">{{ form.experiment.siteContext }}</div>
            <div class="target-intent">{{ t('w3.slotSummary', { a: autoCount, m: manualCount }) }}</div>
          </div>
          <div class="mini stats">
            <div><div class="stat mono">{{ form.persona.numSubjects }}</div><div class="stat-l">{{ t('w3.personas') }}</div></div>
            <div><div class="stat mono">{{ form.experiment.maxSteps }}</div><div class="stat-l">{{ t('w3.maxSteps') }}</div></div>
            <div><div class="stat mono">{{ questions.length }}</div><div class="stat-l">{{ t('w3.questions') }}</div></div>
          </div>
        </div>
      </div>

      <div class="ux-card">
        <div class="card-title mb16">{{ t('w3.distribution') }}</div>
        <div class="col gap18">
          <div v-for="(d, i) in form.persona.demographics" :key="i">
            <div class="dist-name">{{ d.name }}</div>
            <div class="col gap7">
              <div v-for="(c, j) in d.choices" :key="j" class="dist-row">
                <div class="dist-label">{{ c.name }}</div>
                <div class="dist-bar"><div class="dist-fill" :style="{ width: pct(d, c) + '%' }">
                  <span class="dist-pct mono">{{ pct(d, c) }}%</span></div></div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="ux-card">
        <div class="card-title mb14">{{ t('w3.questionnaire', { n: questions.length }) }}</div>
        <div class="col gap9">
          <div v-for="(q, i) in questions" :key="i" class="q-review">
            <span class="q-num mono">{{ String(i + 1).padStart(2, '0') }}</span>
            <span class="badge mono small-badge" :class="q.type === 'multiple_choice' ? 'mc' : 'tx'">
              {{ q.type === 'multiple_choice' ? 'MC' : 'TXT' }}</span>
            <span class="q-review-text">{{ q.prompt }}</span>
          </div>
        </div>
      </div>

      <div class="confirm-card">
        <div class="confirm-text">
          <div v-if="runBlockedReason" class="block-reason">⚠ {{ runBlockedReason }}</div>
          <template v-else>{{ t('w3.confirmText', { n: form.persona.numSubjects }) }}</template>
        </div>
        <button class="btn-run" :disabled="launching || !!runBlockedReason"
                :title="runBlockedReason || ''" @click="confirmRun">
          {{ launching ? t('w3.starting') : t('w3.confirmRun') }}
        </button>
      </div>
      <div v-if="launchError" class="launch-err">{{ launchError }}</div>
    </div>

    <!-- footer nav -->
    <div class="wiz-footer">
      <div class="foot-left">
        <button class="btn" :style="{ visibility: app.step === 1 ? 'hidden' : 'visible' }" @click="prevStep">{{ t('wizard.back') }}</button>
        <button class="btn small reset-btn" @click="onReset" :title="t('wizard.reset.hint')">{{ t('wizard.reset') }}</button>
      </div>
      <div class="step-count mono">{{ t('wizard.step', { n: app.step }) }}</div>
      <button v-if="app.step < 4" class="btn-primary nav-next" @click="tryNext">{{ t('wizard.continue') }}</button>
      <div v-else class="nav-spacer"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject } from 'vue'
import { apiFetch } from '../api'
import { FORM_KEY, type WizardForm } from '../types'
import { useApp } from '../composables/useApp'
import { useRun } from '../composables/useRun'
import { resetWizardForm } from '../composables/useForm'
import { useI18n } from '../i18n'
import { PRESET_KEYS, PRESET_SITE_CONTEXT, presetQuestions, type PresetKey } from '../surveyPresets'
import { nextTick, ref } from 'vue'

const form = inject<WizardForm>(FORM_KEY)!
const { state: app, nextStep, prevStep, goStep, goScreen } = useApp()
const { start } = useRun()
const { t } = useI18n()

// "both" alternates personas between desktop and mobile rather than running
// each one twice, so choosing it costs no extra time — it splits the panel.
// The labels say so, because the obvious assumption is that it doubles the run.
const deviceChoices = computed(() => ([
  { value: 'desktop' as const, icon: '🖥', label: t('w1.device.desktop') },
  { value: 'mobile' as const, icon: '📱', label: t('w1.device.mobile') },
  { value: 'both' as const, icon: '🖥📱', label: t('w1.device.both') },
]))

// One rule for what counts as a usable start URL, mirroring the backend's
// check in app.py. There used to be three: this gate required a scheme, the
// inline field feedback below accepted a bare host (silently prepending
// https:// just to parse it), and the backend had a third regex — so typing
// "example.com" showed a green valid-domain chip AND blocked the Run button.
// A run with an empty/invalid URL launches happily and dies on Page.goto(""),
// burning every session, so this must stay strict.
const START_URL_RE = /^https?:\/\/[^\s.]+\.[^\s]+$/i

function startUrlOk(v: string): boolean {
  return START_URL_RE.test(v.trim())
}

const runBlockedReason = computed<string | null>(() => {
  const url = (form.experiment.startUrl || '').trim()
  if (!url) return t('w3.needUrl')
  if (!startUrlOk(url)) return t('w3.badUrl')
  if (!(form.persona.generalIntent || '').trim()) return t('w3.needIntent')
  return null
})

const launching = ref(false)
const launchError = ref<string | null>(null)

const genLoading = ref(false)
const genError = ref<string | null>(null)

const fillLoading = ref(false)
const fillError = ref<string | null>(null)

// URL validation + domain chip (design: inline feedback under the URL field)
const urlError = computed(() => {
  const v = form.experiment.startUrl.trim()
  if (!v) return false
  return !startUrlOk(v)
})
const urlDomain = computed(() => {
  const v = form.experiment.startUrl.trim()
  if (!v || urlError.value) return ''
  try { return new URL(v).hostname.replace(/^www\./, '') } catch { return '' }
})

async function fillSettings() {
  fillLoading.value = true
  fillError.value = null
  try {
    const res = await apiFetch('/generate-audit-settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start_url: form.experiment.startUrl }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`)
    if (data.general_intent) form.persona.generalIntent = data.general_intent
    // NOT data.site_context: siteContext is now driven by the category select
    // (presetKey), which must always hold one of the known preset texts or empty —
    // writing an arbitrary AI sentence here would desync the dropdown from the
    // text silently applied underneath it.
    if (data.num_subjects) form.persona.numSubjects = Math.min(MAX_PERSONAS, Math.max(MIN_PERSONAS, Number(data.num_subjects)))
    if (data.max_steps) form.experiment.maxSteps = data.max_steps
    if (Array.isArray(data.demographics) && data.demographics.length) {
      form.persona.demographics = data.demographics.map((d: any) => ({
        name: d.name,
        choices: (d.choices || []).map((c: any) => ({ name: c.name, weight: c.weight ?? 1 })),
      }))
    }
  } catch (e: any) {
    fillError.value = e?.message ?? 'Failed to generate settings'
  } finally {
    fillLoading.value = false
  }
}

async function generatePersona() {
  genLoading.value = true
  genError.value = null
  try {
    const res = await apiFetch('/generate-example-persona', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        start_url: form.experiment.startUrl,
        general_intent: form.persona.generalIntent,
        demographics: form.persona.demographics,
        site_context: form.experiment.siteContext,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`)
    if (!data.persona) throw new Error('Empty response from server')
    form.persona.examplePersona = data.persona
  } catch (e: any) {
    genError.value = e?.message ?? 'Failed to generate persona'
  } finally {
    genLoading.value = false
  }
}

const steps = computed(() => [
  { n: 1, label: t('step.recruitment') },
  { n: 2, label: t('step.personas') },
  { n: 3, label: t('step.survey') },
  { n: 4, label: t('step.review') },
].map(s => ({ ...s, done: s.n < app.step, active: s.n === app.step })))

const autoCount = computed(() => form.persona.slots.filter(s => s.mode === 'auto').length)
const manualCount = computed(() => form.persona.slots.filter(s => s.mode === 'manual').length)

const slotGenLoading = ref<number | null>(null)
const slotError = ref<string | null>(null)

async function generateSlotPersona(i: number) {
  slotGenLoading.value = i
  slotError.value = null
  try {
    const res = await apiFetch('/generate-example-persona', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        start_url: form.experiment.startUrl,
        general_intent: form.persona.generalIntent,
        demographics: form.persona.demographics,
        site_context: form.experiment.siteContext,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`)
    if (!data.persona) throw new Error('Empty response from server')
    form.persona.slots[i].description = data.persona
  } catch (e: any) {
    slotError.value = e?.message ?? 'Failed to generate persona'
  } finally {
    slotGenLoading.value = null
  }
}

function tryNext() {
  if (app.step === 2) {
    const missing = form.persona.slots.some(s => s.mode === 'manual' && !s.description.trim())
    if (missing) {
      slotError.value = t('w2p.required')
      return
    }
    slotError.value = null
  }
  nextStep()
}

const MIN_PERSONAS = 1
const MAX_PERSONAS = 50

function inc() { if (form.persona.numSubjects < MAX_PERSONAS) form.persona.numSubjects++ }
function dec() { if (form.persona.numSubjects > MIN_PERSONAS) form.persona.numSubjects-- }
// Typed entry — clicking + up to 50 is unusable, so the field is editable and clamped.
function onPersonaCount(e: Event) {
  const raw = Number((e.target as HTMLInputElement).value)
  const n = Number.isFinite(raw) ? Math.round(raw) : MIN_PERSONAS
  form.persona.numSubjects = Math.min(MAX_PERSONAS, Math.max(MIN_PERSONAS, n))
  ;(e.target as HTMLInputElement).value = String(form.persona.numSubjects)
}

function pct(d: any, c: any): number {
  const total = d.choices.reduce((a: number, x: any) => a + (+x.weight || 0), 0) || 1
  return Math.round(((+c.weight || 0) / total) * 100)
}

function addDemo() {
  form.persona.demographics.push({ name: 'New dimension', choices: [{ name: 'Option A', weight: 1 }, { name: 'Option B', weight: 1 }] })
}
function removeDemo(i: number) { form.persona.demographics.splice(i, 1) }
function addChoice(d: any) { d.choices.push({ name: 'New option', weight: 1 }) }
function removeChoice(d: any, j: number) { if (d.choices.length > 1) d.choices.splice(j, 1) }

// Questions live in the survey questionnaire JSON string; mirror to a working array.
type Q = { id: string; type: 'short_text' | 'multiple_choice'; prompt: string; options?: string[] }

const questions = computed<Q[]>({
  get() {
    try {
      const obj = JSON.parse(form.survey.questionnaire || '{}')
      return Array.isArray(obj.questions) ? obj.questions : []
    } catch { return [] }
  },
  set(list) {
    let obj: any = {}
    try { obj = JSON.parse(form.survey.questionnaire || '{}') } catch { obj = {} }
    obj.questionnaire_id = obj.questionnaire_id || 'survey_v1'
    obj.title = obj.title || 'Post-session survey'
    obj.questions = list
    form.survey.questionnaire = JSON.stringify(obj, null, 2)
  },
})

function writeQuestions(list: Q[]) { questions.value = list }

// Adding a question used to change nothing visible: the new card is appended
// below the fold, the page does not move, and nothing confirms the click. The
// first external reviewer reported exactly that — she only discovered the
// questions had been added by scrolling down. Scroll the first new card into
// view and flash it, so the click has a visible consequence.
const questionEls = new Map<number, HTMLElement>()
const addedFrom = ref<number | null>(null)
let flashTimer: number | undefined

function setQuestionRef(el: unknown, i: number) {
  if (el instanceof HTMLElement) questionEls.set(i, el)
  else questionEls.delete(i)
}

async function revealAddedFrom(firstNew: number) {
  addedFrom.value = firstNew
  await nextTick()
  questionEls.get(firstNew)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  clearTimeout(flashTimer)
  flashTimer = window.setTimeout(() => { addedFrom.value = null }, 1600)
}

// Ids must be unique: the report keys answers by id. 'q' + (length + 1) reused
// one after any deletion but the last — delete q1, add one, and the new question
// became a second q3 — filing one question's ratings under another's prompt.
function freshId(prefix: string, taken: Set<string>): string {
  let n = 1
  while (taken.has(prefix + n)) n++
  taken.add(prefix + n)
  return prefix + n
}
const takenIds = () => new Set(questions.value.map(q => String(q.id)))
function addOpen() {
  const at = questions.value.length
  writeQuestions([...questions.value, { id: freshId('q', takenIds()), type: 'short_text', prompt: 'New open-ended question' }])
  revealAddedFrom(at)
}
function addMC() {
  const at = questions.value.length
  writeQuestions([...questions.value, { id: freshId('q', takenIds()), type: 'multiple_choice', prompt: 'New multiple-choice question', options: ['Option 1', 'Option 2', 'Option 3'] }])
  revealAddedFrom(at)
}
function removeQuestion(i: number) { const l = questions.value.slice(); l.splice(i, 1); writeQuestions(l) }
function onReset() {
  resetWizardForm(form)
  presetKey.value = ''
  goStep(1)
}

const presetKeys = PRESET_KEYS

// One category drives both the site-description text (step 1) and the survey
// question set (step 2) — they were always the same choice ("this is a
// catalog site") shown twice. siteContext is a plain string, not a select
// value, so on load we recover the dropdown's position by matching it back
// against the known preset texts; an old free-typed description (pre-select
// UI) simply won't match anything and the dropdown shows unselected while the
// text itself is still sent as-is.
const presetKey = ref<string>(
  (Object.entries(PRESET_SITE_CONTEXT) as [PresetKey, string][])
    .find(([, text]) => text === form.experiment.siteContext)?.[0] ?? ''
)

function applySitePreset(key: PresetKey | '') {
  presetKey.value = key
  form.experiment.siteContext = key ? PRESET_SITE_CONTEXT[key] : ''
  if (key) writeQuestions(presetQuestions(key) as Q[])
}
function onPresetChange(e: Event) {
  applySitePreset((e.target as HTMLSelectElement).value as PresetKey | '')
}

function insertSUS() {
  const sus = [
    'I think I would like to use this site frequently.',
    'I found the site unnecessarily complex.',
    'I thought the site was easy to use.',
    'I think I would need support to use this site.',
    'I found the various functions well integrated.',
    'I thought there was too much inconsistency in this site.',
    'I would imagine most people would learn this very quickly.',
    'I found the site very cumbersome to use.',
    'I felt very confident using the site.',
    'I needed to learn a lot before I could get going.',
  ]
  const base = questions.value.slice()
  const at = base.length
  // Already there: a second copy would only duplicate every SUS item.
  const present = base.findIndex(q => q.prompt === sus[0])
  if (present >= 0 && sus.every(p => base.some(q => q.prompt === p))) {
    revealAddedFrom(present)
    return
  }
  const taken = takenIds()
  sus.forEach(p => base.push({ id: freshId('sus', taken), type: 'multiple_choice', prompt: p, options: ['1', '2', '3', '4', '5'] }))
  writeQuestions(base)
  revealAddedFrom(at)
}

async function confirmRun() {
  launching.value = true
  launchError.value = null
  try {
    await start(form)
    goScreen('running')
  } catch (e: any) {
    launchError.value = e?.message ?? 'Failed to start run'
  } finally {
    launching.value = false
  }
}
</script>

<style scoped lang="scss">
.wizard { width: 100%; padding: 30px 36px 140px; }
.col { display: flex; flex-direction: column; gap: 18px; }
.grid-2col { display: grid; grid-template-columns: 1.7fr 1fr; gap: 18px; align-items: start; }
.sticky-col { position: sticky; top: 0; }
.url-err { display: flex; align-items: center; gap: 6px; margin-top: 7px; font: 500 12px var(--sans); color: var(--ac); }
.url-chip { display: inline-flex; align-items: center; gap: 7px; margin-top: 7px; padding: 4px 10px 4px 5px; background: var(--surface-2); border: 1px solid var(--border-2); border-radius: 20px; white-space: nowrap; }
.url-fav { width: 20px; height: 20px; border-radius: 5px; background: var(--surface-3); display: inline-flex; align-items: center; justify-content: center; font: 700 9px var(--mono); color: var(--text-3); }
.url-dom { font: 600 12px var(--mono); color: var(--text-3); }
.card-title.sm { font-size: 15px; margin-bottom: 14px; }
.sum-rows { display: flex; flex-direction: column; gap: 12px; }
.sum-row { display: flex; justify-content: space-between; align-items: center; font-size: 13px; color: var(--text-muted); }
.sum-val { font: 700 16px var(--mono); color: var(--text); }
.sum-note { border-top: 1px solid var(--border-2); margin-top: 16px; padding-top: 14px; font-size: 12.5px; color: var(--text-muted); line-height: 1.5; }
.gap14 { gap: 14px; } .gap12 { gap: 12px; } .gap9 { gap: 9px; } .gap7 { gap: 7px; } .gap18 { gap: 18px; } .gap16 { gap: 16px; }
.mt16 { margin-top: 16px; } .mb18 { margin-bottom: 18px; } .mb16 { margin-bottom: 16px; } .mb14 { margin-bottom: 14px; } .nomb { margin-bottom: 0; }

.stepper { display: flex; align-items: center; margin-bottom: 32px; }
.step-item { display: flex; align-items: center; flex: 1; }
.step-btn { display: flex; align-items: center; gap: 10px; background: transparent; border: none; cursor: pointer; padding: 0; }
.circle { width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  font: 700 13px var(--mono); background: #eceef1; color: var(--faint); }
.circle.done { background: var(--ac); color: #fff; }
.circle.active { background: var(--ac); color: #fff; box-shadow: 0 0 0 4px var(--ac-shadow); }
.step-label { font: 600 13.5px var(--sans); white-space: nowrap; color: var(--ink); }
.step-label.dim { color: var(--faint); }
.connector { flex: 1; height: 2px; margin: 0 14px; border-radius: 1px; background: #e2e4e8; }
.connector.on { background: var(--ac); }

.card-title { font-weight: 700; font-size: 16px; margin-bottom: 3px; }
.card-sub { font-size: 13px; color: var(--muted-2); margin-bottom: 20px; }
.opt { font: 500 12px var(--sans); color: var(--faint); }
.row-between { display: flex; align-items: flex-start; justify-content: space-between; }
.hint { font-size: 11px; color: #9aa0a8; }

.grid-url { display: grid; grid-template-columns: 1fr 200px; gap: 16px; }
.stepper-num { display: flex; align-items: center; border: 1px solid var(--border-input); border-radius: 8px; overflow: hidden; background: var(--surface); }
.stepper-num button { width: 42px; height: 43px; border: none; background: var(--surface-3); color: var(--text-3); font-size: 20px; cursor: pointer; flex: none; }
.stepper-num button:hover { filter: brightness(.96); }
.stepper-num .num { flex: 1; min-width: 0; width: 100%; text-align: center; font: 700 16px var(--mono); color: var(--text);
  border: none; background: transparent; outline: none; padding: 0; -moz-appearance: textfield; }
.stepper-num .num::-webkit-outer-spin-button, .stepper-num .num::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.ta-mono { font: 400 13.5px/1.6 var(--mono); background: #fcfcfd; resize: vertical; }
.gen-btn { gap: 7px; }
.gen-btn:disabled { opacity: .65; cursor: default; }
.spinner { width: 12px; height: 12px; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; display: inline-block; animation: uxspin .7s linear infinite; }
.gen-err { color: #b5564f; font-size: 12.5px; margin-top: 8px; }

.demo-card { border: 1px solid var(--border-soft); border-radius: 10px; padding: 14px; background: #fcfcfd; }
.slot-title { font: 700 14px var(--sans); }
.slot-hint { font-size: 12.5px; color: var(--muted-2); }
.mode-toggle { display: flex; gap: 6px; }
.mode-on { background: var(--ac); color: #fff; border-color: var(--ac); }
.mt10 { margin-top: 10px; }
.demo-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.demo-name { font: 600 14px var(--sans); }
.choice { display: flex; align-items: center; gap: 9px; }
.choice-name { padding: 7px 10px; font-size: 13.5px; }
.wt { display: flex; align-items: center; gap: 6px; background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 0 4px 0 9px; }
.wt-label { font-size: 10px; color: #9aa0a8; }
.wt-input { width: 46px; padding: 7px 4px; border: none; font: 600 13.5px var(--mono); text-align: center; box-shadow: none; }
.wt-input:focus { box-shadow: none; }
.pct { font-size: 11px; color: var(--faint); width: 38px; text-align: right; }
.icon-x { width: 28px; height: 28px; border: none; background: transparent; border-radius: 6px; color: #b6bcc4; cursor: pointer; font-size: 15px; }
.icon-x:hover { background: #f1f2f4; color: #7a8088; }
.icon-del { width: 34px; height: 34px; border: 1px solid #e6e1e1; background: #fff; border-radius: 7px; color: #c0625c; cursor: pointer; font-size: 16px; flex: none; }
.icon-del:hover { background: #fdf2f1; }
.link-btn { margin-top: 10px; background: transparent; border: none; color: var(--ac); font: 600 12.5px var(--sans); cursor: pointer; padding: 4px 0; }
.ml23 { padding-left: 23px; }
.add-dim { margin-top: 14px; width: 100%; padding: 11px; border: 1.5px dashed #cfd4db; background: #fafbfc; border-radius: 9px; color: var(--muted); font: 600 13px var(--sans); cursor: pointer; }
.add-dim:hover { border-color: var(--ac); color: var(--ac); }

.q-actions { display: flex; gap: 8px; flex: none; align-items: center; }
.q-count { font: 600 11.5px var(--mono); color: var(--text-muted); margin-right: 4px; white-space: nowrap; }

/* A newly added question flashes so the click has a visible consequence —
   without it the card lands below the fold and nothing appears to happen. */
@keyframes q-flash {
  0%   { border-color: var(--ac); box-shadow: 0 0 0 3px var(--ac-shadow); }
  100% { border-color: var(--border-soft); box-shadow: 0 0 0 0 transparent; }
}
.q-card.just-added { animation: q-flash 1.6s ease-out; }
@media (prefers-reduced-motion: reduce) {
  .q-card.just-added { animation: none; border-color: var(--ac); }
}
.btn.small { padding: 9px 13px; font-size: 12.5px; }
.btn.small.sus { border: none; background: #eef3fc; color: var(--ac); }
.preset-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 12px 14px; margin-bottom: 14px; background: #f5f7fb; border: 1px solid #e7ebf3; border-radius: 9px; }
.preset-label { font: 600 10px var(--mono); letter-spacing: .06em; color: var(--ac); flex: none; }
.preset-select { padding: 7px 10px; border: 1px solid var(--border); border-radius: 7px; font: 500 13px var(--sans); background: #fff; color: var(--ink); cursor: pointer; min-width: 240px; }
.preset-select:focus { outline: none; border-color: var(--ac); }
.preset-hint { font-size: 12px; color: var(--muted-2); }
.empty { text-align: center; padding: 38px 0; color: var(--faint); font-size: 14px; }
.q-card { border: 1px solid var(--border-soft); border-radius: 10px; padding: 14px 16px; background: #fcfcfd; }
.q-head { display: flex; align-items: center; gap: 11px; margin-bottom: 11px; }
.q-num { font: 700 12px var(--mono); color: var(--faint); width: 26px; }
.badge { font: 600 10px/1 var(--mono); letter-spacing: .05em; text-transform: uppercase; padding: 5px 9px; border-radius: 5px; }
.badge.mc { background: #eef3fc; color: var(--ac); }
.badge.tx { background: #eef6ee; color: #1f9d57; }
.q-prompt { font: 500 14px var(--sans); }
.q-options { padding-left: 37px; display: flex; flex-direction: column; gap: 6px; }
.q-opt { display: flex; align-items: center; gap: 9px; }
.radio { width: 14px; height: 14px; border: 2px solid #cfd4db; border-radius: 50%; flex: none; }
.opt-input { max-width: 320px; padding: 6px 10px; font-size: 13px; }
.q-textph { margin-left: 37px; border: 1px dashed #dde0e5; border-radius: 7px; padding: 9px 12px; font-size: 13px; color: var(--faint); background: #fff; }

.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.mini { background: #fcfcfd; border: 1px solid var(--border-soft); border-radius: 10px; padding: 16px; }
.target-url { font-size: 14px; color: var(--ac); word-break: break-all; }
.target-intent { font-size: 13.5px; color: var(--ink-2); margin-top: 8px; }
.target-intent.muted { color: var(--faint); font-size: 12.5px; }
.stats { display: flex; gap: 24px; }
.stat { font: 700 26px var(--mono); }
.stat-l { font-size: 12px; color: var(--muted-2); margin-top: 2px; }
.dist-name { font: 600 13px var(--sans); color: var(--ink-2); margin-bottom: 9px; }
.dist-row { display: flex; align-items: center; gap: 12px; }
.dist-label { width: 120px; font-size: 13px; color: var(--muted); flex: none; text-align: right; }
.dist-bar { flex: 1; height: 22px; background: #f1f2f4; border-radius: 5px; overflow: hidden; }
.dist-fill { height: 100%; background: var(--ac); border-radius: 5px; display: flex; align-items: center; justify-content: flex-end; padding-right: 8px; min-width: 30px; }
.dist-pct { font-size: 11px; color: #fff; }
.q-review { display: flex; align-items: center; gap: 11px; padding: 11px 13px; background: #fcfcfd; border: 1px solid var(--border-soft); border-radius: 8px; }
.small-badge { font-size: 9.5px; padding: 4px 7px; }
.q-review-text { font-size: 13.5px; color: var(--ink-2); flex: 1; }

.confirm-card { background: linear-gradient(180deg, #fff, #fcfdff); border: 1px solid #e1e8f4; border-radius: 12px; padding: 22px 24px; display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.confirm-text { font-size: 13.5px; color: var(--muted); line-height: 1.5; max-width: 480px; }
.btn-run { display: inline-flex; align-items: center; gap: 9px; padding: 14px 26px; border-radius: 10px; border: none; background: var(--ac); color: #fff; font: 700 15px var(--sans); cursor: pointer; flex: none; box-shadow: 0 6px 16px rgba(42,111,219,.28); }
.btn-run:hover { filter: brightness(1.07); }
.btn-run:disabled { opacity: .7; cursor: default; }
.launch-err { color: #b5564f; font-size: 13px; padding: 4px 2px; }

.wiz-footer { position: fixed; left: 240px; right: 0; bottom: 0; background: var(--bg-blur); backdrop-filter: blur(8px); border-top: 1px solid var(--border); padding: 16px 36px; display: flex; justify-content: space-between; align-items: center; }
.foot-left { display: flex; align-items: center; gap: 10px; }
.block-reason { color: #a8691a; font-weight: 600; }
.reset-btn { color: var(--muted-2); }
.reset-btn:hover { color: #b5564f; border-color: #e0d6d6; }
.step-count { font-size: 12px; color: var(--faint); }
.nav-spacer { width: 120px; }

.device-pick { display: flex; gap: 8px; }
.device-btn { flex: 1; display: flex; align-items: center; justify-content: center; gap: 8px;
  padding: 11px 12px; border: 1px solid var(--border-input); background: var(--surface);
  border-radius: 8px; font: 600 13px var(--sans); color: var(--text-2); cursor: pointer; }
.device-btn:hover { border-color: var(--ac); color: var(--ac); }
.device-btn.on { border-color: var(--ac); background: var(--accent-tint); color: var(--ac); }
.device-ico { font-size: 14px; }
.device-note { font-size: 12px; color: var(--text-muted); margin-top: 7px; line-height: 1.45; }
</style>
