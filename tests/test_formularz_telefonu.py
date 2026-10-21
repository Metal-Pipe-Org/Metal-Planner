"""Zwijanie formularza na telefonie (zgłoszenie #375) - w prawdziwej przeglądarce.

Po wyszukaniu formularz zwija się do jednej linijki. Rozwinięty (stuknięciem
w linijkę) ma ustąpić mapie, gdy ktoś stuknie w mapę - ale nie, gdy ją tylko
przeciąga. Po powrocie z tła ma zostać rozwinięty, żeby jedno „Szukaj"
odświeżyło trasę od teraz. Trzy skróty „moje miejsca" znikają ze zwiniętym
formularzem, a przy rozwiniętym leżą w rzędzie.

Ta sama przeglądarka i ten sam serwer co test układu telefonu.
"""

import pytest

from test_uklad_telefonu import (PUSTE_ZRODLA, TRASA, _odpowiedz, _wybierz,  # noqa: F401
                                 adres, przegladarka)

TELEFON = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2,
           "is_mobile": True, "has_touch": True}

ZWINIETY = "document.body.classList.contains('search-collapsed')"


def _wyszukaj(page):
    page.fill("#time", "12:00")
    _wybierz(page, "#start", TRASA[0])
    _wybierz(page, "#end", TRASA[1])
    if not page.evaluate("document.body.classList.contains('searching')") \
            and page.is_visible("#search"):
        page.click("#search")
    page.wait_for_function(ZWINIETY, timeout=20000)


def _wroc_z_tla(page):
    page.evaluate("""() => {
        const set = value => Object.defineProperty(document, 'hidden', {configurable: true, get: () => value});
        set(true);
        document.dispatchEvent(new Event('visibilitychange'));
        set(false);
        document.dispatchEvent(new Event('visibilitychange'));
    }""")


@pytest.fixture
def telefon(adres, przegladarka):
    context = przegladarka.new_context(locale="pl-PL", service_workers="block", **TELEFON)
    page = context.new_page()
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres)
    page.wait_for_selector("#start")
    yield page
    context.close()


def test_stuknięcie_w_mape_zwija_rozwiniety_formularz(telefon):
    _wyszukaj(telefon)
    telefon.click(".search-summary")
    assert not telefon.evaluate(ZWINIETY)
    telefon.touchscreen.tap(195, 520)
    telefon.wait_for_function(ZWINIETY)


def test_przeciagniecie_mapy_nie_zwija_formularza(telefon):
    _wyszukaj(telefon)
    telefon.click(".search-summary")
    telefon.wait_for_timeout(3000)       # kadrowanie trasy przerwałoby przeciągnięcie
    przed = telefon.evaluate("plannerBridge.map.getCenter().lat")
    cdp = telefon.context.new_cdp_session(telefon)
    dotyk = lambda typ, punkty: cdp.send("Input.dispatchTouchEvent", {
        "type": typ, "touchPoints": [{"x": x, "y": y} for x, y in punkty]})
    dotyk("touchStart", [(195, 500)])
    for krok in range(1, 9):
        dotyk("touchMove", [(195 + krok * 8, 500 + krok * 8)])
        telefon.wait_for_timeout(30)
    dotyk("touchEnd", [])
    telefon.wait_for_timeout(500)
    assert telefon.evaluate("plannerBridge.map.getCenter().lat") != przed
    assert not telefon.evaluate(ZWINIETY)


def test_stuknięcie_w_mape_bez_wyniku_nie_zwija_formularza(telefon):
    telefon.touchscreen.tap(195, 520)
    telefon.wait_for_timeout(500)
    assert not telefon.evaluate(ZWINIETY)


def test_powrot_z_tla_rozwija_formularz_z_wynikiem(telefon):
    _wyszukaj(telefon)
    assert telefon.evaluate(ZWINIETY)
    _wroc_z_tla(telefon)
    assert not telefon.evaluate(ZWINIETY)
    assert telefon.is_visible("#search")


def test_powrot_z_tla_bez_wyniku_nic_nie_zmienia(telefon):
    _wroc_z_tla(telefon)
    assert not telefon.evaluate(ZWINIETY)


def test_skroty_znikaja_ze_zwinietym_formularzem_a_przy_rozwinietym_leza_w_rzedzie(telefon):
    rzad = """() => [...document.querySelectorAll('.places-bar .place-button')]
        .map(b => Math.round(b.getBoundingClientRect().top))"""
    assert telefon.is_visible(".places-bar")
    assert len(set(telefon.evaluate(rzad))) == 1
    _wyszukaj(telefon)
    assert not telefon.is_visible(".places-bar")
    telefon.click(".search-summary")
    assert telefon.is_visible(".places-bar")
    assert len(set(telefon.evaluate(rzad))) == 1
