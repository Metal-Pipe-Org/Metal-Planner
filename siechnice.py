"""Rozkład Siechnickiej Komunikacji Publicznej - statyczna kopia doklejana
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
wykorzystanie. Dlatego serwer go NIE odpytuje i nawet nie umie: ten moduł
nie ma żadnego kodu sieciowego, a w repo nie ma ani kodu pobierania, ani
samych danych. Kopię tygodnia rozkładu (te same publiczne dane, które gmina
wykłada w PDF-ach) przygotowuje się poza projektem i wgrywa w trybie
deweloperskim (⚙ → „Wgraj rozkład Siechnic”, patrz validate_snapshot
i save_snapshot); aktualizacja bazy dokleja ją co noc do rozkładu Wrocławia.
Brak pliku = brak Siechnic, nic więcej.
Docelowo poprosić gminę o eksport GTFS - platforma Operibus już go u innych
klientów wystawia (Zduńska Wola, Oborniki figurują w tabeli KPD z formatem
"GTFS static/realtime"), więc to kwestia włączenia funkcji, a nie budowania
czegokolwiek. Szczegóły i wzór pisma: docs/SIECHNICE_DANE.md.

Kopia to surowe odpowiedzi API (format: validate_snapshot) - słupki,
linie na słupkach i odjazdy z każdego słupka na każdy dzień tygodnia
({departure: sek. od północy, trip_id, index: pozycja słupka w kursie}).
Z tego składamy kursy: odjazdy o tym samym trip_id, posortowane po `index`,
to jeden przejazd - dokładnie to, czym w GTFS jest trip + stop_times. Numer
linii bierzemy z przecięcia zbiorów linii obsługujących kolejne słupki kursu
(każdy słupek kursu obsługuje jego linię, więc prawdziwa linia jest w każdym
z tych zbiorów).
"""

import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path

# Kopia leży poza repo: lokalnie w data/ (gitignorowane), w kontenerze na
# osobnym wolumenie (SIECHNICE_ROZKLAD, patrz docker-compose.yml). Trafia tam
# wyłącznie przez wgranie w trybie deweloperskim.
SNAPSHOT_PATH = Path(
    os.environ.get("SIECHNICE_ROZKLAD")
    or Path(__file__).resolve().parent / "data" / "siechnice" / "rozklad.json"
)

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

# Źródło pisze ten sam przystanek raz z łącznikiem, raz z półpauzą; reszta
# danych ma łącznik, więc inaczej autouzupełnianie pokazuje dwa "różne" słupki.
_DASHES = re.compile("[\u2013\u2014]")

SECONDS_PER_DAY = 24 * 3600

# Kolumny tabeli calendar w kolejności date.weekday() - zarazem nazwy
# service_id kopii (SIE:monday, ...).
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


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
            "name": _DASHES.sub("-", name.strip()),
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


def to_gtfs_rows(days, stops, stop_id_map, window):
    """Kursy z tygodnia kopii -> wiersze w kształcie tabel z update_gtfs.SCHEMA.

    days: [(datetime.date, [kurs, ...]), ...] - wynik build_trips dla każdej
    daty z kopii, po jednej na dzień tygodnia.
    stop_id_map: {designator: stop_id w bazie}.
    window: (pierwszy, ostatni dzień) jako "RRRRMMDD" - okres, w którym
    kopia ma obowiązywać.

    Każdy dzień tygodnia dostaje własny service_id z regułą w calendar
    (kursy z poniedziałku kopii jeżdżą w każdy poniedziałek okna). API oddaje
    rozkład per data, ale kopia ma działać tygodniami, a rozkład gminy jest
    tygodniowy. Świąt kopia nie zna - jeżdżą wg swojego dnia tygodnia.
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
    calendar_rows = []

    for day, trips in days:
        weekday = WEEKDAYS[day.weekday()]
        service_id = f"{ID_PREFIX}{weekday}"
        flags = [int(column == weekday) for column in WEEKDAYS]
        calendar_rows.append((service_id, *flags, *window))
        for trip in trips:
            line = trip["line"]
            route_id = f"{ID_PREFIX}{line}"
            routes[route_id] = (route_id, line, f"Siechnice - linia {line}", 3)

            trip_id = f"{service_id}:{trip['trip_id']}"
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
        "calendar": calendar_rows,
    }


# --------------------------------------------------------------------------
# Złożenie całości
# --------------------------------------------------------------------------

def feed_from_snapshot(snapshot, log=print):
    """Kopia (patrz validate_snapshot) -> (słupki, [(data, kursy), ...])."""
    stops = parse_stops(snapshot["stops"])
    lines_by_stop = {k: set(v) for k, v in snapshot["lines_by_stop"].items()}
    days = []
    for entry in snapshot["days"]:
        day = date.fromisoformat(entry["date"])
        trips, stats = build_trips(entry["departures_by_stop"], lines_by_stop)
        log(f"  {day} ({WEEKDAYS[day.weekday()]}): kursów {len(trips)}"
            f" (pominięto: bez linii {stats['skipped_no_line']},"
            f" jednoprzystankowych {stats['skipped_short']})")
        days.append((day, trips))
    return stops, days


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
        ("calendar", "service_id"), ("stops", "stop_id"),
    ):
        db.execute(f"DELETE FROM {table} WHERE {column} LIKE ?", (prefix,))


def service_window(db):
    """(pierwszy, ostatni) dzień rozkładu Wrocławia w bazie, jako "RRRRMMDD".

    Kopia Siechnic jeździ dokładnie w tym oknie, a nie "do odwołania": po
    ostatnim dniu wrocławskiej paczki aplikacja mówi pasażerowi, że rozkładu
    jeszcze nie opublikowano (gtfs.last_service_date) - Siechnice z datą
    końca w dalekiej przyszłości zagłuszyłyby ten komunikat. Baza buduje się
    od nowa co dzień, więc okno przesuwa się razem z paczką Wrocławia.
    """
    return db.execute(
        "SELECT MIN(d0), MAX(d1) FROM ("
        " SELECT start_date AS d0, end_date AS d1 FROM calendar"
        " UNION ALL SELECT date, date FROM calendar_dates WHERE exception_type = 1)"
    ).fetchone()


def merge_into(db_path, stops, days, log=print):
    """Dokleja rozkład z kopii do gotowej bazy GTFS. Zwraca statystyki.

    Wołane po zbudowaniu bazy z wrocławskiej paczki, a przed atomową
    podmianą - dzięki temu działająca aplikacja nigdy nie widzi bazy
    z połową Siechnic.
    """
    db = sqlite3.connect(db_path)
    try:
        purge(db)
        window = service_window(db)
        existing = list(db.execute("SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops"))
        matched = match_existing_stops(stops, existing)
        stop_id_map = {
            s["designator"]: matched.get(s["designator"], ID_PREFIX + s["designator"])
            for s in stops
        }
        log(f"  słupki wspólne z wrocławskim GTFS: {len(matched)}"
            f", nowe: {len(stop_id_map) - len(matched)}")

        rows = to_gtfs_rows(days, stops, stop_id_map, window)
        db.executemany("INSERT OR REPLACE INTO stops VALUES (?, ?, ?, ?)", rows["stops"])
        db.executemany("INSERT OR REPLACE INTO routes VALUES (?, ?, ?, ?)", rows["routes"])
        db.executemany("INSERT OR REPLACE INTO trips VALUES (?, ?, ?, ?, ?)", rows["trips"])
        db.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", rows["stop_times"])
        db.executemany(
            "INSERT INTO calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows["calendar"])
        db.commit()
    finally:
        db.close()

    counts = {name: len(value) for name, value in rows.items()}
    log("  doklejono: " + ", ".join(f"{k} {v}" for k, v in counts.items())
        + f" (okno {window[0]}-{window[1]})")
    return counts


# --------------------------------------------------------------------------
# Wgranie kopii
# --------------------------------------------------------------------------

class InvalidSnapshot(ValueError):
    """Wgrany plik nie jest kopią rozkładu - treść idzie wprost do dewelopera."""


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate_snapshot(raw):
    """Sprawdza wgrany plik i zwraca go jako słownik. Zły -> InvalidSnapshot.

    Oczekiwany format (JSON):
      {"stops": [[designator, kod, nazwa, lon*1e6, lat*1e6, ...], ...],
       "lines_by_stop": {designator: [linia, ...]},
       "days": [{"date": "RRRR-MM-DD",
                 "departures_by_stop": {designator: [
                     {"departure": sek. od północy, "trip_id": liczba,
                      "index": pozycja słupka w kursie},
                 ]}}, ...]}
    z siedmioma dniami - po jednym na każdy dzień tygodnia, bo każdy z nich
    staje się regułą tygodniową (to_gtfs_rows). Pozostałe pola są ignorowane.

    Sprawdzamy kształt przed zapisem, bo zły plik na wolumenie wyszedłby na
    jaw dopiero w nocy, jako ciche zniknięcie Siechnic z bazy.
    """
    try:
        snapshot = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise InvalidSnapshot(f"To nie jest plik JSON ({e}).") from None
    if not isinstance(snapshot, dict):
        raise InvalidSnapshot("Plik nie jest obiektem JSON z polami stops, lines_by_stop, days.")
    missing = [key for key in ("stops", "lines_by_stop", "days") if key not in snapshot]
    if missing:
        raise InvalidSnapshot("Brak pól: " + ", ".join(missing) + ".")

    stops = snapshot["stops"]
    if not isinstance(stops, list) or not stops:
        raise InvalidSnapshot("Pole stops musi być niepustą listą słupków.")
    designators = set()
    for row in stops:
        if not (isinstance(row, list) and len(row) >= 5
                and isinstance(row[0], str) and row[0]
                and isinstance(row[2], str)
                and _is_number(row[3]) and _is_number(row[4])):
            raise InvalidSnapshot(
                f"Zły słupek w stops: {json.dumps(row, ensure_ascii=False)[:120]}"
                " (oczekiwane [designator, kod, nazwa, lon*1e6, lat*1e6, ...]).")
        designators.add(row[0])

    lines_by_stop = snapshot["lines_by_stop"]
    if not (isinstance(lines_by_stop, dict) and all(
            isinstance(lines, list) and all(isinstance(line, str) for line in lines)
            for lines in lines_by_stop.values())):
        raise InvalidSnapshot("Pole lines_by_stop musi mapować słupek na listę numerów linii.")

    days = snapshot["days"]
    if not isinstance(days, list):
        raise InvalidSnapshot("Pole days musi być listą dni.")
    weekdays = set()
    for entry in days:
        if not isinstance(entry, dict):
            raise InvalidSnapshot("Każdy element days musi mieć pola date i departures_by_stop.")
        try:
            day = date.fromisoformat(entry.get("date"))
        except (TypeError, ValueError):
            raise InvalidSnapshot(f"Zła data w days: {entry.get('date')!r}.") from None
        if day.weekday() in weekdays:
            raise InvalidSnapshot(f"Dzień tygodnia daty {day} powtarza się w days.")
        weekdays.add(day.weekday())

        departures_by_stop = entry.get("departures_by_stop")
        if not isinstance(departures_by_stop, dict):
            raise InvalidSnapshot(f"{day}: departures_by_stop musi być obiektem.")
        for designator, departures in departures_by_stop.items():
            if designator not in designators:
                raise InvalidSnapshot(f"{day}: odjazdy z nieznanego słupka {designator!r}.")
            if not (isinstance(departures, list) and all(
                    isinstance(d, dict)
                    and _is_int(d.get("departure")) and d["departure"] >= 0
                    and _is_int(d.get("trip_id"))
                    and _is_int(d.get("index"))
                    for d in departures)):
                raise InvalidSnapshot(
                    f"{day}, słupek {designator}: odjazd musi mieć departure,"
                    " trip_id i index.")
    if len(weekdays) != len(WEEKDAYS):
        raise InvalidSnapshot(
            f"Plik ma {len(weekdays)} z 7 dni tygodnia - potrzebny pełny tydzień.")

    _, built = feed_from_snapshot(snapshot, log=lambda *_: None)
    if not any(trips for _, trips in built):
        raise InvalidSnapshot("Z pliku nie da się złożyć ani jednego kursu.")
    return snapshot


def save_snapshot(raw, path=SNAPSHOT_PATH):
    """Sprawdza i zapisuje wgraną kopię. Zły plik -> InvalidSnapshot, bez zapisu.

    Przez plik obok i podmianę: przebudowa bazy może czytać kopię w tej samej
    chwili i nie może trafić na połowę pliku.
    """
    validate_snapshot(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_bytes(raw)
    os.replace(tmp_path, path)


def update(db_path, snapshot_path=SNAPSHOT_PATH, log=print):
    """Dokleja Siechnice z kopii, jeśli leży na miejscu. True, gdy doklejono.

    Brak kopii nie jest błędem - aplikacja działa wtedy po prostu bez Siechnic.
    """
    if not snapshot_path.exists():
        log(f"Siechnice: pominięte (brak kopii rozkładu {snapshot_path}).")
        return False

    log(f"Siechnice: doklejam rozkład z kopii {snapshot_path}...")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    stops, days = feed_from_snapshot(snapshot, log=log)
    merge_into(db_path, stops, days, log=log)
    return True


