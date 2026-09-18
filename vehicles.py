"""Pozycje pojazdów MPK z https://mpk.wroc.pl/bus_position - zasila warstwę
„autobusy i tramwaje" na mapie (patrz routes.api_vehicles i static/app.js).

Ten endpoint nie ma trybu "zwróć wszystkie pozycje" - trzeba mu POST-em
podać listę numerów linii, które go interesują (tak robi też oficjalna
mapa na mpk.wroc.pl, patrz jej static/js/buspositions.js), więc dociągamy
z GTFS-u komplet route_short_name i pytamy o wszystkie naraz (na dziś to
~135 linii -> ~325 pojazdów, odpowiedź przychodzi w ułamku sekundy).
W przeciwieństwie do poprzedniego źródła (open-data) odpowiedź od razu
niesie rodzaj pojazdu ("tram"/"bus"), więc nie trzeba tego dopasowywać
osobno przez gtfs.line_kind_map() - używamy go tu tylko po to, by wiedzieć,
o jakie numery linii pytać.

W przeciwieństwie do update_gtfs.py to źródło NIE trafia do bazy: pozycje
zmieniają się co kilkanaście sekund, więc trzyma się je wyłącznie w pamięci
procesu, z krótkim cache (CACHE_SEC) - inaczej każde odświeżenie warstwy w
przeglądarce (front odpytuje się cyklicznie, patrz VEHICLES_REFRESH_MS w
app.js) biłoby wprost w cudzy serwer.
"""

import json
import math
import threading
import time
import urllib.parse
import urllib.request

import gtfs

VEHICLES_URL = "https://mpk.wroc.pl/bus_position"

CACHE_SEC = 10

# Wrocław i najbliższa aglomeracja, z zapasem - dalej MPK nie jeździ. Poza
# tym zakresem w danych API trafiają się wyłącznie błędne/sentinelowe
# współrzędne (np. (0, 0) albo losowy szum rzędu dziesiątek/tysięcy stopni -
# zmierzone na żywych danych).
LAT_RANGE = (50.6, 51.6)
LON_RANGE = (16.4, 17.8)

_lock = threading.Lock()
_cache = {"at": 0.0, "vehicles": None}


def _query_fields():
    """Pary (busList[tram][]/busList[bus][], numer_linii) do POST-a - patrz
    docstring modułu. Rodzaj z GTFS trafia tylko do koszyka zapytania;
    faktyczny rodzaj pojazdu i tak bierzemy z odpowiedzi w _parse."""
    fields = []
    for name, kind in gtfs.line_kind_map().items():
        if kind not in ("tram", "bus"):
            continue
        fields.append((f"busList[{kind}][]", name))
    return fields


def _fetch_raw():
    body = urllib.parse.urlencode(_query_fields()).encode()
    request = urllib.request.Request(
        VEHICLES_URL, data=body, headers={"User-Agent": "Metal-Planner/0.1"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def _clean(value):
    if isinstance(value, str):
        value = value.strip()
    return value if value not in (None, "") else None


def _parse(raw_results):
    parsed = []
    for item in raw_results:
        kind = item.get("type")
        if kind not in ("tram", "bus"):
            continue
        line = _clean(item.get("name"))
        if not line:
            continue
        # API zwraca lat jako "x", lon jako "y" (zmierzone na żywych danych -
        # potwierdza to też static/js/buspositions.js na mpk.wroc.pl, które
        # tak samo buduje z nich google.maps.LatLng(x, y)).
        lat, lon = item.get("x"), item.get("y")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
            continue
        parsed.append({"line": line, "kind": kind, "lat": lat, "lon": lon})
    return parsed


def get_vehicles():
    """Aktualne pozycje pojazdów, z krótkim cache (CACHE_SEC) - patrz docstring
    modułu. Rzuca wyjątkiem sieciowym (OSError, w tym urllib.error.URLError
    i timeout) albo gtfs.FileNotFoundError (brak bazy rozkładów) - kto woła,
    łapie i zamienia na odpowiedź błędu (patrz routes.api_vehicles)."""
    with _lock:
        if _cache["vehicles"] is not None and time.monotonic() - _cache["at"] < CACHE_SEC:
            return _cache["vehicles"]
        vehicles = _parse(_fetch_raw())
        _cache["vehicles"] = vehicles
        _cache["at"] = time.monotonic()
        return vehicles


# ------------------------------------------- którym kursem to właściwie jedzie ---
#
# ODSTAWIONE 2026-09-13, na wyraźną prośbę: warstwa pokazuje wszystkie pojazdy
# linii, które są na mapie, i nie odsiewa pojedynczych kursów. Kod zostaje tu
# zakomentowany, bo działał i był zmierzony - na relacji Księże Małe -
# pl. Grunwaldzki z 49 pojazdów linii z mapy zostawało 12, odpadało 28
# (kurs w drugą stronę albo poza oknem mapy), a 9 nie miało rozpoznanego kursu
# i zostawało pokazanych. Dopasowanie: dla każdego dzisiejszego kursu linii
# liczone jest, gdzie powinien być w tej sekundzie (po przebiegu trasy, nie po
# prostej), kursy rozdawane są pojazdom jeden do jednego, a przy pojeździe
# wraca dalsza część rozkładu jego kursu. Testy tego kodu czekają w
# tests/test_pojazd_i_kurs.py (pominięte razem z nim).
#
# Żeby to wróciło: zdjąć z każdej linijki poniżej dokładnie jeden przedrostek
# „# ", w routes.api_vehicles przepuścić parametr `lines` przez with_courses,
# a we froncie odsiewać po `stops` pojazdu (patrz komentarz nad sekcją pojazdów
# w static/app.js).

# #
# # Feed mówi tylko „linia 146 jest tutaj". To za mało, żeby odpowiedzieć na
# # pytanie, które naprawdę się zadaje - „czy tym pojazdem dojadę tam, dokąd
# # jadę" - bo nie wiadomo nawet, w którą stronę on jedzie. Dokładamy więc to,
# # czego brakuje: KURS z rozkładu, którym ten pojazd najpewniej jedzie, a wraz
# # z nim jego dalsze przystanki z godzinami.
# #
# # Dopasowanie po położeniu: dla każdego dzisiejszego kursu tej linii liczymy,
# # gdzie POWINIEN teraz być (interpolacja po prawdziwym przebiegu trasy między
# # dwoma przystankami, nie po prostej), i bierzemy ten najbliższy. Kursy
# # rozdzielane są jeden do jednego - dwa pojazdy tej samej linii nie jadą tym
# # samym kursem, a bez tego oba przykleiłyby się do jednego.
# #
# # Gdy najbliższy kurs jest dalej niż MATCH_MAX_M, pojazd zostaje BEZ rozkładu:
# # „nie wiem, którym kursem jedzie" to uczciwa odpowiedź, a front pokazuje
# # wtedy taki pojazd zamiast go ukrywać (patrz vehicleFits w app.js).
#
# MATCH_MAX_M = 1200
# # Ile rozkładu dopisujemy przy pojeździe: tyle, ile potrafi objąć okno mapy.
# COURSE_AHEAD_SEC = 2 * 3600
#
# _trips_cache = {}       # (numer, rodzaj, data, mtime bazy) -> [kurs, ...]
#
#
# def _trip_rows(num, kind, day, db):
#     """Dzisiejsze kursy jednej linii: [{shape_id, stops: [(lat, lon, sec), ...]}].
#
#     Liczone raz na dzień i trzymane w pamięci: rozkład się w ciągu dnia nie
#     zmienia, a to samo pytanie wraca co kilkanaście sekund razem z warstwą.
#     """
#     key = (num, kind, day, gtfs.geo_generation())
#     cached = _trips_cache.get(key)
#     if cached is not None:
#         return cached
#
#     services = gtfs.active_service_ids(db, day)
#     if not services:
#         return []
#     holes = ",".join("?" * len(services))
#     rows = db.execute(
#         f"""SELECT t.trip_id, t.shape_id, s.stop_lat, s.stop_lon, st.departure_sec
#             FROM trips t
#             JOIN routes r ON r.route_id = t.route_id
#             JOIN stop_times st ON st.trip_id = t.trip_id
#             JOIN stops s ON s.stop_id = st.stop_id
#             WHERE TRIM(r.route_short_name) = ? AND r.route_type = ?
#               AND t.service_id IN ({holes})
#             ORDER BY t.trip_id, st.stop_sequence""",
#         (num, _ROUTE_TYPE[kind], *services),
#     ).fetchall()
#
#     trips = {}
#     for trip_id, shape_id, lat, lon, sec in rows:
#         trips.setdefault(trip_id, {"shape_id": shape_id, "stops": []})
#         trips[trip_id]["stops"].append((lat, lon, sec))
#     out = [t for t in trips.values() if len(t["stops"]) >= 2]
#     # Ze starego dnia (i sprzed nocnej podmiany bazy) nic już nie jest ważne,
#     # ale rozkłady POZOSTAŁYCH linii tego samego dnia zostają: mapa pyta
#     # o kilka linii naraz, co kilkanaście sekund.
#     for old in [k for k in _trips_cache if k[2:] != key[2:]]:
#         del _trips_cache[old]
#     _trips_cache[key] = out
#     return out
#
#
# _ROUTE_TYPE = {"tram": 0, "bus": 3}    # odwrotność gtfs.ROUTE_KIND_BY_TYPE
#
#
# def _metres(a, b):
#     """Odległość w linii prostej - do porównywania kandydatów, nie do rysowania."""
#     lat = math.radians((a[0] + b[0]) / 2)
#     return math.hypot((b[0] - a[0]) * 111320,
#                       (b[1] - a[1]) * 111320 * math.cos(lat))
#
#
# def _position_at(trip, sec, db):
#     """Gdzie ten kurs powinien być o tej sekundzie - PO TRASIE, nie po prostej.
#
#     Prosta między przystankami potrafi uciąć zakręt o kilkaset metrów, a to
#     dokładnie ten rząd wielkości, który decyduje o wyborze kursu.
#     """
#     stops = trip["stops"]
#     if sec < stops[0][2] or sec > stops[-1][2]:
#         return None
#     for before, after in zip(stops, stops[1:]):
#         if not before[2] <= sec <= after[2]:
#             continue
#         span = after[2] - before[2]
#         if span <= 0:
#             return (before[0], before[1])
#         path = gtfs.shape_slice(trip["shape_id"],
#                                 [(before[0], before[1]), (after[0], after[1])], db)
#         return _along(path, (sec - before[2]) / span)
#     return None
#
#
# def _along(path, fraction):
#     """Punkt na łamanej w danym ułamku jej DŁUGOŚCI."""
#     spans = [_metres(a, b) for a, b in zip(path, path[1:])]
#     total = sum(spans)
#     if total <= 0:
#         return (path[0][0], path[0][1])
#     left = total * fraction
#     for (a, b), span in zip(zip(path, path[1:]), spans):
#         if left <= span:
#             f = left / span if span else 0
#             return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
#         left -= span
#     return (path[-1][0], path[-1][1])
#
#
# def with_courses(vehicles, lines, when):
#     """Dopisuje przy pojazdach wskazanych linii `stops` - dalsze przystanki
#     kursu, którym jadą, każdy jako [lat, lon, sekunda].
#
#     `lines` to klucze "rodzaj numer" (np. {"bus 146"}); reszta pojazdów wraca
#     nietknięta, bo rozkład przy nich nie jest do niczego potrzebny, a
#     rozwijanie wszystkich linii miasta kosztowałoby przy każdym odświeżeniu.
#     """
#     if not lines:
#         return vehicles
#     db = gtfs.open_db()
#     gtfs.geo_generation()
#     try:
#         sec = when.hour * 3600 + when.minute * 60 + when.second
#         for key in lines:
#             kind, _, num = key.partition(" ")
#             if kind not in _ROUTE_TYPE or not num:
#                 continue
#             mine = [v for v in vehicles
#                     if v["kind"] == kind and v["line"].strip() == num]
#             if not mine:
#                 continue
#             trips = _trip_rows(num, kind, when.date(), db)
#             spots = [(trip, _position_at(trip, sec, db)) for trip in trips]
#             _assign(mine, [(t, p) for t, p in spots if p], sec)
#     finally:
#         db.close()
#     return vehicles
#
#
# def _assign(vehicles, running, sec):
#     """Kursy rozdane jeden do jednego, od najpewniejszej pary: pojazd bierze
#     ten kurs, do którego jest bliżej niż ktokolwiek inny."""
#     pairs = sorted(
#         (_metres((v["lat"], v["lon"]), spot), i, j)
#         for i, v in enumerate(vehicles)
#         for j, (_, spot) in enumerate(running)
#     )
#     taken_v, taken_t = set(), set()
#     for distance, i, j in pairs:
#         if distance > MATCH_MAX_M or i in taken_v or j in taken_t:
#             continue
#         taken_v.add(i)
#         taken_t.add(j)
#         vehicles[i]["stops"] = [
#             [round(lat, 5), round(lon, 5), stop_sec]
#             for lat, lon, stop_sec in running[j][0]["stops"]
#             if sec - 60 <= stop_sec <= sec + COURSE_AHEAD_SEC
#         ]
