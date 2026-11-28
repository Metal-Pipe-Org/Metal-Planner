"""Klik w godzinę odjazdu wysyła odliczenie do PushWard tylko z kluczem i tylko
w trybie deweloperskim (zgłoszenie #453), sprawdzane w prawdziwej przeglądarce.
Trasa /api/dev/pushward jest atrapą - żadnej sieci ani prawdziwego klucza."""

import json
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import gtfs
from test_odswiezanie_pozycji import tryb_deweloperski  # noqa: F401
from tests.test_uklad_telefonu import PUSTE_ZRODLA, _chromium, _odpowiedz

KLUCZ = "hlk_testowy"
TABLICA = {"stop": "Pl. Grunwaldzki", "from_time": "22:00", "departures": [
    {"time": "22:57", "sec": 82620, "num": "5", "mode": "tram",
     "headsign": "Księże Małe", "trip": None, "stop_id": "1605"},
]}

TABLICA_JS = """(data) => {
    const body = document.getElementById('flow-panel-body');
    body.innerHTML = timetableLinesHtml(data);
    document.getElementById('flow-panel').hidden = false;
}"""


@pytest.fixture(scope="module")
def adres():
    """Serwer w trybie deweloperskim (tryb_deweloperski); test o tym, co jest
    bez niego, sam go zdejmuje na swój czas - serwer czyta tryb przy każdym
    zapytaniu."""
    try:
        gtfs.all_stop_names()
    except FileNotFoundError:
        pytest.skip("brak bazy rozkładów (data/gtfs.sqlite)")
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
    page, wyslane = _strona(przegladarka, adres, klucz=True)
    pytania = _kliknij_godzine(page, potwierdzaj=True)
    assert "5 → Księże Małe, 22:57" in pytania[0]
    assert wyslane[0][0] == KLUCZ
    assert wyslane[0][1]["sec"] == 82620
    assert len(pytania) == 1


def test_wysylka_niesie_link_do_biezacego_wyszukiwania_pod_publicznym_adresem(przegladarka, adres):
    page, wyslane = _strona(przegladarka, adres, klucz=True)
    # Strona stoi na 127.0.0.1, więc link ma iść na wdrożoną kopię, nie na localhost.
    page.evaluate("""() => {
        document.getElementById('start').value = 'Sosnowiecka';
        document.getElementById('end').value = 'Wojszyce';
    }""")
    _kliknij_godzine(page, potwierdzaj=True)
    assert wyslane[0][1]["link"] == "https://metal-testing.sze.one/?from=Sosnowiecka&to=Wojszyce"


def _link_z_klikniecia(przegladarka, adres, kurs, zewnetrzny):
    tablica = {**TABLICA, "departures": [{**TABLICA["departures"][0], **kurs}]}
    page, wyslane = _strona(przegladarka, adres, klucz=True, tablica=tablica)
    if zewnetrzny:
        page.evaluate("document.getElementById('pushward-link').checked = true")
    _kliknij_godzine(page, potwierdzaj=True)
    return wyslane[0][1]["link"]


@pytest.mark.parametrize("kurs, link", [
    ({"stop_id": "1605"}, "https://czynaczas.pl/wroclaw?przystanek=1605"),
    # Kurs gminy na słupku wspólnym z MPK (810 na Kościuszki): liczy kurs, nie słupek.
    ({"stop_id": "886", "siechnice_stop": "30307:54819"},
     "https://siechnice.kiedyprzyjedzie.pl/#/stops/30307:54819/departures"),
    ({"stop_id": "SIE:30329:54854", "siechnice_stop": "30329:54854"},
     "https://siechnice.kiedyprzyjedzie.pl/#/stops/30329:54854/departures"),
    # Słupka gminnego kursu nie ustalono: Czy na czas go nie zna, więc link do Metal-Plannera.
    ({"stop_id": "886", "siechnice_stop": None}, "https://metal-testing.sze.one/"),
])
def test_wlaczone_ustawienie_wysyla_link_do_przystanku(przegladarka, adres, kurs, link):
    wyslany = _link_z_klikniecia(przegladarka, adres, kurs, zewnetrzny=True)
    assert wyslany.startswith(link)


def test_wylaczone_ustawienie_zostawia_link_do_metal_plannera(przegladarka, adres):
    link = _link_z_klikniecia(przegladarka, adres, {"stop_id": "1605"}, zewnetrzny=False)
    assert link.startswith("https://metal-testing.sze.one/")


def test_odmowa_w_potwierdzeniu_nic_nie_wysyla(przegladarka, adres):
    page, wyslane = _strona(przegladarka, adres, klucz=True)
    _kliknij_godzine(page, potwierdzaj=False)
    assert wyslane == []


@pytest.mark.parametrize("dev", [True, False], ids=["dev", "bez-dev"])
def test_bez_klucza_nic_sie_nie_dzieje(przegladarka, adres, monkeypatch, dev):
    if not dev:
        monkeypatch.delenv("DEV_MODE")
    page, wyslane = _strona(przegladarka, adres, klucz=False)
    assert _kliknij_godzine(page, potwierdzaj=True) == []
    assert wyslane == []


def test_poza_trybem_dev_klucz_nic_nie_znaczy(przegladarka, adres, monkeypatch):
    monkeypatch.delenv("DEV_MODE")
    page, wyslane = _strona(przegladarka, adres, klucz=True)
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
    # Teraz 21:00: 20:59 minął, 21:01 i 04:59 w oknie, 05:01 dalej niż 8 h.
    page, _ = _strona(przegladarka, adres, klucz=True,
                      tablica=_tablica(20 * 3600 + 59 * 60, 21 * 3600 + 60,
                                       28 * 3600 + 59 * 60, 29 * 3600 + 60))
    assert page.locator("#flow-panel-body .tt-times > span").count() == 4
    assert page.locator("#flow-panel-body [data-dep-sec]").evaluate_all(
        "els => els.map(e => Number(e.dataset.depSec))") == [21 * 3600 + 60, 28 * 3600 + 59 * 60]
    assert _podkreslone(page) == ["underline", "underline"]


def test_odjazd_juz_miniony_nie_jest_klikalny(przegladarka, adres):
    page, wyslane = _strona(przegladarka, adres, klucz=True, teraz="2026-01-05T23:30:00+01:00")
    assert _klikalne(page) == 0
    page.click("#flow-panel-body .tt-times > span")
    page.wait_for_timeout(200)
    assert wyslane == []


def test_jutrzejszy_odjazd_w_oknie_jest_klikalny(przegladarka, adres):
    page, _ = _strona(przegladarka, adres, klucz=True, teraz="2026-01-05T23:00:00+01:00",
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
    page, _ = _strona(przegladarka, adres, klucz=False)
    page.click("#dev-toggle")
    page.fill("#settings-search", "PushWard")
    panel = page.locator("#dev-panel").bounding_box()
    przycisk = page.locator("#pushward-clear").bounding_box()
    assert przycisk["x"] + przycisk["width"] <= panel["x"] + panel["width"]


def test_zapis_poprawnego_klucza_pokazuje_zuzycie(przegladarka, adres):
    page, _ = _strona(przegladarka, adres, klucz=False,
                      sprawdzenie=(200, {"used": 12, "limit": 250}))
    status, zapisany = _zapisz(page, KLUCZ)
    assert status == "Włączone — wykorzystano 12 z 250 aktualizacji w tym miesiącu."
    assert zapisany == KLUCZ


def test_zapis_zlego_klucza_nic_nie_zapisuje(przegladarka, adres):
    page, _ = _strona(przegladarka, adres, klucz=False, sprawdzenie=(502, {"status": 401}))
    status, zapisany = _zapisz(page, KLUCZ)
    assert status == "Nieprawidłowy klucz."
    assert zapisany is None


def test_zapis_bez_polaczenia_nic_nie_zapisuje(przegladarka, adres):
    page, _ = _strona(przegladarka, adres, klucz=False, sprawdzenie=(502, {"status": 0}))
    status, zapisany = _zapisz(page, KLUCZ)
    assert "Nie dało się sprawdzić" in status
    assert zapisany is None


def test_zapisany_klucz_odswieza_licznik_przy_wejsciu(przegladarka, adres):
    page, _ = _strona(przegladarka, adres, klucz=True,
                      sprawdzenie=(200, {"used": 7, "limit": 250}))
    page.wait_for_function("document.getElementById('pushward-status').textContent.includes('7 z 250')")


def test_godzina_w_wierszu_niesie_slupek_wlasnego_kursu_a_nie_pierwszej_godziny_wiersza(przegladarka, adres):
    """810+81 z Radwanic: pierwsza godzina wiersza bez słupka nie może odebrać
    kolejnej rozpoznania kursu gminy (karta otwierała Czy na czas z pustym przystankiem)."""
    dev, url = adres
    if not dev:
        pytest.skip("sprawdzane w trybie dev")
    wiersz = {"num": "810+81", "mode": "bus", "headsign": "Bastion Sakwowy", "trip": None}
    tablica = {"stop": "Radwanice - ul. Skrajna", "from_time": "22:00", "departures": [
        {**wiersz, "time": "22:50", "sec": 82200},
        {**wiersz, "time": "22:57", "sec": 82620, "stop_id": "SIE:1:2", "siechnice_stop": "1:2"},
    ]}
    page, wyslane = _strona(przegladarka, url, klucz=True, tablica=tablica)
    page.evaluate("document.getElementById('pushward-link').checked = true")
    page.on("dialog", lambda d: d.accept())
    page.click("#flow-panel-body [data-dep-sec='82620']")
    page.wait_for_timeout(300)
    assert wyslane[0][1]["link"].startswith("https://siechnice.kiedyprzyjedzie.pl/#/stops/1:2/")
