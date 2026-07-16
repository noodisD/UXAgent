// src/composables/useForm.ts
import { reactive, watch } from 'vue'
import type { PersonaSlot, WizardForm } from '../types'

export function getWizardDefaults(): WizardForm {
  return {
    persona: {
      numSubjects: 3,
      generalIntent: 'Buy a jacket',
      slots: [
        { mode: 'auto', description: '' },
        { mode: 'auto', description: '' },
        { mode: 'auto', description: '' },
      ],
      examplePersona: `Persona: Clara
Background:
Clara is a PhD student in Computer Science at a prestigious university. She is deeply engaged in research focusing on artificial intelligence and machine learning, aiming to contribute to advancements in technology that can benefit society.

Demographics:

Age: 28
Gender: Female
Education: Pursuing a PhD in Computer Science
Profession: PhD student
Income: $50,000

Financial Situation:
Clara lives on her stipend as a PhD student and is careful with her spending. She prefers to save money for research-related expenses and invest in her academic pursuits.

Shopping Habits:
Clara dislikes shopping and avoids spending much time browsing through products. She prefers straightforward, efficient shopping experiences and often shops online for convenience. When she does shop, she looks for practicality and affordability over style or trendiness.
So Clara wants to shop QUICKLY and EFFICIENTLY.

Professional Life:
Clara spends most of her time in academia, attending conferences, working in the lab, and writing papers. Her commitment to her research is her main priority, and she manages her time around her academic responsibilities.

Personal Style:
Clara prefers comfortable, functional clothing, often choosing items that are easy to wear for long hours spent at her desk or in the lab. She wears medium-sized clothing and likes colors that reflect her personality—mostly red, which she finds uplifting and energizing.`,
      demographics: [
        {
          name: 'Age',
          choices: [
            { name: '18-55', weight: 1 },
          ]
        },
        {
          name: 'Gender',
          choices: [
            { name: 'Male', weight: 1 },
            { name: 'Female', weight: 1 },
            { name: 'Non-binary', weight: 1 },
          ]
        },
        {
          name: "Online Shopping Frequency",
          choices: [
            { name: 'A few times per year', weight: 1 },
            { name: 'option 2', weight: 1 },
            { name: 'option 3', weight: 1 },
          ]
        },
        // Where the persona lives. Without it, tasks like "check availability in
        // a nearby store" have no anchor and the agent invents a city.
        {
          name: 'Miejsce zamieszkania',
          choices: [
            { name: 'Warszawa', weight: 2 },
            { name: 'Kraków', weight: 1 },
            { name: 'średnie miasto (50–200 tys.)', weight: 2 },
            { name: 'małe miasto lub wieś (do 50 tys.)', weight: 1 },
          ]
        },
      ]
    },
    experiment: {
      startUrl: '',
      headless: true,
      maxSteps: 25,
      concurrency: 1,
    },
    survey: {
      questionnaire: ''
    }
  }
}

/** Grow/shrink the persona slots array to match numSubjects (grow with auto slots). */
export function syncPersonaSlots(form: WizardForm) {
  const n = Math.max(1, Number(form.persona.numSubjects) || 1)
  const slots = form.persona.slots
  while (slots.length < n) slots.push({ mode: 'auto', description: '' })
  if (slots.length > n) slots.splice(n)
}

function isValidSlot(s: any): s is PersonaSlot {
  return s && typeof s === 'object' && (s.mode === 'auto' || s.mode === 'manual') && typeof s.description === 'string'
}

export function createWizardForm() {
  const STORAGE_KEY = 'wizard_form'

  const defaults = getWizardDefaults()

  let saved: any = null
  try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null') } catch { saved = null }

  const form = reactive<WizardForm>({ ...defaults })
  if (saved && typeof saved === 'object') {
    try {
      if (saved.persona && typeof saved.persona === 'object') Object.assign(form.persona!, saved.persona)
      if (saved.experiment && typeof saved.experiment === 'object') Object.assign(form.experiment, saved.experiment)
      if (saved.survey && typeof saved.survey === 'object') Object.assign(form.survey, saved.survey)
    } catch {}
  }

  // Backward-compat: older saved forms have no slots (or malformed ones) — rebuild.
  if (!Array.isArray(form.persona.slots) || !form.persona.slots.every(isValidSlot)) {
    form.persona.slots = []
  }
  syncPersonaSlots(form)

  // Keep slot count in lockstep with the participant stepper.
  watch(() => form.persona.numSubjects, () => syncPersonaSlots(form))

  watch(form, (val) => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(val)) } catch {}
  }, { deep: true })

  return form
}


export type RunPayload = {
  total_personas: number
  demographics: { name: string; choices: { name: string; weight: number }[] }[]
  general_intent: string
  example_persona?: string
  persona_slots?: { mode: 'auto' | 'manual'; description: string }[]
  start_url: string
  max_steps: number
  concurrency: number
  questionnaire: Record<string, any>
  headless: boolean
}

/** Restore factory defaults and drop the saved form.
 * A form saved before a defaults change (e.g. the new "Miejsce zamieszkania"
 * demographic) otherwise shadows it forever, since localStorage wins on load. */
export function resetWizardForm(form: WizardForm) {
  const d = getWizardDefaults()
  try { localStorage.removeItem('wizard_form') } catch { /* ignore */ }
  Object.assign(form.experiment, JSON.parse(JSON.stringify(d.experiment)))
  Object.assign(form.survey, JSON.parse(JSON.stringify(d.survey)))
  Object.assign(form.persona, JSON.parse(JSON.stringify(d.persona)))
}

export function buildRunPayload(form: WizardForm): RunPayload {
  // parse questionnaire string to object
  let questionnaireObj: any = {}
  try { questionnaireObj = JSON.parse(form?.survey?.questionnaire ?? '{}') } catch { questionnaireObj = {} }

  return {
    total_personas: Number(form?.persona?.numSubjects ?? 0),
    demographics:   (form?.persona?.demographics ?? []).map(f => ({
      name: String(f?.name ?? '').trim(),
      choices: Array.isArray(f?.choices) ? f.choices.map(c => ({
        name: String(c?.name ?? '').trim(),
        weight: Number(c?.weight ?? 0) / 100
      })) : []
    })),
    general_intent: String(form?.persona?.generalIntent ?? ''),
    example_persona: form?.persona?.examplePersona ?? '',
    persona_slots: (form?.persona?.slots ?? []).map(s => ({
      mode: s.mode,
      description: s.mode === 'manual' ? String(s.description ?? '').trim() : '',
    })),
    start_url:      String(form?.experiment?.startUrl ?? ''),
    max_steps:      Number(form?.experiment?.maxSteps ?? 0),
    concurrency:    Number(form?.experiment?.concurrency ?? 1),
    questionnaire:  questionnaireObj,
    headless:       Boolean(form?.experiment?.headless ?? true),
  }
}

export async function submitWizardForm(form: WizardForm) {
  const payload = buildRunPayload(form)
  const res = await fetch('/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  if (!res.ok) {
    // surface server’s error body to help you debug (400 shows missing fields)
    const text = await res.text()
    throw new Error(`Submit failed: ${res.status} ${text}`)
  }
  return await res.json()
}
