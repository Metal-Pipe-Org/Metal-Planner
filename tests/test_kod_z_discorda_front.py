"""Panel ⚙ blokuje wgrywanie Siechnic do czasu kodu (zgłoszenie #433), sprawdzane
w prawdziwej przeglądarce. Trasy /api/dev/* są atrapami, bez Discorda."""

import json
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import gtfs
from tests.test_uklad_telefonu import PUSTE_ZRODLA, _chromium, _odpowiedz

ZABLOKOWANE = {"uploaded_at": None, "rebuilding": False, "in_database": False, "unlocked": False}


@pytest.fixture(scope="module")
def adres():
    try:
        gtfs.all_stop_names()
    except FileNotFoundError:
        pytest.skip("brak bazy rozkładów (data/gtfs.sqlite)")
    import os
    os.environ["DEV_MODE"] = "1"
    from werkzeug.serving import make_server
    import app as aplikacja
    serwer = make_server("127.0.0.1", 0, aplikacja.app, threaded=True)
    threading.Thread(target=serwer.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{serwer.server_port}/"
    serwer.shutdown()
    del os.environ["DEV_MODE"]


@pytest.fixture(scope="module")
def przegladarka():
    with sync_api.sync_playwright() as p:
        browser = _chromium(p)
        yield browser
        browser.close()


def _strona(przegladarka, kod_info, siechnice):
    context = przegladarka.new_context(service_workers="block")
    page = context.new_page()
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.route("**/api/dev/kod", _odpowiedz(kod_info))
    page.route("**/api/dev/siechnice", siechnice)
    return context, page


def _otworz_panel(page):
    page.wait_for_selector("#dev-toggle")
    if not page.evaluate("document.body.classList.contains('dev-open')"):
        page.click("#dev-toggle")
    page.wait_for_selector("#dev-panel", state="visible")
    # Wiersze ustawień siedzą w zwijanych grupach.
    page.evaluate("document.querySelector('#siechnice-code').closest('details').open = true")


def test_zablokowane_pokazuje_liste_osob_i_kladke_przy_wgrywaniu(przegladarka, adres):
    context, page = _strona(przegladarka, {"available": True, "people": ["Ala", "Olek"]},
                            _odpowiedz(ZABLOKOWANE))
    try:
        page.goto(adres)
        page.wait_for_function("document.querySelector('#siechnice-person').options.length === 2")
        _otworz_panel(page)
        assert page.locator("#access-lock").evaluate("e => !e.hidden")
        assert page.locator("#siechnice-file").is_disabled()
        assert page.locator("[data-protected]").evaluate("e => e.classList.contains('locked')")
        assert "\U0001F512" in page.locator("[data-protected]").evaluate(
            "e => getComputedStyle(e, '::before').content")
        assert page.locator("#siechnice-person option").evaluate_all("o => o.map(e => e.textContent)") == ["Ala", "Olek"]
        assert page.locator("#siechnice-send").inner_text() == "Wyślij kod"
    finally:
        context.close()


def test_klik_w_kladke_przenosi_do_wiersza_dostepu(przegladarka, adres):
    context, page = _strona(przegladarka, {"available": True, "people": ["Ala"]},
                            _odpowiedz(ZABLOKOWANE))
    try:
        page.goto(adres)
        page.wait_for_function("document.querySelector('#siechnice-person').options.length === 1")
        _otworz_panel(page)
        page.fill("#settings-search", "Siechnic")
        page.click("[data-protected]", force=True)
        page.wait_for_function(
            "document.querySelector('[data-setting=dev-access]').classList.contains('jump-flash')")
        assert page.input_value("#settings-search") == ""
        assert page.locator("[data-setting=dev-access]").evaluate("e => e.closest('details').open")
        assert page.locator("[data-setting=dev-access]").is_visible()
    finally:
        context.close()


def test_bez_konfiguracji_panel_mowi_ze_nie_da_sie_wyslac(przegladarka, adres):
    context, page = _strona(przegladarka, {"available": False, "people": []},
                            _odpowiedz(ZABLOKOWANE))
    try:
        page.goto(adres)
        page.wait_for_function(
            "document.querySelector('#access-status').textContent.includes('brak konfiguracji')")
        assert page.locator("#siechnice-send").is_disabled()
    finally:
        context.close()


def test_odblokowana_karta_nie_ma_kladki(przegladarka, adres):
    context, page = _strona(przegladarka, {"available": True, "people": ["Ala"]},
                            _odpowiedz({**ZABLOKOWANE, "unlocked": True}))
    try:
        page.goto(adres)
        page.wait_for_function("!document.querySelector('#siechnice-file').disabled")
        assert not page.locator("[data-protected]").evaluate("e => e.classList.contains('locked')")
        assert page.locator("#access-lock").evaluate("e => e.hidden")
        assert page.locator("#access-unlocked").evaluate("e => !e.hidden")
    finally:
        context.close()


def test_poprawny_kod_zapisuje_token_w_sessionstorage_i_zdejmuje_kladke(przegladarka, adres):
    def siechnice(route):
        odblokowana = route.request.headers.get("x-dev-token") == "tok"
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({**ZABLOKOWANE, "unlocked": odblokowana}))

    context, page = _strona(przegladarka, {"available": True, "people": ["Ala"]}, siechnice)
    page.route("**/api/dev/odblokuj", _odpowiedz({"token": "tok"}))
    try:
        page.goto(adres)
        page.wait_for_function("document.querySelector('#siechnice-person').options.length === 1")
        _otworz_panel(page)
        page.fill("#siechnice-code", "123456")
        assert not page.locator("[data-setting=dev-access]").evaluate("e => e.classList.contains('changed')")
        page.click("#siechnice-unlock")
        page.wait_for_function("!document.querySelector('#siechnice-file').disabled")
        assert not page.locator("[data-protected]").evaluate("e => e.classList.contains('locked')")
        assert page.evaluate("sessionStorage.getItem('siechnice-token')") == "tok"
    finally:
        context.close()
