"""Godzina „teraz" nie starzeje się w formularzu (zgłoszenie #346).

Godzina, której pasażer sam nie ustawił, ma być chwilą wyszukiwania: po
powrocie z tła i przy kliknięciu zapisanego miejsca. Ręcznie wpisana zostaje.
Zegar w teście jest podstawiony (page.clock) - „po godzinie" trwa ułamek sekundy.
"""

import datetime

import pytest

import gtfs
from test_odswiezanie_pozycji import (DATA_JS, TERAZ_JS, UKRYJ_JS, _minuty_temu,
                                      _otworz)
from test_uklad_telefonu import (PUSTE_ZRODLA, TRASA, _odpowiedz, _wybierz,  # noqa: F401
                                 adres, przegladarka)


def _po_godzinie(page):
    page.clock.run_for(60 * 60 * 1000)
    page.evaluate(UKRYJ_JS, True)
    page.evaluate(UKRYJ_JS, False)


def test_powrot_z_tla_odswieza_godzine_nieustawiona_recznie(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    try:
        przed = page.input_value("#time")
        _po_godzinie(page)
        assert page.input_value("#time") != przed
        assert _minuty_temu(page, page.input_value("#time"))
    finally:
        context.close()


def test_godzina_wpisana_recznie_przezywa_powrot_z_tla(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    try:
        page.click("#time", click_count=3)
        page.keyboard.type("0930")
        page.keyboard.press("Tab")
        _po_godzinie(page)
        assert page.input_value("#time") == "09:30"
    finally:
        context.close()


def _miejsce(przegladarka, adres, przelacznik):
    context, page, _ = _otworz(przegladarka, adres)
    page.evaluate("""() => localStorage.setItem('metal-planner:my-places',
        JSON.stringify([{icon: '🏠', place: %r}]))""" % TRASA[1])
    page.reload()
    page.wait_for_selector(".place-button.set")
    page.evaluate("v => { document.getElementById('locate-sets-time').checked = v; }",
                  przelacznik)
    page.fill("#time", "09:30")
    page.click(".place-button.set")
    return context, page


def test_zapisane_miejsce_idzie_droga_lokalizacji_i_ustawia_teraz(przegladarka, adres):
    context, page = _miejsce(przegladarka, adres, True)
    try:
        page.wait_for_function("document.getElementById('time').value !== '09:30'")
        assert _minuty_temu(page, page.input_value("#time"))
        assert page.input_value("#date") == page.evaluate(DATA_JS)
        # ta sama droga co ◎: start jest „moją pozycją", więc ◎ świeci jako gotowe do odświeżania
        page.wait_for_selector("body.here-start", state="attached")
    finally:
        context.close()


def test_zapisane_miejsce_przy_wylaczonym_przelaczniku_nie_rusza_godziny(przegladarka, adres):
    context, page = _miejsce(przegladarka, adres, False)
    try:
        page.wait_for_selector("body.here-start", state="attached")
        page.wait_for_selector("body:not(.searching) .journey", state="attached", timeout=20000)
        assert page.input_value("#time") == "09:30"
    finally:
        context.close()


def test_godzina_nieruszana_nie_zapisuje_sie_z_trasa(przegladarka, adres):
    context, page = _miejsce(przegladarka, adres, True)
    try:
        page.wait_for_selector("body.here-start", state="attached")
        zapis = page.evaluate("JSON.parse(localStorage.getItem('metal-planner:last-search'))")
        assert zapis and "time" not in zapis
    finally:
        context.close()
