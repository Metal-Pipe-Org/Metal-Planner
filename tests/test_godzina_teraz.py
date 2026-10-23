"""Godzina „teraz" nie starzeje się w formularzu (zgłoszenie #346).

Godzina, której pasażer sam nie ustawił, ma być chwilą wyszukiwania: po
powrocie z tła i przy kliknięciu zapisanego miejsca. Ręcznie wpisana zostaje.
Zegar w teście jest podstawiony (page.clock) - „po godzinie" trwa ułamek sekundy.
"""

from test_odswiezanie_pozycji import (DATA_JS, UKRYJ_JS, _minuty_temu,
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
    """Panel, w którym naprawdę stoją pola: to samo pole #time i #date
    przenosi się między widokami, więc samą wartość trzeba czytać razem z miejscem."""
    return page.evaluate("""() => ({
        time: document.getElementById('time').closest('#tt-time-field') ? 'rozklady' : 'trasy',
        date: document.getElementById('date').closest('.tt-date-part') ? 'rozklady' : 'trasy',
    })""")


def test_godzina_z_tras_jest_w_rozkladach(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    try:
        page.fill("#time", "09:30")
        page.click("#day-toggle")
        page.fill("#date", "2030-01-02")
        _rozklady(page)
        assert _gdzie(page) == {"time": "rozklady", "date": "rozklady"}
        assert page.input_value("#time") == "09:30"
        assert page.input_value("#date") == "2030-01-02"
    finally:
        context.close()


def test_godzina_z_rozkladow_jest_w_trasach(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    try:
        _rozklady(page)
        page.click("#time", click_count=3)
        page.keyboard.type("0745")
        page.keyboard.press("Tab")
        page.fill("#date", "2030-01-02")
        _trasy(page)
        assert _gdzie(page) == {"time": "trasy", "date": "trasy"}
        assert page.input_value("#time") == "07:45"
        assert page.input_value("#date") == "2030-01-02"
        # wpisana w Rozkładach jest ręczna także w Trasach
        _po_godzinie(page)
        assert page.input_value("#time") == "07:45"
    finally:
        context.close()


def test_teraz_w_rozkladach_wraca_oba_widoki_do_teraz(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    try:
        page.fill("#time", "09:30")
        page.click("#day-toggle")
        page.fill("#date", "2030-01-02")
        _rozklady(page)
        assert _gdzie(page) == {"time": "rozklady", "date": "rozklady"}
        assert page.input_value("#time") == "09:30"
        page.click("#tt-now")
        assert page.input_value("#date") == page.evaluate(DATA_JS)
        assert _minuty_temu(page, page.input_value("#time"))
        teraz = page.input_value("#time")
        _trasy(page)
        assert _gdzie(page) == {"time": "trasy", "date": "trasy"}
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
        page.fill("#time", "09:30")
        _trasy(page)
        assert _gdzie(page)["time"] == "trasy"
        assert page.input_value("#time") == "09:30"
        page.click("#time-now")
        teraz = page.input_value("#time")
        assert teraz != "09:30"
        assert _minuty_temu(page, teraz)
        _rozklady(page)
        assert _gdzie(page)["time"] == "rozklady"
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
        assert _gdzie(page)["time"] == "rozklady"
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


def test_godzina_wpisana_wprost_w_pole_tras_jest_w_rozkladach(przegladarka, adres):
    context, page, _ = _otworz(przegladarka, adres)
    try:
        # tak ustawia godzinę link do wyszukiwania: bez zdarzeń, bez wiedzy o rozkładach
        page.evaluate("""() => {
            document.getElementById('time').value = '06:15';
            document.getElementById('date').value = '2030-03-04';
        }""")
        _rozklady(page)
        assert page.is_visible("#time")
        assert page.input_value("#time") == "06:15"
        assert page.input_value("#date") == "2030-03-04"
    finally:
        context.close()
