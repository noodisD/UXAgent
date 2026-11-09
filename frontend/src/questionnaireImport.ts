// Validation for a questionnaire pasted into the wizard as JSON. Pure: no Vue,
// no i18n. A failure comes back as an i18n key plus params, so the screen
// translates it and the message follows the language switch.

export type QuestionnaireQuestion = {
  id: string
  type: 'short_text' | 'multiple_choice'
  prompt: string
  options?: string[]
  // Scoring markers read by the backend (sus.py), kept so that a questionnaire
  // exported from the wizard keeps scoring as SUS when pasted back.
  sus_item?: number
  reverse?: boolean
}

export type ParsedQuestionnaire = {
  questionnaire_id?: string
  title?: string
  questions: QuestionnaireQuestion[]
}

export type QuestionnaireIssue = { key: string; params?: Record<string, string | number> }

export type QuestionnaireParse =
  | { ok: true; questionnaire: ParsedQuestionnaire }
  | { ok: false; issue: QuestionnaireIssue }

const fail = (key: string, params?: QuestionnaireIssue['params']): QuestionnaireParse =>
  ({ ok: false, issue: { key, params } })

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)

const isText = (v: unknown): v is string => typeof v === 'string' && v.trim() !== ''

/** Parse and validate `{questionnaire_id?, title?, questions: [...]}`.
 *  Keys the backend does not read are dropped; text fields come back trimmed. */
export function parseQuestionnaireJson(text: string): QuestionnaireParse {
  if (!text.trim()) return fail('w2.import.err.empty')

  let raw: unknown
  try { raw = JSON.parse(text) } catch (e) {
    return fail('w2.import.err.json', { detail: e instanceof Error ? e.message : String(e) })
  }
  if (!isObject(raw)) return fail('w2.import.err.notObject')

  const out: ParsedQuestionnaire = { questions: [] }
  for (const key of ['questionnaire_id', 'title'] as const) {
    const v = raw[key]
    if (v === undefined || v === null || v === '') continue
    if (!isText(v)) return fail('w2.import.err.meta', { field: key })
    out[key] = v.trim()
  }

  if (!Array.isArray(raw.questions) || raw.questions.length === 0) return fail('w2.import.err.noQuestions')

  const seen = new Set<string>()
  for (const [i, q] of raw.questions.entries()) {
    const n = i + 1
    if (!isObject(q)) return fail('w2.import.err.notQuestion', { n })
    if (!isText(q.id)) return fail('w2.import.err.id', { n })
    const id = q.id.trim()
    // The report keys survey answers by id; a repeat files one question's
    // ratings under another's prompt (the backend rejects it as well).
    if (seen.has(id)) return fail('w2.import.err.dupId', { n, id })
    seen.add(id)
    if (q.type !== 'multiple_choice' && q.type !== 'short_text') return fail('w2.import.err.type', { n })
    if (!isText(q.prompt)) return fail('w2.import.err.prompt', { n })

    const question: QuestionnaireQuestion = { id, type: q.type, prompt: q.prompt.trim() }
    if (q.type === 'multiple_choice') {
      if (!Array.isArray(q.options) || q.options.length === 0 || !q.options.every(isText)) {
        return fail('w2.import.err.noOptions', { n })
      }
      question.options = q.options.map(o => o.trim())
    } else if (q.options !== undefined && q.options !== null && !(Array.isArray(q.options) && q.options.length === 0)) {
      return fail('w2.import.err.textOptions', { n })
    }

    if (q.sus_item !== undefined && q.sus_item !== null) {
      const no = q.sus_item
      if (typeof no !== 'number' || !Number.isInteger(no) || no < 1 || no > 10) {
        return fail('w2.import.err.susItem', { n })
      }
      question.sus_item = no
    }
    if (q.reverse !== undefined && q.reverse !== null) {
      if (typeof q.reverse !== 'boolean') return fail('w2.import.err.reverse', { n })
      question.reverse = q.reverse
    }
    out.questions.push(question)
  }
  return { ok: true, questionnaire: out }
}
