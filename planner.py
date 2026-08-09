"""Wyszukiwanie najszybszego połączenia algorytmem CSA (Connection Scan).

CSA nie buduje grafu: wszystkie połączenia dnia (przejazdy między sąsiednimi
przystankami) są posortowane po czasie odjazdu i skanowane raz, liniowo.
Połączenie jest "osiągalne", jeśli jesteśmy już w tym kursie albo zdążymy
na jego odjazd na przystanku startowym.
"""

from bisect import bisect_left, bisect_right
from datetime import datetime

import gtfs

TRANSFER_SEC = 120   # bufor bezpieczeństwa przy przesiadce na tym samym słupku
WALK_SEC = 180       # przejście między słupkami tego samego miejsca (patrz gtfs.py)
INF = float("inf")


def plan_route(start_query, end_query, when=None):
    """Zwraca dict z trasą ('legs', czasy) albo z kluczem 'error'."""
    when = when or datetime.now()

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

    legs = _reconstruct(day, journey, best_stop)
    first_dep = legs[0]["dep_sec"]
    return {
        "start": start_name,
        "end": end_name,
        "departure": _fmt_time(first_dep),
        "arrival": _fmt_time(best_arr),
        "travel_time": f"{round((best_arr - first_dep) / 60)} min",
        "legs": legs,
    }


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

    # Użytkownik podaje nazwę przystanku, więc startuje ze wszystkich jego słupków.
    for stop in source_stops:
        earliest[stop] = dep_sec
        journey[stop] = ("origin",)

    targets = set(target_stops)
    best_arr = INF
    best_stop = None
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

        if arr_t < earliest.get(arr_s, INF):
            earliest[arr_s] = arr_t
            journey[arr_s] = ("ride", trip_board[trip], i)
            if arr_s in targets and arr_t < best_arr:
                best_arr = arr_t
                best_stop = arr_s
            # Relaksacja pieszo na pozostałe słupki tego samego miejsca.
            for sibling in day.siblings.get(arr_s, ()):
                walk_arr = arr_t + WALK_SEC
                if walk_arr < earliest.get(sibling, INF):
                    earliest[sibling] = walk_arr
                    journey[sibling] = ("walk", arr_s)

    return best_stop, best_arr, journey


def _reconstruct(day, journey, last_stop, geo_db=None):
    """Odtwarza trasę od celu do startu i skleja ją w czytelne etapy.

    Z otwartym `geo_db` ścieżka etapu jest wycinkiem geometrii kursu (realne
    ulice i tory, tak jak na mapie przepływów); bez niego - łamaną po
    przystankach.
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
            trip = board[4]
            line, headsign = day.trip_info[trip]
            exit_arr = day.conns[exit_i][1]
            # Pełna lista przystanków etapu - do narysowania linii na mapie.
            path_rows = gtfs.trip_path(
                trip, board[2], board[0], stop, exit_arr, geo_db
            )
            coords = [day.stop_coords[s] for s, _, _ in path_rows]
            if geo_db is not None and len(coords) >= 2:
                coords = gtfs.shape_slice(day.trip_shape.get(trip), coords, geo_db)
            num, mode = _line_parts(line)
            legs.append({
                "kind": "ride",
                "line": line,
                "num": num,
                "mode": mode,
                "headsign": headsign,
                "from": day.stop_names[board[2]],
                "from_time": _fmt_time(board[0]),
                "to": day.stop_names[stop],
                "to_time": _fmt_time(exit_arr),
                "dep_sec": board[0],
                "minutes": round((exit_arr - board[0]) / 60),
                "stops": [day.stop_names[s] for s, _, _ in path_rows],
                "stops_count": max(len(path_rows) - 1, 1),
                "path": _round_path(coords),
            })
            stop = board[2]
    legs.reverse()
    return legs


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


MODE_OF_LABEL = {"Tramwaj": "tram", "Autobus": "bus"}


def _line_parts(label):
    """'Tramwaj 17' -> ('17', 'tram') - numer na plakietkę i rodzaj do koloru."""
    kind, _, num = label.partition(" ")
    return (num or label), MODE_OF_LABEL.get(kind, "other")


def _round_path(coords):
    return [[round(lat, 5), round(lon, 5)] for lat, lon in coords]


# Jeden suwak okna czasowego: "pokaż trasy do N minut wolniejsze niż
# najszybsza". Dawniej to były cztery nakładające się na siebie suwaki
# (próg jasności + mnożnik + widełki min/maks zapasu) - matematycznie ich
# efekt zawsze sprowadzał się do jednej liczby (okno × (1 − próg jasności)),
# więc zostaje jedna, wprost w minutach - bez utraty żadnej realnej
# możliwości ustawienia.
DEFAULT_EXTRA_SEC = 1800  # domyślnie 30 min dłużej niż najszybsza trasa
MIN_EXTRA_SEC = 0
MAX_EXTRA_SEC = 3600      # (suwak w UI go nadpisuje) - sufit rozsądku, 60 min

Q_ANCHOR_TOL = 0.10     # ogon rysujemy tylko do przesiadki w kontynuację
                        # niewiele ciemniejszą od segmentu (tolerancja jasności)
BACKTRACK_TOL_SEC = 120 # wsiadanie nie może wymagać oddalenia się od celu
                        # (cofnięcia) o więcej niż 2 min
PROGRESS_TOL_SEC = 0    # domyślny luz reguły postępu (suwak w UI go nadpisuje) -
                        # metryka latest bywa zaszumiona o 1-2 min między
                        # sąsiednimi węzłami, nawet na dobrej trasie
MIN_PROGRESS_TOL_SEC = 0
MAX_PROGRESS_TOL_SEC = 600
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


def _deadline(best_arr, extra_sec=None):
    """Granica sensowności: najlepszy przyjazd + extra_sec (suwak w UI, patrz
    DEFAULT_EXTRA_SEC/MIN_EXTRA_SEC/MAX_EXTRA_SEC powyżej)."""
    extra_sec = (
        DEFAULT_EXTRA_SEC if extra_sec is None
        else int(max(MIN_EXTRA_SEC, min(MAX_EXTRA_SEC, extra_sec)))
    )
    return best_arr + extra_sec


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


def plan_flow(start_query, end_query, when=None, progress_tol_sec=None,
              start_point=None, end_point=None, range_m=None, extra_sec=None,
              journey_limit=None):
    """Mapa przepływów ("mrówki"): wszystkie użyteczne przejazdy start -> cel.

    Jednostką jest KURS, nie pojedynczy przeskok: dla każdego kursu, do którego
    realnie da się wsiąść (skan w przód), rysujemy jeden ciągły segment od
    przystanku wsiadania do celu albo do ostatniego wyjścia z WIDOCZNĄ
    kontynuacją (przesiadką na segment, który też jest narysowany). Jasność
    propaguje się wstecz przez przesiadki: dowóz nigdy nie jest ciemniejszy
    niż to, do czego dowozi - narysowana sieć jest spójna od startu do celu.

    Liczone w krokach (patrz odpowiednie funkcje): odkrycie segmentów
    kandydujących (_discover_segments), dopracowanie ich jasności przez
    konkretne kontynuacje (_refine_brightness), próg + spójność narysowanej
    sieci (_select_and_anchor), złożenie odpowiedzi z geometrią
    (_finalize_segments).

    Lista propozycji tras ("journeys") to NIE osobny algorytm - to ścieżki
    przeczytane wprost z tego samego, już narysowanego grafu segmentów
    (_extract_transfer_graph + _enumerate_journeys), więc lista nigdy nie
    pokaże przesiadki, której nie ma na mapie, i reaguje na te same suwaki
    (progress_tol_sec, extra_sec) co mapa.

    extra_sec to jedyny suwak okna czasowego: "pokaż trasy do tylu sekund
    wolniejsze niż najszybsza" (patrz _deadline) - zastępuje dawny próg
    jasności + trzy suwaki wydłużenia, których łączny efekt zawsze
    sprowadzał się do jednej liczby. Nie ma już osobnego progu jasności -
    wszystko w oknie czasowym jest pokazywane, jasność (q) służy już tylko
    do intensywności rysowania.
    progress_tol_sec to luz reguły postępu (patrz _discover_segments);
    None = domyślne PROGRESS_TOL_SEC.
    journey_limit to ile propozycji tras SZUKAĆ (suwak w UI, patrz
    DEFAULT_JOURNEY_LIMIT/MIN_JOURNEY_LIMIT/MAX_JOURNEY_LIMIT) - wyższa
    wartość nie zmyśla nieistniejących wariantów, tylko każe
    _enumerate_journeys przeszukać graf głębiej (patrz CANDIDATES_PER_JOURNEY/
    VISITS_PER_JOURNEY); gdy w grafie jest ich mniej, dostaje się tyle, ile
    faktycznie da się złożyć.
    """
    when = when or datetime.now()
    progress_tol_sec = (
        PROGRESS_TOL_SEC if progress_tol_sec is None
        else max(MIN_PROGRESS_TOL_SEC, min(MAX_PROGRESS_TOL_SEC, progress_tol_sec))
    )
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

    # Najszybsza trasa wyznacza skalę ("większość mrówek") i jest zapasowym
    # planem, gdyby kotwiczenie (patrz niżej) przycięło wszystko do zera.
    best_stop, best_arr, best_journey = _scan(day, source_stops, target_stops, dep_sec)
    if best_stop is None:
        return _no_connection(start_name, end_name, dep_sec)
    deadline = _deadline(best_arr, extra_sec)

    earliest, arrived_by, trip_board = _forward(day, source_stops, dep_sec, deadline)
    latest = _backward(day, target_stops, dep_sec, deadline)

    # Punkt odniesienia reguły cofnięcia: im później można być na przystanku
    # i wciąż zdążyć (latest), tym bliżej celu się jest.
    origin_latest = max(
        (latest[s] for s in source_stops if s in latest), default=None,
    )
    target_set = target_stops

    segs = _discover_segments(
        day, dep_sec, deadline, earliest, arrived_by, trip_board,
        latest, origin_latest, target_set, progress_tol_sec,
    )
    _refine_brightness(day, segs, target_set, deadline, best_arr)
    kept, ranges = _select_and_anchor(day, segs, source_stops, target_set)

    gtfs.geo_generation()      # jeden stat na zapytanie; czyści cache po podmianie bazy
    geo_db = gtfs.open_db()    # jedno połączenie na WSZYSTKIE wycinki geometrii zapytania
    try:
        if kept:
            seg_list = _finalize_segments(day, kept, ranges, geo_db)
            graph = _extract_transfer_graph(day, kept, ranges, source_stops, target_set)
            journeys = _enumerate_journeys(day, graph, dep_sec, geo_db, limit=journey_limit)
        else:
            # Zabezpieczenie: _scan już udowodnił, że połączenie istnieje
            # (best_stop nie jest None), więc jeśli kotwiczenie i tak
            # przycięło WSZYSTKO do zera (skrajny, rzadki przypadek - nie
            # mylić z brakiem pojedynczego segmentu, na to jest reguła
            # cofnięcia w _discover_segments, patrz komentarz przy
            # arrived_by[dep_s] != "origin"), narysuj i wylistuj
            # przynajmniej samą najszybszą trasę zamiast pustej odpowiedzi.
            fallback_legs = _reconstruct(day, best_journey, best_stop, geo_db)
            fallback_rides = [leg for leg in fallback_legs if leg["kind"] == "ride"]
            seg_list = []
            for leg in fallback_rides:
                num, mode = _line_parts(leg["line"])
                seg_list.append({"path": leg["path"], "num": num, "kind": mode, "w": 1.0})
            journeys = (
                [_summarize_journey(fallback_legs, fallback_rides, best_arr, dep_sec)]
                if fallback_rides else []
            )
    finally:
        geo_db.close()

    return {
        "start": start_name,
        "end": end_name,
        "departure": _fmt_time(dep_sec),
        "best_arrival": _fmt_time(best_arr),
        "deadline": _fmt_time(deadline),
        "segments": seg_list,
        "journeys": journeys,
    }


def _discover_segments(day, dep_sec, deadline, earliest, arrived_by, trip_board,
                        latest, origin_latest, target_set, progress_tol_sec):
    """Krok 1: dla każdego kursu w oknie wybiera miejsce wsiadania (reguła
    cofnięcia, patrz BACKTRACK_TOL_SEC) i idzie nim naprzód zbierając
    wyjścia - zwraca listę segmentów kandydujących, jeszcze bez dopracowanej
    jasności (patrz _refine_brightness).

    Reguła postępu porównuje każdy przystanek do NAJLEPSZEGO `latest`
    osiągniętego na tym kursie DO TEJ PORY (`best_latest_seen`), nie do
    wartości zanotowanej raz przy wsiadaniu - bo ta druga wersja przepuszcza
    powolny, ale realny odpływ: kilka przystanków z rzędu, każdy trochę
    gorszy od poprzedniego, nigdy nie spadnie poniżej PIERWSZEGO pomiaru,
    jeśli akurat od niego zaczyna się spadek.
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
        best_latest_seen = None
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
                best_latest_seen = stop_latest
            elif dep_s != stops_seq[-1]:
                break                        # przerwany łańcuch - utnij
            departures.append((dep_s, dep_t))
            arrivals.append((arr_s, arr_t))
            stops_seq.append(arr_s)
            leave_by = latest.get(arr_s)
            prior_best = best_latest_seen   # najlepszy punkt PRZED tym przystankiem
            if leave_by is not None and (
                    best_latest_seen is None or leave_by > best_latest_seen):
                best_latest_seen = leave_by
            if leave_by is None or arr_t > leave_by:
                continue
            # Wyjście liczy się tylko, gdy jazda PRZYBLIŻYŁA do celu - do
            # NAJLEPSZEGO punktu tego kursu SPRZED tego przystanku, nie
            # tylko do startu (inaczej kurs "w drugą stronę" świeciłby pełną
            # jasnością). Porównanie do prior_best (a nie do już
            # zaktualizowanego best_latest_seen) jest celowe: świeży
            # rekord nie może sam siebie zdyskwalifikować.
            if (prior_best is not None
                    and leave_by <= prior_best - progress_tol_sec):
                continue
            # bound: najwcześniejszy możliwy przyjazd do celu, jeśli
            # wysiądziemy tutaj ((deadline - leave_by) = czas stąd do celu).
            exits.append((len(stops_seq), arr_t + (deadline - leave_by), arr_t, arr_s))
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


def _refine_brightness(day, segs, target_set, deadline, best_arr):
    """Krok 2: aproksymacja (deadline - latest) wlicza dla rzadkich linii
    czekanie "do ostatniego kursu" i przekłamuje jasność. Liczymy więc
    wartość każdego WYJŚCIA przez konkretne kontynuacje: najbliższy odjazd
    segmentu, w który da się wskoczyć, plus najlepsze z jego DALSZYCH wyjść
    (sufiks - wyjść sprzed punktu wskoczenia nie da się już użyć). Wyjścia
    na cel są dokładne (wartość = przyjazd). Ustawia seg['bound']/seg['q'].

    q=1.0 musi wypaść dokładnie dla trasy najszybszej (bound == best_arr) -
    stąd odniesienie do best_arr, NIE do deadline: przy oknie zerowym
    (extra_sec=0, deadline == best_arr) odległość "deadline - bound" dla
    jedynej ocalałej, optymalnej trasy też wynosi 0, więc licząc względem
    deadline wyszłoby q=0 (najciemniej) właśnie dla trasy, która powinna
    świecić najjaśniej - mapa wtedy rysowała wszystko jako ledwie widoczne
    duchy, łącznie z jedyną prawdziwą propozycją.
    """
    for seg in segs:
        for times in seg["dep_times"].values():
            times.sort()

    passing_index = _board_index(day, segs)

    for seg in segs:
        seg["exit_vals"] = [e[1] for e in seg["exits"]]

    def refresh_suffixes():
        for seg in segs:
            suffix = list(seg["exit_vals"])
            for j in range(len(suffix) - 2, -1, -1):
                suffix[j] = min(suffix[j], suffix[j + 1])
            seg["suffix"] = suffix
            seg["exit_pos"] = [e[0] for e in seg["exits"]]

    def join_value(arr_t, stop, other):
        """Przyjazd do celu, gdy z (arr_t, stop) wskakujemy w `other`
        i korzystamy z jego wyjść ZA punktem wskoczenia."""
        best = None
        for stop2 in _sibling_places(day, stop):
            times = other["dep_times"].get(stop2)
            position = other["pos_of"].get(stop2)
            if times is None or position is None:
                continue
            buffer = TRANSFER_SEC if stop2 == stop else WALK_SEC
            i = bisect_left(times, arr_t + buffer)
            if i == len(times):
                continue
            j = bisect_right(other["exit_pos"], position)
            if j == len(other["suffix"]):
                continue          # za punktem wskoczenia nie ma już wyjść
            shift = max(0, times[i] - other["best_deps"].get(stop2, times[i]))
            candidate = other["suffix"][j] + shift
            if best is None or candidate < best:
                best = candidate
        return best

    for _ in range(8):        # punkt stały; zbiega w 2-4 obiegach
        refresh_suffixes()
        changed = False
        for seg in segs:
            for j, (pos, raw_bound, arr_t, stop) in enumerate(seg["exits"]):
                if stop in target_set:
                    continue          # wartość = przyjazd, już dokładna
                best = None
                for other in passing_index.get(stop, ()):
                    if other is seg:
                        continue
                    value = join_value(arr_t, stop, other)
                    if value is not None and (best is None or value < best):
                        best = value
                # bez widocznej kontynuacji zostaje surowa aproksymacja
                new_value = raw_bound if best is None else best
                if new_value != seg["exit_vals"][j]:
                    seg["exit_vals"][j] = new_value
                    changed = True
        if not changed:
            break

    span = max(deadline - best_arr, 1)
    for seg in segs:
        seg["bound"] = min(seg["exit_vals"])
        seg["q"] = max(0.0, min(1.0, 1 - (seg["bound"] - best_arr) / span))


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


def _select_and_anchor(day, segs, source_stops, target_set):
    """Krok 3: spójność narysowanej sieci (bez progu jasności - to, co jest
    w oknie czasowym, jest już wyznaczone przez deadline; q służy dalej
    tylko do intensywności rysowania). Segment jest przycinany z OBU stron
    do zakotwiczonych punktów:
    - początek: start relacji albo miejsce, gdzie dołącza (zdążalnie) inny
      narysowany segment - żaden segment nie zaczyna się "znikąd";
    - koniec: cel albo ostatnia przesiadka w porównywalnie jasny narysowany
      segment - żaden ogon nie prowadzi "w powietrze".
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
            for pos, _, arr_t, stop in seg["exits"]:
                if pos <= start_pos + 1:
                    continue                 # wyjście przed/na starcie segmentu
                if stop in target_set:
                    cut = max(cut, pos)      # cel jest "widoczny" z definicji
                    continue
                for other in passing_index.get(stop, ()):
                    if other is seg or id(other) not in drawn_stops:
                        continue
                    # Kontynuacja musi być zdążalna i porównywalnie jasna -
                    # jasny korytarz nie ciągnie ogona do bladej niszy.
                    if (other["q"] + Q_ANCHOR_TOL >= seg["q"]
                            and _joins(day, arr_t, stop, other,
                                       drawn_stops[id(other)])):
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
    - origin_ids: id() segmentów zaczynających się na starcie relacji -
      punkty startowe przeszukiwania (patrz _enumerate_journeys),
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

    origin_ids = {id(seg) for seg in kept if seg["stops"][0] in source_stops}

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
        for pos, _, arr_t, stop in seg["exits"]:
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
                if (other["q"] + Q_ANCHOR_TOL >= seg["q"]
                        and _can_board(day, arr_t, stop, other, other_board)):
                    edges.append(
                        ("transfer", pos, arr_t, stop, id(other), other_start, other_board)
                    )
        exit_edges[sid] = edges

    return {
        "origin_ids": origin_ids,
        "exit_edges": exit_edges,
        "seg_by_id": {id(seg): seg for seg in kept},
    }


def _finalize_segments(day, kept, ranges, geo_db):
    """Krok 4: agreguje po (linia, przycięta trasa) biorąc maksimum jakości,
    tnie geometrię (patrz gtfs.shape_slice) i formatuje odpowiedź.

    geo_db to połączenie współdzielone z resztą zapytania (patrz plan_flow) -
    jedno połączenie na wszystkie wycinki geometrii, także te do propozycji
    tras."""
    segments = {}
    for seg in kept:
        start_pos, cut = ranges[id(seg)]
        key = (seg["label"], tuple(seg["stops"][start_pos:cut]))
        entry = segments.get(key)
        if entry is None or seg["q"] > entry[0]:
            segments[key] = (seg["q"], seg["shape"])

    brightest = sorted(
        segments.items(), key=lambda kv: kv[1][0], reverse=True,
    )
    seg_list = []
    for (label, stops_seq), (q, shape_id) in brightest:
        path = gtfs.shape_slice(
            shape_id, [day.stop_coords[s] for s in stops_seq], geo_db,
        )
        num, mode = _line_parts(label)
        seg_list.append({
            "path": _round_path(path),
            "num": num,
            "kind": mode,
            "w": round(q, 3),
        })
    seg_list.sort(key=lambda s: s["w"])   # blade rysujemy pierwsze, jaskrawe na wierzchu
    return seg_list


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
        "minutes": round((arr_t - dep_t) / 60),
        "stops": [day.stop_names[s] for s in stops],
        "stops_count": len(stops) - 1,
        "path": _round_path(path),
    }


def _enumerate_journeys(day, graph, dep_sec, geo_db, limit=DEFAULT_JOURNEY_LIMIT):
    """Lista konkretnych propozycji tras, czytana wprost z grafu przesiadek
    mapy przepływów (patrz _extract_transfer_graph) - żadnego osobnego
    przeszukiwania CSA. Propozycja to po prostu ścieżka przez ten sam graf,
    który mapa już narysowała: nic tu nie może pokazać przesiadki, której
    nie ma na mapie.

    Przeszukiwanie w przód (najpierw najjaśniejsze gałęzie) od segmentów
    zaczynających się na starcie relacji (graph['origin_ids']), z sufitami
    kosztu skalowanymi do `limit` (patrz CANDIDATES_PER_JOURNEY/
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

    candidates = []   # łańcuchy: [(seg, board_pos, alight_pos), ...]
    seen = set()
    visits = 0

    def edge_priority(edge):
        kind, _, _, _, other_id, _, _ = edge
        if kind == "target":
            return (0, 0.0)
        return (1, -seg_by_id[other_id]["q"])

    def recurse(chain, sid, board_pos, visited):
        nonlocal visits
        if visits >= visit_cap or len(candidates) >= candidate_cap:
            return
        visits += 1
        seg = seg_by_id[sid]
        for edge in sorted(exit_edges.get(sid, ()), key=edge_priority):
            kind, alight_pos, _, _, other_id, other_start, _ = edge
            new_chain = chain + [(seg, board_pos, alight_pos)]
            if kind == "target":
                candidates.append(new_chain)
                if len(candidates) >= candidate_cap:
                    return
            elif other_id not in visited and len(new_chain) < MAX_JOURNEY_CHAIN_LEGS:
                recurse(new_chain, other_id, other_start, visited | {other_id})

    for sid in sorted(origin_ids, key=lambda i: -seg_by_id[i]["q"]):
        recurse([], sid, 0, {sid})
        if len(candidates) >= candidate_cap or visits >= visit_cap:
            break

    ranked = []
    for chain in candidates:
        signature = tuple(
            (seg["label"], day.stop_names[seg["stops"][board_pos]])
            for seg, board_pos, _ in chain
        )
        if signature in seen:
            continue
        seen.add(signature)
        first_dep = chain[0][0]["best_deps"][chain[0][0]["stops"][chain[0][1]]]
        last_seg, _, last_alight = chain[-1]
        arrival = last_seg["arr_times"][last_seg["stops"][last_alight - 1]]
        ranked.append((arrival, len(chain) - 1, -first_dep, chain))
    ranked.sort(key=lambda item: item[:3])

    journeys = []
    for arrival, _, neg_dep, chain in ranked[:limit]:
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
