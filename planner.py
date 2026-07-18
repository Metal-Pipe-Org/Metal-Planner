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


def plan_flow(start_query, end_query, when=None, q_min=None):
    """Mapa przepływów ("mrówki"): wszystkie użyteczne przejazdy start -> cel.

    Jednostką jest KURS, nie pojedynczy przeskok: dla każdego kursu, do którego
    realnie da się wsiąść (skan w przód), rysujemy jeden ciągły segment od
    przystanku wsiadania do celu albo do ostatniego wyjścia z WIDOCZNĄ
    kontynuacją (przesiadką na segment, który też jest narysowany). Jasność
    propaguje się wstecz przez przesiadki: dowóz nigdy nie jest ciemniejszy
    niż to, do czego dowozi - narysowana sieć jest spójna od startu do celu.

    q_min (0..1) to próg jasności; poniżej niego segmenty nie są wysyłane.
    """
    when = when or datetime.now()
    q_min = DEFAULT_Q_MIN if q_min is None else max(0.2, min(0.95, q_min))

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

    earliest, arrived_by, trip_board = _forward(day, source_stops, dep_sec, deadline)
    latest = _backward(day, set(target_stops), dep_sec, deadline)

    # Zapas czasowy trasy optymalnej = pełna jasność.
    span = max(deadline - best_arr, 1)
    # Punkt odniesienia reguły postępu: im później można być na przystanku
    # i wciąż zdążyć (latest), tym bliżej celu się jest.
    origin_latest = max(
        (latest[s] for s in source_stops if s in latest), default=None,
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
                reached = earliest.get(dep_s)
                if reached is None:
                    continue
                buffer = TRANSFER_SEC if arrived_by[dep_s] == "ride" else 0
                if reached + buffer > dep_t:
                    continue
                stop_latest = latest.get(dep_s)
                if (origin_latest is not None and stop_latest is not None
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

    stop_names = day.stop_names
    segs = list(raw.values())

    for seg in segs:
        for times in seg["dep_times"].values():
            times.sort()

    def catchable(arr_t, buffer, dep_list):
        i = bisect_left(dep_list, arr_t + buffer)
        return i < len(dep_list) and dep_list[i] <= arr_t + WAIT_CAP_SEC

    def joins(arr_t, stop, other, drawn=None):
        """Czy z przyjazdu (arr_t, stop) da się wskoczyć w segment `other`
        (na tym samym słupku lub sąsiednim o tej samej nazwie), opcjonalnie
        tylko w jego narysowanej części `drawn`."""
        for stop2 in (stop, *day.siblings.get(stop, ())):
            times = other["dep_times"].get(stop2)
            if times is None or (drawn is not None and stop2 not in drawn):
                continue
            buffer = TRANSFER_SEC if stop2 == stop else WALK_SEC
            if catchable(arr_t, buffer, times):
                return True
        return False

    passing_index = {}   # nazwa przystanku -> segmenty przez niego przejeżdżające
    for seg in segs:
        for stop in seg["dep_times"]:
            passing_index.setdefault(stop_names[stop], []).append(seg)

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
        for stop2 in (stop, *day.siblings.get(stop, ())):
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

    for iteration in range(8):        # punkt stały; zbiega w 2-4 obiegach
        refresh_suffixes()
        changed = False
        for seg in segs:
            for j, (pos, raw_bound, arr_t, stop) in enumerate(seg["exits"]):
                if stop in target_set:
                    continue          # wartość = przyjazd, już dokładna
                best = None
                for other in passing_index.get(stop_names[stop], ()):
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
    while True:
        drawn_stops = {
            id(seg): set(seg["stops"][ranges[id(seg)][0]:ranges[id(seg)][1]])
            for seg in kept
        }
        survivors = []
        new_ranges = {}
        for seg in kept:
            # --- kotwica początku ---
            if stop_names[seg["stops"][0]] == start_name:
                start_pos = 0
            else:
                start_pos = None
                for other in kept:
                    if other is seg:
                        continue
                    o_start, o_cut = ranges[id(other)]
                    for pos, _, arr_t, stop in other["exits"]:
                        if not (o_start < pos <= o_cut):
                            continue         # wyjście poza narysowaną częścią
                        for stop2 in (stop, *day.siblings.get(stop, ())):
                            p = seg["pos_of"].get(stop2)
                            times = seg["dep_times"].get(stop2)
                            if p is None or times is None:
                                continue
                            if p >= len(seg["stops"]) - 1:
                                continue     # dołączenie na samym końcu - puste
                            buffer = TRANSFER_SEC if stop2 == stop else WALK_SEC
                            if catchable(arr_t, buffer, times):
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
                for other in passing_index.get(stop_names[stop], ()):
                    if other is seg or id(other) not in drawn_stops:
                        continue
                    # Kontynuacja musi być zdążalna i porównywalnie jasna -
                    # jasny korytarz nie ciągnie ogona do bladej niszy.
                    if (other["q"] + Q_ANCHOR_TOL >= seg["q"]
                            and joins(arr_t, stop, other,
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

    segments = {}
    for seg in kept:
        start_pos, cut = ranges[id(seg)]
        key = (seg["label"], tuple(seg["stops"][start_pos:cut]))
        entry = segments.get(key)
        if entry is None or seg["q"] > entry[0]:
            segments[key] = (seg["q"], seg["shape"])

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
