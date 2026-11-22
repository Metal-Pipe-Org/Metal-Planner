"""Odliczanie do wybranego odjazdu w Dynamic Island przez aplikację PushWard.

Przeglądarka wysyła tu klucz integracji i opis odjazdu, a serwer woła REST API
PushWard (https://api.pushward.app). Przejście przez nasz serwer jest dlatego,
że z dokumentacji API nie wynika, czy przyjmuje zapytania z przeglądarki
(CORS), a sprawdzić tego bez prawdziwego klucza się nie da. Klucz jest tylko
w nagłówku wychodzącego zapytania: nie trafia do logów, plików ani odpowiedzi.

Tytuł karty (`name`) iOS ustala przy starcie Live Activity i PATCH go nie
zmieni, a tytuł ma mówić, jaka to linia i dokąd. Dlatego każdy przejazd
dostaje własną, nową kartę z własnym slugiem. Poprzednich nie kasujemy ani nie
kończymy: kasowanie zdejmowałoby z ekranu kartę, którą pasażer może jeszcze
śledzić, a stare i tak znikają same po `ended_ttl`.
"""

import json
import secrets
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as clock
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

API = "https://api.pushward.app"
SLUG_PREFIX = "metal-planner-"
HTTP_TIMEOUT_SEC = 10
ENDED_TTL_SEC = 600
WINDOW_SEC = 8 * 3600
WARNING_SEC = 120
DISMISSAL_SEC = 300
ICONS = {"tram": "tram.fill", "bus": "bus.fill"}
WARSAW = ZoneInfo("Europe/Warsaw")
# Cloudflare przed API odrzuca domyślny User-Agent urllib kodem 403 (błąd 1010),
# więc bez własnego nagłówka każdy poprawny klucz wyglądałby na pozbawiony uprawnień.
USER_AGENT = "Metal-Planner/1.0"
CLOUDFLARE_BLOCK = "error code: 1010"


class OutOfWindow(Exception):
    pass


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


def in_window(ends, now):
    """iOS kończy Live Activity po 8 h; odjazd dalszy (albo już miniony) odpada."""
    return 0 < ends - now <= WINDOW_SEC


def _request(method, path, key, body):
    """Jedno wywołanie API; zwraca zdekodowany JSON albo rzuca PushWardError."""
    request = urllib.request.Request(
        API + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/merge-patch+json" if method == "PATCH"
                            else "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        if e.code == 403 and CLOUDFLARE_BLOCK in raw.decode("utf-8", "replace"):
            raise PushWardError(0) from None
        try:
            problem = json.loads(raw)
        except ValueError:
            problem = {}
        raise PushWardError(e.code, problem.get("code"), problem.get("detail")) from None
    except OSError:
        raise PushWardError(0) from None
    return json.loads(raw) if raw else {}


def _tap_url(link):
    """Link tylko http(s) z hostem; PushWard odrzuca resztę, a to nie powód, by nie wysłać odliczenia."""
    try:
        parts = urlsplit(link)
        return link if parts.scheme in ("http", "https") and parts.hostname else None
    except (ValueError, AttributeError):
        return None


def _card_body(departure):
    ends = end_date(departure["date"], departure["sec"])
    body = {
        "state": "ONGOING",
        "dismissal_ttl": DISMISSAL_SEC,
        "content": {
            "template": "countdown",
            "state": departure["stop"],
            "icon": ICONS.get(departure["mode"], "bus.fill"),
            # limit 4 znaków po stronie PushWard; dłuższy numer (np. "810+81") ucinamy, by karta nie została odrzucona
            "compact_label": departure["num"][:4],
            "show_ruler": False,
            "start_date": int(time.time()),
            "end_date": ends,
            "warning_threshold": WARNING_SEC,
            "completion_message": "Odjeżdża",
        },
    }
    tap = _tap_url(departure.get("link"))
    if tap:
        body["content"]["tap_action"] = {"url": tap, "foreground": True}
    return body


def send_departure(key, departure):
    """Zakłada nową kartę z tytułem tego odjazdu i wypełnia ją."""
    if not in_window(end_date(departure["date"], departure["sec"]), time.time()):
        raise OutOfWindow()
    body = _card_body(departure)
    slug = f"{SLUG_PREFIX}{int(time.time() * 1000)}-{secrets.token_hex(3)}"
    _request("POST", "/activities", key, {
        "slug": slug,
        "name": f'{departure["num"]} → {departure["headsign"]}',
        "ended_ttl": ENDED_TTL_SEC,
        "dismissal_ttl": DISMISSAL_SEC,
    })
    _request("PATCH", f"/activities/{slug}", key, body)


def account(key):
    """Konto właściciela klucza (/auth/me): m.in. zużycie miesięcznego limitu."""
    return _request("GET", "/auth/me", key, None)
