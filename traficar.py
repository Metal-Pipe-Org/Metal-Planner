"""Dostęp do samochodów Traficar (car-sharing) przez REST API fioletowe.live.

Traficar (marka rozpoznawalna po fioletowym malowaniu aut - stąd nazwa
serwisu) sam nie publikuje żadnego oficjalnego API. `fioletowe.live`
(open source, GitHub `divadsn/traficar-map`, licencja GPLv3) republikuje
jego wewnętrzne API jako udokumentowany REST/JSON bez klucza
(`/docs/`, `/api/openapi.json`) - to jest STRONA TRZECIA, nie sam Traficar,
więc może zniknąć albo zmienić kształt bez ostrzeżenia (to samo zastrzeżenie
co przy realtime.py / GTFS-Realtime, patrz PROJECT.md).

Wrocław to zoneId=3 (potwierdzone przez `GET /api/v1/zones`). Moduł daje:
- `car_list()` - auta do warstwy informacyjnej na mapie (jak dotąd);
- `available_cars()` / `nearest_available()` - auta wolne do wynajęcia, do
  wpięcia Traficara jako opcji „od drzwi do drzwi" w planerze (patrz
  `planner.py`, „Traficar jako opcja dojazdu");
- `return_zone_geojson()` / `can_end()` / `nearest_return_point()` - STREFA
  ZWROTU (gdzie wolno zakończyć najem), z `/api/v1/zones/3/shapes`: poligon
  `END_RESERVATION_ENABLE` minus wykluczenia `END_RESERVATION_DISABLE`.
  Nie mamy logiki szukania parkingu, ale wiemy, gdzie w ogóle wolno oddać
  auto - jeśli cel jest poza strefą, planer dowozi do najbliższego punktu
  strefy i resztę pieszo.
"""

import json
import math
import time
import urllib.request

ZONE_ID = 3
CARS_URL = f"https://fioletowe.live/api/v1/cars?zoneId={ZONE_ID}"
ZONE_SHAPES_URL = f"https://fioletowe.live/api/v1/zones/{ZONE_ID}/shapes"
CARS_TTL_SEC = 20        # feed sam deklaruje Cache-Control: max-age=12 - nie odpytujemy częściej
ZONE_TTL_SEC = 6 * 3600  # granice strefy zmieniają się bardzo rzadko

_cars_cache = {"at": 0.0, "cars": []}
_zone_cache = {"at": 0.0, "enable": None, "disable": None}


class TraficarDataError(Exception):
    """API fioletowe.live niedostępne, a w cache'u nie ma jeszcze żadnych danych."""


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Metal-Planner/0.1"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def car_list():
    """Auta Traficar we Wrocławiu: [{lat, lon, fuel, range, plate, available}, ...].

    `fuel` to procent paliwa (0-100), `range` zasięg w km, `plate` numer
    rejestracyjny (do odróżnienia aut w dymku), `available` czy auto jest
    w tej chwili wolne do wynajęcia. Błąd sieci przy pustym cache'u ->
    TraficarDataError; przy niepustym cache'u - stare dane zamiast wyjątku
    (ten sam styl co bikes.py - auto „zestarzeje się” zamiast zniknąć)."""
    if time.monotonic() - _cars_cache["at"] >= CARS_TTL_SEC:
        try:
            data = _fetch(CARS_URL)
            _cars_cache["cars"] = [
                {
                    "lat": float(c["lat"]),
                    "lon": float(c["lng"]),
                    "fuel": round(c["fuel"]),
                    "range": c["range"],
                    "plate": c["regPlate"],
                    "available": c["available"],
                }
                for c in data["cars"]
            ]
            _cars_cache["at"] = time.monotonic()
        except (OSError, ValueError, KeyError) as e:
            _cars_cache["at"] = time.monotonic()      # backoff nawet po błędzie
            if not _cars_cache["cars"]:
                raise TraficarDataError(
                    "Nie udało się pobrać danych Traficar z fioletowe.live"
                ) from e
    return _cars_cache["cars"]


def available_cars():
    """Tylko auta wolne do wynajęcia: [{lat, lon, fuel, range, plate}, ...].
    Pusta lista, gdy feed niedostępny (Traficar to rozszerzenie planera,
    nie wywalamy przez nie wyszukiwania)."""
    try:
        return [c for c in car_list() if c["available"]]
    except TraficarDataError:
        return []


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_available(lat, lon, max_m=None):
    """Najbliższe wolne auto do punktu: (auto, odległość_m) albo (None, None).
    `max_m` (opcjonalnie) - odcięcie, gdy najbliższe auto jest zbyt daleko,
    by dojść do niego pieszo."""
    best, best_d = None, None
    for c in available_cars():
        d = _haversine_m(lat, lon, c["lat"], c["lon"])
        if (max_m is None or d <= max_m) and (best_d is None or d < best_d):
            best, best_d = c, d
    return best, best_d


# --- Strefa zwrotu (gdzie wolno zakończyć najem) ---------------------------
# GeoJSON MultiPolygon: [poligon, ...] -> [pierścień, ...] -> [[lon, lat], ...].
# Bierzemy tylko pierścień ZEWNĘTRZNY każdego poligonu (dziury i tak realizuje
# osobna warstwa DISABLE), a punkt jest „w strefie", gdy leży w którymś
# poligonie ENABLE i w ŻADNYM z DISABLE.

def _outer_rings(multipolygon_coords):
    """Zewnętrzne pierścienie z GeoJSON-owych coordinates MultiPolygon, każdy
    jako (bbox, [(lon, lat), ...]) - bbox do szybkiego odsiania."""
    rings = []
    for polygon in multipolygon_coords:
        if not polygon:
            continue
        ring = polygon[0]                     # pierścień zewnętrzny
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        rings.append(((min(lons), min(lats), max(lons), max(lats)), ring))
    return rings


def _refresh_zone():
    if time.monotonic() - _zone_cache["at"] < ZONE_TTL_SEC and _zone_cache["enable"] is not None:
        return
    try:
        data = _fetch(ZONE_SHAPES_URL)
        enable, disable = [], []
        for shape in data["shapes"]:
            name = shape.get("name", "")
            rings = _outer_rings(shape["geo"]["coordinates"])
            if "END_RESERVATION_ENABLE" in name:
                enable = rings
            elif "END_RESERVATION_DISABLE" in name:
                disable = rings
        _zone_cache.update(at=time.monotonic(), enable=enable, disable=disable)
    except (OSError, ValueError, KeyError):
        _zone_cache["at"] = time.monotonic()          # backoff; gdy enable None, can_end poluzuje
        if _zone_cache["enable"] is None:
            _zone_cache["enable"] = []
            _zone_cache["disable"] = []


def _point_in_ring(lon, lat, ring):
    """Ray casting: czy punkt leży w pierścieniu (lista [lon, lat])."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_any(lon, lat, rings):
    for bbox, ring in rings:
        if bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3] and _point_in_ring(lon, lat, ring):
            return True
    return False


def can_end(lat, lon):
    """Czy w tym punkcie wolno zakończyć najem Traficara (w strefie ENABLE,
    poza wykluczeniami DISABLE). Gdy strefa niedostępna (feed padł) -
    zakładamy True (nie blokujemy opcji przez brak danych o granicy)."""
    _refresh_zone()
    if not _zone_cache["enable"]:
        return True
    return _in_any(lon, lat, _zone_cache["enable"]) and not _in_any(lon, lat, _zone_cache["disable"])


def nearest_return_point(lat, lon):
    """Punkt, w którym realnie da się oddać auto najbliżej celu:
    (lat, lon, odległość_pieszo_m). Gdy cel jest w strefie - sam cel (0 m).
    Gdy poza - najbliższy WIERZCHOŁEK strefy ENABLE (przybliżenie; nie mamy
    logiki parkingów, tylko granicę strefy - patrz nagłówek). Rzadkie, bo
    strefa obejmuje całe miasto."""
    if can_end(lat, lon):
        return lat, lon, 0.0
    # Kandydaci: wierzchołki strefy ENABLE ORAZ granice wykluczeń DISABLE
    # (krawędź strefy pieszej to często najbliższy legalny zwrot dla celu w
    # środku takiej strefy, np. Rynek). Odrzucamy te, które same wpadają w
    # wykluczenie - punkt zwrotu musi przejść `can_end`.
    best = None
    rings = (_zone_cache["enable"] or []) + (_zone_cache["disable"] or [])
    for bbox, ring in rings:
        for plon, plat in ring:
            d = _haversine_m(lat, lon, plat, plon)
            if (best is None or d < best[2]) and can_end(plat, plon):
                best = (plat, plon, d)
    return best if best is not None else (lat, lon, 0.0)


def return_zone_geojson():
    """Strefa zwrotu do narysowania na mapie: {"enable": [[[lat,lon],...],...],
    "disable": [...]} - pierścienie zewnętrzne, współrzędne [lat, lon]
    (kolejność Leaflet). Pusta, gdy feed niedostępny."""
    _refresh_zone()

    def to_latlon(rings):
        return [[[lat, lon] for lon, lat in ring] for _, ring in rings]

    return {
        "enable": to_latlon(_zone_cache["enable"] or []),
        "disable": to_latlon(_zone_cache["disable"] or []),
    }
