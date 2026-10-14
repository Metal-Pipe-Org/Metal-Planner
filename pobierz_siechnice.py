"""Jednorazowe, ręczne pobranie kopii rozkładu gminy Siechnice z API
kiedyPrzyjedzie - TYLKO LOKALNIE: `python pobierz_siechnice.py [ścieżka]`.

TEN PLIK NIE TRAFIA DO OBRAZU DOCKERA (.dockerignore). To jedyne miejsce
w projekcie, które umie odpytać kiedyPrzyjedzie, więc serwer nie ma czym tego
zrobić - ani przy starcie, ani w nocy, ani przez pomyłkę. Robots.txt tego
serwisu to "Disallow: /" i nie ma tam regulaminu (patrz siechnice.py
i docs/SIECHNICE_DANE.md), dlatego rozkład pobiera się raz, z własnego
komputera, a wynik commituje do repo jako siechnice.REPO_SNAPSHOT_PATH.

KONTRAKT API (odtworzony z bundla aplikacji, stan: sierpień 2026):
  GET /stops?rev=<n>                      - wszystkie słupki: [designator, kod,
                                            nazwa, lon*1e6, lat*1e6, ...]
  GET /api/directions/<designator>        - linie i kierunki na tym słupku
  GET /api/timetables/<designator>?date=  - cały dzień odjazdów z tego słupka:
                                            {departure: sek. od północy,
                                             trip_id, index: pozycja słupka
                                             w kursie}
"""

import concurrent.futures
import http.client
import json
import sys
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import siechnice

BASE_URL = "https://siechnice.kiedyprzyjedzie.pl"
USER_AGENT = "Metal-Planner/0.1 (+https://github.com/Metal-Pipe-Org/Metal-Planner)"

# Ile dni rozkładu ściągamy. API oddaje rozkład tylko dla konkretnej daty (nie
# ma calendar.txt z regułą tygodniową), a kopia ma działać tygodniami - więc
# bierzemy jeden pełny tydzień i każdy dzień tygodnia powtarzamy co tydzień
# (patrz siechnice.to_gtfs_rows).
DEFAULT_DAYS = 7

# Przerwa między zapytaniami - wspólna dla wszystkich wątków, więc jest
# sufitem tempa całego pobierania (~3 zapytania/s), nie tempa jednego wątku.
# To nie strojenie wydajności, tylko uprzejmość wobec cudzego serwera.
REQUEST_DELAY_SEC = 0.3
REQUEST_TIMEOUT_SEC = 30

# Ile zapytań naraz. Tempa to nie podnosi - sufitem jest REQUEST_DELAY_SEC,
# wspólny dla wszystkich wątków. Chodzi o to, żeby pojedyncza wolniejsza
# odpowiedź nie wstrzymywała kolejnych: przy jednym wątku każda sekunda
# czekania to sekunda przestoju całego pobierania.
CONCURRENCY = 4

# Utrzymywane połączenie serwer może zamknąć w dowolnym momencie (limit
# zapytań na połączenie, keep-alive timeout). Wtedy zapytanie trzeba powtórzyć
# na świeżym gnieździe - bez tego jedno takie zamknięcie kasuje całe pobieranie.
REQUEST_RETRIES = 3
RETRY_BACKOFF_SEC = 2.0


class HttpError(Exception):
    """Serwer odpowiedział statusem innym niż 200."""


class Client:
    """Odpytywanie API po połączeniu trwałym, z wymuszonym tempem.

    POŁĄCZENIE JEST TU RZECZĄ ISTOTNĄ, nie szczegółem. Zwykłe
    urllib.request.urlopen otwiera nowe połączenie TLS na każde zapytanie,
    a przy kilkuset zapytaniach pod rząd ten serwer przestaje odpowiadać na
    część nawiązań: co kilkanaste zapytanie wisiało 20-60 s (charakterystyczne
    czasy retransmisji SYN), niezależnie od tego, jak wolno pytaliśmy.
    Na jednym połączeniu trzymanym otwartym te same zapytania idą po ~0,14 s
    i nie zwiecha się ani jedno - pełne przejście po 237 słupkach skraca się
    z ~20 minut do ~40 sekund.

    Połączenie jest per wątek (threading.local): http.client.HTTPSConnection
    nie jest bezpieczne dla wielu wątków, a każdy wątek i tak potrzebuje
    własnego gniazda.

    Tempo jest wspólne dla wszystkich wątków. Zamek obejmuje samo wyznaczenie
    terminu, nie czekanie - inaczej wątki stałyby w kolejce po zamek zamiast
    po termin i cała równoległość zeszłaby na nie.
    """

    def __init__(self, base_url=BASE_URL, delay=REQUEST_DELAY_SEC):
        self.host = urllib.parse.urlsplit(base_url).netloc
        self.delay = delay
        self._next_slot_at = 0.0
        self._slot_lock = threading.Lock()
        self._local = threading.local()

    def _wait_for_slot(self):
        with self._slot_lock:
            now = time.monotonic()
            slot = max(now, self._next_slot_at)
            self._next_slot_at = slot + self.delay
        wait = slot - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _connection(self):
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = http.client.HTTPSConnection(self.host, timeout=REQUEST_TIMEOUT_SEC)
            self._local.connection = connection
        return connection

    def _drop_connection(self):
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def close(self):
        self._drop_connection()

    def get_json(self, path, params=None):
        if params:
            path += "?" + urllib.parse.urlencode(params)

        for attempt in range(REQUEST_RETRIES):
            self._wait_for_slot()
            try:
                connection = self._connection()
                connection.request("GET", path, headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Connection": "keep-alive",
                })
                response = connection.getresponse()
                body = response.read()
                if response.status != 200:
                    raise HttpError(f"HTTP {response.status} dla {path}")
                return json.loads(body.decode("utf-8"))
            except HttpError:
                # Odpowiedź serwera, nie awaria łącza - ponowienie da to samo.
                raise
            except (OSError, http.client.HTTPException):
                # Utrzymywane połączenie serwer może zamknąć w dowolnym
                # momencie - wtedy trzeba je odtworzyć, a nie tylko ponowić.
                self._drop_connection()
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


def fetch_snapshot(client=None, days=DEFAULT_DAYS, today=None, log=print):
    """Pobiera surowy rozkład na `days` dni od `today` - kopię do zapisania.

    Surowy, a nie przerobiony na kursy, żeby całe przekształcenie żyło
    w jednym miejscu (siechnice.feed_from_snapshot) i dało się je poprawić
    bez ponownego odpytywania serwera.
    """
    client = client or Client()
    today = today or date.today()

    stop_rows = client.stops()
    designators = [s["designator"] for s in siechnice.parse_stops(stop_rows)]
    log(f"  słupki: {len(designators)}")

    snapshot = {
        "source": BASE_URL,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "stops": stop_rows,
        "days": [],
    }

    # Jedna pula na całe pobieranie, nie po jednej na przebieg: wątek trzyma
    # otwarte połączenie (patrz Client), a nowa pula co przebieg kazałaby
    # nawiązywać je od nowa - czyli dokładnie to, czego unikamy.
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        def gather(fetch_one):
            """fetch_one(designator) na wszystkich słupkach -> {designator: wynik}."""
            return dict(zip(designators, pool.map(fetch_one, designators)))

        snapshot["lines_by_stop"] = {
            designator: sorted({d["line"] for d in directions if d.get("line")})
            for designator, directions in gather(client.directions).items()
        }

        for offset in range(days):
            day = today + timedelta(days=offset)
            departures_by_stop = {
                designator: [
                    {key: d[key] for key in ("departure", "trip_id", "index")}
                    for d in payload.get("departures", [])
                ]
                for designator, payload in gather(
                    lambda designator: client.timetable(designator, day)
                ).items()
            }
            count = sum(len(v) for v in departures_by_stop.values())
            log(f"  {day}: odjazdów {count}")
            snapshot["days"].append({
                "date": day.isoformat(),
                "departures_by_stop": departures_by_stop,
            })

    return snapshot


def main():
    """Zapis idzie przez plik obok i podmianę, więc przerwane pobieranie nie
    zostawia połowy tygodnia tam, skąd czyta aktualizacja bazy."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else siechnice.REPO_SNAPSHOT_PATH
    started = time.monotonic()
    print(f"Siechnice: pobieram tydzień rozkładu z {BASE_URL}...")
    snapshot = fetch_snapshot()

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)
    print(f"Gotowe: {path} ({time.monotonic() - started:.0f} s)")


if __name__ == "__main__":
    main()
