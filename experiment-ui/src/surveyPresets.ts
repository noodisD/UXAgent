// Purpose-calibrated survey presets (see SURVEY_QUESTION_SETS.md). Selecting a
// site category loads its ~10 questions into the editable survey builder; the
// user can then modify / add / remove them like any other question.
// Grounded in SUPR-Q (usability/trust/appearance/loyalty), SUS, SEQ + domain
// heuristics. Likert = 1–5.

export type PresetQuestion = {
  id: string
  type: 'multiple_choice' | 'short_text'
  prompt: string
  options?: string[]
}

const L = (id: string, prompt: string): PresetQuestion => ({
  id, type: 'multiple_choice', prompt, options: ['1', '2', '3', '4', '5'],
})
const O = (id: string, prompt: string): PresetQuestion => ({ id, type: 'short_text', prompt })

export const PRESET_KEYS = [
  'ecommerce', 'catalog', 'directory', 'corporate',
] as const
export type PresetKey = typeof PRESET_KEYS[number]

export const SURVEY_PRESETS: Record<PresetKey, PresetQuestion[]> = {
  ecommerce: [
    L('e1', 'Strona była łatwa w obsłudze.'),
    L('e2', 'Łatwo było znaleźć i porównać produkty.'),
    L('e3', 'Informacje o produkcie (cena, zdjęcia, opis) były wystarczające.'),
    L('e4', 'Pełne koszty (cena, dostawa, opłaty) były jasne przed finalizacją.'),
    L('e5', 'Proces dodania do koszyka i przejścia do zamówienia był prosty.'),
    L('e6', 'Czułem/czułam kontrolę i bezpieczeństwo przy podawaniu danych do płatności.'),
    L('e7', 'Warunki dostawy i zwrotów były łatwe do znalezienia i zrozumienia.'),
    L('e8', 'Strona wydaje się wiarygodna i godna zaufania.'),
    O('e9', 'Co (jeśli cokolwiek) powstrzymałoby Cię przed finalizacją zakupu?'),
    O('e10', 'Czego szukałeś/szukałaś, a czego nie udało się znaleźć?'),
  ],
  catalog: [
    L('c1', 'Strona była łatwa w obsłudze.'),
    L('c2', 'Łatwo było znaleźć interesujący mnie produkt.'),
    L('c3', 'Informacje o produkcie (cena, rozmiar, materiał) były jasne.'),
    L('c4', 'Kategorie i nawigacja produktów były zrozumiałe.'),
    L('c5', 'Łatwo było sprawdzić dostępność produktu w sklepie.'),
    L('c6', 'Lokalizator sklepów działał sprawnie i pomógł mi znaleźć sklep.'),
    L('c7', 'Było dla mnie jasne, że zakupu dokonuję w sklepie stacjonarnym, a nie online.'),
    L('c8', 'Strona jest przejrzysta i atrakcyjna wizualnie.'),
    O('c9', 'Czy oczekiwałeś/oczekiwałaś możliwości zakupu online? Co było mylące?'),
    O('c10', 'Co było największą trudnością podczas korzystania ze strony?'),
  ],
  directory: [
    L('d1', 'Szybko zorientowałem/zorientowałam się, do czego służy ta strona.'),
    L('d2', 'Łatwo było znaleźć potrzebną informację (sklep, godziny, wydarzenie, promocję).'),
    L('d3', 'Struktura i nawigacja strony były zrozumiałe.'),
    L('d4', 'Wyszukiwarka lub katalog sklepów/usług działały sprawnie.'),
    L('d5', 'Informacje wydawały się aktualne i kompletne (godziny, lokalizacje, wydarzenia).'),
    L('d6', 'Łatwo było znaleźć, jak dotrzeć na miejsce lub zorientować się w planie obiektu.'),
    L('d7', 'Strona jest przejrzysta i atrakcyjna wizualnie.'),
    L('d8', 'Chętnie skorzystał(a)bym z tej strony ponownie.'),
    O('d9', 'Jakiej informacji szukałeś/szukałaś, a nie mogłeś/mogłaś jej znaleźć?'),
    O('d10', 'Co było największą trudnością?'),
  ],
  corporate: [
    L('k1', 'Strona była łatwa w obsłudze.'),
    L('k2', 'Szybko zrozumiałem/zrozumiałam, czym zajmuje się firma.'),
    L('k3', 'Łatwo było znaleźć potrzebną informację lub kontakt.'),
    L('k4', 'Struktura i nawigacja były zrozumiałe.'),
    L('k5', 'Treści były jasne i konkretne.'),
    L('k6', 'Strona jest przejrzysta i atrakcyjna wizualnie.'),
    L('k7', 'Strona buduje zaufanie do marki.'),
    L('k8', 'Chętnie wrócił(a)bym na tę stronę.'),
    O('k9', 'Jakie pytanie o firmę lub ofertę pozostało bez odpowiedzi?'),
    O('k10', 'Co było największą trudnością?'),
  ],
}

/** Deep-clone a preset so the editable list never shares references. */
export function presetQuestions(key: PresetKey): PresetQuestion[] {
  return SURVEY_PRESETS[key].map((q) => ({ ...q, options: q.options ? [...q.options] : undefined }))
}
