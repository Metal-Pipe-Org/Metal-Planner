"""Odliczanie do wybranego odjazdu w Dynamic Island przez aplikację PushWard.

Przeglądarka wysyła tu klucz integracji i opis odjazdu, a serwer woła REST API
PushWard (https://api.pushward.app). Przejście przez nasz serwer jest dlatego,
że z dokumentacji API nie wynika, czy przyjmuje zapytania z przeglądarki
(CORS), a sprawdzić tego bez prawdziwego klucza się nie da. Klucz jest tylko
w nagłówku wychodzącego zapytania: nie trafia do logów, plików ani odpowiedzi.

Jedna karta (stały slug) służy za każdym razem: darmowy plan to 250
aktualizacji miesięcznie, więc przejazd ma kosztować jeden PATCH, a nie
tworzenie karty od zera. Karta powstaje tylko wtedy, gdy PATCH odpowie 404.
"""

import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as clock
from zoneinfo import ZoneInfo

API = "https://api.pushward.app"
SLUG = "metal-planner-odjazd"
CARD_NAME = "Odjazd"
HTTP_TIMEOUT_SEC = 10
THIRTY_DAYS_SEC = 30 * 24 * 3600
WARNING_SEC = 120
DISMISSAL_SEC = 300
ICONS = {"tram": "tram.fill", "bus": "bus.fill"}
WARSAW = ZoneInfo("Europe/Warsaw")


class PushWardError(Exception):
    def __init__(self, status, code=None, detail=None):
        super().__init__(status, code, detail)
        self.status = status
        self.code = code
        self.detail = detail


def end_date(day, sec):
    """Unix timestamp odjazdu o `sec` sekundzie doby rozkładowej `day`.

    Doba GTFS liczy się od południa minus 12 h w czasie lokalnym, więc w dniu
    zmiany czasu `sec` mierzy prawdziwe sekundy od "północy" rozkładu, a godzina
    25:10 wypada o 01:10 następnego dnia.
    """
    noon = datetime.combine(date.fromisoformat(day), clock(12), WARSAW)
    return int(noon.timestamp() - 12 * 3600 + sec)


def _hhmm(timestamp):
    return datetime.fromtimestamp(timestamp, WARSAW).strftime("%H:%M")


def _request(method, path, key, body):
    """Jedno wywołanie API; zwraca zdekodowany JSON albo rzuca PushWardError."""
    request = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode("utf-8"),
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/merge-patch+json" if method == "PATCH"
                            else "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        try:
            problem = json.loads(e.read())
        except ValueError:
            problem = {}
        raise PushWardError(e.code, problem.get("code"), problem.get("detail")) from None
    except OSError:
        raise PushWardError(0) from None
    return json.loads(raw) if raw else {}


def _card_body(departure):
    ends = end_date(departure["date"], departure["sec"])
    return {
        "state": "ONGOING",
        "dismissal_ttl": DISMISSAL_SEC,
        "content": {
            "template": "countdown",
            "state": f'{departure["num"]} → {departure["headsign"]}',
            "subtitle": f'{departure["stop"]} · {_hhmm(ends)}',
            "icon": ICONS.get(departure["mode"], "bus.fill"),
            "show_ruler": False,
            "start_date": int(time.time()),
            "end_date": ends,
            "warning_threshold": WARNING_SEC,
            "completion_message": "Odjeżdża",
        },
    }


def send_departure(key, departure):
    """Podmienia treść jedynej karty; gdy jej nie ma, tworzy ją i ponawia."""
    body = _card_body(departure)
    path = f"/activities/{SLUG}"
    try:
        _request("PATCH", path, key, body)
    except PushWardError as e:
        if e.status != 404:
            raise
        # ended_ttl na maksimum: serwer skasowałby zakończoną kartę po krótkim
        # czasie, a każde ponowne tworzenie to dodatkowa aktualizacja z limitu.
        _request("POST", "/activities", key, {
            "slug": SLUG,
            "name": CARD_NAME,
            "ended_ttl": THIRTY_DAYS_SEC,
            "dismissal_ttl": DISMISSAL_SEC,
        })
        _request("PATCH", path, key, body)
