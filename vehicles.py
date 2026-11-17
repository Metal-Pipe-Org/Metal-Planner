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
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gtfs
import tls

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
    with urllib.request.urlopen(request, timeout=15, context=tls.SSL_CONTEXT) as response:
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
        # Kanał oddaje linie literowe małymi literami ("d", "k"), a rozkład
        # ma je wielkimi - bez tego nie zgadzają się z kursem ani z mapą.
        line = line.upper()
        # API zwraca lat jako "x", lon jako "y" (zmierzone na żywych danych -
        # potwierdza to też static/js/buspositions.js na mpk.wroc.pl, które
        # tak samo buduje z nich google.maps.LatLng(x, y)).
        lat, lon = item.get("x"), item.get("y")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
            continue
        parsed.append({"line": line, "kind": kind, "lat": lat, "lon": lon,
                       "key": item.get("k")})
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
        _track_motion(vehicles)
        _add_courses(vehicles)
        _add_headings(vehicles)
        for v in vehicles:
            del v["key"]
        _cache["vehicles"] = vehicles
        _cache["at"] = time.monotonic()
        return vehicles


# ------------------------------------------------- w którą stronę jedzie pojazd ---
#
# Szybki kanał nie mówi, dokąd jedzie pojazd. Kierunek składamy z dwóch
# źródeł. (1) Kurs: ośmiocyfrowy klucz "k" przy pojeździe to numer kursu, a
# numer kursu w rozkładzie GTFS (trip_id "3_17599100") różni się od niego o
# stałą, wspólną dla grupy linii (zmierzone na żywych danych: wszystkie
# tramwaje 11826866, linie 122-128 11866456 itd.; stałe zmieniają się z
# kolejnymi wydaniami rozkładu). Stałe uczymy się w locie z pojazdów, których
# kurs jest ponad wszelką wątpliwość (kropka stoi tam, gdzie kurs ma być wg
# rozkładu, a żaden inny kurs tej linii nie jest blisko), a przyjmujemy dopiero
# stałą potwierdzoną przez kilka różnych kursów. Potem pojazd wiążemy z kursem
# po samym kluczu i pokazujemy cel tylko wtedy, gdy kurs istnieje, jest tej
# samej linii, trwa teraz i kropka leży na jego trasie. Strzałka celuje w
# następny przystanek kursu, także gdy pojazd stoi.
# (2) Ruch: bez kursu strzałka pokazuje się tylko, gdy pojazd dwa razy z rzędu
# przesunął się w tę samą stronę. Ruch jest też wetem dla kursu: jeśli
# przesunięcie przeczy rozkładowi, wolimy nie pokazywać nic niż pokazać źle.

LEARN_NEAR_M = 150            # kropka tak blisko pozycji rozkładowej kursu ...
LEARN_RIVAL_M = 600           # ... a każdy inny kurs tej linii tak daleko: uczymy się stałej
OFFSET_MIN_KEYS = 3           # tyle sąsiednich kursów musi potwierdzić stałą ...
OFFSET_SHARE = 0.5            # ... i stanowić tyle wszystkich sąsiednich głosów
KEY_WINDOW = 300              # sąsiad = klucz różniący się o nie więcej niż tyle
OFFSET_TTL_SEC = 6 * 3600
WINDOW_SEC = 5 * 60           # o tyle kurs może się spóźnić z końcem rozkładu
ON_ROUTE_M = 200              # dalej od trasy kursu - nie ufamy takiemu wiązaniu
STEP_M = 30                   # krótsze przesunięcie to szum GPS
VETO_DEG = 90
STRAIGHT_DEG = 30
AT_STOP_M = 25
ROUTE_NEAR_M = 100            # kropka tak blisko odcinka wariantu trasy ...
ROUTE_DEG = 45                # ... a odcinek biegnie tak zgodnie z ruchem: pasuje
TRACK_TTL_SEC = 300

_TRIP_ID = re.compile(r"\d+_\d+")
WARSAW = ZoneInfo("Europe/Warsaw")
_ROUTE_TYPE = {"tram": 0, "bus": 3}

_tracks = {}                  # klucz kursu z szybkiego kanału -> {"pts": [...], "seen": t}
_anchors = {}                 # klucz kursu -> (stała, kiedy widziany)
_anchors_db = [None]          # mtime bazy, z której pochodzą stałe
_window_cache = {}            # (dzień, mtime bazy) -> kursy z oknem czasu
_stops_cache = {}             # trip_id -> przystanki; czyszczony razem z oknami


def _metres(a, b):
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot((b[0] - a[0]) * 111320, (b[1] - a[1]) * 111320 * math.cos(lat))


def _bearing(a, b):
    dy = b[0] - a[0]
    dx = (b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.degrees(math.atan2(dx, dy)) % 360


def _angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def _track_motion(vehicles):
    """Dopisuje przy pojeździe kierunki dwóch ostatnich przesunięć o co najmniej
    STEP_M (najstarsze pierwsze): v["steps"]."""
    now = time.monotonic()
    for k in [k for k, t in _tracks.items() if now - t["seen"] > TRACK_TTL_SEC]:
        del _tracks[k]
    for v in vehicles:
        point = (v["lat"], v["lon"])
        track = _tracks.setdefault(v["key"], {"pts": [point], "seen": now})
        track["seen"] = now
        if _metres(track["pts"][-1], point) >= STEP_M:
            track["pts"] = (track["pts"] + [point])[-3:]
        pts = track["pts"]
        v["steps"] = [_bearing(a, b) for a, b in zip(pts, pts[1:])]


def _day_trips(day, db):
    """Kursy dnia z oknem czasu: liczone raz na dzień."""
    key = (day, gtfs.DB_PATH.stat().st_mtime)
    cached = _window_cache.get(key)
    if cached is not None:
        return cached
    services = gtfs.active_service_ids(db, day)
    holes = ",".join("?" * len(services))
    rows = db.execute(
        f"""SELECT t.trip_id, TRIM(r.route_short_name), r.route_type,
                   t.trip_headsign, MIN(st.departure_sec), MAX(st.arrival_sec)
            FROM trips t
            JOIN routes r ON r.route_id = t.route_id
            JOIN stop_times st ON st.trip_id = t.trip_id
            WHERE t.service_id IN ({holes})
            GROUP BY t.trip_id""",
        tuple(services),
    ).fetchall()
    # Doklejone kursy z innych źródeł (np. "SIE:friday:35169589") nie mają
    # numeru, który dałoby się porównać z kluczem - po kluczu ich nie wiążemy.
    trips = [{"id": i, "num": int(i.split("_")[1]), "route": n, "type": ty,
              "headsign": h, "first": f, "last": la}
             for i, n, ty, h, f, la in rows if _TRIP_ID.fullmatch(i)]
    for old in [k for k in _window_cache if k[1] != key[1] or abs((k[0] - day).days) > 1]:
        del _window_cache[old]
    if not _window_cache:
        _stops_cache.clear()
    _window_cache[key] = trips
    return trips


def _running_trips(now, db):
    """Kursy trwające teraz - także sprzed północy, liczone w sekundach
    rozkładu dnia poprzedniego (> 24:00)."""
    out = []
    for back in (0, 1):
        day = (now - timedelta(days=back)).date()
        sec = now.hour * 3600 + now.minute * 60 + now.second + back * 86400
        for t in _day_trips(day, db):
            if t["first"] - WINDOW_SEC <= sec <= t["last"] + WINDOW_SEC:
                out.append({**t, "sec": sec})
    return out


def _trip_stops(trip, db):
    stops = _stops_cache.get(trip["id"])
    if stops is None:
        stops = _stops_cache[trip["id"]] = db.execute(
            """SELECT s.stop_lat, s.stop_lon, st.departure_sec, s.stop_name
               FROM stop_times st JOIN stops s ON s.stop_id = st.stop_id
               WHERE st.trip_id = ? ORDER BY st.stop_sequence""", (trip["id"],)
        ).fetchall()
    return stops


def _seg_distance(p, a, b):
    """Odległość punktu od odcinka a-b w metrach (płaskie przybliżenie)."""
    k = math.cos(math.radians(p[0]))
    ax, ay = (a[1] - p[1]) * 111320 * k, (a[0] - p[0]) * 111320
    bx, by = (b[1] - p[1]) * 111320 * k, (b[0] - p[0]) * 111320
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    t = 0 if span == 0 else max(0, min(1, -(ax * dx + ay * dy) / span))
    return math.hypot(ax + t * dx, ay + t * dy)


def _locate(trip, point, db):
    """(odległość od trasy kursu, indeks następnego przystanku). Trasa to cały
    kurs; przy kilku równie bliskich odcinkach (pętla, wspólny odcinek tam i z
    powrotem) wygrywa ten bliższy temu, gdzie kurs ma być wg rozkładu."""
    stops = _trip_stops(trip, db)
    ahead = sum(1 for s in stops if s[2] <= trip["sec"])
    near = [(_seg_distance(point, stops[j][:2], stops[j + 1][:2]), j + 1)
            for j in range(len(stops) - 1)]
    if not near:
        return None
    best = min(d for d, _ in near)
    d, nxt = min(((d, j) for d, j in near if d <= best + 30), key=lambda c: abs(c[1] - ahead))
    if nxt + 1 < len(stops) and _metres(point, stops[nxt][:2]) < AT_STOP_M:
        nxt += 1
    return best, nxt


def _scheduled_position(trip, db):
    """Gdzie kurs powinien być teraz wg rozkładu (prosta między przystankami)."""
    stops = _trip_stops(trip, db)
    sec = trip["sec"]
    for before, after in zip(stops, stops[1:]):
        if before[2] <= sec <= after[2]:
            span = after[2] - before[2]
            f = (sec - before[2]) / span if span else 0
            return (before[0] + (after[0] - before[0]) * f,
                    before[1] + (after[1] - before[1]) * f)
    return None


def _learn_offsets(vehicles, trips, db, now):
    """Dopisuje do _anchors stałe pojazdów, których kurs jest oczywisty."""
    stamp = gtfs.DB_PATH.stat().st_mtime
    if _anchors_db[0] != stamp:
        _anchors.clear()
        _anchors_db[0] = stamp
    spots = {}
    by_line = {}
    for t in trips:
        spots[t["id"]] = _scheduled_position(t, db)
        by_line.setdefault((t["route"], t["type"]), []).append(t)
    for v in vehicles:
        ranked = sorted(
            (_metres((v["lat"], v["lon"]), spots[t["id"]]), t["num"])
            for t in by_line.get((v["line"], _ROUTE_TYPE[v["kind"]]), []) if spots[t["id"]]
        )
        if ranked and ranked[0][0] < LEARN_NEAR_M and (len(ranked) == 1 or ranked[1][0] > LEARN_RIVAL_M):
            _anchors[v["key"]] = (v["key"] - ranked[0][1], now)
    for key in [k for k, (_, t) in _anchors.items() if now - t > OFFSET_TTL_SEC]:
        del _anchors[key]


def _offset_for(key):
    """Stała dla tego klucza, jeśli sąsiednie klucze (±KEY_WINDOW) w zdecydowanej
    większości zgadzają się co do niej - stała jest bowiem stała tylko w obrębie
    bloku kursów, a gdzie indziej (zmierzone) rozjeżdża się o kilkadziesiąt
    numerów. Własny głos klucza się nie liczy."""
    votes = {}
    for k, (offset, _) in _anchors.items():
        if k != key and abs(k - key) <= KEY_WINDOW:
            votes[offset] = votes.get(offset, 0) + 1
    if not votes:
        return None
    offset, top = max(votes.items(), key=lambda kv: kv[1])
    if top >= OFFSET_MIN_KEYS and top >= OFFSET_SHARE * sum(votes.values()):
        return offset
    return None


def _add_courses(vehicles):
    """Przy pojazdach z rozpoznanym kursem: v["course"] = (kurs, następny przystanek).

    Pojazd wiąże się z kursem po kluczu (patrz komentarz nad sekcją). Gdy dwa
    pojazdy dostają ten sam kurs, nie wiążemy żadnego."""
    now = datetime.now(WARSAW)
    db = gtfs.open_db()
    try:
        trips = _running_trips(now, db)
        _learn_offsets(vehicles, trips, db, time.time())
        by_num = {t["num"]: t for t in trips}
        found = {}
        for i, v in enumerate(vehicles):
            offset = _offset_for(v["key"])
            trip = by_num.get(v["key"] - offset) if offset is not None else None
            if (trip and trip["route"] == v["line"] and trip["type"] == _ROUTE_TYPE[v["kind"]]
                    and _scheduled_position(trip, db)):
                found[i] = trip
        claims = {}
        for trip in found.values():
            claims[trip["id"]] = claims.get(trip["id"], 0) + 1
        for i, trip in found.items():
            v = vehicles[i]
            where = _locate(trip, (v["lat"], v["lon"]), db)
            if claims[trip["id"]] == 1 and where is not None and where[0] <= ON_ROUTE_M:
                stops = _trip_stops(trip, db)
                v["course"] = (trip, stops[where[1]])
                v["rest"] = [[round(s[0], 5), round(s[1], 5)] for s in stops[where[1]:]]
        _add_routes(vehicles, trips, db)
    finally:
        db.close()


def _add_routes(vehicles, trips, db):
    """Pojazdom bez kursu, ale z wiarygodnym ruchem: v["route"] = (cel, następny
    przystanek), gdy wszystkie warianty trasy tej linii, które mijają kropkę
    (≤ ROUTE_NEAR_M) w kierunku zgodnym z ruchem (< ROUTE_DEG), mają ten sam cel
    i ten sam następny przystanek. Pętla, rozjazd albo brak dopasowania - nic."""
    variants = {}
    for t in trips:
        stops = _trip_stops(t, db)
        key = (t["route"], t["type"], t["headsign"], len(stops), stops[0][:2], stops[-1][:2])
        variants.setdefault(key, t)
    by_line = {}
    for (route, kind, *_), t in variants.items():
        by_line.setdefault((route, kind), []).append(t)
    for v in vehicles:
        steps = v["steps"]
        if "course" in v or len(steps) != 2 or _angle_diff(steps[0], steps[1]) >= STRAIGHT_DEG:
            continue
        point = (v["lat"], v["lon"])
        found = set()
        for t in by_line.get((v["line"], _ROUTE_TYPE[v["kind"]]), []):
            stops = _trip_stops(t, db)
            for j in range(len(stops) - 1):
                if (_seg_distance(point, stops[j][:2], stops[j + 1][:2]) <= ROUTE_NEAR_M
                        and _angle_diff(steps[1], _bearing(stops[j][:2], stops[j + 1][:2])) < ROUTE_DEG):
                    nxt = j + 1
                    if nxt + 1 < len(stops) and _metres(point, stops[nxt][:2]) < AT_STOP_M:
                        nxt += 1
                    found.add((t["headsign"], stops[nxt][3], stops[nxt][0], stops[nxt][1]))
        if len({f[:2] for f in found}) == 1:
            headsign, name, lat, lon = next(iter(found))
            v["route"] = (headsign, (lat, lon, None, name))


def _add_headings(vehicles):
    """Składa z kursu (albo wariantu trasy) i ruchu to, co dostaje front: heading
    (stopnie), source ("trip"/"route") oraz to (cel) i next (następny przystanek).
    Strzałka jest tylko tam, gdzie znamy cel - bez celu pojazd ma samo kółko."""
    for v in vehicles:
        steps = v.pop("steps")
        course = v.pop("course", None)
        route = v.pop("route", None)
        v["heading"] = v["source"] = None
        if course or route:
            headsign, stop = (course[0]["headsign"], course[1]) if course else route
            bearing = _bearing((v["lat"], v["lon"]), stop[:2])
            if steps and _angle_diff(steps[-1], bearing) > VETO_DEG:
                continue
            v["heading"], v["source"] = round(bearing), "trip" if course else "route"
            v["to"], v["next"] = headsign, stop[3]


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
