"""
Narzędzia, które klient MCP dostaje od wyszukiwarki (patrz mcp_server.py).

Wszystkie tylko CZYTAJĄ i każde jest cienką nakładką na tę samą funkcję, którą
woła odpowiedni endpoint /api/* - więc odpowiedź narzędzia to dokładnie to, co
widzi strona, tylko bez geometrii. Zasięg ma być ten sam co strony: wszystko,
co pasażer może tam sprawdzić - wyszukiwanie (też z pokładu pojazdu i z jego
ustawieniami spod ⚙), tablice i pełne rozkłady przystanków, trasy linii,
kursy, pociągi z opóźnieniami, pojazdy na żywo, rowery i auta.

Współrzędne przebiegów, sekundy na osi doby i identyfikatory słupków są dla
mapy; modelowi zjadają kontekst i nic nie mówią, więc _plain wycina je
z odpowiedzi w jednym miejscu. Wyjątkiem są narzędzia "na żywo" (pojazdy,
rowery, auta): tam położenie jest treścią, więc oddają je same, z nazwą
najbliższego przystanku obok, żeby dało się je przeczytać bez mapy.

Daty i godziny są w strefie serwera (w kontenerze Europe/Warsaw), tak jak przy
formularzu na stronie. W przeciwieństwie do /api/* błędna data czy godzina nie
przechodzi tu po cichu w "teraz": model dostaje odmowę i wie, co poprawić.
"""

import http.client
import json
import re
from datetime import date as date_type
from datetime import datetime
from functools import partial
from urllib.parse import urlencode

from flask import request

import bikes
import gtfs
import onboard
import pkp_live
import timetables
import traficar
import vehicles
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
    "Zamiast nazwy można podać punkt \"lat,lon\". Do przejazdu z A do B jest plan_trip "
    "(też z pokładu pojazdu, w którym się siedzi); oddaje link do mapy przepływów na "
    "stronie - warto go pokazać, bo mapa mówi więcej niż lista tras. Rozkład wiszący "
    "na słupku to stop_schedule, najbliższe odjazdy - departures. Opóźnienia pociągów "
    "są tylko dla dzisiaj."
)


class ToolError(Exception):
    """Odmowa, którą narzędzie oddaje klientowi jako swoją odpowiedź."""


_TOOLS = {}


def tool(name, description, properties=None, required=(), live=False):
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
                    "idempotentHint": not live,
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
PLACE = "nazwa przystanku albo stacji, albo punkt \"lat,lon\""


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


def _point(text):
    """(lat, lon) z napisu "lat,lon" albo None."""
    match = _POINT.match(text)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    return None


def _place(text):
    """(nazwa, punkt) dla plannera: albo nazwa przystanku, albo "lat,lon"."""
    point = _point(text)
    return ("", point) if point else (text, None)


def _flag(arguments, name, default):
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise ToolError(f"Argument „{name}” to true albo false.")
    return value


def _count(arguments, name, default, ceiling, floor=1):
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not floor <= value:
        raise ToolError(f"Argument „{name}” to liczba całkowita od {floor}.")
    return min(value, ceiling)


def _choice(arguments, name, allowed):
    value = _text(arguments, name, required=False)
    if value and value not in allowed:
        raise ToolError(f"Argument „{name}” to jedno z: {', '.join(sorted(allowed))}.")
    return value or None


def _number(arguments, name):
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ToolError(f"Argument „{name}” to liczba większa od zera.")
    return float(value)


def _day(when):
    try:
        return gtfs.load_day(when.date())
    except FileNotFoundError as e:
        raise ToolError(str(e)) from None


def _norm(text):
    """Klucz porównania nazw: bez wielkości liter, spacji z brzegu i ogonków."""
    return gtfs._strip_diacritics(" ".join(text.split()).casefold())


def _digits(text):
    return "".join(ch for ch in text if ch.isdigit())


def _locate(text):
    """(opis, (lat, lon)) miejsca podanego nazwą przystanku albo punktem.
    Rozkład dnia potrzebny jest tylko do nazwy - punkt obchodzi się bez niego."""
    point = _point(text)
    if point:
        return text, point
    day = _day(_when({}))
    name, stop_ids, hints = gtfs.match_stop(text, day)
    if name is None:
        raise ToolError(f"Nie znam przystanku „{text}”."
                        + (f" Może: {', '.join(hints)}." if hints else ""))
    coords = [day.stop_coords[s] for s in stop_ids if s in day.stop_coords]
    if not coords:
        raise ToolError(f"Przystanek „{name}” nie ma położenia na mapie.")
    return name, (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))


def _nearest_stop(lat, lon, stops):
    """Nazwa najbliższego przystanku i odległość w metrach - opis położenia."""
    best = min(stops, key=lambda s: bikes.haversine_m(lat, lon, s["lat"], s["lon"]))
    return best["name"], round(bikes.haversine_m(lat, lon, best["lat"], best["lon"]))


def _page_link(start, end, when):
    """Adres strony z tym samym wyszukiwaniem (parametry jak w routes.link_preview).

    Za nginxem Flask widzi http, więc schemat bierzemy z X-Forwarded-Proto,
    gdy proxy go podaje - inaczej link z https-owej strony prowadziłby na http.
    """
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    query = urlencode({"from": start, "to": end,
                       "date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M")})
    return f"{scheme}://{request.host}/?{query}"


# --- opóźnienia pociągów -----------------------------------------------------------------


def _delays(day, when, trips):
    """{kurs: {stacja: {"arr", "dep"}}} jak /api/train_delays - tylko dla dziś
    i tylko pociągów; reszta to pusty słownik (pkp_live.delays sam pilnuje)."""
    trains = [t for t in trips if t and t.startswith("PKP:")]
    if not trains or when.date() != date_type.today():
        return {}
    return pkp_live.delays(day, trains, when.date())


def _delay_of(per_stop, station, key):
    minutes = (per_stop.get(station) or {}).get(key)
    return minutes if isinstance(minutes, (int, float)) else None


# --- wyszukiwanie ------------------------------------------------------------------------


def _in_vehicle(arguments, when):
    """Start z pokładu pojazdu (onboard_*) jak `in_vehicle` w /api/flow albo None.

    Strona wybiera NASTĘPNY przystanek z listy słupków kierunku; tu przychodzi
    jego nazwa, więc słupek szukamy po niej w kierunkach linii - najpierw po
    nazwie słupka, potem po całym miejscu (gtfs.match_stop), bo "Dworzec Główny"
    to kilka słupków o różnych nazwach.
    """
    num = _text(arguments, "onboard_line", required=False)
    if not num:
        return None
    next_stop = _text(arguments, "onboard_next_stop")
    wanted_headsign = _norm(_text(arguments, "onboard_headsign", required=False))
    # Numer pociągu ma cztery cyfry i więcej; linie miejskie najwyżej trzy.
    mode = "train" if len(_digits(num)) >= 4 else None
    data = onboard.directions(num, when.date(), mode)
    if "error" in data:
        raise ToolError(data["error"])

    directions = [d for d in data.get("directions") or []
                  if not wanted_headsign or wanted_headsign in _norm(d["headsign"])]
    if not directions:
        raise ToolError(f"Linia {num} nie jedzie w kierunku „{arguments.get('onboard_headsign')}”. "
                        f"Kierunki: {', '.join(d['headsign'] for d in data.get('directions') or [])}.")

    def matches(by):
        return [(d, s) for d in directions for s in d["stops"] if by(s)]

    found = matches(lambda s: _norm(s["name"]) == _norm(next_stop))
    if not found:
        _, place_ids, _ = gtfs.match_stop(next_stop, _day(when))
        place_ids = set(place_ids or ())
        found = matches(lambda s: s.get("id") in place_ids)
    if not found:
        raise ToolError(f"Linia {num} nie staje na przystanku „{next_stop}” w tym kierunku.")
    headsigns = {d["headsign"] for d, _ in found}
    if len(headsigns) > 1:
        raise ToolError(f"Linia {num} staje na „{next_stop}” w kilku kierunkach - podaj "
                        f"onboard_headsign: {', '.join(sorted(headsigns))}.")
    direction, stop = found[0]
    return {"num": data["num"], "mode": data.get("mode"), "headsign": direction["headsign"],
            "stop": stop["id"], "slack_sec": None}


@tool(
    "plan_trip",
    "Propozycje przejazdu z A do B komunikacją miejską Wrocławia (to samo, co lista tras "
    "pod mapą przepływów na stronie): odjazd, przyjazd, czas, przesiadki i etapy - którą "
    "linią, skąd, dokąd, o której, przez jakie przystanki; przy pociągach dziś też "
    "opóźnienie. Każdy etap ma `trip`, o który można dopytać narzędziem trip. Start może "
    "być z pokładu pojazdu (onboard_*): wtedy `from` jest zbędne, a propozycje mówią, "
    "gdzie wysiąść. Oddaje też `link` do mapy na stronie.",
    {
        "from": {"type": "string", "description": f"Skąd: {PLACE}."},
        "to": {"type": "string", "description": f"Dokąd: {PLACE}."},
        "date": DATE,
        "time": {"type": "string", "description": "Godzina wyjścia jako HH:MM; domyślnie teraz."},
        "onboard_line": {"type": "string",
                         "description": "Jadę już tą linią albo pociągiem (numer, np. \"3\" albo \"6123\")."},
        "onboard_next_stop": {"type": "string",
                              "description": "Najbliższy przystanek, na którym pojazd się zatrzyma."},
        "onboard_headsign": {"type": "string",
                             "description": "Kierunek z czoła pojazdu, gdy linia staje tam w kilku."},
        "bikes": {"type": "boolean",
                  "description": "Dołóż propozycje z rowerem miejskim WRM (tylko dziś; domyślnie nie)."},
        "electric_bikes": {"type": "boolean", "description": "Rowery elektryczne (domyślnie tak)."},
        "regular_bikes": {"type": "boolean", "description": "Rowery zwykłe (domyślnie tak)."},
        "traficar": {"type": "boolean",
                     "description": "Dołóż propozycje kończące się autem Traficara (domyślnie tak)."},
        "vans": {"type": "boolean", "description": "Także dostawczaki Traficara (domyślnie nie)."},
        "walking": {"type": "boolean",
                    "description": "Przejścia piesze między przystankami (domyślnie tak)."},
        "walk_pace": {"type": "string", "enum": sorted(gtfs.WALK_PACES),
                      "description": f"Tempo marszu (domyślnie {gtfs.DEFAULT_WALK_PACE})."},
        "bike_kmh": {"type": "number", "description": "Prędkość roweru w km/h."},
        "car_kmh": {"type": "number", "description": "Prędkość auta w km/h."},
        "more": {"type": "integer",
                 "description": "Ile razy kliknąć „Pokaż więcej” (0-3): dłuższe, ale inne trasy."},
        "limit": {"type": "integer", "description": f"Ile propozycji szukać (1-{MAX_JOURNEY_LIMIT})."},
    },
    required=("to",),
)
def plan_trip(arguments):
    end = _text(arguments, "to")
    when = _when(arguments)
    in_vehicle = _in_vehicle(arguments, when)
    start = _text(arguments, "from", required=in_vehicle is None)
    start_name, start_point = _place(start)
    end_name, end_point = _place(end)
    limit = arguments.get("limit")
    search = partial(
        plan_flow, start_name, end_name, when, start_point, end_point,
        journey_limit=None if limit is None else _count(arguments, "limit", 1, MAX_JOURNEY_LIMIT),
        more=_count(arguments, "more", 0, 3, floor=0),
        use_bikes=_flag(arguments, "bikes", False),
        bike_electric=_flag(arguments, "electric_bikes", True),
        bike_regular=_flag(arguments, "regular_bikes", True),
        use_cars=_flag(arguments, "traficar", True),
        car_vans=_flag(arguments, "vans", False),
        walking=_flag(arguments, "walking", True),
        walk_pace=_choice(arguments, "walk_pace", gtfs.WALK_PACES),
        bike_kmh=_number(arguments, "bike_kmh"),
        car_kmh=_number(arguments, "car_kmh"),
        in_vehicle=in_vehicle,
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
    legs = [leg for journey in journeys for leg in journey.get("legs") or []]
    delays = _delays(_day(when), when, [leg.get("trip") for leg in legs])
    for leg in legs:
        per_stop = delays.get(leg.get("trip"))
        if per_stop:
            leg["delay_min"] = {"departure": _delay_of(per_stop, leg.get("from"), "dep"),
                                "arrival": _delay_of(per_stop, leg.get("to"), "arr")}

    answer = {
        "start": result.get("start"),
        "end": result.get("end"),
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M"),
        "journeys": journeys,
    }
    if any(leg.get("kind") == "drive" for leg in legs) and "cars_dest_in_zone" in result:
        # Auto da się oddać tylko w strefie Traficara - strona ostrzega o tym
        # na liście, więc i tu: False to cel poza strefą.
        answer["traficar_destination_in_zone"] = result["cars_dest_in_zone"]
    if in_vehicle is None:
        # Link niesie tylko skąd-dokąd-kiedy; startu z pojazdu adres strony nie zna.
        answer["link"] = _page_link(start, end, when)
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
    name, stops, hints = gtfs.match_stop(query, _day(_when(arguments)))
    if name is None:
        return {"error": f"Nie znam przystanku „{query}”.", "suggestions": hints or []}
    return {"name": name, "stops_count": len(stops)}


# --- rozkłady ----------------------------------------------------------------------------


@tool(
    "departures",
    "Najbliższe odjazdy z przystanku albo stacji - wszystkie linie i pociągi, od podanej "
    "godziny (tablica „podana godzina” na stronie). Przystanek to całe miejsce, nie jeden słupek.",
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


def _line_matches(entry, wanted):
    """Czy wiersz `lines` tablicy to linia, o którą pytano: numer bez wielkości
    liter i spacji, a dla pociągu (cztery cyfry i więcej) - sam numer bez
    kategorii, bo pytający mówi "6123", a nie "IC 6123"."""
    num = _norm(entry["num"]).replace(" ", "")
    if num == wanted.replace(" ", ""):
        return True
    digits = _digits(wanted)
    return len(digits) >= 4 and _digits(num) == digits


@tool(
    "stop_schedule",
    "Rozkład z przystanku albo stacji na cały dzień - „pełny rozkład” na stronie, czyli to, "
    "co wisi na słupku: dla każdej linii i kierunku godziny z minutami odjazdów. Bez `line` "
    "oddaje listę linii i kierunków, które tu odjeżdżają (z liczbą kursów); z `line` - "
    "ich godziny. `repeats` mówi, w które dni rozkład jest taki sam.",
    {"stop": {"type": "string", "description": "Nazwa przystanku albo stacji."},
     "line": {"type": "string", "description": "Numer linii albo pociągu, np. \"3\", \"N\", \"6123\"."},
     "headsign": {"type": "string", "description": "Tylko ten kierunek (fragment napisu z czoła)."},
     "date": DATE},
    required=("stop",),
)
def stop_schedule(arguments):
    when = _when(arguments)
    board = timetables.stop_board(_text(arguments, "stop"), when.date())
    if "error" in board:
        return board
    lines = board.get("lines") or []
    head = {"stop": board["stop"], "date": board["date"], "repeats": board.get("repeats")}

    wanted = _norm(_text(arguments, "line", required=False))
    if not wanted:
        return {**head, "lines": [{"num": entry["num"], "mode": entry["mode"],
                                   "headsign": entry["headsign"], "count": entry["count"]}
                                  for entry in lines],
                "note": "Podaj `line`, żeby dostać godziny."}

    headsign = _norm(_text(arguments, "headsign", required=False))
    chosen = [i for i, entry in enumerate(lines)
              if _line_matches(entry, wanted) and headsign in _norm(entry["headsign"])]
    if not chosen:
        raise ToolError(f"Z „{board['stop']}” nie odjeżdża {arguments['line']}"
                        + (f" w kierunku „{arguments['headsign']}”" if headsign else "")
                        + f". Odjeżdżają: {', '.join(sorted({e['num'] for e in lines}))}.")

    schedules = []
    for i in chosen:
        hours = {}
        platforms = set()
        for departure in board["departures"]:
            if departure["line"] != i:
                continue
            hour, _, minute = departure["t"].partition(":")
            hours.setdefault(hour, []).append(minute)
            if departure.get("platform"):
                platforms.add(departure["platform"])
        entry = lines[i]
        schedule = {"num": entry["num"], "mode": entry["mode"], "headsign": entry["headsign"],
                    "count": entry["count"],
                    "hours": {hour: " ".join(minutes) for hour, minutes in hours.items()}}
        if platforms:
            schedule["platforms"] = sorted(platforms)
        schedules.append(schedule)
    return {**head, "schedules": schedules}


@tool(
    "trip",
    "Jeden kurs: wszystkie przystanki z godzinami, a przy pociągu dziś też opóźnienia. "
    "`trip` pochodzi z etapu plan_trip.",
    {"trip": {"type": "string", "description": "Identyfikator kursu z plan_trip."},
     "date": {"type": "string",
              "description": "Dzień kursu jako YYYY-MM-DD - ten sam, o który pytało plan_trip."}},
    required=("trip",),
)
def trip(arguments):
    when = _when(arguments)
    trip_id = _text(arguments, "trip")
    result = _plain(timetables.trip_detail(trip_id, when.date()))
    if "error" not in result:
        _add_stop_delays(result, _delays(_day(when), when, [trip_id]).get(trip_id))
    return result


def _add_stop_delays(detail, per_stop):
    for stop in detail.get("stops") or []:
        delay = (per_stop or {}).get(stop.get("name"))
        if delay:
            stop["delay_min"] = delay


@tool(
    "line",
    "Trasa linii danego dnia: jej warianty (kierunki i kursy skrócone), każdy z liczbą "
    "kursów i listą przystanków po kolei. Godzin tu nie ma - rozkład linii na konkretnym "
    "przystanku daje stop_schedule z `line`.",
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


@tool(
    "train",
    "Pociąg po numerze: jego kursy tego dnia, każdy ze stacjami i godzinami, a dziś także "
    "z opóźnieniem na stacjach, o których wie PLK. Niepełny numer oddaje podpowiedzi.",
    {"num": {"type": "string", "description": "Numer pociągu, np. \"6123\" albo \"IC 6123\"."},
     "date": DATE},
    required=("num",),
)
def train(arguments):
    num = _text(arguments, "num")
    when = _when(arguments)
    digits = _digits(num)
    if len(digits) < 2:
        raise ToolError("Podaj numer pociągu - co najmniej dwie cyfry.")
    day = _day(when)
    trips = sorted(t for t, (label, _) in day.trip_info.items()
                   if label.startswith("Pociąg ") and _digits(label) == digits)
    if not trips:
        suggestions = onboard.trains(day, num)
        return {"error": f"Nie ma pociągu {num} w rozkładzie na {when:%Y-%m-%d}.",
                "suggestions": [f"{t['label']} → {t['headsign']}" for t in suggestions]}
    delays = _delays(day, when, trips)
    runs = []
    for trip_id in trips[:3]:
        detail = _plain(timetables.trip_detail(trip_id, when.date()))
        if "error" not in detail:
            _add_stop_delays(detail, delays.get(trip_id))
        runs.append(detail)
    return {"num": num, "date": when.strftime("%Y-%m-%d"), "runs": runs}


# --- na żywo -----------------------------------------------------------------------------


def _radius(arguments, default, ceiling):
    return _count(arguments, "radius_m", default, ceiling, floor=50)


@tool(
    "vehicles",
    "Gdzie teraz są tramwaje i autobusy danej linii (pozycje na żywo z MPK, jak warstwa ◉ "
    "na stronie) - każdy pojazd z położeniem i najbliższym przystankiem.",
    {"line": {"type": "string", "description": "Numer linii, np. \"3\" albo \"N\"."}},
    required=("line",),
    live=True,
)
def vehicle_positions(arguments):
    wanted = _norm(_text(arguments, "line"))
    try:
        found = [v for v in vehicles.get_vehicles() if _norm(str(v.get("line", ""))) == wanted]
    except FileNotFoundError as e:
        raise ToolError(str(e)) from None
    except (OSError, ValueError) as e:
        raise ToolError(f"Nie udało się pobrać pozycji pojazdów: {e}") from None
    stops = gtfs.all_stops_geo()
    out = []
    for vehicle in found:
        near, metres = _nearest_stop(vehicle["lat"], vehicle["lon"], stops)
        out.append({"line": vehicle["line"], "kind": vehicle.get("kind"),
                    "lat": vehicle["lat"], "lon": vehicle["lon"],
                    "near_stop": near, "near_m": metres})
    return {"line": arguments["line"], "vehicles": out,
            **({} if out else {"note": "Żaden pojazd tej linii nie nadaje teraz pozycji."})}


@tool(
    "bikes_near",
    "Rower miejski WRM przy miejscu (jak warstwa 🚲 na stronie): stacje z liczbą rowerów "
    "(też elektrycznych) i wolnych miejsc oraz rowery stojące luzem, od najbliższych.",
    {"place": {"type": "string", "description": f"Gdzie: {PLACE}."},
     "radius_m": {"type": "integer", "description": "Promień w metrach (domyślnie 500, najwyżej 3000)."}},
    required=("place",),
    live=True,
)
def bikes_near(arguments):
    name, (lat, lon) = _locate(_text(arguments, "place"))
    radius = _radius(arguments, 500, 3000)
    try:
        stations, free = bikes.stations(), bikes.free_bikes()
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise ToolError(f"Nie udało się pobrać rowerów: {e}") from None

    def near(items):
        scored = [(round(bikes.haversine_m(lat, lon, i["lat"], i["lon"])), i) for i in items]
        return sorted(((m, i) for m, i in scored if m <= radius), key=lambda p: p[0])

    return {
        "place": name,
        "radius_m": radius,
        "stations": [{"name": s["name"], "distance_m": m, "bikes": s.get("bikes"),
                      "electric": s.get("electric"), "docks": s.get("docks"),
                      "renting": s.get("renting"), "returning": s.get("returning")}
                     for m, s in near(stations)[:10]],
        "free_bikes": [{"distance_m": m, "electric": b.get("electric"),
                        "near_stop": _nearest_stop(b["lat"], b["lon"], gtfs.all_stops_geo())[0]}
                       for m, b in near(free)[:10]],
    }


@tool(
    "cars_near",
    "Wolne auta Traficara przy miejscu (jak warstwa 🚗 na stronie): model, rejestracja, "
    "ulica, paliwo i zasięg, od najbliższych.",
    {"place": {"type": "string", "description": f"Gdzie: {PLACE}."},
     "radius_m": {"type": "integer", "description": "Promień w metrach (domyślnie 1000, najwyżej 5000)."},
     "vans": {"type": "boolean", "description": "Także dostawczaki (domyślnie nie)."}},
    required=("place",),
    live=True,
)
def cars_near(arguments):
    name, (lat, lon) = _locate(_text(arguments, "place"))
    radius = _radius(arguments, 1000, 5000)
    vans = _flag(arguments, "vans", False)
    try:
        cars = traficar.car_list()
    except traficar.TraficarDataError as e:
        raise ToolError(f"Nie udało się pobrać aut: {e}") from None
    scored = sorted(
        ((round(bikes.haversine_m(lat, lon, c["lat"], c["lon"])), c) for c in cars
         if vans or not c.get("van")),
        key=lambda p: p[0])
    return {
        "place": name,
        "radius_m": radius,
        "cars": [{"distance_m": m, "model": c.get("model"), "plate": c.get("plate"),
                  "van": bool(c.get("van")), "where": c.get("where"),
                  "fuel": c.get("fuel"), "range_km": c.get("range")}
                 for m, c in scored if m <= radius][:10],
    }
