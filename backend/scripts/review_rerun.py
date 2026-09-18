"""Re-run the cases from the first external review against the current code.

Karolina Kniaziuk ran hyundai.com.pl, hebe.pl, lidl.pl and t-mobile.pl unaided
and reported four specific failures. This script reproduces those four cases so
the claim "the plan fixed them" can be checked against a live run rather than
inferred from tests:

  hyundai   a "recurring cookie banner" finding she says never happens to a
            real user, cited against a homepage observation that shows no
            banner at all
  hebe      a Cloudflare bot wall turned into six site-UX defects, one
            recommending the *site* add a timeout to its bot protection
  lidl      the session left the site under test and ended up on reserved.pl
  t-mobile  70 minutes, then nothing — no report and no error

One argument, the site key. Runs are deliberately small: the point is whether
the same false findings reappear, which shows up in two personas as clearly as
in ten, and a short run can be repeated cheaply when something needs checking.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from simulated_web_agent.main.run import run_async  # noqa: E402

# Demographics stay identical across sites so a difference between reports is a
# difference in the site or the code, never in who was sent to look.
DEMOGRAPHICS = [
    {
        "name": "Typ osoby",
        "choices": [
            {"name": "osoba pracująca, 30-45 lat, ceni szybkość i konkret", "weight": 1},
            {"name": "młoda dorosła, kupuje głównie przez telefon", "weight": 1},
        ],
    },
    {
        "name": "Doświadczenie cyfrowe",
        "choices": [
            {"name": "sprawnie porusza się po stronach internetowych", "weight": 2},
            {"name": "korzysta z internetu rzadziej, gubi się przy wielu opcjach", "weight": 1},
        ],
    },
]

QUESTIONNAIRE = {
    "questionnaire_id": "review_rerun_v1",
    "title": "Ankieta użyteczności",
    "questions": [
        {"id": "q1", "type": "multiple_choice",
         "prompt": "Czy łatwo było mi wykonać zadanie? (1 = Zdecydowanie nie, 5 = Zdecydowanie tak)",
         "options": ["1", "2", "3", "4", "5"]},
        {"id": "q2", "type": "multiple_choice",
         "prompt": "Czy strona była przejrzysta i czytelna? (1 = Zdecydowanie nie, 5 = Zdecydowanie tak)",
         "options": ["1", "2", "3", "4", "5"]},
        {"id": "q3", "type": "short_text",
         "prompt": "Co było największą trudnością podczas korzystania ze strony?"},
    ],
}

# start_url and intent are copied from the reports she was given, so the new
# run answers the same question about the same page. site_context is what the
# judgement passes calibrate against; without it a catalogue site is marked
# down for lacking a checkout it never had.
CASES = {
    "hyundai": {
        "start_url": "https://www.hyundai.com/pl/pl/konfigurator.html",
        "intent": (
            "Przejdź przez cały proces konfiguracji modelu i wypisz zalety tej ścieżki "
            "oraz wady i pomysły na ulepszenie jej pod kątem UX."
        ),
        "site_context": (
            "Strona producenta samochodów Hyundai w Polsce. Konfigurator służy do "
            "złożenia specyfikacji samochodu (model, wersja, silnik, kolor, wyposażenie) "
            "i zakończenia się zapytaniem ofertowym lub kontaktem z dealerem. "
            "Serwis NIE sprzedaje samochodów online — brak koszyka i płatności jest "
            "zgodny z przeznaczeniem strony i nie jest wadą użyteczności."
        ),
        "max_steps": 18,
    },
    "hebe": {
        "start_url": "https://www.hebe.pl/",
        "intent": "Zakup wybranego produktu z kategorii kosmetyków lub pielęgnacji z dostawą do domu.",
        "site_context": (
            "Drogeria internetowa Hebe. Pełna sprzedaż online: koszyk, dostawa do domu "
            "i do punktów odbioru. Ocena powinna dotyczyć ścieżki zakupowej."
        ),
        "max_steps": 18,
    },
    "lidl": {
        "start_url": "https://www.lidl.pl/",
        "intent": (
            "Sprawdź aktualną ofertę promocyjną i znajdź szczegóły wybranego produktu "
            "z gazetki, a następnie sprawdź, w którym sklepie w pobliżu jest dostępny."
        ),
        "site_context": (
            "Lidl Polska — serwis informacyjny sieci sklepów stacjonarnych z gazetką "
            "promocyjną, wyszukiwarką sklepów i osobnym sklepem online (Lidl Sklep) dla "
            "części asortymentu. Brak zakupu spożywczego online jest zgodny z modelem "
            "biznesowym i nie jest wadą użyteczności."
        ),
        "max_steps": 18,
    },
    "tmobile": {
        "start_url": "https://www.t-mobile.pl/",
        "intent": (
            "Znajdź ofertę abonamentu komórkowego dla siebie, porównaj co najmniej dwa "
            "warianty pod kątem ceny i zawartości, i przejdź do szczegółów wybranego."
        ),
        "site_context": (
            "Operator telekomunikacyjny T-Mobile Polska. Serwis prezentuje oferty "
            "abonamentowe, na kartę i usługi dodatkowe; zakup online jest możliwy. "
            "Ocena powinna dotyczyć wyboru i porównania oferty."
        ),
        "max_steps": 18,
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=sorted(CASES))
    ap.add_argument("--personas", type=int, default=2)
    ap.add_argument("--device", default="desktop")
    ap.add_argument("--max-steps", type=int, default=None)
    # hebe.pl serves a Cloudflare challenge to headless Chromium and the real
    # site to a headed one — verified by probing both. Nothing about the page is
    # different; only whether the browser has a display.
    ap.add_argument("--headed", action="store_true",
                    help="run with a visible browser (needed where bot protection blocks headless)")
    args = ap.parse_args()

    case = CASES[args.site]
    # Date-prefixed like new_run_id(), so these sort alongside real runs in the
    # history instead of clumping at one end, but named so they are obviously
    # the review re-runs and can be deleted as a group.
    run_id = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_review-{args.site}"

    def on_progress(event):
        # One line per event, flushed: this runs detached, so the log file is
        # the only way to see a stall while it is happening.
        print(f"[{args.site}] {event.get('label', '')} {event.get('detail', '')}".rstrip(),
              flush=True)

    asyncio.run(run_async(
        total_personas=args.personas,
        demographics=DEMOGRAPHICS,
        general_intent=case["intent"],
        start_url=case["start_url"],
        max_steps=args.max_steps or case["max_steps"],
        questionnaire=QUESTIONNAIRE,
        headless=not args.headed,
        concurrency=1,
        device=args.device,
        site_context=case["site_context"],
        on_progress=on_progress,
        run_id=run_id,
    ))

    manifest = json.loads((Path("runs") / run_id / "run_manifest.json").read_text(encoding="utf-8"))
    print(f"[{args.site}] run_id={run_id}", flush=True)
    print(f"[{args.site}] FINAL status={manifest.get('status')} "
          f"duration={manifest.get('duration_seconds')}s", flush=True)
    return 0 if manifest.get("status") in {"done", "error", "cancelled"} else 1


if __name__ == "__main__":
    os.environ.setdefault("UXAGENT_PERSONA_BUDGET_MIN", "12")
    sys.exit(main())
