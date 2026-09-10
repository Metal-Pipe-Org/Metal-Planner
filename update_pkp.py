"""Pobiera rozkład kolejowy z PKP PLK OpenData API i buduje lokalną bazę
SQLite (data/pkp.sqlite) - żeby wyszukiwarka połączeń pociągowych (pkp.py)
nie odpytywała tego API przy KAŻDYM wyszukiwaniu użytkownika, tylko czytała
z dysku. Ten plik jest kolejowym odpowiednikiem update_gtfs.py - ta sama
architektura (pobranie -> budowa SQLite -> atomowa podmiana -> harmonogram),
osobny plik, bo to niezależny pipeline nad niezależnym źródłem danych.

DLACZEGO CAŁY KRAJ NARAZ. /api/v1/schedules bez parametru `stations` zwraca
rozkład wszystkich stacji - jedno zapytanie zamiast osobnego na każdą parę
stacji, którą ktoś kiedykolwiek wpisze w formularz. Sprawdzone empirycznie:
tydzień rozkładu całego kraju (~12,7 tys. tras) to ok. 60 MB i jedno
zapytanie kilka sekund - więcej niż mieści limit 100 zapytań/h w planie
Basic, ale rozkład odświeża się raz na dobę (harmonogram), więc kosztuje
to jedno zapytanie dziennie, nie jedno na wyszukiwanie.

OKNO CZASOWE. Pobieramy SCHEDULE_WINDOW_DAYS dni od dziś - `operatingDates`
w odpowiedzi API działa jak GTFS-owy calendar.txt: jeden wpis trasy niesie
WSZYSTKIE daty w żądanym zakresie, na które kursuje, więc szerszy zakres nie
mnoży liczby tras, tylko wydłuża listę dat przy każdej. Data spoza okna po
prostu nie ma czego znaleźć - to znany, udokumentowany limit (patrz
docs/PROJECT.md, sekcja "Znane ograniczenia").

GEOKODOWANIE. API PKP nie ma NIGDZIE współrzędnych stacji (sprawdzone -
żaden endpoint, żadne pole w /api/v1/fields/schedules) - to jedyny sposób,
żeby stacje kolejowe miały markery na mapie, tak jak słupki MPK, jest
dociągnięcie pozycji z zewnątrz. Robi to geocode_missing_stations() z TRZECH
źródeł po kolei (patrz jej nagłówek): najpierw oficjalna mapa infrastruktury
PLK (_fetch_plk_points, jedno zbiorcze zapytanie) - dane samego zarządcy
sieci. Potem katalog stacji portalu pasażera PKP (_fetch_portalpasazera_point,
per stacja, więc wolniejsze - stąd dopiero drugie; obejmuje tylko Polskę).
Dopiero na końcu OpenStreetMap (_fetch_osm_stations, Overpass, Polska
i kraje sąsiednie naraz) - węzły OTAGOWANE jako infrastruktura kolejowa
(tag `railway`), NIE dopasowanie tekstowe do nazwy miejscowości jak
Nominatim (usunięty z tego pliku wcześniej, na wyraźną prośbę użytkownika -
dopasowywał po samej nazwie, a Polska ma więcej niż jedno miejsce o tej
samej nazwie w różnych regionach, co kilkukrotnie kończyło się złym, ale
przekonującym wynikiem, patrz historia w docs/PROJECT.md; węzeł otagowany
wprost jako stacja kolejowa tego problemu nie ma). Trwały cache na dysku -
raz znaleziona stacja nigdy nie jest odpytywana drugi raz, kolejne przebiegi
dogadują tylko nowe. Stacja, której żadne z trzech źródeł nie ma, zostaje
bez markera (i trafia do pkp_unmapped_stations.json, do wglądu) - to
świadomy wybór: brak markera jest zawsze bezpieczniejszy niż zgadywanie.

TRZY WEJŚCIA, jak przy GTFS: wywołanie ręczne/z crona (`main()`), start
serwera (`refresh_on_start()` - wołane z app.py przy uruchomieniu lokalnym)
i codzienny harmonogram (`start_daily_scheduler()` - wątek z on_starting
w gunicorn.conf.py, w kontenerze). W kontenerze pierwsze pobranie robi
docker/entrypoint.sh, przed startem gunicorna.

Bez PKP_API_KEY (patrz config.py) cały ten moduł jest wyłączony po cichu -
brak klucza to nie błąd, tylko brak konfiguracji (tak samo jak Siechnice bez
SIECHNICE_ENABLED w update_gtfs.py), więc entrypoint/harmonogram mogą wołać
funkcje tego pliku bezwarunkowo.
"""

import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from pyproj import Transformer

# Ten plik bywa uruchamiany sam (docker/entrypoint.sh, cron), poza procesem
# serwera - musi wczytać data/.env na własną rękę.
import config  # noqa: F401
import pkp

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "pkp.sqlite"
NEW_DB_PATH = DATA_DIR / "pkp_new.sqlite"

PKP_BASE_URL = "https://pdp-api.plk-sa.pl/api/v1"
USER_AGENT = "Metal-Planner/0.1 (+https://github.com/Metal-Pipe-Org/Metal-Planner)"
REQUEST_TIMEOUT_SEC = 180   # odpowiedź waży dziesiątki MB - zwykły timeout by nie starczył

SCHEDULE_WINDOW_DAYS = 7    # dziś + 6 dni - patrz nagłówek modułu

DEFAULT_MAX_AGE_HOURS = 12
SCHEDULER_TICK_SEC = 300

SCHEMA = """
CREATE TABLE stations (
    station_id INTEGER PRIMARY KEY,
    name       TEXT NOT NULL
);
CREATE TABLE routes (
    schedule_id     INTEGER NOT NULL,
    order_id        INTEGER NOT NULL,
    name            TEXT,
    carrier_code    TEXT,
    national_number TEXT,
    category        TEXT,
    PRIMARY KEY (schedule_id, order_id)
);
CREATE TABLE stops (
    schedule_id    INTEGER NOT NULL,
    order_id       INTEGER NOT NULL,
    station_id     INTEGER NOT NULL,
    order_number   INTEGER NOT NULL,
    arrival_time   TEXT,
    departure_time TEXT
);
CREATE TABLE operating_dates (
    schedule_id INTEGER NOT NULL,
    order_id    INTEGER NOT NULL,
    date        TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX idx_stops_route ON stops (schedule_id, order_id);
CREATE INDEX idx_stops_station ON stops (station_id);
CREATE INDEX idx_opdates_route_date ON operating_dates (schedule_id, order_id, date);
"""


def batched_insert(db, sql, rows, batch_size=50_000):
    """Ten sam trik co w update_gtfs.py: executemany w paczkach, żeby jedna
    ogromna transakcja nie trzymała w pamięci całej listy wierszy naraz."""
    count = 0
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            db.executemany(sql, batch)
            count += len(batch)
            batch.clear()
    if batch:
        db.executemany(sql, batch)
        count += len(batch)
    return count


def _fetch_schedule(date_from, date_to):
    url = (f"{PKP_BASE_URL}/schedules?" + urllib.parse.urlencode({
        "dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat(),
    }))
    request = urllib.request.Request(url, headers={
        "X-API-Key": config.pkp_api_key(),
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SEC) as response:
        return json.load(response)


def build_database(data, db_path):
    db_path.unlink(missing_ok=True)
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)

    n = batched_insert(
        db, "INSERT OR REPLACE INTO stations VALUES (?, ?)",
        (
            # Nazwa zostaje CZYSTA (bez dopisków) - to prawdziwa nazwa stacji,
            # taka, jaką zna PKP: używa jej geocode_missing_stations (dopasowanie
            # po nazwie do mapy PLK/portalu pasażera) i
            # augment_day (mecz z wpisaną przez użytkownika nazwą). Plakietka
            # "PKP" odróżniająca stację od przystanku MPK jest sprawą samej
            # WARSTWY WYŚWIETLANIA, nie danych - patrz routes.py (`kind` obok
            # nazwy) i static/app.js (dokleja plakietkę tylko w podpowiedziach
            # i w dymku na mapie, nigdy do samej nazwy).
            (row["id"], row["name"])
            for row in data.get("dictionaries", {}).get("stations", {}).values()
        ),
    )
    print(f"  stations: {n}")

    routes = data.get("routes", [])
    n = batched_insert(
        db, "INSERT OR REPLACE INTO routes VALUES (?, ?, ?, ?, ?, ?)",
        (
            (r["scheduleId"], r["orderId"], r.get("name"), r.get("carrierCode"),
             r.get("nationalNumber"), r.get("commercialCategorySymbol"))
            for r in routes
        ),
    )
    print(f"  routes: {n}")

    def stop_rows():
        for r in routes:
            for s in r.get("stations", ()):
                yield (
                    r["scheduleId"], r["orderId"], s["stationId"], s["orderNumber"],
                    s.get("arrivalTime"), s.get("departureTime"),
                )
    n = batched_insert(db, "INSERT INTO stops VALUES (?, ?, ?, ?, ?, ?)", stop_rows())
    print(f"  stops: {n}")

    def date_rows():
        for r in routes:
            for d in r.get("operatingDates", ()):
                yield (r["scheduleId"], r["orderId"], d)
    n = batched_insert(db, "INSERT INTO operating_dates VALUES (?, ?, ?)", date_rows())
    print(f"  operating_dates: {n}")

    print("Tworzę indeksy...")
    db.executescript(INDEXES)
    db.commit()
    db.close()


def run_update():
    """Pełne przejście: pobranie, budowa, atomowa podmiana. True = udało się
    (albo nie było czego robić - patrz niżej).

    Bez PKP_API_KEY zwraca True bez żadnej pracy - patrz nagłówek modułu."""
    if not pkp.enabled():
        print("Brak PKP_API_KEY - pomijam aktualizację rozkładu kolejowego.")
        return True

    started = time.monotonic()
    DATA_DIR.mkdir(exist_ok=True)
    date_from = date.today()
    date_to = date_from + timedelta(days=SCHEDULE_WINDOW_DAYS - 1)
    try:
        print(f"Pobieram rozkład PKP {date_from} - {date_to} (kilkadziesiąt MB)...")
        data = _fetch_schedule(date_from, date_to)
        build_database(data, NEW_DB_PATH)
    except Exception as e:
        # Stara baza zostaje nietknięta - wyszukiwarka kolejowa dalej działa
        # na wczorajszych danych zamiast się wyłączyć.
        print(f"BŁĄD aktualizacji rozkładu PKP: {e}", file=sys.stderr)
        NEW_DB_PATH.unlink(missing_ok=True)
        return False

    os.replace(NEW_DB_PATH, DB_PATH)
    print(f"Gotowe: {DB_PATH} ({time.monotonic() - started:.0f} s)")
    return True


# --------------------------------------------------------------------------
# Oficjalna mapa infrastruktury PLK (mapa.plk-sa.pl) - PIERWSZE, główne
# źródło współrzędnych: to dane samego zarządcy sieci kolejowej (warstwa
# "punkty eksploatacyjne"), nie zewnętrzny geokoder zgadujący po nazwie
# miejscowości. Sprawdzone na żywo (nazwa stacji przekazana przez
# użytkownika): dopasowanie po polu NAZWA_POS trafia w 92,7% z 3250 stacji
# PKP.
#
# WARSTWA WFS (surowe dane) wymaga zalogowania (401) - ten sam GetFeature
# przechodzi jednak przez PUBLICZNY endpoint WMS, jeśli poda się mu
# service=WFS w parametrach (sprawdzone na żywo). To nie jest udokumentowane
# zachowanie - GeoServer dzieli dyspozytora zapytań między usługami, więc
# działa, ale może przestać w dowolnej chwili, gdyby PLK to poprawiło; stąd
# osobna, ostrożna obsługa błędów (byle jaka odpowiedź -> None, nigdy
# wyjątek) i to źródło zawsze jako PIERWSZA próba, nie jedyna.
#
# WSPÓŁRZĘDNE w danych są w układzie PUWG 1992 (EPSG:2180, oficjalny układ
# geodezyjny Polski) - metry, nie stopnie - stąd pyproj do przeliczenia na
# WGS84 (EPSG:4326), którego używa reszta aplikacji.
# --------------------------------------------------------------------------

PLK_MAP_URL = "https://mapa.plk-sa.pl/geoserver/wms"
# "Wszystkie punkty eksploatacyjne", nie samo "STACJE" - ta druga warstwa
# obejmuje tylko stacje w wąskim, formalnym sensie (z rozjazdami), a PKP
# w swoim rozkładzie ma też zwykłe przystanki osobowe (RODZAJ="PO") i inne
# punkty - sprawdzone na żywo: "STACJE" dopasowuje się do 37% stacji PKP,
# "WSZYSTKIE" do 92,7%.
PLK_LAYER = "wektory:PUNKTY_EKSPLOATACYJNE_WSZYSTKIE"
PLK_CRS = "EPSG:2180"
PLK_PAGE_SIZE = 1000     # ~6 stron na całą Polskę (5763 punktów, sprawdzone na żywo)
PLK_TIMEOUT_SEC = 60


def _fetch_plk_points():
    """NAZWA_POS -> (lat, lon) dla WSZYSTKICH punktów eksploatacyjnych
    oficjalnej mapy PLK, po jednym na fizyczne miejsce - patrz nagłówek
    sekcji i _same_cluster: punkt bywa zapisany osobno na każdą linię, którą
    stacja obsługuje - to wciąż to samo miejsce, sprawdzone na żywo:
    796 z 800 nazw ze zduplikowanym punktem mieści się w promieniu 2 km;
    tylko 3 to naprawdę różne miejsca o tej samej nazwie w różnych
    częściach kraju, np. "Zwierzyniec"). None przy awarii KTÓREJKOLWIEK
    strony zapytania (stronicowane - WFS STARTINDEX/COUNT) - lepiej nic nie
    zwrócić niż połowę kraju, bo brakujące stacje i tak dostaną kolejną
    szansę z tańszych źródeł niżej.
    """
    transformer = Transformer.from_crs(PLK_CRS, "EPSG:4326", always_xy=True)

    features = []
    start = 0
    while True:
        url = PLK_MAP_URL + "?" + urllib.parse.urlencode({
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeName": PLK_LAYER, "outputFormat": "application/json",
            "count": PLK_PAGE_SIZE, "startIndex": start,
        })
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=PLK_TIMEOUT_SEC) as response:
                data = json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                OSError, ValueError):
            return None
        batch = data.get("features", [])
        features.extend(batch)
        if len(batch) < PLK_PAGE_SIZE:
            break
        start += PLK_PAGE_SIZE

    by_name = {}
    for feature in features:
        props = feature.get("properties") or {}
        name = props.get("NAZWA_POS") or props.get("NAZWA")
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates")
        if not name or not coords or len(coords) != 2:
            continue
        lon, lat = transformer.transform(coords[0], coords[1])
        by_name.setdefault(name, []).append((lat, lon))

    result = {}
    for name, points in by_name.items():
        if len(points) == 1 or _same_cluster(points):
            result[name] = points[0]
    return result


# --------------------------------------------------------------------------
# Klastrowanie punktów PLK o tej samej nazwie - patrz _fetch_plk_points.
# --------------------------------------------------------------------------

CLUSTER_RADIUS_KM = 2.0   # patrz _same_cluster


def _same_cluster(points):
    """Czy WSZYSTKIE punkty leżą w promieniu CLUSTER_RADIUS_KM od
    pierwszego - patrz nagłówek _fetch_plk_points (punkt PLK bywa zapisany
    osobno na każdą linię, którą stacja obsługuje - to wciąż jedno miejsce).
    Porównanie tylko do pierwszego (nie każdy z każdym) wystarcza: klaster
    tej samej stacji jest mały (dwa-trzy punkty blisko siebie), więc
    przechodniość w praktyce nie zawodzi, a to O(n) zamiast O(n²)."""
    lat0, lon0 = points[0]
    return all(
        _haversine_km(lat0, lon0, lat, lon) <= CLUSTER_RADIUS_KM
        for lat, lon in points[1:]
    )


# --------------------------------------------------------------------------
# Cache współrzędnych na dysku - drugi krok (portal pasażera) jest w osobnej
# sekcji niżej.
# --------------------------------------------------------------------------

GEOCODE_CACHE_PATH = DATA_DIR / "pkp_station_coords.json"
# Stacje, których ANI PLK, ANI portal pasażera nie znalazły (patrz
# geocode_missing_stations) - osobny plik, TYLKO do wglądu (nic z kodu go
# nie czyta z powrotem) - zgłoszone przez użytkownika, żeby nie trzeba było
# za każdym razem różnicować _read_stations względem cache'a ręcznie.
# Nadpisywany w całości przy każdym geocode_missing_stations (patrz jej
# koniec) - to zawsze AKTUALNY stan, nie historia.
UNMAPPED_STATIONS_PATH = DATA_DIR / "pkp_unmapped_stations.json"
GEOCODE_DELAY_SEC = 1.1   # tempo dla portalu pasażera (brak dokumentowanego API - ostrożność)
GEOCODE_SAVE_EVERY = 50   # zapis okresowy - przerwanie w trakcie nie traci wszystkiego


def _load_coords_cache():
    if not GEOCODE_CACHE_PATH.exists():
        return {}
    try:
        raw = json.loads(GEOCODE_CACHE_PATH.read_text(encoding="utf-8"))
        return {int(k): tuple(v) for k, v in raw.items()}
    except (OSError, ValueError, TypeError):
        return {}


def _save_coords_cache(cache):
    try:
        GEOCODE_CACHE_PATH.write_text(
            json.dumps({str(k): list(v) for k, v in cache.items()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _save_unmapped_stations(unmapped):
    """Zapisuje `unmapped` (station_id -> nazwa) do UNMAPPED_STATIONS_PATH,
    posortowane po nazwie - wyłącznie do wglądu (patrz jej nagłówek)."""
    try:
        UNMAPPED_STATIONS_PATH.write_text(
            json.dumps(
                {str(sid): name for sid, name in sorted(unmapped.items(), key=lambda kv: kv[1])},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


# --------------------------------------------------------------------------
# OpenStreetMap (Overpass) - TRZECIE źródło, po mapie PLK i portalu
# pasażera. Przywrócone na wyraźną prośbę użytkownika, ale INACZEJ niż
# usunięty wcześniej Nominatim (patrz nagłówek geocode_missing_stations
# i historia w docs/PROJECT.md, dlaczego w ogóle wypadł): to zapytanie
# o węzły OTAGOWANE JAKO INFRASTRUKTURA KOLEJOWA w warstwie transportu
# publicznego (tag `railway`), nie o dopasowanie tekstowe do nazwy
# miejscowości. Pyta się więc wprost "gdzie jest węzeł kolejowy o nazwie
# X", a nie "gdzie jest miejscowość X" - to właśnie to drugie pytanie
# (Nominatim) było źródłem błędów przy zbieżnych nazwach miejscowości
# w Polsce (Augustów, Widuchowa, ...).
# --------------------------------------------------------------------------

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Dwa schematy tagowania naraz, bo OSM ma oba w użyciu równolegle: stare
# railway=station/halt (węzeł na budynku/terenie stacji) i nowsze
# public_transport=stop_position (PTv2 - punkt DOKŁADNIE tam, gdzie pociąg
# się zatrzymuje na torze, często dokładniejszy niż stary węzeł). `out
# body` (nie `out center`) wystarcza, bo interesują nas tylko węzły
# punktowe.
#
# DWA OSOBNE zapytania (Polska / kraje sąsiednie), NIE jedno o wszystko na
# raz - sprawdzone na żywo: połączony zapytanie o 10 krajów (w tym Niemcy
# i Ukrainę - duże obszary) NIE MIEŚCI SIĘ w rozsądnym czasie, Overpass
# zwraca `remark: runtime error: Query timed out` po >220 s i 0 wyników,
# mimo że każdy z dwóch mniejszych zakresów osobno kończy się bez
# problemu. Ten sam podział, co w usuniętym wcześniej mechanizmie "stacje
# za granicą" (patrz historia w docs/PROJECT.md) - tu bez trwałego
# oznaczania "to na pewno zagranica": oba zapytania są tanie (raz na
# geokodowanie), więc nie ma po co dodawać tej złożoności z powrotem.
OVERPASS_TIMEOUT_SEC = 200
OVERPASS_QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="PL"][admin_level=2]->.pl;
(
  node["railway"="station"](area.pl);
  node["railway"="halt"](area.pl);
  node["public_transport"="stop_position"]["train"="yes"](area.pl);
);
out body;
"""

# Kraje sąsiednie, do których PKP ma połączenia międzynarodowe (widoczne
# wprost wśród nazw stacji, których ani PLK, ani portal pasażera nie mają -
# oba źródła są domenowo polskie). PO JEDNYM KRAJU NARAZ, NIE jedno
# połączone zapytanie o wszystkie 9 - sprawdzone na żywo (i potwierdzone
# przez użytkownika): serwer Overpass jest sprawny i normalnie odpowiada
# na małe/średnie zapytania, ale samo połączone zapytanie o 9 krajów naraz
# (w tym Niemcy i Ukrainę - duże obszary) REGULARNIE kończy się `runtime
# error: Query timed out` PO STRONIE Overpass (widoczne w polu `remark`,
# patrz _fetch_osm_nodes) - to nie awaria połączenia ani przeciążenie
# serwera jako takie, tylko sam ten jeden zapytanie jest za ciężkie, żeby
# zdążyć w rozsądnym czasie. Osobne, dużo lżejsze zapytanie na kraj kończy
# się dużo pewniej - a awaria pojedynczego kraju (np. akurat Ukrainy) nie
# blokuje już wyników z pozostałych ośmiu, tak jak blokowała przy jednym
# połączonym zapytaniu.
FOREIGN_AREA_CODES = ["DE", "CZ", "SK", "AT", "HU", "SI", "LT", "UA", "HR"]
OVERPASS_ABROAD_TIMEOUT_SEC = 90
OVERPASS_QUERY_ABROAD_TEMPLATE = """
[out:json][timeout:75];
area["ISO3166-1"="{code}"][admin_level=2]->.a;
(
  node["railway"="station"](area.a);
  node["railway"="halt"](area.a);
  node["public_transport"="stop_position"]["train"="yes"](area.a);
);
out body;
"""


def _fetch_osm_stations():
    """Nazwa -> (lat, lon) dla węzłów kolejowej warstwy transportu
    publicznego w Polsce (patrz OVERPASS_QUERY), jednym zapytaniem o
    wszystkie naraz."""
    return _fetch_osm_nodes(OVERPASS_QUERY, OVERPASS_TIMEOUT_SEC)


def _fetch_osm_stations_abroad():
    """Jak _fetch_osm_stations, ale dla krajów sąsiednich (patrz
    FOREIGN_AREA_CODES) - PO JEDNYM zapytaniu NA KRAJ, nie jednym
    połączonym (patrz nagłówek sekcji, dlaczego). Wyniki ze WSZYSTKICH
    krajów złączone w jeden słownik; awaria pojedynczego kraju (None
    z _fetch_osm_nodes) po prostu nie wnosi nic z tego kraju do wyniku -
    nie przerywa reszty ani nie liczy się jako całkowita awaria (None
    zwracane stąd TYLKO, gdy padły WSZYSTKIE kraje na raz - patrz koniec
    funkcji)."""
    combined = {}
    any_succeeded = False
    for code in FOREIGN_AREA_CODES:
        query = OVERPASS_QUERY_ABROAD_TEMPLATE.format(code=code)
        result = _fetch_osm_nodes(query, OVERPASS_ABROAD_TIMEOUT_SEC)
        if result is None:
            continue
        any_succeeded = True
        for name, coords in result.items():
            combined.setdefault(name, coords)
    return combined if any_succeeded else None


# Stacje ZAGRANICZNE w rozkładzie PKP mają nazwy SKRÓCONE i/lub ASCII
# (bez znaków diakrytycznych) - zgłoszone przez użytkownika na żywo: "Hbf"
# zamiast "Hauptbahnhof" (Niemcy), a poza tym: "Muenchen" zamiast "München"
# (niemiecka transliteracja ASCII "ue"/"oe"/"ae" dla umlautów - inna niż
# zwykłe zdjęcie akcentu), "Praha-Liben" zamiast "Praha-Libeň", "Ceska
# Trebova" zamiast "Česká Třebová", "Hlavni Nadrazi" zamiast "hlavní
# nádraží" (czeski/słowacki - to akurat SAMO zdjęcie akcentów, nie skrót).
# Węzeł OSM ma pełną, poprawną nazwę w oryginalnym języku - dopasowanie
# WPROST (jak dotąd) nigdy by więc nie trafiło. _normalize_for_osm_match
# ujednolica OBIE strony (nazwę z OSM przy budowaniu by_name I nazwę
# z rozkładu PKP przy szukaniu w nim) tym samym przekształceniem, więc
# obie mają szansę się spotkać.
_GERMAN_TRANSLITERATION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
# Tylko CAŁE SŁOWA (nie podciąg) - zamieniane PRZED zdjęciem akcentów,
# stąd klucze już bez akcentów (np. nie böhmisch, bo tak i tak trafiłby
# w "boehmisch" po transliteracji niemieckiej wyżej - w praktyce PKP nie
# miesza dwóch konwencji w jednym słowie). "nadr" (czeskie/słowackie
# "Nadr." = "nádraží", dworzec) sprawdzone na żywo: "Jablonec nad Nisou
# Dolni Nadr." - samo zdjęcie akcentów daje "dolni nadr", OSM ma pełne
# słowo "nádraží" -> "nadrazi", więc bez tego wpisu i tak by się minęły.
_STATION_ABBREVIATIONS = {"hbf": "hauptbahnhof", "bf": "bahnhof", "nadr": "nadrazi"}


def _normalize_for_osm_match(name):
    """Wspólna postać `name` do porównania stacji PKP z węzłem OSM - patrz
    komentarz nad _STATION_ABBREVIATIONS. Kolejność: transliteracja
    niemiecka (na wypadek, gdyby zostały jakieś umlauty - w nazwach PKP
    zwykle już ASCII, ale węzeł OSM ma je NAPRAWDĘ, patrz wywołanie
    w _fetch_osm_nodes) -> zdjęcie WSZYSTKICH pozostałych znaków
    diakrytycznych (NFKD, kategoria Mn - Czechy/Słowacja/Polska naraz,
    jedna reguła zamiast osobnej per język) -> casefold -> rozwinięcie
    skrótów całosłownie -> spacje jako jedyny separator (PKP czasem pisze
    "St.Poelten" bez spacji po kropce, OSM zawsze ze spacją - kropka i tak
    nie niesie tu informacji)."""
    decomposed = unicodedata.normalize("NFKD", name.translate(_GERMAN_TRANSLITERATION))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    words = stripped.casefold().replace(".", " ").split()
    return " ".join(_STATION_ABBREVIATIONS.get(w, w) for w in words)


def _fetch_osm_nodes(query, timeout_sec):
    """Silnik współdzielony przez _fetch_osm_stations i
    _fetch_osm_stations_abroad - samo zapytanie do Overpass plus dedukcja
    nazw do węzłów, albo None przy awarii zapytania (sieć, przekroczony
    czas HTTP, ALBO wewnętrzny timeout Overpass - patrz niżej).

    UWAGA sprawdzona na żywo: Overpass, gdy PRZEKROCZY swój WŁASNY,
    zadeklarowany w zapytaniu limit czasu ([timeout:N] w samym query),
    odpowiada HTTP 200 z POPRAWNYM JSON-em - `elements: []` i polem
    `remark` opisującym błąd ("runtime error: Query timed out...") -
    zamiast rzucić wyjątek sieciowy. Bez sprawdzenia tego pola taki
    przypadek wygląda identycznie jak "zapytanie się udało, po prostu nic
    nie ma" (pusty słownik), co jest FAŁSZYWYM ustaleniem "nie znaleziono"
    zamiast prawdziwego "nie sprawdzono" - stacja dostałaby permanentny
    wpis w pkp_unmapped_stations.json zamiast kolejnej szansy przy
    następnym geokodowaniu. `remark` obecny -> traktujemy jak każdą inną
    awarię (None).

    Nazwa może należeć do WIĘCEJ NIŻ JEDNEGO węzła z dwóch różnych
    powodów, które trzeba rozróżnić, nie tylko policzyć: TA SAMA stacja
    otagowana dwoma schematami naraz (węzły leżą blisko siebie - patrz
    _same_cluster/CLUSTER_RADIUS_KM, ten sam mechanizm klastrowania co przy
    mapie PLK) - to jedno miejsce, bierzemy pierwszy węzeł; albo NAPRAWDĘ
    różne stacje w różnych częściach obszaru zapytania o zbieżnej nazwie -
    węzły leżą daleko od siebie - nazwa jest odrzucana jako niejednoznaczna,
    zła współrzędna jest gorsza niż jej brak (zostaje w
    pkp_unmapped_stations.json, spróbuje ponownie przy kolejnym
    geokodowaniu)."""
    url = OVERPASS_URL + "?" + urllib.parse.urlencode({"data": query})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            data = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            OSError, ValueError):
        return None
    if data.get("remark"):
        return None

    by_name = {}
    for element in data.get("elements", ()):
        tags = element.get("tags") or {}
        lat, lon = element.get("lat"), element.get("lon")
        if lat is None or lon is None:
            continue
        # WSZYSTKIE nazwy węzła, nie tylko `name` - zgłoszone przez
        # użytkownika: stacje mają w OSM nazwę w języku NATYWNYM (`name`),
        # a rozkład PKP dla stacji zagranicznych - łacińską transliterację/
        # nazwę międzynarodową (widoczne wprost: "Kyiv-Pasazhyrskyi" wobec
        # ukraińskiego `name` w cyrylicy) - dopasowanie WYŁĄCZNIE po `name`
        # nie miałoby tam żadnych szans, niezależnie od normalizacji
        # (_normalize_for_osm_match nie umie przepisać cyrylicy na alfabet
        # łaciński). `name:en`/`int_name` to właśnie międzynarodowa/łacińska
        # nazwa węzła, gdy ktoś w OSM ją dodał - dodatkowa, nie zamienna
        # szansa na trafienie, bez cofania się do dopasowania tekstowego
        # po dowolnej nazwie miejscowości (to był problem Nominatim, patrz
        # historia w docs/PROJECT.md) - to wciąż ten sam węzeł, tylko więcej
        # jego udokumentowanych nazw.
        names = {tags.get("name"), tags.get("name:en"), tags.get("int_name")}
        for name in names:
            if not name:
                continue
            # Klucz to znormalizowana nazwa (patrz _normalize_for_osm_match),
            # nie surowa nazwa OSM - żeby dopasować rozkładowi PKP (skróty,
            # ASCII zamiast znaków diakrytycznych). Dwa RÓŻNE surowe nazwy OSM
            # (czy to dwóch różnych węzłów, czy `name` i `name:en` TEGO
            # SAMEGO węzła) normalizujące się do tego samego klucza trafiają
            # więc do jednego klastra - to nie problem, bo i tak przechodzą
            # przez tę samą kontrolę odległości (_same_cluster) co zwykły
            # duplikat.
            by_name.setdefault(_normalize_for_osm_match(name), []).append((lat, lon))

    result = {}
    for name, points in by_name.items():
        if len(points) == 1 or _same_cluster(points):
            result[name] = points[0]
    return result


PORTAL_PASAZERA_URL = "https://portalpasazera.pl/KatalogStacji/Index"


def _fetch_portalpasazera_point(name):
    """(lat, lon) dla POJEDYNCZEJ stacji z Katalogu Stacji na
    portalpasazera.pl - oficjalnego portalu sprzedaży biletów PKP. Zgłoszone
    przez użytkownika na żywo (Góra Śląska, której nie miała mapa PLK -
    https://portalpasazera.pl/KatalogStacji/Index?stacja=Gora+Slaska
    faktycznie ją ma).

    DRUGI, ostatni krok w geocode_missing_stations - zaraz PO mapie PLK.
    Wołany tylko dla garstki, której PLK nie znalazła - w odróżnieniu od
    PLK ten katalog nie ma zbiorczego zapytania (brak publicznego API,
    tylko strona WWW na pojedynczą stację), więc nie ma sensu odpytywać nim
    stacji, które PLK już załatwiła. Wyłącznie stacje POLSKIE - katalog nie
    ma zagranicznych (sprawdzone na żywo: "Kyiv-Pasazhyrskyi" przekierowuje
    na listę bez wyników) - stacja za granicą po prostu zostaje bez
    współrzędnych (patrz nagłówek modułu).

    Strona po znalezieniu stacji ma wprost w HTML-u (sprawdzone na żywo):
    `<span class="item-label txlc">Współrzędne GPS</span>` a zaraz po nim
    `<strong class="item-value">SZEROKOŚĆ<br />DŁUGOŚĆ` (przecinek jako
    separator dziesiętny, po polsku) - brak tej etykiety (stacja
    nierozpoznana - przekierowanie na katalog bez wyników) albo
    nieoczekiwany kształt liczby po niej to po prostu "nie znaleziono",
    tak samo jak przy mapie PLK."""
    url = PORTAL_PASAZERA_URL + "?" + urllib.parse.urlencode({"stacja": name})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=PLK_TIMEOUT_SEC) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None
    finally:
        # Tempo pilnowane TUTAJ (nie w pętli geocode_missing_stations) -
        # żeby testy mockujące całą tę funkcję (patrz TestGeocodeMissingStations*)
        # nie czekały bez potrzeby.
        time.sleep(GEOCODE_DELAY_SEC)
    idx = html.find("Współrzędne GPS")
    if idx == -1:
        return None
    match = re.search(r'item-value">\s*([\d,]+)\s*<br\s*/?>\s*([\d,]+)', html[idx:idx + 400])
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ".")), float(match.group(2).replace(",", "."))
    except ValueError:
        return None


# Stacje, dla których sama NAZWA jest niejednoznaczna między Polską
# a zagranicą - zgłoszone przez użytkownika na żywo: station_id 128006
# "Kolin" to prawdziwy CZESKI węzeł kolejowy (Kolín) na trasie
# międzynarodowej, ale ta sama, gola nazwa "Kolin" należy też do zupełnie
# innej, prawdziwej POLSKIEJ wsi (station_id 2329, koło Choszczna,
# poprawnie zgeokodowanej pod tą samą nazwą przez portal pasażera).
# Domyślne dopasowanie PO NAZWIE (PLK/portal, oba źródła czysto polskie)
# nie ma jak ich rozróżnić - regularnie przypisywało 128006 współrzędne
# polskiej wsi, które i tak zaraz odrzucała walidacja tras (patrz
# find_suspect_coords - sąsiedzi 128006 na trasie są ~350 km od tej wsi),
# więc stacja w kółko wracała jako "nieznaleziona" zamiast dostać szansę
# na PRAWDZIWE dopasowanie za granicą. Ręczna adnotacja (kod kraju czysto
# informacyjny/do wglądu - patrz komentarz w geocode_missing_stations, co
# faktycznie z nią robi): taki station_id pomija oba źródła POLSKIE (PLK,
# portal) i domową warstwę OSM od razu, idąc prosto do OpenStreetMap
# (zagranica) - tam nazwa "Kolin"/"Kolín" trafia we właściwy, czeski węzeł
# bez kolizji z polską wsią.
KNOWN_FOREIGN_STATIONS = {128006: "CZ"}


def geocode_missing_stations(stations_by_id):
    """Uzupełnia data/pkp_station_coords.json o brakujące stacje.

    TRZY źródła w tej kolejności - od najbardziej OFICJALNEGO do
    najogólniejszego. Najpierw oficjalna mapa infrastruktury PLK
    (_fetch_plk_points) - dane samego zarządcy sieci, jedno zbiorcze
    zapytanie o wszystkie stacje naraz. To, czego tam nie ma (mapa PLK
    obejmuje tylko punkty, które PLK sam sklasyfikował jako
    "eksploatacyjne" - nie musi to być 1:1 ze stacjami z rozkładu PKP),
    dogania portal pasażera PKP (_fetch_portalpasazera_point) - per stacja,
    więc tylko dla tego, czego PLK nie znalazła; obejmuje wyłącznie Polskę
    (patrz jej nagłówek). To, czego oba źródła PKP/PLK nie mają - typowo
    stacja ZA GRANICĄ (PKP ma połączenia międzynarodowe, a oba źródła są
    domenowo polskie) - dogania na końcu OpenStreetMap: węzły OTAGOWANE
    jako infrastruktura kolejowa, nie dopasowanie tekstowe do nazwy
    miejscowości (patrz nagłówek _fetch_osm_nodes, czym różni się od
    usuniętego wcześniej Nominatim - historia w docs/PROJECT.md). DWA
    OSOBNE zapytania - najpierw Polska (_fetch_osm_stations), potem kraje
    sąsiednie naraz (_fetch_osm_stations_abroad) - jedno połączone zapytanie
    o wszystko na raz nie mieści się w rozsądnym czasie (sprawdzone na
    żywo - patrz nagłówek sekcji OSM). Stacja, której ŻADNE z czterech
    kroków nie ma, zostaje bez współrzędnych: bezpieczniejsze niż
    zgadywanie (patrz nagłówek modułu). Taka stacja trafia też (nazwa i id)
    do osobnego pliku do wglądu - patrz UNMAPPED_STATIONS_PATH/
    _save_unmapped_stations, wołane na końcu funkcji niezależnie od tego,
    czy coś było do geokodowania - plik zawsze odzwierciedla AKTUALNY stan,
    nadpisywany w całości, nie dopisywany.

    Jednorazowa (per stacja) usługa: raz znaleziona stacja nigdy nie jest
    odpytywana drugi raz - kolejne wywołania (po każdej codziennej
    aktualizacji rozkładu) dogadują już tylko naprawdę nowe stacje.

    Stacja z KNOWN_FOREIGN_STATIONS (patrz jej nagłówek - ręcznie
    potwierdzone przypadki, w których sama nazwa myli polskie źródła
    z zagranicznym miejscem o tej samej nazwie) pomija PLK, portal i OSM
    (Polska) od razu - idzie prosto do OSM (zagranica), tak samo jak
    stacja bez żadnej nazwy-kolizji, dla której te źródła po prostu nic
    nie znalazły.
    """
    cache = _load_coords_cache()
    missing = {sid: name for sid, name in stations_by_id.items() if sid not in cache}
    if not missing:
        _save_unmapped_stations({})
        return

    # Patrz KNOWN_FOREIGN_STATIONS - dołączają z powrotem do `missing`
    # tuż przed krokiem OSM (zagranica), pomijając PLK/portal/OSM (Polska).
    known_foreign = {sid: name for sid, name in missing.items() if sid in KNOWN_FOREIGN_STATIONS}
    for sid in known_foreign:
        del missing[sid]

    found_plk = found_portal = found_osm = 0
    if missing:
        print(f"Mapuję {len(missing)} nowych stacji - najpierw mapa PLK...")
        plk_by_name = _fetch_plk_points()
        if plk_by_name is None:
            print("  OSTRZEŻENIE: zapytanie do mapy PLK padło - "
                  "całość idzie przez portal pasażera.")
        else:
            for station_id, name in list(missing.items()):
                coords = plk_by_name.get(name)
                if coords is not None:
                    cache[station_id] = coords
                    found_plk += 1
                    del missing[station_id]
            _save_coords_cache(cache)
            print(f"  Mapa PLK: {found_plk} znalezionych, "
                  f"{len(missing)} zostaje dla portalu pasażera.")

    if missing:
        total = len(missing)
        print(f"Mapuję {total} stacji przez portal pasażera PKP...")
        for i, (station_id, name) in enumerate(list(missing.items()), 1):
            coords = _fetch_portalpasazera_point(name)   # tempo pilnuje ona sama
            if coords is not None:
                cache[station_id] = coords
                found_portal += 1
                del missing[station_id]
            if i % GEOCODE_SAVE_EVERY == 0:
                _save_coords_cache(cache)
                print(f"  {i}/{total} ({found_portal} znalezionych)")
        _save_coords_cache(cache)
        print(f"  Portal pasażera: {found_portal} znalezionych, "
              f"{len(missing)} zostaje dla OpenStreetMap.")

    if missing:
        print(f"Mapuję {len(missing)} stacji przez OpenStreetMap (Polska)...")
        osm_by_name = _fetch_osm_stations()
        if osm_by_name is None:
            print("  OSTRZEŻENIE: zapytanie do OpenStreetMap (Polska) padło.")
        else:
            for station_id, name in list(missing.items()):
                coords = osm_by_name.get(_normalize_for_osm_match(name))
                if coords is not None:
                    cache[station_id] = coords
                    found_osm += 1
                    del missing[station_id]
            _save_coords_cache(cache)
            print(f"  OpenStreetMap (Polska): {found_osm} znalezionych, "
                  f"{len(missing)} zostaje dla zagranicy.")

    # Dołącza tu, nie na starcie funkcji - patrz KNOWN_FOREIGN_STATIONS
    # i nagłówek funkcji: ma pominąć wszystkie "polskie" kroki wyżej.
    missing.update(known_foreign)

    found_osm_abroad = 0
    if missing:
        print(f"Mapuję {len(missing)} stacji przez OpenStreetMap (zagranica)...")
        osm_abroad_by_name = _fetch_osm_stations_abroad()
        if osm_abroad_by_name is None:
            print("  OSTRZEŻENIE: zapytanie do OpenStreetMap (zagranica) padło.")
        else:
            for station_id, name in list(missing.items()):
                coords = osm_abroad_by_name.get(_normalize_for_osm_match(name))
                if coords is not None:
                    cache[station_id] = coords
                    found_osm_abroad += 1
                    del missing[station_id]
            _save_coords_cache(cache)
            print(f"  OpenStreetMap (zagranica): {found_osm_abroad} znalezionych, "
                  f"{len(missing)} pozostaje nieznalezionych.")

    _save_unmapped_stations(missing)
    print(f"Gotowe: {found_plk} z mapy PLK + {found_portal} z portalu pasażera + "
          f"{found_osm} z OpenStreetMap (Polska) + "
          f"{found_osm_abroad} z OpenStreetMap (zagranica) "
          f"({len(missing)} nieznalezionych - zostają bez markera, "
          f"lista w {UNMAPPED_STATIONS_PATH.name}).")


def _is_city_wildcard(name):
    """Czy `name` to wpis "dowolna stacja w mieście X" z rozkładu PKP, NIE
    prawdziwa, pojedyncza stacja - zgłoszone przez użytkownika: PKP
    oznacza takie wpisy WIELKIMI LITERAMI z myślnikiem na końcu (sprawdzone
    na żywo: "WARSZAWA -", "BERLIN -", "MŁAWA-", ...) - to uproszczenie dla
    połączeń bezpośrednich do DOWOLNEJ stacji w danym mieście, nie jedno
    konkretne miejsce, więc nie ma czego geokodować (patrz _read_stations,
    jedyne miejsce, gdzie ten filtr jest stosowany)."""
    stripped = name.rstrip()
    return bool(stripped) and stripped.endswith("-") and stripped == stripped.upper()


def _read_stations(db_path):
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute("SELECT station_id, name FROM stations")
        return {sid: name for sid, name in rows if not _is_city_wildcard(name)}
    finally:
        db.close()


def reconcile_with_plk(stations_by_id):
    """Nadpisuje w cache'u KAŻDĄ stację, którą uda się znaleźć na oficjalnej
    mapie PLK (patrz _fetch_plk_points) - jedynym źródle na tyle
    wiarygodnym, że warto nim REWALIDOWAĆ już zgadane współrzędne, nie
    tylko uzupełniać brakujące (w odróżnieniu od geocode_missing_stations).
    Przydatne np. gdy mapa PLK dostanie nowy/poprawiony punkt dla stacji,
    którą wcześniej znalazł tylko portal pasażera pod nieco inną pozycją.

    Zwraca (zmienione, sprawdzone) - `zmienione` to nowe ALBO inne niż
    wcześniej współrzędne; `sprawdzone` to ile stacji PLK w ogóle miała
    czym porównać, niezależnie od tego, czy coś się zmieniło - do raportu
    w main(). (0, 0), gdy zapytanie do mapy PLK padło."""
    plk_by_name = _fetch_plk_points()
    if not plk_by_name:
        return 0, 0
    cache = _load_coords_cache()
    changed = checked = 0
    for station_id, name in stations_by_id.items():
        coords = plk_by_name.get(name)
        if coords is None:
            continue
        checked += 1
        if cache.get(station_id) != coords:
            cache[station_id] = coords
            changed += 1
            print(station_id, name)
    if changed:
        _save_coords_cache(cache)
    return changed, checked


# --------------------------------------------------------------------------
# Sanity check współrzędnych: zarówno mapa PLK, jak i portal pasażera
# dopasowują po SAMEJ NAZWIE, więc żadne z nich nie ma jak wiedzieć, że
# w Polsce bywa WIĘCEJ NIŻ JEDNO miejsce o tej samej nazwie - sprawdzone na
# żywo w obu źródłach: dawniejszy Nominatim/OSM łapał "Chałupy" (prawdziwa
# stacja na Helu) ~500 km dalej, na Śląsku; portal pasażera dla stacji
# "Kolin" (czeski węzeł na trasie międzynarodowej) zwraca zupełnie inną,
# przypadkowo tak samo nazwaną polską wieś koło Choszczna. Trasa kolejowa
# daje za to darmową, niezależną kontrolę: DWA SĄSIEDNIE przystanki na tej samej
# linii (ten sam schedule_id/order_id, order_number różniący się o 1-2) nie
# są w prawdziwym świecie oddalone o więcej niż NEIGHBOR_DISTANCE_THRESHOLD_KM -
# odległość ponad ten próg między nimi to twardy sygnał złego geokodowania,
# nie zbieg okoliczności.
# --------------------------------------------------------------------------

NEIGHBOR_DISTANCE_THRESHOLD_KM = 100
NEIGHBOR_MAX_GAP = 2   # o ile order_number sąsiadów smie się różnić - patrz wyżej


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _route_neighbor_distances(db_path, coords):
    """station_id -> {sąsiad_id: odległość_km} dla par przystanków
    sąsiadujących na tej samej trasie (patrz nagłówek sekcji), obu ze
    znanymi współrzędnymi. Ta sama para z kilku różnych tras/dni zostaje
    z najmniejszą zmierzoną odległością (jedna zbieżność wystarczy, żeby
    parę uznać za "blisko")."""
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute(
            "SELECT schedule_id, order_id, station_id, order_number "
            "FROM stops ORDER BY schedule_id, order_id, order_number"
        ).fetchall()
    finally:
        db.close()

    routes = {}
    for schedule_id, order_id, station_id, order_number in rows:
        routes.setdefault((schedule_id, order_id), []).append((order_number, station_id))

    neighbors = {}
    for stops in routes.values():
        stops.sort()
        for (on1, id1), (on2, id2) in zip(stops, stops[1:]):
            if id1 == id2 or on2 - on1 > NEIGHBOR_MAX_GAP:
                continue
            if id1 not in coords or id2 not in coords:
                continue
            d = _haversine_km(*coords[id1], *coords[id2])
            best1 = neighbors.setdefault(id1, {})
            best1[id2] = min(best1.get(id2, math.inf), d)
            best2 = neighbors.setdefault(id2, {})
            best2[id1] = min(best2.get(id1, math.inf), d)
    return neighbors


def find_suspect_coords(db_path, coords):
    """Id-y stacji, których współrzędne są prawdopodobnie błędne: WIĘKSZOŚĆ
    ich sąsiadów na trasach (patrz _route_neighbor_distances) leży dalej niż
    NEIGHBOR_DISTANCE_THRESHOLD_KM. Większość, nie "choć jeden daleki sąsiad" -
    to SĄSIAD bywa tym błędnym (para jest symetryczna, obie strony dostają
    ten sam sygnał "daleko"); stacja, u której zgadza się większość
    niezależnych sąsiadów z różnych tras, prawie na pewno jest tą dobrą."""
    neighbors = _route_neighbor_distances(db_path, coords)
    suspects = []
    for station_id, dists in neighbors.items():
        far = sum(1 for d in dists.values() if d > NEIGHBOR_DISTANCE_THRESHOLD_KM)
        if far > len(dists) / 2:
            suspects.append(station_id)
    print(suspects)
    return suspects


def purge_suspect_coords():
    """Usuwa z cache'u współrzędne uznane za błędne (patrz find_suspect_coords) -
    zostają "brakujące" i geocode_missing_stations spróbuje je znów przy
    następnym uruchomieniu. Zwraca liczbę usuniętych wpisów.

    Zła współrzędna jest gorsza niż jej brak: pokazuje stację kolejową
    w zupełnie innej części Polski, niż naprawdę jest - użytkownik nie ma
    jak się zorientować, że marker kłamie, dopóki nie sprawdzi na miejscu.
    Brak markera przynajmniej nie wprowadza w błąd."""
    if not DB_PATH.exists():
        return 0
    cache = _load_coords_cache()
    if not cache:
        return 0
    suspects = find_suspect_coords(DB_PATH, cache)
    for station_id in suspects:
        cache.pop(station_id, None)
    if suspects:
        _save_coords_cache(cache)
    return len(suspects)


def run_geocode():
    if not pkp.enabled() or not DB_PATH.exists():
        return
    stations = _read_stations(DB_PATH)
    geocode_missing_stations(stations)
    # Uzgodnienie PO uzupełnieniu brakujących, PRZED czyszczeniem podejrzanych -
    # stacja, którą portal pasażera zgadł gorzej, a którą mapa PLK
    # w międzyczasie zdążyła dostać (nowy/poprawiony punkt), ma dostać
    # poprawkę zamiast tylko zostać wyczyszczona na "brak".
    changed_plk, checked_plk = reconcile_with_plk(stations)
    if changed_plk:
        print(f"Uzgodniono z mapą PLK: {changed_plk}/{checked_plk} zmienionych "
              f"współrzędnych (stacje zgadane wcześniej, którym mapa PLK dała "
              f"teraz inną odpowiedź).")
    removed = purge_suspect_coords()
    if removed:
        print(f"Usunięto {removed} współrzędnych niezgodnych z sąsiadami na "
              f"trasie (patrz find_suspect_coords) - spróbują ponownie przy "
              f"następnym uruchomieniu.")


def _update_and_geocode():
    if run_update():
        run_geocode()


# --------------------------------------------------------------------------
# Trzy wejścia (patrz nagłówek modułu) - ten sam schemat co w update_gtfs.py.
# --------------------------------------------------------------------------

def db_age_hours():
    """Wiek lokalnego rozkładu PKP w godzinach albo None, gdy jeszcze go nie ma."""
    if not DB_PATH.exists():
        return None
    return (time.time() - DB_PATH.stat().st_mtime) / 3600


def refresh_on_start(max_age_hours=None):
    """Aktualizacja rozkładu PKP przy starcie serwera. Zwraca wątek albo None.

    Geokodowanie stacji (wolne, patrz geocode_missing_stations) jest zawsze
    w tle, niezależnie od tego, która gałąź niżej odpala - nie ma prawa
    opóźniać ani startu serwera, ani samego rozkładu.

    PKP_UPDATE_ON_START=off wyłącza całość, PKP_MAX_AGE_HOURS zmienia próg.
    """
    if not pkp.enabled():
        return None
    if os.environ.get("PKP_UPDATE_ON_START", "on").lower() == "off":
        return None

    if max_age_hours is None:
        try:
            max_age_hours = float(os.environ.get("PKP_MAX_AGE_HOURS", DEFAULT_MAX_AGE_HOURS))
        except ValueError:
            max_age_hours = DEFAULT_MAX_AGE_HOURS

    age = db_age_hours()
    if age is None:
        print("Brak lokalnego rozkładu PKP - pobieram (kilkanaście sekund)...")
        # Cache współrzędnych (GEOCODE_CACHE_PATH) jest OSOBNYM plikiem od
        # samej bazy rozkładu (patrz jej nagłówek) - przeżywa więc usunięcie/
        # brak pkp.sqlite. Wypisanie tu, ile stacji już ma znane
        # współrzędne, daje od razu obraz postępu geokodowania (wolnego,
        # rozłożonego na wiele uruchomień - patrz geocode_missing_stations),
        # zamiast czekać w ciemno, aż coś się w ogóle pojawi.
        mapped = len(_load_coords_cache())
        if mapped:
            print(f"  (z poprzednich uruchomień już zmapowanych: {mapped} stacji)")
        if not run_update():
            print("OSTRZEŻENIE: nie udało się pobrać rozkładu PKP.", file=sys.stderr)
            return None
        thread = threading.Thread(target=run_geocode, name="pkp-geocode", daemon=True)
        thread.start()
        return thread

    if age < max_age_hours:
        print(f"Rozkład PKP sprzed {age:.1f} h - pomijam aktualizację przy starcie.")
        # Świeży rozkład NIE znaczy świeży cache współrzędnych - to dwa
        # osobne pliki (patrz nagłówek _fetch_portalpasazera_point/
        # GEOCODE_CACHE_PATH), więc usunięcie/wyczyszczenie samego cache'a
        # (np. ręcznie) przy zachowanym pkp.sqlite kiedyś kończyło się
        # ciszą - ta gałąź w ogóle nie wołała run_geocode, wbrew temu, co
        # obiecuje nagłówek tej funkcji ("zawsze w tle"). Uzupełnianie
        # brakujących współrzędnych to i tak tania operacja, gdy nic nie
        # brakuje (geocode_missing_stations wraca natychmiast - patrz jej
        # nagłówek), więc bezpieczne w każdej gałęzi.
        thread = threading.Thread(target=run_geocode, name="pkp-geocode", daemon=True)
        thread.start()
        return thread

    print(f"Rozkład PKP sprzed {age:.1f} h - odświeżam w tle (serwer działa na obecnym).")
    thread = threading.Thread(target=_update_and_geocode, name="pkp-update", daemon=True)
    thread.start()
    return thread


def _next_run_at(hour, now=None):
    now = now or datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def scheduled_hour(value=None):
    """Godzina z PKP_AUTO_UPDATE_HOUR jako int 0-23, albo None = bez harmonogramu."""
    if value is None:
        value = os.environ.get("PKP_AUTO_UPDATE_HOUR", "")
    value = str(value).strip()
    if not value:
        return None

    try:
        hour = int(value)
    except ValueError:
        hour = -1
    if not 0 <= hour <= 23:
        print(
            f"PKP_AUTO_UPDATE_HOUR={value!r} to nie godzina 0-23 "
            "- automatyczna aktualizacja rozkładu PKP wyłączona.",
            file=sys.stderr,
        )
        return None
    return hour


def _run_update_subprocess():
    """Aktualizacja (rozkład + geokodowanie) w osobnym procesie, nie w tym
    wątku - ten sam powód co w update_gtfs.py, tylko ważniejszy: rozkład
    całego kraju parsuje się do struktur Pythona rzędu kilkuset MB, a taka
    pamięć w procesie serwera zostałaby z nim aż do restartu."""
    subprocess.run([sys.executable, str(Path(__file__).resolve())], check=False)


def _daily_loop(hour):
    while True:
        target = _next_run_at(hour)
        print(f"Kolejna automatyczna aktualizacja rozkładu PKP: {target:%Y-%m-%d %H:%M} "
              f"(PKP_AUTO_UPDATE_HOUR={hour}).", flush=True)

        while True:
            remaining = (target - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, SCHEDULER_TICK_SEC))

        try:
            _run_update_subprocess()
        except Exception as e:
            print(f"BŁĄD harmonogramu aktualizacji PKP: {e}", file=sys.stderr, flush=True)


def start_daily_scheduler(hour=None):
    """Wątek odświeżający rozkład PKP codziennie o `hour`. Zwraca wątek albo None.

    Wołane raz na proces serwera: z on_starting w gunicorn.conf.py (master,
    przed forkiem workerów) i z app.py przy uruchomieniu lokalnym."""
    if not pkp.enabled():
        return None
    hour = scheduled_hour(hour)
    if hour is None:
        return None

    thread = threading.Thread(
        target=_daily_loop, args=(hour,), name="pkp-scheduler", daemon=True
    )
    thread.start()
    return thread


def main():
    """Wywołanie ręczne/z crona/z docker/entrypoint.sh.

    Flagi dla entrypoint.sh: przy PIERWSZYM uruchomieniu w kontenerze budowa
    rozkładu ma być blokująca (nie ma czego serwować bez niej), ale
    godzinne geokodowanie - nie (serwer ma wstać od razu, markery stacji
    PKP dojdą na mapę stopniowo). Stąd dwie osobne flagi zamiast zawsze
    robić oba kroki naraz - patrz docker/entrypoint.sh.
    """
    if "--schedule-only" in sys.argv:
        sys.exit(0 if run_update() else 1)
    if "--geocode-only" in sys.argv:
        run_geocode()
        sys.exit(0)
    if "--reconcile-plk" in sys.argv:
        # Tylko uzgodnienie z mapą PLK (patrz reconcile_with_plk) - bez
        # czekania na cały run_geocode.
        if not DB_PATH.exists():
            print("Brak lokalnego rozkładu PKP - nie ma czego uzgadniać.")
            sys.exit(1)
        changed, checked = reconcile_with_plk(_read_stations(DB_PATH))
        print(f"Uzgodniono z mapą PLK: {changed}/{checked} zmienionych współrzędnych.")
        sys.exit(0)

    ok = run_update()
    if ok:
        run_geocode()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
