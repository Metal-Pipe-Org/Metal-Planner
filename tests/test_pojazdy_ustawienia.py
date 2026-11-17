"""Dwa ustawienia developerskie warstwy pojazdów (#256): filtr kierunku
(domyślnie wyłączony) i statystyki na mapie (domyślnie wyłączone). Sprawdzane
w prawdziwej przeglądarce, na mapie z prawdziwego wyszukiwania."""

import json

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_uklad_telefonu import (PUSTE_ZRODLA, _odpowiedz, adres,  # noqa: F401
                                 przegladarka)

WYSZUKIWANIE = "?from=51.1103533,17.0586404&to=Sosnowiecka"


def _pojazdy(seg):
    """Trzy pojazdy linii z mapy: jadący w naszą stronę, w przeciwną i bez kursu."""
    lat, lon = seg["stops_t"][0][:2]
    wspolne = {"line": seg["num"], "kind": seg["kind"], "to": "Cel", "next": "Następny"}
    return [
        {**wspolne, "lat": 51.10, "lon": 17.00, "heading": 10, "source": "trip",
         "rest": [[lat, lon]]},
        {**wspolne, "lat": 51.10, "lon": 17.01, "heading": 190, "source": "trip",
         "rest": [[50.0, 16.0]]},
        {**wspolne, "lat": 51.10, "lon": 17.02, "heading": None, "source": None},
    ]


def _ustaw(page, id_, wartosc):
    page.evaluate("""([id, on]) => {
        const box = document.getElementById(id);
        box.checked = on;
        box.dispatchEvent(new Event('change', {bubbles: true}));
    }""", [id_, wartosc])


@pytest.fixture(scope="module")
def mapa(adres, przegladarka):
    # Jedno wyszukiwanie na wszystkie testy: ustawienia przestawia się na
    # żywo, a każdy test zostawia je wyłączone.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DEV_MODE", "1")
    context = przegladarka.new_context(viewport={"width": 1280, "height": 800},
                                       locale="pl-PL", service_workers="block")
    page = context.new_page()
    segmenty = []

    def zapamietaj(route):
        odpowiedz = route.fetch()
        for linia in odpowiedz.text().splitlines():     # odpowiedź idzie etapami
            segmenty.extend(s for s in json.loads(linia).get("segments", [])
                            if s.get("stops_t"))
        route.fulfill(response=odpowiedz)

    page.route("**/api/flow*", zapamietaj)
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        if "vehicles" not in wzor:
            page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres + WYSZUKIWANIE)
    for _ in range(120):
        if segmenty:
            break
        page.wait_for_timeout(500)
    assert segmenty
    page.wait_for_timeout(1500)   # mapa musi zdążyć narysować tę odpowiedź
    page.route("**/api/vehicles*", _odpowiedz({"vehicles": _pojazdy(segmenty[0])}))
    page.click("#vehicles-toggle")
    page.wait_for_selector(".vehicle-marker")
    yield page
    context.close()
    monkeypatch.undo()


def _markery(page):
    return page.locator(".vehicle-marker").count()


def test_domyslnie_bez_filtra_i_bez_statystyk(mapa):
    assert _markery(mapa) == 3
    assert mapa.locator(".vehicle-stats:visible").count() == 0


def test_filtr_kierunku_ukrywa_tylko_jadace_w_przeciwna_strone(mapa):
    _ustaw(mapa, "vehicles-direction", True)
    assert _markery(mapa) == 2
    _ustaw(mapa, "vehicles-direction", False)
    assert _markery(mapa) == 3


def test_statystyki_licza_to_co_wiemy(mapa):
    _ustaw(mapa, "vehicles-direction", True)
    _ustaw(mapa, "vehicles-stats", True)
    tekst = mapa.locator(".vehicle-stats").inner_text()
    assert "Z kanału: 3" in tekst
    assert "ze strzałką: 2 (z kursu 2, z ruchu 0)" in tekst
    assert "bez kierunku: 1" in tekst
    assert "Pokazane: 2" in tekst
    assert "ukryte filtrem kierunku: 1" in tekst
    _ustaw(mapa, "vehicles-stats", False)
    _ustaw(mapa, "vehicles-direction", False)
    assert mapa.locator(".vehicle-stats:visible").count() == 0

