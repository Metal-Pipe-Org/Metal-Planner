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

Model czasu przejazdu też siedzi tutaj, nie w plannerze: to wiedza o TYM
środku transportu, a nie o algorytmie, który go wplata w trasę. Model jest
jeden, dla mapy i dla propozycji tras (zgłoszenie #151): wcześniej propozycje
liczyły osobno, z inną prędkością, innym marszem i innym narzutem. Dojście do
stacji nie jest tu liczone wcale - to zwykły marsz (gtfs.walk_time_sec).
"""

import json
import math
from bisect import bisect_left
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

# Poniżej pół kilometra W LINII PROSTEJ przejazd nie ma szans odrobić
# wypożyczenia i zwrotu - szybciej jest po prostu przejść.
MIN_RIDE_M = 500
# Sufit stoi na CZASIE PEDAŁOWANIA, nie na kilometrach: pół godziny to
# granica, za którą „dojazd rowerem miejskim do tramwaju" robi się osobną
# wycieczką (a pierwsze bezpłatne 20 minut WRM dawno się kończy). Sufit
# w metrach wyliczamy z niego, żeby dało się nim odsiewać pary stacji samą
# odległością, bez liczenia czegokolwiek.
MAX_RIDE_SEC = 30 * 60

# Przejazd liczymy JEDNĄ prędkością zamiast pary "prędkość × krętość".
# To ta sama liczba, tylko już policzona: 14 km/h realnej jazdy podzielone
# przez 1,35 krętości miasta daje 10,4 km/h w linii prostej, a bierzemy
# równe 10 - zaokrąglenie w dół, bo w tę stronę zaokrągla się cały czas
# nierozkładowy w tym projekcie (patrz gtfs.walk_time_sec).
#
# Rozbicie na dwa czynniki miałoby sens, gdybyśmy znali przebieg trasy.
# Nie znamy - znamy wyłącznie odległość w linii prostej - więc dwie liczby
# udawałyby wiedzę, której nie ma. Jedna prędkość mówi dokładnie tyle, ile
# wiadomo: kilometr w linii prostej to sześć minut pedałowania.
MAP_RIDE_MPS = 10.0 * 1000 / 3600

# Stały narzut przejazdu, niezależny od długości: wypożyczenie w aplikacji,
# wyjęcie roweru z blokady, na drugim końcu wpięcie i potwierdzenie.
# Trzymany jako jedna liczba, bo w dymku i tak pokazujemy jeden czas
# przejazdu, a nie rachunek z trzech pozycji.
MAP_OVERHEAD_SEC = 2 * 60

# Krętość miasta dla roweru: przeprawy przez Odrę, jednokierunkowe, brak
# przejazdu na wprost przez tory. Już tylko do POKAZANIA długości trasy
# („ok. 2,4 km") - czas liczy się z MAP_RIDE_MPS, w którym krętość siedzi.
BIKE_DETOUR = 1.35


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


# Sufit czasu przeliczony na odległość w linii prostej - patrz MAX_RIDE_SEC.
MAX_RIDE_M = MAX_RIDE_SEC * MAP_RIDE_MPS


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


def _walk_index(grid, lat, lon, mps):
    """Słupki w zasięgu jednego dojścia, od najbliższego: [(sek, m, słupek)].

    Ten sam promień i ta sama prędkość marszu, co przy każdym innym przejściu
    na mapie (punkt 14 kontraktu) - rower nie dostaje własnej, hojniejszej
    miary tylko dlatego, że jest rowerem. `mps` to tempo pytającego
    (day.walk_mps).
    """
    i, j = int(lat / _CELL_LAT), int(lon / _CELL_LON)
    near = []
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for stop, slat, slon in grid.get((i + di, j + dj), ()):
                metres = haversine_m(slat, slon, lat, lon)
                if metres <= gtfs.WALK_M:
                    near.append((gtfs.walk_time_sec(metres, mps), round(metres), stop))
    near.sort()
    return near


def ride_time_sec(straight_m, mps=MAP_RIDE_MPS, overhead_sec=MAP_OVERHEAD_SEC):
    """Ile trwa przejazd rowerem między dwoma punktami - z narzutem.

    Jedyna liczba, jaką mapa o rowerze ZGADUJE. Wolno ją zgadywać z tego
    samego powodu, dla którego wolno zgadywać marsz (punkt 10 kontraktu):
    rower rozkładu nie ma, więc nie ma czego odczytać. Zakaz szacowania
    dotyczy pojazdów, które rozkład mają. Prędkość i narzut pytający może
    ustawić sobie pod zębatką (zgłoszenie #151) - domyślne to te wyżej.
    """
    return overhead_sec + _whole_minutes(straight_m / mps)


def _ma_rodzaj(place, electric, regular):
    """Czy w tym miejscu stoi choć jeden rower WŁĄCZONEGO rodzaju.

    Kanał podaje, ile rowerów stoi razem i ile z nich to elektryki (patrz
    _electric_count); zwykłe to reszta. Rower luzem ma tę samą parę liczb,
    tylko zawsze o jednej sztuce."""
    elektryki = place.get("electric") or 0
    if electric and elektryki > 0:
        return True
    return regular and (place["bikes"] - elektryki) > 0


def map_places(day, arrivals, onward, target_set, limit, live=True,
               min_level=0, electric=True, regular=True, ride_mps=MAP_RIDE_MPS,
               overhead_sec=MAP_OVERHEAD_SEC):
    """Rowery na mapie przepływów (punkt 16): [{lat, lon, ..., rides: [...]}, ...].

    Kandydatem jest PRZEJAZD - stąd do konkretnej stacji - oceniany w całej
    podróży, bo z samego odcinka wiadomo tylko, jak jest długi. Obie połowy
    podróży idą tym, co mapa RYSUJE, a nie całym miastem, i przychodzą
    z plannera jako funkcje: ich policzenie to przejście po całej mapie, więc
    woła się je dopiero wtedy, gdy jest dla kogo.

    - `arrivals()` - {słupek: [(godzina, pojazdy), ...]}: o której mapa tu
      dowozi, osobno dla każdej liczby pojazdów (plus sam start z zerem).
      Stąd wiadomo, o której da się być PRZY rowerze: dojazd plus jedno
      dojście. Rower, do którego mapa nie dowozi, na mapie nie staje.
    - `onward()` - {słupek: [(odjazd, w celu o, pojazdy), ...]}: wsiadając tu
      w ten narysowany odjazd, o której jest się w celu. Przystanek, z którego
      mapa nie rysuje ani jednego odjazdu, niczego nie otwiera.
    - `target_set` - słupki celu: dojechać rowerem pod sam cel to koniec
      podróży, bez pojazdu po rowerze.

    Przejazd NIE musi być szybszy od tramwaju: ktoś może chcieć jechać
    rowerem właśnie dlatego, że woli rower - dlatego godzina przy rowerze jest
    osobnym kryterium. Nie musi też dowozić przed progiem mapy: próg jest
    progiem kursów z rozkładem, a o rowerze decydują jego trzy liczby.

    Wybór idzie w dwóch etapach. Najpierw STACJE (miejsca z rowerem, także
    rower luzem) - _skyband, ta sama reguła co przy autach, na `limit`
    stacji; stacja bije drugą, gdy daje każdą jej podróż co najmniej tak samo
    dobrze (_covers). Potem każda wybrana stacja osobno decyduje, które swoje
    przejazdy pokazać (_station_rides) - ich łączna liczba nie ma znaczenia,
    bo widać je dopiero pod kursorem. Drugi koniec
    przejazdu kropki sam z siebie nie dostaje - pokazuje się razem z kreską
    (a gdy sam jest początkiem wybranego przejazdu, jest na mapie z własnego
    tytułu).

    `electric`/`regular` to rodzaj roweru, na który pasażer chce wsiąść (dwa
    przyciski w pasku warstw, patrz app.js setBikeKind): miejsce jest
    kandydatem, gdy stoi w nim choć jeden rower włączonego rodzaju. Dotyczy
    to WSIADANIA - stacja, na której przejazd się kończy, żadnego roweru mieć
    nie musi.

    Przejazd zaczyna się na stacji ALBO przy rowerze stojącym luzem, a kończy
    zawsze na stacji (patrz free_bikes). Przy pytaniu o inny dzień (`live`
    False) stan stojaków jest nieznany: kropki zostają, bo stacje stoją tam
    zawsze, ale liczby rowerów nie ma i nie udajemy, że jest.
    """
    if not enabled():
        return []
    # Oba rodzaje odhaczone to to samo, co zgaszony rower - nie ma na czym
    # wsiąść, więc nie ma czego liczyć.
    if not (electric or regular):
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

    starts = [s for s in docks if s["bikes"] > 0 or not live]
    starts += [{**bike, "name": None, "bikes": 1,
                "electric": 1 if bike["electric"] else 0} for bike in loose]

    # Rodzaj roweru to osobny wybór, nie ranking (zgłoszenie #147): kto chce
    # elektryka, nie weźmie zwykłego, i odwrotnie. Miejsce zostaje, gdy stoi
    # w nim choć jeden rower włączonego rodzaju - a odsiew idzie PRZED
    # wyborem przejazdów, nie po nim, żeby suwak dostał tyle kandydatów,
    # ile obiecuje, i żeby nie zniknął zwycięzca, zostawiając gorszego.
    #
    # Przy nieznanym stanie stojaków (`live` False) nie odsiewamy nic:
    # nie wiadomo, co tam wtedy stoi, a udawanie tej wiedzy jest gorsze niż
    # pokazanie kropki, która wprost mówi, że stanu nie zna.
    if live and not (electric and regular):
        starts = [s for s in starts if _ma_rodzaj(s, electric, regular)]

    reach = arrivals()
    reach_cells = _cells(day, reach)
    reachable = []
    for place in starts:
        before = _before_bike(reach_cells, reach, place, day.walk_mps)
        if before:
            reachable.append((place, before))
    if not reachable:
        return []

    ahead = onward()
    ahead_cells = _cells(day, set(ahead) | set(target_set))
    # Dalsza droga policzona RAZ na stację - te same słupki wychodziłyby
    # inaczej przy każdym z kilkudziesięciu przejazdów do niej.
    after = {}

    found = []
    for place, before in reachable:
        rides = []
        for target in targets:
            if target["id"] == place["id"]:
                continue
            metres = haversine_m(place["lat"], place["lon"],
                                 target["lat"], target["lon"])
            if target["id"] not in after:
                after[target["id"]] = _after_bike(ahead_cells, ahead,
                                                  target_set, target, day.walk_mps)
            ride_sec_ = ride_time_sec(metres, ride_mps, overhead_sec)
            options = _journeys(before, ride_sec_, after[target["id"]])
            if not options:
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
                "at": before[0][0] + ride_sec_,
                "options": options,
            })
        if rides:
            found.append((place, rides))

    # Etap 1: które STACJE (punkt 16). Stację opisują podróże przez nią, każda
    # trzema liczbami z tej samej drogi (_shown_as) - "o której przy rowerze"
    # mówi coś o stacji, nie o przejeździe z niej.
    fronts = [_front([_shown_as(option) for ride in rides
                      for option in ride["options"]])
              for _, rides in found]
    beaten_by = _station_beaten_by(fronts)
    chosen = _skyband([len(b) for b in beaten_by], limit, min_level)

    out = []
    for i, (place, all_rides) in enumerate(found):
        if i not in chosen:
            continue
        # Etap 2: co pokazać NA tej stacji - niezależnie od innych stacji,
        # bo przejazdy i tak widać dopiero pod kursorem. Na razie: przejazdy,
        # których nie bije inny przejazd stąd (godzina w celu, pojazdy).
        rides = _station_rides(all_rides)
        at = _arrival_at(day, reach_cells, reach, place)
        rides.sort(key=lambda ride: (ride["at"], ride["m"]))
        mine = fronts[i]
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
            # Debug pod zębatką: dlaczego ta stacja przeszła wybór.
            "why": {
                "records": [name for k, name in enumerate(WHY_CRITERIA)
                            if min(t[k] for t in mine)
                            == min(t[k] for front in fronts for t in front)],
                "beaten": len(beaten_by[i]),
                "beaten_by": [found[j][0].get("name") or "rower luzem"
                              for j in beaten_by[i][:3]],
                "of": len(found),
            },
        })
    out.sort(key=lambda place: (place["at"], -place["bikes"]))
    return out


def _arrival_at(day, reach_cells, reach, place):
    """{at, from, walk_sec, walk_m} - najwcześniej, o której i skąd się tu
    dochodzi."""
    best = None
    for walk_sec_, metres, stop in _walk_index(reach_cells, place["lat"],
                                               place["lon"], day.walk_mps):
        option = (min(when for when, _ in reach[stop]) + walk_sec_,
                  walk_sec_, metres, stop)
        if best is None or option < best:
            best = option
    if best is None:
        return None
    at, walk_sec_, metres, stop = best
    return {"at": at, "from": day.stop_names.get(stop, stop),
            "walk_sec": walk_sec_, "walk_m": metres}


def _pareto(pairs):
    """Z par (godzina, pojazdy) te, których żadna inna nie bije: wcześniej
    albo mniej pojazdów. Rosnąco po godzinie."""
    kept = []
    for when, rides in sorted(pairs):
        if not kept or rides < kept[-1][1]:
            kept.append((when, rides))
    return kept


def _before_bike(reach_cells, reach, place, mps):
    """[(o której przy rowerze, iloma pojazdami), ...] - dojazd plus jedno
    dojście, tylko pary, których nic nie bije."""
    return _pareto(
        (when + walk_sec_, rides)
        for walk_sec_, _, stop in _walk_index(reach_cells, place["lat"],
                                              place["lon"], mps)
        for when, rides in reach[stop])


def _after_bike(ahead_cells, ahead, target_set, station, mps):
    """Dalsza droga ze stacji oddania: (dojście pod sam cel, schodki).

    Dojście pod cel to sekundy marszu do najbliższego słupka celu (None, gdy
    cel jest dalej niż jedno dojście). Schodki to dla każdej liczby pojazdów
    k: rosnące "najpóźniej zsiąść z roweru, żeby zdążyć" i najwcześniejszy
    przyjazd do celu z pojazdami najwyżej k - odczyt to jedno wyszukanie
    binarne na przejazd, a nie przegląd odjazdów."""
    finish = None
    by_rides = {}
    for walk_sec_, _, stop in _walk_index(ahead_cells, station["lat"],
                                          station["lon"], mps):
        if stop in target_set and (finish is None or walk_sec_ < finish):
            finish = walk_sec_
        for dep_t, arrival, rides in ahead.get(stop, ()):
            by_rides.setdefault(rides, []).append((dep_t - walk_sec_, arrival))
    steps = []
    rows = []
    for rides in sorted(by_rides):
        rows = sorted(rows + by_rides[rides])
        running, best = float("inf"), []
        for _, arrival in reversed(rows):
            running = min(running, arrival)
            best.append(running)
        steps.append((rides, [latest for latest, _ in rows], best[::-1]))
    return finish, steps


def _journeys(before, ride_sec_, after):
    """Podróże przez ten przejazd: [{bike_at, arrival, vehicles}, ...] - każda
    trójka to jedna prawdziwa droga (dojazd, rower, dalsza droga), a nie
    najlepsza godzina z jednej i najmniej pojazdów z drugiej. `bike_at` to
    godzina przy rowerze NA TEJ drodze. Tylko te, których nic nie bije."""
    finish, steps = after
    found = []
    for at, rides_before in before:
        there = at + ride_sec_
        if finish is not None:
            found.append((at, there + finish, rides_before))
        for rides_after, latest, best in steps:
            i = bisect_left(latest, there)
            if i < len(latest):
                found.append((at, best[i], rides_before + rides_after))
    kept = []
    for trio in sorted(set(found)):
        if not any(all(k <= t for k, t in zip(other, trio)) for other in kept):
            kept.append(trio)
    return [{"bike_at": at, "arrival": when, "vehicles": rides}
            for at, when, rides in kept]


def _shown_as(option):
    """Trzy liczby, którymi przejazd się porównuje, z dokładnością, z jaką
    mapa je wypisuje (app.js: fmtClock) - zwrócone tak, że mniej znaczy
    lepiej: o której jest się przy rowerze, o której w celu i iloma pojazdami.
    Wcześniej przy rowerze jest lepiej, bo ma się pokazać rower dla kogoś, kto
    po prostu chce jechać rowerem - najszybciej osiągalny ze startu, a nie
    taki, do którego najpierw idzie się w złą stronę (tak robiło dawne
    kryterium "jak najdłuższa jazda", zgłoszone 2026-09-25). Pojazdów nie
    przelicza się na minuty - kosztem przesiadki jest ryzyko, którego rozkład
    nie zawiera. Sam rower pojazdem nie jest."""
    return (
        (option["bike_at"] + 30) // 60,
        (option["arrival"] + 30) // 60,
        option["vehicles"],
    )


# Nazwy trzech liczb z _shown_as - do podglądu "dlaczego ten rower" (Debug).
WHY_CRITERIA = ("najwcześniej przy rowerze", "najwcześniej w celu",
                "najmniej pojazdów")


def _front(trios):
    """Trójki (przy rowerze, w celu, pojazdy), których nic spośród nich nie
    bije - podróże, którymi stacja naprawdę się broni."""
    kept = []
    for trio in sorted(set(trios)):
        if not any(all(k <= t for k, t in zip(other, trio)) for other in kept):
            kept.append(trio)
    return kept


def _covers(one, other):
    """Czy stacja `one` daje każdą podróż stacji `other` co najmniej tak samo
    dobrze - we wszystkich trzech liczbach naraz, podróż do podróży. Nigdy
    najlepszej godziny z jednej drogi i najmniej pojazdów z drugiej."""
    return all(any(all(a <= b for a, b in zip(mine, theirs)) for mine in one)
               for theirs in other)


def _station_beaten_by(fronts):
    """[[indeksy stacji, które bije stację i], ...]. Stacja bije drugą, gdy
    daje każdą jej podróż co najmniej tak samo dobrze, a ona jej nie.

    Najpierw tani odsiew: pokrycie wymaga, żeby w każdej z trzech liczb
    najlepsza wartość pokrywającej nie była gorsza - bez niego to kilkaset
    stacji razy kilkaset pełnych porównań."""
    best = [tuple(min(t[k] for t in front) for k in range(3)) for front in fronts]
    out = []
    for i, mine in enumerate(fronts):
        out.append([j for j, other in enumerate(fronts)
                    if j != i and all(o <= m for o, m in zip(best[j], best[i]))
                    and _covers(other, mine) and not _covers(mine, other)])
    return out


def _station_rides(rides):
    """Przejazdy z jednej stacji, z opcjami, których nie bije inna opcja
    z TEJ stacji: w celu nie później (co do wypisanej minuty) i nie więcej
    pojazdów. Godziny przy rowerze tu nie ma - mówi o stacji, nie o
    przejeździe. Remisy zostają: to osobny, jeszcze nierozstrzygnięty
    problem (kilka prawie takich samych przejazdów z jednej stacji)."""
    pary = [((o["arrival"] + 30) // 60, o["vehicles"])
            for ride in rides for o in ride["options"]]
    out = []
    for ride in rides:
        kept = [o for o in ride["options"]
                if not any(p != ((o["arrival"] + 30) // 60, o["vehicles"])
                           and p[0] <= (o["arrival"] + 30) // 60
                           and p[1] <= o["vehicles"] for p in pary)]
        if kept:
            out.append({**ride, "options": kept})
    return out


def _skyband(beaten, limit, min_level=0):
    """Które stacje pokazać (punkt 16): indeksy, k-skyband - jak auta.

    `beaten[i]` to liczba stacji, które biją stację i. Pierwszy poziom - te,
    których nie bije nic - jest zawsze; kolejne poziomy wchodzą w całości,
    aż uzbiera się `limit`. `min_level` to gwarancja "pokaż więcej": każde
    kliknięcie luzuje regułę o co najmniej jeden poziom."""
    if len(beaten) <= limit:
        return set(range(len(beaten)))
    level = max(sorted(beaten)[limit - 1], min_level)
    return {i for i, count in enumerate(beaten) if count <= level}
