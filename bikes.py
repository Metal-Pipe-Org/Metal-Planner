"""Stacje Wrocławskiego Roweru Miejskiego (WRM) - kanał GBFS operatora.

WRM ma **otwarte, ustandaryzowane** dane: nextbike wystawia dla Wrocławia
kanał [GBFS](https://github.com/MobilityData/gbfs) 2.3 pod `system_id`
`nextbike_pl` - ten sam, który widnieje w oficjalnym rejestrze systemów
MobilityData (`systems.csv`, wiersz "WRM nextbike Poland"). Licencja podana
w `system_information` to **CC0-1.0**, więc - inaczej niż przy Siechnicach
(patrz siechnice.py i docs/SIECHNICE_DANE.md) - nie ma tu żadnego powodu,
żeby trzymać źródło wyłączone za zmienną środowiskową. Wyłącznik jest
mimo to (`WRM_ENABLED=off`), bo to cudzy serwer.

Bierzemy trzy z ośmiu kanałów:

- `station_information` - tożsamość i geometria stacji (id, nazwa, lat/lon,
  pojemność). Zmienia się rzadko, więc cache na godzinę.
- `station_status` - ile w tej chwili stoi rowerów i ile jest wolnych
  miejsc, plus flagi `is_renting`/`is_returning`. Kanał deklaruje `ttl` 60 s
  i tyle też trzymamy w cache - inaczej każde wyszukanie połączenia biłoby
  wprost w cudzy serwer (ta sama zasada co w vehicles.py).
- `free_bike_status` - rowery stojące POZA stacją. We Wrocławiu jest ich
  kilkaset naraz i da się je wypożyczyć jak każdy inny, więc jako miejsce
  startu przejazdu liczą się na równi ze stacją. Jako miejsce ZWROTU nie:
  nextbike liczy za zostawienie roweru poza stacją osobną, wysoką opłatę,
  więc przejazd zawsze kończy się na stacji.

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

import gtfs

GBFS_BASE = os.environ.get(
    "WRM_GBFS_URL",
    "https://gbfs.nextbike.net/maps/gbfs/v2/nextbike_pl/pl",
).rstrip("/")

INFO_URL = f"{GBFS_BASE}/station_information.json"
STATUS_URL = f"{GBFS_BASE}/station_status.json"
FREE_URL = f"{GBFS_BASE}/free_bike_status.json"
TYPES_URL = f"{GBFS_BASE}/vehicle_types.json"

INFO_CACHE_SEC = 3600    # geometria stacji zmienia się w skali sezonu
STATUS_CACHE_SEC = 60    # tyle deklaruje sam kanał (pole `ttl`)
TYPES_CACHE_SEC = 3600   # katalog modeli roweru - zmienia się w skali lat

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

# --------------------------------------- model czasu po stronie MAPY ----

# Mapa liczy przejazd JEDNĄ prędkością zamiast pary "prędkość × krętość".
# To ta sama liczba, tylko już policzona: 14 km/h realnej jazdy podzielone
# przez 1,35 krętości miasta daje 10,4 km/h w linii prostej, a bierzemy
# równe 10 - zaokrąglenie w dół, bo w tę stronę zaokrągla się cały czas
# nierozkładowy w tym projekcie (patrz gtfs.walk_time_sec).
#
# Rozbicie na dwa czynniki miałoby sens, gdyby mapa znała przebieg trasy.
# Nie zna - zna wyłącznie odległość w linii prostej - więc dwie liczby
# udawałyby wiedzę, której nie ma. Jedna prędkość mówi dokładnie tyle, ile
# wiadomo: kilometr w linii prostej to sześć minut pedałowania.
MAP_RIDE_MPS = 10.0 * 1000 / 3600

# Stały narzut przejazdu, niezależny od długości: wypożyczenie w aplikacji,
# wyjęcie roweru z blokady, na drugim końcu wpięcie i potwierdzenie.
# Trzymany jako jedna liczba, bo w dymku i tak pokazujemy jeden czas
# przejazdu, a nie rachunek z trzech pozycji.
MAP_OVERHEAD_SEC = 2 * 60


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
_cache = {}      # klucz kanału -> payload, klucz + "_at" -> monotonic


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
    if _cache.get(key) is not None and now - _cache.get(f"{key}_at", 0.0) < max_age_sec:
        return _cache[key]
    payload = _fetch(url)
    _cache[key] = payload
    _cache[f"{key}_at"] = now
    return payload


def _electric_ids(types):
    """Identyfikatory modeli ze wspomaganiem elektrycznym.

    Kanał `vehicle_types` opisuje każdy model osobno; interesuje nas jedno
    pole - `propulsion_type`. Nazwy modeli ("E-Bike", "e-SMARTbike 2.0
    RFID") czytać się nie da, bo to marketing operatora, a lista modeli
    rośnie z każdą dostawą.
    """
    return {
        str(row.get("vehicle_type_id"))
        for row in types.get("data", {}).get("vehicle_types", [])
        if str(row.get("propulsion_type", "")).startswith("electric")
    }


def _electric_count(live, electric_ids):
    """Ile z rowerów na stacji to elektryki (0, gdy kanał nie mówi)."""
    return sum(
        int(row.get("count") or 0)
        for row in (live.get("vehicle_types_available") or [])
        if str(row.get("vehicle_type_id")) in electric_ids
    )


def _stations_from(info, status, electric_ids=frozenset()):
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
            "electric": _electric_count(live, electric_ids),
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
        types = _cached("types", TYPES_URL, TYPES_CACHE_SEC)
    return _stations_from(info, status, _electric_ids(types))


def free_bikes():
    """Rowery stojące POZA stacją: [{id, lat, lon, electric}, ...].

    Wypożycza się je tak samo jak te ze stojaka, więc jako początek przejazdu
    liczą się na równi ze stacją. Nie są jednak miejscem ZWROTU - stąd brak
    pola `returning` i osobna lista zamiast dorzucenia ich do `stations()`.

    Rower zarezerwowany albo zepsuty odpada: `is_reserved`/`is_disabled` to
    jedyne dwa stany, w których rower stoi, ale nie jest dla nikogo dostępny.
    """
    if not enabled():
        return []
    with _lock:
        free = _cached("free", FREE_URL, STATUS_CACHE_SEC)
        types = _cached("types", TYPES_URL, TYPES_CACHE_SEC)
    electric_ids = _electric_ids(types)
    out = []
    for row in free.get("data", {}).get("bikes", []):
        # Rower Z przypisaną stacją stoi w stojaku i jest już policzony
        # w `station_status` - wpuszczony tutaj, byłby na mapie drugi raz.
        if row.get("station_id") or row.get("is_reserved") or row.get("is_disabled"):
            continue
        lat, lon = row.get("lat"), row.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        out.append({
            "id": str(row.get("bike_id")),
            "lat": float(lat),
            "lon": float(lon),
            "electric": str(row.get("vehicle_type_id")) in electric_ids,
        })
    return out


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


# ------------------------------------------ rower na mapie przepływów ----

# Sufit przejazdu przeliczony na linię prostą już MAPOWĄ prędkością
# (patrz MAP_RIDE_MPS) - pół godziny pedałowania to jakieś pięć kilometrów.
MAP_MAX_RIDE_M = MAX_RIDE_SEC * MAP_RIDE_MPS

# Bok komórki siatki: dokładnie promień dojścia, żeby dowolny punkt miał
# wszystkich swoich sąsiadów w dziewięciu komórkach wokół siebie.
_CELL_LAT = gtfs.WALK_M / 111_320
_CELL_LON = gtfs.WALK_M / 71_000     # 111 320 * cos(51,1°) - szerokość Wrocławia


def _cells(day, times):
    """Słupki z `times` wrzucone do siatki, żeby dało się pytać "co obok".

    Bez tego każda ze stacji przemiatałaby wszystkie kilka tysięcy słupków
    w oknie mapy, a stacji jest we Wrocławiu blisko trzysta.
    """
    grid = {}
    for stop in times:
        coords = day.stop_coords.get(stop)
        if coords is None:
            continue
        lat, lon = coords
        key = (int(lat / _CELL_LAT), int(lon / _CELL_LON))
        grid.setdefault(key, []).append((stop, lat, lon))
    return grid


def _walk_index(grid, lat, lon):
    """Słupki w zasięgu jednego dojścia, od najbliższego: [(sek, m, słupek)].

    Ten sam promień i ta sama prędkość marszu, co przy każdym innym przejściu
    na mapie (punkt 14 kontraktu) - rower nie dostaje własnej, hojniejszej
    miary tylko dlatego, że jest rowerem.
    """
    i, j = int(lat / _CELL_LAT), int(lon / _CELL_LON)
    near = []
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for stop, slat, slon in grid.get((i + di, j + dj), ()):
                metres = haversine_m(slat, slon, lat, lon)
                if metres <= gtfs.WALK_M:
                    near.append((gtfs.walk_time_sec(metres), round(metres), stop))
    near.sort()
    return near


def ride_time_sec(straight_m):
    """Ile trwa przejazd rowerem między dwoma punktami - z narzutem.

    Jedyna liczba, jaką mapa o rowerze ZGADUJE. Wolno ją zgadywać z tego
    samego powodu, dla którego wolno zgadywać marsz (punkt 10 kontraktu):
    rower rozkładu nie ma, więc nie ma czego odczytać. Zakaz szacowania
    dotyczy pojazdów, które rozkład mają.
    """
    return MAP_OVERHEAD_SEC + _whole_minutes(straight_m / MAP_RIDE_MPS)


def map_places(day, reach, board, live=True):
    """Rowery na mapie przepływów: [{lat, lon, ..., rides: [...]}, ...].

    Dwa wejścia i obydwa pochodzą z tego, co mapa RYSUJE, nie z całego miasta:

    - `reach` - {słupek: godzina, o której mapa tu DOWOZI} (plus sam start).
      Stąd wiadomo, o której da się być PRZY rowerze: dojazd plus jedno
      dojście. Rower, do którego mapa nie dowozi, na mapie nie staje.
    - `board` - {słupek: najpóźniejsza godzina, o której mapa pozwala tu
      WSIĄŚĆ w coś, co jedzie dalej} (plus krańce relacji, liczone do
      deadline'u). Stąd wiadomo, czy przejazd ma SENS.

    I to jest cała reguła sensu: przejazd zostaje, jeżeli po zsiadaniu zdąży
    się jeszcze wsiąść w coś, co mapa rysuje - albo dojechać pod sam cel.
    Przejazd NIE musi być szybszy od tramwaju: ktoś może chcieć jechać
    rowerem właśnie dlatego, że woli rower. Musi natomiast do czegoś
    prowadzić, i to do czegoś WIDOCZNEGO - przystanek, z którego mapa nie
    rysuje ani jednego odjazdu, niczego nie otwiera, choćby dało się do
    niego dojść.

    Na mapie stają wyłącznie miejsca, w których da się WSIĄŚĆ NA ROWER:
    stacja z rowerami albo rower stojący luzem, do których mapa dowozi i
    z których prowadzi choć jeden sensowny przejazd. Drugi koniec przejazdu
    kropki sam z siebie nie dostaje - pokazuje się razem ze strzałką, pod
    kursorem (a gdy sam jest takim miejscem, jest na mapie z własnego tytułu).

    Przejazd zaczyna się na stacji ALBO przy rowerze stojącym luzem, a kończy
    zawsze na stacji (patrz free_bikes). Przy pytaniu o inny dzień (`live`
    False) stan stojaków jest nieznany: kropki zostają, bo stacje stoją tam
    zawsze, ale liczby rowerów nie ma i nie udajemy, że jest.
    """
    if not enabled() or not reach or not board:
        return []
    try:
        docks = stations()
        loose = free_bikes() if live else []
    except (OSError, ValueError, KeyError):
        return []

    # Dokąd wolno przyjechać. Przy nieznanym stanie stojaków bierzemy każdą
    # stację: "nie wiadomo, czy przyjmie rower" to nie to samo, co "nie
    # przyjmie", a kropka i tak powie wprost, że stanu nie znamy.
    targets = [s for s in docks if s["returning"] or not live]
    if not targets:
        return []

    reach_cells = _cells(day, reach)
    board_cells = _cells(day, board)
    # Co która stacja otwiera, policzone RAZ na stację - te same słupki
    # wychodziłyby inaczej przy każdym z kilkudziesięciu przejazdów do niej.
    opens = {s["id"]: _walk_index(board_cells, s["lat"], s["lon"])
             for s in targets}

    starts = [s for s in docks if s["bikes"] > 0 or not live]
    starts += [{**bike, "name": None, "bikes": 1,
                "electric": 1 if bike["electric"] else 0} for bike in loose]

    out = []
    for place in starts:
        at = _arrival_at(day, reach_cells, reach, place)
        if at is None:
            continue
        rides = _rides_from(day, at["at"], place, targets, opens, board)
        if not rides:
            continue
        out.append({
            "id": place["id"],
            "name": place.get("name"),
            "lat": place["lat"],
            "lon": place["lon"],
            "bikes": place["bikes"],
            "electric": place["electric"],
            "docks": place.get("docks"),
            # Rower luzem nie ma nazwy ani stojaka - front rysuje go inaczej
            # i inaczej o nim pisze ("rower luzem", nie "stacja").
            "loose": place.get("name") is None,
            **at,
            "rides": rides,
        })
    out.sort(key=lambda place: (place["at"], -place["bikes"]))
    return out


def _arrival_at(day, reach_cells, reach, place):
    """{at, from, walk_sec, walk_m} - o której i skąd się tu dochodzi."""
    best = None
    for walk_sec_, metres, stop in _walk_index(reach_cells, place["lat"],
                                               place["lon"]):
        option = (reach[stop] + walk_sec_, walk_sec_, metres, stop)
        if best is None or option < best:
            best = option
    if best is None:
        return None
    at, walk_sec_, metres, stop = best
    return {"at": at, "from": day.stop_names.get(stop, stop),
            "walk_sec": walk_sec_, "walk_m": metres}


def _rides_from(day, at, place, targets, opens, board):
    """Wszystkie sensowne przejazdy z tego miejsca, od najwcześniejszego."""
    rides = []
    for target in targets:
        if target["id"] == place["id"]:
            continue
        metres = haversine_m(place["lat"], place["lon"],
                             target["lat"], target["lon"])
        # Dolny próg: poniżej pół kilometra samo odblokowanie i wpięcie trwa
        # dłużej, niż idzie się na piechotę. Górny: pół godziny pedałowania
        # to już osobna wycieczka, nie dojazd do tramwaju.
        if not MIN_RIDE_M <= metres <= MAP_MAX_RIDE_M:
            continue
        ride_sec_ = ride_time_sec(metres)
        there = at + ride_sec_
        opened = _first_open(day, opens[target["id"]], there, board)
        if opened is None:
            continue
        rides.append({
            "id": target["id"],
            "name": target["name"],
            "lat": target["lat"],
            "lon": target["lon"],
            "bikes": target["bikes"],
            "docks": target["docks"],
            "m": round(metres),
            "sec": ride_sec_,
            "at": there,
            **opened,
        })
    rides.sort(key=lambda ride: (ride["at"], ride["m"]))
    return rides


def _first_open(day, index, there, board):
    """Najbliższy słupek, na którym po zsiadaniu jeszcze się ZDĄŻY wsiąść.

    Najbliższy, bo po zsiadaniu idzie się do najbliższego - a dalszy, na
    który też by się zdążyło, nie jest przez ten przejazd "otwarty" bardziej.
    `board` niesie wyłącznie słupki, z których mapa RYSUJE odjazd jadący
    dalej (plus krańce relacji), więc "zdąży" nie może tu znaczyć "zdąży na
    przystanek, z którego nic nie widać".
    """
    for walk_sec_, metres, stop in index:
        when = there + walk_sec_
        if when <= board[stop]:
            return {"opens": day.stop_names.get(stop, stop), "opens_at": when,
                    "opens_last": board[stop], "opens_walk_sec": walk_sec_,
                    "opens_m": metres}
    return None
