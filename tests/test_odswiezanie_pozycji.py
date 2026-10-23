"""Pozycję da się odświeżać co 30 s (zgłoszenie #267).

◎ wpisuje sam start i nie dotyka godziny ani daty: nieruszana jest „teraz" przy
każdym szukaniu, wpisana ręcznie zostaje. Drugie kliknięcie ◎, gdy start już jest moją pozycją, włącza (i
gasi) odświeżanie: co 30 s ◎ czyta pozycję od nowa, ustawia „teraz" i
przelicza trasę. Włączone ◎ świeci.
Zegar w teście jest podstawiony (page.clock), więc nikt nie czeka pół minuty.

Ta sama przeglądarka i ten sam serwer co test układu telefonu - i tak samo
pomija się bez playwright albo bez bazy rozkładów.
"""

import datetime
import json
import os

import pytest

import gtfs
from test_uklad_telefonu import (PUSTE_ZRODLA, TRASA, _odpowiedz, _wybierz,  # noqa: F401
                                 adres, przegladarka)

TERAZ_JS = "(() => { const d = new Date(); return d.toTimeString().slice(0, 5); })()"
DATA_JS = ("(() => { const d = new Date(); return d.getFullYear() + '-'"
           " + String(d.getMonth() + 1).padStart(2, '0') + '-'"
           " + String(d.getDate()).padStart(2, '0'); })()")
UKRYJ_JS = """hidden => {
    Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
    document.dispatchEvent(new Event('visibilitychange'));
}"""
USTAW_JS = """([id, value]) => { document.getElementById(id).value = value; }"""


@pytest.fixture(scope="module", autouse=True)
def tryb_deweloperski():
    """Przełącznik odświeżania siedzi w sekcji DEV (#379) - bez DEV_MODE nie działa."""
    stare = os.environ.get("DEV_MODE")
    os.environ["DEV_MODE"] = "1"
    yield
    if stare is None:
        del os.environ["DEV_MODE"]
    else:
        os.environ["DEV_MODE"] = stare


def _otworz(przegladarka, adres, przelacznik=True, **opcje):
    slupek = next(s for s in gtfs.all_stops_geo() if s["name"] == TRASA[0])
    context = przegladarka.new_context(
        viewport={"width": 390, "height": 844}, device_scale_factor=2,
        is_mobile=True, has_touch=True, locale="pl-PL", service_workers="block",
        permissions=["geolocation"],
        geolocation={"latitude": slupek["lat"], "longitude": slupek["lon"]}, **opcje)
    page = context.new_page()
    # Południe dzisiaj: trasa ma zawsze czym jechać, niezależnie od pory,
    # o której leci test. Czas płynie dalej, a run_for przeskakuje o resztę.
    page.clock.install(time=datetime.datetime.combine(
        datetime.date.today(), datetime.time(12, 0)))
    bledy = []
    page.on("pageerror", lambda e: bledy.append(str(e)))
    page.add_init_script(
        "localStorage.setItem('metal-planner:settings', %s)"
        % json.dumps(json.dumps({"location-refresh": przelacznik})))
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres)
    page.wait_for_selector("#start")
    page.wait_for_load_state("networkidle")
    return context, page, bledy


def _minuty_temu(page, rano):
    """Godzina w polu to „teraz" z dokładnością do minuty - test mógł trafić
    na przełom minuty między kliknięciem a odczytem."""
    po = page.evaluate(TERAZ_JS)
    return rano in (po, _poprzednia(po))


def _poprzednia(godzina):
    h, m = map(int, godzina.split(":"))
    t = (datetime.datetime(2000, 1, 1, h, m) - datetime.timedelta(minutes=1))
    return t.strftime("%H:%M")


def _swieci(page):
    return "active" in page.get_attribute("#locate", "class").split()


def _przelacz_odswiezanie(page):
    """Druga strona ◎: po wyszukaniu telefon zwija kartę, więc najpierw ją
    rozwija."""
    if page.is_visible(".search-summary"):
        page.click(".search-summary")
    page.click("#locate")


def _wlacz_odswiezanie(page):
    """Włączenie odświeża od razu - czeka na to zapytanie, żeby test liczył
    dopiero te z zegara."""
    with page.expect_request(lambda r: "/api/flow" in r.url, timeout=20000):
        _przelacz_odswiezanie(page)
    page.wait_for_selector("body:not(.searching)", state="attached")
    page.wait_for_timeout(500)


def _z_trasa_od_pozycji(page):
    """Cel wpisany przy pustym „Skąd" robi to samo co ◎ (#275) - szukanie
    rusza samo, start to „moja pozycja"."""
    _wybierz(page, "#end", TRASA[1])
    page.wait_for_selector("#locate-offer", state="visible")
    page.wait_for_selector("body:not(.searching) .journey", state="attached",
                           timeout=20000)


def test_moja_pozycja_nie_rusza_recznej_godziny_i_daty(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    page.fill("#time", "03:33")
    page.evaluate("""() => { const d = document.getElementById('date');
        d.value = '2030-01-02'; d.dispatchEvent(new Event('input')); }""")
    page.click("#locate")
    page.wait_for_selector("#locate-offer", state="visible")
    assert page.input_value("#start")
    assert page.input_value("#time") == "03:33"
    assert page.input_value("#date") == "2030-01-02"

    # Odświeżanie też odświeża tylko pozycję.
    _przelacz_odswiezanie(page)
    page.clock.run_for(30000)
    page.wait_for_timeout(500)
    assert page.input_value("#time") == "03:33"
    assert page.input_value("#date") == "2030-01-02"
    context.close()
    assert not bledy


def test_moja_pozycja_z_celem_szuka_od_teraz_gdy_godziny_nie_wpisano(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    page.evaluate(USTAW_JS, ["time", "03:33"])
    _z_trasa_od_pozycji(page)
    assert _minuty_temu(page, page.input_value("#time"))
    assert page.input_value("#date") == page.evaluate(DATA_JS)
    context.close()
    assert not bledy


def test_pierwsze_klikniecie_nie_wlacza_odswiezania(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    assert not _swieci(page)
    page.click("#locate")
    page.wait_for_selector("#locate-offer", state="visible")
    assert not _swieci(page)
    assert page.get_attribute("#locate", "title") == "Odświeżaj co 30 s"

    _przelacz_odswiezanie(page)
    assert _swieci(page)
    assert page.get_attribute("#locate", "title") == "Wyłącz odświeżanie"
    _przelacz_odswiezanie(page)
    assert not _swieci(page)
    assert page.get_attribute("#locate", "title") == "Odświeżaj co 30 s"
    context.close()
    assert not bledy


def test_wlaczone_nie_przetrwa_odswiezenia_strony(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    page.click("#locate")
    page.wait_for_selector("#locate-offer", state="visible")
    _przelacz_odswiezanie(page)
    assert _swieci(page)
    page.reload()
    page.wait_for_selector("#start")
    assert not _swieci(page)
    context.close()
    assert not bledy


def test_wlaczone_odswieza_od_razu_i_co_30_s_z_godzina_teraz(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    zapytania = []
    page.on("request", lambda r: zapytania.append(r.url) if "/api/flow" in r.url else None)
    _z_trasa_od_pozycji(page)
    page.click('#view-tabs [data-view="list"]')
    page.click(".journey")
    page.wait_for_selector(".journey.selected")

    page.evaluate(USTAW_JS, ["time", "03:33"])
    _wlacz_odswiezanie(page)
    assert page.input_value("#time") != "03:33"

    page.evaluate(USTAW_JS, ["time", "03:33"])
    zapytania.clear()
    with page.expect_request(lambda r: "/api/flow" in r.url, timeout=20000):
        page.clock.run_for(30000)
    page.wait_for_selector("body:not(.searching)", state="attached")
    assert len(zapytania) == 1
    assert f"time={page.input_value('#time').replace(':', '%3A')}" in zapytania[0]
    assert page.input_value("#time") != "03:33"
    assert page.input_value("#date") == page.evaluate(DATA_JS)
    # Wybrana propozycja przetrwała przeliczenie (ten sam kurs jest w wyniku).
    page.wait_for_selector(".journey.selected", state="attached", timeout=20000)
    context.close()
    assert not bledy


def test_ukryta_karta_wstrzymuje_a_widoczna_wznawia(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    zapytania = []
    page.on("request", lambda r: zapytania.append(r.url) if "/api/flow" in r.url else None)
    _z_trasa_od_pozycji(page)
    _wlacz_odswiezanie(page)

    page.evaluate(UKRYJ_JS, True)
    zapytania.clear()
    page.clock.run_for(90000)
    page.wait_for_timeout(500)
    assert zapytania == []
    assert _swieci(page)

    page.evaluate(UKRYJ_JS, False)
    with page.expect_request(lambda r: "/api/flow" in r.url, timeout=20000):
        page.clock.run_for(30000)
    context.close()
    assert not bledy


def test_wylaczone_przestaje_przeliczac(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    zapytania = []
    page.on("request", lambda r: zapytania.append(r.url) if "/api/flow" in r.url else None)
    _z_trasa_od_pozycji(page)
    _wlacz_odswiezanie(page)
    _przelacz_odswiezanie(page)
    assert not _swieci(page)
    zapytania.clear()
    page.clock.run_for(90000)
    page.wait_for_timeout(500)
    assert zapytania == []
    context.close()
    assert not bledy


def test_zwinieta_karta_ma_wlasne_kolko_tylko_przy_mojej_pozycji(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    zapytania = []
    page.on("request", lambda r: zapytania.append(r.url) if "/api/flow" in r.url else None)
    # Start przed celem: cel przy pustym „Skąd" sam by użył ◎ (#275).
    _wybierz(page, "#start", TRASA[0])
    _wybierz(page, "#end", TRASA[1])
    page.wait_for_selector(".search-summary", state="visible", timeout=20000)
    assert not page.is_visible("#locate-peek")

    page.click(".search-summary")
    page.click("#locate")
    page.wait_for_selector("#locate-offer", state="visible")
    page.wait_for_selector("body:not(.searching) .journey", state="attached",
                           timeout=20000)
    page.wait_for_selector(".search-summary", state="visible")
    wysokosc = page.evaluate("document.querySelector('.search-card').offsetHeight")
    assert page.is_visible("#locate-peek")
    assert page.evaluate("document.querySelector('.search-card').offsetHeight") == wysokosc
    assert not _swieci(page)
    assert page.get_attribute("#locate-peek", "title") == "Odświeżaj co 30 s"

    with page.expect_request(lambda r: "/api/flow" in r.url, timeout=20000):
        page.click("#locate-peek")
    page.wait_for_selector("body:not(.searching)", state="attached")
    page.wait_for_timeout(500)
    assert "active" in page.get_attribute("#locate-peek", "class").split()
    assert page.get_attribute("#locate-peek", "title") == "Wyłącz odświeżanie"
    assert page.is_visible(".search-summary")
    zapytania.clear()
    with page.expect_request(lambda r: "/api/flow" in r.url, timeout=20000):
        page.clock.run_for(30000)

    page.click("#locate-peek")
    assert "active" not in page.get_attribute("#locate-peek", "class").split()
    page.wait_for_timeout(500)
    zapytania.clear()
    page.clock.run_for(90000)
    page.wait_for_timeout(500)
    assert zapytania == []
    context.close()
    assert not bledy


def test_zmiana_startu_na_przystanek_gasi_odswiezanie(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    zapytania = []
    page.on("request", lambda r: zapytania.append(r.url) if "/api/flow" in r.url else None)
    _z_trasa_od_pozycji(page)
    _wlacz_odswiezanie(page)
    _wybierz(page, "#start", TRASA[0])
    page.wait_for_selector("body:not(.searching)", state="attached")
    assert not _swieci(page)
    zapytania.clear()
    page.clock.run_for(90000)
    page.wait_for_timeout(500)
    assert zapytania == []
    context.close()
    assert not bledy


OPOZNIONY_GPS_JS = """() => {
    navigator.geolocation.getCurrentPosition = (ok, fail) => {
        window.__gps = {ok, fail};
    };
}"""


@pytest.mark.parametrize("wynik", ["pozycja", "blad"])
def test_spozniony_gps_nie_nadpisuje_startu_wpisanego_w_miedzyczasie(
        przegladarka, adres, wynik):
    context, page, bledy = _otworz(przegladarka, adres)
    page.evaluate(OPOZNIONY_GPS_JS)
    page.fill("#time", "03:33")      # wpisana przez pasażera, a nie ustawiona przez stronę
    _wybierz(page, "#end", TRASA[1])
    page.wait_for_function("window.__gps")
    _wybierz(page, "#start", TRASA[0])
    page.wait_for_selector("body:not(.searching) .journey", state="attached",
                           timeout=20000)
    if wynik == "pozycja":
        page.evaluate("window.__gps.ok({coords: {latitude: 51.1, longitude: 17.03}})")
    else:
        page.evaluate("window.__gps.fail({code: 1})")
    page.wait_for_timeout(500)
    assert page.input_value("#start") == TRASA[0]
    assert page.input_value("#time") == "03:33"
    assert not page.is_visible("#locate-offer")
    assert not page.is_visible("#locate-msg")
    assert page.is_enabled("#locate")
    context.close()
    assert not bledy


def test_przelacznik_wylaczony_drugie_kolko_nie_wlacza_odswiezania(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres, przelacznik=False)
    zapytania = []
    page.on("request", lambda r: zapytania.append(r.url) if "/api/flow" in r.url else None)
    _z_trasa_od_pozycji(page)
    start = page.input_value("#start")
    _przelacz_odswiezanie(page)
    page.wait_for_selector("body:not(.searching)", state="attached")
    assert page.input_value("#start") == start
    assert not _swieci(page)
    assert page.get_attribute("#locate", "title") == "Użyj mojej lokalizacji"
    # Drugie ◎ szuka od nowa jak pierwsze - liczą się dopiero zapytania z zegara.
    page.wait_for_timeout(1000)
    zapytania.clear()
    page.clock.run_for(90000)
    page.wait_for_timeout(500)
    assert zapytania == []
    assert not page.is_visible("#locate-peek")
    context.close()
    assert not bledy


def test_przelacznik_zapisany_bez_trybu_deweloperskiego_nie_dziala(przegladarka, adres, monkeypatch):
    monkeypatch.delenv("DEV_MODE", raising=False)
    context, page, bledy = _otworz(przegladarka, adres)
    assert not page.evaluate("document.getElementById('location-refresh').checked")
    page.click("#locate")
    page.wait_for_selector("#locate-offer", state="visible")
    _przelacz_odswiezanie(page)
    assert not _swieci(page)
    context.close()
    assert not bledy
