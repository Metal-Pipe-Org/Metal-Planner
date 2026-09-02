"""Rozkład kolejowy (PKP PLK OpenData, pdp-api.plk-sa.pl) - czyta lokalną
bazę data/pkp.sqlite, budowaną i odświeżaną przez update_pkp.py. ŻADNA
funkcja tutaj nie łączy się z siecią: z bazą zbudowaną raz na dobę
wyszukiwanie użytkownika kosztuje jedno doklejenie do rozkładu MPK już
leżącego w pamięci (patrz augment_day), a nie zapytanie do zewnętrznego API.

JEDEN SILNIK, NIE DWA. `augment_day()` dokleja połączenia kolejowe wprost do
`gtfs.DayData` (patrz gtfs.load_day) - do TEJ SAMEJ tablicy `conns`, którą
skanuje Connection Scan w planner.py. Stacja PKP jest więc dla CSA zwykłym
przystankiem, a przesiadka między pociągiem a tramwajem/autobusem - zwykłą
przesiadką (przez mechanizm siblings, patrz niżej). `routes.py` nie ma żadnej
specjalnej gałęzi dla PKP: `/api/flow` woła `plan_flow` dokładnie tak samo,
jak przed tym plikiem - PKP i MPK są dla wyszukiwarki tym samym.

(Wcześniejsza wersja tego modułu robiła to inaczej: osobne zapytania SQL
o połączenia BEZPOŚREDNIE, sklejane z wynikiem MPK w routes.py specjalnymi
gałęziami (rail_only, "stacja-brama"). Zastąpione doklejeniem do wspólnej
tablicy połączeń - jeden skan widzi obie sieci naraz, w tym przesiadki
MIĘDZY pociągami, których tamta wersja w ogóle nie widziała.)

STACJA PKP dostaje syntetyczny stop_id "PKP:<id>" (prefiks jak
siechnice.ID_PREFIX - odróżnia go od identyfikatorów GTFS). Bez ustalonych
współrzędnych (geokodowanie w toku albo nie znalazło - patrz
update_pkp.geocode_missing_stations) stacja jest CAŁKOWICIE pomijana: reszta
systemu zakłada, że każdy stop_id ma współrzędne (rysowanie, szukanie
najbliższych przystanków), więc dodanie jej bez nich zepsułoby więcej, niż
dałoby. Stacje pośrednie kursu bez współrzędnych są pomijane w SEKWENCJI
(pociąg "przeskakuje" przez nie w naszym grafie połączeń) - realny rozkład
się nie zmienia, zmienia się tylko to, co potrafimy pokazać.

PRZESIADKA stacja PKP <-> przystanek MPK nie ma tu ŻADNEGO własnego
mechanizmu (2026-08-31). Stacja jest dokładana do dnia PRZED budowaniem
miejsc (gtfs.load_day), więc przechodzi przez to samo grupowanie co każdy
słupek miejski: ta sama nazwa = to samo miejsce = przejście pieszo między
nimi. Wcześniej było inaczej - stacja dostawała sąsiadów z własnego promienia
500 m, obok mechanizmu miejsca - i to był drugi mechanizm odpowiadający na to
samo pytanie. Skutek uboczny usunięcia jest znany i zamierzony: nazwy stacji
i przystanków prawie nigdy się nie pokrywają ("Wrocław Główny" vs "DWORZEC
GŁÓWNY"), więc dziś obie sieci stykają się w pojedynczych punktach. Porządne
łączenie stacji z przystankami to osobne zadanie, nie ten plik.

CZAS jest ucinany do pełnych minut (patrz _sec_of): API kolei podaje sekundy,
rozkład miejski nie, a jedna oś czasu nie może mieć dwóch dokładności - inaczej
"11:24:42" wygrywa z "11:25" o czterdzieści dwie sekundy, których pasażer
nigdzie nie zobaczy. Zaokrąglenie jest OSTROŻNE, nie najbliższe: odjazd w dół,
przyjazd w górę - plan może być pesymistyczny co do sekund, nigdy optymistyczny.

WŁĄCZANIE. Jak przy PKP_API_KEY (patrz config.py): brak klucza wyłącza tę
funkcję po cichu (i update_pkp.py w ogóle nie buduje bazy), tak samo jak
Siechnice bez SIECHNICE_ENABLED. Brak samej bazy (klucz jest, ale
update_pkp.py jeszcze nie zdążył jej zbudować) też nie jest błędem -
augment_day() wtedy po prostu nic nie dokleja.
"""

import json
import math
import sqlite3
from pathlib import Path

import config

DB_PATH = Path(__file__).resolve().parent / "data" / "pkp.sqlite"
COORDS_PATH = Path(__file__).resolve().parent / "data" / "pkp_station_coords.json"

_DIACRITIC_MAP = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
})

# Klucz cache'a to mtime pliku bazy - ten sam trik co gtfs._day_cache: po
# nocnej podmianie (update_pkp.py) dane przeładują się same, bez restartu
# procesu, i trzymamy naraz tylko jedną (aktualną) wersję.
_stations_cache = {}


def enabled():
    """Czy skonfigurowano klucz PKP - brak klucza wyłącza funkcję po cichu."""
    return config.pkp_api_key() is not None


def _strip_diacritics(casefolded):
    return casefolded.translate(_DIACRITIC_MAP)


def _connect():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA query_only = 1")
    return db


def _stations():
    """id -> nazwa stacji, cache'owane w pamięci (patrz nagłówek modułu).
    {} gdy bazy jeszcze nie ma - to nie błąd, tylko "jeszcze nie pobrano"."""
    if not DB_PATH.exists():
        return {}
    mtime = DB_PATH.stat().st_mtime
    cached = _stations_cache.get(mtime)
    if cached is not None:
        return cached

    db = _connect()
    try:
        by_id = dict(db.execute("SELECT station_id, name FROM stations"))
    finally:
        db.close()
    _stations_cache.clear()
    _stations_cache[mtime] = by_id
    return by_id


def _is_city_wildcard(name):
    """Czy `name` to wpis "dowolna stacja w mieście X" w słowniku stacji
    PKP (np. "WARSZAWA -") - ta sama reguła co
    update_pkp._is_city_wildcard, celowo zduplikowana tutaj: pkp.py to
    warstwa ODCZYTU przy każdym zapytaniu, update_pkp.py to warstwa
    AKTUALIZACJI danych (pobieranie/geokodowanie) - nie mają się nawzajem
    importować, żeby odczyt nie ciągnął za sobą całego pipeline'u
    aktualizacji. Taki wpis nigdy nie ma WŁASNYCH współrzędnych (patrz
    all_station_names niżej, dlaczego to tu w ogóle ma znaczenie) - zamiast
    tego dopasowuje się specjalnie, przez gtfs._match_city_group, do
    WSZYSTKICH prawdziwych stacji tego miasta na raz."""
    stripped = name.rstrip()
    return bool(stripped) and stripped.endswith("-") and stripped == stripped.upper()


def all_station_names():
    """Posortowane nazwy stacji PKP - do tej samej listy podpowiedzi
    w formularzu, co przystanki MPK (patrz gtfs.all_stop_names, routes.py).
    Wyszukiwanie samo (gtfs.match_stop) zna te nazwy już z augment_day -
    to tylko lista do podpowiedzi, zanim użytkownik cokolwiek wpisze.

    TYLKO stacje z ustalonymi współrzędnymi (patrz _coords) - stacja bez
    nich nigdy nie trafia do grafu połączeń (augment_day pomija ją przy
    budowaniu dnia - patrz jej nagłówek), więc pokazanie jej w podpowiedziach
    byłoby mylące: użytkownik wybiera nazwę z listy, a wyszukiwarka i tak
    odpowiada "nie znaleziono" - dokładnie to zgłoszone przez użytkownika
    na żywo. Nazwa, pod którą kryje się KILKA stacji (różne station_id,
    ta sama nazwa) zostaje na liście, jeśli choć JEDNA z nich ma
    współrzędne - reszta bez nich i tak nie wejdzie do grafu, ale to nie
    powód, by chować nazwę w całości.

    WYJĄTEK od reguły "tylko z współrzędnymi": "zbiorcze" stacje typu
    "WARSZAWA -" (patrz _is_city_wildcard) - te NIGDY nie mają własnych
    współrzędnych (nie są jednym miejscem), więc reguła wyżej zawsze by je
    ukryła, mimo że są w pełni szukalne (patrz gtfs._match_city_group,
    dopasowuje się do nich specjalnie, nie przez zwykły graf połączeń) -
    zgłoszone przez użytkownika na żywo: bez tego wyjątku grupy stacji nie
    pokazywały się w podpowiedziach wyszukiwarki, mimo że samo wyszukiwanie
    już działa poprawnie.

    DRUGI WYJĄTEK: syntetyczne etykiety "Miasto -" dopisane tu dla KAŻDEGO
    miasta z więcej niż jedną szukalną stacją (patrz _city_group_labels) -
    PKP w swoim słowniku zdefiniowało dosłownie takie wpisy tylko dla 17
    miast (patrz _is_city_wildcard wyżej), ale gtfs._match_city_group
    dopasowuje się do KAŻDEGO miasta z wieloma stacjami, nie tylko tych 17 -
    zgłoszone przez użytkownika na żywo (Wrocław, dziesiątki prawdziwych
    stacji, ale bez własnego wpisu w słowniku PKP, więc bez tego wyjątku
    w ogóle nie pokazywał się jako grupa w podpowiedziach)."""
    coords = _coords()
    stations = _stations()
    named = {name for station_id, name in stations.items() if station_id in coords}
    coords_by_name = {}
    for station_id, name in stations.items():
        if station_id in coords:
            coords_by_name.setdefault(name, coords[station_id])
    literal_wildcards = {name for name in stations.values() if _is_city_wildcard(name)}
    names = named | literal_wildcards | _city_group_labels(named, coords_by_name, literal_wildcards)
    return sorted(names)


MIN_CITY_GROUP_STATIONS = 5   # patrz _city_group_labels - próg dobrany na wyraźną prośbę użytkownika
# Promień od środka ciężkości grupy, w którym MUSZĄ zmieścić się WSZYSTKIE
# jej stacje - patrz _city_group_labels, dlaczego to konieczne, nie tylko
# ładne. Duże miasto (np. Warszawa) ma stacje rozrzucone na kilkanaście km,
# więc próg musi to obejmować - ale dwa RÓŻNE miasta dzielące pierwszy człon
# nazwy (patrz niżej) są zwykle dziesiątki/setki km od siebie, więc żaden
# rozsądny próg miejskiego rozrzutu ich nie złapie.
CITY_GROUP_MAX_RADIUS_KM = 20


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _city_group_labels(station_names, coords_by_name, literal_wildcards):
    """Etykiety "MIASTO -" (WIELKIMI LITERAMI, tak samo jak dosłowne wpisy
    PKP - patrz _is_city_wildcard, na wyraźną prośbę użytkownika) do
    podpowiedzi wyszukiwarki, dla każdego miasta z co najmniej
    MIN_CITY_GROUP_STATIONS prawdziwymi, szukalnymi stacjami PKP
    (`station_names`, patrz all_station_names, dlaczego to w ogóle
    potrzebne obok literalnych wpisów PKP z `literal_wildcards`). Miasto
    rozpoznajemy jako PIERWSZY człon nazwy stacji - ten sam podział, po
    którym faktycznie dopasowuje gtfs._match_city_group ("Wrocław
    Główny"/"Wrocław Psie Pole" -> "Wrocław", dopasowanie tam jest
    bezwiednie na wielkość liter - patrz jego nagłówek, więc "WROCŁAW -"
    zamiast "Wrocław -" tu nie psuje samego wyszukiwania, to czysto
    kosmetyczna zmiana etykiety) - więc etykieta wygenerowana tu zawsze da
    się z powrotem poprawnie dopasować.

    PUŁAPKA sprawdzona na żywo, zgłoszona przez użytkownika: sam pierwszy
    człon nazwy NIE wystarcza jako "miasto" - polskie nazwy miejscowości
    często mają POSPOLITE pierwsze słowo dzielone przez WIELE różnych,
    odległych o dziesiątki/setki km miejscowości ("Nowy Sącz" i "Nowy
    Targ" - obie realne miasta, ~50 km od siebie, żadnym sposobem nie "ta
    sama stacja w jednym mieście"; podobnie "Stare"/"Stary"/"Wola"/
    "Wólka"/"Dąbrowa"/"Grodzisk" - to nie nazwy miast, tylko pospolite
    przedrostki nazw WIELU różnych miejscowości). Bez sprawdzenia
    geograficznego taka etykieta grupowałaby zupełnie NIEZWIĄZANE miasta
    w jedną "opcję" wyszukiwania - CSA wybrałby MIĘDZY NIMI jak gdyby były
    zamiennikami, co dawałoby PEWNĄ, ale BŁĘDNĄ trasę zamiast "nie
    znaleziono" (dokładnie ten rodzaj błędu, którego cała reszta tego
    pliku unika - patrz np. NAME_OVERRIDES i historia w docs/PROJECT.md).
    Stąd dodatkowy warunek: wszystkie stacje kandydata muszą zmieścić się
    w promieniu CITY_GROUP_MAX_RADIUS_KM od ŚRODKA CIĘŻKOŚCI grupy -
    prawdziwe miasto (nawet duże, patrz stała) przechodzi, dwie różne
    miejscowości o zbieżnym przedrostku - nie.

    Pomija miasta, które mają już DOSŁOWNY wpis PKP (`literal_wildcards`) -
    dwie prawie identyczne podpowiedzi obok siebie tylko myliłyby, skoro
    obie i tak dopasowują się identycznie."""
    literal_cities = {
        name.rstrip().rstrip("-").strip().casefold() for name in literal_wildcards
    }
    by_city = {}
    for name in station_names:
        city, sep, rest = name.partition(" ")
        if not sep or not rest:
            continue
        by_city.setdefault(city, set()).add(name)

    labels = set()
    for city, matched in by_city.items():
        if len(matched) < MIN_CITY_GROUP_STATIONS or city.casefold() in literal_cities:
            continue
        points = [coords_by_name[name] for name in matched if name in coords_by_name]
        if not points:
            continue
        avg_lat = sum(lat for lat, lon in points) / len(points)
        avg_lon = sum(lon for lat, lon in points) / len(points)
        if all(
            _haversine_km(avg_lat, avg_lon, lat, lon) <= CITY_GROUP_MAX_RADIUS_KM
            for lat, lon in points
        ):
            labels.add(f"{city.upper()} -")
    return labels


def _coords():
    if not COORDS_PATH.exists():
        return {}
    try:
        raw = json.loads(COORDS_PATH.read_text(encoding="utf-8"))
        return {int(k): tuple(v) for k, v in raw.items()}
    except (OSError, ValueError, TypeError):
        return {}


def all_stations_geo():
    """Stacje PKP, dla których geokodowanie (patrz nagłówek modułu) znalazło
    współrzędne - do markerów na mapie, tak jak gtfs.all_stops_geo(). Stacja
    bez współrzędnych po prostu nie ma tu wpisu."""
    coords = _coords()
    return [
        {"name": name, "lat": coords[station_id][0], "lon": coords[station_id][1]}
        for station_id, name in _stations().items()
        if station_id in coords
    ]


def station_coords(station_id):
    """(lat, lon) jednej stacji, jeśli geokodowanie ją znalazło - albo None."""
    return _coords().get(station_id)


def _sec_of(hms, round_up=False):
    """Godzina "HH:MM:SS" na sekundy, uciętE do pełnej minuty - patrz nagłówek.

    round_up rozstrzyga stronę: przyjazd w górę, odjazd w dół, żeby ucięcie
    nigdy nie obiecało pasażerowi sekund, których nie ma.
    """
    h, m, s = hms.split(":")
    sec = int(h) * 3600 + int(m) * 60 + int(s)
    minuta = sec // 60 * 60
    return minuta + 60 if round_up and sec != minuta else minuta


def augment_day(day, date):
    """Dokleja połączenia kolejowe do już zbudowanego dnia MPK - patrz
    nagłówek modułu. Wołane raz na dzień, z gtfs.load_day(), więc jest tak
    samo cache'owane jak reszta rozkładu. Bez klucza/bazy/współrzędnych po
    prostu nic nie robi - `day` zostaje czystym rozkładem MPK."""
    if not enabled() or not DB_PATH.exists():
        return

    coords = _coords()
    if not coords:
        return
    stations = _stations()
    date_iso = date.isoformat()

    db = _connect()
    try:
        rows = db.execute(
            """
            SELECT r.name, r.carrier_code, r.national_number, r.category,
                   s.schedule_id, s.order_id, s.station_id, s.order_number,
                   s.arrival_time, s.departure_time
            FROM stops s
            JOIN routes r ON r.schedule_id = s.schedule_id AND r.order_id = s.order_id
            JOIN operating_dates d
              ON d.schedule_id = s.schedule_id AND d.order_id = s.order_id
             AND d.date = ?
            ORDER BY s.schedule_id, s.order_id, s.order_number
            """,
            (date_iso,),
        ).fetchall()
    finally:
        db.close()
    if not rows:
        return

    used_ids = {row[6] for row in rows if row[6] in coords}
    if not used_ids:
        return

    stop_of = {}
    for station_id in used_ids:
        stop_id = f"PKP:{station_id}"
        stop_of[station_id] = stop_id
        name = stations.get(station_id, f"Stacja {station_id}")
        lat, lon = coords[station_id]
        day.stop_names[stop_id] = name
        day.stop_coords[stop_id] = (lat, lon)
        day.pkp_stations.append((name, stop_id))
        name_key = name.casefold()
        day.stops_by_key.setdefault(name_key, []).append(stop_id)
        day.display_name.setdefault(name_key, name)
        norm_key = _strip_diacritics(name_key)
        day.stops_by_norm_key.setdefault(norm_key, []).append(stop_id)
        day.norm_display_name.setdefault(norm_key, name)

    # Jedno przejście po wierszach (posortowanych SQL-em wg schedule_id,
    # order_id, order_number) buduje i połączenia (day.conns), i sekwencję
    # przystanków do rysowania (day.pkp_trip_stops) - jeden spójny czas dla
    # obu, patrz komentarz przy day_shift niżej.
    added = 0
    prev_key = None
    trip_id = None
    day_shift = 0        # narastające +24h przy przejściu kursu przez północ
    prev_corrected = None
    prev_stop = None
    prev_dep_c = None
    for row in rows:
        (name, carrier_code, national_number, category, schedule_id, order_id,
         station_id, order_number, arrival_time, departure_time) = row
        key = (schedule_id, order_id)
        if key != prev_key:
            prev_key = key
            trip_id = f"PKP:{schedule_id}:{order_id}"
            day_shift = 0
            prev_corrected = None
            prev_stop = None
            prev_dep_c = None
            number_digits = "".join(c for c in national_number or "" if c.isdigit())
            label = f"{carrier_code or ''} {number_digits}".strip()
            day.trip_info[trip_id] = (
                f"Pociąg {label}" if label else "Pociąg", name or "",
            )

        raw_arr = _sec_of(arrival_time, round_up=True) if arrival_time else None
        raw_dep = _sec_of(departure_time) if departure_time else None
        # Postój krótszy niż minuta znika po ucięciu i odjazd potrafi wypaść
        # PRZED przyjazdem na tę samą stację (11:21:42 -> 11:22 przyjazdu,
        # 11:21:48 -> 11:21 odjazdu). Nietknięte, cofnięcie czasu zostałoby
        # niżej wzięte za przejście przez północ i dodałoby całą dobę.
        if raw_arr is not None and raw_dep is not None and raw_dep < raw_arr:
            raw_dep = raw_arr

        # Skorygowany czas rośnie monotonicznie wzdłuż CAŁEGO kursu - jeśli
        # surowy czas (0-86399, API PKP nie zna godzin >23:59 jak GTFS) spadł
        # względem poprzedniego, to przejście przez północ: przesunięcie
        # +24h zostaje już do końca tego kursu (ten sam pomysł co
        # gtfs.PREV_DAY_SEC, tylko liczony w locie zamiast z pliku).
        if raw_arr is not None:
            if prev_corrected is not None and raw_arr + day_shift < prev_corrected:
                day_shift += 24 * 3600
            arr_c = raw_arr + day_shift
            prev_corrected = arr_c
        else:
            arr_c = None
        if raw_dep is not None:
            if prev_corrected is not None and raw_dep + day_shift < prev_corrected:
                day_shift += 24 * 3600
            dep_c = raw_dep + day_shift
            prev_corrected = dep_c
        else:
            dep_c = None

        stop_id = stop_of.get(station_id)
        if stop_id is None:
            continue   # stacja bez współrzędnych - pociąg "przeskakuje" przez nią

        if prev_stop is not None and prev_dep_c is not None and arr_c is not None:
            day.conns.append((prev_dep_c, arr_c, prev_stop, stop_id, trip_id))
            added += 1
        day.pkp_trip_stops.setdefault(trip_id, []).append((
            stop_id,
            arr_c if arr_c is not None else dep_c,
            dep_c if dep_c is not None else arr_c,
        ))
        if dep_c is not None:
            prev_stop, prev_dep_c = stop_id, dep_c
        else:
            prev_stop = None   # koniec trasy (sama arrival) - nie da się jechać dalej

    # Ani sortowania, ani własnego mostu przesiadkowego: kursy dokładają się
    # do tablicy PRZED kursami miejskimi, a gtfs.load_day sortuje ją raz, gdy
    # są już w niej obie sieci. Przesiadka stacja <-> przystanek bierze się
    # wyłącznie z tego, że stacja przechodzi przez to samo budowanie miejsc
    # co przystanek (ta sama nazwa = to samo miejsce).


def trip_path(day, trip_id, board_stop, board_dep, exit_stop, exit_arr):
    """Odpowiednik gtfs.trip_path dla kursów kolejowych (prefiks "PKP:") -
    sekwencja przystanków zapisana przy budowie dnia (patrz augment_day,
    day.pkp_trip_stops), bez drugiego zapytania do żadnej bazy."""
    if day is None:
        return []
    rows = day.pkp_trip_stops.get(trip_id, ())
    start_i = None
    for i, (stop_id, arrival_sec, departure_sec) in enumerate(rows):
        if start_i is None:
            if stop_id == board_stop and departure_sec == board_dep:
                start_i = i
        elif stop_id == exit_stop and arrival_sec == exit_arr:
            return rows[start_i:i + 1]
    return []
