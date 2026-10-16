"""Dymki na mapie na telefonie (zgłoszenie #251) - w PRAWDZIWEJ przeglądarce.

Dymek auta stał nad kropką i wychodził poza ekran, gdy kropka była przy
brzegu mapy: długi opis ucinał się z lewej. Test otwiera dymki (auta, słupka
bez kierunku i dymka otwieranego w prawo) przy krawędziach i w rogach na dwóch
telefonach i sprawdza, że ramka leży w oknie z marginesem, a strzałka jest
po stronie kropki i dalej na nią wskazuje.

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
# wyniku wyszukiwania, a tu chodzi o samo położenie dymka).
DYMEK_AUTA = "<br>".join([
    "<b>RENAULT Arkana · WE1AX71</b>",
    "Jesteś przy nim 23:09 — 9 min pieszo z „Wybrany punkt (51.1113, 17.0199)”",
    "Do celu 2,6 km w linii prostej", "Paliwo 61%, zasięg 442 km",
    "Ogarniam: nic do wzięcia"])

# Kropka ląduje w podanym punkcie okna. Rodzaj "auto" idzie tym samym kodem,
# który rysuje auta na mapie (dymek nad kropką); "przystanek" wiąże dymek bez
# kierunku jak słupki na mapie (Leaflet sam wybiera lewo/prawo); "w-prawo"
# to dymek otwierany w prawo, jak etykietka przejazdu rowerem. Zwraca ramkę
# dymka, stronę, po której się otworzył, i czubek strzałki.
OTWORZ_DYMEK_JS = """([rodzaj, x, y, html]) => {
    const rect = map.getContainer().getBoundingClientRect();
    const latlng = map.containerPointToLatLng([x - rect.left, y - rect.top]);
    const marker = rodzaj === 'auto' ? carMarker({lat: 0, lon: 0}, html)
        : L.circleMarker([0, 0]).bindTooltip(html,
            rodzaj === 'w-prawo' ? {direction: 'right', offset: [6, 0]} : {});
    marker.addTo(map);
    marker.setLatLng(latlng);
    marker.openTooltip();
    const tip = marker.getTooltip().getElement();
    const box = tip.getBoundingClientRect();
    const side = ['top', 'bottom', 'left', 'right']
        .find(s => tip.classList.contains('leaflet-tooltip-' + s));
    const arrow = getComputedStyle(tip, '::before');
    const px = v => parseFloat(v) || 0;
    // Strzałka to kwadrat 12 px z jedną kolorową połową; czubek leży 6 px
    // od krawędzi ramki, a w poprzek - na środku kwadratu.
    const alongX = box.left + tip.clientLeft + px(arrow.left) + px(arrow.marginLeft) + 6;
    const alongY = box.top + tip.clientTop + px(arrow.top) + px(arrow.marginTop) + 6;
    const czubek = {
        top: [alongX, box.bottom - tip.clientTop + 6],
        bottom: [alongX, box.top + tip.clientTop - 6],
        left: [box.right - tip.clientLeft + 6, alongY],
        right: [box.left + tip.clientLeft - 6, alongY],
    }[side];
    return {left: box.left, right: box.right, top: box.top, bottom: box.bottom,
            width: innerWidth, height: innerHeight, side,
            shift: parseFloat(tip.style.getPropertyValue('--tip-shift') || '0'),
            centre: (box.left + box.right) / 2, tipX: czubek[0], tipY: czubek[1]};
}"""

DLUGI_PRZYSTANEK = "Plac Grunwaldzki (Politechnika Wrocławska) – Wybrzeże Wyspiańskiego PKP"

# Odsunięcie dymka od środka kropki (offset w app.js / w teście).
ODSTEP = {"auto": 4, "przystanek": 0, "w-prawo": 6}


def _otworz(page, rodzaj, x, y, html):
    return page.evaluate(OTWORZ_DYMEK_JS, [rodzaj, x, y, html])


def _miesci_sie(m):
    assert m["left"] >= 8 - 0.5 and m["right"] <= m["width"] - 8 + 0.5, m
    assert m["top"] >= 8 - 0.5 and m["bottom"] <= m["height"] - 8 + 0.5, m


def _strzalka_na_kropce(m, rodzaj, x, y):
    """Ramka po stronie dymka, czubek strzałki przy kropce: w poprzek co do
    2 px, w osi otwierania nie dalej niż odsunięcie dymka od kropki."""
    side = m["side"]
    if side in ("top", "bottom"):
        assert abs(m["tipX"] - x) <= 2, m
        wzdluz = m["tipY"] - y
    else:
        assert abs(m["tipY"] - y) <= 2, m
        wzdluz = m["tipX"] - x
    assert abs(abs(wzdluz) - ODSTEP[rodzaj]) <= 2, m
    if side == "top":
        assert m["bottom"] <= y, m
    elif side == "bottom":
        assert m["top"] >= y, m
    elif side == "left":
        assert m["right"] <= x, m
    else:
        assert m["left"] >= x, m


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


# (rodzaj, gdzie kropka, oczekiwana strona dymka, treść). Kropki stoją ~20 px od
# brzegu - tyle, ile dymek z marginesem 8 px może jeszcze sięgnąć strzałką.
PRZYPADKI = {
    "auto-lewa": ("auto", lambda w, h: (24, h / 2), "top", DYMEK_AUTA),
    "auto-prawa": ("auto", lambda w, h: (w - 24, h / 2), "top", DYMEK_AUTA),
    "auto-gora": ("auto", lambda w, h: (w / 2, 30), "bottom", DYMEK_AUTA),
    "auto-dol": ("auto", lambda w, h: (w / 2, h - 12), "top", DYMEK_AUTA),
    "auto-lewy-gorny-rog": ("auto", lambda w, h: (24, 30), "bottom", DYMEK_AUTA),
    "auto-prawy-gorny-rog": ("auto", lambda w, h: (w - 24, 30), "bottom", DYMEK_AUTA),
    "auto-lewy-dolny-rog": ("auto", lambda w, h: (24, h - 12), "top", DYMEK_AUTA),
    "przystanek-lewa-gora": ("przystanek", lambda w, h: (12, 20), "right", "Plac Grunwaldzki"),
    "przystanek-prawa-dol": ("przystanek", lambda w, h: (w - 12, h - 20), "left", "Plac Grunwaldzki"),
    "przystanek-za-szeroki-na-bok": ("przystanek", lambda w, h: (w / 2 - 20, h / 2), "top",
                                     DLUGI_PRZYSTANEK),
    "w-prawo-prawa": ("w-prawo", lambda w, h: (w - 12, h / 2), "left", "23:41 · 1,2 km"),
    "w-prawo-prawy-gorny-rog": ("w-prawo", lambda w, h: (w - 12, 20), "left", "23:41 · 1,2 km"),
}


@pytest.mark.parametrize("przypadek", list(PRZYPADKI))
def test_dymek_przy_brzegu_miesci_sie_i_wskazuje_kropke(strona, przypadek):
    page, width, height = strona
    rodzaj, gdzie, strona_dymka, html = PRZYPADKI[przypadek]
    x, y = gdzie(width, height)
    m = _otworz(page, rodzaj, x, y, html)
    _miesci_sie(m)
    assert m["right"] - m["left"] <= m["width"] - 16, m
    assert m["side"] == strona_dymka, m
    _strzalka_na_kropce(m, rodzaj, x, y)


@pytest.mark.parametrize("gdzie", ["lewa", "prawa"])
def test_kropka_na_samej_krawedzi_strzalka_nie_wychodzi_z_ramki(strona, gdzie):
    """Kropka 4 px od brzegu jest poza zasięgiem dymka z marginesem 8 px:
    dymek zostaje w oknie, a strzałka staje na jego rogu, nie poza nim."""
    page, width, height = strona
    x = 4 if gdzie == "lewa" else width - 4
    m = _otworz(page, "auto", x, height / 2, DYMEK_AUTA)
    _miesci_sie(m)
    assert m["left"] + 6 <= m["tipX"] <= m["right"] - 6, m
    assert abs(m["tipX"] - x) < 20, m


def test_dymek_w_srodku_zostaje_nad_kropka_bez_przesuniecia(strona):
    page, width, height = strona
    m = _otworz(page, "auto", width / 2, height / 2, DYMEK_AUTA)
    assert m["shift"] == 0
    assert m["side"] == "top"
    assert abs(m["centre"] - width / 2) < 2
    _strzalka_na_kropce(m, "auto", width / 2, height / 2)


def test_etykietka_przejazdu_rowerem_zostaje_w_jednej_linijce(strona):
    page, _, _ = strona
    zawijanie = page.evaluate("""() => {
        const marker = L.circleMarker([51.11, 17.03]).addTo(map)
            .bindTooltip('23:41 · 1,2 km', {permanent: true, direction: 'right',
                                            className: 'bike-ride-label'});
        return getComputedStyle(marker.getTooltip().getElement()).whiteSpace;
    }""")
    assert zawijanie == "nowrap"
