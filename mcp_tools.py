"""
Narzędzia, które klient MCP dostaje od wyszukiwarki (patrz mcp_server.py).

Wszystkie tylko CZYTAJĄ i każde jest cienką nakładką na tę samą funkcję, którą
woła odpowiedni endpoint /api/* - więc odpowiedź narzędzia to dokładnie to, co
widzi strona, tylko bez geometrii. Współrzędne przebiegów, sekundy na osi doby
i identyfikatory słupków są dla mapy; modelowi zjadają kontekst i nic nie
mówią, więc _plain wycina je z każdej odpowiedzi w jednym miejscu.

Daty i godziny są w strefie serwera (w kontenerze Europe/Warsaw), tak jak przy
formularzu na stronie. W przeciwieństwie do /api/* błędna data czy godzina nie
przechodzi tu po cichu w "teraz": model dostaje odmowę i wie, co poprawić.
"""

import json
import re
from datetime import datetime
from functools import partial
from urllib.parse import urlencode

from flask import request

import gtfs
import timetables
import warmup
from planner import (MAX_JOURNEY_LIMIT, TIMETABLE_LIMIT, TIMETABLE_MAX,
                     plan_flow, stop_timetable)
from routes import LIVE_REFRESH_WAIT_SEC


INSTRUCTIONS = (
    "Metal-Planner to wyszukiwarka połączeń komunikacji miejskiej Wrocławia: "
    "tramwaje i autobusy MPK, pociągi (PKP), Polinka, rower miejski WRM i auta "
    "Traficara. Rozkład pochodzi z GTFS miasta i z danych PKP. Daty to YYYY-MM-DD, "
    "godziny HH:MM czasu polskiego; bez nich narzędzia biorą dzisiaj i teraz. "
    "Przystanki podaje się nazwą - wielkość liter, polskie znaki i skróty "
    "(\"pl grunwaldzki\") nie mają znaczenia; nieznana nazwa wraca z podpowiedziami. "
    "Zamiast nazwy można podać punkt \"lat,lon\". plan_trip oddaje też link do mapy "
    "przepływów na stronie - warto go pokazać, bo mapa mówi więcej niż lista tras."
)


class ToolError(Exception):
    """Odmowa, którą narzędzie oddaje klientowi jako swoją odpowiedź."""


_TOOLS = {}


def tool(name, description, properties=None, required=()):
    def register(handler):
        _TOOLS[name] = {
            "definition": {
                "name": name,
                "description": description,
                "inputSchema": {
                    "type": "object",
                    "properties": properties or {},
                    "required": list(required),
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    # Rozkład i dane na żywo przychodzą z zewnątrz, ale
                    # narzędzie nie sięga po nic, czego nie wskazał serwer.
                    "openWorldHint": False,
                },
            },
            "handler": handler,
        }
        return handler

    return register


def definitions():
    return [entry["definition"] for entry in _TOOLS.values()]


def exists(name):
    return name in _TOOLS


def call(name, arguments):
    """Woła jedno narzędzie i opakowuje odpowiedź tak, jak oddaje ją tools/call.

    Słownik z kluczem "error" (tak odmawiają funkcje plannera: nieznany
    przystanek, rozkład jeszcze nieopublikowany) jest odpowiedzią z isError -
    model ma ją przeczytać, a nie uznać za awarię serwera.
    """
    try:
        data = _TOOLS[name]["handler"](arguments)
    except ToolError as refusal:
        data = {"error": str(refusal)}
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}],
        "isError": "error" in data,
    }


# --- odpowiedzi bez geometrii ------------------------------------------------------------

# Pola dla mapy i dla frontu: przebiegi, współrzędne, sekundy na osi doby,
# identyfikatory słupków. Wycinane rekurencyjnie, więc nowy rodzaj etapu trasy
# (rower, auto, Polinka) nie przemyci przebiegu tylko dlatego, że nikt tu o nim
# nie pomyślał.
_MAP_ONLY = frozenset({
    "path", "tail", "lat", "lon", "center", "points", "id", "stop_id", "board_index",
    "sec", "dep_sec", "arr_sec", "departure_sec", "arrival_sec", "from_sec",
})


def _plain(value):
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if k not in _MAP_ONLY}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


# --- argumenty ---------------------------------------------------------------------------

_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_POINT = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

DATE = {"type": "string", "description": "Dzień jako YYYY-MM-DD; domyślnie dzisiaj."}
TIME = {"type": "string", "description": "Godzina jako HH:MM; domyślnie teraz."}


def _text(arguments, name, required=True):
    value = arguments.get(name)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ToolError(f"Brak argumentu „{name}” (tekst).")
    return value.strip()


def _when(arguments):
    """Moment z `date` i `time` - brakujące to dzisiaj i teraz."""
    when = datetime.now().replace(second=0, microsecond=0)
    date = _text(arguments, "date", required=False)
    if date:
        match = _DATE.match(date)
        try:
            when = when.replace(year=int(match.group(1)), month=int(match.group(2)),
                                day=int(match.group(3)))
        except (AttributeError, ValueError):
            raise ToolError(f"Zła data „{date}” - potrzebne YYYY-MM-DD.") from None
    time = _text(arguments, "time", required=False)
    if time:
        match = _TIME.match(time)
        try:
            when = when.replace(hour=int(match.group(1)), minute=int(match.group(2)))
        except (AttributeError, ValueError):
            raise ToolError(f"Zła godzina „{time}” - potrzebne HH:MM.") from None
    return when


def _place(text):
    """(nazwa, punkt) dla plannera: albo nazwa przystanku, albo "lat,lon"."""
    match = _POINT.match(text)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return "", (lat, lon)
    return text, None


def _flag(arguments, name, default):
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise ToolError(f"Argument „{name}” to true albo false.")
    return value


def _count(arguments, name, default, ceiling):
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ToolError(f"Argument „{name}” to liczba całkowita od 1.")
    return min(value, ceiling)


def _page_link(start, end, when):
    """Adres strony z tym samym wyszukiwaniem (parametry jak w routes.link_preview).

    Za nginxem Flask widzi http, więc schemat bierzemy z X-Forwarded-Proto,
    gdy proxy go podaje - inaczej link z https-owej strony prowadziłby na http.
    """
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    query = urlencode({"from": start, "to": end,
                       "date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M")})
    return f"{scheme}://{request.host}/?{query}"


# --- narzędzia ---------------------------------------------------------------------------


@tool(
    "plan_trip",
    "Propozycje przejazdu z A do B komunikacją miejską Wrocławia (to samo, co lista tras "
    "pod mapą przepływów na stronie): odjazd, przyjazd, czas, przesiadki i etapy - którą "
    "linią, skąd, dokąd, o której, przez jakie przystanki. Każdy etap ma `trip`, o który "
    "można dopytać narzędziem trip. Oddaje też `link` do mapy na stronie.",
    {
        "from": {"type": "string", "description": "Skąd: nazwa przystanku albo \"lat,lon\"."},
        "to": {"type": "string", "description": "Dokąd: nazwa przystanku albo \"lat,lon\"."},
        "date": DATE,
        "time": {"type": "string", "description": "Godzina wyjścia jako HH:MM; domyślnie teraz."},
        "bikes": {"type": "boolean",
                  "description": "Dołóż propozycje z rowerem miejskim WRM (tylko dziś; domyślnie nie)."},
        "traficar": {"type": "boolean",
                     "description": "Dołóż propozycje kończące się autem Traficara (domyślnie tak)."},
        "limit": {"type": "integer", "description": f"Ile propozycji szukać (1-{MAX_JOURNEY_LIMIT})."},
    },
    required=("from", "to"),
)
def plan_trip(arguments):
    start = _text(arguments, "from")
    end = _text(arguments, "to")
    when = _when(arguments)
    start_name, start_point = _place(start)
    end_name, end_point = _place(end)
    limit = arguments.get("limit")
    search = partial(
        plan_flow, start_name, end_name, when, start_point, end_point,
        journey_limit=None if limit is None else _count(arguments, "limit", 1, MAX_JOURNEY_LIMIT),
        use_bikes=_flag(arguments, "bikes", False),
        use_cars=_flag(arguments, "traficar", True),
    )
    # Jak /api/flow: liczymy na danych na żywo (rowery, auta), które są
    # w pamięci, a gdy były stare, czekamy na świeże i liczymy jeszcze raz.
    # Strona dosyła wtedy etap "fresh"; tu odpowiedź jest jedna, więc bez
    # drugiego liczenia propozycje z Traficarem i rowerem by przepadały.
    with warmup.no_waiting() as live:
        result = search()
    if live.stale and "error" not in result:
        warmup.wait_for_refreshes(LIVE_REFRESH_WAIT_SEC)
        with warmup.no_waiting():
            result = search()
    if "error" in result:
        return _plain(result)
    journeys = _plain(result.get("journeys") or [])
    answer = {
        "start": result.get("start"),
        "end": result.get("end"),
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M"),
        "journeys": journeys,
        "link": _page_link(start, end, when),
    }
    if not journeys:
        answer["note"] = "Brak propozycji w tym oknie czasu - spróbuj innej godziny albo pokaż mapę."
    return answer


@tool(
    "find_stop",
    "Sprawdza, jak wyszukiwarka rozumie nazwę przystanku albo stacji: oddaje nazwę, pod "
    "którą go zna, albo podpowiedzi, gdy nazwa jest nieznana lub niejednoznaczna.",
    {"query": {"type": "string", "description": "Wpisana nazwa, np. \"pl grunwaldzki\"."},
     "date": DATE},
    required=("query",),
)
def find_stop(arguments):
    query = _text(arguments, "query")
    try:
        day = gtfs.load_day(_when(arguments).date())
    except FileNotFoundError as e:
        return {"error": str(e)}
    name, stops, hints = gtfs.match_stop(query, day)
    if name is None:
        return {"error": f"Nie znam przystanku „{query}”.", "suggestions": hints or []}
    return {"name": name, "stops_count": len(stops)}


@tool(
    "departures",
    "Najbliższe odjazdy z przystanku - wszystkie linie, od podanej godziny (tablica, którą "
    "strona pokazuje pod kropką przesiadki). Przystanek to całe miejsce, nie jeden słupek.",
    {"stop": {"type": "string", "description": "Nazwa przystanku albo stacji."},
     "date": DATE,
     "time": TIME,
     "limit": {"type": "integer",
               "description": f"Ile odjazdów (domyślnie {TIMETABLE_LIMIT}, najwyżej {TIMETABLE_MAX})."}},
    required=("stop",),
)
def departures(arguments):
    return _plain(stop_timetable(
        _text(arguments, "stop"),
        _when(arguments),
        limit=_count(arguments, "limit", TIMETABLE_LIMIT, TIMETABLE_MAX),
    ))


@tool(
    "trip",
    "Jeden kurs: wszystkie przystanki z godzinami. `trip` pochodzi z etapu plan_trip.",
    {"trip": {"type": "string", "description": "Identyfikator kursu z plan_trip."},
     "date": {"type": "string",
              "description": "Dzień kursu jako YYYY-MM-DD - ten sam, o który pytało plan_trip."}},
    required=("trip",),
)
def trip(arguments):
    return _plain(timetables.trip_detail(_text(arguments, "trip"), _when(arguments).date()))


@tool(
    "line",
    "Trasa linii danego dnia: jej warianty (kierunki i kursy skrócone), każdy z liczbą "
    "kursów i listą przystanków po kolei. Godzin tu nie ma - o nie pyta się departures.",
    {"num": {"type": "string", "description": "Numer linii, np. \"3\", \"N\", \"D\"."},
     "mode": {"type": "string", "enum": ["tram", "bus"],
              "description": "Tylko gdy ten sam numer ma tramwaj i autobus."},
     "date": DATE},
    required=("num",),
)
def line(arguments):
    mode = _text(arguments, "mode", required=False) or None
    result = timetables.line_timetable(_text(arguments, "num"), _when(arguments).date(),
                                       mode, geometry=False)
    # Przystanki wariantu same w sobie to tylko nazwy po kolei - słupek ze
    # współrzędnymi i id nic modelowi nie mówi, a linia ma ich po kilkadziesiąt.
    for variant in result.get("variants") or []:
        variant["stops"] = [stop["name"] for stop in variant.get("stops") or []]
    return _plain(result)
