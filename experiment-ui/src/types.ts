import type { InjectionKey, Ref } from 'vue'

export type RangeBin = { id: number; min: number; max: number; mass: number };
export type GenderBin = { id: number; label: string; mass: number };

export type PersonaSlot = {
  mode: 'auto' | 'manual';
  description: string;
};

export type PersonaConfig = {
  numSubjects: number; // >= 1
  generalIntent: string; // non-empty
  examplePersona: string;
  slots: PersonaSlot[];
  // Unified demographics schema
  demographics: {
    name: string;
    choices: { name: string; weight: number }[]; // now use weight instead of probability
  }[];
  // Legacy fields kept optional for backward compatibility
  agePmf?: Record<`${number}-${number}`, number>;
  genderPmf?: Record<string, number>;
  incomePmf?: Record<`${number}-${number}`, number>;
};

export type ExperimentConfig = {
  startUrl: string;
  headless: boolean;
  maxSteps: number; // [1..5000]
  concurrency: number;
};

export type SurveyConfig = {
  questionnaire: string; // raw JSON string from textarea
};

export type WizardForm = {
  experiment: ExperimentConfig;
  survey: SurveyConfig;
  persona: PersonaConfig;
};

export interface WizardNav {
  current: Ref<number>
  steps: number
  next: () => void
  prev: () => void
  finish: () => void
}

export const FORM_KEY: InjectionKey<WizardForm> = Symbol('WizardForm')
export const NAV_KEY: InjectionKey<WizardNav> = Symbol('WizardNav')

// ---- Results dashboard payloads (mirror backend results_api.py) ----

export type Outcome = 'success' | 'partial' | 'failed'
export type Severity = 'critical' | 'serious' | 'minor'

export type ObjectiveReflection = {
  summary: string
  blockers: string[]
  wins: string[]
  priorityFix: string
}

export type FindingEvidence = {
  step: number | null
  url: string
  hasScreenshot: boolean
  observationQuote: string
  verified: boolean
}

export type AccessibilityFinding = FindingEvidence & { detail: string }
export type HeuristicFinding = FindingEvidence & {
  severity: Severity; id: number; evidence: string; recommendation: string
}

export type PersonaResult = {
  id: string
  name: string
  objective: string
  homeLocation?: string | null
  demographics: Record<string, unknown>
  metaParts: string[]
  outcome: Outcome
  steps: number
  duration: string | null
  reflection: ObjectiveReflection
  firstImpression: { purpose: number | null; navigation: number | null; cta: number | null; hierarchy: number | null; trust: number | null }
  accessibility: { score: number | null; findings: AccessibilityFinding[] }
  viewport: { findings: { device: string; issue: string; severity: Severity }[] }
  heuristics: HeuristicFinding[]
  survey: { q: string; answer: string; reasoning: string }[]
}

export type RunMeta = {
  site: string
  startUrl: string
  intent: string
  date: string
  duration: string | null
  personaCount: number
  maxSteps: number | null
  status: string
  pagespeed: { performance: number | null; accessibility: number | null; bestPractices: number | null; seo: number | null }
}

export type AggIssueExample = {
  persona_dir: string
  persona_name: string
  step: number | null
  url: string
  screenshot: string | null
  observation_quote: string
  evidence: string
  verified: boolean
}

export type AggIssue = {
  heuristic_id: number
  heuristic_name: string
  frequency: string
  impact: string
  recommendation: string
  examples?: AggIssueExample[]
  verified?: boolean
}
export type AggRecommendation = {
  priority: number
  area: string
  action: string
  expected_impact: string
}
export type AggNarrative = {
  executive_summary?: string
  overall_ux_score?: number
  score_rationale?: string
  key_strengths?: string[]
  critical_issues?: AggIssue[]
  serious_issues?: AggIssue[]
  minor_issues?: AggIssue[]
  accessibility_assessment?: string
  responsiveness_assessment?: string
  first_impression_assessment?: string
  priority_recommendations?: AggRecommendation[]
  methodology_note?: string
}
export type AggReport = {
  narrative?: AggNarrative
  statistics?: Record<string, any>
  pagespeed?: any
  scroll?: Record<string, any> | null
  links?: Record<string, any> | null
  forms?: Record<string, any> | null
  task?: string
  website?: string
}

export type RunDetail = { meta: RunMeta; personas: PersonaResult[]; report?: AggReport | null }

export type RunHistoryRow = {
  id: string
  site: string
  date: string
  personas: number
  status: string
  success: number
  partial: number
  failed: number
  critical: number
  score: number
  current?: boolean
}
