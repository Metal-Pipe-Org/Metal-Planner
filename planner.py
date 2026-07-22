"""Wyszukiwanie najszybszego połączenia algorytmem CSA (Connection Scan).

CSA nie buduje grafu: wszystkie połączenia dnia (przejazdy między sąsiednimi
przystankami) są posortowane po czasie odjazdu i skanowane raz, liniowo.
Połączenie jest "osiągalne", jeśli jesteśmy już w tym kursie albo zdążymy
na jego odjazd na przystanku startowym.
"""

import math
from bisect import bisect_left, bisect_right
from datetime import datetime

import bike_transfer
import gtfs
import realtime
import traficar

TRANSFER_SEC = 120   # bufor bezpieczeństwa przy przesiadce na tym samym słupku
INF = float("inf")

# --- Traficar jako opcja dojazdu (patrz traficar.py i plan_flow) ---
CAR_SPEED_MPS = 7.5           # ~27 km/h - miejska jazda ze światłami/korkami (bez modelu ruchu na żywo)
DRIVE_DETOUR = 1.4            # mnożnik prostej -> droga (do czasu prawdziwego routingu ulicami, patrz roadmap)
CAR_UNLOCK_SEC = 60          # otwarcie auta, zapięcie pasów - narzut przed jazdą
MIN_CAR_TRIP_M = 1000        # poniżej ~1 km auto nie ma sensu (piechota/tramwaj wygrywa) - opcji nie pokazujemy
TRAFICAR_MIN_W = 0.35        # dolna podłoga jasności - użytkownik chce ZAWSZE widzieć opcję Traficar,
                             #   nawet gdy transport publiczny jest lepszy (kolor/dymek mówią, czy się opłaca)
TRAFICAR_PER_MIN_PLN = 0.80  # przybliżone stawki Traficara - patrz „Znane ograniczenia" (koszt to SZACUNEK,
TRAFICAR_PER_KM_PLN = 0.80   #   nie optymalizujemy pod niego, tylko pokazujemy orientacyjnie)


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _centroid(coords):
    n = len(coords)
    return (sum(c[0] for c in coords) / n, sum(c[1] for c in coords) / n)

# Piesi sąsiedzi przystanku (dzień.siblings) - patrz WALK_SEC/WALK_RADIUS_M
# w gtfs.py: ta sama nazwa (bufor stały) i/lub inny bliski przystanek
# (haversine + prędkość marszu). Każda para niesie WŁASNY czas dojścia,
# więc poniższy kod czyta go z krotki zamiast jednej stałej.


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


DEPARTURES_LIMIT = 24   # ile najbliższych odjazdów zwracamy (klasyczna tablica ma tyle miejsca)
KIND_MAP = {"Tramwaj": "tram", "Autobus": "bus"}


def stop_departures(stop_query, when=None, limit=DEPARTURES_LIMIT):
    """Najbliższe odjazdy z przystanku (wszystkich jego słupków) od danej chwili.

    Zwraca {"stop": nazwa, "departures": [{time, line, kind, headsign}, ...]}
    (najbliższe pierwsze) albo dict z kluczem "error".
    """
    when = when or datetime.now()

    try:
        day = gtfs.load_day(when.date())
    except FileNotFoundError as e:
        return {"error": str(e)}

    name, stop_ids, hints = gtfs.match_stop(stop_query, day)
    if name is None:
        return _unknown_stop(stop_query, hints)

    stop_set = set(stop_ids)
    from_sec = when.hour * 3600 + when.minute * 60 + when.second
    conns = day.conns

    # Opóźnienia na żywo (patrz realtime.py) - None, gdy feed niedostępny albo
    # dany odjazd jest zbyt daleko w przyszłości, by dane bieżących pojazdów
    # coś znaczyły (delay_for pilnuje okna świeżości).
    delays = realtime.live_delays(day)

    departures = []
    for i in range(bisect_left(day.dep_times, from_sec), len(conns)):
        dep_t, _, dep_s, _, trip = conns[i]
        if dep_s not in stop_set:
            continue
        label, headsign = day.trip_info[trip]
        line = label.split(" ", 1)[1] if " " in label else label
        kind = KIND_MAP.get(label.split(" ", 1)[0], "other")
        delay = delays.delay_for(kind, line, headsign, dep_t) if delays else None
        departures.append({
            "time": _fmt_time(dep_t),
            "line": line,
            "kind": kind,
            "headsign": headsign,
            "delay": None if delay is None else round(delay),
        })
        if len(departures) >= limit:
            break

    return {"stop": name, "departures": departures}


def _scan(day, source_stops, target_stops, dep_sec, source_walk=None, siblings=None):
    """Connection Scan: najwcześniejszy przyjazd do celu, ze śladem do rekonstrukcji.

    `source_walk` to opcjonalna „ostatnia mila” z dowolnego punktu (np.
    prawdziwej lokalizacji użytkownika) - [(stop_id, sek_dojścia), ...]
    z gtfs.nearest_stops, zamiast/obok nazwanych source_stops. `siblings`
    to opcjonalna relacja SĄSIEDZTWA do użycia zamiast `day.siblings` -
    tym mechanizmem `plan_flow` wpina rower WRM jako dodatkowy transfer
    (patrz `bike_transfer.py`); domyślnie (None) czysto piesza `day.siblings`.
    """
    sib = day.siblings if siblings is None else siblings
    conns = day.conns
    earliest = {}
    journey = {}      # stop_id -> ("origin",) | ("ride", idx_wsiadania, idx_wysiadania) | ("walk", skad, sek)
    trip_board = {}   # trip_id -> indeks połączenia, na którym wsiedliśmy do kursu

    # Użytkownik podaje nazwę przystanku, więc startuje ze wszystkich jego słupków.
    for stop in source_stops:
        earliest[stop] = dep_sec
        journey[stop] = ("origin",)
    # Piesza "ostatnia mila" na starcie: dojście do sąsiedniego przystanku
    # (ta sama nazwa - patrz gtfs.py - albo inny bliski) jako alternatywa dla
    # czekania na kurs z dokładnie tego słupka. Osobny przebieg PO seedowaniu
    # źródeł, żeby nie zależał od kolejności iteracji po source_stops.
    for stop in source_stops:
        for sibling, walk_sec in sib.get(stop, ()):
            walk_arr = dep_sec + walk_sec
            if walk_arr < earliest.get(sibling, INF):
                earliest[sibling] = walk_arr
                journey[sibling] = ("walk", stop, walk_sec)
    # Dojście z dowolnego punktu (np. prawdziwej lokalizacji) do najbliższych
    # przystanków - ten sam mechanizm, jednorazowo, bez dalszej relaksacji
    # (jeden skok pieszy naraz, patrz gtfs.py).
    for stop, walk_sec in (source_walk or ()):
        walk_arr = dep_sec + walk_sec
        if walk_arr < earliest.get(stop, INF):
            earliest[stop] = walk_arr
            journey[stop] = ("walk", None, walk_sec)

    targets = set(target_stops)
    best_arr = INF
    best_stop = None

    for i in range(bisect_left(day.dep_times, dep_sec), len(conns)):
        dep_t, arr_t, dep_s, arr_s, trip = conns[i]
        if dep_t > best_arr:
            break                     # dalsze odjazdy nie mogą już poprawić wyniku

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
            # Relaksacja pieszo (i rowerem, gdy `siblings` je niesie) na
            # sąsiednie przystanki (patrz gtfs.py / bike_transfer.py).
            for sibling, walk_sec in sib.get(arr_s, ()):
                walk_arr = arr_t + walk_sec
                if walk_arr < earliest.get(sibling, INF):
                    earliest[sibling] = walk_arr
                    journey[sibling] = ("walk", arr_s, walk_sec)

    return best_stop, best_arr, journey


def _reconstruct(day, journey, last_stop):
    """Odtwarza trasę od celu do startu i skleja ją w czytelne etapy."""
    legs = []
    stop = last_stop
    while journey[stop][0] != "origin":
        entry = journey[stop]
        if entry[0] == "walk":
            from_stop, walk_sec = entry[1], entry[2]
            minutes = max(1, round(walk_sec / 60))
            if day.stop_names[from_stop] == day.stop_names[stop]:
                text = f"Zmiana stanowiska na przystanku {day.stop_names[stop]} (ok. {minutes} min)"
            else:
                text = f"Dojście pieszo do przystanku {day.stop_names[stop]} (ok. {minutes} min)"
            legs.append({
                "kind": "walk",
                "text": text,
                "dep_sec": 0,
                "path": [day.stop_coords[from_stop], day.stop_coords[stop]],
            })
            stop = from_stop
        else:
            _, board_i, exit_i = entry
            board = day.conns[board_i]
            trip = board[4]
            line, headsign = day.trip_info[trip]
            # Pełna lista przystanków etapu - do narysowania linii na mapie.
            path_rows = gtfs.trip_path(
                trip, board[2], board[0], stop, day.conns[exit_i][1]
            )
            legs.append({
                "kind": "ride",
                "line": line,
                "headsign": headsign,
                "from": day.stop_names[board[2]],
                "from_time": _fmt_time(board[0]),
                "to": day.stop_names[stop],
                "to_time": _fmt_time(day.conns[exit_i][1]),
                "dep_sec": board[0],
                "stops": [day.stop_names[s] for s, _, _ in path_rows],
                "path": [day.stop_coords[s] for s, _, _ in path_rows],
            })
            stop = board[2]
    legs.reverse()
    return legs


SLOWDOWN = 1.5          # pokazujemy trasy do ~1,5x czasu najszybszej...
MIN_EXTRA_SEC = 300     # ...ale zawsze z co najmniej 5 min zapasu...
MAX_EXTRA_SEC = 1800    # ...i nigdy więcej niż 30 min (sufit rozsądku)
Q_ANCHOR_TOL = 0.10     # ogon rysujemy tylko do przesiadki w kontynuację
                        # niewiele ciemniejszą od segmentu (tolerancja jasności)
BACKTRACK_TOL_SEC = 120 # wsiadanie nie może wymagać oddalenia się od celu
                        # (cofnięcia) o więcej niż 2 min
PROGRESS_TOL_SEC = 180  # luz reguły postępu dla wyjść - metryka latest bywa
                        # zaszumiona o 1-2 min między sąsiednimi węzłami
WAIT_CAP_SEC = 1200     # przesiadka "łączy" segmenty, gdy czekanie <= 20 min
DEFAULT_Q_MIN = 0.60    # domyślny próg jasności (suwak w UI go nadpisuje)
MAX_SEGMENTS = 150      # twardy limit liczby segmentów w odpowiedzi
KEPT_CAP = 400          # twardy limit WEJŚCIA do pętli spójności (patrz niżej -
                        # ta pętla jest O(kept² x wyjścia), więc dla słabo
                        # skomunikowanego celu (rzadkie kursy -> godziny
                        # okna czasu -> tysiące wpisów w kept) potrafiła bez
                        # tego limitu liczyć się MINUTAMI zamiast milisekund;
                        # 400 to duży zapas nad MAX_SEGMENTS - dla zwykłych
                        # zapytań (kept dużo mniejsze) ten limit nic nie ucina


def plan_flow(start_query, end_query, when=None, q_min=None, start_point=None):
    """Mapa przepływów ("mrówki"): wszystkie użyteczne przejazdy start -> cel.

    Jednostką jest KURS, nie pojedynczy przeskok: dla każdego kursu, do którego
    realnie da się wsiąść (skan w przód), rysujemy jeden ciągły segment od
    przystanku wsiadania do celu albo do ostatniego wyjścia z WIDOCZNĄ
    kontynuacją (przesiadką na segment, który też jest narysowany). Jasność
    propaguje się wstecz przez przesiadki: dowóz nigdy nie jest ciemniejszy
    niż to, do czego dowozi - narysowana sieć jest spójna od startu do celu.

    q_min (0..1) to próg jasności; poniżej niego segmenty nie są wysyłane.
    start_point (lat, lon) - opcjonalnie, zamiast start_query: prawdziwa
    lokalizacja zamiast nazwy przystanku (patrz gtfs.nearest_stops).
    """
    when = when or datetime.now()
    q_min = DEFAULT_Q_MIN if q_min is None else max(0.2, min(0.95, q_min))

    try:
        day = gtfs.load_day(when.date())
    except FileNotFoundError as e:
        return {"error": str(e)}

    # Rower WRM jako dodatkowy transfer, scalony z pieszymi sąsiadami w JEDNĄ
    # relację - _scan/_forward/_backward i cała reszta tej funkcji nie
    # rozróżniają dalej "sąsiad pieszy" od "sąsiad rowerem", liczy się tylko
    # czas (patrz bike_transfer.py). `day.siblings` samo w sobie zostaje
    # nietknięte (dzielony cache dnia), więc dostępność rowerów - inaczej niż
    # piesi sąsiedzi - sprawdza się na żywo przy KAŻDYM zapytaniu, nigdy nie
    # jest "zamrożona" w cache'u dnia. Feed niedostępny -> zwykli piesi
    # sąsiedzi, bez wywalania wyszukiwania (patrz build_bike_edges).
    #
    # DWIE wersje relacji, nie jedna: piesi sąsiedzi są symetryczni (blisko
    # A <=> blisko B), ale dostępność roweru jest KIERUNKOWA (stacja A ma
    # rower ≠ stacja B ma rower) - `siblings` ("dokąd stąd można dojechać")
    # i `reverse_siblings` ("kto stąd może dojechać") to różne relacje przy
    # rowerze. Wszystko, co pyta "gdzie mogę się stąd dostać" (_scan,
    # _forward, kotwica początku, joins/join_value) używa `siblings`;
    # wszystko, co propaguje wstecz "kto dotrze DO tego miejsca" (_backward,
    # dojście DO celu) używa `reverse_siblings`.
    bike_edges, bike_reverse_edges, bike_hints = bike_transfer.build_bike_edges(day)
    siblings = bike_transfer.merge_siblings(day.siblings, bike_edges)
    reverse_siblings = bike_transfer.merge_siblings(day.siblings, bike_reverse_edges)

    # Opóźnienia na żywo (patrz realtime.py) - liczone względem REALNEGO „teraz",
    # nie `when`: dane bieżących pojazdów dotyczą tego, co jedzie w tej chwili.
    # Dla zapytań o przyszłość `delay_for` i tak zwróci None (okno świeżości),
    # więc rozkładowy margines przesiadki zostaje nietknięty. None = feed padł.
    delays = realtime.live_delays(day)

    if start_point is not None:
        source_walk = gtfs.nearest_stops(start_point[0], start_point[1], day)
        if not source_walk:
            return {"error": "Brak przystanków w rozsądnym zasięgu marszu od Twojej lokalizacji."}
        source_stops, start_name = [], "Twoja lokalizacja"
    else:
        start_name, source_stops, start_hints = gtfs.match_stop(start_query, day)
        if start_name is None:
            return _unknown_stop(start_query, start_hints)
        source_walk = None
    end_name, target_stops, end_hints = gtfs.match_stop(end_query, day)
    if end_name is None:
        return _unknown_stop(end_query, end_hints)
    if start_name == end_name:
        return {"error": "Przystanek początkowy i końcowy są takie same."}

    dep_sec = when.hour * 3600 + when.minute * 60 + when.second

    # Najszybsza trasa wyznacza skalę ("większość mrówek").
    best_stop, best_arr, _ = _scan(
        day, source_stops, target_stops, dep_sec, source_walk, siblings,
    )
    if best_stop is None:
        return {
            "error": f"Nie znaleziono połączenia {start_name} → {end_name} "
                     f"po {_fmt_time(dep_sec)} tego dnia."
        }
    duration = best_arr - dep_sec
    extra = min(max(int(duration * (SLOWDOWN - 1)), MIN_EXTRA_SEC), MAX_EXTRA_SEC)
    deadline = best_arr + extra

    earliest, arrived_by, trip_board = _forward(
        day, source_stops, dep_sec, deadline, source_walk, siblings,
    )
    latest = _backward(day, set(target_stops), dep_sec, deadline, reverse_siblings)

    # Zapas czasowy trasy optymalnej = pełna jasność.
    span = max(deadline - best_arr, 1)
    # Punkt odniesienia reguły postępu: im później można być na przystanku
    # i wciąż zdążyć (latest), tym bliżej celu się jest. Przy starcie z punktu
    # (source_walk) liczy się tak samo, jak nazwane source_stops.
    origin_stops = set(source_stops) | {s for s, _ in (source_walk or ())}
    origin_latest = max(
        (latest[s] for s in origin_stops if s in latest), default=None,
    )

    # Połączenia okna pogrupowane per kurs (tablica jest posortowana po
    # odjeździe, więc w ramach kursu indeksy idą w kolejności jazdy).
    conns = day.conns
    trip_conns = {}
    for i in range(
        bisect_left(day.dep_times, dep_sec),
        bisect_left(day.dep_times, deadline),
    ):
        trip = conns[i][4]
        if trip in trip_board:
            trip_conns.setdefault(trip, []).append(i)

    target_set = set(target_stops)
    # Start/cel "widoczne bez przesiadki" przy kotwiczeniu segmentów (niżej):
    # słupek startu/celu wprost ALBO jego pieszy/rowerowy sąsiad (patrz
    # gtfs.py, bike_transfer.py) - dojście z/do trasy liczy się tak samo jak
    # bycie na miejscu. *_walk_info: stop_id -> None (to dosłowny start/cel,
    # bez dojścia) albo (współrzędne drugiego końca, sek, hint_roweru) - do
    # narysowania samego dojścia na mapie (patrz `add_walk` niżej), nie tylko
    # do algorytmu; `hint_roweru` to None (zwykłe dojście pieszo) albo krotka
    # z bike_hints (patrz build_bike_edges) - odróżnia kind:"bike" od "walk"
    # przy rysowaniu. Kierunek: do celu trzeba użyć `reverse_siblings` (kto
    # dotrze DO celu), nie `siblings` (dokąd z celu można dojechać) - patrz
    # komentarz przy merge_siblings wyżej.
    start_walk_info = {s: None for s in source_stops}
    for s in source_stops:
        for sib, sec in siblings.get(s, ()):
            if sib not in start_walk_info:
                start_walk_info[sib] = (day.stop_coords[s], sec, bike_hints.get((s, sib)))
    if source_walk:
        for sib, sec in source_walk:
            start_walk_info.setdefault(sib, (start_point, sec, None))
    start_walkable = set(start_walk_info)

    target_walk_info = {s: None for s in target_stops}
    for s in target_stops:
        for sib, sec in reverse_siblings.get(s, ()):
            if sib not in target_walk_info:
                target_walk_info[sib] = (day.stop_coords[s], sec, bike_hints.get((sib, s)))
    target_walkable = set(target_walk_info)

    raw = {}     # (linia, pełna trasa) -> dane segmentu
    for trip, idxs in trip_conns.items():
        stops_seq = None
        board_latest = None
        departures = []   # (przystanek, odjazd) wzdłuż kursu - do przesiadek
        exits = []   # (pozycja w stops_seq, bound, przyjazd, przystanek)
        for i in idxs:
            dep_t, arr_t, dep_s, arr_s, _ = conns[i]
            if stops_seq is None:
                # Wybór miejsca wsiadania: pierwszy przystanek kursu, na
                # który zdążymy i którego osiągnięcie nie wymaga cofnięcia
                # się (oddalenia od celu) o więcej niż BACKTRACK_TOL_SEC.
                # To ucina np. "podjedź na pętlę i wracaj tym samym wozem".
                # NIE dotyczy to samego punktu startowego (`dep_s` w
                # `origin_stops`) - tam nie ma z czego "się cofać", a
                # `origin_latest` to MAKSIMUM po wszystkich słupkach startu,
                # więc słupek z dosłownie tego samego przystanku, ale
                # akurat obsługiwany przez inną, niepowiązaną linię o
                # odrobinę gorszym `latest[]`, odpadał tu jako fałszywe
                # "cofnięcie" - potrafiło to całkiem wyciąć bezpośredni,
                # poprawny kurs z prawdziwego startu (patrz PROJECT.md).
                reached = earliest.get(dep_s)
                if reached is None:
                    continue
                buffer = TRANSFER_SEC if arrived_by[dep_s] == "ride" else 0
                if reached + buffer > dep_t:
                    continue
                stop_latest = latest.get(dep_s)
                if (dep_s not in origin_stops and origin_latest is not None
                        and stop_latest is not None
                        and stop_latest < origin_latest - BACKTRACK_TOL_SEC):
                    continue
                stops_seq = [dep_s]
                board_latest = stop_latest
            elif dep_s != stops_seq[-1]:
                break                        # przerwany łańcuch - utnij
            departures.append((dep_s, dep_t))
            stops_seq.append(arr_s)
            leave_by = latest.get(arr_s)
            if leave_by is None or arr_t > leave_by:
                continue
            # Wyjście liczy się tylko, gdy jazda PRZYBLIŻYŁA do celu
            # (latest rośnie wzdłuż każdej sensownej trasy) - inaczej
            # kurs "w drugą stronę" świeciłby pełną jasnością.
            if (board_latest is not None
                    and leave_by <= board_latest - PROGRESS_TOL_SEC):
                continue
            # bound: najwcześniejszy możliwy przyjazd do celu, jeśli
            # wysiądziemy tutaj ((deadline - leave_by) = czas stąd do celu).
            exits.append((len(stops_seq), arr_t + (deadline - leave_by), arr_t, arr_s))
            if arr_s in target_set:
                break    # dojechaliśmy do celu - dalej nie rysujemy
        if not exits:
            continue     # kurs bez użytecznego wyjścia - nie rysujemy go wcale
        best_bound = min(e[1] for e in exits)
        q = max(0.0, min(1.0, (deadline - best_bound) / span))
        label, _ = day.trip_info[trip]
        key = (label, tuple(stops_seq))
        entry = raw.get(key)
        if entry is None:
            entry = raw[key] = {
                "label": label,
                "q": q,
                "stops": stops_seq,
                "pos_of": {s: p for p, s in enumerate(stops_seq)},
                "exits": exits,
                "best_deps": dict(departures),   # odjazdy najlepszego kursu
                "dep_times": {},   # przystanek -> odjazdy wszystkich kursów
                "shape": day.trip_shape.get(trip),
            }
        elif q > entry["q"]:
            entry["q"] = q
            entry["exits"] = exits
            entry["best_deps"] = dict(departures)
        for stop, dep in departures:
            entry["dep_times"].setdefault(stop, []).append(dep)

    segs = list(raw.values())
    if len(segs) > KEPT_CAP:
        # Słabo skomunikowany cel (rzadkie kursy) potrafi rozciągnąć okno
        # [dep_sec, deadline] na GODZINY (deadline zależy od best_arr, który
        # dla takiego celu bywa daleko) - a z nim liczbę wpisów tutaj w
        # tysiące. Doprecyzowanie jasności niżej i pętla spójności dalej są
        # w najgorszym razie kwadratowe względem len(segs)/len(kept), więc
        # bez tego limitu potrafiły liczyć się dziesiątkami sekund zamiast
        # milisekund (zmierzone: 33 s na przykładzie ze skrajnie rzadkim
        # połączeniem). Ucinamy tu, PRZED drogimi pętlami, do najjaśniejszych
        # wg surowego przybliżenia - to i tak jedyne, co finalnie przejdzie
        # próg MAX_SEGMENTS niżej; best_arrival/deadline (z osobnego,
        # nieograniczonego _scan wyżej) zostają dokładne niezależnie od tego
        # cięcia.
        segs.sort(key=lambda seg: seg["q"], reverse=True)
        segs = segs[:KEPT_CAP]

    for seg in segs:
        for times in seg["dep_times"].values():
            times.sort()

    def catchable(arr_t, buffer, dep_list):
        i = bisect_left(dep_list, arr_t + buffer)
        return i < len(dep_list) and dep_list[i] <= arr_t + WAIT_CAP_SEC

    # joins/candidates_at/join_value celowo używają CZYSTO PIESZYCH sąsiadów
    # (day.siblings), nie `siblings` (z rowerem) - to doprecyzowanie jasności
    # (refresh_suffixes/join_value niżej) i szukanie "z czym się to jeszcze
    # łączy" wołane są setki tysięcy razy (segment x wyjście x kandydat x do
    # 8 iteracji punktu stałego); rower ma dużo szerszy zasięg niż piesi
    # sąsiedzi (do 4 km, nie 400 m), więc wpuszczenie go tutaj powiększa
    # rozgałęzienie kombinatorycznie - zmierzone: ~3x wolniej na typowym
    # zapytaniu, bez odpowiadającej korzyści (to tylko DOPRECYZOWANIE
    # jasności już narysowanych segmentów, nie decyzja, czy segment w ogóle
    # da się narysować - tę decyzję podejmują _scan/_forward/_backward i
    # kotwica początku niżej, które JUŻ znają rower przez `siblings`).
    def joins(arr_t, stop, other, drawn=None):
        """Czy z przyjazdu (arr_t, stop) da się wskoczyć w segment `other`
        (na tym samym słupku albo pieszo na sąsiedni - patrz gtfs.py),
        opcjonalnie tylko w jego narysowanej części `drawn`."""
        for stop2, buffer in ((stop, TRANSFER_SEC), *day.siblings.get(stop, ())):
            times = other["dep_times"].get(stop2)
            if times is None or (drawn is not None and stop2 not in drawn):
                continue
            if catchable(arr_t, buffer, times):
                return True
        return False

    passing_index = {}   # stop_id -> segmenty przez niego przejeżdżające
    for seg in segs:
        for stop in seg["dep_times"]:
            passing_index.setdefault(stop, []).append(seg)

    def candidates_at(stop):
        """Segmenty osiągalne z `stop` wprost albo pieszo z sąsiedniego słupka."""
        result = []
        seen = set()
        for sid in (stop, *(s for s, _ in day.siblings.get(stop, ()))):
            for seg in passing_index.get(sid, ()):
                if id(seg) not in seen:
                    seen.add(id(seg))
                    result.append(seg)
        return result

    # Doprecyzowanie jasności: aproksymacja (deadline - latest) wlicza dla
    # rzadkich linii czekanie "do ostatniego kursu" i przekłamuje jasność.
    # Liczymy więc wartość każdego WYJŚCIA przez konkretne kontynuacje:
    # najbliższy odjazd segmentu, w który da się wskoczyć, plus najlepsze
    # z jego DALSZYCH wyjść (sufiks - wyjść sprzed punktu wskoczenia nie
    # da się już użyć). Wyjścia na cel są dokładne (wartość = przyjazd).
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
        for stop2, buffer in ((stop, TRANSFER_SEC), *day.siblings.get(stop, ())):
            times = other["dep_times"].get(stop2)
            position = other["pos_of"].get(stop2)
            if times is None or position is None:
                continue
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

    for iteration in range(8):        # punkt stały; zbiega w 2-4 obiegach
        refresh_suffixes()
        changed = False
        for seg in segs:
            for j, (pos, raw_bound, arr_t, stop) in enumerate(seg["exits"]):
                if stop in target_set:
                    continue          # wartość = przyjazd, już dokładna
                best = None
                for other in candidates_at(stop):
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

    for seg in segs:
        seg["bound"] = min(seg["exit_vals"])
        seg["q"] = max(0.0, min(1.0, (deadline - seg["bound"]) / span))

    # Próg jasności + spójność narysowanej sieci. Segment jest przycinany
    # z OBU stron do zakotwiczonych punktów:
    # - początek: start relacji albo miejsce, gdzie dołącza (zdążalnie)
    #   inny narysowany segment - żaden segment nie zaczyna się "znikąd";
    # - koniec: cel albo ostatnia przesiadka w porównywalnie jasny
    #   narysowany segment - żaden ogon nie prowadzi "w powietrze".
    # Punkt stały: zakresy mogą tylko się kurczyć, więc iteracja zbiega.
    kept = [seg for seg in segs if seg["q"] >= q_min]
    ranges = {id(seg): (0, len(seg["stops"])) for seg in kept}
    # id(seg) -> {"margin", "board_time", "start_walk", "end_walk"}; margin/
    # board_time None = start trasy (bufor/przesiadka nie dotyczy). *_walk to
    # (skąd, dokąd, sek) dla dojścia pieszo, które trzeba narysować, albo
    # None, gdy segment zaczyna/kończy się dokładnie na słupku (bez dojścia).
    info = {}
    while True:
        drawn_stops = {
            id(seg): set(seg["stops"][ranges[id(seg)][0]:ranges[id(seg)][1]])
            for seg in kept
        }
        # Indeks "z czego (i skąd, o której) da się dojść/dojechać do stopu
        # X" - budowany RAZ na iterację całego punktu stałego, NIE per
        # segment. Poprzednia wersja robiła to skanowanie (wszystkie wyjścia
        # wszystkich `other`) OSOBNO dla każdego `seg` w kotwicy początku
        # niżej - O(kept²) x wyjścia x sąsiedzi. Dla słabo skomunikowanego
        # celu (rzadkie kursy -> okno czasu na godziny -> setki/tysiące
        # segmentów) to potrafiło liczyć się dziesiątkami sekund zamiast
        # milisekund (patrz PROJECT.md). Ten sam zestaw kandydatów, policzony
        # raz i wykorzystany przez wszystkie segmenty tej iteracji.
        reachable = {}   # stop2 -> [(other, arr_t, buffer, stop_wyjścia), ...]
        for other in kept:
            o_start, o_cut = ranges[id(other)]
            for pos, _, arr_t, stop in other["exits"]:
                if not (o_start < pos <= o_cut):
                    continue         # wyjście poza narysowaną częścią
                for stop2, buffer in ((stop, TRANSFER_SEC), *siblings.get(stop, ())):
                    reachable.setdefault(stop2, []).append((other, arr_t, buffer, stop))
        survivors = []
        new_ranges = {}
        new_info = {}
        for seg in kept:
            # --- kotwica początku ---
            start_walk = None
            if seg["stops"][0] in start_walkable:
                start_pos = 0
                start_margin = None     # start trasy (wprost albo pieszo) - bufor nie dotyczy
                start_board_time = None
                walk_info = start_walk_info[seg["stops"][0]]
                if walk_info is not None:
                    start_walk = (
                        walk_info[0], day.stop_coords[seg["stops"][0]], walk_info[1], walk_info[2],
                    )
            else:
                start_pos = None
                start_margin = None
                start_board_time = None
                # (czy_wymaga_chodzenia, pozycja, -zapas) - klucz do wyboru
                # NAJLEPSZEGO kandydata, nie pierwszego czy najwcześniejszego.
                start_key = None
                # Tylko WŁASNE przystanki `seg` (ostatni wyłączony - dołączenie
                # tam byłoby puste) - dla każdego patrzymy w indeks wyżej,
                # zamiast skanować wszystkich innych od nowa.
                for stop2 in seg["stops"][:-1]:
                    times = seg["dep_times"].get(stop2)
                    if times is None:
                        continue
                    p = seg["pos_of"][stop2]
                    for other, arr_t, buffer, stop in reachable.get(stop2, ()):
                        if other is seg:
                            continue
                        if catchable(arr_t, buffer, times):
                            i = bisect_left(times, arr_t + buffer)
                            margin = times[i] - arr_t - buffer
                            # Priorytet: (1) BEZ CHODZENIA bije chodzenie,
                            # niezależnie od pozycji - jechać dalej do
                            # przystanku, na którym `seg` i tak się
                            # zatrzymuje, jest zawsze lepsze niż wysiąść
                            # wcześniej i iść pieszo (to była przyczyna
                            # "każe wysiąść wcześniej i iść, choć można
                            # dojechać wprost do przesiadki" - patrz
                            # PROJECT.md); (2) przy remisie najwcześniejsza
                            # pozycja (więcej odcinka `seg` do narysowania);
                            # (3) przy remisie obu - większy zapas.
                            key = (0 if stop2 == stop else 1, p, -margin)
                            if start_key is None or key < start_key:
                                start_key = key
                                start_pos = p
                                start_margin = margin
                                start_board_time = times[i]
                                start_walk = (
                                    (day.stop_coords[stop], day.stop_coords[stop2], buffer,
                                     bike_hints.get((stop, stop2)))
                                    if stop2 != stop else None
                                )
                if start_pos is None:
                    continue                 # nie da się tu dojechać widocznie
            # --- kotwica końca ---
            # Wyjścia w seg["exits"] przeszły regułę postępu (PROGRESS_TOL_SEC)
            # względem NATURALNEGO wsiadania tego kursu (patrz budowa raw[]
            # wyżej) - ale gdy start_pos wskazuje na inne miejsce (dołączenie
            # przez kogoś innego, czasem pieszo), ten punkt odniesienia jest
            # nieaktualny. Bez ponownej walidacji kurs jadący od miejsca
            # dołączenia W ZŁĄ STRONĘ (np. zahaczony piechotą o przystanek,
            # który akurat ma dobre latest[] z zupełnie INNEGO powodu) mógłby
            # świecić pełną jasnością przez kilka przystanków, zanim faktyczna
            # trasa go dogoni - patrz PROJECT.md, ten sam mechanizm co reguła
            # postępu przy budowie wyjść, tylko przeliczony względem
            # rzeczywistego (a nie domyślnego) miejsca wsiadania.
            effective_board_latest = latest.get(seg["stops"][start_pos])
            cut = 0
            end_walk = None
            # (tier, -pozycja) - najniższy tier wygrywa; w nim najdalsza
            # pozycja (więcej odcinka narysowane). Bez tego kolejne wyjścia
            # nadpisywały cut/end_walk bezwarunkowo (kto ostatni w pętli, ten
            # wygrywał) - kurs, który już dojechał DOKŁADNIE do celu, potrafił
            # zostać przedłużony za cel do dalszej, gorszej przesiadki albo
            # przypadkowego pieszego sąsiada napotkanego później na trasie
            # (patrz PROJECT.md - "chodzenie z przypadkowych, niepowiązanych
            # punktów"). Tier 0 = dotarcie DOKŁADNIE na cel (bez chodzenia),
            # tier 1 = cel osiągalny pieszo z tego przystanku, tier 2 =
            # przesiadka na inny narysowany segment - w tej kolejności, bo
            # dotarcie na miejsce zawsze bije jazdę dalej w poszukiwaniu
            # przesiadki.
            end_key = None
            for pos, _, arr_t, stop in seg["exits"]:
                if pos <= start_pos + 1:
                    continue                 # wyjście przed/na starcie segmentu
                leave_by = latest.get(stop)
                if (effective_board_latest is not None and leave_by is not None
                        and leave_by <= effective_board_latest - PROGRESS_TOL_SEC):
                    continue                 # stąd dalej to jazda w złą stronę
                if stop in target_walkable:
                    walk_info = target_walk_info.get(stop)
                    key = (0 if walk_info is None else 1, -pos)
                    if end_key is None or key < end_key:
                        end_key = key
                        cut = pos            # cel jest "widoczny" z definicji (wprost albo pieszo)
                        end_walk = (
                            (day.stop_coords[stop], walk_info[0], walk_info[1], walk_info[2])
                            if walk_info is not None else None
                        )
                    continue
                for other in candidates_at(stop):
                    if other is seg or id(other) not in drawn_stops:
                        continue
                    # Kontynuacja musi być zdążalna i porównywalnie jasna -
                    # jasny korytarz nie ciągnie ogona do bladej niszy.
                    if (other["q"] + Q_ANCHOR_TOL >= seg["q"]
                            and joins(arr_t, stop, other,
                                      drawn_stops[id(other)])):
                        key = (2, -pos)      # tier 2: przesiadka - gorsza niż dotarcie na cel
                        if end_key is None or key < end_key:
                            end_key = key
                            cut = pos
                            end_walk = None  # przesiadka na inny narysowany segment, nie "prawie cel"
                        break
            if cut >= start_pos + 2:
                survivors.append(seg)
                new_ranges[id(seg)] = (start_pos, cut)
                new_info[id(seg)] = {
                    "margin": start_margin, "board_time": start_board_time,
                    "start_walk": start_walk, "end_walk": end_walk,
                }
        if len(survivors) == len(kept) and \
                new_ranges == {k: ranges[k] for k in new_ranges}:
            info = new_info
            break
        kept = survivors
        ranges = new_ranges
        info = new_info

    segments = {}
    for seg in kept:
        start_pos, cut = ranges[id(seg)]
        key = (seg["label"], tuple(seg["stops"][start_pos:cut]))
        entry = segments.get(key)
        if entry is None or seg["q"] > entry[0]:
            segments[key] = (seg["q"], seg["shape"], info[id(seg)])

    brightest = sorted(
        segments.items(), key=lambda kv: kv[1][0], reverse=True,
    )[:MAX_SEGMENTS]
    seg_list = []
    # Dojścia pieszo doklejone do pokazanych segmentów (start/przesiadka/cel -
    # patrz *_walk w pętli kotwiczenia wyżej) - narysowane jako osobne segmenty
    # `kind:"walk"`, nie tylko domyślnie "gdzieś między liniami". Klucz to
    # NAZWY końców (nie dokładne współrzędne): duży węzeł (patrz PROJECT.md -
    # "8 Maja" ma 6 słupków) potrafi dać kilka osobnych dojść pieszo między
    # różnymi słupkami tych samych dwóch przystanków, prawie równoległych na
    # mapie - to samo dojście "w sensie użytkownika" rysuje się więc raz,
    # jasnością najjaśniejszego z konkurujących wariantów (patrz add_walk).
    # Punkt z prawdziwej lokalizacji (patrz Frontend) nie ma nazwy - zostaje
    # kluczowany współrzędnymi, jak dawniej (nie ma z czym go grupować).
    stop_name_by_coord = {coord: day.stop_names[sid] for sid, coord in day.stop_coords.items()}
    walk_segments = {}

    def add_walk(from_coord, to_coord, walk_sec, q, bike_hint=None):
        if from_coord == to_coord:
            return
        # `bike_hint` (patrz build_bike_edges) odróżnia rower od zwykłego
        # dojścia pieszo - osobny wpis w kluczu, żeby nie sklejać dwóch
        # różnych typów transferu między tymi samymi nazwami w jeden wariant.
        key = (stop_name_by_coord.get(from_coord, from_coord),
               stop_name_by_coord.get(to_coord, to_coord),
               bike_hint is not None)
        entry = walk_segments.get(key)
        if entry is None or q > entry["w"]:
            walk_segments[key] = {
                "path": (from_coord, to_coord), "sec": walk_sec, "w": q, "bike_hint": bike_hint,
            }

    gtfs.geo_generation()           # jeden stat na zapytanie; czyści cache po podmianie bazy
    geo_db = gtfs.open_db()         # jedno połączenie na wszystkie wycinki geometrii
    try:
        for (label, stops_seq), (q, shape_id, seg_info) in brightest:
            path = gtfs.shape_slice(
                shape_id, [day.stop_coords[s] for s in stops_seq], geo_db,
            )
            board_time = seg_info["board_time"]
            num = label.split(" ", 1)[1] if " " in label else label
            kind = KIND_MAP.get(label.split(" ", 1)[0], "other")
            # Opóźnienie na żywo linii, w którą się TU wsiada (patrz realtime.py):
            # spóźniony kurs odjeżdża później, więc realny zapas na przesiadkę =
            # rozkładowy + to opóźnienie. None, gdy to start trasy (bez przesiadki),
            # feed padł albo odjazd jest zbyt daleko w przyszłości.
            board_delay = None
            if delays is not None and board_time is not None and seg_info["margin"] is not None:
                board_delay = delays.delay_for(kind, num, sched_sec=board_time)
            seg_list.append({
                "path": [[round(lat, 5), round(lon, 5)] for lat, lon in path],
                "num": num,
                "kind": kind,
                "w": round(q, 3),
                "transfer_margin": seg_info["margin"],
                "board_time": _fmt_time(board_time) if board_time is not None else None,
                "board_stop": day.stop_names[stops_seq[0]] if seg_info["margin"] is not None else None,
                "board_delay": None if board_delay is None else round(board_delay),
                "walk_sec": None,
            })
            if seg_info["start_walk"] is not None:
                s_from, s_to, s_sec, s_hint = seg_info["start_walk"]
                add_walk(s_from, s_to, s_sec, q, s_hint)
            if seg_info["end_walk"] is not None:
                e_from, e_to, e_sec, e_hint = seg_info["end_walk"]
                add_walk(e_from, e_to, e_sec, q, e_hint)
    finally:
        geo_db.close()

    for w in walk_segments.values():
        hint = w["bike_hint"]   # None (pieszo) albo (stacja1, stacja2, sek_dojscia1, sek_roweru, sek_dojscia2)
        seg_list.append({
            "path": [[round(lat, 5), round(lon, 5)] for lat, lon in w["path"]],
            "num": "",
            "kind": "bike" if hint is not None else "walk",
            "w": round(w["w"], 3),
            "transfer_margin": None,
            "board_time": None,
            "board_stop": None,
            "board_delay": None,
            "walk_sec": w["sec"],
            "bike_stations": [hint[0], hint[1]] if hint is not None else None,
        })

    # Traficar jako całościowa alternatywa (patrz _traficar_option) - liczona
    # niezależnie od mapy przepływów, zawsze dołączana, jeśli jest wolne auto
    # i trasa jest dość długa. Punkt startu/celu to prawdziwa lokalizacja
    # (start_point) albo środek słupków danego przystanku.
    start_coord = start_point if start_point is not None else (
        _centroid([day.stop_coords[s] for s in source_stops]) if source_stops else None
    )
    dest_coord = _centroid([day.stop_coords[s] for s in target_stops]) if target_stops else None
    if start_coord and dest_coord:
        seg_list.extend(_traficar_option(dep_sec, start_coord, dest_coord, deadline, span))

    seg_list.sort(key=lambda s: s["w"])   # blade rysujemy pierwsze, jaskrawe na wierzchu

    return {
        "start": start_name,
        "end": end_name,
        "departure": _fmt_time(dep_sec),
        "best_arrival": _fmt_time(best_arr),
        "deadline": _fmt_time(deadline),
        "segments": seg_list,
    }


def _forward(day, source_stops, dep_sec, deadline, source_walk=None, siblings=None):
    """Jak _scan, ale bez celu: najwcześniejsze przyjazdy wszędzie do deadline.

    Zwraca (earliest, arrived_by, trip_board); trip_board[kurs] to indeks
    pierwszego połączenia, na które w ogóle da się zdążyć (właściwe miejsce
    wsiadania, z regułą postępu, wybiera dopiero plan_flow). `source_walk`
    jak w _scan. `siblings` jak w _scan (domyślnie `day.siblings`).
    """
    sib = day.siblings if siblings is None else siblings
    conns = day.conns
    earliest = {}
    arrived_by = {}     # 'origin' | 'ride' | 'walk' - do bufora przesiadki
    trip_board = {}

    for stop in source_stops:
        earliest[stop] = dep_sec
        arrived_by[stop] = "origin"
    # Piesza "ostatnia mila" na starcie (patrz _scan) - osobny przebieg
    # po pełnym zasiedleniu source_stops.
    for stop in source_stops:
        for sibling, walk_sec in sib.get(stop, ()):
            walk_arr = dep_sec + walk_sec
            if walk_arr < earliest.get(sibling, INF):
                earliest[sibling] = walk_arr
                arrived_by[sibling] = "walk"
    for stop, walk_sec in (source_walk or ()):
        walk_arr = dep_sec + walk_sec
        if walk_arr < earliest.get(stop, INF):
            earliest[stop] = walk_arr
            arrived_by[stop] = "walk"

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
            for sibling, walk_sec in sib.get(arr_s, ()):
                walk_arr = arr_t + walk_sec
                if walk_arr < earliest.get(sibling, INF):
                    earliest[sibling] = walk_arr
                    arrived_by[sibling] = "walk"
    return earliest, arrived_by, trip_board


def _backward(day, target_set, dep_sec, deadline, siblings=None):
    """Skan wstecz: najpóźniejszy moment na każdym przystanku, z którego
    da się jeszcze dotrzeć do celu przed deadline.

    Połączenia przetwarzamy malejąco po odjeździe - wszystko, co wpływa na
    latest[przystanek] po czasie t, jest już policzone, zanim do t dojdziemy.
    `siblings` jak w _scan (domyślnie `day.siblings`).
    """
    sib = day.siblings if siblings is None else siblings
    conns = day.conns
    latest = {stop: deadline for stop in target_set}
    # Piesza "ostatnia mila" do celu - symetria do _forward: dotrzeć na
    # sąsiedni słupek i dojść stamtąd pieszo do celu też się liczy.
    for stop in target_set:
        for sibling, walk_sec in sib.get(stop, ()):
            walk_dep = deadline - walk_sec
            if walk_dep > latest.get(sibling, -1):
                latest[sibling] = walk_dep
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
            for sibling, walk_sec in sib.get(dep_s, ()):
                walk_dep = dep_t - walk_sec
                if walk_dep > latest.get(sibling, -1):
                    latest[sibling] = walk_dep
    return latest


def _traficar_option(dep_sec, start_coord, dest_coord, deadline, span):
    """Opcja Traficar „od drzwi do drzwi": dojście do najbliższego wolnego
    auta, przejazd do celu (albo do najbliższego punktu STREFY ZWROTU, gdy
    cel jest poza nią - np. w strefie pieszej) i ewentualne dojście resztą.

    Zwraca listę segmentów (walk do auta, car, opcjonalnie walk do celu) albo
    [] gdy: brak wolnego auta (feed padł/pusty) albo trasa jest za krótka na
    sensowną jazdę autem (MIN_CAR_TRIP_M). Poza tymi przypadkami pokazujemy ją
    ZAWSZE - użytkownik chce zawsze widzieć co najmniej jedną opcję Traficar;
    jasność (z podłogą TRAFICAR_MIN_W, żeby nie znikła przy dobrym transporcie)
    i dymek z czasem/kosztem mówią, czy się opłaca. Nie odsiewamy jej progiem
    q_min ani limitem MAX_SEGMENTS - to osobna, całościowa alternatywa dla
    mapy przepływów, nie jeden z jej segmentów.

    Koszt to SZACUNEK (patrz „Znane ograniczenia") - pokazujemy orientacyjnie,
    świadomie NIE optymalizujemy pod niego (założenie użytkownika)."""
    if _haversine_m(start_coord[0], start_coord[1], dest_coord[0], dest_coord[1]) < MIN_CAR_TRIP_M:
        return []
    car, access_m = traficar.nearest_available(start_coord[0], start_coord[1])
    if car is None:
        return []
    rlat, rlon, egress_m = traficar.nearest_return_point(dest_coord[0], dest_coord[1])
    car_coord = (car["lat"], car["lon"])
    return_coord = (rlat, rlon)

    drive_m = _haversine_m(car_coord[0], car_coord[1], return_coord[0], return_coord[1]) * DRIVE_DETOUR
    walk_to_sec = max(1, round(access_m / gtfs.WALK_SPEED_MPS))
    drive_sec = CAR_UNLOCK_SEC + round(drive_m / CAR_SPEED_MPS)
    walk_from_sec = round(egress_m / gtfs.WALK_SPEED_MPS)
    arrival = dep_sec + walk_to_sec + drive_sec + walk_from_sec

    honest_w = max(0.0, min(1.0, (deadline - arrival) / span))
    w = max(honest_w, TRAFICAR_MIN_W)
    drive_km = drive_m / 1000
    cost = round(drive_sec / 60 * TRAFICAR_PER_MIN_PLN + drive_km * TRAFICAR_PER_KM_PLN, 1)

    def seg(path, kind, **extra):
        base = {
            "path": [[round(la, 5), round(lo, 5)] for la, lo in path],
            "num": "", "kind": kind, "w": round(w, 3),
            "transfer_margin": None, "board_time": None, "board_stop": None,
            "board_delay": None, "walk_sec": None,
        }
        base.update(extra)
        return base

    segs = [seg([start_coord, car_coord], "walk", walk_sec=walk_to_sec)]
    segs.append(seg(
        [car_coord, return_coord], "car",
        car_sec=drive_sec, car_km=round(drive_km, 1), car_cost=cost,
        car_plate=car["plate"], car_eta=_fmt_time(arrival),
    ))
    if egress_m > 1:
        segs.append(seg([return_coord, dest_coord], "walk", walk_sec=walk_from_sec))
    return segs


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
