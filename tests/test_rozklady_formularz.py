"""Formularz zakładki „Rozkłady" (#269) - sprawdzany w PRAWDZIWEJ przeglądarce.

Pole szukania (z małym ✕ po prawej) stoi w pierwszym wierszu, a pod nim, na
całą szerokość karty, czas - ten sam .time-field, co w wyszukiwarce. Rozkład linii nie
używa godziny, więc zostaje w nim sama data.

Pomija się bez playwright, bez przeglądarki albo bez bazy rozkładów (patrz
test_uklad_telefonu.py - te same założenia).
"""

import datetime
import glob
import json
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import gtfs

EKRANY = {"telefon": (390, 844), "maly-telefon": (360, 640), "komputer": (1280, 800)}

PRZYSTANEK = "Sosnowiecka"
LINIA = "17"

PUSTE_ZRODLA = {
    "**/api/vehicles*": {"vehicles": []},
    "**/api/cars*": {"cars": []},
    "**/api/bikes*": {"stations": [], "free": []},
}

# Prostokąty elementów formularza (null = niewidoczny) - jednym przebiegiem.
POMIAR_JS = """() => {
    const box = sel => {
        const el = document.querySelector(sel);
        if (!el || !el.getClientRects().length) return null;
        const b = el.getBoundingClientRect();
        return b.width > 0 && b.height > 0
            ? {l: b.left, r: b.right, t: b.top, b: b.bottom} : null;
    };
    return {
        pole: box('#tt-query'), wyczysc: box('#tt-clear'), zamknij: box('#tt-close'), czas: box('#tt-time-field'), karta: box('#timetable-card'),
        godzina: box('#tt-time'), teraz: box('#tt-now'), 
        data: box('#tt-date'), szerokosc: document.documentElement.scrollWidth,
        ekran: innerWidth,
    };
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


def _wybierz(page, nazwa):
    page.fill("#tt-query", nazwa)
    page.wait_for_selector("#tt-query-list:not([hidden])")
    page.keyboard.press("Enter")


@pytest.fixture(params=list(EKRANY), ids=list(EKRANY))
def strona(request, adres, przegladarka):
    szerokosc, wysokosc = EKRANY[request.param]
    telefon = szerokosc < 600
    context = przegladarka.new_context(
        viewport={"width": szerokosc, "height": wysokosc}, is_mobile=telefon,
        has_touch=telefon, locale="pl-PL", service_workers="block")
    page = context.new_page()
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres)
    page.click('#view-tabs [data-view="timetable"]' if telefon else "#mode-toggle")
    yield page
    context.close()


def _w_dwoch_wierszach(m):
    karta = m["karta"]
    return (m["czas"]["t"] >= m["pole"]["b"] - 0.5
            and m["czas"]["r"] - m["czas"]["l"] >= karta["r"] - karta["l"] - 40)


def _miesci_sie(m):
    karta = m["karta"]
    return (m["szerokosc"] <= m["ekran"]
            and all(m[k]["l"] >= karta["l"] - 0.5 and m[k]["r"] <= karta["r"] + 0.5
                    for k in ("pole", "czas")))


def test_pole_nad_czasem_bez_wystawania(strona):
    m = strona.evaluate(POMIAR_JS)
    assert _w_dwoch_wierszach(m), m
    assert _miesci_sie(m), m
    assert strona.get_attribute("#tt-query", "placeholder") == "Linia albo przystanek"
    assert not m["wyczysc"], "pusty rozkład nie ma czego czyścić"


def test_wyczysc_zostaje_w_polu_a_zamkniecie_to_maly_krzyzyk_po_prawej(strona):
    _wybierz(strona, PRZYSTANEK)
    strona.wait_for_selector("#tt-results .tt-rows, #tt-results .card")
    m = strona.evaluate(POMIAR_JS)
    assert m["wyczysc"], m
    assert m["pole"]["l"] <= m["wyczysc"]["l"] and m["wyczysc"]["r"] <= m["pole"]["r"] + 0.5, \
        "Wyczyść ma zostać ✕ w polu"


def test_zamknij_rozklad_to_maly_krzyzyk_w_wierszu_pola_tylko_na_telefonie_z_mapa(strona):
    telefon = strona.evaluate("innerWidth") < 600
    if not telefon:
        assert not strona.is_visible("#tt-close"), "na szerokim ekranie jest ◷"
        return
    assert not strona.is_visible("#tt-close"), "w widoku listy zamykają zakładki"
    _wybierz(strona, PRZYSTANEK)
    strona.wait_for_selector("#tt-results .tt-rows, #tt-results .card")
    strona.click('#view-tabs [data-view="map"]')
    m = strona.evaluate(POMIAR_JS)
    z = m["zamknij"]
    assert z, m
    assert z["l"] >= m["czas"]["r"] - 0.5, "✕ ma stać na prawo od ramki czasu"
    assert z["t"] < m["czas"]["b"] and m["czas"]["t"] < z["b"], "w drugim wierszu"
    assert z["t"] >= m["pole"]["b"] - 0.5
    assert z["r"] <= m["karta"]["r"] + 0.5
    assert abs((z["b"] - z["t"]) - (m["czas"]["b"] - m["czas"]["t"])) <= 1, "wysokość ramki czasu"
    assert z["r"] - z["l"] <= 48
    assert m["pole"]["r"] - m["pole"]["l"] >= m["karta"]["r"] - m["karta"]["l"] - 40, \
        "pole szukania ma całą szerokość pierwszego wiersza"
    assert strona.evaluate("getComputedStyle(document.querySelector('#tt-close')).backgroundColor") \
        == strona.evaluate("getComputedStyle(document.querySelector('#search')).backgroundColor"), \
        "niebieski jak przycisk Szukaj" 
    assert _miesci_sie(m), m
    strona.click("#tt-close")
    assert not strona.evaluate("document.body.classList.contains('mode-timetable')")


def test_przystanek_ma_godzine_i_date_obok_siebie(strona):
    _wybierz(strona, PRZYSTANEK)
    strona.wait_for_selector("#tt-results .tt-rows, #tt-results .card")
    m = strona.evaluate(POMIAR_JS)
    assert m["godzina"] and m["teraz"] and m["data"], m
    assert strona.query_selector("#tt-day-toggle") is None, "data nie jest już pod ⌵"
    assert m["godzina"]["r"] <= m["data"]["l"] + 0.5, "data ma stać obok godziny"
    assert m["godzina"]["t"] < m["data"]["b"] and m["data"]["t"] < m["godzina"]["b"]
    # Miejsce na kółko ładowania przy prawej krawędzi ramki.
    assert m["data"]["r"] <= m["czas"]["r"] - 25, m
    assert m["czas"]["b"] - m["czas"]["t"] <= 46, "ramka ma zostać jednowierszowa"
    telefon = strona.evaluate("innerWidth") < 600
    if not telefon:
        assert strona.inner_text("#tt-time-field .tt-hour-row span") == "Odjazd"
    assert _w_dwoch_wierszach(m) and _miesci_sie(m), m


def test_teraz_ustawia_dzis_i_te_chwile(strona):
    strona.fill("#tt-date", "2030-01-01")
    strona.fill("#tt-time", "03:00")
    strona.click("#tt-now")
    assert strona.input_value("#tt-date") == datetime.date.today().isoformat()
    assert strona.input_value("#tt-time") != "03:00"


def test_rozklad_linii_ma_sama_date(strona):
    _wybierz(strona, LINIA)
    strona.wait_for_selector("#tt-time-field.date-only")
    m = strona.evaluate(POMIAR_JS)
    assert m["data"], "w rozkładzie linii data ma być widoczna"
    assert not (m["godzina"] or m["teraz"]), m
    assert _w_dwoch_wierszach(m) and _miesci_sie(m), m

    strona.click("#tt-clear")
    m = strona.evaluate(POMIAR_JS)
    assert m["godzina"] and m["teraz"] and m["data"]


def test_po_linii_przystanek_przywraca_pelne_pole(strona):
    _wybierz(strona, LINIA)
    strona.wait_for_selector("#tt-time-field.date-only")
    _wybierz(strona, PRZYSTANEK)
    strona.wait_for_selector("#tt-time-field:not(.date-only)")
    m = strona.evaluate(POMIAR_JS)
    assert m["godzina"] and m["teraz"]
