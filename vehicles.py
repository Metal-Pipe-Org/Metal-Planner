"""Pozycje pojazdów MPK z otwartych danych Wrocławia (portal open-data,
zestaw nr 14: https://api.open-data.cui.wroclaw.pl/od3-records/data/14/) -
zasila warstwę „autobusy i tramwaje" na mapie (patrz routes.api_vehicles
i static/app.js).

W przeciwieństwie do update_gtfs.py to źródło NIE trafia do bazy: pozycje
zmieniają się co kilkanaście-kilkadziesiąt sekund, więc trzyma się je
wyłącznie w pamięci procesu, z krótkim cache (CACHE_SEC) - inaczej każde
odświeżenie warstwy w przeglądarce (front odpytuje się cyklicznie, patrz
VEHICLES_REFRESH_MS w app.js) biłoby wprost w cudzy portal.
"""

import json
import threading
import time
import urllib.request
from datetime import datetime

import gtfs

VEHICLES_URL = "https://api.open-data.cui.wroclaw.pl/od3-records/data/14/"
# Dziś ~1000 pojazdów mieści się w jednej stronie; pętla w _fetch_all_raw
# i tak dociąga kolejne strony, gdyby kiedyś przestało.
PAGE_SIZE = 5000

CACHE_SEC = 10

# Portal miesza aktualne pozycje z pojazdami dawno wycofanymi/offline (jeden
# rekord na sztukę taboru, nigdy nie kasowany) - bez filtra świeżości mapa
# pokazywałaby "duchy" stojące w jednym miejscu od miesięcy albo lat.
# Odniesieniem jest NAJŚWIEŻSZY znacznik czasu W TEJ SAMEJ paczce, nie zegar
# serwera: strefa czasowa znaczników bywa niespójna z serwerem, ale różnice
# WEWNĄTRZ jednej odpowiedzi portalu są ze sobą porównywalne (zmierzone na
# żywych danych: ~85% rekordów z numerem linii ląduje w ciągu 5 minut od
# najświeższego, reszta to odstające o dni/miesiące/lata "duchy").
FRESH_WINDOW_SEC = 15 * 60

# Wrocław i najbliższa aglomeracja, z zapasem - dalej MPK nie jeździ. Poza
# tym zakresem w danych portalu trafiają się wyłącznie błędne/sentinelowe
# współrzędne (0.0, 0.0 albo stała spoza Polski - zmierzone na żywych danych).
LAT_RANGE = (50.6, 51.6)
LON_RANGE = (16.4, 17.8)

_lock = threading.Lock()
_cache = {"at": 0.0, "vehicles": None}


def _fetch_page(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Metal-Planner/0.1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def _fetch_all_raw():
    results = []
    url = f"{VEHICLES_URL}?page_size={PAGE_SIZE}"
    while url:
        page = _fetch_page(url)
        results.extend(page.get("results", []))
        url = page.get("next")
    return results


def _clean(value):
    """None i pusty/białoznakowy string liczą się tu jako to samo "brak
    danych" (patrz config.pkp_api_key), nie jako pusty tekst do pokazania
    w dymku - stąd też dymek w app.js nie musi osobno sprawdzać "" i null."""
    if isinstance(value, str):
        value = value.strip()
    return value if value not in (None, "") else None


def _parse(raw_results):
    kind_of = gtfs.line_kind_map()
    parsed = []
    for item in raw_results:
        data = item.get("data") or {}
        line = _clean(data.get("Nazwa_Linii"))
        if not line:
            continue   # brak numeru linii = nic sensownego do narysowania/podpisania
        lat = data.get("Ostatnia_Pozycja_Szerokosc")
        lon = data.get("Ostatnia_Pozycja_Dlugosc")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
            continue
        updated = data.get("Data_Aktualizacji")
        if not updated:
            continue
        parsed.append({
            "line": line,
            "kind": kind_of.get(line, "other"),
            "lat": lat,
            "lon": lon,
            "_updated": updated,
            "side_number": _clean(data.get("Nr_Boczny")),
            "brigade": _clean(data.get("Brygada")),
        })

    if not parsed:
        return []

    newest = max(datetime.fromisoformat(v["_updated"]) for v in parsed)
    fresh = []
    for v in parsed:
        age = (newest - datetime.fromisoformat(v.pop("_updated"))).total_seconds()
        if age <= FRESH_WINDOW_SEC:
            fresh.append(v)
    return fresh


def get_vehicles():
    """Aktualne pozycje pojazdów, z krótkim cache (CACHE_SEC) - patrz docstring
    modułu. Rzuca wyjątkiem sieciowym (OSError, w tym urllib.error.URLError
    i timeout) albo gtfs.FileNotFoundError (brak bazy rozkładów) - kto woła,
    łapie i zamienia na odpowiedź błędu (patrz routes.api_vehicles)."""
    with _lock:
        if _cache["vehicles"] is not None and time.monotonic() - _cache["at"] < CACHE_SEC:
            return _cache["vehicles"]
        vehicles = _parse(_fetch_all_raw())
        _cache["vehicles"] = vehicles
        _cache["at"] = time.monotonic()
        return vehicles
