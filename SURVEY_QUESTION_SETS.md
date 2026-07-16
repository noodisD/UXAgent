# Zestawy pytań ankietowych wg celu strony (survey presets)

Zasada (§3.1 metodologii): **pytania muszą pasować do rzeczywistego celu
strony.** Pytanie „czy checkout był łatwy?" na stronie katalogowej jak Pepco
(brak zakupu online) jest bezsensowne; pytanie „czy dokonałeś zakupu?" na
stronie-katalogu centrum handlowego jak Westfield też. Dlatego zamiast jednego
uniwersalnego zestawu używamy **rdzenia uniwersalnego + dodatku zależnego od
kategorii strony**.

## Na czym oparte

- **SUPR-Q** (Sauro / MeasuringU, 2015) — standard jakości UX strony: 4 wymiary —
  użyteczność, zaufanie, wygląd, lojalność; skala 1–5.
- **SUS** — ogólna użyteczność (10 pozycji, benchmark 68).
- **SEQ** (Single Ease Question) — łatwość konkretnego zadania; benchmark ~5,5.
- Heurystyki domenowe (§5 metodologii: e-commerce/Baymard, media, fintech,
  public itd.).

Wszystkie pytania Likerta: skala 1–5 (1 = zdecydowanie nie / bardzo trudne,
5 = zdecydowanie tak / bardzo łatwe). Format zgodny z kreatorem ankiety w
narzędziu: `{id, type, prompt, options}`.

## Jak wybrać kategorię

| Cel strony | Kategoria | Przykłady |
|---|---|---|
| Sprzedaż online z koszykiem i płatnością | `ecommerce` | Zalando, Allegro, sklep internetowy |
| Katalog produktów + lokalizator sklepu, **bez** zakupu online | `catalog` | **Pepco**, Action, Kik |
| Katalog/portal-przegląd: sklepy, oferty, wydarzenia, godziny, dojazd | `directory` | **Westfield**, galeria handlowa, katalog usług |
| Przeglądanie/oglądanie/czytanie treści, subskrypcja | `media` | streaming, portal wideo, prasa |
| Rezerwacja terminu / biletu / usługi | `booking` | wizyty, hotele, przejazdy, bilety |
| Bankowość / płatności / wysokie zaufanie | `fintech` | bank, portfel, przelewy |
| Usługi publiczne / urzędowe | `public` | e-urząd, wnioski, sprawy |
| Wizytówka firmy / marka / informacja | `corporate` | strona korporacyjna, landing |

---

## Rdzeń uniwersalny (dołączaj do KAŻDEJ kategorii)

Adaptacja SUPR-Q (użyteczność / zaufanie / wygląd / lojalność) + SEQ + pytania
otwarte. Narzędzie i tak dokleja syntetyczne SEQ i pewność (1–7) automatycznie —
poniższe są wersją 1–5 do ręcznej ankiety.

```json
{
  "questionnaire_id": "core_universal_v1",
  "title": "Rdzeń uniwersalny",
  "questions": [
    {"id": "u1", "type": "multiple_choice", "prompt": "Strona była łatwa w obsłudze.", "options": ["1","2","3","4","5"]},
    {"id": "u2", "type": "multiple_choice", "prompt": "Łatwo było poruszać się po stronie i znaleźć to, czego szukam.", "options": ["1","2","3","4","5"]},
    {"id": "t1", "type": "multiple_choice", "prompt": "Strona wydaje się wiarygodna i godna zaufania.", "options": ["1","2","3","4","5"]},
    {"id": "a1", "type": "multiple_choice", "prompt": "Strona jest przejrzysta i atrakcyjna wizualnie.", "options": ["1","2","3","4","5"]},
    {"id": "l1", "type": "multiple_choice", "prompt": "Chętnie skorzystał(a)bym z tej strony ponownie.", "options": ["1","2","3","4","5"]},
    {"id": "o1", "type": "short_text", "prompt": "Co było największą trudnością podczas korzystania ze strony?"},
    {"id": "o2", "type": "short_text", "prompt": "Czy jest coś, czego szukałeś/szukałaś, a czego nie udało się znaleźć lub zrozumieć?"}
  ]
}
```

---

## `ecommerce` — sprzedaż online (koszyk + płatność)

```json
{
  "questionnaire_id": "ecommerce_v1",
  "title": "E-commerce (zakup online)",
  "questions": [
    {"id": "e1", "type": "multiple_choice", "prompt": "Łatwo było znaleźć i porównać produkty.", "options": ["1","2","3","4","5"]},
    {"id": "e2", "type": "multiple_choice", "prompt": "Pełne koszty (cena, dostawa, opłaty) były jasne przed finalizacją.", "options": ["1","2","3","4","5"]},
    {"id": "e3", "type": "multiple_choice", "prompt": "Proces dodania do koszyka i przejścia do zamówienia był prosty.", "options": ["1","2","3","4","5"]},
    {"id": "e4", "type": "multiple_choice", "prompt": "Czułem/czułam kontrolę i bezpieczeństwo przy podawaniu danych do płatności.", "options": ["1","2","3","4","5"]},
    {"id": "e5", "type": "multiple_choice", "prompt": "Warunki dostawy i zwrotów były łatwe do znalezienia i zrozumienia.", "options": ["1","2","3","4","5"]},
    {"id": "e6", "type": "short_text", "prompt": "Co (jeśli cokolwiek) powstrzymałoby Cię przed finalizacją zakupu?"}
  ]
}
```

## `catalog` — katalog + lokalizator sklepu, bez zakupu online (np. Pepco)

Kluczowe: sprawdza **dopasowanie modelu** (czy użytkownik rozumie, że kupuje w
sklepie stacjonarnym) — częsty problem UX na stronach typu Pepco.

```json
{
  "questionnaire_id": "catalog_v1",
  "title": "Katalog / lokalizator sklepu (bez zakupu online)",
  "questions": [
    {"id": "c1", "type": "multiple_choice", "prompt": "Łatwo było znaleźć interesujący mnie produkt.", "options": ["1","2","3","4","5"]},
    {"id": "c2", "type": "multiple_choice", "prompt": "Informacje o produkcie (cena, rozmiar, materiał) były jasne i wystarczające.", "options": ["1","2","3","4","5"]},
    {"id": "c3", "type": "multiple_choice", "prompt": "Łatwo było sprawdzić dostępność produktu lub znaleźć najbliższy sklep.", "options": ["1","2","3","4","5"]},
    {"id": "c4", "type": "multiple_choice", "prompt": "Było dla mnie jasne, że zakupu dokonuję w sklepie stacjonarnym, a nie online.", "options": ["1","2","3","4","5"]},
    {"id": "c5", "type": "multiple_choice", "prompt": "Lokalizator sklepów działał sprawnie i pomógł mi znaleźć odpowiedni sklep.", "options": ["1","2","3","4","5"]},
    {"id": "c6", "type": "short_text", "prompt": "Czy oczekiwałeś/oczekiwałaś możliwości zakupu online? Co ewentualnie było mylące?"}
  ]
}
```

## `directory` — katalog/portal-przegląd: sklepy, oferty, wydarzenia (np. Westfield)

Kluczowe: **orientacja, findability i aktualność informacji** — nie transakcja.

```json
{
  "questionnaire_id": "directory_v1",
  "title": "Katalog/portal (galeria, oferty, wydarzenia)",
  "questions": [
    {"id": "d1", "type": "multiple_choice", "prompt": "Szybko zorientowałem/zorientowałam się, do czego służy ta strona.", "options": ["1","2","3","4","5"]},
    {"id": "d2", "type": "multiple_choice", "prompt": "Łatwo było znaleźć potrzebną informację (sklep, godziny otwarcia, wydarzenie, promocję).", "options": ["1","2","3","4","5"]},
    {"id": "d3", "type": "multiple_choice", "prompt": "Struktura i nawigacja strony były zrozumiałe.", "options": ["1","2","3","4","5"]},
    {"id": "d4", "type": "multiple_choice", "prompt": "Informacje wydawały się aktualne i kompletne (godziny, lokalizacje, wydarzenia).", "options": ["1","2","3","4","5"]},
    {"id": "d5", "type": "multiple_choice", "prompt": "Łatwo było znaleźć, jak dotrzeć na miejsce lub zorientować się w planie obiektu.", "options": ["1","2","3","4","5"]},
    {"id": "d6", "type": "short_text", "prompt": "Jakiej informacji szukałeś/szukałaś, a nie mogłeś/mogłaś jej znaleźć?"}
  ]
}
```

## `media` — treści / streaming

```json
{
  "questionnaire_id": "media_v1",
  "title": "Media / treści / streaming",
  "questions": [
    {"id": "m1", "type": "multiple_choice", "prompt": "Szybko znalazłem/znalazłam treści, które mnie interesują.", "options": ["1","2","3","4","5"]},
    {"id": "m2", "type": "multiple_choice", "prompt": "Było jasne, co się stanie po kliknięciu (odtwórz / subskrybuj / dodaj).", "options": ["1","2","3","4","5"]},
    {"id": "m3", "type": "multiple_choice", "prompt": "Kategorie i rekomendacje były trafne i pomocne.", "options": ["1","2","3","4","5"]},
    {"id": "m4", "type": "multiple_choice", "prompt": "Miałem/miałam jasną kontrolę nad odtwarzaniem lub subskrypcją.", "options": ["1","2","3","4","5"]},
    {"id": "m5", "type": "short_text", "prompt": "Czego zabrakło Ci w ofercie treści lub w sposobie ich prezentacji?"}
  ]
}
```

## `booking` — rezerwacja / bilet / usługa

```json
{
  "questionnaire_id": "booking_v1",
  "title": "Rezerwacja / usługa",
  "questions": [
    {"id": "b1", "type": "multiple_choice", "prompt": "Łatwo było znaleźć i wybrać dostępny termin lub opcję.", "options": ["1","2","3","4","5"]},
    {"id": "b2", "type": "multiple_choice", "prompt": "Cena i warunki rezerwacji były jasne przed potwierdzeniem.", "options": ["1","2","3","4","5"]},
    {"id": "b3", "type": "multiple_choice", "prompt": "Proces rezerwacji był prosty i nie wymagał zbędnych kroków.", "options": ["1","2","3","4","5"]},
    {"id": "b4", "type": "multiple_choice", "prompt": "Otrzymałem/otrzymałam jasne potwierdzenie rezerwacji.", "options": ["1","2","3","4","5"]},
    {"id": "b5", "type": "multiple_choice", "prompt": "Było jasne, jak zmienić lub anulować rezerwację.", "options": ["1","2","3","4","5"]}
  ]
}
```

## `fintech` — bankowość / płatności (wysokie zaufanie)

```json
{
  "questionnaire_id": "fintech_v1",
  "title": "Fintech / bankowość",
  "questions": [
    {"id": "f1", "type": "multiple_choice", "prompt": "Wiedziałem/wiedziałam dokładnie, komu i ile wysyłam lub co potwierdzam.", "options": ["1","2","3","4","5"]},
    {"id": "f2", "type": "multiple_choice", "prompt": "Miałem/miałam kontrolę i mogłem/mogłam zweryfikować dane przed potwierdzeniem.", "options": ["1","2","3","4","5"]},
    {"id": "f3", "type": "multiple_choice", "prompt": "Komunikaty bezpieczeństwa i błędów były zrozumiałe.", "options": ["1","2","3","4","5"]},
    {"id": "f4", "type": "multiple_choice", "prompt": "Ufam, że moje dane i środki są bezpieczne.", "options": ["1","2","3","4","5"]},
    {"id": "f5", "type": "multiple_choice", "prompt": "W razie pomyłki mogłem/mogłam cofnąć lub poprawić operację.", "options": ["1","2","3","4","5"]}
  ]
}
```

## `public` — usługi publiczne / urzędowe

```json
{
  "questionnaire_id": "public_v1",
  "title": "Usługi publiczne",
  "questions": [
    {"id": "p1", "type": "multiple_choice", "prompt": "Rozumiałem/rozumiałam swój obowiązek i kolejne kroki, bez wiedzy urzędowej.", "options": ["1","2","3","4","5"]},
    {"id": "p2", "type": "multiple_choice", "prompt": "Język strony był prosty i zrozumiały.", "options": ["1","2","3","4","5"]},
    {"id": "p3", "type": "multiple_choice", "prompt": "Wiedziałem/wiedziałam, jaki jest status mojej sprawy.", "options": ["1","2","3","4","5"]},
    {"id": "p4", "type": "multiple_choice", "prompt": "Formularz dało się wypełnić bez pomocy innej osoby.", "options": ["1","2","3","4","5"]},
    {"id": "p5", "type": "short_text", "prompt": "Co było najbardziej niejasne lub blokujące w załatwieniu sprawy?"}
  ]
}
```

## `corporate` — wizytówka / marka / informacja

```json
{
  "questionnaire_id": "corporate_v1",
  "title": "Strona korporacyjna / marka",
  "questions": [
    {"id": "k1", "type": "multiple_choice", "prompt": "Szybko zrozumiałem/zrozumiałam, czym zajmuje się firma / czego dotyczy strona.", "options": ["1","2","3","4","5"]},
    {"id": "k2", "type": "multiple_choice", "prompt": "Łatwo było znaleźć potrzebną informację lub kontakt.", "options": ["1","2","3","4","5"]},
    {"id": "k3", "type": "multiple_choice", "prompt": "Strona buduje zaufanie do marki.", "options": ["1","2","3","4","5"]},
    {"id": "k4", "type": "short_text", "prompt": "Jakie pytanie o firmę/ofertę pozostało bez odpowiedzi?"}
  ]
}
```

---

## Uwagi metodologiczne

- **Zawsze** łącz rdzeń uniwersalny + zestaw kategorii (razem ~10–13 pozycji =
  ankieta ~5 min).
- To sygnały **syntetyczne** (self-report agenta) — nie raportować jako realnych
  wskaźników populacyjnych (§1.2). Wymagają walidacji z realnymi użytkownikami.
- Dla Pepco domyślnie: `core_universal_v1` + `catalog_v1`.
- Dla Westfield domyślnie: `core_universal_v1` + `directory_v1`.
- Pytania otwarte (`short_text`) dają najlepszy materiał jakościowy — zostawiaj
  co najmniej jedno na zestaw.

## Źródła

- SUPR-Q — Sauro, MeasuringU: https://measuringu.com/product/suprq/ ,
  https://blog.uxtweak.com/supr-q/
- SUS / SEQ / benchmarki — MeasuringU: https://measuringu.com/ux-benchmarks/
- Heurystyki domenowe — wewnętrzna baza metodologiczna (§5), Baymard,
  W3C WCAG, NN/g.
