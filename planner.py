"""Wyszukiwanie najszybszego połączenia algorytmem CSA (Connection Scan).

CSA nie buduje grafu: wszystkie połączenia dnia (przejazdy między sąsiednimi
przystankami) są posortowane po czasie odjazdu i skanowane raz, liniowo.
Połączenie jest "osiągalne", jeśli jesteśmy już w tym kursie albo zdążymy
na jego odjazd na przystanku startowym.
"""

from bisect import bisect_left, bisect_right
from collections import deque
from datetime import datetime

import gtfs

TRANSFER_SEC = 120   # bufor bezpieczeństwa przy przesiadce na tym samym słupku
WALK_SEC = 180       # przejście między słupkami tego samego miejsca (patrz gtfs.py)

# Ile MUSI oszczędzić przesiadka, żeby w ogóle warto ją było proponować.
# Nocne linie zjeżdżają się w węźle i ruszają z niego tą samą minutą tą samą
# ulicą, więc różnice na końcu bywają czystym zaokrągleniem dwóch rozkładów -
# a wysiadanie z pojazdu, który sam dowozi do celu, kosztuje przejście na
# inny peron i ryzyko utraty połączenia na całe pół godziny. Suwak w panelu
# deweloperskim (transfer_gain_sec w API).
TRANSFER_GAIN_SEC = 600
# W jakim oknie w ogóle SZUKAMY wariantu bez przesiadki. Celowo niezależne
# od progu: suwak rozstrzyga, który wariant jest proponowany jako najlepszy,
# a nie który istnieje - przy progu 0 obie opcje mają dalej być widoczne.
# Tyle, ile wynosi górny koniec suwaka.
SEATED_HORIZON_SEC = 1800
INF = float("inf")


def plan_route(start_query, end_query, when=None, transfer_gain_sec=None):
    """Zwraca dict z trasą ('legs', czasy) albo z kluczem 'error'."""
    when = when or datetime.now()
    gain_sec = TRANSFER_GAIN_SEC if transfer_gain_sec is None else int(transfer_gain_sec)

    try:
        day = gtfs.load_day(when.date())
    except FileNotFoundError as e:
        return {"error": str(e)}

    start_name, source_stops, start_hints = gtfs.match_stop(start_query, day)
    if start_name is None:
        return _unknown_stop(start_query, start_hints)
    end_name, target_stops, end_hints = gtfs.match_stop(end_query, day)
    if end_name is None:
        return _unknown_stop(end_query, end_hints)
    if start_name == end_name:
        return {"error": "Przystanek początkowy i końcowy są takie same."}

    dep_sec = when.hour * 3600 + when.minute * 60 + when.second
    best_stop, best_arr, journey = _scan(day, source_stops, target_stops, dep_sec)

    if best_stop is None:
        return {
            "error": f"Nie znaleziono połączenia {start_name} → {end_name} "
                     f"po {_fmt_time(dep_sec)} tego dnia."
        }

    # /api/plan oddaje JEDNĄ trasę, więc bierzemy wariant proponowany jako
    # najlepszy przy obecnym progu (pełen wachlarz jest w /api/flow).
    legs = _variants(day, _reconstruct(day, journey, best_stop), gain_sec)[0]
    # Przyjazd bierzemy z samej trasy, nie z best_arr - w wariancie bez
    # przesiadki to może być świadomie oddana minuta czy dwie.
    arrival = _arrival_of(legs)
    first_dep = legs[0]["dep_sec"]
    _drop_private(legs)
    return {
        "start": start_name,
        "end": end_name,
        "departure": _fmt_time(first_dep),
        "arrival": _fmt_time(arrival),
        "travel_time": f"{round((arrival - first_dep) / 60)} min",
        "legs": legs,
    }


# Ile odjazdów pokazuje tablica przystanku pod kropką przesiadki. Tyle mieści
# się w dymku bez przewijania, a dalsze i tak są poza horyzontem decyzji
# "czym stąd pojechać".
TIMETABLE_LIMIT = 8
# Sufit dla `limit` z zapytania. Mapa przepływów prosi z zapasem, bo z tej listy
# zostawia potem tylko linie, w które sama pozwala tu wsiąść - a przy węźle,
# z którego odjeżdża pół miasta, te kilka właściwych bywa dopiero w trzeciej
# dziesiątce.
TIMETABLE_MAX = 60


def stop_timetable(stop_query, when=None, from_sec=None, limit=TIMETABLE_LIMIT,
                   point=None):
    """Tablica odjazdów jednego przystanku - to, co widać po najechaniu na
    kropkę przesiadki na mapie.

    Przystanek wskazuje się NAZWĄ albo `point` = (lat, lon). Po współrzędnych
    pyta mapa przepływów: kropki stawia z geometrii kawałków, więc zna
    położenie słupka, a nie jego nazwę (patrz gtfs.stop_at).

    `when` wyznacza DOBĘ rozkładową, `from_sec` godzinę na jej osi. Osobno,
    bo etap trasy potrafi wypaść po północy (odjazd 24:40 to wciąż rozkład
    dnia poprzedniego) - a wtedy sama godzina "00:40" wskazywałaby dobę
    o jedną za daleko. Bez `from_sec` liczymy od godziny z `when`.

    Przystanek rozumiemy jako MIEJSCE, nie słupek (patrz gtfs.match_stop):
    najeżdżając na węzeł, pasażer pyta o wszystko, co z niego odjeżdża, a nie
    o jeden peron, przy którym akurat wysiadł.
    """
    when = when or datetime.now()

    try:
        day = gtfs.load_day(when.date())
    except FileNotFoundError as e:
        return {"error": str(e)}

    if point is not None:
        name, stops = gtfs.stop_at(point[0], point[1], day)
        if name is None:
            return {"error": "W tym miejscu nie ma przystanku."}
    else:
        name, stops, hints = gtfs.match_stop(stop_query, day)
        if name is None:
            return _unknown_stop(stop_query, hints)

    if from_sec is None:
        from_sec = when.hour * 3600 + when.minute * 60 + when.second
    from_sec = int(from_sec)

    departures = []
    for dep_sec, trip, _stop_id in gtfs.stop_departures(day, stops, from_sec, limit):
        line, headsign = day.trip_info[trip]
        num, mode = _line_parts(line)
        departures.append({
            "time": _fmt_time(dep_sec),
            # Sekunda na osi doby - po niej front odsiewa odjazdy sprzed
            # horyzontu mapy; "HH:MM" po północy zawija się i nie da się
            # z niego porównywać (patrz gtfs.load_day).
            "sec": dep_sec,
            "in_min": round((dep_sec - from_sec) / 60),
            "line": line,
            "num": num,
            "mode": mode,
            "headsign": headsign,
        })

    return {"stop": name, "from_time": _fmt_time(from_sec), "departures": departures}


def _cheaper_boarding(earliest, journey, legs, stop, dep_t, board_legs):
    """Czy w kurs, którym już jedziemy, można wsiąść na `stop` mniejszą
    liczbą przejazdów niż w zapisanym punkcie wsiadania.

    Sam warunek "mniej przejazdów" nie wystarczy - trzeba jeszcze zdążyć na
    odjazd z tego przystanku, tym samym buforem co przy zwykłym wsiadaniu.
    Przystanek osiągnięty PRZEZ TEN kurs nigdy nie przejdzie: ma o przejazd
    więcej niż punkt wsiadania, więc przesunięcie nie potrafi rozciąć jazdy
    jednym pojazdem na dwa etapy.
    """
    reached = earliest.get(stop, INF)
    if reached is INF or legs[stop] >= board_legs:
        return False
    buffer = TRANSFER_SEC if journey[stop][0] == "ride" else 0
    return reached + buffer <= dep_t


def _scan(day, source_stops, target_stops, dep_sec, banned_labels=None, deadline=None):
    """Connection Scan: najwcześniejszy przyjazd do celu, ze śladem do rekonstrukcji.

    banned_labels to zbiór etykiet linii ("Tramwaj 17"), których skan ma nie
    używać - tak `plan_journeys` wymusza warianty strukturalnie inne od
    najszybszego. deadline ucina skan, gdy przy takim zakazie nie ma już
    czego szukać (inaczej skan jechałby do końca doby).
    """
    conns = day.conns
    earliest = {}
    journey = {}      # stop_id -> ("origin",) | ("ride", idx_wsiadania, idx_wysiadania) | ("walk", skad)
    trip_board = {}   # trip_id -> indeks połączenia, na którym wsiedliśmy do kursu
    trip_legs = {}    # trip_id -> liczba przejazdów PRZED wsiadaniem do kursu
    legs = {}         # stop_id -> liczba przejazdów w najlepszej drodze do niego

    # Użytkownik podaje nazwę przystanku, więc startuje ze wszystkich jego słupków.
    for stop in source_stops:
        earliest[stop] = dep_sec
        journey[stop] = ("origin",)
        legs[stop] = 0

    targets = set(target_stops)
    best_arr = INF
    best_stop = None
    best_legs = INF
    limit = INF if deadline is None else deadline

    for i in range(bisect_left(day.dep_times, dep_sec), len(conns)):
        dep_t, arr_t, dep_s, arr_s, trip = conns[i]
        if dep_t > best_arr or dep_t > limit:
            break                     # dalsze odjazdy nie mogą już poprawić wyniku
        if banned_labels and day.trip_info[trip][0] in banned_labels:
            continue

        if trip not in trip_board:
            reached = earliest.get(dep_s, INF)
            if reached is INF:
                continue
            # Bufor tylko przy przesiadce z pojazdu; przy starcie i po
            # przejściu pieszym czas przesiadki jest już uwzględniony.
            buffer = TRANSFER_SEC if journey[dep_s][0] == "ride" else 0
            if reached + buffer > dep_t:
                continue
            trip_board[trip] = i
            trip_legs[trip] = legs[dep_s]
        elif _cheaper_boarding(earliest, journey, legs, dep_s, dep_t,
                               trip_legs[trip]):
            # Jedziemy już tym kursem, ale właśnie mijamy przystanek, na
            # którym stalibyśmy MNIEJSZĄ liczbą przejazdów niż w zapisanym
            # punkcie wsiadania - w skrajnym przypadku sam start relacji.
            # Punkt wsiadania zapisuje się przy PIERWSZYM przystanku kursu,
            # do którego dało się zdążyć, a bywa nim miejsce, do którego
            # trzeba się dopiero dowieźć innym pojazdem. Bez przesunięcia
            # rekonstrukcja musi ten dojazd potem czymś wytłumaczyć i wypisuje
            # etap "dojedź dwa przystanki pod początek trasy tego autobusu",
            # choć autobus i tak zaraz przejeżdża obok nas. Godziny się przez
            # to nie zmieniają - to ten sam pojazd - więc przesunięcie tylko
            # zdejmuje z trasy etap, który niczego nie dawał.
            trip_board[trip] = i
            trip_legs[trip] = legs[dep_s]

        # Przy REMISIE na godzinie przyjazdu wygrywa droga z mniejszą liczbą
        # przejazdów - inaczej decyduje o tym kolejność skanowania i podróżny
        # dostaje polecenie przesiadki do sąsiedniego autobusu, który dowozi
        # go na miejsce o tej samej minucie. Lista propozycji z mapy
        # przepływów sortuje tak od dawna (patrz _enumerate_journeys: klucz
        # (arrival, len(chain) - 1, ...)); tu chodzi o to samo w samym skanie,
        # bo z niego bierze się trasa w gałęzi awaryjnej plan_flow.
        ride_legs = trip_legs[trip] + 1
        known = earliest.get(arr_s, INF)
        if arr_t < known or (arr_t == known and ride_legs < legs[arr_s]):
            earliest[arr_s] = arr_t
            journey[arr_s] = ("ride", trip_board[trip], i)
            legs[arr_s] = ride_legs
            if arr_s in targets and (arr_t < best_arr or
                                     (arr_t == best_arr and ride_legs < best_legs)):
                best_arr = arr_t
                best_stop = arr_s
                best_legs = ride_legs
            # Relaksacja pieszo na pozostałe słupki tego samego miejsca.
            for sibling in day.siblings.get(arr_s, ()):
                walk_arr = arr_t + WALK_SEC
                known_sib = earliest.get(sibling, INF)
                if walk_arr < known_sib or (walk_arr == known_sib
                                            and ride_legs < legs[sibling]):
                    earliest[sibling] = walk_arr
                    journey[sibling] = ("walk", arr_s)
                    legs[sibling] = ride_legs

    return best_stop, best_arr, journey


def _reconstruct(day, journey, last_stop, geo_db=None):
    """Odtwarza trasę od celu do startu i skleja ją w czytelne etapy.

    Z otwartym `geo_db` ścieżka etapu jest wycinkiem geometrii kursu (realne
    ulice i tory, tak jak na mapie przepływów); bez niego - łamaną po
    przystankach.

    Oddaje trasę taką, jaka wyszła ze skanu; wariant bez nieopłacalnych
    przesiadek dokłada obok _variants.
    """
    legs = []
    stop = last_stop
    while journey[stop][0] != "origin":
        entry = journey[stop]
        if entry[0] == "walk":
            from_stop = entry[1]
            legs.append(_walk_leg(day, from_stop, stop))
            stop = from_stop
        else:
            _, board_i, exit_i = entry
            board = day.conns[board_i]
            legs.append(_ride_leg(day, board[4], board[2], board[0], stop,
                                  day.conns[exit_i][1], geo_db))
            stop = board[2]
    legs.reverse()
    return legs


_PRIVATE_LEG_KEYS = ("_trip", "_from_id", "_to_id", "_arr_sec", "_stops_t")


def _next_ride(legs, i):
    """Indeks kolejnego przejazdu po `i` (po drodze może być przejście)."""
    for j in range(i + 1, len(legs)):
        if legs[j]["kind"] == "ride":
            return j
    return None


def _seated_exit(day, leg, nxt, gain_sec, allow_siblings):
    """Gdzie i o której pojazd z etapu `leg` sam dowozi tam, dokąd dojeżdża
    `nxt` - albo None, jeśli nie dowozi wcale lub za późno."""
    limit = nxt["_arr_sec"] + gain_sec
    rodzenstwo = day.siblings.get(nxt["_to_id"], ()) if allow_siblings else ()
    for i in gtfs.trip_conns(day, leg["_trip"]):
        _, arr_t, _, arr_s, _ = day.conns[i]
        if arr_t <= leg["_arr_sec"]:
            continue             # jeszcze przed naszym wysiadaniem
        if arr_t > limit:
            break                # czasy w kursie rosną - dalej może być tylko gorzej
        if arr_s == nxt["_to_id"] or arr_s in rodzenstwo:
            return arr_s, arr_t
    return None


def _seated_legs(day, legs, horizon_sec, geo_db=None):
    """Ta sama trasa bez przesiadek, które nie zarabiają na siebie - albo
    None, gdy nie ma czego zdejmować.

    Dla dwóch kolejnych przejazdów sprawdza, czy pojazd z pierwszego sam
    dojeżdża tam, gdzie kończy się drugi - i czy nie później niż `gain_sec`
    po nim. Jeśli tak, oba etapy (razem z przejściem między nimi) zastępuje
    jedną, dłuższą jazdą. Przyjazd może się przez to opóźnić o mniej niż
    `gain_sec`.

    Wejścia NIE rusza: oba warianty trasy - z przesiadką i bez - jadą dalej
    obok siebie na listę propozycji, a próg rozstrzyga tylko, który z nich
    jest proponowany jako najlepszy (patrz _variants). `horizon_sec` mówi,
    jak dużo później wolno dojechać, żeby wariant w ogóle uznać za sensowny
    do pokazania - to NIE jest próg opłacalności.

    Na CELU relacji dopuszczamy inny słupek tego samego miejsca - nocne linie
    zjeżdżają na różne perony jednego dworca (241 na 3512, 249 na 3519),
    a dla pasażera to ten sam przystanek, o który pytał. W środku trasy
    wymagamy dokładnie tego samego słupka, bo następny etap musi odjechać
    stamtąd, gdzie go zostawiliśmy.
    """
    if horizon_sec <= 0:
        return None
    legs = list(legs)
    zmienione = False
    while True:
        for i, leg in enumerate(legs):
            j = _next_ride(legs, i) if leg["kind"] == "ride" else None
            if j is None:
                continue
            cel = _seated_exit(day, leg, legs[j], horizon_sec,
                               allow_siblings=(j == len(legs) - 1))
            if cel is None:
                continue
            legs[i:j + 1] = [_ride_leg(day, leg["_trip"], leg["_from_id"],
                                       leg["dep_sec"], cel[0], cel[1], geo_db)]
            zmienione = True
            break
        else:
            return legs if zmienione else None


def _transfers(legs):
    return max(sum(1 for leg in legs if leg["kind"] == "ride") - 1, 0)


def _journey_cost(legs, gain_sec):
    """Przyjazd z karą za każdą przesiadkę - klucz wyboru wariantu
    "proponowany jako najlepszy". Sam przyjazd pokazujemy prawdziwy."""
    return _arrival_of(legs) + _transfers(legs) * gain_sec


def _variants(day, legs, gain_sec, geo_db=None):
    """Trasa w wariantach: tak jak wyszła ze skanu (najwcześniejszy przyjazd)
    i - jeśli jest co zdjąć - bez nieopłacalnych przesiadek. Obie zostają
    widoczne; kolejność mówi, którą przy obecnym progu uważamy za lepszą."""
    warianty = [legs]
    bez_przesiadki = _seated_legs(
        day, legs, max(gain_sec, SEATED_HORIZON_SEC), geo_db)
    if bez_przesiadki is not None:
        warianty.append(bez_przesiadki)
    warianty.sort(key=lambda w: (_journey_cost(w, gain_sec), _transfers(w)))
    return warianty


def _arrival_of(legs):
    """Godzina dojazdu do celu wg samych etapów - po sklejeniu w _stay_seated
    nie musi się już równać najwcześniejszemu możliwemu przyjazdowi."""
    rides = [leg for leg in legs if leg["kind"] == "ride"]
    return rides[-1]["_arr_sec"] if rides else None


def _drop_private(legs):
    """Zdejmuje pola robocze, żeby nie wyciekły do odpowiedzi API."""
    for leg in legs:
        for key in _PRIVATE_LEG_KEYS:
            leg.pop(key, None)
    return legs


def _ride_leg(day, trip, board_stop, board_dep, exit_stop, exit_arr, geo_db=None):
    """Etap przejazdu jednym kursem, od wsiadania do wysiadania.

    Prywatne pola `_trip`/`_from_id`/`_to_id` służą wyłącznie sklejaniu
    etapów w _stay_seated i są z odpowiedzi zdejmowane.
    """
    line, headsign = day.trip_info[trip]
    # Pełna lista przystanków etapu - do narysowania linii na mapie. `data=day`
    # jest potrzebne wyłącznie kursom kolejowym (patrz gtfs.trip_path).
    path_rows = gtfs.trip_path(trip, board_stop, board_dep, exit_stop, exit_arr, geo_db, day)
    coords = [day.stop_coords[s] for s, _, _ in path_rows]
    if geo_db is not None and len(coords) >= 2:
        coords = gtfs.shape_slice(day.trip_shape.get(trip), coords, geo_db)
    num, mode = _line_parts(line)
    return {
        "kind": "ride",
        "line": line,
        "num": num,
        "mode": mode,
        "headsign": headsign,
        "from": day.stop_names[board_stop],
        "from_time": _fmt_time(board_dep),
        "to": day.stop_names[exit_stop],
        "to_time": _fmt_time(exit_arr),
        "dep_sec": board_dep,
        "arr_sec": exit_arr,
        "minutes": round((exit_arr - board_dep) / 60),
        "stops": [day.stop_names[s] for s, _, _ in path_rows],
        "stops_count": max(len(path_rows) - 1, 1),
        "path": _round_path(coords),
        "_trip": trip,
        "_from_id": board_stop,
        "_to_id": exit_stop,
        "_arr_sec": exit_arr,
        # Godziny przejazdu przez kolejne przystanki etapu - do interpolacji
        # godziny w dowolnym punkcie linii na mapie (patrz _piece_times; tu
        # to samo, tylko dla trybu awaryjnego plan_flow, gdzie mapa rysuje
        # się wprost z etapów trasy, a nie z kawałków). Pole prywatne: do
        # odpowiedzi listy tras nie trafia (patrz _PRIVATE_LEG_KEYS).
        "_stops_t": [
            [*_round_path([day.stop_coords[stop]])[0],
             departure if i == 0 else arrival]
            for i, (stop, arrival, departure) in enumerate(path_rows)
        ],
    }


def _walk_leg(day, from_stop, to_stop):
    """Etap pieszy między słupkami tego samego miejsca (patrz gtfs.siblings) -
    współdzielony przez _reconstruct (rekonstrukcja CSA) i _enumerate_journeys
    (przesiadka między segmentami mapy przepływów)."""
    return {
        "kind": "walk",
        "text": f"Zmiana stanowiska na przystanku "
                f"{day.stop_names[to_stop]} (ok. {WALK_SEC // 60} min)",
        "minutes": WALK_SEC // 60,
        "from": day.stop_names[from_stop],
        "to": day.stop_names[to_stop],
        "dep_sec": 0,
        "path": _round_path([day.stop_coords[from_stop], day.stop_coords[to_stop]]),
    }


MODE_OF_LABEL = {"Tramwaj": "tram", "Autobus": "bus", "Pociąg": "train"}


def _line_parts(label):
    """'Tramwaj 17' -> ('17', 'tram') - numer na plakietkę i rodzaj do koloru."""
    kind, _, num = label.partition(" ")
    return (num or label), MODE_OF_LABEL.get(kind, "other")


def _round_path(coords):
    return [[round(lat, 5), round(lon, 5)] for lat, lon in coords]


# Okno czasowe: "pokaż trasy do X% dłuższe niż najszybsza" - procentowo,
# nie w minutach, żeby okno rosło razem z długością trasy zamiast być
# stałym naddatkiem (30 min "dodatku" to nic dla trasy godzinnej, ale
# 250% dla trasy 20-minutowej). Dwa dodatkowe suwaki łatają skrajności
# samej procentówki:
#   - floor (minimalne okno w sekundach) - bez niego krótka trasa (np. 3
#     min) przy 110% dostaje tylko ~18 s naddatku i prawie nic więcej się
#     nie mieści w oknie, nawet przy 200%;
#   - cap (maksymalne okno w sekundach) - żeby bardzo długa trasa nie
#     otwierała absurdalnie szerokiego okna przy wysokim %.
# Efektywne okno = clamp(czas_trasy × (pct/100 − 1), floor, cap).
DEFAULT_EXTRA_PCT = 125   # domyślnie: pokaż trasy do 125% czasu najszybszej
MIN_EXTRA_PCT = 110
MAX_EXTRA_PCT = 200        # (suwak w UI go nadpisuje)

DEFAULT_EXTRA_FLOOR_SEC = 300   # domyślnie: co najmniej 5 min naddatku
MIN_EXTRA_FLOOR_SEC = 0
MAX_EXTRA_FLOOR_SEC = 1800      # (suwak w UI go nadpisuje) - sufit 30 min

DEFAULT_EXTRA_CAP_SEC = 900     # domyślnie: najwyżej 15 min naddatku
MIN_EXTRA_CAP_SEC = 600
MAX_EXTRA_CAP_SEC = 7200        # (suwak w UI go nadpisuje) - sufit 120 min

Q_ANCHOR_TOL = 0.10     # tolerancja jasności przy porównaniu segmentów
                        # (patrz _extract_transfer_graph; kotwica końca mapy
                        # już jej nie używa - patrz _select_and_anchor)
BACKTRACK_TOL_SEC = 120 # wsiadanie nie może wymagać oddalenia się od celu
                        # (cofnięcia) o więcej niż 2 min
WAIT_CAP_SEC = 1200     # przesiadka "łączy" segmenty, gdy czekanie <= 20 min

MIN_RANGE_M = 200       # zasięg szukania słupków wokół klikniętego punktu
MAX_RANGE_M = 1500      # (suwak w UI go nadpisuje) - patrz gtfs.nearby_stops
DEFAULT_RANGE_M = 1000


DEFAULT_JOURNEY_LIMIT = 6     # domyślnie tyle propozycji tras szukamy/pokazujemy
MIN_JOURNEY_LIMIT = 1
MAX_JOURNEY_LIMIT = 20        # (suwak w UI go nadpisuje) - "na siłę" więcej wariantów
MAX_JOURNEY_CHAIN_LEGS = 4    # maks. liczba etapów przejazdu w jednej propozycji
MAX_JOURNEY_CANDIDATES = 18   # tyle łańcuchów zbieramy przed sortowaniem/ucięciem PRZY
                              # DOMYŚLNYM limicie (patrz CANDIDATES_PER_JOURNEY niżej)
MAX_JOURNEY_VISITS = 500      # sufit kosztu (węzły przeszukiwania) PRZY DOMYŚLNYM limicie
CANDIDATES_PER_JOURNEY = 3    # gdy suwak żąda więcej niż domyślne 6 - MAX_JOURNEY_CANDIDATES/
                              # DEFAULT_JOURNEY_LIMIT, żeby żądanie większej liczby
VISITS_PER_JOURNEY = 84       # propozycji faktycznie szukało głębiej, a nie tylko ucinało
                              # krócej listę tych samych paru znalezionych łańcuchów


def _resolve_endpoints(day, start_query, end_query, start_point, end_point, range_m):
    """Start i cel -> nazwy do pokazania + zbiory słupków do skanowania.

    Każda strona niezależnie: nazwa przystanku (match_stop, całe kanoniczne
    miejsce) albo dowolny punkt z mapy (słupki w zasięgu). Wspólne dla mapy
    przepływów i listy propozycji - obie muszą rozumieć endpointy tak samo.
    """
    resolved = {}
    for side, query, point, missing in (
        ("start", start_query, start_point, "startowego"),
        ("end", end_query, end_point, "docelowego"),
    ):
        stops_key = "source_stops" if side == "start" else "target_stops"
        if point is not None:
            lat, lon = point
            stops = gtfs.nearby_stops(lat, lon, day, range_m)
            if not stops:
                return {"error": f"Brak przystanków w zasięgu wybranego punktu {missing}."}
            resolved[side] = f"Wybrany punkt ({lat:.4f}, {lon:.4f})"
            resolved[stops_key] = stops
        else:
            name, stops, hints = gtfs.match_stop(query, day)
            if name is None:
                return _unknown_stop(query, hints)
            resolved[side] = name
            resolved[stops_key] = set(stops)

    if resolved["start"] == resolved["end"]:
        return {"error": "Przystanek początkowy i końcowy są takie same."}
    return resolved


def _deadline(best_arr, dep_sec, extra_pct=None, extra_floor_sec=None, extra_cap_sec=None):
    """Granica sensowności: najlepszy przyjazd + naddatek (trzy suwaki w UI,
    patrz DEFAULT_EXTRA_PCT/FLOOR/CAP powyżej) - naddatek to procent czasu
    najszybszej trasy, przycięty do [floor, cap] w sekundach."""
    extra_pct = (
        DEFAULT_EXTRA_PCT if extra_pct is None
        else max(MIN_EXTRA_PCT, min(MAX_EXTRA_PCT, extra_pct))
    )
    extra_floor_sec = (
        DEFAULT_EXTRA_FLOOR_SEC if extra_floor_sec is None
        else int(max(MIN_EXTRA_FLOOR_SEC, min(MAX_EXTRA_FLOOR_SEC, extra_floor_sec)))
    )
    extra_cap_sec = (
        DEFAULT_EXTRA_CAP_SEC if extra_cap_sec is None
        else int(max(MIN_EXTRA_CAP_SEC, min(MAX_EXTRA_CAP_SEC, extra_cap_sec)))
    )
    best_duration_sec = best_arr - dep_sec
    extra_sec = best_duration_sec * (extra_pct / 100 - 1)
    extra_sec = max(extra_floor_sec, min(extra_cap_sec, extra_sec))
    return best_arr + int(round(extra_sec))


def _no_connection(start_name, end_name, dep_sec):
    return {
        "error": f"Nie znaleziono połączenia {start_name} → {end_name} "
                 f"po {_fmt_time(dep_sec)} tego dnia."
    }


def _summarize_journey(legs, rides, arrival, dep_sec):
    """Nagłówek karty: odjazd, przyjazd, czas w drodze, czekanie, przesiadki."""
    first_dep = rides[0]["dep_sec"]
    return {
        "departure": _fmt_time(first_dep),
        "arrival": _fmt_time(arrival),
        "duration_min": round((arrival - first_dep) / 60),
        "wait_min": round((first_dep - dep_sec) / 60),
        "transfers": len(rides) - 1,
        "legs": legs,
    }


def plan_flow(start_query, end_query, when=None,
              start_point=None, end_point=None, range_m=None, extra_pct=None,
              extra_floor_sec=None, extra_cap_sec=None, journey_limit=None,
              transfer_gain_sec=None):
    """Mapa przepływów ("mrówki"): wszystkie użyteczne przejazdy start -> cel.

    Jednostką ODKRYWANIA jest KURS, nie pojedynczy przeskok: dla każdego
    kursu, do którego realnie da się wsiąść (skan w przód), rozważamy jazdę
    od przystanku wsiadania do celu albo do ostatniego wyjścia z WIDOCZNĄ
    kontynuacją (przesiadką na segment, który też jest narysowany) - narysowana
    sieć jest spójna od startu do celu, żaden fragment nie wisi w powietrzu.

    Jednostką RYSOWANIA nie jest już jednak cały kurs naraz: jasność w danym
    punkcie kursu odzwierciedla, jak dobrym wyborem jest wciąż w nim siedzieć
    W TYM MIEJSCU (co da się jeszcze osiągnąć STĄD), nie jak dobrym wyborem
    było wsiadanie do niego na starcie. Mijamy realną, porównywalnie widoczną
    przesiadkę i z niej NIE korzystamy -> jasność dalszej części TEGO SAMEGO
    fizycznego kursu spada do tego, co faktycznie zostaje osiągalne stąd.
    Jeden kurs może więc wyjść na mapie jako kilka kolejnych kawałków o
    różnej jasności (patrz _finalize_segments) - ale tylko tam, gdzie coś
    naprawdę się zmienia; korytarz bez mijanej, lepszej opcji nadal dostaje
    jedną, stałą jasność na całej narysowanej długości. Ta jasność per
    pozycja to WYŁĄCZNIE wynik konkretnych, znalezionych kontynuacji
    (_refine_brightness, patrz suffix-min tamże) - _discover_segments nie
    odrzuca żadnego zdążalnego wyjścia z góry na podstawie porównania
    sąsiednich przystanków (taki filtr istniał kiedyś, ale porównywał
    surowe `latest` - wartość zależną od aktualnego okna czasowego - więc
    dwa sąsiednie przystanki mogły zamienić się miejscami przy samym tylko
    poszerzeniu suwaka i gasić realne wyjścia; usunięty 2026-08-12, bo
    dokładnie ta sama gwarancja "brak migotania" wynika już poprawnie,
    stabilnie względem okna, z suffix-min w _refine_brightness).

    Liczone w krokach (patrz odpowiednie funkcje): odkrycie segmentów
    kandydujących (_discover_segments), dopracowanie ich jasności PER WYJŚCIE
    przez konkretne kontynuacje (_refine_brightness), próg + spójność
    narysowanej sieci (_select_and_anchor), pocięcie na kawałki i złożenie
    odpowiedzi z geometrią (_finalize_segments).

    Lista propozycji tras ("journeys") to NIE osobny algorytm - to ścieżki
    przeczytane wprost z tego samego, już narysowanego grafu segmentów
    (_extract_transfer_graph + _enumerate_journeys), więc lista nigdy nie
    pokaże przesiadki, której nie ma na mapie, i reaguje na te same suwaki
    (extra_pct/extra_floor_sec/extra_cap_sec) co mapa.

    extra_pct/extra_floor_sec/extra_cap_sec to suwaki okna czasowego: "pokaż
    trasy do X% dłuższe niż najszybsza, ale co najmniej floor i najwyżej cap
    sekund naddatku" (patrz _deadline) - procent zamiast stałej liczby minut,
    żeby okno skalowało się z długością trasy; floor/cap łatają skrajności
    (bardzo krótkie albo bardzo długie trasy). Nie ma osobnego progu
    jasności - wszystko w oknie czasowym jest pokazywane, jasność (q) służy
    już tylko do intensywności rysowania.
    journey_limit to ile propozycji tras SZUKAĆ (suwak w UI, patrz
    DEFAULT_JOURNEY_LIMIT/MIN_JOURNEY_LIMIT/MAX_JOURNEY_LIMIT) - wyższa
    wartość nie zmyśla nieistniejących wariantów, tylko każe
    _enumerate_journeys przeszukać graf głębiej (patrz CANDIDATES_PER_JOURNEY/
    VISITS_PER_JOURNEY); gdy w grafie jest ich mniej, dostaje się tyle, ile
    faktycznie da się złożyć.
    """
    when = when or datetime.now()
    range_m = (
        DEFAULT_RANGE_M if range_m is None
        else max(MIN_RANGE_M, min(MAX_RANGE_M, range_m))
    )
    journey_limit = (
        DEFAULT_JOURNEY_LIMIT if journey_limit is None
        else int(max(MIN_JOURNEY_LIMIT, min(MAX_JOURNEY_LIMIT, journey_limit)))
    )

    try:
        day = gtfs.load_day(when.date())
    except FileNotFoundError as e:
        return {"error": str(e)}

    ends = _resolve_endpoints(day, start_query, end_query, start_point, end_point, range_m)
    if "error" in ends:
        return ends
    start_name, source_stops = ends["start"], ends["source_stops"]
    end_name, target_stops = ends["end"], ends["target_stops"]

    dep_sec = when.hour * 3600 + when.minute * 60 + when.second
    gain_sec = TRANSFER_GAIN_SEC if transfer_gain_sec is None else int(transfer_gain_sec)

    # Najszybsza trasa wyznacza skalę ("większość mrówek") i jest zapasowym
    # planem, gdyby kotwiczenie (patrz niżej) przycięło wszystko do zera.
    best_stop, best_arr, best_journey = _scan(day, source_stops, target_stops, dep_sec)
    if best_stop is None:
        return _no_connection(start_name, end_name, dep_sec)
    deadline = _deadline(best_arr, dep_sec, extra_pct, extra_floor_sec, extra_cap_sec)

    earliest, arrived_by, trip_board = _forward(day, source_stops, dep_sec, deadline)
    latest = _backward(day, target_stops, dep_sec, deadline)

    # Punkt odniesienia reguły cofnięcia: im później można być na przystanku
    # i wciąż zdążyć (latest), tym bliżej celu się jest. Liczony względem
    # best_arr (STAŁEJ, patrz _refine_brightness - ten sam powód: q=1.0 dla
    # najszybszej trasy musi się odnosić do best_arr, nie do deadline), NIE
    # względem samego `latest` (policzonego do deadline) - inaczej suwak
    # okna, poszerzając się, mógłby gdzieś w mieście ujawnić zupełnie
    # niepowiązaną, szybką trasę z innego przystanku startowego, winduje
    # origin_latest i - wciąż w ramach TEGO SAMEGO progu BACKTRACK_TOL_SEC -
    # kasuje w _discover_segments kandydatów na zupełnie innych, wolniejszych
    # korytarzach, które z tamtą trasą nie mają nic wspólnego (ten sam rodzaj
    # niestabilności, co już raz naprawiony dla `bound` przez WAIT_CAP_SEC -
    # patrz FLOW_MAP_CONTRACT.md, punkt 9). `stop_latest` w _discover_segments
    # zostaje liczone względem deadline jak dotychczas - rośnie razem z oknem,
    # więc przy origin_latest ZAMROŻONYM na best_arr próg cofnięcia może z
    # oknem tylko złagodnieć, nigdy zaostrzeć.
    latest_at_best = _backward(day, target_stops, dep_sec, best_arr)
    origin_latest = max(
        (latest_at_best[s] for s in source_stops if s in latest_at_best), default=None,
    )
    target_set = target_stops

    segs = _discover_segments(
        day, dep_sec, deadline, earliest, arrived_by, trip_board,
        latest, origin_latest, target_set,
    )
    # Jeden skan wstecz daje ODCZYTANĄ odpowiedź "wysiadam tu o tej godzinie -
    # o której jestem w celu" dla każdego przystanku w oknie. To z niego bierze
    # się jasność; bez niego była szacowana (patrz _target_profile).
    profile = _target_profile(day, target_set, dep_sec, deadline)
    _refine_brightness(day, segs, target_set, deadline, best_arr, profile)
    kept, ranges = _select_and_anchor(day, segs, source_stops, target_set)

    gtfs.geo_generation()      # jeden stat na zapytanie; czyści cache po podmianie bazy
    geo_db = gtfs.open_db()    # jedno połączenie na WSZYSTKIE wycinki geometrii zapytania
    try:
        # Najszybsza trasa i tak jest już policzona wyżej (_scan wyznacza nią
        # skalę całej mapy) - odtwarzamy ją raz, tutaj, żeby front mógł podać
        # "najszybciej tyle a tyle" i pokazać, KTÓRĄ trasą to jest, bez
        # sięgania po listę propozycji (i bez drugiego szukania).
        best_legs = _reconstruct(day, best_journey, best_stop, geo_db)
        fastest = _fastest_summary(best_legs, best_arr, dep_sec)
        degraded = False
        if kept:
            seg_list, nodes = _finalize_segments(
                day, kept, ranges, geo_db, earliest, profile[2], deadline,
                source_stops)
            graph = _extract_transfer_graph(day, kept, ranges, source_stops, target_set)
            journeys = _enumerate_journeys(day, graph, dep_sec, geo_db,
                                           limit=journey_limit, gain_sec=gain_sec)
        else:
            # Zabezpieczenie: _scan już udowodnił, że połączenie istnieje
            # (best_stop nie jest None), więc jeśli kotwiczenie i tak
            # przycięło WSZYSTKO do zera, narysuj i wylistuj przynajmniej
            # samą najszybszą trasę zamiast pustej odpowiedzi.
            #
            # To NIE jest zwykła mapa i odpowiedź mówi o tym wprost
            # (degraded), bo łamie kontrakt: rysuje jedną trasę zamiast
            # całego wachlarza (punkt 1), a jasności ma wpisane na sztywno,
            # nie policzone (punkty 2 i 9). Bez tego znacznika rzadka mapa
            # wygląda tak samo jak "tędy naprawdę nic nie jedzie".
            #
            # Kiedy tu wpadamy: gdy nic nie zazębia się w łańcuch start ->
            # cel, np. jedyna kontynuacja najlepszej trasy zawraca po
            # przystankach, które właśnie minęliśmy (_leads_onward), a
            # nigdzie po drodze nie da się wsiąść na przystanku startowym.
            # Nie jest to przypadek "skrajny i rzadki", jak głosił tu
            # komentarz do 2026-08-27: zwykła dzienna relacja przez pół
            # miasta (Sosnowiecka -> Wojszyce, 15:37) w niego wpadała, bo
            # kotwica początku pytała "czy kurs się tu ZACZYNA" zamiast
            # "czy da się TU wsiąść" (patrz _select_and_anchor). Po tamtej
            # naprawie na przemiecie 64 realnych relacji nie wpada w niego
            # już żadna, ale konstrukcyjnie wciąż jest osiągalny.
            # Najszybsza trasa i - jeśli jest co zdjąć - ta sama bez
            # nieopłacalnej przesiadki. Pokazujemy OBIE; pierwsza jest tą
            # proponowaną jako najlepsza przy obecnym progu.
            warianty = _variants(day, best_legs, gain_sec, geo_db)
            degraded = True
            journeys = []
            seg_list = []
            nodes = []
            narysowane = set()
            for rank, wariant in enumerate(warianty):
                rides = [leg for leg in wariant if leg["kind"] == "ride"]
                if not rides:
                    continue
                # Przyjazd bierzemy z samej trasy; best_arr zostaje
                # najwcześniejszym możliwym i dalej wyznacza okno mapy.
                arrival = _arrival_of(wariant) or best_arr
                journeys.append(_summarize_journey(
                    wariant, rides, arrival, dep_sec))
                # Jaśniej rysujemy wariant proponowany - tak jak wszędzie
                # indziej na tej mapie jasność znaczy "lepsza opcja".
                waga = 1.0 if rank == 0 else 0.6
                for leg in rides:
                    num, mode = _line_parts(leg["line"])
                    klucz = (num, mode, tuple(map(tuple, leg["path"])))
                    if klucz in narysowane:
                        continue
                    narysowane.add(klucz)
                    item = {"path": leg["path"], "num": num, "kind": mode, "w": waga}
                    # Tryb awaryjny też ma podawać godziny - inaczej mapa raz
                    # je ma, a raz nie, zależnie od tego, czy kotwiczenie coś
                    # zostawiło. Cel osiąga się tu z definicji tą trasą, więc
                    # przyjazd jest odczytany, nie zgadnięty.
                    if leg.get("_stops_t"):
                        item["stops_t"] = leg["_stops_t"]
                        item["arrive"] = arrival
                    seg_list.append(item)
            seg_list.sort(key=lambda seg: seg["w"])   # blade pierwsze, jaskrawe na wierzchu
            for wariant in warianty:
                _drop_private(wariant)
    finally:
        geo_db.close()

    return {
        "start": start_name,
        "end": end_name,
        "departure": _fmt_time(dep_sec),
        "best_arrival": _fmt_time(best_arr),
        "deadline": _fmt_time(deadline),
        # Cały zakres czasowy mapy w sekundach, tą samą miarą co kawałki:
        # od najszybszego możliwego dojazdu do najpóźniejszego, jaki mapa
        # jeszcze rysuje (deadline). "Najszybciej X, pokazane do Y".
        "best_sec": best_arr - dep_sec,
        "limit_sec": deadline - dep_sec,
        # Horyzont mapy na osi doby. Odjazd późniejszy nie należy do ŻADNEGO
        # rysowanego wariantu - to warunek konieczny, liczony z best_arr
        # (skan CSA), więc nie zależy od szacowanych przyjazdów kawałków.
        "deadline_sec": deadline,
        "fastest": fastest,
        "segments": seg_list,
        # Węzły przesiadkowe: po jednym na miejsce, z liniami, w które MAPA
        # pozwala tu wsiąść (patrz _transfer_nodes).
        "nodes": nodes,
        "journeys": journeys,
        # True tylko w trybie awaryjnym (patrz gałąź else wyżej): mapa jest
        # wtedy jedną trasą z jasnościami wpisanymi na sztywno, a nie
        # wachlarzem opcji. Front ma po czym poznać, że pokazuje coś innego
        # niż zwykle - i nie brać rzadkiej mapy za "tędy nic nie jedzie".
        "degraded": degraded,
    }


def _fastest_summary(legs, arrival, dep_sec):
    """Najszybsza trasa w postaci minimalnej: ile trwa i którędy biegnie.

    To NIE jest pozycja listy propozycji tras - nie ma tu przystanków,
    godzin ani opisów etapów, tylko tyle, ile trzeba, żeby napisać "najszybciej
    X min" i po najechaniu na tę liczbę pokazać na mapie, która to trasa."""
    return {
        "sec": arrival - dep_sec,
        "arrival": _fmt_time(arrival),
        "legs": [
            {
                "num": leg["num"],
                "kind": leg["mode"],
                "sec": leg["minutes"] * 60,
                "path": leg["path"],
            }
            for leg in legs if leg["kind"] == "ride"
        ],
    }


def _discover_segments(day, dep_sec, deadline, earliest, arrived_by, trip_board,
                        latest, origin_latest, target_set):
    """Krok 1: dla każdego kursu w oknie wybiera miejsce wsiadania (reguła
    cofnięcia, patrz BACKTRACK_TOL_SEC) i idzie nim naprzód zbierając
    KAŻDE zdążalne wyjście - zwraca listę segmentów kandydujących, jeszcze
    bez dopracowanej jasności (patrz _refine_brightness).

    Jedyny filtr tutaj jest ABSOLUTNY, nie względny: wyjście liczy się,
    gdy jazda do tego przystanku i tak jeszcze mieści się w oknie (`arr_t
    <= leave_by`, patrz niżej) - to samo `leave_by` sprawdzane przeciw
    SOBIE SAMEMU (własny przyjazd vs własny termin), więc wynik jest
    stabilny względem szerokości okna: raz zdążalny przystanek zostaje
    zdążalny przy każdym szerszym oknie, nigdy na odwrót.

    Było tu kiedyś DRUGIE, WZGLĘDNE sito ("reguła postępu") porównujące
    `latest` sąsiednich przystanków tego samego kursu, żeby odrzucić z
    góry przystanki "bez postępu" (miało to realizować punkt 3 kontraktu -
    jasność ma spadać po minięciu realnej, lepszej przesiadki). USUNIĘTE
    2026-08-12: `latest` dwóch sąsiednich przystanków rośnie z oknem w
    RÓŻNYM tempie (każdy zależnie od tego, jaka akurat alternatywa jest w
    danym miejscu "widoczna" w oknie), więc ich względna kolejność
    potrafiła się odwrócić przy samym tylko poszerzeniu suwaka - przystanek
    uznany za "postęp" przy jednej szerokości okna przestawał nim być przy
    szerszej, gasząc realne, niezmienione fizycznie wyjście (stąd zgłoszenie
    "trasy znikają przy zwiększeniu okna"). Podniesienie tolerancji tego
    porównania (dawny suwak "Tolerancja regresji") tylko przesuwało próg
    szumu, nie usuwało go - w gęstszej siatce POGARSZAŁO sprawę. Punkt 3 nie
    wymaga tego filtra: _refine_brightness i tak liczy jasność KAŻDEGO
    zdążalnego wyjścia z osobna przez suffix-min najlepszej REALNIE
    znalezionej kontynuacji (nie przez surowe `latest`) - a to jest
    dowodliwie monotoniczne (widoczny, jeśli w ogóle zdążalny) i stabilne
    względem okna (patrz komentarz przy `bound` niżej i punkt 9 kontraktu).
    Filtr "z góry" był więc nadmiarowy wobec już poprawnej, stabilnej
    maszynerii niżej w potoku - i to on, nie ona, był źródłem niestabilności.
    """
    conns = day.conns
    trip_conns = {}   # kurs -> indeksy jego połączeń w oknie [dep_sec, deadline)
    for i in range(
        bisect_left(day.dep_times, dep_sec),
        bisect_left(day.dep_times, deadline),
    ):
        trip = conns[i][4]
        if trip in trip_board:
            trip_conns.setdefault(trip, []).append(i)

    raw = {}     # (linia, pełna trasa) -> dane segmentu
    for trip, idxs in trip_conns.items():
        stops_seq = None
        departures = []   # (przystanek, odjazd) wzdłuż kursu - do przesiadek
        arrivals = []     # (przystanek, przyjazd) wzdłuż kursu - do etapów tras
        exits = []   # (pozycja w stops_seq, bound, przyjazd, przystanek)
        for i in idxs:
            dep_t, arr_t, dep_s, arr_s, _ = conns[i]
            if stops_seq is None:
                # Wybór miejsca wsiadania: pierwszy przystanek kursu, na
                # który zdążymy i którego osiągnięcie nie wymaga cofnięcia
                # się (oddalenia od celu) o więcej niż BACKTRACK_TOL_SEC.
                # To ucina np. "podjedź na pętlę i wracaj tym samym wozem".
                reached = earliest.get(dep_s)
                if reached is None:
                    continue
                buffer = TRANSFER_SEC if arrived_by[dep_s] == "ride" else 0
                if reached + buffer > dep_t:
                    continue
                stop_latest = latest.get(dep_s)
                # Reguła cofnięcia dotyczy tylko wsiadania w TRAKCIE podróży -
                # bycie na którymkolwiek z własnych przystanków startowych
                # nigdy nie jest cofnięciem, nawet jeśli inny przystanek
                # startowy (klik w punkt mapy rozwija się w kilka fizycznie
                # różnych słupków w zasięgu) akurat ma lepsze dalsze
                # połączenia - to nie "oddalenie się od celu", tylko wybór
                # KTÓREGO z kilku równie zerokosztowych startów użyć.
                if (arrived_by[dep_s] != "origin"
                        and origin_latest is not None and stop_latest is not None
                        and stop_latest < origin_latest - BACKTRACK_TOL_SEC):
                    continue
                stops_seq = [dep_s]
                seen_places = {day.place_of.get(dep_s, dep_s): 0}
            elif dep_s != stops_seq[-1]:
                break                        # przerwany łańcuch - utnij
            # Kurs wracający na MIEJSCE, przez które już przejechał, dalej nie
            # wiezie - zawraca (punkt 4 kontraktu: "mapa wjeżdża na pętlę
            # końcową tylko po to, żeby zaraz z niej wrócić"). Zgłoszone
            # 2026-08-29: autobus 102 rysował zjazd na pętlę pod Kosmonautów
            # i natychmiastowy powrót tą samą ulicą.
            #
            # Po MIEJSCU, nie po słupku: pętla nawrotowa ma zwykle osobny
            # słupek w tę i we w tę, więc porównanie identyfikatorów niczego
            # by nie złapało.
            #
            # Cięcie jest bezpieczne, bo trafia wyłącznie w nawroty: na
            # przemiecie 4 relacji wszystkie 14 zawróceń mieściło się w 1-2
            # przystankach, ani jedno nie było linią realnie obsługującą to
            # samo miejsce drugi raz w dalszym przebiegu.
            #
            # SĄSIEDNIE przystanki tego samego miejsca to NIE zawrócenie:
            # miejsce bywa grubsze od słupka i linia potrafi minąć dwa jego
            # przystanki jeden po drugim, jadąc prosto (tramwaj 7 mija tak
            # dwa razy Kamieńskiego w drodze na Klecinę). Zawróceniem jest
            # dopiero POWRÓT - wyjazd z miejsca i przyjazd do niego z powrotem,
            # czyli odległość co najmniej dwóch kroków.
            arr_place = day.place_of.get(arr_s, arr_s)
            wczesniej = seen_places.get(arr_place)
            if wczesniej is not None and len(stops_seq) - wczesniej >= 2:
                break
            seen_places.setdefault(arr_place, len(stops_seq))
            departures.append((dep_s, dep_t))
            arrivals.append((arr_s, arr_t))
            stops_seq.append(arr_s)
            leave_by = latest.get(arr_s)
            if leave_by is None or arr_t > leave_by:
                continue   # ten konkretny przystanek już nie mieści się w oknie
            # bound: najwcześniejszy możliwy przyjazd do celu, jeśli
            # wysiądziemy tutaj, ZANIM odczyta się realną wartość (patrz
            # _target_profile): arr_t + "kara" za nieznaną
            # resztę trasy. Karą jest (deadline - leave_by) OGRANICZONE do
            # WAIT_CAP_SEC (ten sam, już przyjęty w kodzie próg "jeszcze
            # spójnej przesiadki", patrz _catchable) - NIE samo
            # (deadline - leave_by) bez ograniczenia: leave_by bywa
            # spłaszczone realną częstotliwością kursów (ostatni kurs dnia
            # z danego przystanku) i przy szerszym oknie przestaje rosnąć,
            # więc bez ograniczenia kara rosłaby wraz z suwakiem okna bez
            # końca - wyjście stawało się WIDOCZNIE gorsze tylko dlatego, że
            # przesunięto suwak "pokaż więcej", co jest sprzeczne z jego
            # intencją (patrz plan_flow). Ograniczenie do WAIT_CAP_SEC nie
            # zmienia niczego, dopóki okno jest ciasne (kara i tak wypada
            # mniejsza od sufitu - dokładnie jak dawniej), a tylko zatrzymuje
            # dalszy wzrost, gdy okno urośnie ponad sensowną, stałą wartość -
            # poszerzenie okna nie może już POGORSZYĆ tej estymaty, tylko
            # najwyżej zastąpić ją odczytaną wartością z _target_profile.
            exits.append((
                len(stops_seq), arr_t + min(WAIT_CAP_SEC, deadline - leave_by),
                arr_t, arr_s,
            ))
            if arr_s in target_set:
                break    # dojechaliśmy do celu - dalej nie rysujemy
        if not exits:
            continue     # kurs bez użytecznego wyjścia - nie rysujemy go wcale
        best_bound = min(e[1] for e in exits)
        label, headsign = day.trip_info[trip]
        key = (label, tuple(stops_seq))
        entry = raw.get(key)
        if entry is None or best_bound < entry["_raw_bound"]:
            entry = raw[key] = {
                "label": label,
                "headsign": headsign,             # do etapów tras (patrz _segment_ride_leg)
                "trip_id": trip,                  # jw. - zabezpieczenie/debug
                "stops": stops_seq,
                "pos_of": {s: p for p, s in enumerate(stops_seq)},
                "exits": exits,
                "best_deps": dict(departures),    # odjazdy najlepszego kursu
                "arr_times": dict(arrivals),       # przyjazdy najlepszego kursu
                "dep_times": entry["dep_times"] if entry else {},
                "shape": day.trip_shape.get(trip),
                "_raw_bound": best_bound,
            }
        for stop, dep in departures:
            entry["dep_times"].setdefault(stop, []).append(dep)
    return list(raw.values())


def _sibling_places(day, stop):
    """Ten sam przystanek plus jego siblingi - dosłownie ten sam słupek albo
    sąsiedni w tym samym miejscu (patrz gtfs._walking_bridges). Jedyne
    miejsce, które rozwija "przystanek -> te same fizyczne miejsce", żeby
    _refine_brightness i _select_and_anchor nie robiły tego niezależnie."""
    return (stop, *day.siblings.get(stop, ()))


def _board_index(day, segs):
    """Przystanek (+ siblingi) -> segmenty, w które da się tam wskoczyć (mają
    tam zapisany odjazd). Współdzielone przez _refine_brightness (szukanie
    kontynuacji) i _select_and_anchor (kotwica końca)."""
    index = {}
    for seg in segs:
        for stop in seg["dep_times"]:
            for anchor in _sibling_places(day, stop):
                index.setdefault(anchor, []).append(seg)
    return index


def _refine_brightness(day, segs, target_set, deadline, best_arr, profile):
    """Krok 2: surowe przybliżenie wyjścia (patrz _discover_segments: arr_t +
    WAIT_CAP_SEC) to tylko zgadywanka na wypadek braku realnej kontynuacji -
    ma stały naddatek niezależny od deadline, więc samo w sobie nie
    przekłamuje jasności przy przesunięciu suwaka okna czasowego, ale wciąż
    nic nie wie o faktycznej dalszej trasie. Wartość każdego WYJŚCIA
    ODCZYTUJEMY więc z profilu (patrz _target_profile): "wysiadam tu o tej
    godzinie - o której najwcześniej jestem w celu". Wyjścia na sam cel są
    z definicji dokładne (wartość = przyjazd).
    Ustawia seg['bound']/seg['q'].

    Do 2026-08-29 stała tu zamiast tego iteracja do punktu stałego po
    kontynuacjach WIDOCZNYCH NA MAPIE (join_value): bierz gotową wartość
    sąsiedniego segmentu i przesuń ją o opóźnienie wsiadania. Dwa błędy
    naraz - zakładała, że sztywny rozkład przesunie się elastycznie o tyle
    samo na całej dalszej długości, i oznaczała wynik jako ODCZYTANY, choć
    powstał z oszacowania (a to oszacowanie mogło samo pochodzić z kolejnego).
    Stąd sprzeczność widoczna gołym okiem: kawałek podawał godzinę przyjazdu,
    a jego jedyna kontynuacja nie znała żadnej. Profil zna odpowiedź dla
    KAŻDEGO przystanku w oknie, nie tylko dla narysowanych, i to bez iteracji.

    q=1.0 musi wypaść dokładnie dla trasy najszybszej (bound == best_arr) -
    stąd odniesienie do best_arr, NIE do deadline: przy oknie zerowym
    (okno=0, deadline == best_arr) odległość "deadline - bound" dla
    jedynej ocalałej, optymalnej trasy też wynosi 0, więc licząc względem
    deadline wyszłoby q=0 (najciemniej) właśnie dla trasy, która powinna
    świecić najjaśniej - mapa wtedy rysowała wszystko jako ledwie widoczne
    duchy, łącznie z jedyną prawdziwą propozycją.

    Mianownik (span) to osobna sprawa - patrz jego wyliczenie niżej, tuż
    przed q_of.
    """
    for seg in segs:
        for times in seg["dep_times"].values():
            times.sort()

    for seg in segs:
        vals, exact = [], []
        for _pos, _raw, arr_t, stop in seg["exits"]:
            if stop in target_set:
                vals.append(arr_t)        # wyjście na sam cel: wartość = przyjazd
                exact.append(True)
                continue
            # INF znaczy tu coś konkretnego, nie "nie wiem": WYSIADANIE TUTAJ
            # do niczego nie prowadzi. Prawie zawsze dlatego, że jedyną
            # kontynuacją jest ten sam pojazd, a na przesiadkę nie starcza
            # nawet bufora (129 ze 129 takich wyjść na relacji LEŚNICA ->
            # BARTOSZOWICE, 16:44). Wartość tej POZYCJI weźmie się wtedy z
            # sufiksu niżej - z dalszych wyjść tego samego kursu, czyli z
            # jazdy dalej. Wpisywanie tu kary z _discover_segments byłoby
            # zmyśleniem gorszej opcji tam, gdzie opcji po prostu nie ma -
            # a że kar było więcej niż odczytów, to ONE wyznaczały skalę
            # jasności całej mapy.
            value = _profile_value(day, profile, stop, arr_t)
            vals.append(value)
            exact.append(value != INF)
        seg["exit_vals"] = vals
        seg["exit_exact"] = exact

    for seg in segs:
        # suffix[j] = najlepsza wartość osiągalna z pozycji j LUB PÓŹNIEJ, czyli
        # to, co jeszcze zostaje w zasięgu, gdy WCIĄŻ się w tym pojeździe siedzi.
        # Minimum niesie ze sobą swoją "dokładność": jeśli najlepsze osiągalne
        # stąd wyjście jest zgadnięte, to cała ta pozycja jest zgadnięta.
        suffix = list(seg["exit_vals"])
        exact = list(seg["exit_exact"])
        for j in range(len(suffix) - 2, -1, -1):
            if suffix[j + 1] < suffix[j]:
                suffix[j] = suffix[j + 1]
                exact[j] = exact[j + 1]
        # Co zostało na INF, to ogon za ostatnim użytecznym wyjściem (sufiks
        # jest niemalejący, więc INF-y mogą siedzieć tylko na końcu): dalsza
        # jazda tym kursem nie prowadzi już nigdzie w oknie. Tu dopiero wchodzi
        # surowa aproksymacja z _discover_segments - z progiem best_arr, żeby
        # zgadywanka nie twierdziła, że pobija udowodnione optimum, i niemalejąco,
        # bo jazda dalej nie może nagle zacząć wyglądać lepiej. exact=False:
        # mapa nie ma prawa pokazać takiej liczby jako godziny (punkt 10).
        for j, value in enumerate(suffix):
            if value == INF:
                floor = suffix[j - 1] if j else best_arr
                suffix[j] = max(floor, seg["exits"][j][1], best_arr)
                exact[j] = False
        seg["suffix"] = suffix
        seg["suffix_exact"] = exact

    # Rozpiętość skali jasności to NIE cała szerokość okna czasowego
    # (deadline - best_arr), tylko odległość do najgorszego kursu, który
    # faktycznie się w oknie znalazł. Przy szerokim oknie prawdziwe kursy
    # zwykle klastrują się blisko best_arr, więc dzielenie przez pełną
    # (dużo większą) szerokość okna spłaszczałoby różnice między nimi w
    # stronę samej góry skali - poszerzanie okna suwakiem NIE ma prawa
    # rozjaśniać istniejących już opcji, tylko dopuszczać kolejne, gorsze.
    # Pełny zakres jasności (od 1.0 do granicy widoczności) ma być
    # wykorzystany zawsze, niezależnie od tego, jak szerokie jest okno -
    # patrz punkt 9 kontraktu (FLOW_MAP_CONTRACT.md).
    # suffix jest niemalejący (patrz refresh_suffixes) - suffix[0] to NAJLEPSZA
    # wartość segmentu (to ona trafia w seg["bound"] parę linijek niżej), a
    # suffix[-1] to jego NAJGORSZA narysowana wartość. Rozpiętość ma sięgać
    # do najgorszej wartości gdziekolwiek na mapie, więc bierzemy suffix[-1],
    # nie suffix[0].
    worst_bound = max((seg["suffix"][-1] for seg in segs if seg["suffix"]), default=best_arr)
    worst_bound = min(worst_bound, deadline)   # zabezpieczenie na surowe aproksymacje ponad deadline
    span = max(worst_bound - best_arr, 1)

    def q_of(bound):
        return max(0.0, min(1.0, 1 - (bound - best_arr) / span))

    for seg in segs:
        seg["bound"] = seg["suffix"][0]   # = min(exit_vals), tak jak dawniej
        seg["q"] = q_of(seg["bound"])
        # Jasność W KAŻDYM PUNKCIE kursu, nie jedna na cały segment: suffix[j]
        # to najlepsza wartość osiągalna z pozycji wyjścia j LUB PÓŹNIEJ - czyli
        # to, co jeszcze jest osiągalne, gdy WCIĄŻ siedzimy w pojeździe na tej
        # wysokości. Mijamy realną, lepszą przesiadkę i z niej NIE korzystamy ->
        # jasność dalszego odcinka spada do tego, co faktycznie zostaje
        # osiągalne stąd. _finalize_segments tnie narysowany kurs na kawałki
        # dokładnie w takich miejscach (i tylko w takich - suffix jest
        # monotoniczny, więc żadnego bezsensownego migotania tam, gdzie nic
        # się nie zmieniło).
        seg["exit_q"] = [q_of(v) for v in seg["suffix"]]


def _target_profile(day, target_set, dep_sec, deadline):
    """Dla KAŻDEGO przystanku: o której najwcześniej jest się w celu, będąc tu
    o godzinie t. Profilowy CSA - jeden skan wstecz po tych samych połączeniach,
    które i tak są posortowane po odjeździe.

    To jest odpowiedź ODCZYTANA z rozkładu, nie oszacowana. Zastąpiła
    zgadywankę, która stąd wyrosła: dawne join_value brało gotową wartość
    kontynuacji i dodawało do niej `shift` - opóźnienie wsiadania względem
    kursu, dla którego tamtą wartość policzono - zakładając, że cały dalszy
    łańcuch przesunie się dokładnie o tyle samo. Rozkład jest sztywny, więc
    to założenie myli się w obie strony, a przede wszystkim NIE WIE, kiedy
    późniejszy kurs traci przesiadkę i realny przyjazd skacze o kwadrans.
    Na relacji LEŚNICA -> BARTOSZOWICE (16:44, 2026-08-29) 116 z 232 wyjść
    obiecywało w ten sposób przyjazd wcześniejszy, niż da się osiągnąć - do
    10 minut za wcześnie - a że wartość poniżej optimum i tak jest obcinana
    do q=1.0, połowa mapy świeciła pełnym blaskiem bez pokrycia.

    Zwraca (neg_deps, arrs): dla przystanku dwie równoległe listy - godziny
    odjazdu ze znakiem minus (rosnąco, więc bisect działa wprost) i przyjazdy
    do celu. Obie maleją wzdłuż listy: im wcześniej się tu stoi, tym więcej
    kursów zostaje do wyboru, więc przyjazd może tylko być wcześniejszy.
    Wpis dopisujemy TYLKO gdy poprawia - lista jest z definicji Pareto-
    optymalna i krótka.

    Koszt: 17,7 tys. połączeń w oknie, 23 ms (relacja jak wyżej).
    """
    conns = day.conns
    neg_deps, arrs = {}, {}
    board_value = {}       # (kurs, słupek) -> przyjazd do celu, wsiadając tu w ten kurs
    trip_arr = {}          # kurs -> przyjazd do celu, jadąc nim dalej stąd

    def value_at(stop, t):
        times = neg_deps.get(stop)
        if times is None:
            return INF
        # Wpisy z odjazdem >= t to prefiks listy, a przyjazdy wzdłuż niej
        # maleją - więc najlepszy z nich stoi na jego końcu.
        i = bisect_right(times, -t) - 1
        return arrs[stop][i] if i >= 0 else INF

    # Malejąco po odjeździe: zanim dojdziemy do połączenia, wszystko, na co da
    # się z niego przesiąść, jest już policzone (tak samo jak w _backward).
    for i in range(bisect_left(day.dep_times, deadline) - 1, -1, -1):
        dep_t, arr_t, dep_s, arr_s, trip = conns[i]
        if dep_t < dep_sec:
            break
        # Trzy sposoby dojechania do celu tym połączeniem: wysiąść w celu,
        # jechać dalej tym samym kursem, przesiąść się na przystanku dojazdu.
        best = arr_t if arr_s in target_set else INF
        stay = trip_arr.get(trip, INF)
        if stay < best:
            best = stay
        for stop2 in _sibling_places(day, arr_s):
            buffer = TRANSFER_SEC if stop2 == arr_s else WALK_SEC
            value = value_at(stop2, arr_t + buffer)
            if value < best:
                best = value
        if best == INF:
            continue
        trip_arr[trip] = best
        # To samo `best`, tylko zaadresowane inaczej: profil odpowiada "stoję
        # tu o tej godzinie", a to - "wsiadam TU w TEN kurs". Tablica odjazdów
        # pyta o to drugie, bo wiersz dotyczy konkretnego odjazdu, nie
        # najlepszego, jaki stąd jest (patrz _line_deadlines).
        board_value[(trip, dep_s)] = best
        known = arrs.get(dep_s)
        if known is None:
            neg_deps[dep_s], arrs[dep_s] = [-dep_t], [best]
        elif best < known[-1]:
            neg_deps[dep_s].append(-dep_t)
            known.append(best)
    return neg_deps, arrs, board_value


def _profile_value(day, profile, stop, arr_t):
    """Wysiadamy na `stop` o `arr_t` - o której najwcześniej jesteśmy w celu?
    INF, gdy wysiadanie tutaj do niczego nie prowadzi (bywa: gdy jedyną
    kontynuacją jest ten sam pojazd, na przesiadkę nie ma nawet bufora)."""
    neg_deps, arrs, _board = profile
    best = INF
    for stop2 in _sibling_places(day, stop):
        buffer = TRANSFER_SEC if stop2 == stop else WALK_SEC
        times = neg_deps.get(stop2)
        if times is None:
            continue
        i = bisect_right(times, -(arr_t + buffer)) - 1
        if i >= 0 and arrs[stop2][i] < best:
            best = arrs[stop2][i]
    return best


def _catchable(arr_t, buffer, dep_list):
    i = bisect_left(dep_list, arr_t + buffer)
    return i < len(dep_list) and dep_list[i] <= arr_t + WAIT_CAP_SEC


def _joins(day, arr_t, stop, other, drawn=None):
    """Czy z przyjazdu (arr_t, stop) da się wskoczyć w segment `other`
    (na tym samym słupku lub sąsiednim tego samego miejsca), opcjonalnie
    tylko w jego narysowanej części `drawn`."""
    for stop2 in _sibling_places(day, stop):
        times = other["dep_times"].get(stop2)
        if times is None or (drawn is not None and stop2 not in drawn):
            continue
        buffer = TRANSFER_SEC if stop2 == stop else WALK_SEC
        if _catchable(arr_t, buffer, times):
            return True
    return False


def _can_board(day, arr_t, stop, other, other_board):
    """Czy z przyjazdu (arr_t, stop) da się REALNIE wsiąść w KONKRETNY,
    już rozstrzygnięty kurs `other` (jego faktyczny, zapisany odjazd z
    other_board) - w przeciwieństwie do _joins, który sprawdza tylko czy
    JAKIŚ kurs wzorca `other` jest zdążalny (dep_times to suma odjazdów
    wszystkich kursów tego wzorca w oknie, nie tego jednego konkretnego).

    _joins odpowiada za pytanie mapy "czy ten segment ma dokąd prowadzić"
    (dobre dla jasności/kotwiczenia - niekoniecznie ten sam kurs). Budowa
    KONKRETNEJ propozycji trasy musi trzymać się jednego, already-wybranego
    kursu `other`, więc liczy się wyłącznie jego własny, zapisany odjazd -
    inaczej propozycja mogłaby "przesiąść się" z przyjazdu o 21:00 w kurs,
    który przy tym konkretnym odjeździe już dawno odjechał."""
    dep_t = other["best_deps"].get(other_board)
    if dep_t is None:
        return False
    if other_board == stop:
        return arr_t + TRANSFER_SEC <= dep_t
    if other_board in _sibling_places(day, stop):
        return arr_t + WALK_SEC <= dep_t
    return False


def _exit_index(day, kept, ranges):
    """Przystanek (+ siblingi) -> lista (segment, pozycja, arr_t, przystanek)
    dla wyjść leżących w NARYSOWANEJ (aktualnej) części segmentu. Współdzielone
    przez _select_and_anchor (kotwica końca) i _extract_transfer_graph."""
    exit_index = {}
    for other in kept:
        o_start, o_cut = ranges[id(other)]
        for pos, _, arr_t, stop in other["exits"]:
            if not (o_start < pos <= o_cut):
                continue         # wyjście poza narysowaną częścią
            for anchor in _sibling_places(day, stop):
                exit_index.setdefault(anchor, []).append((other, pos, arr_t, stop))
    return exit_index


def _leads_onward(day, other, stop, behind, drawn=None):
    """Czy `other` jest w tym miejscu prawdziwą KONTYNUACJĄ, czy tylko
    ZAWRACA po naszych własnych śladach.

    Sedno punktu 4 kontraktu. Sam fakt, że na końcu ogona stoi zdążalny,
    jasny kurs, NIE wystarcza, żeby ogon uznać za zakotwiczony: jeśli ten
    kurs jedzie z powrotem na przystanek, przez który już przejechaliśmy
    (klasycznie: pętla końcowa, na którą wjeżdża się tylko po to, żeby z
    niej zaraz wrócić), to ogon nadal wisi w powietrzu - wystaje z sieci i
    prowadzi donikąd, mimo że technicznie da się tam "przesiąść".

    `behind` to CAŁA przejechana dotąd droga tego kursu (wszystkie
    przystanki przed tym wyjściem, wraz z siblingami), nie tylko poprzedni
    przystanek. Wersja "tylko poprzedni" (2026-08-15, pierwsza) łapała samą
    czołową pętlę, ale przepuszczała każdą, która zawraca choć jeden
    przystanek dalej - a to jest w realnej siatce regułą, nie wyjątkiem:
    tramwaj 1 dojeżdżał do pętli Kamieńskiego, "kotwicząc się" o piętnastkę,
    która zaraz wraca przez Bałtycką i Kleczkowską, czyli dokładnie tam,
    skąd przyjechaliśmy - realna przesiadka jest cztery przystanki
    wcześniej, na Pl. Staszica, i dopiero tam ogon ma się kończyć.
    Zawrócenie na przystanek już minięty nigdy nie jest potrzebne, żeby
    coś pokazać: skoro tam byliśmy, to segment odjeżdżający STAMTĄD jest
    rysowany osobno i sam się kotwiczy - mapa nic nie traci, a przestaje
    prowadzić w ślepe zaułki.

    Liczone z FIZYCZNEJ kolejności przystanków kursu (z rozkładu, nie z
    zapytania) - ani z zegara, ani z aktualnie narysowanych zakresów. Dzięki
    temu odpowiedź "czy to zawrócenie" jest zawsze taka sama, niezależnie od
    szerokości okna, więc przesunięcie suwaka nie może przez tę regułę
    skasować niczego, co było widać wcześniej (punkt 9 kontraktu).

    To była pierwotna intencja dawnej "reguły postępu", tyle że ta mierzyła
    postęp przez `latest` ("jak późno mogę stąd wyjechać"), a ta wartość na
    węźle przesiadkowym jest wysoka z powodu gęstych kursów, nie bliskości
    celu - dlatego dawna reguła kasowała pół mapy razem z pętlami.
    """
    o_start, o_cut = drawn if drawn else (0, len(other["stops"]))
    for stop2 in _sibling_places(day, stop):
        position = other["pos_of"].get(stop2)
        if position is None or position >= o_cut - 1 or position < o_start:
            continue          # ten kurs się tu kończy - nie ma dokąd dalej
        if other["stops"][position + 1] not in behind:
            return True
    return False


def _select_and_anchor(day, segs, source_stops, target_set):
    """Krok 3: spójność narysowanej sieci (bez progu jasności - to, co jest
    w oknie czasowym, jest już wyznaczone przez deadline; q służy dalej
    tylko do intensywności rysowania). Segment jest przycinany z OBU stron
    do zakotwiczonych punktów:
    - początek: start relacji albo miejsce, gdzie dołącza (zdążalnie) inny
      narysowany segment - żaden segment nie zaczyna się "znikąd";
    - koniec: cel albo ostatnia przesiadka w porównywalnie jasny narysowany
      segment, który prowadzi DALEJ, a nie z powrotem tam, skąd właśnie
      przyjechaliśmy (patrz _leads_onward) - żaden ogon nie prowadzi
      "w powietrze" ani na pętlę, z której trzeba by tylko wracać.
    Punkt stały: zakresy mogą tylko się kurczyć, więc iteracja zbiega.
    Zwraca (kept, ranges) - listę segmentów i ich (start_pos, cut).
    """
    passing_index = _board_index(day, segs)

    kept = list(segs)
    ranges = {id(seg): (0, len(seg["stops"])) for seg in kept}
    while True:
        drawn_stops = {
            id(seg): set(seg["stops"][ranges[id(seg)][0]:ranges[id(seg)][1]])
            for seg in kept
        }
        exit_index = _exit_index(day, kept, ranges)
        survivors = []
        new_ranges = {}
        for seg in kept:
            # --- kotwica początku ---
            if seg["stops"][0] in source_stops:
                start_pos = 0
            else:
                start_pos = None
                for stop2, p in seg["pos_of"].items():
                    if p >= len(seg["stops"]) - 1:
                        continue         # dołączenie na samym końcu - puste
                    times = seg["dep_times"].get(stop2)
                    if times is None:
                        continue
                    if stop2 in source_stops:
                        # Wsiadamy tu wprost, bez żadnej przesiadki: to jeden
                        # z przystanków startowych relacji, a kurs ma stąd
                        # odjazd w oknie. Miejsce wsiadania wybrane w
                        # _discover_segments (stops[0]) to NAJWCZEŚNIEJSZE
                        # możliwe, a nie jedyne - kurs wyjeżdżający z pętli
                        # końcowej mija start dopiero w swoim środku (da się
                        # do niego wcześniej wsiąść, dojechawszy na tę pętlę
                        # czymś innym). Bez tej gałęzi taki kurs mógłby się
                        # zakotwiczyć wyłącznie o segment jadący NA pętlę, a
                        # ten słusznie ginie na kotwicy końca (_leads_onward:
                        # z pętli wraca się po własnych śladach) - i cała
                        # sieć, opierając się o niego, rozplątywała się do
                        # zera, po czym plan_flow wchodził w tryb awaryjny.
                        # Zmierzone 2026-08-27 na Sosnowiecka -> Wojszyce
                        # 15:37: 31 kandydatów, 0 zatrzymanych.
                        if start_pos is None or p < start_pos:
                            start_pos = p
                        continue
                    for other, _, arr_t, stop in exit_index.get(stop2, ()):
                        if other is seg:
                            continue
                        buffer = TRANSFER_SEC if stop2 == stop else WALK_SEC
                        if _catchable(arr_t, buffer, times):
                            if start_pos is None or p < start_pos:
                                start_pos = p
                if start_pos is None:
                    continue                 # nie da się tu dojechać widocznie
            # --- kotwica końca ---
            cut = 0
            behind = set()     # cała droga przejechana przed danym wyjściem
            ridden = 0         # dokąd `behind` jest już wypełnione
            for j, (pos, _, arr_t, stop) in enumerate(seg["exits"]):
                while ridden < pos:
                    behind.update(_sibling_places(day, seg["stops"][ridden]))
                    ridden += 1
                if pos <= start_pos + 1:
                    continue                 # wyjście przed/na starcie segmentu
                if stop in target_set:
                    cut = max(cut, pos)      # cel jest "widoczny" z definicji
                    continue
                for other in passing_index.get(stop, ()):
                    if other is seg or id(other) not in drawn_stops:
                        continue
                    if not _leads_onward(day, other, stop, behind,
                                         ranges[id(other)]):
                        continue
                    # Zostaje już tylko zdążalność. Był tu do 2026-08-15
                    # jeszcze wymóg PORÓWNYWALNEJ JASNOŚCI kontynuacji
                    # (`other["q"] + Q_ANCHOR_TOL >= seg["exit_q"][j]`) -
                    # świadoma decyzja porządkowa "nie ciągnij jasnego
                    # korytarza ogonem w bladą niszę", nigdy wymóg punktu 4.
                    # USUNIĘTY: jasność jest liczona względem najgorszej
                    # opcji, która AKURAT mieści się w oknie (punkt 9), więc
                    # obie strony tego porównania przeskalowują się przy
                    # ruchu suwaka - i potrafią się rozjechać w przeciwne
                    # strony. To była JEDYNA składowa kotwicy końca zależna
                    # od szerokości okna; przy ostrym wymogu kontynuacji
                    # (patrz _leads_onward) jej wahania rozchodziły się
                    # kaskadą przez cały łańcuch kotwic i kasowały odcinki
                    # przy samym poszerzeniu suwaka (zmierzone: 32 zniknięcia
                    # na ~1000 odcinków; bez tego warunku - zero, przy
                    # WIĘKSZEJ liczbie narysowanych kawałków). Nisza, o którą
                    # tu chodziło, i tak nie ma już jak powstać: kontynuacja
                    # musi prowadzić dalej i sama być narysowana dalej, więc
                    # jest częścią realnej drogi do celu, a nie ślepym
                    # zaułkiem - i rysuje się bladą barwą (punkty 3 i 8).
                    if _joins(day, arr_t, stop, other, drawn_stops[id(other)]):
                        cut = max(cut, pos)
                        break
            if cut >= start_pos + 2:
                survivors.append(seg)
                new_ranges[id(seg)] = (start_pos, cut)
        if len(survivors) == len(kept) and \
                new_ranges == {k: ranges[k] for k in new_ranges}:
            break
        kept = survivors
        ranges = new_ranges
    return kept, ranges


def _extract_transfer_graph(day, kept, ranges, source_stops, target_set):
    """Krok 5 (propozycje tras): zamienia narysowane, przycięte segmenty
    w mały graf przesiadkowy - węzły to segmenty, krawędzie to miejsca,
    gdzie da się realnie wskoczyć/wysiąść między nimi. To ten sam graf,
    który mapa już rysuje: żadna propozycja trasy nie może więc pokazać
    przesiadki, której mapa by nie narysowała.

    Uruchamiane RAZ, po zbiegnięciu _select_and_anchor - nie jest wplecione
    w jego punkt stały, żeby nie dotykać jego istniejącej wydajności/logiki.

    Zwraca słownik z:
    - origin_ids: id() segmentu -> pozycja wsiadania, dla segmentów, które
      mapa rysuje OD przystanku startowego relacji - punkty startowe
      przeszukiwania (patrz _enumerate_journeys). Pozycja wsiadania to
      zakotwiczony początek narysowanego kawałka (ranges), a nie stops[0]:
      kurs wyjeżdżający z pętli końcowej mija start dopiero w swoim środku
      i właśnie tam się do niego wsiada (patrz kotwica początku w
      _select_and_anchor),
    - exit_edges: dla każdego segmentu - lista jego wyjść w narysowanej
      części: albo dojazd do celu, albo przesiadka w inny segment (ten sam
      warunek porównywalnej jasności co _select_and_anchor przy kotwicy
      końca, żeby żadna podana przesiadka nie była jaśniejsza niż to, co
      widać na mapie),
    - seg_by_id: id() -> sam segment (wygodny odczyt).

    Przeszukiwanie idzie tylko w przód, wyłącznie po exit_edges: to jedyna
    z dwóch reguł kotwiczenia _select_and_anchor (początek/koniec), która
    sama sprawdza porównywalną jasność - reguła kotwicy początku jest
    celowo bardziej przepustowa (dowolna zdążalna przesiadka, żeby segment
    "nie wisiał w powietrzu"), więc nie nadaje się do wyznaczania KONKRETNYCH
    przesiadek w propozycji trasy.
    """
    passing_index = _board_index(day, kept)
    drawn_stops = {
        id(seg): set(seg["stops"][ranges[id(seg)][0]:ranges[id(seg)][1]])
        for seg in kept
    }

    origin_ids = {
        id(seg): ranges[id(seg)][0]
        for seg in kept
        if seg["stops"][ranges[id(seg)][0]] in source_stops
    }

    # Krawędź: ("target", pos, arr_t, stop, None, None, None) albo
    # ("transfer", pos, arr_t, stop, id(other), other_start, other_board) -
    # pos to wyjście TEGO segmentu (koniec etapu), other_start/other_board
    # to stały, już rozstrzygnięty (przez _select_and_anchor) punkt
    # wsiadania w segment `other`.
    exit_edges = {}
    for seg in kept:
        sid = id(seg)
        start_pos, cut = ranges[sid]
        edges = []
        for j, (pos, _, arr_t, stop) in enumerate(seg["exits"]):
            if not (start_pos < pos <= cut):
                continue                        # wyjście poza narysowaną częścią
            if stop in target_set:
                edges.append(("target", pos, arr_t, stop, None, None, None))
                continue
            for other in passing_index.get(stop, ()):
                if other is seg:
                    continue
                other_start, _ = ranges[id(other)]
                other_board = other["stops"][other_start]
                # _can_board (nie _joins!) - propozycja trasy musi trzymać
                # się REALNEGO odjazdu tego jednego, konkretnego kursu
                # `other`, nie samej "zdążalności wzorca w ogóle" (patrz
                # docstring _can_board) - inaczej powstaje "teleportacja":
                # przesiadka na kurs, który przy tym konkretnym przyjeździe
                # już odjechał.
                #
                # Filtr jasności (`other["q"] + Q_ANCHOR_TOL >= exit_q[j]`)
                # zniknął stąd 2026-08-15 razem z tym samym filtrem przy
                # kotwicy końca mapy (patrz _select_and_anchor) - te dwa
                # miejsca muszą mówić to samo, bo obietnica plan_flow działa
                # w obie strony: lista nie pokazuje przesiadki, której nie ma
                # na mapie, ale też mapa nie ma prawa mieć przesiadki, o
                # której lista przez niespójność milczy.
                if _can_board(day, arr_t, stop, other, other_board):
                    edges.append(
                        ("transfer", pos, arr_t, stop, id(other), other_start, other_board)
                    )
        exit_edges[sid] = edges

    return {
        "origin_ids": origin_ids,
        "exit_edges": exit_edges,
        "seg_by_id": {id(seg): seg for seg in kept},
    }


def _hop_key(a, b):
    return (a, b) if a <= b else (b, a)


def _build_hop_members(kept, ranges):
    """Dla każdego odcinka toru/ulicy (pary sąsiednich przystanków w
    narysowanej części kursu) - zbiór ETYKIET linii, które tamtędy jadą.
    Liczone z DOKŁADNYCH id przystanków (nie współrzędnych) - dwie linie
    zatrzymujące się na tych samych dwóch, kolejnych słupkach fizycznie
    dzielą tę samą ulicę/tory, więc to dokładne, nie przybliżone kryterium
    "co się tu nakłada" (patrz _finalize_segments, sekcja o rozdzielaniu
    wiązki)."""
    hop_members = {}
    for seg in kept:
        start_pos, cut = ranges[id(seg)]
        stops = seg["stops"]
        for k in range(start_pos, cut - 1):
            hop_members.setdefault(_hop_key(stops[k], stops[k + 1]), set()).add(seg["label"])
    return hop_members


def _membership_boundaries(hop_members, stops, start_pos, cut):
    """Pozycje (konwencja 'exits' - wyłączna górna granica kawałka), w
    których zestaw linii dzielących ten sam odcinek się zmienia - druga,
    niezależna od jasności, przyczyna cięcia kawałka na mapie (patrz
    _finalize_segments).

    Zwraca INDEKSY PRZYSTANKÓW (nie pozycje w konwencji "exits"): indeks k
    oznacza, że odcinek zaczynający się na stops[k] ma już inny zestaw linii
    niż ten, który się na stops[k] kończy. Kawałek rysowany jako całość nie
    ma prawa przez taki punkt przechodzić - inaczej cały dostałby skład
    korytarza policzony dla swojego PIERWSZEGO odcinka i twierdziłby "tędy
    jadą też X i Y" także tam, gdzie X i Y już dawno skręciły."""
    boundaries = set()
    prev_members = None
    for k in range(start_pos, cut - 1):
        members = frozenset(hop_members.get(_hop_key(stops[k], stops[k + 1]), ()))
        if prev_members is not None and members != prev_members:
            boundaries.add(k)
        prev_members = members
    return boundaries


def _line_sort_key(label):
    """JEDEN, globalny porządek linii - tramwaje przed autobusami, w obrębie
    rodzaju numerycznie. To nie jest kosmetyka: skład korytarza (patrz
    _corridor_lines) jest zawsze OBCIĘCIEM tego jednego porządku do linii
    obecnych na danym odcinku, więc grupka numerów rysowana na wspólnym
    korytarzu ma zawsze tę samą kolejność - i ta sama kolejność wychodzi w
    podpowiedzi pod kursorem (patrz app.js). Numer nie ma jak przeskoczyć w
    grupce z miejsca na miejsce między jednym odcinkiem a drugim."""
    num, mode = _line_parts(label)
    return (
        {"tram": 0, "bus": 1}.get(mode, 2),
        int(num) if num.isdigit() else 10 ** 6,
        num,
    )


def _corridor_lines(pieces, hop_members):
    """Dla każdego kawałka - PEŁNY skład korytarza, którym jedzie: wszystkie
    linie dzielące z nim te same, kolejne przystanki, RAZEM Z NIM SAMYM, w
    jednym, globalnym porządku (_line_sort_key). Kawałki jadące solo nie
    dostają nic.

    To jest cała odpowiedź backendu na kontrakt p.7 ("zawsze wiadomo, co tam
    jedzie"): mapa rysuje prawdziwą geometrię, więc linie wspólnego korytarza
    leżą jedna na drugiej i po samym kształcie nie da się ich rozróżnić.
    Rozróżnia je front - grupką numerów postawioną raz na całym korytarzu i
    przełączaniem między nimi pod kursorem (patrz app.js). Do jednego i do
    drugiego potrzebna jest właśnie ta lista.

    Liczona jest z ROZKŁADU (dokładne id przystanków, patrz
    _build_hop_members), nie z odległości na ekranie. Front próbował kiedyś
    zgadywać skład korytarza, mierząc piksele wokół kursora, i przy widoku
    całego miasta doliczał linie z sąsiednich ulic - stąd brały się plakietki
    "13 linii" tam, gdzie realnie jadą dwie.

    Skład jest stały na całej długości kawałka, bo kawałki są cięte dokładnie
    tam, gdzie się zmienia (patrz _membership_boundaries) - dlatego wystarczy
    odczytać go z pierwszego odcinka."""
    result = {}
    for label, stops_seq in pieces:
        members = hop_members.get(_hop_key(stops_seq[0], stops_seq[1]))
        if not members or len(members) < 2:
            continue
        result[(label, stops_seq)] = [
            {"num": _line_parts(other)[0], "kind": _line_parts(other)[1]}
            for other in sorted(members, key=_line_sort_key)
        ]
    return result



def _finalize_segments(day, kept, ranges, geo_db, earliest=None,
                       board_value=None, deadline=None, source_stops=None):
    """Krok 4: tnie każdy zatrzymany kurs na kawałki DOKŁADNIE tam, gdzie po
    drodze mijamy realną, lepszą kontynuację, z której nie korzystamy (patrz
    seg["exit_q"] w _refine_brightness) - jeden fizyczny kurs może więc
    wyjść na mapie jako kilka kolejnych kawałków o różnej jasności, nie
    jeden płaski odcinek od wsiadania do końca. RÓWNOLEGLE, tym samym
    cięciem, tnie też tam, gdzie zmienia się zestaw linii dzielących ten
    sam odcinek ulicy/torów (patrz _membership_boundaries) - to druga,
    niezależna przyczyna cięcia, potrzebna do rozdzielania nakładających
    się linii kawałek niżej. Kawałki, w których NIC z tego się nie zmienia,
    są sklejane, żeby nie mnożyć bez potrzeby liczby narysowanych
    fragmentów. Prosty kurs bez żadnej mijanej, lepszej przesiadki i bez
    współdzielonego odcinka dostaje - tak jak dawniej - jeden kawałek na
    całej narysowanej długości.

    Poza tym: agregacja po (linia, dokładny fragment) biorąc maksimum
    jakości (kilka kursów tego samego wzorca w oknie), tnie geometrię
    (patrz gtfs.shape_slice) i formatuje odpowiedź.

    Nakładające się linie (kontrakt p.7 - zawsze wiadomo, co tu jedzie):
    geometria zostaje prawdziwa, po torach i ulicach (kontrakt p.6), więc
    linie wspólnego korytarza leżą na mapie jedna na drugiej. Backend nie
    próbuje ich rozsuwać - podaje tylko SKŁAD korytarza (_corridor_lines,
    pole `corridor` w odpowiedzi), a rozróżnianie robi front: grupką numerów
    i przełączaniem pod kursorem.

    Na końcu jasność jest PRZESKALOWANA tak, żeby najgorszy kawałek, który
    FAKTYCZNIE trafia na mapę, lądował dokładnie na dole skali (w=0), nie
    gdzieś w środku - okno czasowe (deadline) rozstrzyga tylko, co się w
    ogóle pokazuje, ale sama skala jasności ma zawsze wykorzystywać cały
    zakres 0-1 tego, co zostało pokazane. Podział na kawałki (wyżej) i
    decyzje `_select_and_anchor` o tym, co przeżywa, liczą się na
    WCZEŚNIEJSZYCH, nieprzeskalowanych wartościach seg["q"]/exit_q -
    przeskalowanie na końcu jest czysto kosmetyczne (zmienia tylko liczby
    do rysowania), nie wpływa na to, co się pokazuje ani gdzie tnie się
    kawałki. Efekt: poszerzenie suwaka okna czasowego nie może rozjaśnić
    ani przyciemnić już pokazanej opcji, dopóki się ona nadal pokazuje -
    może tylko dopisać nowe, gorsze opcje pod spodem (patrz punkt 9
    kontraktu, `docs/FLOW_MAP_CONTRACT.md`).

    geo_db to połączenie współdzielone z resztą zapytania (patrz plan_flow) -
    jedno połączenie na wszystkie wycinki geometrii, także te do propozycji
    tras."""
    hop_members = _build_hop_members(kept, ranges)

    pieces = {}   # (linia, dokładny fragment) -> (q, shape_id)
    for seg in kept:
        start_pos, cut = ranges[id(seg)]
        boundary_stops = _membership_boundaries(hop_members, seg["stops"], start_pos, cut)
        piece_start = start_pos
        pending_end = None
        pending_q = None
        pending_reach = None      # o której jest się w celu, jadąc dalej stąd
        pending_ok = False        # ...i czy ta godzina jest odczytana, czy zgadnięta
        for (pos, _, _, _), exit_q, reach, reach_ok in zip(
                seg["exits"], seg["exit_q"], seg["suffix"], seg["suffix_exact"]):
            if pos <= start_pos + 1 or pos > cut:
                continue         # wyjście przed/na starcie narysowanej części - pomiń
            # Wydłużenie kawałka do `pos` obejmie odcinki o indeksach
            # piece_start .. pos-2. Jeśli któryś z nich zaczyna już inny
            # zestaw linii, kawałek przeszedłby przez zmianę składu korytarza
            # i podawałby ten sam skład na całej długości, także tam, gdzie
            # jest już inny (patrz _membership_boundaries).
            crosses = any(piece_start < k <= pos - 2 for k in boundary_stops)
            same = (pending_q is not None
                    and abs(exit_q - pending_q) < 5e-4
                    and not crosses)
            if same:
                pending_end = pos          # nic się nie zmieniło - wydłuż bieżący kawałek
                continue
            if pending_q is not None:
                _keep_piece(pieces, seg, piece_start, pending_end, pending_q,
                            pending_reach, pending_ok)
                # Kolejny kawałek zaczyna się DOKŁADNIE tam, gdzie poprzedni
                # się skończył (ten sam przystanek na styku - inaczej dwa
                # kawałki tego samego fizycznego kursu miałyby dziurę między
                # sobą na mapie). `pending_end` to `pos` (liczba przystanków,
                # wyłączna górna granica wycinka) - jego WŁASNY indeks w
                # `stops` to `pending_end - 1`.
                piece_start = pending_end - 1
            pending_end, pending_q = pos, exit_q
            pending_reach, pending_ok = reach, reach_ok
        if pending_q is not None:
            _keep_piece(pieces, seg, piece_start, pending_end, pending_q,
                        pending_reach, pending_ok)

    worst_q = min((entry[0] for entry in pieces.values()), default=1.0)
    span_q = 1.0 - worst_q

    def rescale(q):
        # Gdy wszystko pokazane jest już optymalne (span_q ~ 0 - np. jedyna
        # widoczna opcja to najlepsza trasa), nie ma względem czego się
        # skalować - wszystko dostaje pełną jasność zamiast dzielenia
        # (prawie) przez zero.
        if span_q < 1e-9:
            return 1.0
        return max(0.0, min(1.0, (q - worst_q) / span_q))

    corridors = _corridor_lines(pieces, hop_members)

    brightest = sorted(
        pieces.items(), key=lambda kv: kv[1][0], reverse=True,
    )
    seg_list = []
    for (label, stops_seq), (q, shape_id, times, reach, reach_ok, _headsign) in brightest:
        coords = [day.stop_coords[s] for s in stops_seq]
        path = gtfs.shape_slice(shape_id, coords, geo_db)
        num, mode = _line_parts(label)
        item = {
            "path": _round_path(path),
            "num": num,
            "kind": mode,
            "w": round(rescale(q), 3),
        }
        # Godziny - z rozkładu, nie z geometrii (patrz _piece_times):
        #   stops_t - [lat, lon, sekunda] dla każdego przystanku kawałka;
        #             front interpoluje z tego godzinę w punkcie pod kursorem
        #   arrive  - o której jest się W CELU, jadąc dalej stąd; tylko gdy
        #             ta liczba jest odczytana, nie zgadnięta
        if times is not None:
            item["stops_t"] = [
                [*point, when] for point, when in zip(_round_path(coords), times)
            ]
        if reach is not None and reach_ok:
            item["arrive"] = reach
        corridor = corridors.get((label, stops_seq))
        if corridor:
            # Kto tędy jedzie - CAŁY skład, razem z tą linią, z rozkładu, nie
            # z odległości na ekranie (patrz _corridor_lines). Kawałki jadące
            # solo (zdecydowana większość) nie dostają tego pola wcale, żeby
            # nie puchła odpowiedź.
            item["corridor"] = corridor
        seg_list.append(item)
    seg_list.sort(key=lambda s: s["w"])   # blade rysujemy pierwsze, jaskrawe na wierzchu
    return seg_list, _transfer_nodes(day, pieces, earliest, board_value, deadline,
                                    source_stops)


def _rides_back(earliest, board, alight):
    """Czy ten kawałek WIEZIE Z POWROTEM - mierzone rozkładem, nie geometrią.

    `earliest[stop]` (patrz _forward) to najwcześniejsza godzina, o której da
    się być na przystanku. Jeśli tam, dokąd ten kurs wiezie, można było być
    WCZEŚNIEJ niż tam, gdzie stoimy, to jedziemy w miejsce już za nami -
    przejazd nie daje postępu, tylko cofa. Węzeł nie ma prawa proponować
    takiego kursu (zgłoszone 2026-08-29: Kamiennogórska oferowała tramwaj 3
    na Leśnicę osobie, która właśnie z Leśnicy przyjechała).

    To NIE jest nowa reguła - punkt 4 kontraktu mówi ją od początku ("kurs
    zawracający na JAKIKOLWIEK przystanek, przez który już przejechaliśmy,
    jest drogą powrotną, nie kontynuacją"). Tyle że egzekwował ją tylko
    _leads_onward, dla RYSOWANEJ mapy - lista pod kropką jej nie sprawdzała.

    Miara luzu (`_backward`) się do tego nie nadaje: w szerokim oknie objazd
    o przystanek kosztuje minutę terminu, więc próg cofnięcia go nie łapie.
    Postęp łapie, bo pyta o coś innego - nie "czy zdążę", tylko "czy to
    w ogóle jest przede mną".

    Dotyczy WYŁĄCZNIE tego, co węzeł proponuje. Rysowanej mapy nie rusza:
    stabilność jasności względem szerokości okna zostaje nietknięta
    (patrz _discover_segments i punkt 9 kontraktu).

    Bez `earliest` (tryb awaryjny) nie odsiewamy nic.
    """
    if not earliest:
        return False
    here, there = earliest.get(board), earliest.get(alight)
    if here is None or there is None:
        return False
    return there <= here


def _line_deadlines(day, stops, board_value, from_sec, deadline):
    """Dla każdej linii odjeżdżającej z tego miejsca: OSTATNI odjazd, którym
    jeszcze da się dojechać do celu w oknie mapy.

    To jest mocna wersja reguły "tylko to, co jeszcze zdąży" (punkt 11
    kontraktu). Słaba, dotychczasowa, sprawdzała tylko, czy sam ODJAZD mieści
    się w oknie - warunek konieczny, nie wystarczający: autobus odjeżdżający
    minutę przed zamknięciem okna prawie na pewno do celu w nim nie dowiezie.
    Tutaj pytamy wprost: wsiadam W TEN kurs W TYM miejscu - o której jestem
    w celu? Odpowiedź jest odczytana z rozkładu (patrz _target_profile).

    JEDNA godzina na linię wystarczy, bo późniejszy kurs tej samej linii w tę
    samą stronę nie może dojechać wcześniej niż wcześniejszy - wartość jest
    względem godziny niemalejąca. Ostatni zdążający odjazd dzieli więc listę
    na dwie części i front ma do sprawdzenia jedną liczbę na wiersz.

    Linia bez ANI JEDNEGO zdążającego kursu w ogóle nie jest opcją i nie
    powinna się pojawić na liście.
    """
    out = {}
    for dep, trip, stop in gtfs.departures_between(day, stops, from_sec, deadline):
        value = board_value.get((trip, stop))
        if value is None or value > deadline:
            continue
        label, headsign = day.trip_info[trip]
        num, mode = _line_parts(label)
        key = (num, mode, headsign)
        if dep > out.get(key, -1):
            out[key] = dep
    return out


def _place_center(day, place_key, fallback_stop):
    """Środek wszystkich słupków jednego miejsca.

    Zwykła średnia współrzędnych, nie mediana ani środek prostokąta: miejsce
    to z definicji słupki w promieniu PLACE_MAX_SPAN_M (patrz
    gtfs._build_places), więc nie ma tu rozrzutu, który średnią mógłby
    przesunąć gdziekolwiek poza sam węzeł.
    """
    stops = day.stops_by_place.get(place_key) or [fallback_stop]
    coords = [day.stop_coords[s] for s in stops if s in day.stop_coords]
    if not coords:
        return _round_path([day.stop_coords[fallback_stop]])[0]
    return _round_path([(
        sum(lat for lat, _ in coords) / len(coords),
        sum(lon for _, lon in coords) / len(coords),
    )])[0]


def _transfer_nodes(day, pieces, earliest=None, board_value=None, deadline=None,
                    source_stops=None):
    """Węzły przesiadkowe mapy - to, na czym front stawia kropki z tablicą
    odjazdów.

    Po jednym na MIEJSCE, nie na słupek: plac z trzema peronami ma być jedną
    kropką mówiącą wszystko, a nie trzema, z których każda mówi co innego
    (patrz gtfs._build_places).

    `lines` to linie, o których węzeł ma coś do powiedzenia - z kierunkiem, bo
    ta sama linia mija węzeł w obie strony, a mapa mówi o jednej. Każda niesie
    `flow`, czyli CO SIĘ TU Z NIĄ DZIEJE - trzy rzeczy, nie jedna
    (patrz punkt 11 kontraktu):

      "start"   - mapa wiezie tą linią DALEJ stąd, ale nie wiezie nią DO tego
                  miejsca. Wsiadasz tu pierwszy raz - wcześniej nie było jak.
      "through" - mapa wiezie tą linią i DO tego miejsca, i DALEJ. Tym
                  pojazdem można już jechać, więc wsiadanie tutaj to jedna
                  z możliwości, a nie jedyna.
      "end"     - mapa wiezie tą linią DO tego miejsca i dalej nią nie wiezie.
                  Tu się z niej wysiada.

    GDZIE w ogóle stoi kropka: tam, gdzie coś się ZACZYNA albo KOŃCZY - jakaś
    linia staje się stąd dostępna, albo mapa przestaje którąś dalej wieźć.
    Przystanek, przez który wszystko tylko przejeżdża, kropki nie dostaje,
    choćby leżał na styku dwóch narysowanych kawałków.

    Czytane z KAŻDEGO przystanku kawałka, nie tylko z jego końców. Kawałek
    "Galeria Dominikańska -> Urząd Wojewódzki -> Katedra" przejeżdża przez
    urząd w połowie swojej długości: patrzenie na same końce mówiło, że tej
    linii tam nie ma, choć mapa rysuje ją przez ten przystanek i można w nią
    tam wsiąść (zgłoszone 2026-08-31). Ta sama wąska miara kasowała całe
    kropki - na Katedrze kończyły się kawałki 5 i N, a 10 i 111 tylko tamtędy
    przejeżdżały, więc węzeł orzekał "tylko się tu wysiada" i znikał, mimo że
    to jest dokładnie ta przesiadka, po którą się tam jedzie.

    Wcześniej węzeł niósł wyłącznie pierwsze dwa przypadki zlane w jedno
    ("w co da się tu wsiąść"), więc tablica milczała o tym, czym się tu w ogóle
    przyjechało - a to połowa odpowiedzi na "gdzie ja jestem i co dalej".

    `arrive` (tylko przy "end") to godzina, o której się tu tą linią jest -
    z rozkładu tego samego kursu, z którego narysowano kawałek. Wiersz "end"
    nie jest odjazdem, więc front nie ma go skąd wziąć z tablicy przystanku.

    `depart_by` (tylko przy "start"/"through") to ostatni odjazd, którym
    jeszcze się zdąży (patrz _line_deadlines).

    `sec` to najwcześniejsza godzina, o której można tu być - od niej liczy się
    "co stąd jeszcze odjedzie".

    Współrzędne idą DWIE, bo obie odpowiadają na to samo pytanie inaczej,
    a wybór między nimi to sprawa gustu (przełącznik w panelu ⚙):
    `lat`/`lon` to słupek, z którego wzięta jest godzina - kropka stoi wtedy
    na peronie; `clat`/`clon` to środek WSZYSTKICH słupków miejsca - kropka
    stoi wtedy pośrodku węzła, którego dotyczy, zamiast na losowo wybranym
    jego krańcu. Liczymy obie tutaj, więc przełącznik nic nie dopytuje.
    """
    # Które miejsce jest startem, wiadomo WPROST: `source_stops` to słupki,
    # z których rozwiązano zapytanie (patrz gtfs.match_stop), a węzły i tak
    # idą po kluczu miejsca. Front nie ma tego z czego odtwarzać - nazwa węzła
    # to nazwa jednego z jego słupków, więc zgadywanie po niej myliłoby się
    # dokładnie tam, gdzie plac ma słupki o różnych nazwach.
    start_places = {day.place_of.get(s, s) for s in (source_stops or ())}

    # Każde dotknięcie przystanku przez narysowany kawałek: (miejsce, linia,
    # słupek, godzina, czy wiezie DALEJ, czy dowozi TU, czy to koniec kawałka).
    touches = []
    for (label, stops_seq), (_q, _shape, times, _reach, _ok, headsign) in pieces.items():
        if times is None:
            continue                     # bez godzin nie ma o co pytać
        num, mode = _line_parts(label)
        line = (num, mode, headsign)
        last = len(stops_seq) - 1
        for i, stop in enumerate(stops_seq):
            touches.append((
                day.place_of.get(stop, stop), line, stop, times[i],
                # Wiezie dalej - chyba że dalej znaczy Z POWROTEM (patrz
                # _rides_back). Pytamy o KAWAŁEK JAKO CAŁOŚĆ, nie o drogę od
                # tego przystanku: `_rides_back` uznaje za cofnięcie także
                # RÓWNE godziny, a na przedostatnim przystanku przed celem
                # "najwcześniej tutaj" i "najwcześniej u celu" są zwykle
                # identyczne - pytany per przystanek orzekłby, że linia się tu
                # kończy, choć jedzie jeszcze przystanek do celu (Reja, 111).
                # Kawałek zawracający i tak nie ma prawa być narysowany
                # (punkt 4), więc miara na całości niczego nie przepuszcza.
                i < last and not _rides_back(earliest, stops_seq[0], stops_seq[-1]),
                i > 0,                   # dowozi tu - czyli można już nim jechać
                i == 0 or i == last,     # koniec kawałka - to on stawia kropkę
            ))

    nodes = {}
    for key, line, stop, when, onward, arriving, _is_end in touches:
        node = nodes.setdefault(
            key, {"sec": None, "stop": None, "boards": set(), "arrivals": {}})
        # Najwcześniej, kiedy mapa potrafi tu kogoś postawić - także pojazdem,
        # który tędy tylko przejeżdża: siedząc w nim, jest się tu o tej godzinie.
        if node["sec"] is None or when < node["sec"]:
            node["sec"], node["stop"] = when, stop
        if onward:
            node["boards"].add(line)
        # Kilka kawałków tej samej linii może tu dowozić (różne kursy w oknie).
        # Liczy się NAJWCZEŚNIEJSZY przyjazd - ta sama zasada, co przy `sec`.
        if arriving and when < node["arrivals"].get(line, INF):
            node["arrivals"][line] = when

    out = []
    for key, node in nodes.items():
        # Ostatni odjazd każdej linii, którym jeszcze się zdąży. Liczone dla
        # WSZYSTKICH słupków miejsca, bo dymek i tak scala je w jedną tablicę.
        limits = {}
        if node["boards"] and board_value is not None and deadline is not None:
            limits = _line_deadlines(
                day, day.stops_by_place.get(key, [node["stop"]]),
                board_value, node["sec"], deadline,
            )
        lines = []
        for line in sorted(node["boards"] | set(node["arrivals"])):
            num, kind, headsign = line
            depart_by = limits.get(line)
            # Linia, którą stąd już się nie dojedzie, przestaje być opcją do
            # wsiadania - ale jeśli mapa nią tu PRZYWOZI, wciąż jest czym
            # innym niż niczym: zostaje jako "end".
            boards = line in node["boards"] and not (limits and depart_by is None)
            arrive = node["arrivals"].get(line)
            if not boards and arrive is None:
                continue
            entry = {"num": num, "kind": kind, "headsign": headsign}
            if boards:
                entry["flow"] = "through" if arrive is not None else "start"
                if depart_by is not None:
                    entry["depart_by"] = depart_by
            else:
                entry["flow"] = "end"
                entry["arrive"] = arrive
            lines.append(entry)
        # Miejsce, w którym da się tylko wysiąść, nie jest przesiadką i nie
        # dostaje kropki.
        if not any(l["flow"] != "end" for l in lines):
            continue
        # ...a miejsce, przez które WSZYSTKO tylko przejeżdża, też nie: nic się
        # tu nie staje dostępne i nic nie przestaje, więc nie ma o czym
        # decydować - to przystanek, który się mija siedząc (punkt 11: "nie na
        # każdym mijanym przystanku").
        #
        # To jest właściwa miara "sensownego wysiadania" - i celowo NIE jest nią
        # koniec narysowanego kawałka. Kawałki tnie też zmiana składu korytarza
        # (punkt 7, patrz `crosses` w _refine_brightness), czyli sprawa czysto
        # rysunkowa: na Urzędzie Wojewódzkim (Impart) D i 146 schodzą się w
        # jeden korytarz, więc oba kawałki dostały tam szew przy IDENTYCZNEJ
        # jasności po obu stronach. Kropka dziedziczyła ten szew i stawała
        # w miejscu, w którym nie da się zrobić nic (zgłoszone 2026-08-31).
        # Linia przecięta z powodu korytarza wychodzi tu jako "through" -
        # i tu dowozi, i dalej wiezie - więc sama z siebie kropki nie stawia.
        if not any(l["flow"] != "through" for l in lines):
            continue
        lat, lon = _round_path([day.stop_coords[node["stop"]]])[0]
        clat, clon = _place_center(day, key, node["stop"])
        entry = {
            "name": day.stop_names[node["stop"]],
            "lat": lat,
            "lon": lon,
            "clat": clat,
            "clon": clon,
            "sec": node["sec"],
            "lines": lines,
        }
        if key in start_places:
            entry["start"] = True     # tylko przy tym jednym - pole ma nie puchnąć
        out.append(entry)
    return out


def _keep_piece(pieces, seg, start, end, q, reach, reach_ok):
    """Zapisuje kawałek pod kluczem (linia, dokładny fragment). Ten sam
    fragment tej samej linii może pochodzić z kilku kursów w oknie - liczy
    się najjaśniejszy, i to JEGO godziny jadą razem z nim: kawałek i podane
    przy nim czasy mają pochodzić z tego samego, jednego kursu.

    reach to przyjazd DO CELU, gdy jedzie się dalej stąd najlepszą znaną
    kontynuacją - dokładnie ta sama liczba, z której policzono jasność tego
    kawałka (patrz seg["exit_q"] w _refine_brightness). Kolor i godzina mówią
    więc jedno i to samo, tylko dwoma kanałami. reach_ok mówi, czy ta liczba
    jest ODCZYTANA z rozkładu, czy ZGADNIĘTA (brak widocznej kontynuacji) -
    zgadniętej mapa nie pokazuje."""
    key = (seg["label"], tuple(seg["stops"][start:end]))
    entry = pieces.get(key)
    if entry is None or q > entry[0]:
        pieces[key] = (q, seg["shape"], _piece_times(seg, start, end),
                       reach, reach_ok, seg["headsign"])


def _piece_times(seg, start, end):
    """Godziny przejazdu tego kursu przez KAŻDY przystanek kawałka, po kolei.

    Front ma z tego dwie rzeczy: czas samego kawałka (ostatnia minus pierwsza)
    i - to ważniejsze - godzinę w DOWOLNYM punkcie pod kursorem, przez
    interpolację między dwiema sąsiednimi godzinami (patrz punkt 10
    kontraktu). Dlatego godziny jadą per przystanek, a nie jako jedna para
    na cały kawałek: interpolować wolno tylko MIĘDZY dwoma sąsiednimi
    przystankami, nie przez pół trasy.

    None w środku listy jest niemożliwe do wykorzystania, więc gdy
    czegokolwiek brakuje (przystanek powtórzony w pętli potrafi nadpisać wpis
    w słowniku), oddajemy None i front nie pokazuje dla tego kawałka godziny
    wcale, zamiast pokazywać zmyśloną."""
    stops = seg["stops"][start:end]
    times = []
    for i, stop in enumerate(stops):
        # Pierwszy przystanek kawałka opisuje ODJAZD (stąd się rusza), każdy
        # następny PRZYJAZD (do niego się dojeżdża).
        when = seg["best_deps"].get(stop) if i == 0 else seg["arr_times"].get(stop)
        if when is None:
            return None
        times.append(when)
    for a, b in zip(times, times[1:]):
        if b < a:
            return None      # czasy się cofają - kurs odczytany niewiarygodnie
    return times


def _segment_ride_leg(day, seg, board_pos, alight_pos, geo_db):
    """Etap przejazdu wycięty z segmentu mapy przepływów - odpowiednik
    _reconstruct dla łańcucha znalezionego przez _enumerate_journeys.

    Segment ma już wszystko potrzebne (odjazdy/przyjazdy najlepszego kursu,
    kierunek, geometrię), więc w przeciwieństwie do _reconstruct nie trzeba
    odpytywać stop_times przez gtfs.trip_path.

    board_pos to indeks (jak w _select_and_anchor), alight_pos to pozycja
    w konwencji "exits" (już wliczająca przystanek wyjścia) - `stops`
    wycinamy więc jako [board_pos:alight_pos], bez +1.
    """
    stops = seg["stops"][board_pos:alight_pos]
    from_stop, to_stop = stops[0], stops[-1]
    dep_t = seg["best_deps"][from_stop]
    arr_t = seg["arr_times"][to_stop]
    path = gtfs.shape_slice(seg["shape"], [day.stop_coords[s] for s in stops], geo_db)
    num, mode = _line_parts(seg["label"])
    return {
        "kind": "ride",
        "line": seg["label"],
        "num": num,
        "mode": mode,
        "headsign": seg["headsign"],
        "from": day.stop_names[from_stop],
        "from_time": _fmt_time(dep_t),
        "to": day.stop_names[to_stop],
        "to_time": _fmt_time(arr_t),
        "dep_sec": dep_t,
        "arr_sec": arr_t,
        "minutes": round((arr_t - dep_t) / 60),
        "stops": [day.stop_names[s] for s in stops],
        "stops_count": len(stops) - 1,
        "path": _round_path(path),
    }


def _enumerate_journeys(day, graph, dep_sec, geo_db, limit=DEFAULT_JOURNEY_LIMIT,
                        gain_sec=TRANSFER_GAIN_SEC):
    """Lista konkretnych propozycji tras, czytana wprost z grafu przesiadek
    mapy przepływów (patrz _extract_transfer_graph) - żadnego osobnego
    przeszukiwania CSA. Propozycja to po prostu ścieżka przez ten sam graf,
    który mapa już narysowała: nic tu nie może pokazać przesiadki, której
    nie ma na mapie.

    Przeszukiwanie KOLEJKĄ (BFS po całym drzewie wariantów, najpierw
    najjaśniejsze gałęzie), nie rekurencją: dawniej jeden globalny licznik
    odwiedzin, sprawdzany na wejściu do KAŻDEGO wywołania, pozwalał JEDNEJ
    gałęzi (jednemu miejscu startowemu albo jednemu gęsto rozgałęzionemu
    węzłowi po drodze) zejść rekurencyjnie na pełną głębokość i wyczerpać
    cały budżet na warianty JEDNEGO korytarza (np. kilka linii o zbliżonej
    jasności z tego samego przystanku), zanim reszta origin_ids - albo inne
    rozgałęzienie tej samej trasy - w ogóle dostała szansę. FIFO gwarantuje
    przeciwnie: żadna gałąź nie zejdzie o poziom głębiej, dopóki WSZYSTKIE
    inne żywe gałęzie (inne miejsca startowe, inne rozgałęzienia po drodze)
    nie dostaną swojej kolejki na TYM SAMYM poziomie - jedna bogata okolica
    nie może więc zmonopolizować przeszukiwania kosztem korytarzy, które
    mapa przepływów i tak już narysowała gdzie indziej w grafie. Węzła NIE
    ograniczamy do paru najjaśniejszych krawędzi na raz (kuszące, ale przy
    ciasnym MAX_JOURNEY_CHAIN_LEGS zdarzają się węzły z kilkoma
    kontynuacjami REMISUJĄCYMI na tej samej, najlepszej jasności - obcięcie
    remisu byłoby arbitralne i mogłoby wyciąć jedyną krawędź, która akurat
    prowadzi dalej do celu w limicie etapów, gubiąc nawet najszybszą
    trasę). Duplikat (ten sam ciąg linii i te same miejsca wsiadania) jest
    odrzucany w momencie ukończenia łańcucha, nie dopiero po zebraniu
    wszystkich kandydatów - inaczej zajmowałby miejsce w limicie kosztem
    korytarza znalezionego później.

    Sufity kosztu skalowane do `limit` (patrz CANDIDATES_PER_JOURNEY/
    VISITS_PER_JOURNEY) - przy domyślnym limicie sufity to dokładnie
    MAX_JOURNEY_VISITS/MAX_JOURNEY_CANDIDATES; suwak "ile propozycji szukać"
    wyżej niż domyślne każe przeszukać graf głębiej, a nie tylko wypisać
    dłuższy fragment tych samych paru znalezionych łańcuchów. Bez tego duże
    miasto przy szerokim oknie czasowym mogłoby dać kombinatoryczną eksplozję
    wariantów.

    Może zwrócić listę bez najszybszej trasy w ogóle (patrz plan_flow) -
    _select_and_anchor czasem przycina najlepsze segmenty z zupełnie innych
    powodów niż próg jasności (np. reguła kotwicy), więc to funkcja wyżej
    (plan_flow) pilnuje, żeby najszybsza trasa zawsze była pokazana - tu
    liczy się tylko to, co faktycznie da się złożyć z narysowanego grafu.
    """
    origin_ids = graph["origin_ids"]
    exit_edges = graph["exit_edges"]
    seg_by_id = graph["seg_by_id"]

    candidate_cap = max(MAX_JOURNEY_CANDIDATES, limit * CANDIDATES_PER_JOURNEY)
    visit_cap = max(MAX_JOURNEY_VISITS, limit * VISITS_PER_JOURNEY)

    def edge_priority(edge):
        kind, _, _, _, other_id, _, _ = edge
        if kind == "target":
            return (0, 0.0)
        return (1, -seg_by_id[other_id]["q"])

    queue = deque(
        ([], sid, origin_ids[sid], {sid})
        for sid in sorted(origin_ids, key=lambda i: -seg_by_id[i]["q"])
    )
    candidates = []   # łańcuchy: [(seg, board_pos, alight_pos), ...]
    seen = set()
    visits = 0
    while queue and visits < visit_cap and len(candidates) < candidate_cap:
        chain, sid, board_pos, visited = queue.popleft()
        visits += 1
        seg = seg_by_id[sid]
        edges = sorted(exit_edges.get(sid, ()), key=edge_priority)
        for edge in edges:
            kind, alight_pos, _, _, other_id, other_start, _ = edge
            new_chain = chain + [(seg, board_pos, alight_pos)]
            if kind == "target":
                signature = tuple(
                    (s["label"], day.stop_names[s["stops"][bp]])
                    for s, bp, _ in new_chain
                )
                if signature in seen:
                    continue
                seen.add(signature)
                candidates.append(new_chain)
                if len(candidates) >= candidate_cap:
                    break
            elif other_id not in visited and len(new_chain) < MAX_JOURNEY_CHAIN_LEGS:
                queue.append((new_chain, other_id, other_start, visited | {other_id}))

    ranked = []
    for chain in candidates:
        first_dep = chain[0][0]["best_deps"][chain[0][0]["stops"][chain[0][1]]]
        last_seg, _, last_alight = chain[-1]
        arrival = last_seg["arr_times"][last_seg["stops"][last_alight - 1]]
        # Przesiadka kosztuje gain_sec: propozycja z przesiadką musi tyle
        # oszczędzić, żeby wyprzedzić jazdę bez niej (patrz TRANSFER_GAIN_SEC).
        # Sortujemy po koszcie z karą, ale pokazujemy prawdziwy przyjazd.
        # Liczba przesiadek zostaje rozstrzygnięciem remisu, więc przy progu 0
        # klucz jest dokładnie taki jak przed wprowadzeniem kary.
        przesiadki = len(chain) - 1
        ranked.append((arrival + przesiadki * gain_sec,
                       przesiadki, -first_dep, chain, arrival))
    ranked.sort(key=lambda item: item[:3])

    journeys = []
    for _cost, _przesiadki, neg_dep, chain, arrival in ranked[:limit]:
        legs = []
        for i, (seg, board_pos, alight_pos) in enumerate(chain):
            if i > 0:
                prev_seg, _, prev_alight = chain[i - 1]
                prev_stop = prev_seg["stops"][prev_alight - 1]
                this_board_stop = seg["stops"][board_pos]
                if prev_stop != this_board_stop:
                    legs.append(_walk_leg(day, prev_stop, this_board_stop))
            legs.append(_segment_ride_leg(day, seg, board_pos, alight_pos, geo_db))
        rides = [leg for leg in legs if leg["kind"] == "ride"]
        journeys.append(_summarize_journey(legs, rides, arrival, dep_sec))

    return journeys


def _forward(day, source_stops, dep_sec, deadline):
    """Jak _scan, ale bez celu: najwcześniejsze przyjazdy wszędzie do deadline.

    Zwraca (earliest, arrived_by, trip_board); trip_board[kurs] to indeks
    pierwszego połączenia, na które w ogóle da się zdążyć (właściwe miejsce
    wsiadania, z regułą postępu, wybiera dopiero plan_flow).
    """
    conns = day.conns
    earliest = {}
    arrived_by = {}     # 'origin' | 'ride' | 'walk' - do bufora przesiadki
    trip_board = {}

    for stop in source_stops:
        earliest[stop] = dep_sec
        arrived_by[stop] = "origin"

    for i in range(bisect_left(day.dep_times, dep_sec), len(conns)):
        dep_t, arr_t, dep_s, arr_s, trip = conns[i]
        if dep_t > deadline:
            break
        if trip not in trip_board:
            reached = earliest.get(dep_s)
            if reached is None:
                continue
            buffer = TRANSFER_SEC if arrived_by[dep_s] == "ride" else 0
            if reached + buffer > dep_t:
                continue
            trip_board[trip] = i
        if arr_t < earliest.get(arr_s, INF):
            earliest[arr_s] = arr_t
            arrived_by[arr_s] = "ride"
            for sibling in day.siblings.get(arr_s, ()):
                walk_arr = arr_t + WALK_SEC
                if walk_arr < earliest.get(sibling, INF):
                    earliest[sibling] = walk_arr
                    arrived_by[sibling] = "walk"
    return earliest, arrived_by, trip_board


def _backward(day, target_set, dep_sec, deadline):
    """Skan wstecz: najpóźniejszy moment na każdym przystanku, z którego
    da się jeszcze dotrzeć do celu przed deadline.

    Połączenia przetwarzamy malejąco po odjeździe - wszystko, co wpływa na
    latest[przystanek] po czasie t, jest już policzone, zanim do t dojdziemy.
    """
    conns = day.conns
    latest = {stop: deadline for stop in target_set}
    trip_ok = set()

    for i in range(bisect_left(day.dep_times, deadline) - 1, -1, -1):
        dep_t, arr_t, dep_s, arr_s, trip = conns[i]
        if dep_t < dep_sec:
            break
        if trip not in trip_ok:
            leave_by = latest.get(arr_s)
            if leave_by is None:
                continue
            # Na przystanku końcowym nie ma przesiadki, więc bez bufora.
            buffer = 0 if arr_s in target_set else TRANSFER_SEC
            if arr_t + buffer > leave_by:
                continue
            trip_ok.add(trip)
        if dep_t > latest.get(dep_s, -1):
            latest[dep_s] = dep_t
            for sibling in day.siblings.get(dep_s, ()):
                walk_dep = dep_t - WALK_SEC
                if walk_dep > latest.get(sibling, -1):
                    latest[sibling] = walk_dep
    return latest


def _unknown_stop(query, hints):
    result = {"error": f"Nie znam przystanku „{query.strip()}”."}
    if hints:
        result["suggestions"] = hints
    return result


def _fmt_time(sec):
    hours = sec // 3600
    if hours >= 24:                    # kursy po północy zapisane jako 24:xx, 25:xx
        hours -= 24
    return f"{hours:02d}:{(sec % 3600) // 60:02d}"
