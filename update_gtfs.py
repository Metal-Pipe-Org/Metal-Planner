"""Pobiera rozkład jazdy GTFS Wrocławia i buduje bazę SQLite dla aplikacji.

Uruchamiany ręcznie lub z crona, np. codziennie o 3:00:
    0 3 * * * cd /sciezka/do/Metal-Planner && python3 update_gtfs.py

...a przy starcie serwera przez refresh_on_start() - wywołuje ją app.py
(uruchomienie lokalne). W kontenerze robi to samo docker/entrypoint.sh, jeszcze
przed startem gunicorna, więc tamta ścieżka tędy nie przechodzi.

Trzecie wejście to start_daily_scheduler(): codzienna aktualizacja o godzinie
z GTFS_AUTO_UPDATE_HOUR, wątkiem w procesie serwera. Bez niej kontener, który
stoi tygodniami bez restartu, dojechałby do końca okna ważności paczki
(calendar.txt obejmuje ~3 tygodnie) i przestał znajdować jakiekolwiek kursy.

Baza jest budowana obok jako gtfs_new.sqlite i podmieniana atomowo
(os.replace), więc działająca aplikacja nigdy nie widzi wpół zapisanego pliku.
"""

import csv
import io
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

# Ten plik bywa uruchamiany sam (docker/entrypoint.sh, cron), poza
# procesem serwera - musi wczytać data/.env na własną rękę.
import config  # noqa: F401

# Portal Otwarte Dane Wrocław publikuje kolejne paczki GTFS nazwane datą
# początku obowiązywania (GTFS_DDMMRRRR). Ta strona listuje je wszystkie:
GTFS_LIST_URL = "https://open-data.cui.wroclaw.pl/hdb/ft/6/"
BASE_URL = "https://open-data.cui.wroclaw.pl"

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "gtfs.sqlite"
NEW_DB_PATH = DATA_DIR / "gtfs_new.sqlite"
ZIP_PATH = DATA_DIR / "gtfs_download.zip"

# Po ilu godzinach od ostatniej podmiany start serwera ma pobrać rozkład
# na nowo. Chroni przed pobieraniem 12 MB po każdym zapisie pliku: reloader
# Flaska restartuje serwer przy każdej zmianie kodu, a rozkład zmienia się
# raz na dobę, nie co Ctrl+S.
DEFAULT_MAX_AGE_HOURS = 12

# Co ile scheduler sprawdza zegar, czekając na swoją godzinę. Krótkie drzemki
# zamiast jednego sleep-a na osiem godzin, żeby zmiana czasu, przestawienie
# zegara albo uśpienie hosta nie przesunęły terminu o te osiem godzin.
# Koszt jest pomijalny: pobudka to odczyt zegara i odjęcie dat.
SCHEDULER_TICK_SEC = 300

SCHEMA = """
CREATE TABLE stops (
    stop_id   TEXT PRIMARY KEY,
    stop_name TEXT NOT NULL,
    stop_lat  REAL,
    stop_lon  REAL
);
CREATE TABLE routes (
    route_id         TEXT PRIMARY KEY,
    route_short_name TEXT,
    route_long_name  TEXT,
    route_type       INTEGER
);
CREATE TABLE trips (
    trip_id       TEXT PRIMARY KEY,
    route_id      TEXT NOT NULL,
    service_id    TEXT NOT NULL,
    trip_headsign TEXT,
    shape_id      TEXT
);
CREATE TABLE shapes (
    shape_id TEXT NOT NULL,
    seq      INTEGER NOT NULL,
    lat      REAL NOT NULL,
    lon      REAL NOT NULL
);
CREATE TABLE stop_times (
    trip_id        TEXT NOT NULL,
    stop_sequence  INTEGER NOT NULL,
    stop_id        TEXT NOT NULL,
    arrival_sec    INTEGER NOT NULL,
    departure_sec  INTEGER NOT NULL
);
CREATE TABLE calendar (
    service_id TEXT PRIMARY KEY,
    monday INTEGER, tuesday INTEGER, wednesday INTEGER, thursday INTEGER,
    friday INTEGER, saturday INTEGER, sunday INTEGER,
    start_date TEXT,
    end_date   TEXT
);
CREATE TABLE calendar_dates (
    service_id     TEXT NOT NULL,
    date           TEXT NOT NULL,
    exception_type INTEGER NOT NULL
);
"""


def parse_gtfs_time(value):
    """'HH:MM:SS' -> sekundy od północy; godziny mogą przekraczać 23 (kursy po północy)."""
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def read_csv(zf, filename):
    """Iteruje po wierszach pliku CSV wewnątrz zipa; pusty iterator, gdy pliku brak."""
    try:
        raw = zf.open(filename)
    except KeyError:
        print(f"  (brak {filename} w paczce - pomijam)")
        return
    with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def batched_insert(db, sql, rows, batch_size=50_000):
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


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Metal-Planner/0.1"})
    return urllib.request.urlopen(request, timeout=120)


def find_current_feed_url(today=None):
    """Wybiera z portalu paczkę o najpóźniejszej dacie startu, która już obowiązuje.

    Portal wystawia też paczki z przyszłą datą startu (np. GTFS_18072026
    opublikowany 16 lipca) - tych nie bierzemy, bo ich calendar.txt
    nie obejmuje jeszcze dzisiejszych kursów.
    """
    today = today or date.today()
    with _fetch(GTFS_LIST_URL) as response:
        html = response.read().decode("utf-8", errors="replace")

    feeds = []
    for match in re.finditer(
        r'GTFS_(\d{8})[\s\S]{0,600}?href="(/hdb/download/\d+/)"', html
    ):
        start = datetime.strptime(match.group(1), "%d%m%Y").date()
        feeds.append((start, BASE_URL + match.group(2)))
    if not feeds:
        raise RuntimeError(f"Nie znalazłem żadnej paczki GTFS na {GTFS_LIST_URL}")

    valid_now = [f for f in feeds if f[0] <= today]
    start, url = max(valid_now) if valid_now else min(feeds)
    print(f"Wybrana paczka: obowiązuje od {start} ({url})")
    return url


def download(url, dest):
    print(f"Pobieram {url}")
    with _fetch(url) as response, open(dest, "wb") as out:
        while chunk := response.read(1 << 16):
            out.write(chunk)
    print(f"  zapisano {dest.stat().st_size / 1_000_000:.1f} MB")


def build_database(zip_path, db_path):
    db_path.unlink(missing_ok=True)
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)

    with zipfile.ZipFile(zip_path) as zf:
        n = batched_insert(
            db,
            "INSERT OR REPLACE INTO stops VALUES (?, ?, ?, ?)",
            (
                (r["stop_id"], r["stop_name"].strip(), float(r["stop_lat"]), float(r["stop_lon"]))
                for r in read_csv(zf, "stops.txt")
            ),
        )
        print(f"  stops: {n}")

        n = batched_insert(
            db,
            "INSERT OR REPLACE INTO routes VALUES (?, ?, ?, ?)",
            (
                (
                    r["route_id"],
                    r.get("route_short_name", ""),
                    r.get("route_long_name", ""),
                    int(r["route_type"]) if r.get("route_type") else None,
                )
                for r in read_csv(zf, "routes.txt")
            ),
        )
        print(f"  routes: {n}")

        n = batched_insert(
            db,
            "INSERT OR REPLACE INTO trips VALUES (?, ?, ?, ?, ?)",
            (
                (
                    r["trip_id"], r["route_id"], r["service_id"],
                    r.get("trip_headsign", ""), r.get("shape_id", ""),
                )
                for r in read_csv(zf, "trips.txt")
            ),
        )
        print(f"  trips: {n}")

        n = batched_insert(
            db,
            "INSERT INTO shapes VALUES (?, ?, ?, ?)",
            (
                (
                    r["shape_id"],
                    int(r["shape_pt_sequence"]),
                    float(r["shape_pt_lat"]),
                    float(r["shape_pt_lon"]),
                )
                for r in read_csv(zf, "shapes.txt")
            ),
        )
        print(f"  shapes: {n}")

        n = batched_insert(
            db,
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
            (
                (
                    r["trip_id"],
                    int(r["stop_sequence"]),
                    r["stop_id"],
                    parse_gtfs_time(r["arrival_time"]),
                    parse_gtfs_time(r["departure_time"]),
                )
                for r in read_csv(zf, "stop_times.txt")
            ),
        )
        print(f"  stop_times: {n}")

        n = batched_insert(
            db,
            "INSERT OR REPLACE INTO calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    r["service_id"],
                    int(r["monday"]), int(r["tuesday"]), int(r["wednesday"]),
                    int(r["thursday"]), int(r["friday"]), int(r["saturday"]),
                    int(r["sunday"]),
                    r["start_date"].strip(),
                    r["end_date"].strip(),
                )
                for r in read_csv(zf, "calendar.txt")
            ),
        )
        print(f"  calendar: {n}")

        n = batched_insert(
            db,
            "INSERT INTO calendar_dates VALUES (?, ?, ?)",
            (
                (r["service_id"], r["date"].strip(), int(r["exception_type"]))
                for r in read_csv(zf, "calendar_dates.txt")
            ),
        )
        print(f"  calendar_dates: {n}")

    print("Tworzę indeksy...")
    db.executescript(
        """
        CREATE INDEX idx_stop_times_trip ON stop_times (trip_id, stop_sequence);
        CREATE INDEX idx_trips_service ON trips (service_id);
        CREATE INDEX idx_shapes ON shapes (shape_id, seq);
        """
    )
    db.commit()
    db.close()


def run_update():
    """Pełne przejście: pobranie, budowa, atomowa podmiana. True = udało się."""
    started = time.monotonic()
    DATA_DIR.mkdir(exist_ok=True)
    try:
        download(find_current_feed_url(), ZIP_PATH)
        build_database(ZIP_PATH, NEW_DB_PATH)
    except Exception as e:
        # Stara baza zostaje nietknięta - aplikacja dalej działa na wczorajszych danych.
        print(f"BŁĄD aktualizacji: {e}", file=sys.stderr)
        NEW_DB_PATH.unlink(missing_ok=True)
        return False

    os.replace(NEW_DB_PATH, DB_PATH)
    ZIP_PATH.unlink(missing_ok=True)
    print(f"Gotowe: {DB_PATH} ({time.monotonic() - started:.0f} s)")
    return True


def db_age_hours():
    """Wiek bazy w godzinach albo None, gdy jeszcze jej nie ma."""
    if not DB_PATH.exists():
        return None
    return (time.time() - DB_PATH.stat().st_mtime) / 3600


def refresh_on_start(max_age_hours=None):
    """Aktualizacja rozkładu przy starcie serwera. Zwraca wątek albo None.

    Trzy przypadki, w kolejności:
      - brak bazy      -> pobranie blokujące; bez rozkładu nie ma czego serwować,
      - baza świeższa niż próg -> nic, żeby restart co chwilę nie ciągnął tego samego,
      - baza starsza   -> wątek w tle; serwer rusza od razu na dotychczasowych
        danych, a gdy nowa paczka wjedzie na miejsce (os.replace), gtfs.py
        przeładuje ją sam - mtime bazy siedzi w kluczu jego cache'a.

    GTFS_UPDATE_ON_START=off wyłącza całość, GTFS_MAX_AGE_HOURS zmienia próg.
    """
    if os.environ.get("GTFS_UPDATE_ON_START", "on").lower() == "off":
        return None

    if max_age_hours is None:
        try:
            max_age_hours = float(os.environ.get("GTFS_MAX_AGE_HOURS", DEFAULT_MAX_AGE_HOURS))
        except ValueError:
            max_age_hours = DEFAULT_MAX_AGE_HOURS

    age = db_age_hours()
    if age is None:
        print("Brak bazy rozkładów - pobieram paczkę GTFS (~1 min)...")
        if not run_update():
            print("OSTRZEŻENIE: nie udało się pobrać rozkładu.", file=sys.stderr)
        return None

    if age < max_age_hours:
        print(f"Rozkład sprzed {age:.1f} h - pomijam aktualizację przy starcie.")
        return None

    print(f"Rozkład sprzed {age:.1f} h - odświeżam w tle (serwer działa na obecnym).")
    thread = threading.Thread(target=run_update, name="gtfs-update", daemon=True)
    thread.start()
    return thread


def _next_run_at(hour, now=None):
    """Najbliższe wystąpienie `hour:00` wg czasu lokalnego, jako datetime.

    Zwracamy punkt w czasie, a nie liczbę sekund do niego, celowo: termin
    złożony z "teraz + ileś sekund" wypada o ułamek przed pełną godziną (bo
    zegar czytany jest dwa razy) i w logu widnieje jako 02:59 zamiast 03:00.

    Trafienie w termin co do sekundy liczymy jako jutro, nie jako "teraz":
    inaczej pętla schedulera, wróciwszy z aktualizacji w tej samej sekundzie,
    odpaliłaby ją drugi raz.
    """
    now = now or datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def scheduled_hour(value=None):
    """Godzina z GTFS_AUTO_UPDATE_HOUR jako int 0-23, albo None = bez harmonogramu.

    Pusta wartość to świadome "tylko ręcznie" (tak opisuje ją docker-compose.yml),
    więc nie jest błędem. Wartość niepoprawna już jest - i mówimy o tym głośno,
    bo cicho wyłączony harmonogram wyszedłby na jaw dopiero pustymi wynikami
    wyszukiwania, po wygaśnięciu okna ważności paczki.
    """
    if value is None:
        value = os.environ.get("GTFS_AUTO_UPDATE_HOUR", "")
    value = str(value).strip()
    if not value:
        return None

    try:
        hour = int(value)
    except ValueError:
        hour = -1
    if not 0 <= hour <= 23:
        print(
            f"GTFS_AUTO_UPDATE_HOUR={value!r} to nie godzina 0-23 "
            "- automatyczna aktualizacja wyłączona.",
            file=sys.stderr,
        )
        return None
    return hour


def _run_update_subprocess():
    """Aktualizacja w osobnym procesie (fork+exec), nie w tym wątku.

    Dwa powody. Pierwszy: to leci w masterze gunicorna, który forkuje workery -
    a fork w procesie z wątkiem w środku budowy SQLite kopiuje do dziecka
    zamki trzymane przez wątek, którego tam nie ma. Drugi: ~35 MB budowy
    znika razem z procesem, zamiast zostać w pamięci serwera.

    Kodu wyjścia świadomie nie sprawdzamy: arbiter gunicorna woła
    os.waitpid(-1) i zbiera nasze dziecko przed subprocess, a CPython łyka
    wtedy ChildProcessError i raportuje returncode 0 niezależnie od tego, jak
    poszło. Wynik i tak jest w logach - update_gtfs.py sam wypisuje "Gotowe:"
    albo "BŁĄD aktualizacji:".
    """
    subprocess.run([sys.executable, str(Path(__file__).resolve())], check=False)


def _daily_loop(hour):
    while True:
        target = _next_run_at(hour)
        # flush=True nie jest ozdobnikiem: pierwszy obieg tej pętli leci
        # w masterze gunicorna jeszcze przed forkiem workerów, a wszystko, co
        # zostanie wtedy w buforze stdout, każdy worker dziedziczy i wypłukuje
        # przy swoim wyjściu - ta sama linia pojawiłaby się w logu trzy razy.
        print(f"Kolejna automatyczna aktualizacja rozkładu: {target:%Y-%m-%d %H:%M} "
              f"(GTFS_AUTO_UPDATE_HOUR={hour}).", flush=True)

        # Odliczamy do stałego punktu w czasie, a nie odejmując przespane
        # sekundy - dzięki temu skok zegara do przodu odpala aktualizację od
        # razu, a do tyłu po prostu przedłuża czekanie.
        while True:
            remaining = (target - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, SCHEDULER_TICK_SEC))

        try:
            _run_update_subprocess()
        except Exception as e:
            # Wątek, który zdechnie na wyjątku, przestaje aktualizować
            # cokolwiek aż do restartu serwera - a objaw, czyli puste wyniki
            # wyszukiwania, pojawi się dopiero po wygaśnięciu paczki, tygodnie
            # później. Logujemy i próbujemy jutro.
            print(f"BŁĄD harmonogramu aktualizacji: {e}", file=sys.stderr, flush=True)


def start_daily_scheduler(hour=None):
    """Wątek odświeżający rozkład codziennie o `hour`. Zwraca wątek albo None.

    Wołane raz na proces serwera: z on_starting w gunicorn.conf.py (master,
    przed forkiem workerów - więc jeden harmonogram niezależnie od
    WEB_CONCURRENCY) i z app.py przy uruchomieniu lokalnym.
    """
    hour = scheduled_hour(hour)
    if hour is None:
        return None

    thread = threading.Thread(
        target=_daily_loop, args=(hour,), name="gtfs-scheduler", daemon=True
    )
    thread.start()
    return thread


def main():
    # Wywołanie ręczne i z crona aktualizuje bezwarunkowo - próg świeżości
    # dotyczy tylko startu serwera.
    sys.exit(0 if run_update() else 1)


if __name__ == "__main__":
    main()
