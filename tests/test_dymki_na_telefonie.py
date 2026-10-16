"""Dymki na mapie na telefonie (zgłoszenie #251) - w PRAWDZIWEJ przeglądarce.

Dymek auta stał nad kropką i wychodził poza ekran, gdy kropka była przy
brzegu mapy: długi opis ucinał się z lewej. Test otwiera dymek auta (tym
samym kodem, który rysuje auta na mapie) przy lewej, prawej i górnej krawędzi
na dwóch telefonach i sprawdza, że jego ramka leży w całości w oknie.

Pomija się bez playwright, bez przeglądarki albo bez bazy rozkładów.
"""

import glob
import json
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import gtfs

TELEFONY = {"iphone": (390, 844), "maly-android": (360, 640)}

PUSTE_ZRODLA = {
    "**/api/vehicles*": {"vehicles": []},
    "**/api/cars*": {"cars": []},
    "**/api/bikes*": {"stations": [], "free": []},
}

# Treść dymka auta ze zdjęcia ekranu ze zgłoszenia (carTooltipHtml wymaga
# wyniku wyszukiwania, a tu chodzi o samo położenie dymka). Kropka ląduje w podanym punkcie okna;
# zwraca ramkę dymka i rozmiar okna.
OTWORZ_DYMEK_JS = """([x, y]) => {
    const car = {lat: 0, lon: 0};
    const html = ['<b>RENAULT Arkana · WE1AX71</b>',
        'Jesteś przy nim 23:09 — 9 min pieszo z „Wybrany punkt (51.1113, 17.0199)”',
        'Do celu 2,6 km w linii prostej', 'Paliwo 61%, zasięg 442 km',
        'Ogarniam: nic do wzięcia'].join('<br>');
    const marker = carMarker(car, html).addTo(map);
    const rect = map.getContainer().getBoundingClientRect();
    marker.setLatLng(map.containerPointToLatLng([x - rect.left, y - rect.top]));
    marker.openTooltip();
    const tip = marker.getTooltip().getElement();
    const box = tip.getBoundingClientRect();
    return {left: box.left, right: box.right, top: box.top, bottom: box.bottom,
            width: innerWidth, height: innerHeight,
            shift: parseFloat(tip.style.getPropertyValue('--tip-shift') || '0'),
            centre: (box.left + box.right) / 2};
}"""


def _chromium(playwright):
    try:
        return playwright.chromium.launch()
    except sync_api.Error:
        pass
    for path in sorted(glob.glob(str(Path.home() / "Library/Caches/ms-playwright/chromium-*"
                                     "/chrome-mac*/Google Chrome for Testing.app/Contents"
                                     "/MacOS/Google Chrome for Testing")), reverse=True):
        return playwright.chromium.launch(executable_path=path)
    pytest.skip("brak przeglądarki dla playwright (playwright install chromium)")


@pytest.fixture(scope="module")
def adres():
    try:
        gtfs.all_stop_names()
    except FileNotFoundError:
        pytest.skip("brak bazy rozkładów (data/gtfs.sqlite) - strona pokazałaby sam błąd")
    from werkzeug.serving import make_server
    import app as aplikacja
    serwer = make_server("127.0.0.1", 0, aplikacja.app, threaded=True)
    threading.Thread(target=serwer.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{serwer.server_port}/"
    serwer.shutdown()


@pytest.fixture(scope="module")
def przegladarka():
    with sync_api.sync_playwright() as p:
        browser = _chromium(p)
        yield browser
        browser.close()


def _odpowiedz(tresc):
    return lambda route: route.fulfill(status=200, content_type="application/json",
                                       body=json.dumps(tresc))


@pytest.fixture(params=list(TELEFONY), ids=list(TELEFONY))
def strona(request, adres, przegladarka):
    width, height = TELEFONY[request.param]
    context = przegladarka.new_context(
        viewport={"width": width, "height": height}, is_mobile=True,
        has_touch=True, locale="pl-PL", service_workers="block")
    page = context.new_page()
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres)
    page.wait_for_selector("#start")
    yield page, width, height
    context.close()


@pytest.mark.parametrize("gdzie", ["lewa", "prawa", "gora", "lewy-gorny-rog"])
def test_dymek_auta_miesci_sie_w_oknie(strona, gdzie):
    page, width, height = strona
    x, y = {"lewa": (4, height / 2), "prawa": (width - 4, height / 2),
            "gora": (width / 2, 30), "lewy-gorny-rog": (4, 30)}[gdzie]
    m = page.evaluate(OTWORZ_DYMEK_JS, [x, y])
    assert m["left"] >= 0 and m["right"] <= m["width"], m
    assert m["top"] >= 0 and m["bottom"] <= m["height"], m
    assert m["right"] - m["left"] <= m["width"] - 16, m


def test_dymek_w_srodku_zostaje_nad_kropka_bez_przesuniecia(strona):
    page, width, height = strona
    m = page.evaluate(OTWORZ_DYMEK_JS, [width / 2, height / 2])
    assert m["shift"] == 0
    assert abs(m["centre"] - width / 2) < 2
