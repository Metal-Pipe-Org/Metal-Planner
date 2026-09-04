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

Oprócz ostatnich pozycji trzymamy tu też KRÓTKI ŚLAD każdego pojazdu
(TRACK_KEEP_SEC, patrz _remember/previous_positions). Odpowiedź nie niesie
kierunku jazdy, a bez niego nie da się odróżnić autobusu jadącego w naszą
stronę od tego, który tą samą ulicą wraca - jedyne, z czego kierunek można
odczytać, to przesunięcie tego samego pojazdu między dwoma odczytami.
Identyfikator z pola "k" jest między odczytami stały (sprawdzone na żywych
danych), więc ślad da się w ogóle złożyć; korzysta z niego dopasowanie
pojazdów do wybranej trasy (patrz journey_live.py).
"""

import json
import threading
import time
import urllib.parse
import urllib.request

import gtfs

VEHICLES_URL = "https://mpk.wroc.pl/bus_position"

CACHE_SEC = 10

# Jak długo pamiętamy ślad pojazdu. Tyle wystarczy, żeby po najgorszym
# przypadku (pojazd stojący na przystanku albo w korku przez dwa
# odczyty) mieć wciąż próbkę, na której widać, dokąd jedzie.
TRACK_KEEP_SEC = 180

# Wrocław i najbliższa aglomeracja, z zapasem - dalej MPK nie jeździ. Poza
# tym zakresem w danych API trafiają się wyłącznie błędne/sentinelowe
# współrzędne (np. (0, 0) albo losowy szum rzędu dziesiątek/tysięcy stopni -
# zmierzone na żywych danych).
LAT_RANGE = (50.6, 51.6)
LON_RANGE = (16.4, 17.8)

_lock = threading.Lock()
_cache = {"at": 0.0, "vehicles": None}
# id pojazdu -> [(czas_monotoniczny, lat, lon), ...] rosnąco, patrz _remember
_tracks = {}


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
        # "k" to identyfikator pojazdu, stały między odczytami - bez niego
        # nie dałoby się złożyć śladu (patrz docstring modułu).
        parsed.append({"id": item.get("k"), "line": line, "kind": kind,
                       "lat": lat, "lon": lon})
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
        _remember(vehicles)
        _cache["vehicles"] = vehicles
        _cache["at"] = time.monotonic()
        return vehicles


def _remember(parsed):
    """Dopisuje świeże pozycje do śladów i zapomina to, co starsze niż
    TRACK_KEEP_SEC. Wołane spod tego samego zamka co odświeżenie cache'a."""
    now = time.monotonic()
    for vehicle in parsed:
        vehicle_id = vehicle.get("id")
        if vehicle_id is None:
            continue
        _tracks.setdefault(vehicle_id, []).append((now, vehicle["lat"], vehicle["lon"]))
    horizon = now - TRACK_KEEP_SEC
    for vehicle_id, track in list(_tracks.items()):
        kept = [sample for sample in track if sample[0] >= horizon]
        # Pojazd, który zniknął z odpowiedzi (zjechał do zajezdni, zmienił
        # linię), znika stąd sam - inaczej słownik rósłby przez całą dobę.
        if kept:
            _tracks[vehicle_id] = kept
        else:
            del _tracks[vehicle_id]


def previous_positions():
    """{id pojazdu: (lat, lon)} - NAJSTARSZA zapamiętana próbka każdego
    pojazdu (patrz _remember).

    Najstarsza, nie poprzednia: kierunek czyta się z przesunięcia, a im
    dłuższa baza, tym pewniej widać, dokąd pojazd jedzie - dwie próbki
    sprzed sekund potrafią różnić się samym szumem GPS. Ślad i tak sięga
    najwyżej TRACK_KEEP_SEC wstecz, więc pojazd nie zdąży w tym czasie
    zawrócić na pętli i udawać, że jedzie w drugą stronę.
    """
    with _lock:
        return {
            vehicle_id: (track[0][1], track[0][2])
            for vehicle_id, track in _tracks.items()
            if track
        }
