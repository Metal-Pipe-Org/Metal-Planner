"""Rower WRM jako pełnoprawny transfer w CSA - most między dowolnymi dwoma
przystankami przez stację, na wzór pieszych sąsiadów (`gtfs.py`,
`day.siblings`), ale z dwiema różnicami:

1. Trzy odcinki zamiast jednego: dojście do stacji startowej, przejazd
   rowerem do innej stacji, dojście od niej do przystanku docelowego.
2. Dostępność (rowery na stacji startowej, wolne doki na docelowej) zmienia
   się z minuty na minutę - w odróżnieniu od pieszych sąsiadów NIE da się
   jej prekomputować raz i cache'ować razem z dniem rozkładu.

Rozwiązanie: część GEOMETRYCZNĄ (które przystanki są blisko których stacji,
które stacje są w rozsądnym zasięgu roweru od siebie) liczymy raz i
cache'ujemy - pozycje stacji zmieniają się rzadko (patrz
`bikes.stations_generation()`). Dostępność sprawdzamy NA ŻYWO przy każdym
zapytaniu (cache 60 s, patrz `bikes.py`) i dopiero WYNIK tego sprawdzenia
scalamy w gotowe krawędzie przystanek -> przystanek, w kształcie identycznym
jak `day.siblings` ((sąsiad, sek), ...), żeby dało się je scalić i przekazać
do `_scan`/`_forward`/`_backward` bez zmiany ich logiki.
"""

import math

import bikes

WALK_TO_STATION_M = 400        # promień dojścia do stacji - tyle co zwykli piesi sąsiedzi (gtfs.WALK_RADIUS_M)
WALK_SPEED_MPS = 1.3           # ~4,7 km/h, jak gtfs.WALK_SPEED_MPS
WALK_MIN_SEC = 60
STOP_STATION_TOPK = 2          # ile najbliższych stacji bierzemy pod uwagę per przystanek
STATION_STOP_TOPK = 3          # ile najbliższych przystanków bierzemy pod uwagę per stacja

BIKE_MAX_M = 4000              # maks. sensowna odległość jazdy rowerem między stacjami
BIKE_SPEED_MPS = 4.2           # ~15 km/h - jazda miejska, ze świałami/skrzyżowaniami
BIKE_MIN_SEC = 60
STATION_STATION_TOPK = 6       # ile najbliższych INNYCH stacji bierzemy pod uwagę per stacja

_topology_cache = {}   # (id(day), bikes.stations_generation()) -> _Topology


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class _Topology:
    __slots__ = ("stop_to_stations", "station_to_stops", "station_to_stations", "station_names")

    def __init__(self):
        self.stop_to_stations = {}     # stop_id -> [(station_id, sek_dojścia), ...]
        self.station_to_stops = {}     # station_id -> [(stop_id, sek_dojścia), ...]
        self.station_to_stations = {}  # station_id -> [(inna_stacja, sek_jazdy), ...]
        self.station_names = {}        # station_id -> nazwa (do dymków)


def _nearest(origin_id, olat, olon, candidates, speed_mps, min_sec, top_k, max_m=None):
    """Najbliżsi `top_k` kandydaci z `candidates` (dict id -> {lat,lon}/(lat,lon))
    w promieniu `max_m` (albo bez limitu), z czasem dojazdu w sekundach."""
    near = []
    for cid, pos in candidates.items():
        if cid == origin_id:
            continue
        clat, clon = (pos["lat"], pos["lon"]) if isinstance(pos, dict) else pos
        dist = _haversine_m(olat, olon, clat, clon)
        if max_m is not None and dist > max_m:
            continue
        near.append((cid, dist))
    near.sort(key=lambda x: x[1])
    return [(cid, max(min_sec, round(dist / speed_mps))) for cid, dist in near[:top_k]]


def _build_topology(day):
    positions = bikes.station_positions()   # station_id -> {name, lat, lon}
    topo = _Topology()
    topo.station_names = {sid: info["name"] for sid, info in positions.items()}

    for stop_id, (lat, lon) in day.stop_coords.items():
        near = _nearest(
            None, lat, lon, positions, WALK_SPEED_MPS, WALK_MIN_SEC,
            STOP_STATION_TOPK, max_m=WALK_TO_STATION_M,
        )
        if near:
            topo.stop_to_stations[stop_id] = near

    for sid, info in positions.items():
        near = _nearest(
            sid, info["lat"], info["lon"], day.stop_coords, WALK_SPEED_MPS, WALK_MIN_SEC,
            STATION_STOP_TOPK, max_m=WALK_TO_STATION_M,
        )
        if near:
            topo.station_to_stops[sid] = near

    for sid, info in positions.items():
        near = _nearest(
            sid, info["lat"], info["lon"], positions, BIKE_SPEED_MPS, BIKE_MIN_SEC,
            STATION_STATION_TOPK, max_m=BIKE_MAX_M,
        )
        if near:
            topo.station_to_stations[sid] = near

    return topo


def _topology(day):
    key = (id(day), bikes.stations_generation())
    topo = _topology_cache.get(key)
    if topo is None:
        topo = _build_topology(day)
        _topology_cache.clear()    # jeden wpis wystarczy - zawsze dla aktualnego dnia/stacji
        _topology_cache[key] = topo
    return topo


def build_bike_edges(day):
    """Zwraca (edges, reverse_edges, hints):

    - `edges`: {stop_id: [(inny_stop_id, sek_total), ...]} - gotowe krawędzie
      przystanek -> przystanek przez rower WRM, TYLKO tam, gdzie w TEJ CHWILI
      (cache 60 s, patrz bikes.py) jest dostępny rower na stacji startowej
      I wolny dok na stacji docelowej. Kształt identyczny jak `day.siblings`,
      więc łatwo scalić (patrz `merge_siblings`) i przekazać do
      `_scan`/`_forward` bez zmiany ich logiki.
    - `reverse_edges`: to samo, ale w drugą stronę (kto może dotrzeć DO
      danego przystanku, nie dokąd z niego można dojechać). Dostępność jest
      KIERUNKOWA (stacja A ma rower ≠ stacja B ma rower) - w odróżnieniu od
      pieszych sąsiadów (zawsze symetrycznych) `edges` i `reverse_edges` NIE
      są tym samym. `_backward` (skan wstecz) i konstrukcja dojścia do celu
      w `plan_flow` propagują informację "kto dotrze DO X", więc potrzebują
      tej odwróconej relacji - użycie `edges` tam dałoby błędny kierunek.
    - `hints`: {(stop_a, stop_b): (nazwa_stacji_1, nazwa_stacji_2, sek_dojscia1,
      sek_roweru, sek_dojscia2)} - do renderowania (kind, dymek) w plan_flow;
      krawędzie BEZ wpisu w hints to zwykli piesi sąsiedzi.

    Jeśli feed GBFS jest akurat niedostępny, zwraca (puste, puste, puste) -
    rower to rozszerzenie, nie ma sensu wywalać całego wyszukiwania połączeń.
    """
    try:
        topo = _topology(day)
        available = bikes.station_availability()   # station_id -> (rowery, doki)
    except bikes.BikeDataError:
        return {}, {}, {}

    has_bike = {sid for sid, (b, _) in available.items() if b > 0}
    has_dock = {sid for sid, (_, d) in available.items() if d > 0}

    edges = {}
    reverse_edges = {}
    hints = {}
    for stop_a, near_a in topo.stop_to_stations.items():
        for s1, walk1 in near_a:
            if s1 not in has_bike:
                continue
            for s2, bike_sec in topo.station_to_stations.get(s1, ()):
                if s2 not in has_dock:
                    continue
                for stop_b, walk2 in topo.station_to_stops.get(s2, ()):
                    if stop_b == stop_a:
                        continue
                    total = walk1 + bike_sec + walk2
                    edges.setdefault(stop_a, []).append((stop_b, total))
                    reverse_edges.setdefault(stop_b, []).append((stop_a, total))
                    key = (stop_a, stop_b)
                    if key not in hints or total < sum(hints[key][2:]):
                        hints[key] = (
                            topo.station_names.get(s1, "?"), topo.station_names.get(s2, "?"),
                            walk1, bike_sec, walk2,
                        )
    return edges, reverse_edges, hints


def merge_siblings(base, extra):
    """Scala dwie relacje `stop_id -> [(sąsiad, sek), ...]` w jedną (nowy
    dict - `base`, czyli `day.siblings`, zostaje nietknięty, bo jest
    dzielony między zapytaniami przez cache dnia)."""
    if not extra:
        return base
    merged = {stop: list(pairs) for stop, pairs in base.items()}
    for stop, pairs in extra.items():
        merged.setdefault(stop, []).extend(pairs)
    return merged
