"""Dostęp do danych GTFS: aktywne kursy dnia, tablica połączeń, przystanki.

Rozkład dla danego dnia jest wczytywany z SQLite raz i trzymany w pamięci
(_day_cache). Klucz cache zawiera mtime pliku bazy, więc po nocnej podmianie
przez update_gtfs.py dane przeładują się same przy pierwszym zapytaniu.
"""

import math
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "gtfs.sqlite"

WEEKDAY_COLUMNS = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]

ROUTE_TYPE_LABELS = {0: "Tramwaj", 3: "Autobus"}

_day_cache = {}


class DayData:
    """Rozkład jednego dnia przygotowany pod algorytm wyszukiwania."""

    __slots__ = (
        "conns", "dep_times", "stop_names", "stop_coords", "stops_by_key",
        "display_name", "siblings", "trip_info", "trip_shape",
    )

    def __init__(self):
        # Połączenie = przejazd między dwoma kolejnymi przystankami jednego kursu:
        # (odjazd_sek, przyjazd_sek, przystanek_z, przystanek_do, trip_id),
        # posortowane po czasie odjazdu - tego wymaga Connection Scan.
        self.conns = []
        self.dep_times = []          # równoległa lista odjazdów do bisect
        self.stop_names = {}         # stop_id -> nazwa
        self.stop_coords = {}        # stop_id -> (lat, lon)
        self.stops_by_key = {}       # nazwa.casefold() -> [stop_id, ...]
        self.display_name = {}       # nazwa.casefold() -> oryginalna pisownia
        self.siblings = {}           # stop_id -> inne słupki o tej samej nazwie
        self.trip_info = {}          # trip_id -> (etykieta linii, kierunek)
        self.trip_shape = {}         # trip_id -> shape_id (geometria z shapes.txt)


def _connect():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            "Brak bazy rozkładów (data/gtfs.sqlite). Uruchom: python3 update_gtfs.py"
        )
    return sqlite3.connect(DB_PATH)


def active_service_ids(db, day):
    """service_id kursujące danego dnia wg calendar.txt + wyjątki z calendar_dates.txt."""
    date_str = day.strftime("%Y%m%d")
    weekday = WEEKDAY_COLUMNS[day.weekday()]

    active = {
        row[0]
        for row in db.execute(
            f"SELECT service_id FROM calendar "
            f"WHERE {weekday} = 1 AND start_date <= ? AND end_date >= ?",
            (date_str, date_str),
        )
    }
    for service_id, exception_type in db.execute(
        "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?",
        (date_str,),
    ):
        if exception_type == 1:
            active.add(service_id)
        else:
            active.discard(service_id)
    return active


def load_day(day):
    """Zwraca DayData dla podanej daty (datetime.date), z cache."""
    key = (day.isoformat(), DB_PATH.stat().st_mtime if DB_PATH.exists() else 0)
    if key in _day_cache:
        return _day_cache[key]

    db = _connect()
    data = DayData()

    active = active_service_ids(db, day)

    route_names = {}   # route_id -> etykieta, np. "Tramwaj 5"
    for route_id, short_name, long_name, route_type in db.execute(
        "SELECT route_id, route_short_name, route_long_name, route_type FROM routes"
    ):
        kind = ROUTE_TYPE_LABELS.get(route_type, "Linia")
        route_names[route_id] = f"{kind} {short_name or long_name}".strip()

    active_trips = set()
    for trip_id, route_id, service_id, headsign, shape_id in db.execute(
        "SELECT trip_id, route_id, service_id, trip_headsign, shape_id FROM trips"
    ):
        if service_id in active:
            trip_id = sys.intern(trip_id)
            active_trips.add(trip_id)
            data.trip_info[trip_id] = (route_names.get(route_id, "Linia ?"), headsign or "")
            if shape_id:
                data.trip_shape[trip_id] = sys.intern(shape_id)

    for stop_id, stop_name, lat, lon in db.execute(
        "SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops"
    ):
        stop_id = sys.intern(stop_id)
        data.stop_names[stop_id] = stop_name
        data.stop_coords[stop_id] = (lat, lon)
        name_key = stop_name.casefold()
        data.stops_by_key.setdefault(name_key, []).append(stop_id)
        data.display_name.setdefault(name_key, stop_name)

    # Słupki o tej samej nazwie traktujemy jako jeden węzeł przesiadkowy
    # połączony krótkim przejściem pieszym (patrz WALK_SEC w planner.py).
    for group in data.stops_by_key.values():
        if len(group) > 1:
            for stop_id in group:
                data.siblings[stop_id] = tuple(s for s in group if s != stop_id)

    # stop_times czytamy w kolejności (trip_id, stop_sequence) - to indeks,
    # więc bez sortowania - i sklejamy sąsiednie przystanki kursu w połączenia.
    prev_trip = None
    prev_stop = None
    prev_dep = 0
    conns = data.conns
    for trip_id, stop_id, arrival_sec, departure_sec in db.execute(
        "SELECT trip_id, stop_id, arrival_sec, departure_sec "
        "FROM stop_times ORDER BY trip_id, stop_sequence"
    ):
        if trip_id not in active_trips:
            prev_trip = None
            continue
        trip_id = sys.intern(trip_id)
        stop_id = sys.intern(stop_id)
        if trip_id == prev_trip:
            conns.append((prev_dep, arrival_sec, prev_stop, stop_id, trip_id))
        prev_trip, prev_stop, prev_dep = trip_id, stop_id, departure_sec
    db.close()

    conns.sort(key=lambda c: c[0])
    data.dep_times = [c[0] for c in conns]

    _day_cache[key] = data
    if len(_day_cache) > 2:                      # trzymamy najwyżej 2 dni w RAM
        _day_cache.pop(next(iter(_day_cache)))
    return data


def match_stop(query, data):
    """Dopasowuje wpisaną nazwę do przystanku.

    Zwraca (nazwa, [stop_id, ...], None) przy trafieniu
    albo (None, None, [podpowiedzi]) gdy nazwa jest nieznana/niejednoznaczna.
    """
    key = " ".join(query.split()).casefold()
    if not key:
        return None, None, []
    if key in data.stops_by_key:
        return data.display_name[key], data.stops_by_key[key], None

    candidates = [k for k in data.stops_by_key if key in k]
    if len(candidates) == 1:
        k = candidates[0]
        return data.display_name[k], data.stops_by_key[k], None
    return None, None, sorted(data.display_name[k] for k in candidates)[:8]


def all_stop_names():
    """Posortowane nazwy przystanków do podpowiadania w formularzu."""
    db = _connect()
    names = [row[0] for row in db.execute(
        "SELECT DISTINCT stop_name FROM stops ORDER BY stop_name"
    )]
    db.close()
    return names


def all_stops_geo():
    """Wszystkie słupki z współrzędnymi - do narysowania na mapie."""
    db = _connect()
    stops = [
        {"name": name, "lat": lat, "lon": lon}
        for name, lat, lon in db.execute(
            "SELECT stop_name, stop_lat, stop_lon FROM stops"
        )
    ]
    db.close()
    return stops


_shape_cache = {}       # shape_id -> [(lat, lon), ...]
_slice_cache = {}       # (shape_id, przystanki) -> gotowa ścieżka
_geo_generation = None  # mtime bazy, dla którego ważne są powyższe cache

_SIMPLIFY_DEG = 0.0001   # ~11 m - upraszczanie łamanych
_SNAP_DEG = 0.0025       # ~280 m - maks. wiarygodna odległość przystanku od shape'a
_SLICE_CACHE_MAX = 1500


def geo_generation():
    """Odświeża znacznik ważności cache geometrii (wołać raz na zapytanie).

    Po nocnej podmianie bazy mtime się zmienia i oba cache są czyszczone
    w całości - to jedyny moment, w którym stare wpisy stają się nieważne.
    """
    global _geo_generation
    mtime = DB_PATH.stat().st_mtime if DB_PATH.exists() else 0
    if mtime != _geo_generation:
        _shape_cache.clear()
        _slice_cache.clear()
        _geo_generation = mtime
    return mtime


def clear_caches():
    """Zrzuca z pamięci rozkłady dni i geometrię - następne zapytanie wczyta je z bazy.

    Normalnie niepotrzebne (oba cache same wietrzą się po mtime bazy), ale
    po ręcznej aktualizacji z menu deweloperskiego zwalnia RAM od razu.
    """
    global _geo_generation
    _day_cache.clear()
    _shape_cache.clear()
    _slice_cache.clear()
    _geo_generation = None


def open_db():
    """Połączenie dla wywołującego, np. na czas jednego zapytania o przepływy."""
    return _connect()


def _shape_points(shape_id, db):
    points = _shape_cache.get(shape_id)
    if points is None:
        points = [
            (lat, lon)
            for lat, lon in db.execute(
                "SELECT lat, lon FROM shapes WHERE shape_id = ? ORDER BY seq",
                (shape_id,),
            )
        ]
        _shape_cache[shape_id] = points
    return points


def shape_slice(shape_id, stop_coords, db):
    """Fragment geometrii kursu między pierwszym a ostatnim przystankiem.

    Zakłada, że wywołujący odświeżył cache przez geo_generation().
    Fallback (brak/niewiarygodna geometria): łamana po przystankach.
    """
    if not shape_id or len(stop_coords) < 2:
        return stop_coords
    cache_key = (shape_id, tuple(stop_coords))
    cached = _slice_cache.get(cache_key)
    if cached is not None:
        return cached

    points = _shape_points(shape_id, db)
    result = _compute_slice(points, stop_coords) if len(points) >= 2 else stop_coords

    if len(_slice_cache) >= _SLICE_CACHE_MAX:
        # Zamiast kasować wszystko, upuść ~10% najstarszych wpisów.
        for key in list(_slice_cache)[:_SLICE_CACHE_MAX // 10]:
            del _slice_cache[key]
    _slice_cache[cache_key] = result
    return result


def _compute_slice(points, stop_coords):
    # Lokalna metryka: 1 stopień długości ~ cos(szerokości) stopnia szerokości.
    cos_lat = math.cos(math.radians(stop_coords[0][0]))

    def dist2(a, b):
        d_lat = a[0] - b[0]
        d_lon = (a[1] - b[1]) * cos_lat
        return d_lat * d_lat + d_lon * d_lon

    snap2 = _SNAP_DEG * _SNAP_DEG

    # Monotoniczne dopasowanie: każdy następny przystanek szukany od pozycji
    # poprzedniego, więc pętle i nawroty trasy nie mylą kierunku. Skan urywa
    # się LOOKAHEAD punktów za ostatnim minimum; jeśli tak znalezione minimum
    # jest podejrzanie daleko (fałszywe minimum przy wsiadaniu w środku
    # kursu), doskanowujemy cały pozostały zakres.
    LOOKAHEAD = 50
    marks = []
    position = 0
    for stop in stop_coords:
        best_i = position
        best_d = dist2(points[position], stop)
        for i in range(position + 1, len(points)):
            d = dist2(points[i], stop)
            if d < best_d:
                best_d, best_i = d, i
            elif i - best_i > LOOKAHEAD:
                break
        if best_d > snap2:
            for i in range(position + 1, len(points)):
                d = dist2(points[i], stop)
                if d < best_d:
                    best_d, best_i = d, i
        marks.append((best_i, best_d))
        position = best_i

    first, last = marks[0][0], marks[-1][0]
    if last - first < 1:
        return stop_coords
    # Walidacja: końce wycinka muszą leżeć przy przystankach, a długość
    # wycinka być w rozsądnym stosunku do łamanej po przystankach - inaczej
    # dopasowanie się rozjechało i uczciwiej pokazać łamaną.
    if marks[0][1] > snap2 or marks[-1][1] > snap2:
        return stop_coords
    sliced = points[first:last + 1]
    stops_len = _polyline_len(stop_coords, cos_lat)
    slice_len = _polyline_len(sliced, cos_lat)
    if stops_len > 0 and not (0.85 <= slice_len / stops_len <= 3.0):
        return stop_coords
    return _simplify(sliced)


def _polyline_len(points, cos_lat):
    total = 0.0
    for a, b in zip(points, points[1:]):
        d_lat = a[0] - b[0]
        d_lon = (a[1] - b[1]) * cos_lat
        total += math.sqrt(d_lat * d_lat + d_lon * d_lon)
    return total


def _simplify(points):
    """Usuwa punkty bliższe niż ~11 m od ostatnio zachowanego (mniej JSON-a)."""
    threshold2 = _SIMPLIFY_DEG * _SIMPLIFY_DEG
    kept = [points[0]]
    for point in points[1:-1]:
        d_lat = point[0] - kept[-1][0]
        d_lon = point[1] - kept[-1][1]
        if d_lat * d_lat + d_lon * d_lon >= threshold2:
            kept.append(point)
    kept.append(points[-1])
    return kept


def trip_path(trip_id, board_stop, board_dep, exit_stop, exit_arr):
    """Kolejne przystanki kursu od wsiadania do wysiadania (stop_id, przyjazd, odjazd)."""
    db = _connect()
    rows = db.execute(
        "SELECT stop_id, arrival_sec, departure_sec FROM stop_times "
        "WHERE trip_id = ? ORDER BY stop_sequence",
        (trip_id,),
    ).fetchall()
    db.close()

    start_i = None
    for i, (stop_id, arrival_sec, departure_sec) in enumerate(rows):
        if start_i is None:
            if stop_id == board_stop and departure_sec == board_dep:
                start_i = i
        elif stop_id == exit_stop and arrival_sec == exit_arr:
            return rows[start_i:i + 1]
    return []
