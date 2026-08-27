"""Rozkład Siechnickiej Komunikacji Publicznej - dociągany osobno i doklejany
do bazy zbudowanej z wrocławskiego GTFS-a (patrz update_gtfs.py).

DLACZEGO OSOBNO. Linie gminy Siechnice (800, 810, 83..89, 860, 870, 890)
NIE MA w żadnym otwartym zbiorze danych. Sprawdzone: dane.gov.pl nie zna
takiego zbioru, Krajowy Punkt Dostępowy (KPD/MMTIS) nie wymienia ani gminy
Siechnice, ani przewoźnika TRAKO, wrocławski GTFS ich nie zawiera, a agregatory
(mkuran.pl, transit.land, Mobility Database) nie mają czego zaindeksować.
Gmina przekazuje rozkłady bilateralnie - jakdojade.pl dostaje je na podstawie
porozumienia z Wydziałem Komunalnym UM Siechnice z 2019 r.

Jedyne strukturalne źródło, jakie istnieje, to JSON-owe API systemu
kiedyPrzyjedzie (dostawca: Operibus sp. z o.o.), którym gmina obsługuje
informację pasażerską. Jest niedokumentowane i - co ważne - jego robots.txt
to "Disallow: /", a nigdzie nie ma regulaminu ani zgody na ponowne
wykorzystanie. Dlatego pobieranie jest DOMYŚLNIE WYŁĄCZONE i włącza się je
świadomie (SIECHNICE_ENABLED=on). Docelowo poprosić gminę o eksport GTFS -
platforma Operibus już go u innych klientów wystawia (Zduńska Wola, Oborniki
figurują w tabeli KPD z formatem "GTFS static/realtime"), więc to kwestia
włączenia funkcji, a nie budowania czegokolwiek. Szczegóły i wzór pisma:
docs/SIECHNICE_DANE.md.

KONTRAKT API (odtworzony z bundla aplikacji, stan: sierpień 2026):
  GET /stops?rev=<n>                      - wszystkie słupki: [designator, kod,
                                            nazwa, lon*1e6, lat*1e6, ...]
  GET /api/directions/<designator>        - linie i kierunki na tym słupku
  GET /api/timetables/<designator>?date=  - cały dzień odjazdów z tego słupka:
                                            {departure: sek. od północy,
                                             trip_id, index: pozycja słupka
                                             w kursie}

Z tego składamy kursy: odjazdy o tym samym trip_id, posortowane po `index`,
to jeden przejazd - dokładnie to, czym w GTFS jest trip + stop_times. Numer
linii bierzemy z przecięcia zbiorów linii obsługujących kolejne słupki kursu
(każdy słupek kursu obsługuje jego linię, więc prawdziwa linia jest w każdym
z tych zbiorów).
"""

import concurrent.futures
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

BASE_URL = "https://siechnice.kiedyprzyjedzie.pl"
USER_AGENT = "Metal-Planner/0.1 (+https://github.com/Metal-Pipe-Org/Metal-Planner)"

# Ile dni rozkładu do przodu ściągamy. API oddaje rozkład tylko dla konkretnej
# daty (nie ma calendar.txt z regułą tygodniową), więc każdy dzień to osobne
# przejście po wszystkich słupkach - stąd okno tygodniowe, nie miesięczne.
DEFAULT_DAYS = 7

# Przerwa między zapytaniami - wspólna dla wszystkich wątków, więc jest
# sufitem tempa całego pobierania (~3 zapytania/s), nie tempa jednego wątku.
# To nie strojenie wydajności, tylko uprzejmość wobec cudzego serwera.
REQUEST_DELAY_SEC = 0.3
REQUEST_TIMEOUT_SEC = 30

# Ile zapytań naraz. Serwer bywa kapryśny niezależnie od naszego tempa:
# mediana odpowiedzi to ~0,3 s, ale co kilkanaste zapytanie potrafi wisieć
# 20-60 s, i zwolnienie tempa tego nie zmienia (sprawdzone). Wąskim gardłem
# jest więc opóźnienie, nie liczba zapytań - kilka równoległych wątków
# przykrywa zwiechy, a REQUEST_DELAY_SEC dalej trzyma tempo w ryzach.
CONCURRENCY = 4

# Serwer potrafi zgubić pojedyncze połączenie w środku przejścia (zwykłe
# zerwanie, nie przeciążenie - kolejna próba wchodzi od razu). Bez ponowienia
# jedno takie zgubione zapytanie kasuje kilkuminutowe pobieranie.
REQUEST_RETRIES = 3
RETRY_BACKOFF_SEC = 2.0

# Prefiks identyfikatorów, żeby dane z tego źródła dały się odróżnić od
# wrocławskiego GTFS-a w jednej bazie (i skasować bez ruszania reszty).
ID_PREFIX = "SIE:"

# Promień, w którym słupek z Siechnic uznajemy za ten sam co wrocławski
# o tej samej nazwie. Wspólnych słupków jest sporo - linie 800/810 dojeżdżają
# na Bardzką i Suchą, przez Iwiny - a sklejenie ich w jeden stop_id daje
# przesiadkę bez kary za "przejście" i jeden marker na mapie zamiast dwóch.
SAME_STOP_MAX_M = 200

_DIACRITIC_MAP = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
})

SECONDS_PER_DAY = 24 * 3600


def enabled():
    """Czy wolno ruszać API kiedyPrzyjedzie. Domyślnie nie - patrz nagłówek."""
    return os.environ.get("SIECHNICE_ENABLED", "off").strip().lower() == "on"


def normalize_name(name):
    """Nazwa przystanku sprowadzona do postaci porównywalnej między źródłami:
    bez ogonków, bez wielkości liter, bez zdwojonych spacji. Wrocławski GTFS
    pisze 'SUCHA', kiedyPrzyjedzie 'Sucha' - to ten sam słupek."""
    return re.sub(r"\s+", " ", name.strip().casefold()).translate(_DIACRITIC_MAP)


def haversine_m(lat1, lon1, lat2, lon2):
    import math
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------
# Warstwa sieciowa - jedyne miejsce, które dotyka cudzego serwera.
# --------------------------------------------------------------------------

class Client:
    """Odpytywanie API z wymuszonym tempem, bezpieczne dla wielu wątków.

    Przerwa jest liczona od poprzedniego zapytania, nie doklejana po każdym:
    kiedy odpowiedź i tak przyszła wolniej niż REQUEST_DELAY_SEC, nie ma po co
    dodatkowo czekać. Zamek obejmuje samo wyznaczenie terminu, nie czekanie -
    inaczej wątki stałyby w kolejce po zamek zamiast po termin i cała
    równoległość zeszłaby na nie.
    """

    def __init__(self, base_url=BASE_URL, delay=REQUEST_DELAY_SEC):
        self.base_url = base_url
        self.delay = delay
        self._next_slot_at = 0.0
        self._slot_lock = threading.Lock()

    def _wait_for_slot(self):
        with self._slot_lock:
            now = time.monotonic()
            slot = max(now, self._next_slot_at)
            self._next_slot_at = slot + self.delay
        wait = slot - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def get_json(self, path, params=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        for attempt in range(REQUEST_RETRIES):
            self._wait_for_slot()
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SEC) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError:
                # Odpowiedź serwera, nie awaria łącza - ponowienie da to samo.
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == REQUEST_RETRIES - 1:
                    raise
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))

    def stops(self):
        return self.get_json("/stops")["stops"]

    def directions(self, designator):
        path = "/api/directions/" + urllib.parse.quote(designator, safe="")
        return self.get_json(path)["directions"]

    def timetable(self, designator, day):
        path = "/api/timetables/" + urllib.parse.quote(designator, safe="")
        return self.get_json(path, {"date": day.strftime("%Y-%m-%d")})


# --------------------------------------------------------------------------
# Warstwa przekształceń - czyste funkcje, testowane bez sieci.
# --------------------------------------------------------------------------

def parse_stops(rows):
    """Surowe krotki z /stops -> [{designator, name, lat, lon}].

    API oddaje współrzędne jako liczby całkowite w mikrostopniach i w
    kolejności (lon, lat) - odwrotnie niż wszędzie indziej w tym projekcie.
    """
    stops = []
    for row in rows:
        designator, _code, name, lon_e6, lat_e6 = row[0], row[1], row[2], row[3], row[4]
        stops.append({
            "designator": designator,
            "name": name.strip(),
            "lat": lat_e6 / 1e6,
            "lon": lon_e6 / 1e6,
        })
    return stops


def resolve_line(stop_designators, lines_by_stop):
    """Numer linii kursu = przecięcie zbiorów linii jego słupków.

    Każdy słupek, na którym kurs się zatrzymuje, jest obsługiwany przez jego
    linię, więc prawdziwa linia leży w każdym z tych zbiorów. Zwykle przecięcie
    jest jednoelementowe. Gdy zostaje kilka (dwie linie o identycznym zbiorze
    przystanków) albo zero (słupek bez wpisu w /api/directions), zwracamy None
    - wołający decyduje, co z takim kursem zrobić, zamiast zgadywać numer.
    """
    candidates = None
    for designator in stop_designators:
        lines = lines_by_stop.get(designator)
        if not lines:
            continue
        candidates = set(lines) if candidates is None else candidates & set(lines)
    if candidates is None or len(candidates) != 1:
        return None
    return next(iter(candidates))


def make_times_monotonic(times):
    """Czasy kursu rosnąco, z doliczeniem doby po przekroczeniu północy.

    Odjazd po północy API pokazuje przy dacie następnego dnia, jako małą
    liczbę sekund - w środku kursu wygląda to jak cofnięcie zegara. GTFS
    zapisuje takie kursy godzinami >= 24:00:00 i tak samo robimy tutaj.
    """
    result = []
    offset = 0
    previous = None
    for value in times:
        shifted = value + offset
        if previous is not None and shifted < previous:
            offset += SECONDS_PER_DAY
            shifted = value + offset
        result.append(shifted)
        previous = shifted
    return result


def build_trips(departures_by_stop, lines_by_stop):
    """Odjazdy pozbierane ze wszystkich słupków jednego dnia -> kursy.

    departures_by_stop: {designator: [{"departure", "trip_id", "index"}, ...]}
    Zwraca ([{trip_id, line, stops: [(designator, sek), ...]}], statystyki).

    Kurs to wszystkie odjazdy o tym samym trip_id; `index` to pozycja słupka
    w kursie i po nim je układamy. Kursy jednoprzystankowe odrzucamy - nie da
    się nimi nigdzie dojechać, a biorą się z krańcówek na skraju sieci.
    """
    by_trip = {}
    for designator, departures in departures_by_stop.items():
        for departure in departures:
            by_trip.setdefault(departure["trip_id"], []).append(
                (departure["index"], designator, departure["departure"])
            )

    trips = []
    skipped_short = 0
    skipped_no_line = 0
    for trip_id, entries in sorted(by_trip.items()):
        entries.sort()
        if len(entries) < 2:
            skipped_short += 1
            continue
        designators = [designator for _, designator, _ in entries]
        line = resolve_line(designators, lines_by_stop)
        if line is None:
            skipped_no_line += 1
            continue
        times = make_times_monotonic([seconds for _, _, seconds in entries])
        trips.append({
            "trip_id": trip_id,
            "line": line,
            "stops": list(zip(designators, times)),
        })

    stats = {"skipped_short": skipped_short, "skipped_no_line": skipped_no_line}
    return trips, stats


def match_existing_stops(stops, existing_rows, max_distance_m=SAME_STOP_MAX_M):
    """{designator: stop_id istniejącego słupka} dla tych, które już są w bazie.

    Kryterium to zgodna nazwa znormalizowana ORAZ bliskość - sama nazwa nie
    wystarcza, bo 'Kolejowa' czy 'Szkoła' powtarzają się w aglomeracji, a
    sklejenie dwóch odległych słupków w jeden zrobiłoby z nich fałszywą
    przesiadkę.
    """
    by_name = {}
    for stop_id, name, lat, lon in existing_rows:
        if lat is None or lon is None:
            continue
        by_name.setdefault(normalize_name(name), []).append((stop_id, lat, lon))

    matched = {}
    for stop in stops:
        candidates = by_name.get(normalize_name(stop["name"]))
        if not candidates:
            continue
        best = min(
            candidates,
            key=lambda c: haversine_m(stop["lat"], stop["lon"], c[1], c[2]),
        )
        if haversine_m(stop["lat"], stop["lon"], best[1], best[2]) <= max_distance_m:
            matched[stop["designator"]] = best[0]
    return matched


def to_gtfs_rows(days, stops, stop_id_map):
    """Kursy z kolejnych dni -> wiersze w kształcie tabel z update_gtfs.SCHEMA.

    days: [(datetime.date, [kurs, ...]), ...] - wynik build_trips dla każdej daty.
    stop_id_map: {designator: stop_id w bazie}.

    Każda data dostaje własny service_id i wpis w calendar_dates z
    exception_type=1. To celowo nie jest calendar.txt z regułą tygodniową:
    API oddaje rozkład dla konkretnej daty, więc kalendarz odtworzony z reguł
    byłby zgadywaniem, a wyjątek na datę jest tym, co faktycznie wiemy.
    """
    stop_rows = [
        (stop_id_map[s["designator"]], s["name"], s["lat"], s["lon"])
        for s in stops
        if stop_id_map[s["designator"]].startswith(ID_PREFIX)
    ]

    # Kierunek kursu = nazwa jego ostatniego przystanku. API podaje kierunki
    # osobno, per słupek, i nie wiąże ich z trip_id - a ostatni przystanek
    # kursu jest tym, co w nich i tak widnieje.
    names = {s["designator"]: s["name"] for s in stops}

    routes = {}
    trip_rows = []
    stop_time_rows = []
    calendar_date_rows = []

    for day, trips in days:
        service_id = f"{ID_PREFIX}{day:%Y%m%d}"
        calendar_date_rows.append((service_id, day.strftime("%Y%m%d"), 1))
        for trip in trips:
            line = trip["line"]
            route_id = f"{ID_PREFIX}{line}"
            routes[route_id] = (route_id, line, f"Siechnice - linia {line}", 3)

            trip_id = f"{ID_PREFIX}{day:%Y%m%d}:{trip['trip_id']}"
            headsign = names.get(trip["stops"][-1][0], "")
            trip_rows.append((trip_id, route_id, service_id, headsign, ""))
            for sequence, (designator, seconds) in enumerate(trip["stops"]):
                stop_time_rows.append(
                    (trip_id, sequence, stop_id_map[designator], seconds, seconds)
                )

    return {
        "stops": stop_rows,
        "routes": sorted(routes.values()),
        "trips": trip_rows,
        "stop_times": stop_time_rows,
        "calendar_dates": calendar_date_rows,
    }


# --------------------------------------------------------------------------
# Złożenie całości
# --------------------------------------------------------------------------

def fetch_feed(client=None, days=DEFAULT_DAYS, today=None, log=print):
    """Pobiera rozkład na `days` najbliższych dni i zwraca surowe kursy.

    Zwraca (stops, [(data, kursy), ...]). Nie dotyka bazy - to celowo, żeby
    dało się obejrzeć wynik przed doklejeniem go do czegokolwiek.
    """
    client = client or Client()
    today = today or date.today()

    stop_rows = client.stops()
    stops = parse_stops(stop_rows)
    log(f"  słupki: {len(stops)}")

    designators = [s["designator"] for s in stops]

    def gather(fetch_one):
        """fetch_one(designator) na wszystkich słupkach naraz -> {designator: wynik}."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            return dict(zip(designators, pool.map(fetch_one, designators)))

    lines_by_stop = {
        designator: {d["line"] for d in directions if d.get("line")}
        for designator, directions in gather(client.directions).items()
    }
    all_lines = sorted({line for lines in lines_by_stop.values() for line in lines})
    log(f"  linie: {', '.join(all_lines) or '(brak)'}")

    result = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        departures_by_stop = {
            designator: payload.get("departures", [])
            for designator, payload in gather(
                lambda designator: client.timetable(designator, day)
            ).items()
        }
        trips, stats = build_trips(departures_by_stop, lines_by_stop)
        log(f"  {day}: kursów {len(trips)}"
            f" (pominięto: bez linii {stats['skipped_no_line']},"
            f" jednoprzystankowych {stats['skipped_short']})")
        result.append((day, trips))

    return stops, result


def purge(db):
    """Kasuje z bazy poprzednie dane tego źródła (wszystko po ID_PREFIX).

    Normalnie nie ma czego kasować - aktualizacja buduje bazę od zera. Ale
    `stop_times` nie ma klucza głównego, więc bez tego drugie przejście po tej
    samej bazie nie nadpisałoby kursów, tylko dołożyło ich drugi komplet:
    każde połączenie policzone dwa razy, a planer nie ma jak odróżnić kopii.
    """
    prefix = ID_PREFIX + "%"
    db.execute(
        "DELETE FROM stop_times WHERE trip_id IN "
        "(SELECT trip_id FROM trips WHERE trip_id LIKE ?)", (prefix,)
    )
    for table, column in (
        ("trips", "trip_id"), ("routes", "route_id"),
        ("calendar_dates", "service_id"), ("stops", "stop_id"),
    ):
        db.execute(f"DELETE FROM {table} WHERE {column} LIKE ?", (prefix,))


def merge_into(db_path, stops, days, log=print):
    """Dokleja pobrany rozkład do gotowej bazy GTFS. Zwraca statystyki.

    Wołane po zbudowaniu bazy z wrocławskiej paczki, a przed atomową
    podmianą - dzięki temu działająca aplikacja nigdy nie widzi bazy
    z połową Siechnic.
    """
    db = sqlite3.connect(db_path)
    try:
        purge(db)
        existing = list(db.execute("SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops"))
        matched = match_existing_stops(stops, existing)
        stop_id_map = {
            s["designator"]: matched.get(s["designator"], ID_PREFIX + s["designator"])
            for s in stops
        }
        log(f"  słupki wspólne z wrocławskim GTFS: {len(matched)}"
            f", nowe: {len(stop_id_map) - len(matched)}")

        rows = to_gtfs_rows(days, stops, stop_id_map)
        db.executemany("INSERT OR REPLACE INTO stops VALUES (?, ?, ?, ?)", rows["stops"])
        db.executemany("INSERT OR REPLACE INTO routes VALUES (?, ?, ?, ?)", rows["routes"])
        db.executemany("INSERT OR REPLACE INTO trips VALUES (?, ?, ?, ?, ?)", rows["trips"])
        db.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", rows["stop_times"])
        db.executemany("INSERT INTO calendar_dates VALUES (?, ?, ?)", rows["calendar_dates"])
        db.commit()
    finally:
        db.close()

    counts = {name: len(value) for name, value in rows.items()}
    log("  doklejono: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return counts


def update(db_path, days=DEFAULT_DAYS, log=print):
    """Pełne przejście dla Siechnic. Zwraca True, gdy coś doklejono.

    Wyłączone (SIECHNICE_ENABLED != on) nie jest błędem - to stan domyślny.
    """
    if not enabled():
        log("Siechnice: pominięte (SIECHNICE_ENABLED != on).")
        return False

    log("Siechnice: pobieram rozkład z kiedyPrzyjedzie...")
    stops, days_data = fetch_feed(days=days, log=log)
    merge_into(db_path, stops, days_data, log=log)
    return True
