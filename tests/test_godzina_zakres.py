"""Zakres i ważność wybranej godziny (#501): dni od pierwszego do ostatniego
z rozkładem, ręczna godzina obowiązuje, dopóki nie minie, w pojeździe tylko
„teraz", a link z dniem spoza rozkładu nie jest po cichu przycinany.
"""

import datetime
import json

import gtfs
from test_uklad_telefonu import PUSTE_ZRODLA, TRASA, _odpowiedz, adres, przegladarka  # noqa: F401


def _otworz(przegladarka, adres, sciezka="", init=None):
    context = przegladarka.new_context(viewport={"width": 1280, "height": 800}, locale="pl-PL",
                                       service_workers="block")
    page = context.new_page()
    page.clock.install(time=datetime.datetime.combine(datetime.date.today(), datetime.time(12, 0)))
    if init:
        page.add_init_script(init)
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres + sciezka)
    page.wait_for_selector(".when-pill")
    return context, page


def _pastylka(page):
    return page.inner_text(".when-pill").replace("▾", "").strip()


def _powrot_z_tla(page):
    page.evaluate("""() => {
        Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")


def test_dni_od_pierwszego_do_ostatniego_z_rozkladem(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        page.click(".when-pill")
        dni = page.eval_on_selector_all(".when-day", "els => els.map(e => e.dataset.day)")
        assert dni[0] == gtfs.first_service_date().isoformat()
        assert dni[-1] == gtfs.last_service_date().isoformat()
        assert page.inner_text(".when-day.on") == "Dziś"
    finally:
        context.close()


def test_reczna_godzina_wraca_do_teraz_dopiero_gdy_minie(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        page.click(".when-pill")
        page.click('[data-mode="arrive"]')
        page.keyboard.type("1230")
        page.keyboard.press("Enter")
        _powrot_z_tla(page)
        assert _pastylka(page) == "Przyjazd 12:30, dziś"
        page.clock.run_for(60 * 60 * 1000)
        assert _pastylka(page) == "Przyjazd 12:30, dziś", "w trakcie oglądania zostaje"
        _powrot_z_tla(page)
        assert _pastylka(page) == "Odjazd teraz"
    finally:
        context.close()


def test_w_pojezdzie_tylko_teraz_a_potem_wraca_wybor(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        page.click(".when-pill")
        page.click('[data-mode="arrive"]')
        page.keyboard.type("1800")
        page.keyboard.press("Enter")
        page.click("#mode-vehicle")
        assert _pastylka(page) == "Odjazd teraz"
        assert page.is_disabled(".when-pill")
        page.click("#mode-place")
        assert _pastylka(page) == "Przyjazd 18:00, dziś"
        assert page.is_enabled(".when-pill")
        # wybór, który w pojeździe minął, nie wraca
        page.click("#mode-vehicle")
        page.clock.run_for(7 * 60 * 60 * 1000)
        page.click("#mode-place")
        assert _pastylka(page) == "Odjazd teraz"
    finally:
        context.close()


def test_link_sprzed_rozkladu_to_teraz(przegladarka, adres):
    context, page = _otworz(przegladarka, adres, f"?to={TRASA[1]}&time=08:00&date=2020-01-01&arrive=1")
    try:
        assert _pastylka(page) == "Odjazd teraz"
    finally:
        context.close()


def test_link_z_przeszloscia_w_rozkladzie_zostaje(przegladarka, adres):
    pierwszy = gtfs.first_service_date()
    if pierwszy >= datetime.date.today():
        import pytest
        pytest.skip("rozkład zaczyna się dziś - nie ma przeszłości w zakresie")
    context, page = _otworz(przegladarka, adres, f"?to={TRASA[1]}&time=08:00&date={pierwszy.isoformat()}")
    try:
        assert page.input_value("#date") == pierwszy.isoformat()
        assert _pastylka(page).startswith("Odjazd 08:00, ")
    finally:
        context.close()


def test_link_za_rozkladem_mowi_ze_go_jeszcze_nie_ma(przegladarka, adres):
    dzien = gtfs.last_service_date() + datetime.timedelta(days=3)
    context, page = _otworz(przegladarka, adres,
                            f"?from={TRASA[0]}&to={TRASA[1]}&time=08:00&date={dzien.isoformat()}")
    try:
        assert page.input_value("#date") == dzien.isoformat()
        page.wait_for_selector(".notice.error", timeout=20000)
        assert "jeszcze nie opublikowano" in page.inner_text(".notice.error")
    finally:
        context.close()


def test_przywrocona_trasa_bez_minionej_godziny(przegladarka, adres):
    wczoraj = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    zapis = {"start": TRASA[0], "end": TRASA[1], "savedAt": 0, "time": "08:00", "date": wczoraj,
             "arrive": True}
    context, page = _otworz(przegladarka, adres, init="localStorage.setItem('metal-planner:last-search', %s)"
                            % json.dumps(json.dumps(zapis)))
    try:
        page.wait_for_selector("#recall-offer:not([hidden])")
        assert "8:00" not in page.inner_text("#recall-offer-name")
        page.click("#recall-offer-yes")
        assert _pastylka(page) == "Odjazd teraz"
    finally:
        context.close()
