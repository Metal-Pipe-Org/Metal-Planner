"""Dostęp do danych GTFS: aktywne kursy dnia, tablica połączeń, przystanki.

Rozkład dla danego dnia jest wczytywany z SQLite raz i trzymany w pamięci
(_day_cache). Klucz cache zawiera mtime pliku bazy, więc po nocnej podmianie
przez update_gtfs.py dane przeładują się same przy pierwszym zapytaniu.
"""

import math
import re
import sqlite3
import sys
from bisect import bisect_left
from datetime import timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "gtfs.sqlite"

WEEKDAY_COLUMNS = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]

ROUTE_TYPE_LABELS = {0: "Tramwaj", 3: "Autobus"}

# Doba rozkładowa nie kończy się o północy. Kurs nocny wyjeżdżający o 23:46
# jedzie dalej jako 24:02, 24:16, ... i w GTFS należy do kalendarza dnia,
# w którym wyruszył - inaczej jego stop_times przestałyby rosnąć, a wzorzec
# kursowania ("noc z soboty na niedzielę") trzeba by ciąć na pół. Rozkład
# dnia D musi więc obejmować ogon doby D-1, bo to on obsługuje godziny
# 00:00-06:00 tego dnia (patrz update_gtfs.parse_gtfs_time).
PREV_DAY_SEC = 24 * 3600
# Ten sam kurs bywa aktywny w obu dobach (serwis pon-czw), a planner kluczuje
# przesiadki po trip_id - egzemplarz z doby D-1 dostaje więc własny
# identyfikator. Prefiks nie występuje w identyfikatorach GTFS.
PREV_DAY_PREFIX = "~"

# Duże węzły przesiadkowe bywają w GTFS rozbite na kilka nazwanych peronów
# kierunkowych ("PL. GRUNWALDZKI W/t", "... Z/a", ...) - dla pasażera to
# wciąż jedno miejsce. _platform_base_name ucina taki sufiks, żeby dociągnąć
# peron do miejsca o nazwie bazowej (patrz _build_places).
_PLATFORM_SUFFIX = re.compile(r"^(.*?)\s+(?:z|w|pd|pn)/[a-ząćęłńóśźż]+$")
PLACE_MAX_SPAN_M = 400  # zabezpieczenie: dolepiamy peron tylko gdy naprawdę blisko

# Wyszukiwanie ma ignorować polskie znaki diakrytyczne (użytkownik bez
# polskiej klawiatury pisze "Glowny", "Zabia") - ł/ż nie rozkłada się przez
# unicodedata.normalize, więc jawna tabela zamiast NFKD.
_DIACRITIC_MAP = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
})


def _strip_diacritics(casefolded):
    return casefolded.translate(_DIACRITIC_MAP)

_day_cache = {}


def _platform_base_name(stop_name):
    """Nazwa bazowa węzła, jeśli `stop_name` wygląda na kierunkowy peron
    (np. 'PL. GRUNWALDZKI W/t' -> 'PL. GRUNWALDZKI'), inaczej None."""
    m = _PLATFORM_SUFFIX.match(stop_name.casefold())
    return stop_name[:m.end(1)] if m else None


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _one_spot(stop_ids, stop_coords):
    """Te ze słupków tej nazwy, które naprawdę stoją w jednym miejscu.

    Identyczna nazwa wystarczała, dopóki wszystkie dane pochodziły z jednego
    miasta - tam dwie "Kwiskie" to zawsze ta sama Kwiska. Ogólnopolski słownik
    stacji łamie to założenie: "Wiśniowa" we Wrocławiu i stacja "Wiśniowa"
    354 km dalej to ta sama nazwa i zupełnie inne miejsce, a sklejone dałyby
    trzyminutowe przejście przez pół Polski. Miara jest ta sama, której miejsce
    używa już przy doklejaniu peronów (PLACE_MAX_SPAN_M) - jedna reguła dla
    wszystkich źródeł, bez gałęzi "a jeśli kolej".

    Odstający jest ODRZUCANY, a nie rozwiązuje całej grupy: dwa wrocławskie
    słupki "Wiśniowa" mają zostać jednym miejscem także wtedy, gdy do ich
    nazwy dopisze się stacja spod Kielc. Odrzucamy po kolei tego, który jest
    za daleko od największej liczby pozostałych, aż zostanie sam spójny rdzeń.
    Przy remisie (dwa słupki, jeden za daleko od drugiego - nic nie wskazuje,
    który jest "prawdziwy") zostaje jeden i miejsce po prostu się nie sklei.
    """
    kept = [s for s in stop_ids if s in stop_coords]
    while len(kept) > 1:
        za_daleko = {
            s: sum(
                1 for o in kept
                if o != s
                and _haversine_m(*stop_coords[s], *stop_coords[o]) > PLACE_MAX_SPAN_M
            )
            for s in kept
        }
        odstajacy = max(za_daleko, key=lambda s: (za_daleko[s], s))
        if za_daleko[odstajacy] == 0:
            break
        kept.remove(odstajacy)
    return kept


def _build_places(stop_names, stop_coords, stops_by_key):
    """Grupuje słupki w kanoniczne 'miejsca' - jednostkę, o którą pyta reszta
    systemu (dojechaliśmy? można się tu przesiąść?), zamiast surowej nazwy
    GTFS. Baza to dotychczasowe grupy "identyczna nazwa" (zaufane, bez
    sprawdzania odległości - tak działało to już wcześniej). Do nich
    dolepiamy perony kierunkowe o nazwie bazowej pasującej do istniejącego
    miejsca, o ile faktycznie leżą blisko (PLACE_MAX_SPAN_M) - to
    zabezpieczenie przed przypadkową kolizją nazw gdzie indziej w mieście.
    """
    places = {}
    for key, ids in stops_by_key.items():
        kept = _one_spot(ids, stop_coords)
        if kept:
            places[key] = kept
    for stop_id, name in stop_names.items():
        base = _platform_base_name(name)
        if base is None:
            continue
        base_key = base.casefold()
        target = places.get(base_key)
        if not target or stop_id in target:
            continue
        lat, lon = stop_coords[stop_id]
        if not all(_haversine_m(lat, lon, *stop_coords[t]) <= PLACE_MAX_SPAN_M for t in target):
            continue
        places[base_key] = target + [stop_id]
        # Usuwamy własną grupę "dokładna nazwa" tego peronu - inaczej
        # zostaje osierocona w `places` obok scalonego miejsca, i który
        # klucz "wygra" dla tego słupka w `place_of` zależy od przypadkowej
        # kolejności iteracji zamiast od tego, że właśnie go scaliliśmy.
        own_key = name.casefold()
        if own_key != base_key:
            own_group = places.get(own_key)
            if own_group == [stop_id]:
                del places[own_key]
            elif own_group and stop_id in own_group:
                places[own_key] = [s for s in own_group if s != stop_id]
    return places


def _walking_bridges(place_groups):
    """Krawędzie 'przejście pieszym' między słupkami tego samego miejsca.

    To jest most (bridge): kształt stop_id -> (sąsiad, ...) jest ogólnym
    kontraktem transferu w tym systemie, nie czymś specyficznym dla chodzenia
    - każdy przyszły typ transferu (rower, hulajnoga, ...) dostarcza własne
    krawędzie w tym samym kształcie i scala się z resztą przez _merge_bridges,
    bez zmiany logiki skanowania w planner.py.
    """
    bridges = {}
    for group in place_groups:
        if len(group) > 1:
            for stop_id in group:
                bridges[stop_id] = tuple(s for s in group if s != stop_id)
    return bridges


def _merge_bridges(*bridge_maps):
    """Scala mosty z kilku dostawców (na razie tylko chodzenie) w jedną
    relację. Kolejny typ transferu dokłada się tu, a nie osobną ścieżką."""
    merged = {}
    for bridges in bridge_maps:
        for stop_id, neighbors in bridges.items():
            existing = merged.get(stop_id, ())
            merged[stop_id] = existing + tuple(n for n in neighbors if n not in existing)
    return merged


class DayData:
    """Rozkład jednego dnia przygotowany pod algorytm wyszukiwania."""

    __slots__ = (
        "conns", "dep_times", "stop_names", "stop_coords", "stops_by_key",
        "display_name", "stops_by_norm_key", "norm_display_name",
        "siblings", "trip_info", "trip_shape",
        "stops_by_place", "place_of", "conns_by_trip", "pkp_trip_stops",
        "pkp_stations", "deps_by_stop",
    )

    def __init__(self):
        # Połączenie = przejazd między dwoma kolejnymi przystankami jednego kursu:
        # (odjazd_sek, przyjazd_sek, przystanek_z, przystanek_do, trip_id),
        # posortowane po czasie odjazdu - tego wymaga Connection Scan.
        self.conns = []
        self.dep_times = []          # równoległa lista odjazdów do bisect
        self.stop_names = {}         # stop_id -> nazwa
        self.stop_coords = {}        # stop_id -> (lat, lon)
        self.stops_by_key = {}       # nazwa.casefold() -> [stop_id, ...]
        self.display_name = {}       # nazwa.casefold() -> oryginalna pisownia
        self.stops_by_norm_key = {}  # jw. bez polskich znaków diakrytycznych
        self.norm_display_name = {}  # jw. bez polskich znaków diakrytycznych
        self.siblings = {}           # stop_id -> inne słupki tego samego miejsca
        self.trip_info = {}          # trip_id -> (etykieta linii, kierunek)
        self.trip_shape = {}         # trip_id -> shape_id (geometria z shapes.txt)
        self.stops_by_place = {}     # klucz miejsca -> [stop_id, ...] (patrz _build_places)
        self.place_of = {}           # stop_id -> klucz miejsca
        self.conns_by_trip = None    # kurs -> indeksy w conns (leniwie, patrz trip_conns)
        self.deps_by_stop = None     # słupek -> odjazdy (leniwie, patrz stop_departures)
        # trip_id "PKP:..." -> [(stop_id, przyjazd, odjazd), ...] po kolei -
        # odpowiednik stop_times.txt dla kursów kolejowych (patrz pkp.py:
        # augment_day/trip_path). GTFS-owe kursy tego nie używają - mają
        # własne stop_times w SQLite, stąd osobne pole zamiast rozszerzania
        # istniejącego mechanizmu.
        self.pkp_trip_stops = {}
        # (nazwa, stop_id) TYLKO dla prawdziwych stacji PKP (patrz
        # pkp.augment_day - to dokładnie te same station_id, co trafiają
        # do used_ids: mają choć jeden kurs w rozkładzie TEGO dnia i ustalone
        # współrzędne) - osobna, czysto kolejowa lista, NIE mieszana
        # z przystankami MPK jak stops_by_key/stops_by_norm_key. Używana
        # WYŁĄCZNIE przez match_stop do rozwijania zapytań w kształcie
        # "Miasto -" (patrz _match_pkp_city_group) na wszystkie prawdziwe
        # stacje PKP w tym mieście naraz - świadomie osobna struktura, żeby
        # takie rozwinięcie nigdy przypadkiem nie złapało przystanku MPK
        # o zbieżnym przedrostku nazwy.
        self.pkp_stations = []


def _connect():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            "Brak bazy rozkładów (data/gtfs.sqlite). Uruchom: python3 update_gtfs.py"
        )
    return sqlite3.connect(DB_PATH)


def active_service_ids(db, day):
    """service_id kursujące danego dnia wg calendar.txt + wyjątki z calendar_dates.txt."""
    date_str = day.strftime("%Y%m%d")
    weekday = WEEKDAY_COLUMNS[day.weekday()]

    active = {
        row[0]
        for row in db.execute(
            f"SELECT service_id FROM calendar "
            f"WHERE {weekday} = 1 AND start_date <= ? AND end_date >= ?",
            (date_str, date_str),
        )
    }
    for service_id, exception_type in db.execute(
        "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?",
        (date_str,),
    ):
        if exception_type == 1:
            active.add(service_id)
        else:
            active.discard(service_id)
    return active


def load_day(day):
    """Zwraca DayData dla podanej daty (datetime.date), z cache.

    Oś czasu jest liczona od północy `day`: kursy tej doby zachowują swoje
    sekundy (także te ponad 24 h), a ogon doby poprzedniej wchodzi przesunięty
    o -24 h. Dzięki temu odjazd o 00:46 to 2760 niezależnie od tego, w czyim
    kalendarzu kurs siedzi, i skan CSA nie musi wiedzieć o istnieniu północy
    (patrz PREV_DAY_SEC).

    Import pkp lokalny (nie na górze pliku): pkp.py sam importuje gtfs (żeby
    dociągnąć gtfs._merge_bridges/_haversine_m przy doklejaniu połączeń
    kolejowych - patrz pkp.augment_day) - import na górze obu plików
    zapętliłby się przy starcie procesu. W środku funkcji cyklu nie ma: oba
    moduły są już w pełni załadowane, zanim load_day() zostanie wywołane.
    """
    import pkp

    pkp_mtime = pkp.DB_PATH.stat().st_mtime if pkp.DB_PATH.exists() else 0
    pkp_coords_mtime = pkp.COORDS_PATH.stat().st_mtime if pkp.COORDS_PATH.exists() else 0
    key = (
        day.isoformat(), DB_PATH.stat().st_mtime if DB_PATH.exists() else 0,
        pkp_mtime, pkp_coords_mtime,
    )
    if key in _day_cache:
        return _day_cache[key]

    db = _connect()
    data = DayData()

    active = active_service_ids(db, day)
    active_prev = active_service_ids(db, day - timedelta(days=1))

    route_names = {}   # route_id -> etykieta, np. "Tramwaj 5"
    for route_id, short_name, long_name, route_type in db.execute(
        "SELECT route_id, route_short_name, route_long_name, route_type FROM routes"
    ):
        kind = ROUTE_TYPE_LABELS.get(route_type, "Linia")
        route_names[route_id] = f"{kind} {short_name or long_name}".strip()

    # trip_id z bazy -> ((identyfikator w DayData, przesunięcie sekund), ...).
    # Kurs jeżdżący w obu dobach ma dwa wpisy: to dwa różne autobusy w tym
    # rozkładzie, więc muszą być rozróżnialne dla logiki przesiadek.
    trip_ids = {}
    for trip_id, route_id, service_id, headsign, shape_id in db.execute(
        "SELECT trip_id, route_id, service_id, trip_headsign, shape_id FROM trips"
    ):
        today = service_id in active
        yesterday = service_id in active_prev
        if not (today or yesterday):
            continue
        trip_id = sys.intern(trip_id)
        instances = []
        if today:
            instances.append((trip_id, 0))
        if yesterday:
            instances.append((sys.intern(PREV_DAY_PREFIX + trip_id), PREV_DAY_SEC))
        info = (route_names.get(route_id, "Linia ?"), headsign or "")
        shape = sys.intern(shape_id) if shape_id else None
        for instance_id, _ in instances:
            data.trip_info[instance_id] = info
            if shape:
                data.trip_shape[instance_id] = shape
        trip_ids[trip_id] = tuple(instances)

    for stop_id, stop_name, lat, lon in db.execute(
        "SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops"
    ):
        stop_id = sys.intern(stop_id)
        data.stop_names[stop_id] = stop_name
        data.stop_coords[stop_id] = (lat, lon)
        name_key = stop_name.casefold()
        data.stops_by_key.setdefault(name_key, []).append(stop_id)
        data.display_name.setdefault(name_key, stop_name)
        norm_key = _strip_diacritics(name_key)
        data.stops_by_norm_key.setdefault(norm_key, []).append(stop_id)
        data.norm_display_name.setdefault(norm_key, stop_name)

    # Kolej PRZED budowaniem miejsc: stacja ma przejść przez dokładnie ten
    # sam młynek co przystanek miejski (ta sama nazwa -> to samo miejsce ->
    # przejście pieszo między słupkami). Doklejona po miejscach - tak było
    # do 2026-08-31 - nie należała do żadnego miejsca i musiała mieć własny,
    # drugi mechanizm przesiadki. Nie ma go już; patrz pkp.augment_day.
    pkp.augment_day(data, day)

    # Kanoniczne miejsce (patrz _build_places) i most pieszy między jego
    # słupkami (patrz _walking_bridges) - _merge_bridges scala go tu z
    # dowolnymi innymi dostawcami transferu, gdyby doszły.
    data.stops_by_place = _build_places(data.stop_names, data.stop_coords, data.stops_by_key)
    data.place_of = {
        sid: key for key, ids in data.stops_by_place.items() for sid in ids
    }
    data.siblings = _merge_bridges(_walking_bridges(data.stops_by_place.values()))

    # stop_times czytamy w kolejności (trip_id, stop_sequence) - to indeks,
    # więc bez sortowania - i sklejamy sąsiednie przystanki kursu w połączenia.
    prev_trip = None
    prev_stop = None
    prev_dep = 0
    entries = ()
    conns = data.conns
    for trip_id, stop_id, arrival_sec, departure_sec in db.execute(
        "SELECT trip_id, stop_id, arrival_sec, departure_sec "
        "FROM stop_times ORDER BY trip_id, stop_sequence"
    ):
        if trip_id != prev_trip:
            prev_trip = trip_id
            prev_stop = None
            entries = trip_ids.get(trip_id, ())
        if not entries:
            continue
        stop_id = sys.intern(stop_id)
        if prev_stop is not None:
            for instance_id, shift in entries:
                dep = prev_dep - shift
                # Ogon doby D-1 zaczyna się dla nas o północy: to, co ten kurs
                # przejechał wcześniej, jest już przeszłością i nie da się do
                # niego wsiąść.
                if dep >= 0:
                    conns.append(
                        (dep, arrival_sec - shift, prev_stop, stop_id, instance_id)
                    )
        prev_stop, prev_dep = stop_id, departure_sec
    db.close()

    conns.sort(key=lambda c: c[0])
    data.dep_times = [c[0] for c in conns]

    _day_cache[key] = data
    if len(_day_cache) > 2:                      # trzymamy najwyżej 2 dni w RAM
        _day_cache.pop(next(iter(_day_cache)))
    return data


def _expand_to_places(data, stop_ids):
    """Dokłada do dopasowania resztę słupków tego samego miejsca (patrz
    _build_places) - np. wyszukanie "PL. GRUNWALDZKI" ma rozpoznawać
    dojazd/wsiadanie także na peronach kierunkowych tego placu, nie tylko
    na słupkach o dokładnie tej nazwie."""
    expanded = set(stop_ids)
    for stop_id in stop_ids:
        place_key = data.place_of.get(stop_id)
        if place_key is not None:
            expanded.update(data.stops_by_place[place_key])
    return list(expanded)


LAST_MILE_MAX_STOPS = 5  # ile najbliższych słupków bierzemy pod uwagę jako "wyzwalacze" miejsca


def nearby_stops(lat, lon, day, radius_m, max_n=LAST_MILE_MAX_STOPS):
    """Słupki w promieniu `radius_m` od dowolnego punktu (klik na mapie) -
    dla wybranych `max_n` najbliższych dociąga też resztę ich miejsca
    (patrz _expand_to_places), tak samo jak przy wyszukiwaniu po nazwie.

    Brak modelowania czasu dojścia - słupek w zasięgu liczy się jako
    od razu dostępny, tak jak przy starcie/celu z nazwy.
    """
    in_range = []
    for stop_id, (slat, slon) in day.stop_coords.items():
        dist = _haversine_m(lat, lon, slat, slon)
        if dist <= radius_m:
            in_range.append((dist, stop_id))
    in_range.sort()
    triggers = [stop_id for _, stop_id in in_range[:max_n]]
    return set(_expand_to_places(day, triggers))


STOP_SNAP_M = 60   # kropka na mapie stoi NA słupku, nie "gdzieś w okolicy"


def stop_at(lat, lon, data, max_m=STOP_SNAP_M):
    """Przystanek pod wskazanym punktem: (nazwa, [stop_id, ...] całego miejsca).

    Do pytania o kropkę narysowaną na słupku - front zna jej współrzędne, ale
    nie nazwę. Inaczej niż nearby_stops, które zbiera wszystko w promieniu
    dojścia i odpowiada na zupełnie inne pytanie ("skąd mogę tu zacząć"): tu
    punkt ma trafić w JEDEN konkretny słupek, więc promień jest mały, a wynik
    to ten najbliższy, dociągnięty do reszty swojego miejsca
    (patrz _expand_to_places).

    (None, None) gdy w zasięgu nie ma nic - lepsze niż odpowiedź o przystanku
    oddalonym o pół kilometra.
    """
    best_dist = None
    best_stop = None
    for stop_id, (slat, slon) in data.stop_coords.items():
        dist = _haversine_m(lat, lon, slat, slon)
        if dist <= max_m and (best_dist is None or dist < best_dist):
            best_dist, best_stop = dist, stop_id
    if best_stop is None:
        return None, None
    return data.stop_names[best_stop], _expand_to_places(data, [best_stop])


def _match_city_group(key, norm_key, data):
    """Dopasowuje "zbiorczą" stację PKP typu "Warszawa -" (patrz
    update_pkp._is_city_wildcard) - PKP oznacza tak w słowniku stacji
    "dowolną stację w tym mieście", zawsze bez żadnego WŁASNEGO kursu
    (sprawdzone na żywo: 0 wpisów w stops dla każdej z nich) - żadne
    z wcześniejszych dopasowań w match_stop nigdy jej więc nie złapie,
    rozkład po prostu nie ma czego z nią połączyć.

    Rozpoznanie PO WZORCU zapytania (kończy się myślnikiem), nie po
    sztywnej liście nazw miast - i szukamy WSZYSTKICH prawdziwych, znanych
    stacji zaczynających się od tej nazwy jako CAŁE SŁOWO (nie podciąg -
    "Warszawa" nie ma złapać hipotetycznej "Warszawskiej"), łącząc ich
    słupki w JEDNO zapytanie do CSA zamiast zwracać błąd "nie znaleziono" -
    skan i tak sam wybierze najlepszą z nich (patrz _scan/plan_route:
    przyjmuje ZBIÓR stacji startowych/końcowych z definicji, to nie nowy
    mechanizm, tylko ten sam co przy zwykłym "miejscu" z wielu słupków).

    PRZESZUKUJE WYŁĄCZNIE data.pkp_stations (patrz jej nagłówek w
    DayData.__init__), NIE ogólne stops_by_key/stops_by_norm_key (tam MPK
    i PKP są zmieszane) - "Wrocław -" ma trafić w prawdziwe stacje PKP
    zaczynające się na "Wrocław", nie przypadkiem też w jakiś przystanek
    MPK o zbieżnym przedrostku nazwy. data.pkp_stations to z definicji
    tylko stacje z co najmniej jednym kursem TEGO dnia (patrz
    pkp.augment_day, used_ids) - dokładnie to, co "przystanek" ma znaczyć.

    Zwraca (nazwa_do_wyświetlenia, [stop_id, ...]) albo (None, None), gdy
    nic nie pasuje - wołane jako OSTATNI fallback w match_stop."""
    if not key.endswith("-"):
        return None, None
    city = key[:-1].strip()
    if not city:
        return None, None
    norm_city = norm_key[:-1].strip() if norm_key.endswith("-") else _strip_diacritics(city)

    group = set()
    for name, stop_id in data.pkp_stations:
        name_cf = name.casefold()
        if name_cf == city or name_cf.startswith(city + " "):
            group.add(stop_id)
            continue
        norm_name = _strip_diacritics(name_cf)
        if norm_name == norm_city or norm_name.startswith(norm_city + " "):
            group.add(stop_id)
    if not group:
        return None, None
    return f"{city.title()} (dowolna stacja)", _expand_to_places(data, list(group))


def _match_city_group(key, norm_key, data):
    """Dopasowuje "zbiorczą" stację PKP typu "Warszawa -" (patrz
    update_pkp._is_city_wildcard) - PKP oznacza tak w słowniku stacji
    "dowolną stację w tym mieście", zawsze bez żadnego WŁASNEGO kursu
    (sprawdzone na żywo: 0 wpisów w stops dla każdej z nich) - żadne
    z wcześniejszych dopasowań w match_stop nigdy jej więc nie złapie,
    rozkład po prostu nie ma czego z nią połączyć.

    Rozpoznanie PO WZORCU zapytania (kończy się myślnikiem), nie po
    sztywnej liście nazw miast - i szukamy WSZYSTKICH prawdziwych, znanych
    stacji zaczynających się od tej nazwy jako CAŁE SŁOWO (nie podciąg -
    "Warszawa" nie ma złapać hipotetycznej "Warszawskiej"), łącząc ich
    słupki w JEDNO zapytanie do CSA zamiast zwracać błąd "nie znaleziono" -
    skan i tak sam wybierze najlepszą z nich (patrz _scan/plan_route:
    przyjmuje ZBIÓR stacji startowych/końcowych z definicji, to nie nowy
    mechanizm, tylko ten sam co przy zwykłym "miejscu" z wielu słupków).

    PRZESZUKUJE WYŁĄCZNIE data.pkp_stations (patrz jej nagłówek w
    DayData.__init__), NIE ogólne stops_by_key/stops_by_norm_key (tam MPK
    i PKP są zmieszane) - "Wrocław -" ma trafić w prawdziwe stacje PKP
    zaczynające się na "Wrocław", nie przypadkiem też w jakiś przystanek
    MPK o zbieżnym przedrostku nazwy. data.pkp_stations to z definicji
    tylko stacje z co najmniej jednym kursem TEGO dnia (patrz
    pkp.augment_day, used_ids) - dokładnie to, co "przystanek" ma znaczyć.

    Zwraca (nazwa_do_wyświetlenia, [stop_id, ...]) albo (None, None), gdy
    nic nie pasuje - wołane jako OSTATNI fallback w match_stop."""
    if not key.endswith("-"):
        return None, None
    city = key[:-1].strip()
    if not city:
        return None, None
    norm_city = norm_key[:-1].strip() if norm_key.endswith("-") else _strip_diacritics(city)

    group = set()
    for name, stop_id in data.pkp_stations:
        name_cf = name.casefold()
        if name_cf == city or name_cf.startswith(city + " "):
            group.add(stop_id)
            continue
        norm_name = _strip_diacritics(name_cf)
        if norm_name == norm_city or norm_name.startswith(norm_city + " "):
            group.add(stop_id)
    if not group:
        return None, None
    return f"{city.title()} (dowolna stacja)", _expand_to_places(data, list(group))


def match_stop(query, data):
    """Dopasowuje wpisaną nazwę do przystanku.

    Zwraca (nazwa, [stop_id, ...], None) przy trafieniu - lista obejmuje
    całe kanoniczne miejsce, nie tylko słupki o dokładnie wpisanej nazwie
    (patrz _expand_to_places) - albo (None, None, [podpowiedzi]) gdy nazwa
    jest nieznana/niejednoznaczna.

    Dopasowanie ignoruje wielkość liter i - dopiero gdy dokładna pisownia
    zawiedzie - polskie znaki diakrytyczne (patrz _strip_diacritics), więc
    "Zabia" trafia w "Żabia", a "Dworzec Glowny" w "Dworzec Główny".

    "Zbiorcza" stacja typu "Warszawa -" (patrz _match_city_group) trafia
    we WSZYSTKIE prawdziwe stacje danego miasta na raz - dopiero gdy nic
    innego wyżej nie pasuje, żeby nie odbierać pierwszeństwa zwykłemu,
    dokładnemu dopasowaniu.
    """
    key = " ".join(query.split()).casefold()
    if not key:
        return None, None, []
    if key in data.stops_by_key:
        return data.display_name[key], _expand_to_places(data, data.stops_by_key[key]), None

    norm_key = _strip_diacritics(key)
    if norm_key in data.stops_by_norm_key:
        return (
            data.norm_display_name[norm_key],
            _expand_to_places(data, data.stops_by_norm_key[norm_key]),
            None,
        )

    city_name, city_stops = _match_city_group(key, norm_key, data)
    if city_name is not None:
        return city_name, city_stops, None

    candidates = [k for k in data.stops_by_key if key in k]
    if len(candidates) == 1:
        k = candidates[0]
        return data.display_name[k], _expand_to_places(data, data.stops_by_key[k]), None
    if candidates:
        return None, None, sorted(data.display_name[k] for k in candidates)[:8]

    norm_candidates = [k for k in data.stops_by_norm_key if norm_key in k]
    if len(norm_candidates) == 1:
        k = norm_candidates[0]
        return (
            data.norm_display_name[k],
            _expand_to_places(data, data.stops_by_norm_key[k]),
            None,
        )
    return None, None, sorted({data.norm_display_name[k] for k in norm_candidates})[:8]


def all_stop_names():
    """Posortowane nazwy przystanków do podpowiadania w formularzu.

    Perony kierunkowe (patrz _platform_base_name) pokazujemy jako jedną
    nazwę bazową - użytkownik nie musi wybierać, który z 9 identycznych
    w praktyce wariantów miał na myśli, skoro match_stop i tak dociąga
    całe miejsce niezależnie od tego, który wpisze (patrz _expand_to_places).
    """
    db = _connect()
    names = {row[0] for row in db.execute("SELECT DISTINCT stop_name FROM stops")}
    db.close()
    display = {_platform_base_name(name) or name for name in names}
    return sorted(display)


def all_stops_geo():
    """Wszystkie słupki z współrzędnymi - do narysowania na mapie."""
    db = _connect()
    stops = [
        {"name": name, "lat": lat, "lon": lon}
        for name, lat, lon in db.execute(
            "SELECT stop_name, stop_lat, stop_lon FROM stops"
        )
    ]
    db.close()
    return stops


_shape_cache = {}       # shape_id -> [(lat, lon), ...]
_slice_cache = {}       # (shape_id, przystanki) -> gotowa ścieżka
_geo_generation = None  # mtime bazy, dla którego ważne są powyższe cache

_SIMPLIFY_DEG = 0.0001   # ~11 m - upraszczanie łamanych
_SNAP_DEG = 0.0025       # ~280 m - maks. wiarygodna odległość przystanku od shape'a
_SLICE_CACHE_MAX = 1500


def geo_generation():
    """Odświeża znacznik ważności cache geometrii (wołać raz na zapytanie).

    Po nocnej podmianie bazy mtime się zmienia i oba cache są czyszczone
    w całości - to jedyny moment, w którym stare wpisy stają się nieważne.
    """
    global _geo_generation
    mtime = DB_PATH.stat().st_mtime if DB_PATH.exists() else 0
    if mtime != _geo_generation:
        _shape_cache.clear()
        _slice_cache.clear()
        _geo_generation = mtime
    return mtime


def open_db():
    """Połączenie dla wywołującego, np. na czas jednego zapytania o przepływy."""
    return _connect()


def _shape_points(shape_id, db):
    points = _shape_cache.get(shape_id)
    if points is None:
        points = [
            (lat, lon)
            for lat, lon in db.execute(
                "SELECT lat, lon FROM shapes WHERE shape_id = ? ORDER BY seq",
                (shape_id,),
            )
        ]
        _shape_cache[shape_id] = points
    return points


def shape_slice(shape_id, stop_coords, db):
    """Fragment geometrii kursu między pierwszym a ostatnim przystankiem.

    Zakłada, że wywołujący odświeżył cache przez geo_generation().
    Fallback (brak/niewiarygodna geometria): łamana po przystankach.
    """
    if not shape_id or len(stop_coords) < 2:
        return stop_coords
    cache_key = (shape_id, tuple(stop_coords))
    cached = _slice_cache.get(cache_key)
    if cached is not None:
        return cached

    points = _shape_points(shape_id, db)
    result = _compute_slice(points, stop_coords) if len(points) >= 2 else stop_coords

    if len(_slice_cache) >= _SLICE_CACHE_MAX:
        # Zamiast kasować wszystko, upuść ~10% najstarszych wpisów.
        for key in list(_slice_cache)[:_SLICE_CACHE_MAX // 10]:
            del _slice_cache[key]
    _slice_cache[cache_key] = result
    return result


def _compute_slice(points, stop_coords):
    # Lokalna metryka: 1 stopień długości ~ cos(szerokości) stopnia szerokości.
    cos_lat = math.cos(math.radians(stop_coords[0][0]))

    def dist2(a, b):
        d_lat = a[0] - b[0]
        d_lon = (a[1] - b[1]) * cos_lat
        return d_lat * d_lat + d_lon * d_lon

    snap2 = _SNAP_DEG * _SNAP_DEG

    # Monotoniczne dopasowanie: każdy następny przystanek szukany od pozycji
    # poprzedniego, więc pętle i nawroty trasy nie mylą kierunku. Skan urywa
    # się LOOKAHEAD punktów za ostatnim minimum; jeśli tak znalezione minimum
    # jest podejrzanie daleko (fałszywe minimum przy wsiadaniu w środku
    # kursu), doskanowujemy cały pozostały zakres.
    LOOKAHEAD = 50
    marks = []
    position = 0
    for stop in stop_coords:
        best_i = position
        best_d = dist2(points[position], stop)
        for i in range(position + 1, len(points)):
            d = dist2(points[i], stop)
            if d < best_d:
                best_d, best_i = d, i
            elif i - best_i > LOOKAHEAD:
                break
        if best_d > snap2:
            for i in range(position + 1, len(points)):
                d = dist2(points[i], stop)
                if d < best_d:
                    best_d, best_i = d, i
        marks.append((best_i, best_d))
        position = best_i

    first, last = marks[0][0], marks[-1][0]
    if last - first < 1:
        return stop_coords
    # Walidacja: końce wycinka muszą leżeć przy przystankach, a długość
    # wycinka być w rozsądnym stosunku do łamanej po przystankach - inaczej
    # dopasowanie się rozjechało i uczciwiej pokazać łamaną.
    if marks[0][1] > snap2 or marks[-1][1] > snap2:
        return stop_coords
    sliced = points[first:last + 1]
    stops_len = _polyline_len(stop_coords, cos_lat)
    slice_len = _polyline_len(sliced, cos_lat)
    if stops_len > 0 and not (0.85 <= slice_len / stops_len <= 3.0):
        return stop_coords
    return _simplify(sliced)


def _polyline_len(points, cos_lat):
    total = 0.0
    for a, b in zip(points, points[1:]):
        d_lat = a[0] - b[0]
        d_lon = (a[1] - b[1]) * cos_lat
        total += math.sqrt(d_lat * d_lat + d_lon * d_lon)
    return total


def _simplify(points):
    """Usuwa punkty bliższe niż ~11 m od ostatnio zachowanego (mniej JSON-a)."""
    threshold2 = _SIMPLIFY_DEG * _SIMPLIFY_DEG
    kept = [points[0]]
    for point in points[1:-1]:
        d_lat = point[0] - kept[-1][0]
        d_lon = point[1] - kept[-1][1]
        if d_lat * d_lat + d_lon * d_lon >= threshold2:
            kept.append(point)
    kept.append(points[-1])
    return kept


def db_trip(trip_id):
    """Identyfikator kursu z DayData -> (trip_id w bazie, przesunięcie sekund).

    Egzemplarz z doby D-1 nosi prefiks i czasy przesunięte o -24 h wobec
    tego, co leży w bazie (patrz load_day) - każde sięgnięcie z powrotem do
    SQLite musi to odkręcić.
    """
    if trip_id.startswith(PREV_DAY_PREFIX):
        return trip_id[len(PREV_DAY_PREFIX):], PREV_DAY_SEC
    return trip_id, 0


def trip_conns(data, trip_id):
    """Połączenia jednego kursu z tablicy dnia, po kolei (indeksy w data.conns).

    Indeks kurs -> indeksy budujemy przy pierwszym pytaniu i trzymamy przy
    DayData. Sam skan go nie potrzebuje (idzie liniowo po całej tablicy),
    używa go dopiero sklejanie etapów w plannerze - a że dane są tuż obok,
    w pamięci, nie ma po co wracać po nie do SQLite.
    """
    if data.conns_by_trip is None:
        index = {}
        for i, conn in enumerate(data.conns):
            index.setdefault(conn[4], []).append(i)
        data.conns_by_trip = index
    return data.conns_by_trip.get(trip_id, ())


def stop_departures(data, stop_ids, from_sec, limit=20):
    """Najbliższe odjazdy z podanych słupków, od `from_sec` na osi dnia.

    Zwraca [(odjazd, trip_id, stop_id), ...] po kolei, najwyżej `limit`.

    Indeks słupek -> odjazdy budujemy przy pierwszym pytaniu i trzymamy przy
    DayData - tak samo jak conns_by_trip (patrz trip_conns). Skan CSA go nie
    potrzebuje, więc dopóki nikt nie najedzie na kropkę przesiadki, nie ma po
    co go liczyć.

    Ostatni przystanek kursu nie ma tu wpisu: połączenia opisują przejazd
    MIĘDZY słupkami, a na pętli i tak nie ma do czego wsiąść.
    """
    if data.deps_by_stop is None:
        index = {}
        # conns są posortowane po odjeździe, więc każda lista wychodzi z tej
        # pętli już uporządkowana - bisect niżej może na tym polegać.
        for dep, _arr, from_stop, _to_stop, trip in data.conns:
            index.setdefault(from_stop, []).append((dep, trip))
        data.deps_by_stop = index

    upcoming = []
    for stop_id in stop_ids:
        deps = data.deps_by_stop.get(stop_id)
        if not deps:
            continue
        start = bisect_left(deps, (from_sec,))
        upcoming.extend(
            (dep, trip, stop_id) for dep, trip in deps[start:start + limit]
        )
    upcoming.sort()
    return upcoming[:limit]


def departures_between(data, stop_ids, from_sec, to_sec):
    """Wszystkie odjazdy z podanych słupków w oknie [from_sec, to_sec].

    Jak stop_departures, ale bez limitu sztuk - bo pytanie jest inne. Tablica
    odjazdów pyta "co najbliżej", a to jest do policzenia, KTÓRYM ostatnim
    kursem danej linii jeszcze się gdzieś zdąży (patrz planner._line_deadlines):
    tam obcięcie po liczbie sztuk dałoby odpowiedź zależną od limitu, a nie od
    rozkładu. Oknem jest i tak horyzont mapy, więc lista nie rośnie w
    nieskończoność.
    """
    stop_departures(data, (), from_sec, 0)      # dociąga indeks deps_by_stop
    out = []
    for stop_id in stop_ids:
        deps = data.deps_by_stop.get(stop_id)
        if not deps:
            continue
        start = bisect_left(deps, (from_sec,))
        for dep, trip in deps[start:]:
            if dep > to_sec:
                break
            out.append((dep, trip, stop_id))
    out.sort()
    return out


def trip_path(trip_id, board_stop, board_dep, exit_stop, exit_arr, db=None, data=None):
    """Kolejne przystanki kursu od wsiadania do wysiadania (stop_id, przyjazd, odjazd).

    Czasy - i te na wejściu, i te w wyniku - są na osi wczytanego dnia, więc
    dla kursu z doby D-1 wiersze z bazy trzeba przesunąć tak samo jak przy
    budowie tablicy połączeń.

    Z podanym `db` korzysta z cudzego połączenia (jedno na całe zapytanie);
    bez niego otwiera i zamyka własne.

    Kursy kolejowe (PKP, prefiks "PKP:" - patrz pkp.augment_day) nie mają
    wpisu w stop_times.txt - `data` (DayData już wczytana dla tego dnia)
    daje dostęp do sekwencji przystanków dociągniętej przy budowie dnia, bez
    drugiego zapytania do żadnej bazy. Bez `data` (stare wywołania, testy)
    kurs kolejowy po prostu nie ma tu czego zwrócić - patrz pkp.trip_path.
    """
    if trip_id.startswith("PKP:"):
        import pkp
        return pkp.trip_path(data, trip_id, board_stop, board_dep, exit_stop, exit_arr)

    trip_id, shift = db_trip(trip_id)
    own_db = db is None
    db = db or _connect()
    try:
        rows = [
            (stop_id, arrival_sec - shift, departure_sec - shift)
            for stop_id, arrival_sec, departure_sec in db.execute(
                "SELECT stop_id, arrival_sec, departure_sec FROM stop_times "
                "WHERE trip_id = ? ORDER BY stop_sequence",
                (trip_id,),
            )
        ]
    finally:
        if own_db:
            db.close()

    start_i = None
    for i, (stop_id, arrival_sec, departure_sec) in enumerate(rows):
        if start_i is None:
            if stop_id == board_stop and departure_sec == board_dep:
                start_i = i
        elif stop_id == exit_stop and arrival_sec == exit_arr:
            return rows[start_i:i + 1]
    return []
