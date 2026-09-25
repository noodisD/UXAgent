// Shared color maps + small display helpers, ported from the design's renderVals().
import { locale } from './i18n'

export type Severity = 'critical' | 'serious' | 'minor'
export type Outcome = 'success' | 'partial' | 'failed'

export const SEV: Record<Severity, { label: string; pl: string; color: string; chipBg: string; chipFg: string }> = {
  critical: { label: 'Critical', pl: 'krytyczny', color: '#d83a34', chipBg: '#fdeceb', chipFg: '#c0322c' },
  serious:  { label: 'Serious',  pl: 'poważny',  color: '#cf8420', chipBg: '#fbf2e3', chipFg: '#a8691a' },
  minor:    { label: 'Minor',    pl: 'drobny',   color: '#6b7280', chipBg: '#f1f2f4', chipFg: '#5b6470' },
}

export const OUTCOME: Record<Outcome, { label: string; color: string; bg: string }> = {
  success: { label: 'Goal completed',       color: '#1f9d57', bg: '#e9f6ee' },
  partial: { label: 'Partially completed',  color: '#b5701a', bg: '#fbf2e3' },
  failed:  { label: 'Goal not met',         color: '#d83a34', bg: '#fdeceb' },
}

export const SEV_ORDER: Record<Severity, number> = { critical: 0, serious: 1, minor: 2 }

export const HEURISTICS_EN: Record<number, string> = {
  1: 'Visibility of system status',
  2: 'Match between system and the real world',
  3: 'User control and freedom',
  4: 'Consistency and standards',
  5: 'Error prevention',
  6: 'Recognition rather than recall',
  7: 'Flexibility and efficiency of use',
  8: 'Aesthetic and minimalist design',
  9: 'Help users recognise, diagnose, and recover from errors',
  10: 'Help and documentation',
}

export const HEURISTICS_PL: Record<number, string> = {
  1: 'Widoczność statusu systemu',
  2: 'Zgodność systemu z rzeczywistością',
  3: 'Kontrola użytkownika i swoboda działania',
  4: 'Spójność i standardy',
  5: 'Zapobieganie błędom',
  6: 'Rozpoznawanie zamiast zapamiętywania',
  7: 'Elastyczność i efektywność użytkowania',
  8: 'Estetyczny i minimalistyczny design',
  9: 'Pomoc w rozpoznawaniu i naprawianiu błędów',
  10: 'Pomoc i dokumentacja',
}

const SEV_LABELS = {
  pl: { critical: 'Krytyczny', serious: 'Poważny', minor: 'Drobny' },
  en: { critical: 'Critical', serious: 'Serious', minor: 'Minor' },
}
const OUTCOME_LABELS = {
  pl: { success: 'Cel osiągnięty', partial: 'Częściowo osiągnięty', failed: 'Cel nieosiągnięty' },
  en: { success: 'Goal completed', partial: 'Partially completed', failed: 'Goal not met' },
}

// Locale-aware label getters (read the reactive locale, so templates re-render on switch).
export function sevLabel(key: Severity): string { return SEV_LABELS[locale.value][key] }
export function outcomeLabel(key: Outcome): string { return OUTCOME_LABELS[locale.value][key] }
export function heuristicName(id: number): string {
  return (locale.value === 'pl' ? HEURISTICS_PL : HEURISTICS_EN)[id] || ''
}

export function initials(name: string): string {
  return (name || '?').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase()
}

export function scoreColor(v: number): string {
  return v >= 4 ? '#1f9d57' : v >= 3 ? '#cf8420' : '#d83a34'
}

/** An overall UX score fit to print, or null for "no score". A real score is
 *  1.0-5.0 (scoring.FLOOR is 1.0); 0 is what older backends wrote when a run
 *  had none, and history rows already in the bucket index still carry it. */
export function uxScore(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null
}

export function pageSpeedColor(v: number): string {
  return v >= 90 ? '#1f9d57' : v >= 50 ? '#cf8420' : '#d83a34'
}

// Normalisers: the backend sends free-form strings (and nulls), the label and
// colour maps are keyed by a union. Every caller must go through these, so the
// coercion happens in exactly one place — counting the raw value while
// displaying the normalised one is how ResultsScreen's totals stopped summing.
export function outcomeOf(o: string | null | undefined): Outcome {
  return (o === 'success' || o === 'partial' || o === 'failed') ? o : 'partial'
}

/** Polish severity label (as the backend stores it) -> Severity key. */
export function sevOf(pl: string | null | undefined): Severity {
  if (pl === 'critical' || pl === 'serious' || pl === 'minor') return pl
  return ({ krytyczny: 'critical', poważny: 'serious', drobny: 'minor' } as
    Record<string, Severity>)[pl ?? ''] ?? 'minor'
}
