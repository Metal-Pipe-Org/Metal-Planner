"""Pobiera rozkład jazdy GTFS Wrocławia i buduje bazę SQLite dla aplikacji.

Uruchamiany ręcznie lub z crona, np. codziennie o 3:00:
    0 3 * * * cd /sciezka/do/Metal-Planner && python3 update_gtfs.py

Baza jest budowana obok jako gtfs_new.sqlite i podmieniana atomowo
(os.replace), więc działająca aplikacja nigdy nie widzi wpół zapisanego pliku.
"""

import csv
import io
import os
import re
import sqlite3
import sys
import time
import urllib.request
import zipfile
from datetime import date, datetime
from pathlib import Path

# Portal Otwarte Dane Wrocław publikuje kolejne paczki GTFS nazwane datą
# początku obowiązywania (GTFS_DDMMRRRR). Ta strona listuje je wszystkie:
GTFS_LIST_URL = "https://open-data.cui.wroclaw.pl/hdb/ft/6/"
BASE_URL = "https://open-data.cui.wroclaw.pl"

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "gtfs.sqlite"
NEW_DB_PATH = DATA_DIR / "gtfs_new.sqlite"
ZIP_PATH = DATA_DIR / "gtfs_download.zip"

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


def main():
    started = time.monotonic()
    DATA_DIR.mkdir(exist_ok=True)
    try:
        download(find_current_feed_url(), ZIP_PATH)
        build_database(ZIP_PATH, NEW_DB_PATH)
    except Exception as e:
        # Stara baza zostaje nietknięta - aplikacja dalej działa na wczorajszych danych.
        print(f"BŁĄD aktualizacji: {e}", file=sys.stderr)
        NEW_DB_PATH.unlink(missing_ok=True)
        sys.exit(1)

    os.replace(NEW_DB_PATH, DB_PATH)
    ZIP_PATH.unlink(missing_ok=True)
    print(f"Gotowe: {DB_PATH} ({time.monotonic() - started:.0f} s)")


if __name__ == "__main__":
    main()
