"""Kursor sam przechodzi do „Dokąd" po wybraniu startu (zgłoszenie #275).

Jak w Jakdojade: gdy start jest gotowy - podpowiedź wybrana myszą albo
Enterem, „Stoisz na przystanku X? Tak" - a cel jest pusty, pisze się dalej
w polu celu. Gdy cel już jest, start od razu szuka i kursora nikt nie
przestawia. ◎ jest takim samym wyborem startu (zgłoszenie #319), więc też
przenosi kursor; klik w mapę nie, bo zostawia mapę do wskazania celu.

Ta sama przeglądarka i ten sam serwer co test układu telefonu - i tak samo
pomija się bez playwright albo bez bazy rozkładów.
"""

import pytest

import gtfs
from test_uklad_telefonu import (PUSTE_ZRODLA, TRASA, _odpowiedz, _wybierz,  # noqa: F401
                                 adres, przegladarka)

FOKUS = "document.activeElement && document.activeElement.id"

EKRANY = {
    "komputer": {"viewport": {"width": 1280, "height": 800}},
    "telefon": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2,
                "is_mobile": True, "has_touch": True},
}


@pytest.fixture(params=list(EKRANY), ids=list(EKRANY))
def ekran(request):
    return EKRANY[request.param]


def _otworz(przegladarka, adres, ekran, **opcje):
    context = przegladarka.new_context(locale="pl-PL", service_workers="block",
                                       **ekran, **opcje)
    page = context.new_page()
    bledy = []
    page.on("pageerror", lambda e: bledy.append(str(e)))
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres)
    page.wait_for_selector("#start")
    page.wait_for_load_state("networkidle")   # słupki na mapie - pytanie „Stoisz na…"
    return context, page, bledy


def test_podpowiedz_myszą_przenosi_do_celu(przegladarka, adres, ekran):
    context, page, bledy = _otworz(przegladarka, adres, ekran)
    page.fill("#start", TRASA[0])
    page.click("#start-list .ac-item")
    assert page.input_value("#start")
    assert page.evaluate(FOKUS) == "end"
    assert page.is_visible("#end")
    context.close()
    assert not bledy


def test_enter_przenosi_do_celu(przegladarka, adres, ekran):
    context, page, bledy = _otworz(przegladarka, adres, ekran)
    _wybierz(page, "#start", TRASA[0])
    assert page.evaluate(FOKUS) == "end"
    context.close()
    assert not bledy


def test_wpisany_cel_zostaje_bez_kursora(przegladarka, adres, ekran):
    context, page, bledy = _otworz(przegladarka, adres, ekran)
    page.fill("#time", "12:00")
    _wybierz(page, "#end", TRASA[1])
    _wybierz(page, "#start", TRASA[0])
    page.wait_for_selector("#time-headline:not([hidden])", timeout=20000)
    assert page.evaluate(FOKUS) != "end"
    assert page.input_value("#end")
    context.close()
    assert not bledy


def test_moja_lokalizacja_przenosi_do_celu(przegladarka, adres, ekran):
    slupek = next(s for s in gtfs.all_stops_geo() if s["name"] == TRASA[0])
    context, page, bledy = _otworz(
        przegladarka, adres, ekran, permissions=["geolocation"],
        geolocation={"latitude": slupek["lat"], "longitude": slupek["lon"]})
    page.click("#locate")
    page.wait_for_selector("#locate-offer", state="visible")
    assert page.evaluate(FOKUS) == "end"

    page.click("#locate-offer-yes")
    assert TRASA[0] in page.input_value("#start")
    assert page.evaluate(FOKUS) == "end"
    context.close()
    assert not bledy


def test_moja_lokalizacja_przy_wpisanym_celu_nie_rusza_kursora(przegladarka, adres, ekran):
    slupek = next(s for s in gtfs.all_stops_geo() if s["name"] == TRASA[0])
    context, page, bledy = _otworz(przegladarka, adres, ekran)
    page.fill("#time", "12:00")
    _wybierz(page, "#end", TRASA[1])
    page.wait_for_selector("#locate-msg", state="visible")   # bez zgody: start zostaje pusty
    context.grant_permissions(["geolocation"])
    context.set_geolocation({"latitude": slupek["lat"], "longitude": slupek["lon"]})
    page.evaluate("document.activeElement.blur()")
    page.click("#locate")
    page.wait_for_selector("#time-headline:not([hidden])", timeout=20000)
    assert page.evaluate(FOKUS) != "end"
    context.close()
    assert not bledy


# W drugą stronę: cel wybrany przy pustym starcie znaczy „stąd" - to samo co
# ◎, łącznie z pytaniem „Stoisz na…" i szukaniem.

def test_cel_bez_startu_jedzie_z_mojej_lokalizacji(przegladarka, adres, ekran):
    slupek = next(s for s in gtfs.all_stops_geo() if s["name"] == TRASA[0])
    context, page, bledy = _otworz(
        przegladarka, adres, ekran, permissions=["geolocation"],
        geolocation={"latitude": slupek["lat"], "longitude": slupek["lon"]})
    page.fill("#time", "12:00")
    _wybierz(page, "#end", TRASA[1])
    page.wait_for_selector("#locate-offer", state="visible")
    assert page.input_value("#start")
    page.wait_for_selector("#time-headline:not([hidden])", timeout=20000)

    page.click("#locate-offer-yes")
    assert TRASA[0] in page.input_value("#start")
    assert page.input_value("#end")
    context.close()
    assert not bledy


def test_cel_bez_startu_i_bez_lokalizacji_wraca_do_startu(przegladarka, adres, ekran):
    context, page, bledy = _otworz(przegladarka, adres, ekran)
    page.fill("#end", TRASA[1])
    page.click("#end-list .ac-item")
    page.wait_for_selector("#locate-msg", state="visible")
    assert page.evaluate(FOKUS) == "start"
    assert not page.input_value("#start")
    context.close()
    assert not bledy


def test_cel_przy_gotowym_starcie_nie_pyta_o_lokalizacje(przegladarka, adres, ekran):
    context, page, bledy = _otworz(przegladarka, adres, ekran)
    page.fill("#time", "12:00")
    _wybierz(page, "#start", TRASA[0])
    _wybierz(page, "#end", TRASA[1])
    page.wait_for_selector("#time-headline:not([hidden])", timeout=20000)
    assert page.is_hidden("#locate-msg")
    assert TRASA[0] in page.input_value("#start")
    context.close()
    assert not bledy
