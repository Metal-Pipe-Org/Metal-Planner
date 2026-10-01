"""Numer boczny pojazdu -> dniówka, którą ten pojazd dziś jedzie.

Czterocyfrowa liczba wymalowana na burcie to JEDNA rzecz do wpisania zamiast
trzech (linia, kierunek, najbliższy przystanek) - a odpowiada na to samo
pytanie: którym właściwie kursem jedzie pytający. Ten moduł dostarcza pierwsze
ogniwo tego łańcucha: z numeru bocznego robi BRYGADĘ, czyli numer dniówki.
Drugie ogniwo - z brygady na konkretny kurs rozkładu - leży w onboard.py, bo
to już czytanie rozkładu, a nie żywego kanału.

Skąd to w ogóle brać. Kanał, z którego mapa bierze pozycje pojazdów
(mpk.wroc.pl/bus_position, patrz vehicles.py), numeru bocznego NIE MA wcale -
oddaje numer linii, rodzaj, pozycję i wewnętrzny klucz, który numerem bocznym
nie jest. Ma go dopiero drugi, miejski kanał (zestaw 14 na
api.open-data.cui.wroclaw.pl): przy pojeździe stoją `Nr_Boczny` i `Brygada`.
Za to odświeża się co równo dwadzieścia minut - i właśnie dlatego nie zastąpił
tamtego przy rysowaniu pozycji. Tutaj wolne tempo nie przeszkadza: dniówka
jest przypisana wozowi na cały dzień, a nie na kwadrans.

ŚWIEŻOŚĆ TRZEBA FILTROWAĆ, i to względem samej paczki. W odpowiedzi leży
prawie tysiąc rekordów, z których połowa to duchy - wozy stojące w zajezdni
z `Data_Aktualizacji` sprzed dwóch lat. Znacznika nie da się porównać
z zegarem naszego serwera (nie wiadomo, w jakiej strefie i z jakim
opóźnieniem powstaje), więc porównujemy z NAJŚWIEŻSZYM ZNACZNIKIEM W TEJ
SAMEJ PACZCE: wszystko starsze od niego o więcej niż FRESH_SEC to nie jest
wóz, który dziś jeździ.

Czego ten moduł NIE obiecuje (i co trzeba pokazać człowiekowi wprost):
co piąty jadący wóz nie podaje brygady wcale, a wóz, który wyjechał z
zajezdni po ostatniej paczce, jest niewidoczny nawet dwadzieścia minut.
Rozpoznanie od ręki wychodzi więc mniej więcej dwa razy na trzy - i dlatego
zejście do trzech pól (patrz onboard.py) nie jest awarią, tylko drugą
normalną drogą.
"""

import json
import threading
import time
import urllib.request
from datetime import date, datetime

RECORDS_URL = "https://api.open-data.cui.wroclaw.pl/od3-records/data/14/"

# Kanał odświeża się co równo 20 minut, więc znaczniki w jednej paczce
# rozjeżdżają się najwyżej o tyle. Wszystko starsze to zapis z poprzedniego
# kursowania tego wozu - albo sprzed dwóch lat.
FRESH_SEC = 30 * 60

# Tyle trzymamy paczkę, zanim spytamy o nową. Znacznie krócej niż jej własne
# dwadzieścia minut, bo między paczkami wjeżdżają nowe wozy - ale i tak na
# tyle długo, żeby seria pytań z jednej przeglądarki nie biła w cudze API.
CACHE_SEC = 120

# Sufit stron - paczka ma ich dziś dwie. Gdyby API kiedyś zaczęło stronicować
# po sztuce, nie chcemy zawiesić zapytania na kilkuset żądaniach.
MAX_PAGES = 10

_lock = threading.Lock()
_cache = {"at": 0.0, "vehicles": None}

# Pary "ten numer boczny = ta dniówka" wyczytane z tego, co ktoś sam wpisał
# ręcznie (patrz `remember`). Ważne WYŁĄCZNIE na dziś - jutro zajezdnia
# rozdaje wozy inaczej - więc klucz niesie datę, a nie sam numer.
_learned = {}


def _fetch_pages():
    """Wszystkie strony zestawu 14 jako jedna lista rekordów."""
    out = []
    url = RECORDS_URL
    for _ in range(MAX_PAGES):
        request = urllib.request.Request(
            url, headers={"User-Agent": "Metal-Planner/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            page = json.load(response)
        out += page.get("results") or []
        url = page.get("next")
        if not url:
            break
    return out


def _stamp(value):
    """'2026-09-20T15:40:11.287000' -> datetime, albo None przy śmieciach."""
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def split_brigade(brigade):
    """'01405' -> ('14', '5'): numer linii i numer brygady w niej.

    Zapis jest pozycyjny i stały: trzy cyfry linii, dwie cyfry brygady, obie
    dopełnione zerami z lewej. W paczce GTFS ta sama brygada leży już BEZ
    zer wiodących ('5', nie '05'), a numer linii tak samo ('14'), więc zera
    zdejmujemy tu, raz, zamiast dopasowywać je przy każdym zapytaniu.

    Zwraca None dla zapisu, który nie ma tego kształtu - np. linii
    literowej (A, C, D, K, N), której w trzech cyfrach nie da się zapisać.
    """
    brigade = (brigade or "").strip()
    if len(brigade) != 5 or not brigade.isdigit():
        return None
    return brigade[:3].lstrip("0"), brigade[3:].lstrip("0")


def _parse(records):
    """Świeże rekordy paczki jako {numer boczny: {"num", "brigade"}}.

    Świeżość liczona względem najświeższego znacznika W TEJ SAMEJ paczce
    (patrz docstring modułu). Wóz bez brygady w ogóle tu nie wchodzi:
    sam numer boczny bez dniówki nie prowadzi do żadnego kursu, a wpis
    "znam ten wóz, ale nic o nim nie wiem" byłby gorszy niż jego brak -
    kazałby pytać o linię, kierunek i przystanek dopiero po sekundzie zwłoki.
    """
    stamps = [_stamp(r.get("data", {}).get("Data_Aktualizacji")) for r in records]
    newest = max((s for s in stamps if s), default=None)
    if newest is None:
        return {}

    out = {}
    for record, stamp in zip(records, stamps):
        if stamp is None or (newest - stamp).total_seconds() > FRESH_SEC:
            continue
        data = record.get("data") or {}
        side = str(data.get("Nr_Boczny") or "").strip()
        parts = split_brigade(data.get("Brygada"))
        if not side or side in ("0", "-1") or parts is None:
            continue
        num, brigade = parts
        out[side] = {"num": num, "brigade": brigade}
    return out


def vehicles():
    """Świeże wozy paczki jako {numer boczny: {"num", "brigade"}}, z cache.

    Rzuca wyjątkiem sieciowym (OSError) albo ValueError przy niesparsowanej
    odpowiedzi - kto woła, zamienia to na odpowiedź błędu (patrz routes.py).
    """
    with _lock:
        fresh = (_cache["vehicles"] is not None
                 and time.monotonic() - _cache["at"] < CACHE_SEC)
        if not fresh:
            _cache["vehicles"] = _parse(_fetch_pages())
            _cache["at"] = time.monotonic()
        return _cache["vehicles"]


def brigade_of(side, today=None):
    """Dniówka tego wozu - {"num", "brigade", "learned"} albo None.

    Kanał operatora bije to, czego sami się domyśliliśmy: para nauczona
    (patrz `remember`) jest tylko zapisem jednego cudzego wyboru sprzed
    chwili, a paczka - tym, co miasto o tym wozie mówi teraz. Dlatego
    nauczone sprawdzamy dopiero wtedy, gdy kanał milczy.

    Milczenie kanału (sieć nie odpowiada) nie może skasować tego, czego się
    nauczyliśmy: pytanie "czym ja jadę" nie przestaje mieć odpowiedzi
    dlatego, że akurat padło cudze API.
    """
    side = str(side or "").strip()
    if not side:
        return None
    try:
        found = vehicles().get(side)
    except (OSError, ValueError):
        found = None
    if found:
        return {**found, "learned": False}
    learned = _learned.get(((today or date.today()).isoformat(), side))
    return {**learned, "learned": True} if learned else None


def remember(side, num, brigade, today=None):
    """Zapamiętuje parę "ten wóz jedzie tę dniówkę" na DZISIAJ.

    Wywoływane, gdy ktoś wpisał numer boczny, kanał go nie znał, więc zszedł
    do trzech pól - a z nich wyszedł konkretny kurs, czyli i jego brygada.
    Następny pytający o ten sam wóz dostaje odpowiedź od razu.

    Tylko na dziś, bo dniówka jest własnością dnia, nie pojazdu: jutro ten
    sam wóz obsadzi inną. Stare dni kasujemy przy okazji zapisu - słownik ma
    najwyżej tyle wpisów, ile wozów wyjechało dzisiaj na miasto.
    """
    side = str(side or "").strip()
    if not (side and num and brigade):
        return
    stamp = (today or date.today()).isoformat()
    for key in [k for k in _learned if k[0] != stamp]:
        del _learned[key]
    _learned[(stamp, side)] = {"num": num, "brigade": brigade}
