"""Godzina „teraz" nie starzeje się w formularzu (zgłoszenie #346).

Godzina, której pasażer sam nie ustawił, ma być chwilą wyszukiwania: po
powrocie z tła i przy kliknięciu zapisanego miejsca. Ręcznie wpisana zostaje.
Zegar w teście jest podstawiony (page.clock) - „po godzinie" trwa ułamek sekundy.
"""

import datetime

from test_odswiezanie_pozycji import (DATA_JS, UKRYJ_JS, _minuty_temu,  # noqa: F401
                                      _otworz, tryb_deweloperski)
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
        page.click(".when-pill")
        page.evaluate("""() => {
            const sheet = document.querySelector('.when-sheet');
            sheet.querySelector('.when-wheels').dispatchEvent(new Event('pointerdown', {bubbles: true}));  // palec
            sheet.querySelector('[data-wheel="hour"]').scrollTop = 9 * 40;
            sheet.querySelector('[data-wheel="min"]').scrollTop = 6 * 40;
        }""")
        page.wait_for_function("document.getElementById('time').value === '09:30'")
        page.click("[data-done]")
        _po_godzinie(page)
        assert page.input_value("#time") == "09:30"
    finally:
        context.close()


def _miejsce(przegladarka, adres, recznie):
    context, page, _ = _otworz(przegladarka, adres)
    page.evaluate("""() => localStorage.setItem('metal-planner:my-places',
        JSON.stringify([{icon: '🏠', place: %r}]))""" % TRASA[1])
    page.reload()
    page.wait_for_selector(".place-button.set")
    if recznie:
        page.fill("#time", "09:30")
    else:
        page.evaluate("document.getElementById('time').value = '09:30'")
    page.click(".place-button.set")
    return context, page


def test_zapisane_miejsce_idzie_droga_lokalizacji(przegladarka, adres):
    context, page = _miejsce(przegladarka, adres, True)
    try:
        # ta sama droga co ◎: start jest „moją pozycją"
        page.wait_for_selector("body.here-start", state="attached")
    finally:
        context.close()


def test_zapisane_miejsce_nie_rusza_recznej_godziny(przegladarka, adres):
    context, page = _miejsce(przegladarka, adres, True)
    try:
        page.wait_for_selector("body:not(.searching) .journey", state="attached", timeout=20000)
        assert page.input_value("#time") == "09:30"
    finally:
        context.close()


def test_zapisane_miejsce_szuka_od_teraz_gdy_godziny_nie_wpisano(przegladarka, adres):
    context, page = _miejsce(przegladarka, adres, False)
    try:
        page.wait_for_selector("body:not(.searching) .journey", state="attached", timeout=20000)
        assert _minuty_temu(page, page.input_value("#time"))
        assert page.input_value("#date") == page.evaluate(DATA_JS)
    finally:
        context.close()


def test_godzina_nieruszana_nie_zapisuje_sie_z_trasa(przegladarka, adres):
    context, page = _miejsce(przegladarka, adres, False)
    try:
        page.wait_for_selector("body.here-start", state="attached")
        zapis = page.evaluate("JSON.parse(localStorage.getItem('metal-planner:last-search'))")
        assert zapis and "time" not in zapis
    finally:
        context.close()


def _rozklady(page):
    page.click(".tab[data-view=timetable]")
    page.wait_for_selector("body.mode-timetable", state="attached")


def _trasy(page):
    page.click(".tab[data-view=list]")
    page.wait_for_selector("body:not(.mode-timetable)", state="attached")


def _gdzie(page):
    """Panel, w którym stoi pastylka godziny: ta sama przenosi się między widokami."""
    return page.evaluate("document.getElementById('when').closest('#tt-time-field') ? 'rozklady' : 'trasy'")


def _teraz_z_pastylki(page):
    page.click(".when-pill")
    page.click(".when-sheet [data-now]")
    page.click(".when-sheet [data-done]")


def test_godzina_z_tras_jest_w_rozkladach(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    try:
        page.fill("#time", "09:30")
        page.fill("#date", "2030-01-02")
        _rozklady(page)
        assert _gdzie(page) == "rozklady"
        assert page.input_value("#time") == "09:30"
        assert page.input_value("#date") == "2030-01-02"
    finally:
        context.close()


def test_godzina_z_rozkladow_jest_w_trasach(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    try:
        _rozklady(page)
        page.click(".when-pill")
        page.evaluate("""() => {
            const sheet = document.querySelector('.when-sheet');
            sheet.querySelector('.when-wheels').dispatchEvent(new Event('pointerdown', {bubbles: true}));  // palec
            sheet.querySelector('[data-wheel="hour"]').scrollTop = 7 * 40;
            sheet.querySelector('[data-wheel="min"]').scrollTop = 9 * 40;
            sheet.querySelector('[data-wheel="day"]').scrollTop = 40;
        }""")
        page.wait_for_function("document.getElementById('time').value === '07:45'")
        page.click("[data-done]")
        jutro = page.input_value("#date")
        assert jutro != page.evaluate(DATA_JS)
        _trasy(page)
        assert _gdzie(page) == "trasy"
        assert page.input_value("#time") == "07:45"
        assert page.input_value("#date") == jutro
        # wpisana w Rozkładach jest ręczna także w Trasach
        _po_godzinie(page)
        assert page.input_value("#time") == "07:45"
    finally:
        context.close()


def test_teraz_w_rozkladach_wraca_oba_widoki_do_teraz(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    try:
        page.fill("#time", "09:30")
        page.fill("#date", "2030-01-02")
        _rozklady(page)
        assert _gdzie(page) == "rozklady"
        assert page.input_value("#time") == "09:30"
        _teraz_z_pastylki(page)
        assert page.input_value("#date") == page.evaluate(DATA_JS)
        assert _minuty_temu(page, page.input_value("#time"))
        teraz = page.input_value("#time")
        _trasy(page)
        assert _gdzie(page) == "trasy"
        assert page.input_value("#time") == teraz
        assert page.input_value("#date") == page.evaluate(DATA_JS)
        _po_godzinie(page)
        assert _minuty_temu(page, page.input_value("#time"))
        assert page.input_value("#time") != teraz
    finally:
        context.close()


def test_teraz_w_trasach_wraca_rozklady_do_teraz(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    try:
        _rozklady(page)
        # pole leży w schowanej karcie Tras - wpis jak z pastylki, ze zdarzeniem
        page.evaluate("""() => {
            const pole = document.getElementById('time');
            pole.value = '09:30';
            pole.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        _trasy(page)
        assert _gdzie(page) == "trasy"
        assert page.input_value("#time") == "09:30"
        _teraz_z_pastylki(page)
        teraz = page.input_value("#time")
        assert teraz != "09:30"
        assert _minuty_temu(page, teraz)
        _rozklady(page)
        assert _gdzie(page) == "rozklady"
        assert page.input_value("#time") == teraz
        assert page.input_value("#date") == page.evaluate(DATA_JS)
    finally:
        context.close()


def test_nieruszana_godzina_starzeje_sie_w_obu_widokach(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    try:
        przed = page.input_value("#time")
        _po_godzinie(page)
        w_trasach = page.input_value("#time")
        assert w_trasach != przed
        _rozklady(page)
        assert _gdzie(page) == "rozklady"
        assert page.input_value("#time") == w_trasach
        assert page.input_value("#date") == page.evaluate(DATA_JS)
        _po_godzinie(page)
        w_rozkladach = page.input_value("#time")
        assert w_rozkladach != w_trasach
        assert _minuty_temu(page, w_rozkladach)
        _trasy(page)
        assert page.input_value("#time") == w_rozkladach
    finally:
        context.close()


def test_godzina_z_linku_jest_w_rozkladach(przegladarka, adres):
    jutro = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    context, page, _ = _otworz(przegladarka, f"{adres}?to={TRASA[1]}&time=06:15&date={jutro}")
    try:
        _rozklady(page)
        assert page.inner_text("#tt-time-field .when-pill").startswith("Odjazd 06:15, jutro")
        assert page.input_value("#time") == "06:15"
        assert page.input_value("#date") == jutro
    finally:
        context.close()
