"""Wyszukiwanie najszybszego połączenia algorytmem CSA (Connection Scan).

CSA nie buduje grafu: wszystkie połączenia dnia (przejazdy między sąsiednimi
przystankami) są posortowane po czasie odjazdu i skanowane raz, liniowo.
Połączenie jest "osiągalne", jeśli jesteśmy już w tym kursie albo zdążymy
na jego odjazd na przystanku startowym.
"""

from bisect import bisect_left
from datetime import datetime

import gtfs

TRANSFER_SEC = 120   # bufor bezpieczeństwa przy przesiadce na tym samym słupku
WALK_SEC = 180       # przejście między słupkami o tej samej nazwie przystanku
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


def _scan(day, source_stops, target_stops, dep_sec):
    """Connection Scan: najwcześniejszy przyjazd do celu, ze śladem do rekonstrukcji."""
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
            # Relaksacja pieszo na pozostałe słupki tego samego przystanku.
            for sibling in day.siblings.get(arr_s, ()):
                walk_arr = arr_t + WALK_SEC
                if walk_arr < earliest.get(sibling, INF):
                    earliest[sibling] = walk_arr
                    journey[sibling] = ("walk", arr_s)

    return best_stop, best_arr, journey


def _reconstruct(day, journey, last_stop):
    """Odtwarza trasę od celu do startu i skleja ją w czytelne etapy."""
    legs = []
    stop = last_stop
    while journey[stop][0] != "origin":
        entry = journey[stop]
        if entry[0] == "walk":
            from_stop = entry[1]
            legs.append({
                "kind": "walk",
                "text": f"Zmiana stanowiska na przystanku "
                        f"{day.stop_names[stop]} (ok. {WALK_SEC // 60} min)",
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


SLOWDOWN = 1.3          # pokazujemy trasy do ~1,3x czasu najszybszej...
MIN_EXTRA_SEC = 300     # ...ale zawsze z co najmniej 5 min zapasu...
MAX_EXTRA_SEC = 900     # ...i nigdy więcej niż 15 min (długie trasy)
BOUND_TOL_SEC = 180     # ogon segmentu ucinamy, gdy jazda dalej pogarsza
                        # najlepszy możliwy przyjazd o ponad 3 min
Q_MIN = 0.05            # segmenty praktycznie niewidoczne odrzucamy
MAX_SEGMENTS = 150      # twardy limit liczby segmentów w odpowiedzi


def plan_flow(start_query, end_query, when=None):
    """Mapa przepływów ("mrówki"): wszystkie użyteczne przejazdy start -> cel.

    Jednostką jest KURS, nie pojedynczy przeskok: dla każdego kursu, do którego
    realnie da się wsiąść (skan w przód), rysujemy jeden ciągły segment od
    przystanku wsiadania do ostatniego przystanku, z którego jeszcze da się
    dojechać do celu przed deadline (skan wstecz). Kursy bez takiego wyjścia
    nie są rysowane wcale. Intensywność jest jedna na cały segment - zapas
    czasu najlepszego wyjścia względem deadline (1,5x czasu najszybszej trasy).
    """
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

    # Najszybsza trasa wyznacza skalę ("większość mrówek").
    best_stop, best_arr, _ = _scan(day, source_stops, target_stops, dep_sec)
    if best_stop is None:
        return {
            "error": f"Nie znaleziono połączenia {start_name} → {end_name} "
                     f"po {_fmt_time(dep_sec)} tego dnia."
        }
    duration = best_arr - dep_sec
    extra = min(max(int(duration * (SLOWDOWN - 1)), MIN_EXTRA_SEC), MAX_EXTRA_SEC)
    deadline = best_arr + extra

    earliest, trip_board = _forward(day, source_stops, dep_sec, deadline)
    latest = _backward(day, set(target_stops), dep_sec, deadline)

    # Zapas czasowy trasy optymalnej = pełna jasność.
    span = max(deadline - best_arr, 1)

    # Połączenia okna pogrupowane per kurs, od miejsca wsiadania.
    # Tablica jest posortowana po odjeździe, więc w ramach jednego kursu
    # indeksy idą zgodnie z kolejnością jazdy.
    conns = day.conns
    trip_conns = {}
    for i in range(
        bisect_left(day.dep_times, dep_sec),
        bisect_left(day.dep_times, deadline),
    ):
        trip = conns[i][4]
        board_i = trip_board.get(trip)
        if board_i is None or i < board_i:
            continue
        trip_conns.setdefault(trip, []).append(i)

    target_set = set(target_stops)
    segments = {}
    for trip, idxs in trip_conns.items():
        stops_seq = [conns[idxs[0]][2]]     # przystanek wsiadania
        exits = []       # (pozycja w stops_seq, bound = najlepszy przyjazd do celu)
        for i in idxs:
            dep_t, arr_t, dep_s, arr_s, _ = conns[i]
            if dep_s != stops_seq[-1]:
                break                        # przerwany łańcuch - utnij
            stops_seq.append(arr_s)
            leave_by = latest.get(arr_s)
            if leave_by is not None and arr_t <= leave_by:
                # bound: najwcześniejszy możliwy przyjazd do celu, jeśli
                # wysiądziemy tutaj ((deadline - leave_by) = czas stąd do celu).
                exits.append((len(stops_seq), arr_t + (deadline - leave_by)))
                if arr_s in target_set:
                    break    # dojechaliśmy do celu - dalej nie rysujemy
        if not exits:
            continue     # kurs bez użytecznego wyjścia - nie rysujemy go wcale
        best_bound = min(bound for _, bound in exits)
        q = max(0.0, min(1.0, (deadline - best_bound) / span))
        if q < Q_MIN:
            continue
        if stops_seq[exits[-1][0] - 1] in target_set:
            cut = exits[-1][0]      # linia jadąca do celu: rysuj dokładnie do celu
        else:
            # Utnij ogon: jedź dalej tylko dopóki to nie psuje wyniku o >3 min
            # (koniec z jasnym rysowaniem "za punkt docelowy i z powrotem").
            cut = max(pos for pos, bound in exits
                      if bound <= best_bound + BOUND_TOL_SEC)
        label, _ = day.trip_info[trip]
        key = (label, tuple(stops_seq[:cut]))
        entry = segments.get(key)
        if entry is None or q > entry[0]:
            segments[key] = (q, day.trip_shape.get(trip))

    kind_map = {"Tramwaj": "tram", "Autobus": "bus"}
    brightest = sorted(
        segments.items(), key=lambda kv: kv[1][0], reverse=True,
    )[:MAX_SEGMENTS]
    seg_list = []
    gtfs.geo_generation()           # jeden stat na zapytanie; czyści cache po podmianie bazy
    geo_db = gtfs.open_db()         # jedno połączenie na wszystkie wycinki geometrii
    try:
        for (label, stops_seq), (q, shape_id) in brightest:
            path = gtfs.shape_slice(
                shape_id, [day.stop_coords[s] for s in stops_seq], geo_db,
            )
            seg_list.append({
                "path": [[round(lat, 5), round(lon, 5)] for lat, lon in path],
                "num": label.split(" ", 1)[1] if " " in label else label,
                "kind": kind_map.get(label.split(" ", 1)[0], "other"),
                "w": round(q, 3),
            })
    finally:
        geo_db.close()
    seg_list.sort(key=lambda s: s["w"])   # blade rysujemy pierwsze, jaskrawe na wierzchu

    return {
        "start": start_name,
        "end": end_name,
        "departure": _fmt_time(dep_sec),
        "best_arrival": _fmt_time(best_arr),
        "deadline": _fmt_time(deadline),
        "segments": seg_list,
    }


def _forward(day, source_stops, dep_sec, deadline):
    """Jak _scan, ale bez celu: najwcześniejsze przyjazdy wszędzie do deadline.

    Zwraca (earliest, trip_board), gdzie trip_board[kurs] to indeks połączenia,
    na którym najwcześniej da się do niego wsiąść - początek segmentu na mapie.
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
    return earliest, trip_board


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
