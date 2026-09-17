import type { InjectionKey } from 'vue'

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
  /** Which device the journeys run on. 'both' alternates personas between them. */
  device: 'desktop' | 'mobile' | 'both';
  concurrency: number;
  // What the site actually is (e.g. "product catalog, no online checkout, has
  // a store locator"). Without it, every judgement pass — first impression,
  // heuristics, the aggregate narrative, and outcome success/partial/failed —
  // implicitly assumes an online shop and marks a catalog/locator site down
  // for lacking a cart it was never meant to have.
  siteContext: string;
};

export type SurveyConfig = {
  questionnaire: string; // raw JSON string from textarea
};

export type WizardForm = {
  experiment: ExperimentConfig;
  survey: SurveyConfig;
  persona: PersonaConfig;
};

export const FORM_KEY: InjectionKey<WizardForm> = Symbol('WizardForm')

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
  /** methodology fields — see METHODOLOGY_PLAN.md §1.3 / §3.1 */
  harnessSuspect?: boolean
  requiresHumanCheck?: boolean
  confidence?: 'high' | 'medium' | 'low'
  /** set when evidence capped an over-claimed confidence */
  confidenceCappedBy?: 'harness_suspect' | 'unverified' | 'off_site' | 'claim_unsupported' | 'claim_partial' | null
  /** claim-verifier truth gate vs the cited step's full observation */
  claimVerdict?: 'supported' | 'partial' | 'unsupported' | null
  claimVerdictReason?: string | null
  /** cited page was outside the site under test */
  offSite?: boolean
  severityScore?: number          // NN/g 0–4; 0 = false positive
  severityHumanConfirmed?: string | null
  requiresRealUserValidation?: boolean
}

export type OutcomeSource = 'observed' | 'declared'

export type IsoMetrics = {
  effectiveness: {
    task_success: string; goal_state_reached: boolean; critical_error: boolean; ended_reason: string | null
    /** §4.2 grounded outcome: "observed" (trace evidence) or "declared" (LLM fallback) */
    outcome_source?: OutcomeSource
    declared_outcome?: string | null
    observed_outcome?: string
    target_rung?: string | null
    target_source?: string
    reached_rung?: string
    disagreement?: boolean
    evidence?: { rung: string; step: number | null; url: string }[]
  }
  efficiency: { step_count: number | null; backtracks: number; loop_detected: boolean; reached_step_limit: boolean; optimal_path_delta: number | null }
  satisfaction: {
    source: string
    synthetic_score_1_5: number | null
    synthetic_seq_1_7?: number | null
    synthetic_confidence_1_7?: number | null
    frustration_signals: number
    requires_real_user_validation: boolean
  }
}

export type BiasAudit = {
  flags: { target: string; issue_type: string; explanation: string }[]
  summary: string
  bias_detected: boolean
}

export type CognitiveWalkthroughStep = {
  step: number
  right_intent: string
  action_noticed: string
  action_result_assoc: string
  feedback_visible: string
  note: string
}

export type PersonaResult = {
  id: string
  name: string
  objective: string
  homeLocation?: string | null
  demographics: Record<string, unknown>
  metaParts: string[]
  outcome: Outcome
  /** §4.2: is `outcome` trace-grounded ("observed") or the LLM's self-rating ("declared") */
  outcomeSource?: OutcomeSource
  declaredOutcome?: Outcome
  targetRung?: string | null
  reachedRung?: string | null
  outcomeDisagreement?: boolean
  steps: number
  duration: string | null
  reflection: ObjectiveReflection
  firstImpression: { purpose: number | null; navigation: number | null; cta: number | null; hierarchy: number | null; trust: number | null }
  accessibility: { score: number | null; findings: AccessibilityFinding[] }
  heuristics: HeuristicFinding[]
  survey: { q: string; answer: string; reasoning: string }[]
  iso?: IsoMetrics | null
  biasAudit?: BiasAudit | null
  cognitiveWalkthrough?: CognitiveWalkthroughStep[]
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
  scope?: RunScope | null
  /** Declared site purpose the judgements were calibrated against. */
  siteContext?: string | null
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

/** §6.1 Scope — what produced the findings. Null for runs made before it was
 *  recorded; it cannot be reconstructed, so the report says so rather than guess. */
export type RunScope = {
  provider?: string
  models?: Record<string, string | null>
  prompts_version?: string
}

/** §6.1 persona x task row. `outcome_source` matters: 'observed' = trace-grounded
 *  (§4.2), 'declared' = the LLM's verdict on its own run (see iso_metrics). */
export type PersonaTaskRow = {
  persona: string
  home_location: string
  task: string
  outcome: string | null
  outcome_source: OutcomeSource
  declared_outcome?: string | null
  disagreement?: boolean
  target_rung?: string | null
  goal_state_reached?: boolean | null
  steps: number | null
  backtracks: number | null
  seq: number | null
  seq_source: string
}

/** §6.2 prioritization row. `impact` is derived from reach x task consequence,
 *  never self-reported — the evidence ships with it so the rule can be checked. */
export type PriorityRow = {
  priority: number
  heuristic_id: number
  heuristic_name: string
  severity: string
  severity_score: number
  impact: 'high' | 'medium' | 'low'
  confidence: 'high' | 'medium' | 'low'
  personas_affected: number
  participant_count: number
  blocked: boolean
  blocked_personas: string[]
  finding_count: number
}

export type RunDetail = {
  meta: RunMeta
  personas: PersonaResult[]
  report?: AggReport | null
  personaTasks?: PersonaTaskRow[]
}

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
