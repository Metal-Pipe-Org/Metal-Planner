"""Pozycje pojazdów MPK na żywo i SZACOWANE opóźnienia względem rozkładu.

Źródło: `https://mpk.wroc.pl/bus_position` - wewnętrzny endpoint AJAX strony
MPK (ten sam, z którego korzysta mapa iMPK i kilka projektów społecznościowych,
np. `real-kijmoshi/wroclaw-mpk-map`). To NIE jest oficjalny portal Otwartych
Danych (ten stoi za WAF-em FortiADC i bywa niedostępny spoza Polski), tylko
żywy endpoint samego przewoźnika:

    POST https://mpk.wroc.pl/bus_position
    Content-Type: application/x-www-form-urlencoded
    body: busList[tram][]=5&busList[bus][]=145&...   (trzeba wymienić linie)
    -> [{"name":"5","type":"tram","x":51.07,"y":17.08,"k":28653628}, ...]
       x = szerokość (lat), y = długość (lon), k = stabilne ID pojazdu/kursu.

**Kluczowe ograniczenie**: feed daje POZYCJE, ale NIE `trip_id` ani `delay`.
Opóźnienie trzeba więc OSZACOWAĆ przez dopasowanie pozycji do rozkładu
(map-matching): rzutujemy pozycję pojazdu na łamaną każdego kursu jego linii,
który akurat jest „w trasie", odczytujemy rozkładowy czas w punkcie rzutu i
liczymy `opóźnienie = teraz - czas_rozkładowy`. Spośród kursów bierzemy ten,
który daje najmniejsze |opóźnienie| (najbardziej prawdopodobne przypisanie
biegu, bo bez trip_id nie da się rozróżnić dwóch kolejnych kursów tej samej
linii inaczej niż po czasie). To przybliżenie - patrz „Znane ograniczenia"
w PROJECT.md - ale dobre na tyle, by pokazać, że linia jedzie +5 min, i
skorygować margines przesiadki.

Odróżnienie od ODRZUCONEGO wcześniej pomysłu (GTFS-RT z
`mapadlugoleka.klosok.eu`, patrz Changelog 2026-07-22): tamten feed miał
poprawny kształt, ale puste `trip_id`/`delay`/`stop_id` i 8 pojazdów.
Ten endpoint zwraca ~500 pojazdów z prawdziwymi pozycjami całego miasta -
opóźnienie liczymy sami, ale mamy z czego.
"""

import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime

POSITIONS_URL = "https://mpk.wroc.pl/bus_position"
POSITIONS_TTL_SEC = 15          # feed odświeża się co ~10-15 s
DELAY_TTL_SEC = 12              # wynik dopasowania cache'ujemy chwilę (kilka zapytań pod rząd)

MATCH_MAX_M = 250              # pojazd dalej niż tyle od łamanej kursu = nie ten kurs
DELAY_MAX_SEC = 1800          # |opóźnienie| > 30 min = pewnie złe dopasowanie / pojazd poza służbą
ACTIVE_SLACK_SEC = 2400       # kurs „w trasie" to taki, którego okno [pierwszy odjazd, ostatni
                              #   przyjazd] obejmuje teraz z zapasem tylu sekund po obu stronach
FRESH_WINDOW_SEC = 2700       # opóźnienie stosujemy tylko do odjazdów w ciągu ~45 min od realnego
                              #   „teraz" - dla zapytań o daleką przyszłość dane na żywo nie mają sensu

_M_PER_DEG = 111_320.0        # metrów na stopień szerokości (i długości po przeskalowaniu cos(lat))

KIND_OF_TYPE = {"tram": "tram", "bus": "bus"}
LABEL_KIND = {"Tramwaj": "tram", "Autobus": "bus"}


class RealtimeError(Exception):
    """Feed pozycji niedostępny, a w cache'u nie ma jeszcze żadnych danych."""


_pos_cache = {"at": 0.0, "vehicles": []}
_topo_cache = {}      # id(day) -> _Topology (jeden wpis - zawsze aktualny dzień)
_delay_cache = {"key": None, "at": 0.0, "result": None}


class _Topology:
    """Rozkład dnia przygotowany pod dopasowanie pozycji: dla każdego kursu
    łamana (punkty przystanków) z rozkładowymi czasami, plus indeks kursów
    po linii. Liczone raz na dzień i cache'owane (jak geometria w gtfs.py)."""

    __slots__ = ("trips", "by_line", "line_names")

    def __init__(self):
        self.trips = {}        # trip_id -> (times[], pts[], headsign, first, last)
        self.by_line = {}      # (kind, num) -> [trip_id, ...]
        self.line_names = {"tram": set(), "bus": set()}   # do zbudowania zapytania POST


def _build_topology(day):
    topo = _Topology()
    by_trip = {}
    for dep, arr, from_s, to_s, trip in day.conns:
        by_trip.setdefault(trip, []).append((dep, arr, from_s, to_s))

    coords = day.stop_coords
    for trip, segs in by_trip.items():
        segs.sort()                       # w ramach kursu odjazdy rosną
        times = [segs[0][0]]
        pts = [coords[segs[0][2]]]
        for dep, arr, from_s, to_s in segs:
            times.append(arr)
            pts.append(coords[to_s])
        label, headsign = day.trip_info.get(trip, ("Linia ?", ""))
        kind = LABEL_KIND.get(label.split(" ", 1)[0])
        if kind is None:
            continue                      # linia bez rozpoznanego typu - pomijamy
        num = label.split(" ", 1)[1].strip() if " " in label else label
        topo.trips[trip] = (times, pts, headsign, times[0], times[-1])
        topo.by_line.setdefault((kind, num), []).append(trip)
        topo.line_names[kind].add(num)
    return topo


def _topology(day):
    topo = _topo_cache.get(id(day))
    if topo is None:
        topo = _build_topology(day)
        _topo_cache.clear()
        _topo_cache[id(day)] = topo
    return topo


def _fetch_positions(line_names):
    """POST do feedu MPK dla podanych linii; zwraca listę pojazdów z GPS-em.

    line_names: {"tram": {...}, "bus": {...}}. Pojazdy bez ustalonej pozycji
    (x=y=0) są odfiltrowane."""
    pairs = [("busList[tram][]", n) for n in sorted(line_names["tram"])]
    pairs += [("busList[bus][]", n) for n in sorted(line_names["bus"])]
    body = urllib.parse.urlencode(pairs).encode()
    request = urllib.request.Request(
        POSITIONS_URL, data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Metal-Planner/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        data = json.load(response)
    vehicles = []
    for v in data:
        lat, lon = v.get("x"), v.get("y")
        if not lat or not lon:            # 0/0 albo brak = brak fixu GPS
            continue
        kind = KIND_OF_TYPE.get(v.get("type"))
        if kind is None:
            continue
        vehicles.append({
            "kind": kind, "num": str(v["name"]).strip(),
            "lat": lat, "lon": lon, "k": v.get("k"),
        })
    return vehicles


def vehicle_positions(day):
    """Surowe pozycje pojazdów (cache POSITIONS_TTL_SEC). Błąd sieci przy
    pustym cache'u -> RealtimeError; przy niepustym - stare dane (jak bikes.py).

    Znacznik `at` jest ustawiany także PO nieudanej próbie - to backoff: gdy
    feed padnie, kolejne zapytania (o trasę, odjazdy) przez TTL sekund
    dostają cache zamiast czekać na kolejny timeout. Bez tego padnięty feed
    spowalniałby KAŻDE zapytanie o ~timeout sekund, bo pusty cache nie
    zaliczałby warunku TTL i wymuszał ponowną próbę za każdym razem."""
    if time.monotonic() - _pos_cache["at"] < POSITIONS_TTL_SEC:
        return _pos_cache["vehicles"]
    topo = _topology(day)
    try:
        _pos_cache["vehicles"] = _fetch_positions(topo.line_names)
    except (OSError, ValueError, KeyError) as e:
        _pos_cache["at"] = time.monotonic()           # backoff nawet po błędzie
        if not _pos_cache["vehicles"]:
            raise RealtimeError(f"Nie udało się pobrać pozycji pojazdów z {POSITIONS_URL}") from e
    else:
        _pos_cache["at"] = time.monotonic()
    return _pos_cache["vehicles"]


def _nearest_on_timeline(vlat, vlon, times, pts):
    """Najbliższy punkt łamanej kursu do pozycji pojazdu: (odległość_m,
    czas_rozkładowy_sek w tym punkcie). Lokalna metryka planarna wokół pojazdu
    (skalowanie długości przez cos(lat)) - dokładna na dystansach miejskich."""
    coslat = math.cos(math.radians(vlat))
    best_d2 = float("inf")
    best_t = None
    ax = (pts[0][1] - vlon) * coslat * _M_PER_DEG
    ay = (pts[0][0] - vlat) * _M_PER_DEG
    for i in range(len(pts) - 1):
        bx = (pts[i + 1][1] - vlon) * coslat * _M_PER_DEG
        by = (pts[i + 1][0] - vlat) * _M_PER_DEG
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
        cx, cy = ax + t * dx, ay + t * dy
        d2 = cx * cx + cy * cy
        if d2 < best_d2:
            best_d2 = d2
            best_t = times[i] + t * (times[i + 1] - times[i])
        ax, ay = bx, by
    return math.sqrt(best_d2), best_t


def _match_vehicle(vehicle, trips, now_sec):
    """Dopasowuje pojazd do najbardziej prawdopodobnego kursu jego linii.

    Zwraca (opóźnienie_sek, headsign) albo (None, None), gdy pojazd nie leży
    wiarygodnie na żadnej łamanej albo szacowane opóźnienie jest absurdalne.
    Kryterium wyboru kursu: najmniejsze |opóźnienie| wśród kursów, na których
    łamanej pojazd faktycznie leży (odległość < MATCH_MAX_M) - bez trip_id to
    najlepsze przypisanie biegu (patrz nagłówek modułu). Uwzględniamy też
    `now_sec + 86400`, żeby kursy „po północy" (24:xx w GTFS) dopasowały się
    tuż po północy."""
    best = None                          # (abs_delay, delay, headsign)
    for trip in trips:
        times, pts, headsign, _, _ = trip
        dist_m, sched = _nearest_on_timeline(vehicle["lat"], vehicle["lon"], times, pts)
        if dist_m > MATCH_MAX_M:
            continue
        for now_candidate in (now_sec, now_sec + 86400):
            delay = now_candidate - sched
            if abs(delay) > DELAY_MAX_SEC:
                continue
            if best is None or abs(delay) < best[0]:
                best = (abs(delay), delay, headsign)
    if best is None:
        return None, None
    return best[1], best[2]


def _match_all(day, now_sec):
    """Dla każdego pojazdu z feedu: pozycja + oszacowane opóźnienie (albo None).
    Zwraca listę dictów [{kind, num, lat, lon, k, delay, headsign}, ...]."""
    vehicles = vehicle_positions(day)
    topo = _topology(day)
    results = []
    active_by_line = {}
    for v in vehicles:
        key = (v["kind"], v["num"])
        active = active_by_line.get(key)
        if active is None:
            active = active_by_line[key] = [
                topo.trips[t] for t in topo.by_line.get(key, ())
                if topo.trips[t][3] - ACTIVE_SLACK_SEC <= now_sec <= topo.trips[t][4] + ACTIVE_SLACK_SEC
            ]
        delay, headsign = _match_vehicle(v, active, now_sec) if active else (None, None)
        results.append({
            "kind": v["kind"], "num": v["num"], "lat": v["lat"], "lon": v["lon"],
            "k": v["k"], "delay": delay, "headsign": headsign,
        })
    return results


def _median(values):
    values = sorted(values)
    n = len(values)
    if n == 0:
        return None
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


class LiveDelays:
    """Zagregowane opóźnienia z jednego dopasowania - wygodne `get`i dla
    tablicy odjazdów i marginesów przesiadki. `by_dir` (linia+kierunek) jest
    dokładniejsze niż `by_line` (uśrednia obie strony), więc szukamy najpierw
    po kierunku, potem po samej linii."""

    __slots__ = ("by_line", "by_dir", "n_vehicles", "n_matched", "now_sec")

    def __init__(self, vehicles, now_sec):
        self.now_sec = now_sec
        self.n_vehicles = len(vehicles)
        self.n_matched = sum(1 for v in vehicles if v["delay"] is not None)
        line_delays = {}
        dir_delays = {}
        for v in vehicles:
            if v["delay"] is None:
                continue
            line_delays.setdefault((v["kind"], v["num"]), []).append(v["delay"])
            dir_delays.setdefault((v["kind"], v["num"], v["headsign"]), []).append(v["delay"])
        self.by_line = {k: _median(d) for k, d in line_delays.items()}
        self.by_dir = {k: _median(d) for k, d in dir_delays.items()}

    def delay_for(self, kind, num, headsign=None, sched_sec=None):
        """Szacowane opóźnienie linii (sek, +późno / -wcześnie) albo None.

        `sched_sec` (opcjonalnie): rozkładowy czas odjazdu, którego dotyczy
        pytanie - dane na żywo stosujemy tylko, gdy ten czas jest blisko
        realnego „teraz" (FRESH_WINDOW_SEC); dla zapytań o daleką przyszłość
        opóźnienie bieżących pojazdów jest nieistotne."""
        if sched_sec is not None and not (
            self.now_sec - 600 <= sched_sec <= self.now_sec + FRESH_WINDOW_SEC
        ):
            return None
        if headsign is not None and (kind, num, headsign) in self.by_dir:
            return self.by_dir[(kind, num, headsign)]
        return self.by_line.get((kind, num))


def _now_sec(now=None):
    now = now or datetime.now()
    return now.hour * 3600 + now.minute * 60 + now.second


def live_delays(day, now=None):
    """Zagregowane opóźnienia linii na TERAZ (cache DELAY_TTL_SEC). Zwraca
    obiekt LiveDelays albo None, gdy feed jest niedostępny (rower/opóźnienia
    to rozszerzenie - nigdy nie wywalamy przez nie wyszukiwania)."""
    now_sec = _now_sec(now)
    cache_key = (id(day), now_sec // 30)        # ta sama półminutówka = ten sam wynik
    if (_delay_cache["key"] == cache_key
            and time.monotonic() - _delay_cache["at"] < DELAY_TTL_SEC):
        return _delay_cache["result"]
    try:
        vehicles = _match_all(day, now_sec)
    except RealtimeError:
        return None
    result = LiveDelays(vehicles, now_sec)
    _delay_cache.update(key=cache_key, at=time.monotonic(), result=result)
    return result


def live_vehicles(day, now=None):
    """Pozycje pojazdów + oszacowane opóźnienie każdego - do warstwy „pojazdy
    na żywo" na mapie. Rzuca RealtimeError, gdy feed niedostępny i cache pusty
    (endpoint /api/vehicles zwróci wtedy 503, jak /api/bikes)."""
    return _match_all(day, _now_sec(now))
