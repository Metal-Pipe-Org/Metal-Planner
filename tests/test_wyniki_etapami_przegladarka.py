"""Wynik wyszukiwania przychodzi etapami - sprawdzane w PRAWDZIWEJ przeglądarce
(zgłoszenie #229, patrz tests/test_wyniki_etapami.py dla strony serwera).

Serwer jest prawdziwy, ale etapy wypuszcza test: każdy czeka na swoją bramkę,
więc da się obejrzeć stronę w środku wyszukiwania. Same etapy są policzone na
prawdziwej bazie rozkładów (o 12:00), z dwoma podstawionymi autami Traficara,
żeby etap aut miał co dołożyć - i z innymi autami w etapie świeżych danych.

Sprawdzane jest to, co pasażer widzi:
  - pasek z najszybszą trasą jest od razu, sam - mapa i lista dopiero
    z mapą przepływów - a „Szukaj" kręci się do ostatniego etapu,
  - późniejsze etapy dokładają się do mapy - nie czyszczą jej, nie zabierają
    wybranej trasy ani okienka w rogu, a nowe propozycje idą na koniec listy,
  - „Pokaż więcej" kręci się do końca i nie przestawia kadru, a auta nie
    znikają razem ze starą mapą - zostają, aż przyjdą nowe,
  - na telefonie formularz zwija się przy pierwszym wyniku (a przy błędzie
    nie), a w zwiniętej linijce kręci się kółko, dopóki coś jeszcze dochodzi.
"""

import threading
from datetime import datetime

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import gtfs
import planner
import routes
import traficar
from tests.test_uklad_telefonu import PUSTE_ZRODLA, _chromium, _odpowiedz, _wybierz

TRASA = ("Sosnowiecka", "Wojszyce")
ETAPY = ("fastest", "map", "extras", "fresh")

# Auta w dwóch wersjach: te, które były w pamięci, i świeże. Stoją przy
# przystankach tej relacji, żeby mapa do nich dowoziła.
AUTO = {"model": "Clio", "van": False, "where": "", "range": 200, "fuel": 50}
STARE_AUTA = [{**AUTO, "lat": 51.0848, "lon": 17.0645, "plate": "DW 1",
               "ogarniam": [{"co": "x", "ile": 10}]}]
SWIEZE_AUTA = STARE_AUTA + [{**AUTO, "lat": 51.0778, "lon": 17.0637, "plate": "DW 2",
                             "ogarniam": []}]


class Bramki:
    """Podstawione planner.plan_flow_stages: oddaje gotowe etapy, każdy
    dopiero po otwarciu jego bramki."""

    def __init__(self):
        self.etapy = {}
        self.zamknij()

    def zamknij(self):
        self.otwarte = {name: threading.Event() for name in ETAPY}

    def pusc(self, *names):
        for name in names:
            self.otwarte[name].set()

    def __call__(self, start, *args, more=None, live_refreshed=None, **kwargs):
        if start == "Nieznany":
            yield "error", {"error": "Nie znam przystanku „Nieznany”."}
            return
        for name, delta in self.etapy[int(more or 0)]:
            if not self.otwarte[name].wait(30):
                return
            yield name, delta


@pytest.fixture(scope="module")
def bramki():
    try:
        gtfs.all_stop_names()
    except FileNotFoundError:
        pytest.skip("brak bazy rozkładów (data/gtfs.sqlite)")
    from werkzeug.serving import make_server
    import app as aplikacja
    bramki = Bramki()
    oryginal = routes.plan_flow_stages
    routes.plan_flow_stages = bramki
    serwer = make_server("127.0.0.1", 0, aplikacja.app, threaded=True)
    threading.Thread(target=serwer.serve_forever, daemon=True).start()
    bramki.adres = f"http://127.0.0.1:{serwer.server_port}/"
    yield bramki
    serwer.shutdown()
    routes.plan_flow_stages = oryginal


@pytest.fixture
def etapy(bramki, monkeypatch):
    """Prawdziwe etapy tej relacji, dla zwykłej mapy i po „Pokaż więcej"."""
    auta = {"teraz": STARE_AUTA}
    monkeypatch.setattr(traficar, "enabled", lambda: True)
    monkeypatch.setattr(traficar, "car_list", lambda: auta["teraz"])

    def odswiezone():
        auta["teraz"] = SWIEZE_AUTA
        return True

    kiedy = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    for more in (0, 1):
        auta["teraz"] = STARE_AUTA
        bramki.etapy[more] = list(planner.plan_flow_stages(
            *TRASA, kiedy, more=more, live_refreshed=odswiezone))
    assert [name for name, _ in bramki.etapy[0]] == list(ETAPY)
    bramki.zamknij()
    return bramki


@pytest.fixture(scope="module")
def przegladarka():
    with sync_api.sync_playwright() as p:
        browser = _chromium(p)
        yield browser
        browser.close()


def _strona(przegladarka, adres, viewport, telefon=False):
    context = przegladarka.new_context(
        viewport=viewport, device_scale_factor=2 if telefon else 1,
        is_mobile=telefon, has_touch=telefon, locale="pl-PL",
        service_workers="block")
    page = context.new_page()
    page.bledy = []
    page.on("pageerror", lambda e: page.bledy.append(str(e)))
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres)
    page.wait_for_selector("#start")
    page.fill("#time", "12:00")
    return page


def _szukaj(page):
    _wybierz(page, "#start", TRASA[0])
    _wybierz(page, "#end", TRASA[1])
    page.click("#search")


def _szuka(page):
    return page.evaluate("document.body.classList.contains('searching')")


def _karty(page):
    return page.locator("#results .journey").all_inner_texts()


# Linie mapy przepływów oznaczone przed kolejnym etapem - po nim mają wciąż
# leżeć na mapie, a nie zostać narysowane od nowa.
OZNACZ_LINIE = """() => {
    const map = window.plannerBridge.map;
    let n = 0;
    map.eachLayer(l => { if (l instanceof L.Polyline) { l._sonda = true; n++; } });
    return n;
}"""
OZNACZONE_NA_MAPIE = """() => {
    let n = 0;
    window.plannerBridge.map.eachLayer(l => { if (l._sonda) n++; });
    return n;
}"""
LINIE = """() => {
    let n = 0;
    window.plannerBridge.map.eachLayer(l => { if (l instanceof L.Polyline) n++; });
    return n;
}"""
# Auta na mapie - po pozycjach, bo kropki aut to zwykłe kółka z dymkiem.
AUTA_NA_MAPIE = """(auta) => {
    const szukane = new Set(auta.map(a => a.lat.toFixed(4) + ',' + a.lon.toFixed(4)));
    const out = [];
    window.plannerBridge.map.eachLayer(l => {
        if (!(l instanceof L.CircleMarker) || !l.getTooltip()) return;
        const p = l.getLatLng();
        const klucz = p.lat.toFixed(4) + ',' + p.lng.toFixed(4);
        if (szukane.has(klucz)) out.push(klucz);
    });
    return out.sort();
}"""
KADR = """() => {
    const map = window.plannerBridge.map;
    const c = map.getCenter();
    return [c.lat.toFixed(5), c.lng.toFixed(5), map.getZoom()];
}"""


def test_kolejne_etapy_dokladaja_sie_do_mapy(etapy, przegladarka):
    page = _strona(przegladarka, etapy.adres, {"width": 1280, "height": 800})
    _szukaj(page)
    assert _szuka(page)

    # Najszybsza trasa: sam pasek, bez zakresu mapy - ani mapy, ani listy,
    # a „Szukaj" kręci się dalej.
    etapy.pusc("fastest")
    page.wait_for_selector("#time-headline .headline-best")
    assert not page.is_visible("#time-headline .headline-limit")
    assert _karty(page) == []
    assert page.is_visible("#results .notice.loading")
    # Przy wyłączonych propozycjach karta czeka na listę, której nie ma.
    page.evaluate("document.body.classList.add('routes-off')")
    assert not page.is_visible("#results .notice.loading")
    page.evaluate("document.body.classList.remove('routes-off')")
    assert page.evaluate(LINIE) == 0
    assert _szuka(page)

    # Mapa: mapa i lista. Wybieramy trasę i zostawiamy coś w okienku.
    etapy.pusc("map")
    page.wait_for_selector("#time-headline .headline-limit")
    page.wait_for_selector("#results .journey")
    przed = _karty(page)
    page.click("#results .journey >> nth=0")
    page.wait_for_selector("#results .journey.selected")
    wybrana = page.locator("#results .journey.selected .j-head").inner_text()
    page.evaluate("""() => {
        document.getElementById('flow-panel').hidden = false;
        document.getElementById('flow-panel-body').textContent = 'ZNACZNIK';
    }""")
    linii = page.evaluate(OZNACZ_LINIE)
    kadr = page.evaluate(KADR)
    assert linii > 0 and _szuka(page)

    # Auta: dochodzą na koniec listy, a mapa, wybór i okienko zostają.
    etapy.pusc("extras")
    page.wait_for_function(f"document.querySelectorAll('#results .journey').length > {len(przed)}")
    po = _karty(page)
    assert [k.split("\n")[0] for k in po[:len(przed)]] == [k.split("\n")[0] for k in przed]
    assert "autem" in po[-1]
    assert page.locator("#results .journey.selected .j-head").inner_text() == wybrana
    assert page.evaluate(OZNACZONE_NA_MAPIE) == linii
    assert page.inner_text("#flow-panel-body") == "ZNACZNIK"
    assert page.evaluate(KADR) == kadr
    assert _szuka(page)       # świeże dane jeszcze nie doszły

    etapy.pusc("fresh")
    page.wait_for_function("!document.body.classList.contains('searching')")
    assert page.locator("#results .journey.selected .j-head").inner_text() == wybrana
    assert page.evaluate(OZNACZONE_NA_MAPIE) == linii
    assert page.inner_text("#flow-panel-body") == "ZNACZNIK"
    auta = page.evaluate(AUTA_NA_MAPIE, SWIEZE_AUTA)
    assert auta

    # „Pokaż więcej": przycisk kręci się do końca, kadr stoi, wybór zostaje,
    # o ile gęstsza lista ma tę trasę.
    etapy.zamknij()
    page.click("#time-headline .headline-more")
    assert page.evaluate("document.body.classList.contains('more-busy')")
    assert page.is_visible("#time-headline .headline-more .spinner")
    etapy.pusc("fastest", "map")
    # Gęstsza mapa już stoi (przycisk obiecuje kolejne zagęszczenie), a auta
    # jeszcze się liczą.
    page.wait_for_selector('#time-headline .headline-more[data-tip*="3×"]')
    assert page.evaluate("document.body.classList.contains('more-busy')")
    assert page.is_visible("#time-headline .headline-more .spinner")
    # Auta z poprzedniej mapy stoją dalej - do etapu z nowymi.
    assert page.evaluate(AUTA_NA_MAPIE, SWIEZE_AUTA) == auta
    etapy.pusc("extras", "fresh")
    page.wait_for_function("!document.body.classList.contains('more-busy')")
    assert page.evaluate(KADR) == kadr
    karty = _karty(page)
    assert len(karty) >= len(przed)
    if any(k.startswith(wybrana.split("\n")[0]) for k in karty):
        assert page.locator("#results .journey.selected").count() == 1
    assert not page.bledy, page.bledy
    page.context.close()


def test_telefon_zwija_formularz_przy_pierwszym_wyniku(etapy, przegladarka):
    page = _strona(przegladarka, etapy.adres, {"width": 390, "height": 844}, telefon=True)
    zwiniety = "document.body.classList.contains('search-collapsed')"
    kolko = ".search-summary .search-summary-busy"

    # Błąd: formularz zostaje rozwinięty, do poprawki.
    page.fill("#start", "Nieznany")
    _wybierz(page, "#end", TRASA[1])
    page.click("#search")
    page.wait_for_selector("#results .notice.error", state="attached")
    assert not page.evaluate(zwiniety)

    _szukaj(page)
    assert not page.evaluate(zwiniety)     # jeszcze nic nie przyszło
    etapy.pusc("fastest")
    page.wait_for_function(zwiniety)
    assert page.is_visible(".search-summary")
    assert page.is_visible(kolko)
    etapy.pusc("map", "extras")
    page.wait_for_selector("#time-headline .headline-limit", state="attached")
    assert page.is_visible(kolko)          # świeże dane jeszcze dochodzą
    etapy.pusc("fresh")
    page.wait_for_function("!document.body.classList.contains('searching')")
    assert not page.is_visible(kolko)
    assert not page.bledy, page.bledy
    page.context.close()
