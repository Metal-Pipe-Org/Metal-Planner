"""Rozkłady jazdy: kursy jednej linii i tablica odjazdów z przystanku.

Drugi tryb tej samej aplikacji - obok wyszukiwarki połączeń, która odpowiada
na pytanie "jak dojechać stąd tam", ten moduł odpowiada na dwa inne, zadawane
równie często:

  * "co robi linia 17 dzisiaj" - warianty trasy (kierunki), pełna lista
    przystanków i godziny każdego kursu, razem z geometrią do narysowania
    na mapie;
  * "co odjeżdża z Galerii Dominikańskiej" - tablica odjazdów wszystkich
    linii z jednego przystanku, z możliwością wybrania, które z nich mają
    być w niej złączone.

Skąd biorą się dane. Tablica przystanku idzie z tablicy połączeń dnia
(gtfs.load_day) - tam odjazd z przystanku to po prostu połączenie, którego
`from` jest tym przystankiem, a oś czasu ma już doklejony ogon doby
poprzedniej, więc rozkład na daną DATĘ zaczyna się o północy, a nie o
pierwszym kursie kalendarza GTFS. Rozkład linii idzie wprost z SQLite:
interesują nas wszystkie kursy doby rozkładowej, także te wyjeżdżające po
północy (25:10 to kurs "dzisiejszy", tak samo jak w rozkładzie na słupku).
"""

import gtfs

# route_type z GTFS -> rodzaj używany przez front (kolor linii, etykieta).
# Ten sam podział, co planner._line_parts, tylko liczony z typu trasy, a nie
# z etykiety tekstowej - tu mamy surowy wiersz z bazy.
MODE_OF_TYPE = {0: "tram", 3: "bus"}
MODE_LABEL = {"tram": "Tramwaj", "bus": "Autobus", "other": "Linia"}
# Odwrotność: etykieta kursu z DayData ("Tramwaj 17") niesie rodzaj słowem.
MODE_OF_LABEL = {"Tramwaj": "tram", "Autobus": "bus"}


def _mode_of(route_type):
    return MODE_OF_TYPE.get(route_type, "other")


def _num_of(short_name, long_name):
    return (short_name or long_name or "").strip()


def _hhmm(sec):
    """Sekundy na osi doby -> 'GG:MM'. Kursy po północy mają ponad 24 h
    (25:10), a na tarczy zegara to 01:10 - dnia doczepia osobne pole."""
    return f"{(sec // 3600) % 24:02d}:{(sec % 3600) // 60:02d}"


def _line_sort_key(num, mode):
    """Ten sam porządek, co na mapie przepływów (planner._line_sort_key):
    tramwaje przed autobusami, w obrębie rodzaju numerycznie."""
    return (
        {"tram": 0, "bus": 1}.get(mode, 2),
        int(num) if num.isdigit() else 10 ** 6,
        num,
    )


def all_lines():
    """Wszystkie linie rozkładu - do podpowiedzi w polu 'Linia'.

    Czytane raz przy renderowaniu strony (tak samo jak nazwy przystanków),
    więc front nie musi po nie chodzić osobnym zapytaniem.
    """
    db = gtfs.open_db()
    try:
        rows = db.execute(
            "SELECT route_short_name, route_long_name, route_type FROM routes"
        ).fetchall()
    finally:
        db.close()

    seen = {}
    for short_name, long_name, route_type in rows:
        num = _num_of(short_name, long_name)
        if not num:
            continue
        mode = _mode_of(route_type)
        seen[(num, mode)] = None
    return [
        {"num": num, "mode": mode, "label": f"{MODE_LABEL[mode]} {num}"}
        for num, mode in sorted(seen, key=lambda k: _line_sort_key(*k))
    ]


def _stop_times_of(db, trip_ids):
    """trip_id -> [(stop_id, przyjazd, odjazd), ...] po kolei przystanków.

    Pytamy porcjami, bo SQLite ma sufit na liczbę parametrów zapytania,
    a popularna linia potrafi mieć kilkaset kursów dziennie.
    """
    rows = {}
    ids = list(trip_ids)
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        placeholders = ",".join("?" * len(chunk))
        for trip_id, stop_id, arrival_sec, departure_sec in db.execute(
            f"SELECT trip_id, stop_id, arrival_sec, departure_sec FROM stop_times "
            f"WHERE trip_id IN ({placeholders}) ORDER BY trip_id, stop_sequence",
            chunk,
        ):
            rows.setdefault(trip_id, []).append((stop_id, arrival_sec, departure_sec))
    return rows


def _stops_geo(db):
    return {
        stop_id: (stop_name, lat, lon)
        for stop_id, stop_name, lat, lon in db.execute(
            "SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops"
        )
    }


def _path_of(shape_id, coords, db):
    """Geometria przejazdu po podanych przystankach - prawdziwy kształt
    z shapes.txt, a gdy go nie ma (albo się nie dopasował) łamana po
    przystankach. Dokładnie to samo, co rysuje mapa przepływów."""
    if len(coords) < 2:
        return [[round(lat, 5), round(lon, 5)] for lat, lon in coords]
    sliced = gtfs.shape_slice(shape_id, [tuple(c) for c in coords], db)
    return [[round(lat, 5), round(lon, 5)] for lat, lon in sliced]


def line_timetable(num, day, mode=None):
    """Rozkład jednej linii na dany dzień.

    Kursy grupujemy po CIĄGU PRZYSTANKÓW, nie po samym kierunku: linia ma
    zwykle dwa kierunki, ale prawie zawsze też kursy skrócone (do zajezdni,
    do pętli w połowie trasy). Zlanie ich w jedno dałoby tablicę godzin,
    pod którą podpisany jest przystanek, przez który połowa tych kursów
    nie przejeżdża.
    """
    num = " ".join((num or "").split())
    if not num:
        return {"error": "Podaj numer linii."}

    db = gtfs.open_db()
    gtfs.geo_generation()      # unieważnia cache geometrii po podmianie bazy
    try:
        routes = db.execute(
            "SELECT route_id, route_short_name, route_long_name, route_type FROM routes"
        ).fetchall()

        wanted = {}
        available = set()
        for route_id, short_name, long_name, route_type in routes:
            route_num = _num_of(short_name, long_name)
            route_mode = _mode_of(route_type)
            available.add((route_num, route_mode))
            if route_num.casefold() != num.casefold():
                continue
            if mode and route_mode != mode:
                continue
            wanted[route_id] = route_mode

        if not wanted:
            hints = sorted(
                {n for n, _ in available if n.casefold().startswith(num.casefold())},
                key=lambda n: _line_sort_key(n, "bus"),
            )[:8]
            return {"error": f"Nie ma linii „{num}” w rozkładzie.",
                    "suggestions": hints}

        active = gtfs.active_service_ids(db, day)
        trips = [
            (trip_id, headsign or "", shape_id, wanted[route_id])
            for trip_id, route_id, service_id, headsign, shape_id in db.execute(
                "SELECT trip_id, route_id, service_id, trip_headsign, shape_id FROM trips"
            )
            if route_id in wanted and service_id in active
        ]
        line_mode = trips[0][3] if trips else next(iter(wanted.values()))
        if not trips:
            return {
                "num": num, "mode": line_mode,
                "label": f"{MODE_LABEL[line_mode]} {num}",
                "date": day.isoformat(), "variants": [],
                "note": "Tego dnia ta linia nie kursuje.",
            }

        times = _stop_times_of(db, (t[0] for t in trips))
        geo = _stops_geo(db)

        variants = {}
        for trip_id, headsign, shape_id, _ in trips:
            sequence = times.get(trip_id)
            if not sequence or len(sequence) < 2:
                continue
            key = tuple(stop_id for stop_id, _, _ in sequence)
            variant = variants.setdefault(key, {
                "headsign": headsign, "shape": shape_id, "trips": [],
            })
            variant["trips"].append((sequence[0][2], trip_id, sequence))

        out = []
        for key, variant in variants.items():
            variant["trips"].sort()
            stops = [
                {"name": geo[stop_id][0], "lat": round(geo[stop_id][1], 5),
                 "lon": round(geo[stop_id][2], 5)}
                for stop_id in key
            ]
            out.append({
                "headsign": variant["headsign"] or stops[-1]["name"],
                "from": stops[0]["name"],
                "to": stops[-1]["name"],
                "stops": stops,
                "path": _path_of(variant["shape"],
                                 [(geo[s][1], geo[s][2]) for s in key], db),
                "trips": [
                    {
                        "id": trip_id,
                        "dep": _hhmm(dep_sec),
                        "sec": dep_sec,
                        # Jedna godzina na przystanek: odjazd, a na ostatnim
                        # (skąd już nic nie odjeżdża) przyjazd.
                        "times": [
                            _hhmm(departure_sec if i < len(sequence) - 1 else arrival_sec)
                            for i, (_, arrival_sec, departure_sec) in enumerate(sequence)
                        ],
                    }
                    for dep_sec, trip_id, sequence in variant["trips"]
                ],
            })

        # Najpierw warianty pełne (najwięcej kursów) - to one są "tą linią",
        # a kursy skrócone dopisują się pod nimi.
        out.sort(key=lambda v: (-len(v["trips"]), v["headsign"]))
        return {
            "num": num, "mode": line_mode,
            "label": f"{MODE_LABEL[line_mode]} {num}",
            "date": day.isoformat(), "variants": out,
        }
    finally:
        db.close()


def _trip_rows(db, trip):
    """Cały kurs z bazy: [(stop_id, przyjazd, odjazd), ...] po kolei.

    Czasy trzeba przesunąć tak samo jak przy budowie tablicy dnia -
    egzemplarz kursu z doby poprzedniej jeździ na osi przesuniętej o -24 h
    (patrz gtfs.db_trip).
    """
    raw_trip, shift = gtfs.db_trip(trip)
    return [
        (stop_id, arrival_sec - shift, departure_sec - shift)
        for stop_id, arrival_sec, departure_sec in db.execute(
            "SELECT stop_id, arrival_sec, departure_sec FROM stop_times "
            "WHERE trip_id = ? ORDER BY stop_sequence",
            (raw_trip,),
        )
    ]


def _tail_from(db, data, trip, rows, board_stop, board_dep):
    """Przebieg kursu OD wskazanego słupka do końca i jego geometria. To jest
    odpowiedź na "dokąd stąd pojedzie", więc odcinek przed wsiadaniem nas nie
    interesuje. Nierozpoznany słupek = cały kurs."""
    start = 0
    for i, (stop_id, _, departure_sec) in enumerate(rows):
        if stop_id == board_stop and departure_sec == board_dep:
            start = i
            break
    tail = rows[start:]
    coords = [data.stop_coords[s] for s, _, _ in tail if s in data.stop_coords]
    return tail, _path_of(data.trip_shape.get(trip), coords, db)


def stop_board(query, day):
    """Tablica odjazdów z przystanku: wszystkie odjazdy doby, opisane linią
    (numer + kierunek), do której należą.

    Bez geometrii - i to jest świadome. Węzeł taki jak Galeria Dominikańska
    ma ponad sto par (linia, kierunek); doklejenie do odpowiedzi przebiegu
    każdej z nich to pół megabajta trasy, z której widać naraz i tak parę.
    Trasy dociąga front, przez /api/trip, dla linii faktycznie zaznaczonych
    w tablicy - a że każdy odjazd niesie swój trip_id, nie trzeba do tego
    żadnego dodatkowego wejścia.

    Filtrowanie i łączenie linii zostaje po stronie frontu: jedno zapytanie
    niesie komplet odjazdów, więc odhaczanie kolejnych autobusów w tej samej
    tablicy dzieje się natychmiast, bez chodzenia po sieć.
    """
    try:
        data = gtfs.load_day(day)
    except FileNotFoundError as e:
        return {"error": str(e)}

    name, stop_ids, hints = gtfs.match_stop(query, data)
    if name is None:
        return {
            "error": f"Nie znam przystanku „{query}”." if query
                     else "Podaj nazwę przystanku.",
            "suggestions": hints or [],
        }

    stop_set = set(stop_ids)
    # Odjazd z przystanku to połączenie, które się w nim ZACZYNA. Ostatni
    # przystanek kursu żadnego takiego nie ma - i słusznie, nie da się tam
    # wsiąść. Skan po całej tablicy dnia zamiast zapytania do SQLite: baza
    # nie ma indeksu po stop_id, a tablica dnia i tak jest w pamięci.
    found = []
    for departure_sec, _, from_stop, _, trip in data.conns:
        if from_stop in stop_set:
            found.append((departure_sec, trip, from_stop))
    found.sort()

    lines = {}          # (rodzaj, numer, kierunek) -> licznik kursów
    rows = []
    for departure_sec, trip, from_stop in found:
        label, headsign = data.trip_info.get(trip, ("Linia ?", ""))
        kind, _, line_num = label.partition(" ")
        mode = MODE_OF_LABEL.get(kind, "other")
        key = (mode, line_num or label, headsign)
        lines[key] = lines.get(key, 0) + 1
        rows.append((key, departure_sec, trip, from_stop))

    order = sorted(lines, key=lambda k: (*_line_sort_key(k[1], k[0]), k[2]))
    index_of = {key: i for i, key in enumerate(order)}
    platform_of = {s: data.stop_names[s] for s in stop_ids}
    multi_platform = len(set(platform_of.values())) > 1

    departures = [
        {
            "line": index_of[key],
            "t": _hhmm(departure_sec),
            "sec": departure_sec,
            "trip": trip,
            "stop": from_stop,
            # Nazwa słupka ma sens tylko tam, gdzie miejsce ma ich kilka
            # (perony kierunkowe dużego węzła) - inaczej powtarzałaby nazwę
            # przystanku w każdym wierszu tablicy.
            **({"platform": platform_of[from_stop]} if multi_platform else {}),
        }
        for key, departure_sec, trip, from_stop in rows
    ]

    center = [
        round(sum(data.stop_coords[s][0] for s in stop_ids) / len(stop_ids), 5),
        round(sum(data.stop_coords[s][1] for s in stop_ids) / len(stop_ids), 5),
    ]
    return {
        "stop": name,
        "date": day.isoformat(),
        "center": center,
        "platforms": sorted(set(platform_of.values())),
        "lines": [
            {"mode": mode, "num": line_num, "headsign": headsign,
             "count": lines[(mode, line_num, headsign)]}
            for mode, line_num, headsign in order
        ],
        "departures": departures,
    }


def trip_detail(trip, day, board_stop=None, board_dep=None):
    """Jeden konkretny kurs: przystanki z godzinami i geometria całej trasy.

    `board_stop`/`board_dep` (skąd klikaliśmy w tablicy odjazdów) wyznaczają
    dodatkowo odcinek "stąd dalej" - front rysuje go jaskrawo, a to, co kurs
    ma już za sobą, blado.
    """
    try:
        data = gtfs.load_day(day)
    except FileNotFoundError as e:
        return {"error": str(e)}

    if trip not in data.trip_info:
        return {"error": "Nie ma takiego kursu w rozkładzie na ten dzień."}

    label, headsign = data.trip_info[trip]
    kind, _, line_num = label.partition(" ")
    mode = MODE_OF_LABEL.get(kind, "other")

    db = gtfs.open_db()
    gtfs.geo_generation()
    try:
        rows = _trip_rows(db, trip)
        coords = [data.stop_coords[s] for s, _, _ in rows if s in data.stop_coords]
        path = _path_of(data.trip_shape.get(trip), coords, db)

        board_index = 0
        if board_stop is not None:
            for i, (stop_id, _, departure_sec) in enumerate(rows):
                if stop_id == board_stop and (board_dep is None
                                              or departure_sec == board_dep):
                    board_index = i
                    break
        tail_path = path
        if board_index:
            _, tail_path = _tail_from(db, data, trip, rows,
                                      rows[board_index][0], rows[board_index][2])
    finally:
        db.close()

    return {
        "trip": trip,
        "num": line_num or label,
        "mode": mode,
        "line": label,
        "headsign": headsign,
        "board_index": board_index,
        "stops": [
            {
                "name": data.stop_names.get(stop_id, ""),
                "lat": round(data.stop_coords[stop_id][0], 5),
                "lon": round(data.stop_coords[stop_id][1], 5),
                "t": _hhmm(departure_sec if i < len(rows) - 1 else arrival_sec),
                "sec": departure_sec if i < len(rows) - 1 else arrival_sec,
            }
            for i, (stop_id, arrival_sec, departure_sec) in enumerate(rows)
            if stop_id in data.stop_coords
        ],
        "path": path,
        "tail": tail_path,
    }
