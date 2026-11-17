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


DZIEN = "2026-01-05"


def _strona(przegladarka, adres, klucz, teraz="2026-01-05T21:00:00+01:00", tablica=TABLICA,
            sprawdzenie=None):
    """`teraz` stoi w miejscu, żeby okno 8 h wokół odjazdu nie zależało od zegara.
    `sprawdzenie` to (status, body) atrapy /api/dev/pushward/check."""
    context = przegladarka.new_context(service_workers="block")
    page = context.new_page()
    page.clock.set_fixed_time(teraz)
    if sprawdzenie:
        page.route("**/api/dev/pushward/check", lambda route: route.fulfill(
            status=sprawdzenie[0], content_type="application/json",
            body=json.dumps(sprawdzenie[1])))
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
    page.evaluate("d => { document.getElementById('date').value = d; }", DZIEN)
    page.evaluate(TABLICA_JS, tablica)
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


def test_wysylka_niesie_link_do_biezacego_wyszukiwania_pod_publicznym_adresem(przegladarka, adres):
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    page, wyslane = _strona(przegladarka, url, klucz=True)
    # Strona stoi na 127.0.0.1, więc link ma iść na wdrożoną kopię, nie na localhost.
    page.evaluate("""() => {
        document.getElementById('start').value = 'Sosnowiecka';
        document.getElementById('end').value = 'Wojszyce';
    }""")
    _kliknij_godzine(page, potwierdzaj=True)
    assert wyslane[0][1]["link"] == "https://metal-testing.sze.one/?from=Sosnowiecka&to=Wojszyce"


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


def _klikalne(page):
    return page.locator("#flow-panel-body [data-dep-sec]").count()


def _podkreslone(page):
    return page.evaluate("""() => [...document.querySelectorAll('#flow-panel-body .tt-times > span[data-dep-sec]')]
        .map(e => getComputedStyle(e).textDecorationLine)""")


def _tablica(*sekundy):
    return {**TABLICA, "departures": [
        {"time": "x", "sec": s, "num": "5", "mode": "tram", "headsign": "Księże Małe", "trip": None}
        for s in sekundy]}


def test_podkreslone_tylko_odjazdy_w_oknie_osmiu_godzin(przegladarka, adres):
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    # Teraz 21:00: 20:59 minął, 21:01 i 04:59 w oknie, 05:01 dalej niż 8 h.
    page, _ = _strona(przegladarka, url, klucz=True,
                      tablica=_tablica(20 * 3600 + 59 * 60, 21 * 3600 + 60,
                                       28 * 3600 + 59 * 60, 29 * 3600 + 60))
    assert page.locator("#flow-panel-body .tt-times > span").count() == 4
    assert page.locator("#flow-panel-body [data-dep-sec]").evaluate_all(
        "els => els.map(e => Number(e.dataset.depSec))") == [21 * 3600 + 60, 28 * 3600 + 59 * 60]
    assert _podkreslone(page) == ["underline", "underline"]


def test_odjazd_juz_miniony_nie_jest_klikalny(przegladarka, adres):
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    page, wyslane = _strona(przegladarka, url, klucz=True, teraz="2026-01-05T23:30:00+01:00")
    assert _klikalne(page) == 0
    page.click("#flow-panel-body .tt-times > span")
    page.wait_for_timeout(200)
    assert wyslane == []


def test_jutrzejszy_odjazd_w_oknie_jest_klikalny(przegladarka, adres):
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    page, _ = _strona(przegladarka, url, klucz=True, teraz="2026-01-05T23:00:00+01:00",
                      tablica=_tablica(30 * 3600))
    assert _klikalne(page) == 1


def _zapisz(page, klucz):
    page.click("#dev-toggle")
    # Wiersz leży w zwiniętej grupie „Eksperymenty”; użytkownik znajduje go szukajką.
    page.fill("#settings-search", "PushWard")
    page.fill("#pushward-key", klucz)
    page.click("#pushward-save")
    page.wait_for_function("!document.getElementById('pushward-status').textContent.startsWith('Sprawdzanie')")
    return (page.inner_text("#pushward-status"),
            page.evaluate("localStorage.getItem('pushward-key')"))


def test_przyciski_klucza_mieszcza_sie_w_panelu_ustawien(przegladarka, adres):
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    page, _ = _strona(przegladarka, url, klucz=False)
    page.click("#dev-toggle")
    page.fill("#settings-search", "PushWard")
    panel = page.locator("#dev-panel").bounding_box()
    przycisk = page.locator("#pushward-clear").bounding_box()
    assert przycisk["x"] + przycisk["width"] <= panel["x"] + panel["width"]


def test_zapis_poprawnego_klucza_pokazuje_zuzycie(przegladarka, adres):
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    page, _ = _strona(przegladarka, url, klucz=False,
                      sprawdzenie=(200, {"used": 12, "limit": 250}))
    status, zapisany = _zapisz(page, KLUCZ)
    assert status == "Włączone — wykorzystano 12 z 250 aktualizacji w tym miesiącu."
    assert zapisany == KLUCZ


def test_zapis_zlego_klucza_nic_nie_zapisuje(przegladarka, adres):
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    page, _ = _strona(przegladarka, url, klucz=False, sprawdzenie=(502, {"status": 401}))
    status, zapisany = _zapisz(page, KLUCZ)
    assert status == "Nieprawidłowy klucz."
    assert zapisany is None


def test_zapis_bez_polaczenia_nic_nie_zapisuje(przegladarka, adres):
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    page, _ = _strona(przegladarka, url, klucz=False, sprawdzenie=(502, {"status": 0}))
    status, zapisany = _zapisz(page, KLUCZ)
    assert "Nie dało się sprawdzić" in status
    assert zapisany is None


def test_zapisany_klucz_odswieza_licznik_przy_wejsciu(przegladarka, adres):
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    page, _ = _strona(przegladarka, url, klucz=True,
                      sprawdzenie=(200, {"used": 7, "limit": 250}))
    page.wait_for_function("document.getElementById('pushward-status').textContent.includes('7 z 250')")
