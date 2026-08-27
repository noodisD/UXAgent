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

export function pageSpeedColor(v: number): string {
  return v >= 90 ? '#1f9d57' : v >= 50 ? '#cf8420' : '#d83a34'
}

export function outcomeOf(o: string | undefined): Outcome {
  return (o === 'success' || o === 'partial' || o === 'failed') ? o : 'partial'
}
