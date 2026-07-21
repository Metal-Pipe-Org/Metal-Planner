"""Dostęp do stacji Wrocławskiego Roweru Miejskiego (WRM) przez GBFS.

WRM (system nextbike) publikuje stan sieci jako feed GBFS (General Bikeshare
Feed Specification) - ten sam otwarty standard co w wielu innych miastach.
System w GBFS nazywa się "nextbike_pl" i obejmuje Wrocław oraz gminy
ościenne włączone do tej samej sieci (Kobierzyce, Wisznia, Kąty Wrocławskie,
Siechnice, Czernica) - to jeden, spójny system stacji, nie trzeba nic filtrować.

Trzy pliki nas interesują:
- `station_information.json` - nazwa, współrzędne, pojemność stacji
  (zmienia się rzadko, długi cache);
- `station_status.json` - liczba dostępnych rowerów oraz ich rozbicie na
  typy pojazdów (feed deklaruje odświeżanie co 60 s - `ttl` w odpowiedzi -
  więc tyle też cache'ujemy);
- `vehicle_types.json` - słownik typów pojazdów (nazwa, `propulsion_type`);
  używamy go tylko po to, żeby rozpoznać, które `vehicle_type_id` to rower
  elektryczny ("electric_assist") - nextbike ma kilka modeli elektrycznych
  na raz (różne ID), więc nie da się tego zahardkodować jedną liczbą.

Wszystko cache'ujemy osobno w pamięci procesu i łączymy po station_id.
To warstwa czysto informacyjna: nie wpływa na gtfs.py / planner.py /
wyszukiwanie tras.
"""

import json
import time
import urllib.request

INFO_URL = "https://gbfs.nextbike.net/maps/gbfs/v2/nextbike_pl/pl/station_information.json"
STATUS_URL = "https://gbfs.nextbike.net/maps/gbfs/v2/nextbike_pl/pl/station_status.json"
VEHICLE_TYPES_URL = "https://gbfs.nextbike.net/maps/gbfs/v2/nextbike_pl/pl/vehicle_types.json"

INFO_TTL_SEC = 3600            # lokalizacja/pojemność stacji prawie się nie zmienia
STATUS_TTL_SEC = 60             # tyle deklaruje sam feed GBFS (pole "ttl")
VEHICLE_TYPES_TTL_SEC = 3600    # modele rowerów w systemie zmieniają się rzadko

ELECTRIC_PROPULSION = {"electric_assist", "electric"}

_info_cache = {"at": 0.0, "by_id": {}}
_status_cache = {"at": 0.0, "by_id": {}}
_vehicle_types_cache = {"at": 0.0, "electric_ids": set()}


class BikeDataError(Exception):
    """Feed GBFS WRM niedostępny, a w cache'u nie ma jeszcze żadnych danych."""


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Metal-Planner/0.1"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def _refresh(cache, url, ttl):
    """Odświeża cache, jeśli wygasł. Błąd sieci = zostajemy przy starych
    danych (stacja WRM padnie na chwilę rzadziej niż nasz odczyt); tylko gdy
    cache jest jeszcze zupełnie pusty (pierwsze zapytanie po starcie), błąd
    propaguje się dalej jako BikeDataError."""
    if time.monotonic() - cache["at"] < ttl:
        return
    try:
        data = _fetch(url)
        cache["by_id"] = {s["station_id"]: s for s in data["data"]["stations"]}
        cache["at"] = time.monotonic()
    except (OSError, ValueError, KeyError) as e:
        if not cache["by_id"]:
            raise BikeDataError(f"Nie udało się pobrać danych stacji WRM z {url}") from e


def _refresh_vehicle_types():
    """Jak _refresh, ale dla listy typów pojazdów - to tylko wzbogacenie
    (liczba elektryków), więc błąd sieci nigdy nie podnosi wyjątku: po
    prostu żadna stacja nie pokaże elektryków, dopóki się nie odświeży."""
    if time.monotonic() - _vehicle_types_cache["at"] < VEHICLE_TYPES_TTL_SEC:
        return
    try:
        data = _fetch(VEHICLE_TYPES_URL)
        _vehicle_types_cache["electric_ids"] = {
            v["vehicle_type_id"] for v in data["data"]["vehicle_types"]
            if v.get("propulsion_type") in ELECTRIC_PROPULSION
        }
        _vehicle_types_cache["at"] = time.monotonic()
    except (OSError, ValueError, KeyError):
        pass


def station_list():
    """Stacje WRM z aktualną dostępnością: [{name, lat, lon, bikes, electric}, ...]."""
    _refresh(_info_cache, INFO_URL, INFO_TTL_SEC)
    _refresh(_status_cache, STATUS_URL, STATUS_TTL_SEC)
    _refresh_vehicle_types()
    electric_ids = _vehicle_types_cache["electric_ids"]

    stations = []
    for station_id, info in _info_cache["by_id"].items():
        status = _status_cache["by_id"].get(station_id)
        if status is None or not status.get("is_installed", True):
            continue    # stacja zdemontowana/nieaktywna - nie rysujemy jej
        electric = sum(
            vt["count"] for vt in status.get("vehicle_types_available", ())
            if vt["vehicle_type_id"] in electric_ids
        )
        stations.append({
            "name": info["name"],
            "lat": info["lat"],
            "lon": info["lon"],
            "bikes": status["num_bikes_available"],
            "electric": electric,
        })
    return stations
