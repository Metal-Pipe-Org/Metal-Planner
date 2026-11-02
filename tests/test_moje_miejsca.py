"""Skrót „moje miejsca" nie nadpisuje wpisanego startu (zgłoszenia #255, #429).

Ustalenie z #255: skrót zawsze wypełnia pole docelowe; start leci z lokalizacji
pasażera, chyba że w „Skąd" już coś jest - wtedy start zostaje. Jedno kliknięcie
od razu szuka. Lokalizacja w teście stoi na pierwszym przystanku trasy, więc
start wpisany ręcznie (inny przystanek) odróżnia się od startu z GPS.
"""

from test_odswiezanie_pozycji import _otworz
from test_uklad_telefonu import (TRASA, _wybierz,  # noqa: F401
                                 adres, przegladarka)

INNY_START = "Rynek"


def _otworz_z_miejscem(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    page.evaluate("""() => localStorage.setItem('metal-planner:my-places',
        JSON.stringify([{icon: '🏠', place: %r}]))""" % TRASA[1])
    page.reload()
    page.wait_for_selector(".place-button.set")
    return context, page


def test_skrot_przy_wpisanym_starcie_ustawia_tylko_cel(przegladarka, adres):
    context, page = _otworz_z_miejscem(przegladarka, adres)
    try:
        _wybierz(page, "#start", INNY_START)
        start = page.input_value("#start")
        page.click(".place-button.set")
        page.wait_for_selector("body:not(.searching) .journey", state="attached", timeout=20000)
        assert page.input_value("#end") == TRASA[1]
        assert page.input_value("#start") == start
        assert page.input_value("#start") != "(51.0705, 17.0915)"
    finally:
        context.close()


def test_skrot_przy_pustym_starcie_bierze_start_z_lokalizacji(przegladarka, adres):
    context, page = _otworz_z_miejscem(przegladarka, adres)
    try:
        page.click(".place-button.set")
        page.wait_for_function("document.getElementById('start').value.startsWith('(')")
        assert page.input_value("#end") == TRASA[1]
    finally:
        context.close()


def test_skrot_odswieza_start_z_lokalizacji(przegladarka, adres):
    context, page = _otworz_z_miejscem(przegladarka, adres)
    try:
        page.click("#locate")
        page.wait_for_function("document.getElementById('start').value.startsWith('(')")
        page.evaluate("""() => { const s = document.getElementById('start');
            s.dataset.stary = s.value; }""")
        context.set_geolocation({"latitude": 51.1, "longitude": 17.03})
        page.click(".place-button.set")
        page.wait_for_function(
            "document.getElementById('start').value !== document.getElementById('start').dataset.stary")
        assert page.input_value("#start") == "(51.1000, 17.0300)"
        assert page.input_value("#end") == TRASA[1]
    finally:
        context.close()
