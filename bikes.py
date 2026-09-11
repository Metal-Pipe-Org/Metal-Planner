"""Stacje Wrocławskiego Roweru Miejskiego (WRM) - kanał GBFS operatora.

WRM ma **otwarte, ustandaryzowane** dane: nextbike wystawia dla Wrocławia
kanał [GBFS](https://github.com/MobilityData/gbfs) 2.3 pod `system_id`
`nextbike_pl` - ten sam, który widnieje w oficjalnym rejestrze systemów
MobilityData (`systems.csv`, wiersz "WRM nextbike Poland"). Licencja podana
w `system_information` to **CC0-1.0**, więc - inaczej niż przy Siechnicach
(patrz siechnice.py i docs/SIECHNICE_DANE.md) - nie ma tu żadnego powodu,
żeby trzymać źródło wyłączone za zmienną środowiskową. Wyłącznik jest
mimo to (`WRM_ENABLED=off`), bo to cudzy serwer.

Bierzemy dwa z ośmiu kanałów:

- `station_information` - tożsamość i geometria stacji (id, nazwa, lat/lon,
  pojemność). Zmienia się rzadko, więc cache na godzinę.
- `station_status` - ile w tej chwili stoi rowerów i ile jest wolnych
  miejsc, plus flagi `is_renting`/`is_returning`. Kanał deklaruje `ttl` 60 s
  i tyle też trzymamy w cache - inaczej każde wyszukanie połączenia biłoby
  wprost w cudzy serwer (ta sama zasada co w vehicles.py).

Obu kanałów potrzeba naraz: stacja bez rowerów nie jest miejscem, z którego
da się wyjechać, a stacja bez wolnego miejsca - takim, w którym da się
rower oddać. Dlatego nie ma tu żadnej migawki na dysku „na wypadek awarii
sieci": sama geometria bez stanu i tak nie pozwoliłaby nic zaplanować,
a plan zbudowany na wczorajszej liczbie rowerów byłby gorszy niż brak planu.
Awaria kanału ma jeden skutek: propozycji z rowerem po prostu nie ma
(patrz `stations_quiet`), reszta wyszukiwarki działa bez zmian.

Model czasu (ile trwa dojście, odblokowanie i sam przejazd) też siedzi
tutaj, nie w plannerze: to wiedza o TYM środku transportu, a nie o
algorytmie, który go wplata w trasę.
"""

import json
import math
import os
import threading
import time
import urllib.request

GBFS_BASE = os.environ.get(
    "WRM_GBFS_URL",
    "https://gbfs.nextbike.net/maps/gbfs/v2/nextbike_pl/pl",
).rstrip("/")

INFO_URL = f"{GBFS_BASE}/station_information.json"
STATUS_URL = f"{GBFS_BASE}/station_status.json"

INFO_CACHE_SEC = 3600    # geometria stacji zmienia się w skali sezonu
STATUS_CACHE_SEC = 60    # tyle deklaruje sam kanał (pole `ttl`)

HTTP_TIMEOUT_SEC = 8     # krótko: to tylko DODATEK do wyszukiwania połączeń

# ---------------------------------------------------------- model czasu ----

# Rower miejski jest ciężki, ma trzy biegi i jeździ po mieście ze światłami -
# 14 km/h to prędkość PRZEJAZDU (od odbicia roweru do wpięcia go w stojak),
# a nie chwilowa prędkość jazdy.
BIKE_SPEED_KMH = 14.0
# Odległość w linii prostej to nie długość trasy. Mnożnik krętości dla ruchu
# rowerowego w mieście: przeprawy przez Odrę, jednokierunkowe, brak przejazdu
# na wprost przez tory.
BIKE_DETOUR = 1.35

WALK_SPEED_KMH = 4.5
WALK_DETOUR = 1.30       # chodniki i przejścia dla pieszych, nie linia prosta

# Margines na odblokowanie: podejście do stojaka, wypożyczenie w aplikacji
# albo na terminalu, wyjęcie roweru, ustawienie siodełka. Świadomie hojny -
# to jedyny etap trasy, w którym pasażer stoi przed maszyną, a nie czeka na
# rozkład, więc niedoszacowanie go przekłada się wprost na spóźniony
# autobus po drugiej stronie przejazdu.
UNLOCK_SEC = 180
# Zwrot: wpięcie roweru w stojak i potwierdzenie zakończenia wypożyczenia.
DOCK_SEC = 60

# Jak daleko wolno iść do stacji (i od stacji dalej). Powyżej pół kilometra
# dojście zjada tyle, ile sam przejazd rowerem miałby oszczędzić.
WALK_MAX_M = 500
# Poniżej pół kilometra W LINII PROSTEJ przejazd nie ma szans odrobić
# odblokowania i zwrotu - szybciej jest po prostu przejść.
MIN_RIDE_M = 500
# Sufit stoi na CZASIE PEDAŁOWANIA, nie na kilometrach: pół godziny to
# granica, za którą „dojazd rowerem miejskim do tramwaju" robi się osobną
# wycieczką (a pierwsze bezpłatne 20 minut WRM dawno się kończy). Sufit
# w metrach wyliczamy z niego, żeby dało się nim odsiewać pary stacji samą
# odległością, bez liczenia czegokolwiek.
MAX_RIDE_SEC = 30 * 60


def enabled():
    """Czy w ogóle wolno pytać operatora (WRM_ENABLED=off wyłącza)."""
    return os.environ.get("WRM_ENABLED", "on").strip().lower() != "off"


def haversine_m(lat1, lon1, lat2, lon2):
    """Odległość w linii prostej, w metrach."""
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _whole_minutes(seconds):
    """Sekundy zaokrąglone W GÓRĘ do pełnej minuty, minimum jedna.

    Wszystkie czasy tej warstwy przechodzą przez to zaokrąglenie i to jest
    wybór, nie niedbalstwo. Karta trasy pokazuje godziny z dokładnością do
    minuty, a obok nich - ile który etap trwa. Przy czasach liczonych co do
    sekundy te dwie rzeczy przestają się zgadzać: „13:03 przyjazd, 1 min
    dojścia, 13:03 odjazd", a suma trzech części przejazdu (odblokowanie
    + jazda + zwrot) nie równa się podanej długości etapu. W górę, a nie do
    najbliższej, bo każde z tych zaokrągleń jest MARGINESEM - pasażer, który
    się na nie nabierze, stoi na przystanku i patrzy na odjeżdżający autobus.
    """
    return max(60, -(-int(round(seconds)) // 60) * 60)


def walk_sec(straight_m):
    """Ile trwa dojście na dystansie `straight_m` mierzonym w linii prostej."""
    return _whole_minutes(straight_m * WALK_DETOUR / (WALK_SPEED_KMH / 3.6))


def ride_sec(straight_m):
    """Ile trwa SAM przejazd - bez odblokowania i bez zwrotu (patrz
    UNLOCK_SEC/DOCK_SEC; planner dolicza je osobno, żeby dało się je pokazać
    jako to, czym są)."""
    return _whole_minutes(straight_m * BIKE_DETOUR / (BIKE_SPEED_KMH / 3.6))


# Sufit czasu przeliczony na odległość w linii prostej - patrz MAX_RIDE_SEC.
MAX_RIDE_M = MAX_RIDE_SEC * (BIKE_SPEED_KMH / 3.6) / BIKE_DETOUR


def ride_distance_m(straight_m):
    """Szacowana długość trasy przejazdu (do pokazania „ok. 2,4 km")."""
    return straight_m * BIKE_DETOUR


# ------------------------------------------------------------- pobieranie ----

_lock = threading.Lock()
_cache = {"info": None, "info_at": 0.0, "status": None, "status_at": 0.0}


def _fetch(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "Metal-Planner/0.1 (+https://metal.sze.one)"}
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:
        return json.load(response)


def _cached(key, url, max_age_sec):
    """Pobrane `url`, nie częściej niż raz na `max_age_sec`.

    Wołane pod `_lock`, więc dwa równoległe zapytania nie pobiorą tego
    samego kanału dwa razy.
    """
    now = time.monotonic()
    if _cache[key] is not None and now - _cache[f"{key}_at"] < max_age_sec:
        return _cache[key]
    payload = _fetch(url)
    _cache[key] = payload
    _cache[f"{key}_at"] = now
    return payload


def _stations_from(info, status):
    """Złożenie obu kanałów w jedną listę stacji gotowych do planowania.

    Stacja bez pary w drugim kanale wypada: bez `station_status` nie wiadomo,
    czy stoi w niej choć jeden rower, a bez `station_information` nie wiadomo,
    gdzie ona w ogóle jest.
    """
    state = {
        row.get("station_id"): row
        for row in status.get("data", {}).get("stations", [])
    }
    stations = []
    for row in info.get("data", {}).get("stations", []):
        live = state.get(row.get("station_id"))
        if live is None:
            continue
        lat, lon = row.get("lat"), row.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        stations.append({
            "id": str(row["station_id"]),
            "name": (row.get("name") or "").strip() or f"Stacja {row['station_id']}",
            "lat": float(lat),
            "lon": float(lon),
            "bikes": int(live.get("num_bikes_available") or 0),
            "docks": int(live.get("num_docks_available") or 0),
            # `is_installed` odróżnia stację zdjętą na zimę od pustej.
            "renting": bool(live.get("is_renting", True))
                       and bool(live.get("is_installed", True)),
            "returning": bool(live.get("is_returning", True))
                         and bool(live.get("is_installed", True)),
        })
    return stations


def stations():
    """Aktualny stan wszystkich stacji WRM.

    Rzuca wyjątkiem sieciowym (OSError, w tym urllib.error.URLError i timeout)
    albo ValueError przy odpowiedzi, która nie jest JSON-em - tak jak
    vehicles.get_vehicles(). Kto woła z wnętrza wyszukiwarki, powinien użyć
    `stations_quiet`."""
    if not enabled():
        return []
    with _lock:
        info = _cached("info", INFO_URL, INFO_CACHE_SEC)
        status = _cached("status", STATUS_URL, STATUS_CACHE_SEC)
    return _stations_from(info, status)


def stations_quiet():
    """To samo, ale awaria kanału to pusta lista, nie wyjątek.

    Rower jest DODATKIEM do wyszukiwania połączeń: gdy cudzy serwer nie
    odpowiada, wynik ma być zwykłą listą tras komunikacją miejską, a nie
    komunikatem o błędzie.
    """
    try:
        return stations()
    except (OSError, ValueError, KeyError):
        return []
