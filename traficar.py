"""Dostęp do samochodów Traficar (car-sharing) przez REST API fioletowe.live.

Traficar (marka rozpoznawalna po fioletowym malowaniu aut - stąd nazwa
serwisu) sam nie publikuje żadnego oficjalnego API. `fioletowe.live`
(open source, GitHub `divadsn/traficar-map`, licencja GPLv3) republikuje
jego wewnętrzne API jako udokumentowany REST/JSON bez klucza
(`/docs/`, `/api/openapi.json`) - to jest STRONA TRZECIA, nie sam Traficar,
więc może zniknąć albo zmienić kształt bez ostrzeżenia (to samo zastrzeżenie
co przy GTFS-Realtime, patrz PROJECT.md).

Wrocław to zoneId=3, potwierdzone przez `GET /api/v1/zones`. Czysto
informacyjna warstwa - tak jak WRM, nie wpływa na wyszukiwanie połączeń.
"""

import json
import time
import urllib.request

CARS_URL = "https://fioletowe.live/api/v1/cars?zoneId=3"
CARS_TTL_SEC = 20   # feed sam deklaruje Cache-Control: max-age=12 - nie odpytujemy częściej

_cars_cache = {"at": 0.0, "cars": []}


class TraficarDataError(Exception):
    """API fioletowe.live niedostępne, a w cache'u nie ma jeszcze żadnych danych."""


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Metal-Planner/0.1"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def car_list():
    """Auta Traficar we Wrocławiu: [{lat, lon, fuel, range, plate, available}, ...].

    `fuel` to procent paliwa (0-100), `range` zasięg w km, `plate` numer
    rejestracyjny (do odróżnienia aut w dymku), `available` czy auto jest
    w tej chwili wolne do wynajęcia. Błąd sieci przy pustym cache'u ->
    TraficarDataError; przy niepustym cache'u - stare dane zamiast wyjątku
    (ten sam styl co bikes.py - auto "zestarzeje się" zamiast zniknąć)."""
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
            if not _cars_cache["cars"]:
                raise TraficarDataError(
                    "Nie udało się pobrać danych Traficar z fioletowe.live"
                ) from e
    return _cars_cache["cars"]
