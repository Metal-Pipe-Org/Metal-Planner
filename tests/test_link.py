"""Wyszukiwanie w adresie strony (zgłoszenie #283), sprawdzane w PRAWDZIWEJ
przeglądarce: ?from=...&to=...&time=...&date=... wypełnia formularz i od razu
szuka, a po każdym wyszukaniu ten sam adres leży w pasku przeglądarki.

Trasa jest liczona na prawdziwej bazie rozkładów; żywe źródła dostają puste
listy. Pomija się bez playwright, przeglądarki albo bazy rozkładów.
"""

import json
import threading
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import gtfs
from tests.test_uklad_telefonu import PUSTE_ZRODLA, _chromium, _odpowiedz, _wybierz

TRASA = ("Sosnowiecka", "Wojszyce")
KLUCZ_OSTATNIEJ = "metal-planner:last-search"


@pytest.fixture(scope="module")
def adres():
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


@pytest.fixture
def otworz(przegladarka):
    konteksty = []

    def _otworz(url, wczesniej=None, **opcje):
        context = przegladarka.new_context(
            viewport={"width": 1280, "height": 800}, locale="pl-PL",
            service_workers="block", **opcje)
        konteksty.append(context)
        page = context.new_page()
        page.bledy = []
        page.zapytania = []
        page.on("pageerror", lambda e: page.bledy.append(str(e)))
        page.on("request", lambda r: page.zapytania.append(r.url)
                if "/api/flow" in r.url else None)
        for wzor, odpowiedz in PUSTE_ZRODLA.items():
            page.route(wzor, _odpowiedz(odpowiedz))
        page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
        if wczesniej:
            page.add_init_script(wczesniej)
        page.goto(url)
        page.wait_for_selector("#start")
        return page

    yield _otworz
    for context in konteksty:
        context.close()


def _parametry_adresu(page):
    return {k: v[0] for k, v in parse_qs(urlparse(page.url).query).items()}


def _zapytanie(page):
    return {k: v[0] for k, v in parse_qs(urlparse(page.zapytania[-1]).query).items()}


def test_link_wypelnia_formularz_i_szuka(adres, otworz):
    jutro = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    page = otworz(f"{adres}?from=Sosnowiecka&to=Wojszyce&time=8:00&date={jutro}")
    page.wait_for_selector(".journey")
    assert page.input_value("#time") == "08:00"
    assert page.input_value("#date") == jutro
    zapytanie = _zapytanie(page)
    assert (zapytanie["start"], zapytanie["end"]) == TRASA
    assert (zapytanie["time"], zapytanie["date"]) == ("08:00", jutro)
    assert not page.bledy


def test_adres_po_wyszukaniu_niesie_trase(adres, otworz):
    page = otworz(adres)
    assert page.url == adres
    _wybierz(page, "#start", TRASA[0])
    _wybierz(page, "#end", TRASA[1])
    page.wait_for_selector(".journey")
    parametry = _parametry_adresu(page)
    assert (page.input_value("#start"), page.input_value("#end")) == (
        parametry["from"], parametry["to"])
    page.click("#clear")
    assert page.url == adres


def test_teraz_zostaje_teraz_a_wybrana_godzina_wraca(adres, otworz):
    page = otworz(adres)
    _wybierz(page, "#start", TRASA[0])
    _wybierz(page, "#end", TRASA[1])
    page.wait_for_selector(".journey")
    parametry = _parametry_adresu(page)
    assert "time" not in parametry and "date" not in parametry

    page.fill("#time", "17:40")
    page.click("#search")
    page.wait_for_selector(".journey")
    assert _parametry_adresu(page)["time"] == "17:40"

    # Z linku bez godziny odbiorca dostaje swoje „teraz", nie cudzą godzinę.
    odbiorca = otworz(f"{adres}?from=Sosnowiecka&to=Wojszyce")
    odbiorca.wait_for_selector(".journey")
    assert "time" not in _parametry_adresu(odbiorca)
    teraz = datetime.now()
    godzina = datetime.strptime(odbiorca.input_value("#time"), "%H:%M")
    minuty = abs(godzina.hour * 60 + godzina.minute - (teraz.hour * 60 + teraz.minute))
    assert min(minuty, 1440 - minuty) <= 2

    # ...a wybrana godzina po otwarciu linku wraca do kolejnego linku.
    z_godzina = otworz(f"{adres}?from=Sosnowiecka&to=Wojszyce&time=17:40")
    z_godzina.wait_for_selector(".journey")
    z_godzina.click("#search")
    z_godzina.wait_for_selector(".journey")
    assert _parametry_adresu(z_godzina)["time"] == "17:40"


def test_data_w_linku_pokazuje_panel_daty(adres, otworz):
    jutro = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    page = otworz(f"{adres}?from=Sosnowiecka&to=Wojszyce&date={jutro}")
    page.wait_for_selector(".journey")
    assert page.is_visible("#date")
    assert _parametry_adresu(page)["date"] == jutro
    # Wybrana data robi z godziny wybraną (ta sama zasada co w formularzu).
    assert _parametry_adresu(page)["time"] == page.input_value("#time")


def test_link_nie_rusza_ostatniej_trasy(adres, otworz):
    ostatnia = json.dumps({"start": "Katedra", "end": "Wojszyce", "savedAt": 1,
                           "time": "07:30", "date": "2026-10-22"})
    page = otworz(
        f"{adres}?from=Sosnowiecka&to=Wojszyce&time=9:15",
        wczesniej=f"localStorage.setItem('{KLUCZ_OSTATNIEJ}', {json.dumps(ostatnia)})")
    page.wait_for_selector(".journey")
    assert not page.is_visible("#recall-offer")
    assert json.loads(page.evaluate(
        f"localStorage.getItem('{KLUCZ_OSTATNIEJ}')")) == json.loads(ostatnia)


POZYCJA = {"permissions": ["geolocation"],
           "geolocation": {"latitude": 51.0848, "longitude": 17.0645}}


def test_from_gps_szuka_z_pozycji_odbiorcy(adres, otworz):
    page = otworz(f"{adres}?from=gps&to=Wojszyce", **POZYCJA)
    page.wait_for_selector(".journey")
    assert page.input_value("#end") != ""
    assert page.input_value("#start").startswith("(51.08")
    # Pozycja odbiorcy nie ląduje w adresie - zostaje samo "gps".
    assert _parametry_adresu(page)["from"] == "gps"


def test_brak_from_zostawia_pole_puste_i_nie_szuka(adres, otworz):
    page = otworz(f"{adres}?to=Wojszyce", **POZYCJA)
    page.wait_for_selector("#end")
    assert page.input_value("#end") != ""
    assert page.input_value("#start") == ""
    assert page.evaluate("document.activeElement.id") == "start"
    page.wait_for_timeout(1500)
    assert page.zapytania == []


def test_brak_to_zostawia_pole_puste_i_nie_szuka(adres, otworz):
    page = otworz(f"{adres}?from=Sosnowiecka")
    page.wait_for_selector("#start")
    assert page.input_value("#start") == "Sosnowiecka"
    assert page.input_value("#end") == ""
    assert page.evaluate("document.activeElement.id") == "end"
    page.wait_for_timeout(1500)
    assert page.zapytania == []


def test_to_gps_zostawia_cel_pusty(adres, otworz):
    page = otworz(f"{adres}?from=Sosnowiecka&to=gps", **POZYCJA)
    page.wait_for_selector("#start")
    assert page.input_value("#end") == ""
    page.wait_for_timeout(1000)
    assert page.zapytania == []


def test_start_z_mojej_pozycji_to_from_gps_w_adresie(adres, otworz):
    page = otworz(adres, **POZYCJA)
    page.click("#locate")
    page.wait_for_function("document.getElementById('start').value.startsWith('(')")
    _wybierz(page, "#end", TRASA[1])
    page.wait_for_selector(".journey")
    parametry = _parametry_adresu(page)
    assert parametry["from"] == "gps" and "to" in parametry


def test_link_z_gps_nie_rusza_ostatniej_trasy(adres, otworz):
    ostatnia = json.dumps({"start": "Katedra", "end": "Wojszyce", "savedAt": 1})
    page = otworz(
        f"{adres}?from=gps&to=Wojszyce",
        wczesniej=f"localStorage.setItem('{KLUCZ_OSTATNIEJ}', {json.dumps(ostatnia)})",
        **POZYCJA)
    page.wait_for_selector(".journey")
    assert json.loads(page.evaluate(
        f"localStorage.getItem('{KLUCZ_OSTATNIEJ}')")) == json.loads(ostatnia)


def test_zapisane_miejsce_idzie_do_adresu_jak_kazdy_punkt(adres, otworz):
    dom = json.dumps([{"icon": "🏠", "place": {"lat": 51.1, "lon": 17.03}}])
    page = otworz(
        f"{adres}?from=Sosnowiecka&to=51.10000,17.03000",
        wczesniej=f"localStorage.setItem('metal-planner:my-places', {json.dumps(dom)})")
    page.wait_for_selector(".journey")
    assert _parametry_adresu(page) == {"from": "Sosnowiecka", "to": "51.10000,17.03000"}


def test_przycisk_udostepniania_jest_zawsze_a_bez_trasy_dzieli_adres_aplikacji(adres, otworz):
    page = otworz(
        adres, wczesniej="window.udostepnione = null;"
                         "navigator.share = async data => { window.udostepnione = data; }")
    assert page.is_visible("#share")
    page.click("#share")
    assert page.evaluate("window.udostepnione")["url"] == adres
    _wybierz(page, "#start", TRASA[0])
    _wybierz(page, "#end", TRASA[1])
    page.wait_for_selector(".journey")
    page.click("#clear")
    assert page.is_visible("#share")


def test_udostepnianie_bez_web_share_kopiuje_aktualny_link(adres, otworz):
    page = otworz(
        f"{adres}?from=Sosnowiecka&to=Wojszyce&time=8:00",
        wczesniej="Object.defineProperty(navigator, 'share', {value: undefined})",
        permissions=["clipboard-read", "clipboard-write"])
    page.wait_for_selector(".journey")
    page.click("#share")
    page.wait_for_function("document.getElementById('share').title === 'Skopiowano'")
    skopiowany = page.evaluate("navigator.clipboard.readText()")
    assert skopiowany == page.url
    assert _parametry_adresu(page)["time"] == "08:00"
    page.wait_for_function("document.getElementById('share').title !== 'Skopiowano'")


def test_udostepnianie_przez_web_share(adres, otworz):
    page = otworz(
        f"{adres}?from=Sosnowiecka&to=Wojszyce",
        wczesniej="window.udostepnione = null;"
                  "navigator.share = async data => { window.udostepnione = data; }")
    page.wait_for_selector(".journey")
    page.click("#share")
    udostepnione = page.evaluate("window.udostepnione")
    assert udostepnione == {"title": "Metal Planner", "url": page.url}


def _meta(html, nazwa):
    import re
    from html import unescape
    znaleziony = re.search(
        rf'<meta (?:property|name)="{nazwa}" content="([^"]*)">', html)
    return unescape(znaleziony.group(1)) if znaleziony else None


def test_podglad_linku_z_parametrow():
    import app as aplikacja
    klient = aplikacja.app.test_client()
    html = klient.get("/?from=Katedra&to=Wojszyce&time=8:00&date=2026-10-22").get_data(as_text=True)
    assert _meta(html, "og:title") == "Katedra → Wojszyce"
    assert _meta(html, "og:description") == "22.10 · 8:00"
    assert _meta(html, "twitter:card") == "summary"
    assert "<title>Katedra → Wojszyce — Metal Planner</title>" in html

    teraz = klient.get("/?from=Katedra&to=Wojszyce").get_data(as_text=True)
    assert _meta(teraz, "og:description") is None
    assert _meta(teraz, "og:title") == "Katedra → Wojszyce"


def test_podglad_linku_z_lokalizacji():
    import app as aplikacja
    klient = aplikacja.app.test_client()
    html = klient.get("/?from=gps&to=Wojszyce").get_data(as_text=True)
    assert _meta(html, "og:title") == "Z Twojej lokalizacji → Wojszyce"
    bez_celu = klient.get("/?from=Katedra&to=gps").get_data(as_text=True)
    assert _meta(bez_celu, "og:title") == "Trasa z: Katedra"


def test_podglad_linku_ucieka_znaki():
    import app as aplikacja
    html = aplikacja.app.test_client().get(
        '/?from="><script>alert(1)</script>&to=A%26B').get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html.split("</head>")[0]
    assert _meta(html, "og:title") == '"><script>alert(1)</script> → A&B'


def test_bez_parametrow_strona_bez_podgladu():
    import app as aplikacja
    html = aplikacja.app.test_client().get("/").get_data(as_text=True)
    assert _meta(html, "og:title") is None
    assert "<title>Metal Planner — Wrocław</title>" in html
