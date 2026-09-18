"""Start podróży Z POKŁADU pojazdu - "jestem w 145, następny przystanek Sucha".

Wyszukiwarka pyta zwykle "skąd", czyli o MIEJSCE, w którym się stoi. Pasażer
siedzący w autobusie nie jest w żadnym takim miejscu: jest w pojeździe, który
właśnie gdzieś jedzie, a pytanie brzmi "co z tego zrobić" - dojechać nim do
końca, wysiąść wcześniej i przesiąść się, wysiąść i dojść albo wziąć rower.

Cała sztuczka jest w jednym zdaniu: **z pokładu pojazdu podróż zaczyna się na
NASTĘPNYM przystanku, w chwili, gdy ten pojazd z niego rusza**. Wszystko, co
pasażer może zrobić, zaczyna się właśnie tam i wtedy:

  * zostać w pojeździe - to zwykłe wsiadanie w ten sam kurs na tym przystanku,
    z zerowym czekaniem (kurs stoi tam dokładnie o tej sekundzie);
  * wysiąść i przesiąść się - to zwykłe wsiadanie w inny kurs z tego przystanku;
  * wysiąść i pójść pieszo (albo po rower, albo do auta) - to zwykłe przejście
    z tego przystanku.

Dzięki temu planer nie potrzebuje ani jednej nowej gałęzi: dostaje słupek
i sekundę jak przy każdym innym wyszukiwaniu (patrz planner.plan_flow), a cały
tryb sprowadza się do ROZPOZNANIA KURSU - który to właściwie pojazd - i do
opisania wyniku tak, żeby było widać, gdzie z niego wysiąść.

Rozpoznanie idzie po trzech rzeczach, bo dokładnie tyle widzi pasażer
w pojeździe: numer linii, kierunek z czoła pojazdu i nazwa następnego
przystanku (zapowiedź albo tablica). Z tego wychodzi jeden konkretny kurs
rozkładu: ten, który z tego słupka rusza najbliżej godziny pytania.
"""

from bisect import bisect_left

import gtfs
import timetables

# Jak daleko w przód szukamy kursu. Pasażer siedzi w pojeździe, więc następny
# przystanek jest o minuty, nie o godziny - ale linia nocna potrafi mieć
# godzinny takt i wtedy "najbliższy kurs" wypada naprawdę daleko. Okno jest
# tylko sufitem pomyłki (zła linia, zły przystanek), nie miarą czegokolwiek.
SEARCH_WINDOW_SEC = 3 * 3600

# Pętla końcowa nie ma odjazdów (patrz gtfs.stop_departures), więc kurs, który
# na wskazanym przystanku KOŃCZY bieg, trzeba znaleźć po przyjeździe. Robimy to
# przeglądając połączenia z okna [pytanie - tyle, pytanie + okno]: połączenie
# przyjeżdżające o T wyjechało najwyżej tyle wcześniej.
LONGEST_HOP_SEC = 40 * 60


def _hhmm(sec):
    """Sekundy na osi doby -> 'GG:MM' (ten sam zapis, co planner._fmt_time:
    kurs po północy ma w rozkładzie 25:10, a na zegarze 01:10)."""
    return f"{(sec // 3600) % 24:02d}:{(sec % 3600) // 60:02d}"


def _line_of(day, trip):
    """('146', 'bus') dla kursu - ten sam podział etykiety, co w plannerze
    (_line_parts) i w rozkładach (timetables.MODE_OF_LABEL)."""
    label = day.trip_info[trip][0]
    kind, _, num = label.partition(" ")
    return (num or label), timetables.MODE_OF_LABEL.get(kind, "other")


def _matches(day, trip, num, mode, headsign):
    """Czy ten kurs jest tym, o którym mówi pasażer.

    Kierunek porównujemy luźno (bez wielkości liter i białych znaków) i tylko
    wtedy, gdy pytający go podał: napis z czoła pojazdu bywa skrócony, a i tak
    rozstrzyga głównie sam przystanek - słupek we Wrocławiu należy do jednej
    krawędzi, więc linia mija go tylko w jedną stronę.
    """
    trip_num, trip_mode = _line_of(day, trip)
    if trip_num.casefold() != num.casefold():
        return False
    if mode and trip_mode != mode:
        return False
    if headsign:
        return day.trip_info[trip][1].strip().casefold() == headsign.strip().casefold()
    return True


def _by_departure(day, num, mode, stop_id, from_sec, headsign):
    """Kurs rozpoznany po ODJEŹDZIE z podanego słupka - zwykły przypadek.

    Indeks słupek -> odjazdy jest i tak w dniu (gtfs.stop_departures), więc
    pytanie kosztuje jedno przeszukanie binarne, a nie przemiatanie doby.
    """
    for dep, trip, _ in gtfs.departures_between(
            day, [stop_id], from_sec, from_sec + SEARCH_WINDOW_SEC):
        if _matches(day, trip, num, mode, headsign):
            return trip, dep, dep
    return None


def _by_arrival(day, num, mode, stop_id, from_sec, headsign):
    """Kurs, który na podanym słupku KOŃCZY bieg - nie ma z niego odjazdu, więc
    nie ma go w indeksie odjazdów i trzeba go znaleźć po przyjeździe.

    Osobna, wolniejsza ścieżka, bo dotyczy osobnego, rzadszego pytania
    ("jadę na pętlę, co dalej"). Przeglądamy okno połączeń, nie całą dobę:
    tablica jest posortowana po odjeździe, a przyjazd jest od niego późniejszy
    najwyżej o LONGEST_HOP_SEC.
    """
    best = None
    start = bisect_left(day.dep_times, from_sec - LONGEST_HOP_SEC)
    stop = bisect_left(day.dep_times, from_sec + SEARCH_WINDOW_SEC)
    for _dep_t, arr_t, _from_s, to_s, trip in day.conns[start:stop]:
        if to_s != stop_id or arr_t < from_sec:
            continue
        if best is not None and arr_t >= best[1]:
            continue
        if _matches(day, trip, num, mode, headsign):
            best = (trip, arr_t, arr_t)
    return best


def find_ride(day, num, mode, stop_id, from_sec, headsign=None):
    """Którym kursem jedzie pasażer - {"error"} albo opis startu z pokładu.

    `stop_id` to NASTĘPNY przystanek pojazdu (słupek, nie miejsce): stamtąd
    zaczyna się cała dalsza podróż, bez względu na to, czy pasażer w pojeździe
    zostanie, czy z niego wysiądzie.

    Godziną startu jest ODJAZD kursu z tego słupka, a nie przyjazd na niego.
    To ta sama sekunda dla obu dostępnych wtedy decyzji: pojazd rusza dalej
    albo pasażer zostaje na przystanku - i dopiero od niej cokolwiek się
    liczy. Postoju to zresztą w tych danych prawie nie dotyczy: we wrocławskim
    GTFS przyjazd różni się od odjazdu w 1636 z 1,16 mln wierszy stop_times.
    """
    num = " ".join((num or "").split())
    if not num:
        return {"error": "Podaj numer linii, którą jedziesz."}
    if stop_id not in day.stop_names:
        return {"error": "Nie znam takiego przystanku — wybierz go z listy."}

    found = (_by_departure(day, num, mode, stop_id, from_sec, headsign)
             or _by_arrival(day, num, mode, stop_id, from_sec, headsign))
    if found is None:
        nazwa = day.stop_names[stop_id]
        return {"error": f"Linia {num} nie przejeżdża już dziś przez przystanek "
                         f"„{nazwa}” — sprawdź numer, kierunek i przystanek."}

    trip, at_sec, dep_sec = found
    trip_num, trip_mode = _line_of(day, trip)
    return {
        "trip": trip,
        "stop": stop_id,
        "stop_name": day.stop_names[stop_id],
        "num": trip_num,
        "mode": trip_mode,
        "line": day.trip_info[trip][0],
        "headsign": day.trip_info[trip][1],
        # Sekunda na osi doby rozkładowej - stąd rusza całe wyszukiwanie.
        "sec": dep_sec,
        "at": _hhmm(at_sec),
    }


def mark_journeys(journeys, ride):
    """Dopisuje do gotowych propozycji to, czego pasażer w pojeździe naprawdę
    potrzebuje: GDZIE WYSIĄŚĆ i za ile to przystanków.

    Każda propozycja zaczyna się na tym samym słupku (patrz nagłówek modułu),
    więc są dokładnie dwa przypadki i oba trzeba nazwać:

      * pierwszy etap jedzie NASZYM kursem - siedzimy dalej i wysiadamy tam,
        gdzie ten etap się kończy, `stops` przystanków dalej;
      * cokolwiek innego (inna linia, przejście, rower) - wysiadamy na
        NAJBLIŻSZYM przystanku, czyli `stops` = 0.

    "Nasz kurs" rozpoznajemy po tym, że pierwszy przejazd rusza z naszego
    słupka, naszą linią, dokładnie o naszej sekundzie - ta sama linia nie
    odjeżdża z tego samego przystanku dwa razy w tej samej chwili, więc to
    jest ten sam pojazd. Porównanie po trip_id byłoby ściślejsze, ale etapy
    listy powstają z kawałków mapy, a te niosą godziny jednego kursu, nie jego
    identyfikator (patrz planner._segment_ride_leg).
    """
    for journey in journeys:
        legs = journey.get("legs") or []
        first = legs[0] if legs else None
        siedzimy = (first is not None and first.get("kind") == "ride"
                    and first.get("dep_sec") == ride["sec"]
                    and first.get("line") == ride["line"]
                    and first.get("from") == ride["stop_name"])
        if siedzimy:
            first["onboard"] = True
            journey["onboard"] = {
                "stop": first["to"],
                "stops": first["stops_count"],
                "time": first["to_time"],
                # Czy po wysiadce jedzie się dalej czymkolwiek, czy to już cel.
                "transfer": len(legs) > 1,
            }
        else:
            journey["onboard"] = {
                "stop": ride["stop_name"],
                "stops": 0,
                "time": ride["at"],
                "transfer": True,
            }
    return journeys


def directions(num, day, mode=None):
    """Kierunki linii pod wybór "jestem w pojeździe": co pisze na czole
    pojazdu i jakie przystanki ma jeszcze przed sobą.

    Grupujemy po NAPISIE NA CZOLE, a nie po ciągu przystanków jak
    timetables.line_timetable: pasażer w pojeździe widzi kierunek, nie wariant
    rozkładu, i nie ma jak odróżnić "KRZYKI" od "KRZYKI, ale przez zajezdnię".
    Wybór między wariantami tego samego kierunku i tak rozstrzyga się sam -
    o tym, którym kursem jedzie pytający, decyduje potem przystanek i godzina
    (patrz find_ride).

    Lista przystanków kierunku idzie z tego wariantu, który ma NAJWIĘCEJ
    kursów; krótsze są w praktyce jego początkiem albo końcem, więc pasażer
    kursu skróconego i tak znajdzie na niej swój przystanek. `trips` sumuje
    wszystkie warianty kierunku - to liczba kursów jadących tam dzisiaj, a nie
    liczba kursów jednego wariantu.
    """
    data = timetables.line_timetable(num, day, mode, geometry=False)
    if "error" in data:
        return data

    grouped = {}
    for variant in data["variants"]:
        key = variant["headsign"].strip().casefold()
        entry = grouped.get(key)
        if entry is None or variant["trips"] > entry["trips"]:
            grouped[key] = {
                "headsign": variant["headsign"],
                "from": variant["from"],
                "to": variant["to"],
                "trips": variant["trips"] + (entry["trips"] if entry else 0),
                "stops": variant["stops"],
            }
        else:
            entry["trips"] += variant["trips"]

    out = sorted(grouped.values(), key=lambda d: (-d["trips"], d["headsign"]))
    return {"num": data["num"], "mode": data["mode"], "label": data["label"],
            "date": data["date"], "directions": out,
            **({"note": data["note"]} if data.get("note") else {})}
