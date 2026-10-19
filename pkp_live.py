"""Opóźnienia pociągów na żywo (zgłoszenie #71) - z PKP PLK OpenData,
`/operations/train/{scheduleId}/{orderId}/{date}`, pociąg po pociągu.

PO POCIĄGU, NIE CAŁY KRAJ. W zgłoszeniu ustaliliśmy wspólny bufor wszystkich
opóźnień odświeżany co 30 s. Zmierzone 2026-10-12: cały kraj to 25,8 tys.
pociągów na sześciu stronach po ~4,8 MB, czyli sześć zapytań na jedno
odświeżenie - co 30 s to 720 zapytań na godzinę przy limicie 500, a co minutę
wciąż ponad dobowy limit 5000. Pytamy więc tylko o pociągi z pokazanych
właśnie tras, każdy najwyżej raz na TTL_SEC, i pilnujemy własnych sufitów
(HOURLY_LIMIT, DAILY_LIMIT) poniżej limitów API - zapas zostaje na rozkład
(update_pkp.py) i ręczne sprawdzenia. Po przekroczeniu sufitu opóźnień po
prostu nie ma; trasy działają dalej z rozkładu.

OPÓŹNIENIE LICZYMY SAMI. Odpowiedź dla jednego pociągu ma tylko czasy
rzeczywiste (bez planowych, bez `*DelayMinutes`), więc opóźnienie przy stacji
to czas rzeczywisty minus planowy z naszego rozkładu (day.pkp_trip_stops, ta
sama oś doby). Dla stacji jeszcze przed pociągiem PLK podaje prognozę - to
też jest „czas rzeczywisty" w tym API i tak go traktujemy.

Bez PKP_API_KEY (patrz config.py) nic tu nie wychodzi do sieci.
"""

import json
import threading
import time
import urllib.request
from collections import deque
from datetime import date, datetime, timedelta

import config
import pkp

BASE_URL = "https://pdp-api.plk-sa.pl/api/v1/operations/train"
USER_AGENT = "Metal-Planner/0.1 (+https://github.com/Metal-Pipe-Org/Metal-Planner)"
TIMEOUT_SEC = 10

# Własne sufity, poniżej limitów API (500/h, 5000/dobę) - patrz nagłówek.
HOURLY_LIMIT = 400
DAILY_LIMIT = 4000
# Jak długo trzymamy odpowiedź o jednym pociągu. Opóźnienie zmienia się co
# stację, a nie co sekundę; minuta to też sufit tego, ile zapytań jeden
# pociąg może zjeść, choćby szukało go wiele osób naraz.
TTL_SEC = 60
# Ile pociągów najwyżej w jednym pytaniu z frontu - lista tras ma ich kilka.
MAX_TRAINS = 10

_lock = threading.Lock()
_cache = {}             # (trip, data) -> (kiedy pobrano, {stacja: (przyjazd, odjazd)})
_hour = deque()         # momenty zapytań z ostatniej godziny
_day = [None, 0]        # [data, liczba zapytań tego dnia]


def _take_slot(now):
    """Czy wolno teraz zapytać API - i jeśli tak, zapisuje zapytanie."""
    while _hour and now - _hour[0] > 3600:
        _hour.popleft()
    today = date.today()
    if _day[0] != today:
        _day[0], _day[1] = today, 0
    if len(_hour) >= HOURLY_LIMIT or _day[1] >= DAILY_LIMIT:
        return False
    _hour.append(now)
    _day[1] += 1
    return True


def _parse(stamp):
    return datetime.fromisoformat(stamp) if stamp else None


def _fetch(schedule_id, order_id, when):
    url = f"{BASE_URL}/{schedule_id}/{order_id}/{when.isoformat()}"
    request = urllib.request.Request(url, headers={
        "X-API-Key": config.pkp_api_key(),
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
        data = json.load(response)
    return {
        station["stationId"]: (_parse(station.get("actualArrival")),
                               _parse(station.get("actualDeparture")))
        for station in data.get("stations", ())
    }


def _actual_times(trip, when):
    """{stacja: (przyjazd, odjazd)} pociągu albo None - z bufora, a gdy go nie
    ma albo jest starszy niż TTL_SEC, z API, o ile pozwalają sufity."""
    key = (trip, when)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached is not None and now - cached[0] < TTL_SEC:
            return cached[1]
        if not _take_slot(now):
            return cached[1] if cached is not None else None
    _, schedule_id, order_id = trip.split(":")
    try:
        times = _fetch(schedule_id, order_id, when)
    except (OSError, ValueError):
        times = None
    with _lock:
        _cache[key] = (now, times)
        # Bufor nie rośnie bez końca: wpisy sprzed kwadransa są bezużyteczne.
        for old in [k for k, (at, _) in _cache.items() if now - at > 900]:
            del _cache[old]
    return times


def _minutes(actual, planned):
    if actual is None:
        return None
    return round((actual - planned).total_seconds() / 60)


def delays(day, trips, when):
    """{kurs: {nazwa stacji: {"arr": min, "dep": min}}} dla pociągów z `trips`
    (identyfikatory "PKP:..." z dnia `day`, data `when`) - tylko tam, gdzie
    PLK zna czas. Klucz to nazwa stacji, bo tyle niesie etap trasy na froncie.

    Opóźnienia są tylko dla DZIŚ: dla innej daty nie ma czego pytać.
    """
    if not pkp.enabled() or when != date.today():
        return {}
    midnight = datetime.combine(when, datetime.min.time())
    out = {}
    for trip in trips[:MAX_TRAINS]:
        rows = day.pkp_trip_stops.get(trip)
        if not trip.startswith("PKP:") or not rows:
            continue
        times = _actual_times(trip, when)
        if not times:
            continue
        per_stop = {}
        for stop_id, arr_sec, dep_sec in rows:
            actual = times.get(int(stop_id.partition(":")[2]))
            if actual is None:
                continue
            arr = _minutes(actual[0], midnight + timedelta(seconds=arr_sec))
            dep = _minutes(actual[1], midnight + timedelta(seconds=dep_sec))
            if arr is not None or dep is not None:
                per_stop.setdefault(day.stop_names[stop_id], {"arr": arr, "dep": dep})
        if per_stop:
            out[trip] = per_stop
    return out
