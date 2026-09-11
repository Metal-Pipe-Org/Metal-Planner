"""Traficar (car-sharing) jako OSTATNI etap podróży - dane i szukanie auta.

Traficar (marka rozpoznawalna po fioletowym malowaniu aut) sam nie publikuje
żadnego oficjalnego API. `fioletowe.live` (open source, GitHub
`divadsn/traficar-map`, licencja GPLv3) republikuje jego wewnętrzne API jako
udokumentowany REST/JSON bez klucza (`/docs/`, `/api/openapi.json`) - to jest
STRONA TRZECIA, nie sam Traficar, więc może zniknąć albo zmienić kształt bez
ostrzeżenia. Stąd dwie rzeczy: wyłącznik (patrz `enabled`) i to, że KAŻDY
błąd tego feedu jest po prostu brakiem propozycji z autem, nigdy błędem
wyszukiwania połączenia.

Wrocław to zoneId=3, potwierdzone przez `GET /api/v1/zones`.

Do czego to służy: komunikacja dowozi w okolicę celu, a ostatni kawałek -
ten, na który nie ma już sensownej linii - jedzie się wynajętym autem.
Propozycja składa się więc z czterech kawałków po kolei:

    przejazd(y) komunikacją  ->  dojście z przystanku do auta
                             ->  START_SEC na odpalenie
                             ->  jazda autem do celu

`car_options` szuka tylko TEGO ostatniego ogona: dostaje gotową odpowiedź
"dokąd i o której dowozi komunikacja" i zwraca najlepsze pary
(przystanek wysiadania, auto). Samych etapów nie składa - kształt etapu trasy
jest wiedzą plannera i tam zostaje (patrz planner._traficar_journeys).

Czasu jazdy autem NIE MA SKĄD odczytać: auto nie ma rozkładu, a routingu
samochodowego w projekcie nie ma. Jest więc szacowany z odległości w linii
prostej (patrz DRIVE_*) i wszędzie podpisany jako "ok." - tak samo jak czas
dojścia pieszego, który od zawsze jest szacunkiem. To jedyne miejsce
w projekcie, gdzie czas bierze się z prędkości, a nie z rozkładu, i dlatego
nie dotyka mapy przepływów: mapa dalej rysuje wyłącznie kursy z rozkładu
(punkt 10 kontraktu), a auto pojawia się tylko jako dodatkowa pozycja na
liście propozycji.
"""

import json
import os
import time
import urllib.request

import gtfs

ZONE_ID = 3          # Wrocław (GET /api/v1/zones)
API = "https://fioletowe.live/api/v1"
CARS_TTL_SEC = 20    # feed sam deklaruje Cache-Control: max-age=12 - nie odpytujemy częściej
MODELS_TTL_SEC = 6 * 3600   # lista modeli zmienia się w skali miesięcy, nie minut

# Dojście z przystanku do auta. Promień większy niż rozpiętość jednego
# miejsca (gtfs.PLACE_MAX_SPAN_M, 400 m): tam chodzi o przejście między
# peronami tego samego węzła, a tu o konkretne, jedno auto stojące gdzieś
# w okolicy - po nie idzie się dalej niż na sąsiedni peron, ale nie przez
# pół dzielnicy.
WALK_TO_CAR_M = 600
WALK_SPEED_MPS = 1.3        # ~4,7 km/h - tyle, ile daje planner.WALK_SEC na 400 m
WALK_MIN_SEC = 60

# Odpalenie auta: rezerwacja w aplikacji, dojście dookoła, przegląd,
# odjazd z miejsca postojowego. Pięć minut, w których nie jedzie się nigdzie.
START_SEC = 300

# Jazda autem - szacunek, patrz nagłówek modułu. Obie stałe są ZMIERZONE, nie
# wzięte z głowy: 30 losowych par przystanków w oknie [MIN_DRIVE_M,
# MAX_DRIVE_M] przepuszczonych przez prawdziwy routing samochodowy (OSRM na
# danych OSM, jednorazowo, poza aplikacją - w runtime niczego takiego nie
# wołamy). Z tej próbki mediana krętości (droga / linia prosta) to 1,30,
# a mediana prędkości po drodze 36 km/h.
#
# Bierzemy 34 km/h, nie zmierzone 36: profil samochodowy OSRM liczy jazdę
# swobodną, bez korków, a plan ma bywać pesymistyczny, nigdy optymistyczny
# (punkt 12 kontraktu). Przy tych stałych model myli się co do czasu o 19%
# (mediana), 34% (90. centyl), w zakresie -23%..+54%, i zawyża w 21 z 30
# przypadków - czyli najczęściej w bezpieczną stronę. Co do dystansu: 10%
# (mediana), 22% (90. centyl).
#
# Dla porównania stałe sprzed pomiaru (1,35 i 27 km/h) myliły się co do czasu
# o 47% (mediana) i zawyżały w 30 z 30 przypadków, dwukrotnie w skrajnym.
#
# Ten rząd wielkości błędu jest powodem, dla którego dystans podajemy
# w pełnych kilometrach i z "ok." (patrz planner._car_drive_leg): przy 22%
# rozrzutu "7,7 km" udawałoby dokładność, której tu nie ma.
DRIVE_SPEED_MPS = 9.4
DRIVE_DETOUR = 1.30
DRIVE_MIN_SEC = 120
# Poniżej tego auto nie ma czego załatwić: samo odpalenie (START_SEC) trwa
# dłużej niż przejście tego kawałka, a opłata za przejazd zostaje.
MIN_DRIVE_M = 1500
# Powyżej - to już nie jest "ostatni kawałek podróży", tylko cała podróż
# autem; na taką odpowiedź nikt nie pytał wyszukiwarki komunikacji miejskiej.
MAX_DRIVE_M = 25000
# Zapas zasięgu ponad sam przejazd. Feed podaje zasięg w km i bywa on niski
# (auto z 9% paliwa ma ich czterdzieści) - a nasz dystans jest SZACOWANY
# (linia prosta razy krętość), więc auto, które dojeżdża "na styk", nie jest
# propozycją, tylko zaproszeniem na stację po drodze.
RANGE_RESERVE_M = 5000

_cars_cache = {"at": 0.0, "cars": [], "generation": 0}
_models_cache = {"at": 0.0, "models": {}}


class TraficarDataError(Exception):
    """API fioletowe.live niedostępne, a w cache'u nie ma jeszcze żadnych danych."""


def enabled():
    """Wyłącznik feedu - `TRAFICAR=0` gasi propozycje z autem bez zmiany kodu.

    Domyślnie włączone. Jest, bo źródło jest cudze i niedokumentowane przez
    samego operatora (patrz nagłówek): gdy zacznie oddawać bzdury, ma być czym
    to odciąć na produkcji, zanim pojawi się poprawka.
    """
    return os.environ.get("TRAFICAR", "1").strip() != "0"


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Metal-Planner/0.1"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def _spot_label(location):
    """Gdzie stoi auto, w jednej linijce do pokazania obok trasy.

    Feed podaje adres razem z miastem ("Wrocław, ul. Łagiewnicka"), a cała
    aplikacja jest o Wrocławiu - miasto w każdym wierszu byłoby szumem. Poza
    Wrocławiem jest już informacją i zostaje.

    Pusty napis, gdy adresu nie ma wcale i zostaje samo miasto (tak bywa dla
    kilkunastu aut ze zmiany): lepszy brak niż "Wrocław", które nikomu nie
    pomoże znaleźć auta. Co z tym brakiem zrobić, rozstrzyga już wołający -
    dla dojścia to jedno zdanie mniej, dla wiersza na osi trasy podpis
    zastępczy (patrz planner._car_walk_leg/_car_drive_leg).
    """
    text = (location or "").strip()
    city, _, rest = text.partition(",")
    if rest.strip():
        return rest.strip() if city.strip().casefold() == "wrocław" else text
    return "" if not text or city.strip().casefold() == "wrocław" else text


def _models():
    """id modelu -> nazwa ("RENAULT Clio IV"). Pusty słownik, gdy się nie udało -
    nazwa modelu jest ozdobą dymka, nie powodem, żeby nie proponować auta."""
    if time.monotonic() - _models_cache["at"] >= MODELS_TTL_SEC:
        try:
            data = _fetch(f"{API}/car-models")
            _models_cache["models"] = {
                m["id"]: m["name"] for m in data["carModels"]
            }
            _models_cache["at"] = time.monotonic()
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return _models_cache["models"]


def car_list():
    """Wolne auta Traficar we Wrocławiu: [{lat, lon, plate, model, ...}, ...].

    `fuel` to procent paliwa (0-100), `range` zasięg w km, `plate` numer
    rejestracyjny, `where` opis miejsca postoju wprost z feedu ("Wrocław,
    ul. Łagiewnicka"). Feed oddaje wyłącznie auta wolne do wynajęcia, ale
    `available` i tak sprawdzamy - pole jest w odpowiedzi, więc poleganie na
    tym, że zawsze jest prawdziwe, byłoby zakładem o cudzy serwis.

    Błąd sieci przy pustym cache'u -> TraficarDataError; przy niepustym -
    stare dane zamiast wyjątku (auto "zestarzeje się" zamiast zniknąć).
    """
    if time.monotonic() - _cars_cache["at"] >= CARS_TTL_SEC:
        try:
            data = _fetch(f"{API}/cars?zoneId={ZONE_ID}")
            models = _models()
            _cars_cache["cars"] = [
                {
                    "lat": float(c["lat"]),
                    "lon": float(c["lng"]),
                    "plate": c["regPlate"],
                    "model": models.get(c.get("modelId"), "Traficar"),
                    "where": _spot_label(c.get("location")),
                    "fuel": round(float(c["fuel"])),
                    "range": c["range"],
                }
                for c in data["cars"] if c.get("available")
            ]
            _cars_cache["at"] = time.monotonic()
            _cars_cache["generation"] += 1
        except (OSError, ValueError, KeyError, TypeError) as e:
            if not _cars_cache["cars"]:
                raise TraficarDataError(
                    "Nie udało się pobrać danych Traficar z fioletowe.live"
                ) from e
    return _cars_cache["cars"]


def _full_minutes(sec):
    """Sekundy w górę do pełnej minuty.

    Cały projekt liczy w pełnych minutach i zaokrągla ostrożnie - odjazd
    w dół, przyjazd w górę (punkt 12 kontraktu). Szacunki dojścia i jazdy
    tym bardziej: gdyby zostały w sekundach, karta pokazywałaby "ok. 13 min"
    między godzinami odległymi o czternaście, bo jedno byłoby zaokrąglone,
    a drugie ucięte.
    """
    return -(-int(sec) // 60) * 60


def drive_time(from_lat, from_lon, to_lat, to_lon):
    """(sekundy, metry) jazdy autem - szacunek, patrz nagłówek modułu."""
    metres = gtfs._haversine_m(from_lat, from_lon, to_lat, to_lon) * DRIVE_DETOUR
    return max(DRIVE_MIN_SEC, _full_minutes(metres / DRIVE_SPEED_MPS)), round(metres)


def walk_time(metres):
    """Sekundy dojścia pieszego na podanym dystansie - ta sama miara, co przy
    przejściu między słupkami."""
    return max(WALK_MIN_SEC, _full_minutes(metres / WALK_SPEED_MPS))


def _in_box(lat, lon, clat, clon, metres):
    """Zgrubne "czy w kwadracie ~`metres`" - żeby nie liczyć haversine'a dla
    każdego z kilku tysięcy słupków razy każde z kilkudziesięciu aut."""
    dlat = metres / 111_320
    dlon = metres / 71_000     # 111 320 * cos(51,1°) - szerokość Wrocławia
    return abs(lat - clat) <= dlat and abs(lon - clon) <= dlon


def car_options(day, reachable, dest, limit=2):
    """Najlepsze zakończenia podróży autem: [{stop, car, walk_sec, ...}, ...].

    `reachable` to {stop_id: godzina, o której komunikacja tu dowozi} -
    wyłącznie słupki, na które da się DOJECHAĆ (start się nie liczy:
    propozycja "dojdź do auta i jedź" nie jest trasą komunikacją miejską).
    `dest` to (lat, lon) celu.

    Wynik jest posortowany po godzinie dotarcia do celu i odsiany tak, żeby
    nie były to warianty tego samego: jedno miejsce wysiadania i jedno auto
    występują w nim najwyżej raz. Bez tego "różne propozycje" byłyby trzema
    autami z tego samego parkingu albo trzema słupkami tego samego placu.

    Pusta lista, gdy feed nie działa albo nic sensownego nie wychodzi -
    brak auta nigdy nie jest błędem wyszukiwania.
    """
    if not enabled() or not reachable:
        return []
    try:
        cars = car_list()
    except TraficarDataError:
        return []

    dest_lat, dest_lon = dest
    candidates = []
    for car in cars:
        drive_sec, drive_m = drive_time(car["lat"], car["lon"], dest_lat, dest_lon)
        if not MIN_DRIVE_M <= drive_m <= MAX_DRIVE_M:
            continue
        if (car["range"] or 0) * 1000 < drive_m + RANGE_RESERVE_M:
            continue
        for stop, arrival in reachable.items():
            coords = day.stop_coords.get(stop)
            if coords is None or not _in_box(*coords, car["lat"], car["lon"],
                                             WALK_TO_CAR_M):
                continue
            walk_m = gtfs._haversine_m(*coords, car["lat"], car["lon"])
            if walk_m > WALK_TO_CAR_M:
                continue
            walk_sec = walk_time(walk_m)
            candidates.append({
                "stop": stop,
                "car": car,
                "walk_sec": walk_sec,
                "walk_m": round(walk_m),
                "drive_sec": drive_sec,
                "drive_m": drive_m,
                "start_sec": START_SEC,
                "arrival": arrival + walk_sec + START_SEC + drive_sec,
            })

    candidates.sort(key=lambda c: (c["arrival"], c["walk_m"]))
    best = []
    used_places = set()
    used_cars = set()
    for candidate in candidates:
        place = day.place_of.get(candidate["stop"], candidate["stop"])
        if place in used_places or candidate["car"]["plate"] in used_cars:
            continue
        used_places.add(place)
        used_cars.add(candidate["car"]["plate"])
        best.append(candidate)
        if len(best) >= limit:
            break
    return best
