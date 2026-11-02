import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import bikes
import gtfs
import pkp
import planner
import traficar


@pytest.fixture
def install_day(monkeypatch):
    """install_day(day) podmienia gtfs.load_day tak, żeby plan_flow/plan_route
    dostawały syntetyczny DayData zamiast czytać SQLite."""
    def _install(day):
        monkeypatch.setattr(gtfs, "load_day", lambda d: day)
    return _install


@pytest.fixture(autouse=True)
def _pkp_disabled_by_default(monkeypatch):
    """gtfs.load_day() dokleja rozkład kolejowy przez pkp.augment_day (patrz
    pkp.py) - bez tej blokady KAŻDY test wołający load_day (nie tylko
    test_pkp.py) sięgałby po prawdziwy data/pkp.sqlite i prawdziwy
    PKP_API_KEY ze środowiska, w którym akurat działa pytest. To łamie
    hermetyczność testów (wynik zależy od tego, czy ktoś ma skonfigurowany
    klucz akurat na tej maszynie) i wolno robi się w każdym teście
    dotykającym gtfs.load_day, nie tylko tych o PKP.

    Testy, którym PKP faktycznie jest potrzebne (patrz tests/test_pkp.py),
    same nadpisują `pkp.enabled` w swoim fixturze - ten sam `monkeypatch`
    ma zasięg całego testu, więc kolejne setattr po prostu wygrywa."""
    monkeypatch.setattr(pkp, "enabled", lambda: False)


@pytest.fixture(autouse=True)
def _traficar_disabled_by_default(monkeypatch):
    """plan_flow dokłada do listy propozycje kończące się Traficarem (patrz
    traficar.py) - a te biorą się z ŻYWEGO feedu fioletowe.live. Bez tej
    blokady każdy test wołający plan_flow strzelałby w internet: wynik
    zależałby od tego, ile aut akurat stoi we Wrocławiu i czy serwis żyje,
    a testy chodziłyby tyle, ile trwa timeout HTTP.

    Test, który Traficara faktycznie dotyczy, sam podstawia dane (ten sam
    `monkeypatch` ma zasięg całego testu, więc kolejne setattr wygrywa) -
    patrz tests/test_traficar.py.
    """
    monkeypatch.setattr(traficar, "enabled", lambda: False)
    # Strefa oddawania też przychodzi z feedu, a pytają o nią także testy,
    # które auta już podstawiły - nieznana strefa to brak ostrzeżenia.
    monkeypatch.setattr(traficar, "zone", lambda: None)


@pytest.fixture(autouse=True)
def _bikes_disabled_by_default(monkeypatch):
    """Ten sam powód, co przy Traficarze, tylko źródłem jest kanał GBFS
    operatora WRM. Odkąd rowery stoją NA MAPIE (patrz bikes.map_places),
    sięga po nie każde wyszukanie, nie tylko to z odhaczonym rowerem - więc
    bez tej blokady w internet strzelałby każdy test wołający plan_flow.

    Zatkana jest sama granica sieci, a nie wyłącznik `bikes.enabled` - inaczej
    nie dałoby się przetestować tego wyłącznika (patrz tests/test_rower.py).
    Test o rowerach sam podstawia dane (patrz tests/test_rowery_na_mapie.py).
    """
    def _bez_sieci(url):
        raise OSError(f"test nie wychodzi do sieci: {url}")

    bikes._cache.clear()
    monkeypatch.setattr(bikes, "_fetch", _bez_sieci)


LEAFLET_URLS = {
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js": "application/javascript",
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css": "text/css",
}


@pytest.fixture(scope="session", autouse=True)
def _leaflet_pobrany_raz():
    """Strona ładuje Leaflet z CDN (templates/index.html), a każdy z setek
    testów w przeglądarce otwiera ją od nowa. Przy tylu zapytaniach CDN
    co jakiś czas odpowiada błędem i strona zostaje bez `L` i `map` - test
    pada na czymś, co nie jest jego przedmiotem. Biblioteka jest więc pobrana
    raz na przebieg i podawana z pamięci każdemu kontekstowi przeglądarki."""
    try:
        from playwright import sync_api
    except ImportError:
        yield
        return
    import time
    import urllib.request

    pobrane = {}

    def pobierz(url):
        if url not in pobrane:
            for proba in range(5):
                try:
                    with urllib.request.urlopen(url, timeout=20) as odpowiedz:
                        pobrane[url] = odpowiedz.read()
                    break
                except OSError:
                    time.sleep(1 + proba)
        return pobrane.get(url)

    def podaj(route):
        url = route.request.url
        tresc = pobierz(url)
        if tresc is None:
            route.continue_()
        else:
            route.fulfill(status=200, body=tresc, content_type=LEAFLET_URLS[url])

    oryginal = sync_api.Browser.new_context

    def new_context(self, *args, **kwargs):
        context = oryginal(self, *args, **kwargs)
        for url in LEAFLET_URLS:
            context.route(url, podaj)
        return context

    sync_api.Browser.new_context = new_context
    yield
    sync_api.Browser.new_context = oryginal
