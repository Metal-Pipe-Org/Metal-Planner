"""Klik w godzinę odjazdu wysyła odliczenie do PushWard tylko z kluczem i tylko
w trybie deweloperskim (zgłoszenie #453), sprawdzane w prawdziwej przeglądarce.
Trasa /api/dev/pushward jest atrapą - żadnej sieci ani prawdziwego klucza."""

import json
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import gtfs
from tests.test_uklad_telefonu import PUSTE_ZRODLA, _chromium, _odpowiedz

KLUCZ = "hlk_testowy"
TABLICA = {"stop": "Pl. Grunwaldzki", "from_time": "22:00", "departures": [
    {"time": "22:57", "sec": 82620, "num": "5", "mode": "tram",
     "headsign": "Księże Małe", "trip": None},
]}

TABLICA_JS = """(data) => {
    const body = document.getElementById('flow-panel-body');
    body.innerHTML = timetableLinesHtml(data);
    document.getElementById('flow-panel').hidden = false;
}"""


def _serwer(dev):
    import os
    from werkzeug.serving import make_server
    import app as aplikacja
    if dev:
        os.environ["DEV_MODE"] = "1"
    else:
        os.environ.pop("DEV_MODE", None)
    serwer = make_server("127.0.0.1", 0, aplikacja.app, threaded=True)
    threading.Thread(target=serwer.serve_forever, daemon=True).start()
    return serwer


@pytest.fixture(scope="module", params=[True, False], ids=["dev", "bez-dev"])
def adres(request):
    try:
        gtfs.all_stop_names()
    except FileNotFoundError:
        pytest.skip("brak bazy rozkładów (data/gtfs.sqlite)")
    serwer = _serwer(request.param)
    yield request.param, f"http://127.0.0.1:{serwer.server_port}/"
    serwer.shutdown()


@pytest.fixture(scope="module")
def przegladarka():
    with sync_api.sync_playwright() as p:
        browser = _chromium(p)
        yield browser
        browser.close()


def _strona(przegladarka, adres, klucz):
    context = przegladarka.new_context(service_workers="block")
    page = context.new_page()
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    wyslane = []

    def pushward(route):
        wyslane.append((route.request.headers.get("x-pushward-key"),
                        json.loads(route.request.post_data)))
        route.fulfill(status=200, content_type="application/json", body='{"sent": true}')

    page.route("**/api/dev/pushward", pushward)
    page.goto(adres)
    page.wait_for_selector("#start")
    if klucz:
        page.evaluate("k => localStorage.setItem('pushward-key', k)", KLUCZ)
        page.reload()
        page.wait_for_selector("#start")
    page.evaluate(TABLICA_JS, TABLICA)
    return page, wyslane


def _kliknij_godzine(page, potwierdzaj):
    pytania = []

    def dialog(d):
        pytania.append(d.message)
        d.accept() if potwierdzaj else d.dismiss()

    page.on("dialog", dialog)
    page.click("#flow-panel-body [data-dep-sec]")
    page.wait_for_timeout(300)
    return pytania


def test_z_kluczem_klik_pyta_i_wysyla(przegladarka, adres):
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    page, wyslane = _strona(przegladarka, url, klucz=True)
    pytania = _kliknij_godzine(page, potwierdzaj=True)
    assert "5 → Księże Małe, 22:57" in pytania[0]
    assert wyslane[0][0] == KLUCZ
    assert wyslane[0][1]["sec"] == 82620


def test_odmowa_w_potwierdzeniu_nic_nie_wysyla(przegladarka, adres):
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    page, wyslane = _strona(przegladarka, url, klucz=True)
    _kliknij_godzine(page, potwierdzaj=False)
    assert wyslane == []


def test_bez_klucza_nic_sie_nie_dzieje(przegladarka, adres):
    dev, url = adres
    page, wyslane = _strona(przegladarka, url, klucz=False)
    assert _kliknij_godzine(page, potwierdzaj=True) == []
    assert wyslane == []


def test_poza_trybem_dev_klucz_nic_nie_znaczy(przegladarka, adres):
    dev, url = adres
    if dev:
        pytest.skip("sprawdzane bez trybu dev")
    page, wyslane = _strona(przegladarka, url, klucz=True)
    assert _kliknij_godzine(page, potwierdzaj=True) == []
    assert wyslane == []
    assert not page.is_visible("#pushward-key")
