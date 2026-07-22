"""Trasowanie po prawdziwych ulicach dla odcinków pieszo / rowerem / autem.

Dojścia pieszo, przejazdy rowerem WRM i jazda Traficarem były dotąd rysowane
jako LINIA PROSTA (haversine) - „idź/jedź mniej więcej tędy". Ten moduł
zamienia je na ścieżkę po realnej sieci dróg (jak Google Maps), pytając
publiczne serwery OSRM FOSSGIS (te same, których używa strona osm.org),
osobno per profil:

- `routed-foot`  - profil pieszy (chodniki, przejścia, skróty);
- `routed-bike`  - profil rowerowy (ścieżki, jednokierunkowe inaczej);
- `routed-car`   - profil samochodowy (dla Traficara).

Bez modelu ruchu na żywo (out of scope) - to geometria + rozkładowy czas/
dystans profilu, nie bieżące korki.

Odporność: (1) cache w pamięci - punkty stałe (przystanki, stacje) powtarzają
się między zapytaniami, więc po rozgrzaniu prawie wszystko trafia w cache;
(2) BEZPIECZNIK - gdy serwer OSRM nie odpowiada, pierwszy timeout wyłącza
trasowanie na COOLDOWN sekund, więc padnięty routing NIE spowalnia każdego
zapytania (reszta odcinków leci wtedy prostą, jak dotąd). Cokolwiek się nie
uda - fallback na linię prostą, wyszukiwanie nigdy nie pada przez routing.
"""

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

OSRM_HOSTS = {
    "foot": "https://routing.openstreetmap.de/routed-foot",
    "bike": "https://routing.openstreetmap.de/routed-bike",
    "car": "https://routing.openstreetmap.de/routed-car",
}
PROFILE_PATH = {"foot": "foot", "bike": "bike", "car": "driving"}

TIMEOUT_SEC = 4
COOLDOWN_SEC = 120        # po błędzie sieci tyle sekund nie próbujemy w ogóle (bezpiecznik)

_cache = {}               # (profil, alat, alon, blat, blon) -> (polyline, dist_m, dur_s) | None
_disabled_until = 0.0     # monotonic; dopóki teraz < tego, route() nie rusza sieci


def _key(profile, a, b):
    return (profile, round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5))


def route(profile, a, b, allow_fetch=True):
    """Trasa `a`->`b` (oba (lat, lon)) danym profilem: (polyline [[lat,lon],...],
    dystans_m, czas_s) albo None (nie udało się - wołający rysuje prostą).

    `allow_fetch=False` zwraca TYLKO to, co już jest w cache'u (albo None), nie
    dotykając sieci - tak wołający sprawdza „czy to już policzone" i limituje
    liczbę NOWYCH zapytań na jedno wyszukiwanie (odcinki z cache'u są darmowe).

    Wynik cache'owany; przy błędzie sieci włącza się bezpiecznik i przez
    COOLDOWN_SEC route() od razu zwraca cache-albo-None, nie dotykając sieci."""
    key = _key(profile, a, b)
    if key in _cache:
        return _cache[key]
    if not allow_fetch or time.monotonic() < _disabled_until:
        return None                       # tylko cache / bezpiecznik czynny - bez sieci
    return _fetch_route(profile, a, b, key)


def _fetch_route(profile, a, b, key=None):
    """Faktyczne zapytanie do OSRM (bez sprawdzania cache/bezpiecznika -
    to robi wołający). Sukces cache'uje; błąd włącza bezpiecznik i zwraca None."""
    global _disabled_until
    host = OSRM_HOSTS.get(profile)
    if host is None:
        return None
    coords = f"{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}"    # OSRM chce lon,lat
    url = f"{host}/route/v1/{PROFILE_PATH[profile]}/{coords}?overview=full&geometries=geojson"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Metal-Planner/0.1"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            data = json.load(response)
        rt = data["routes"][0]
        polyline = [[lat, lon] for lon, lat in rt["geometry"]["coordinates"]]
        result = (polyline, rt["distance"], rt["duration"])
        _cache[key if key is not None else _key(profile, a, b)] = result   # tylko sukcesy
        return result
    except (OSError, ValueError, KeyError, IndexError):
        _disabled_until = time.monotonic() + COOLDOWN_SEC     # bezpiecznik
        return None


def route_many(requests, max_fetch=None):
    """Trasuje listę [(profil, a, b), ...] RÓWNOLEGLE, zwraca wyniki w tej
    samej kolejności (result | None). Odcinki z cache'u są darmowe; braki
    dociągane wątkami (jedno OSRM to głównie czekanie na sieć, więc wątki
    pomagają - „zimne" zapytanie z kilkoma odcinkami schodzi z ~sekund do
    ~jednego round-tripu). `max_fetch` ogranicza liczbę NOWYCH pobrań (braki
    ponad limit zostają None); wołający podaje `requests` od najważniejszych.
    Bezpiecznik: gdy czynny, nie dotykamy sieci."""
    results = [_cache.get(_key(*r)) for r in requests]
    if time.monotonic() < _disabled_until:
        return results
    misses = [i for i, res in enumerate(results) if res is None]
    if max_fetch is not None:
        misses = misses[:max_fetch]
    if not misses:
        return results
    with ThreadPoolExecutor(max_workers=min(8, len(misses))) as executor:
        futures = {executor.submit(_fetch_route, *requests[i]): i for i in misses}
        for future in futures:
            try:
                results[futures[future]] = future.result()
            except Exception:
                results[futures[future]] = None    # nieoczekiwany błąd wątku = prosta, nie crash
    return results


def simplify(polyline, tol_deg=0.00008):
    """Rzadszy odpowiednik gtfs._simplify - mniej punktów w JSON-ie (OSRM
    potrafi zwrócić setki punktów na trasę). ~9 m progu."""
    if len(polyline) <= 2:
        return polyline
    tol2 = tol_deg * tol_deg
    kept = [polyline[0]]
    for p in polyline[1:-1]:
        d_lat = p[0] - kept[-1][0]
        d_lon = p[1] - kept[-1][1]
        if d_lat * d_lat + d_lon * d_lon >= tol2:
            kept.append(p)
    kept.append(polyline[-1])
    return kept
