# UX Methodology Implementation Plan — branch `feat/ux-methodology`

Source: the internal methodology knowledge base ("Metodologia UX testing AI",
2026-07) — ISO 9241-11, NN/g heuristics/severity/task-scenarios, WCAG 2.2 /
WCAG2Mobile, MeasuringU benchmarks, Baymard, and the synthetic-participants
critique (Nature Machine Intelligence 2025). This plan maps each requirement
onto concrete modules of this repo.

Guiding rule from the doc (§14): **AI is a pre-testing and hypothesis tool,
not a representative user sample.** The strongest signal is the interaction
log; the weakest is a synthetic satisfaction declaration. Every change below
serves that hierarchy.

Legend: 🟢 quick win · 🟡 contained feature · 🔴 larger effort / process

---

## Phase 1 — Honest signals & labeling (🟢, do first)

### 1.1 ISO 9241-11 signal separation (§1.1) — ✅ DONE (`main/iso_metrics.py`)
Split each persona run's result into the three ISO dimensions instead of one
blended narrative.

- **effectiveness**: `task_success | partial_success | failed`,
  `goal_state_reached`, `critical_error`. Provenance matters here and is now
  explicit. §4.2 has shipped: `main/session_goal_state.py` derives the outcome
  from what the trace shows, and `iso_metrics` labels each result `observed` or
  `declared` — falling back to the LLM's self-rating only when the target rung
  is undecidable. The report presents the observed outcome as the headline and
  flags disagreement with the declared one, so "observed" is never claimed for a
  value that was not measured. `critical_error`/`ended_reason` come from
  `session_result.json` as before.
- **efficiency**: `step_count`, `tap_count`, `backtracks` (count of `back`
  actions), `loop_detected` (already computable —
  `attribution.harness_suspect_steps()` finds exactly these loops),
  `optimal_path_delta` (optional, needs a declared optimal path per task).
- **satisfaction (synthetic)**: survey answers + reflection, **always labelled
  `synthetic_self_report`** per §1.1/§4.3.

Files: new `main/iso_metrics.py` (pure function over TraceStore) →
`trace_store.write_iso_metrics()` → included in `results_api.build_run` and
the per-persona report. Tests: `tests/test_iso_metrics.py`.

### 1.2 Kill population-stat phrasing (§1.2, §14)
"u 3 z 3 uczestników" reads as a population rate; the doc forbids that.

- Reporter/aggregate prompt: frequencies become *"hipoteza syntetyczna:
  zaobserwowano u N z M symulowanych person"*; forbid percentages of "users".
- `aggregate_synthesize.txt`: add explicit instruction; extend the existing
  `methodology_note` with the §1.2 qual-vs-quant disclaimer.
- Score: rename `overall_ux_score` presentation to "synthetic UX score
  (hipoteza)" in report templates + ReportView.vue label.

Files: `audit_prompts/aggregate_synthesize.txt`, `main/reporter.py`,
`main/reporter_html.py`, `frontend/src/screens/ReportView.vue`, i18n.

### 1.3 Finding schema upgrade (§8.3)
Extend every heuristic finding with the doc's issue schema. Already have:
`verified`, `evidence_type`-ish, `harness_suspect`, `requires_human_check`.
Add: `confidence: low|medium|high`, `severity_ai_proposed` (rename of
`severity`), `severity_human_confirmed: null`, `requires_real_user_validation`.

Files: `audit_prompts/heuristic_evaluate.txt` (ask for confidence),
`main/evidence.py` (carry fields through), `main/reporter*.py`,
`results_api.py`, ResultsScreen/ReportView badges. Tests extend
`test_evidence.py`.

---

## Phase 2 — Persona engine v2 (🟡, biggest realism gain)

### 2.1 Structured persona schema (§2.1, §8.2)
Extend `persona_card.json` / generation to the doc's schema:

```json
{
  "status": "evidence_based | hypothesis | synthetic",
  "behavioral_profile": {"tech_savviness", "patience", "exploration_style", "risk_tolerance"},
  "jobs_to_be_done": [], "barriers": [], "mental_models": [],
  "context": {"device", "network", "environment"},
  "accessibility": {"font_scale", "screen_reader", "motor_limitations"},
  "abort_conditions": ["two unclear security warnings", ...],
  "evaluation_focus": ["error prevention", "system status", ...]
}
```

Files: `main/persona.py` (generation prompt asks for structured JSON),
`main/experiment.py::build_persona_card`, wizard Step 1 (optional advanced
fields), `useForm.ts`/`types.ts`. Default `status: "synthetic"` unless the
user marks evidence sources — per §2.3/§12.

### 2.2 Honor `abort_conditions` + `patience` in the agent loop (§2.1 row
"Reguły zachowania")
Today an agent grinds all 25–30 steps into a wall (Julia/Kacper). Realistic
drop-off: inject persona's abort rules into the planning prompt AND add a
harness-side check — after `max_confusing_steps` consecutive no-progress steps
(no URL change + repeat/fallback signals, reusing `attribution` logic), the
agent records `ended_reason: "persona_abandoned"` in `session_result.json`
with the triggering condition. Drop-off becomes a first-class UX signal
(§4.3 metric "Drop-off: dobre jako sygnał ryzyka, nie prognoza").

Files: `agent/shop_prompts/planning.txt`, `main/experiment.py`,
`main/model.py`, `trace_store.py` (session_result shape), tests
`tests/test_abort.py`.

### 2.3 Bias guardrail pass (§1.4, §11.4) — ✅ DONE (`main/bias_audit.py`)
New post-run audit using the §11.4 prompt verbatim: check each finding /
reflection rationale for (a) stereotype-based vs persona-data-based reasoning,
(b) identity traits used without task relevance, (c) flattening. Output
`bias_audit.json` with per-item flags; flagged items get
`requires_human_check: true`.

Files: new `main/bias_audit.py` + `audit_prompts/bias_audit.txt`, wired into
`analysis_runner.py` (async pass), surfaced in report appendix.

---

## Phase 3 — Evaluation engine upgrades (🟡)

### 3.1 Severity 0–4 (NN/g) replacing the 3-level scale (§4.4) — ✅ DONE (`main/severity.py`)
Current: krytyczny/poważny/drobny. Doc: 0–4 with 0 = "not a problem / false
positive" — which is exactly where `harness_suspect` findings belong. Map:
4=katastrofa(stop-ship), 3=major(≈krytyczny), 2=minor(≈poważny),
1=kosmetyczny(≈drobny), 0=false positive. Keep Polish labels; store numeric.
Severity must justify frequency/impact/persistence (prompt requirement).

Files: `heuristic_evaluate.txt`, `main/severity.py` (the single severity scale),
frontend `theme.ts` SEV map + i18n, migration shim for old runs (read old
labels → numeric).

### 3.2 Cognitive walkthrough layer (§4.4) — ✅ DONE (`main/cognitive_walkthrough.py`)
Per step (or per failed step), evaluate the four CW questions: right intent?
action noticed? action↔result association? feedback visible? Emitted as
`cognitive_walkthrough.json` hypotheses feeding heuristic evaluation evidence.
Cheap version: run only on steps flagged as friction (backtracks, loops,
errors) to control token cost.

Files: new `audit_prompts/cognitive_walkthrough.txt`, `main/auditor.py`
(second pass over friction steps), report section.

### 3.3 Post-task SEQ + confidence (§4.3) — ✅ DONE (`main/survey.py`, synthetic_seq / synthetic_confidence)
Add SEQ ("Jak łatwe było wykonanie zadania?" 1–7) and confidence (1–7) as
fixed synthetic post-task questions independent of the user's questionnaire,
stored as `synthetic_seq`, `synthetic_confidence` with the MeasuringU 5.5
SEQ benchmark quoted *as context only*. Never presented as real-user SEQ.

Files: `main/survey.py` (fixed post-task block), `iso_metrics.py`
(satisfaction bucket), report.

### 3.4 Web-adapted mobile/accessibility heuristics (§4.2) — ✅ DONE

Implemented as `main/a11y_checks.py`: deterministic checks parse the captured
aria snapshot and emit deduplicated, step-cited candidates; the LLM narrates and
scores them instead of reading the tree itself.

Checks: `image_missing_alt`, `control_missing_name`, `missing_main_landmark`,
`text_below_12px` (reuses the font sizes viewport_scan already collects).

This also fixed a live defect: the pass used to paste every raw snapshot into
the prompt (one pepco page = 377k chars; a 30-step session = 1.66M chars /
~416k tokens), which failed near the context limit and cost one persona their
whole accessibility audit. Payload is now ~270 chars — 3000–6000x smaller — and
the pass runs in ~20s instead of minutes.

The live-DOM half is in `main/viewport_scan.py`, measured at mobile and tablet:
tap-target size (SC 2.5.8, incl. the spacing exception), text contrast
(SC 1.4.3 AA, 4.5:1 / 3:1 large), drag-only interactions (SC 2.5.7).

Scope honestly — **mobile-web heuristics, not WCAG conformance**, and the report
must not overstate:
- 24×24 is normative; 48×48 is Material/HIG advice and is a separate advisory
  bucket. A 30px button is not a WCAG failure.
- The SC 2.5.8 spacing exception is implemented. Without it pepco.pl reported 27
  tap-target "failures"; 23 were well-spaced links that conform. The real number
  is 4 — the 12×12 carousel dots.
- Contrast over background images/gradients, and translucent layers needing
  compositing, are counted `undetermined`, never scored.
- SC 2.5.7 needs the *absence* of a pointer alternative, which is not
  observable: drag affordances carry `requires_human_check`.

<details><summary>original plan text</summary>
Extend `viewport_scan.py` checks with web equivalents of the doc's mobile
criteria: tap-target size ≥ ~48px CSS for interactive elements, text ≥ 12px,
contrast 4.5:1 (already partly covered), no drag-only interactions detected.
Scope honestly: report as "mobile-web heuristics", not native-app WCAG2Mobile
compliance.

Files: `main/viewport_scan.py`, `main/auditor.py` accessibility prompt,
report labels.
</details>

---

## Phase 4 — Scenario integrity (🟡, needs a design decision)

### 4.1 Separate harness hints from task scenario (§3.1)
Problem: `site_context` currently mixes (a) evaluation context and (b) UI
navigation hints ("use hero cards, avoid footer links") — the doc says
scenarios must not reveal how to use the UI, or the test is contaminated.

Design: split config into
- `task_scenario` — persona-visible; context + goal + success condition, no
  UI hints (validated by a lint: reject strings naming concrete UI controls);
- `harness_hints` — engine-level workarounds (SPA notes, PDF-download ban)
  injected into the *action executor* context only, never into perceive/
  reflect/audit prompts, and excluded from the auditor's inputs.

Files: `backend/conf/runConfig.yaml` schema, `main/run.py`, `main/experiment.py`,
`agent/agent.py` prompt assembly, wizard field split. This is the subtlest
change — verify with an A/B run that agents still navigate pepco.

### 4.2 Machine-checkable success criteria (§3.1 "Kryterium sukcesu") — ✅ DONE (`main/session_goal_state.py`)
Each task gets an explicit `success_condition` the harness can verify (URL
pattern, element present, state reached) so `goal_state_reached` in Phase 1.1
is evidence, not LLM opinion.

Files: config schema + `main/experiment.py` end-of-run check +
`session_result.json`.

---

## Phase 5 — Validation module (🔴, process + tooling)

### 5.1 AI vs real-users validation protocol (§1.5, §12)
The doc's non-negotiable before decision-grade use. Deliverables:
- `VALIDATION_PROTOCOL.md`: same prototype/tasks/success criteria for AI and
  5–8 real users; independent researcher codes both problem lists.
- `main/validation.py` + CLI: given two coded problem lists (CSV), compute
  problem-discovery precision/recall, task-success agreement (Cohen's κ),
  severity rank correlation (Spearman). Start targets from doc: recall of
  critical problems >70% (estimated, pilot).
- Report gains a "Plan walidacji z real users" section listing which findings
  have `requires_real_user_validation: true`.

### 5.2 Benchmark context (§5, §10)
For e-commerce runs, allow attaching benchmark references (e.g. Baymard
guest-checkout, 70% cart abandonment) as *context* in the aggregate prompt —
clearly cited, never blended into synthetic frequencies.

Files: `conf` optional `domain_benchmarks` block → aggregate prompt.

---

## Phase 6 — Report restructure (🟢/🟡, last since it consumes all the above)

Align report to §6.1 structure: executive summary → scope (app version,
model, prompt version — add `model` + prompt hash to `run_manifest.json`) →
methodology + limitations → persona×task matrix → findings (with evidence
status legend: observation / synthetic inference / needs validation) →
accessibility → benchmark context → prioritization (severity × impact ×
confidence matrix from §6.2) → real-user validation plan → appendix (prompts,
persona defs, logs). Frontend ReportView gets the matching sections.

Files: `aggregate_synthesize.txt`, `reporter*.py`, `results_api.py`,
`ReportView.vue`, i18n.

---

## Sequencing & effort

| Order | Item | Effort | Depends on |
|---|---|---|---|
| 1 | 1.2 labeling fix | S | — |
| 2 | 1.1 ISO metrics (loop_detected already exists) | S/M | — |
| 3 | 1.3 finding schema | S | — |
| 4 | 3.1 severity 0–4 | M | 1.3 |
| 5 | 2.1 persona schema v2 | M | — |
| 6 | 2.2 abort conditions | M | 2.1 |
| 7 | 4.2 machine success criteria | M | — |
| 8 | 3.3 SEQ/confidence | S | 1.1 |
| 9 | 2.3 bias audit | M | — |
| 10 | 3.2 cognitive walkthrough | M | — |
| 11 | 4.1 scenario/hints split | M (risky) | validation run |
| 12 | 3.4 a11y web heuristics | M | — |
| 13 | 6 report restructure | M | 1–5 |
| 14 | 5.1 validation module | L (process) | 13 |

Each item: TDD where logic is pure (iso_metrics, validation, severity
mapping, abort detection), plus one end-to-end pepco validation run after
items 6, 11, and 13.

## Acceptance criteria (from doc §12, all mandatory)
- [ ] every output carries status: observation / synthetic inference / needs validation
- [ ] personas carry evidence level; no decision-irrelevant detail required
- [ ] tasks have realistic context, no UI hints, machine-checkable success condition
- [ ] synthetic metrics never presented as real-user metrics
- [ ] AI proposes severity; human confirms high-impact issues (field exists, UI affordance)
- [ ] accessibility layer: contrast, text size, tap targets, alternative input
- [ ] periodic AI-vs-real-users validation exists before decision-grade use
- [ ] every problem has evidence, heuristic, severity, confidence, recommendation
