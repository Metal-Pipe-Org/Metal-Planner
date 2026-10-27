"""Kropka „tu jesteś" na mapie rusza się razem z pasażerem (zgłoszenie #410).

Pojawia się tylko, gdy zgoda na lokalizację już jest - samo wejście na stronę
niczego nie pyta. Pozycję podstawia Playwright (geolocation), więc zmiana
pozycji w trakcie testu to zmiana tego, co widzi nasłuch.

Ta sama przeglądarka i ten sam serwer co test układu telefonu - i tak samo
pomija się bez playwright albo bez bazy rozkładów.
"""

import gtfs
from test_uklad_telefonu import (PUSTE_ZRODLA, _odpowiedz, adres,  # noqa: F401
                                 przegladarka)

KROPKA = ".you-are-here"
SRODEK_JS = """() => {
    const dot = document.querySelector('.you-are-here').getBoundingClientRect();
    return [dot.x + dot.width / 2, dot.y + dot.height / 2];
}"""


def _otworz(przegladarka, adres, **opcje):
    context = przegladarka.new_context(
        viewport={"width": 390, "height": 844}, device_scale_factor=2,
        is_mobile=True, has_touch=True, locale="pl-PL", service_workers="block", **opcje)
    page = context.new_page()
    bledy = []
    page.on("pageerror", lambda e: bledy.append(str(e)))
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres)
    page.wait_for_selector("#start")
    page.wait_for_load_state("networkidle")
    return context, page, bledy


def test_kropka_pokazuje_sie_i_przesuwa_przy_zmianie_pozycji(przegladarka, adres):
    slupek = gtfs.all_stops_geo()[0]
    context, page, bledy = _otworz(
        przegladarka, adres, permissions=["geolocation"],
        geolocation={"latitude": slupek["lat"], "longitude": slupek["lon"]})
    page.wait_for_selector(KROPKA)
    page.evaluate("map.setView([%s, %s], 15, {animate: false})" % (slupek["lat"], slupek["lon"]))
    przed = page.evaluate(SRODEK_JS)
    context.set_geolocation({"latitude": slupek["lat"] + 0.002, "longitude": slupek["lon"]})
    page.wait_for_function(
        "([x, y]) => (%s)()[1] < y - 20" % SRODEK_JS, arg=przed)
    po = page.evaluate(SRODEK_JS)
    assert abs(po[0] - przed[0]) < 2
    context.close()
    assert not bledy


def test_bez_zgody_kropki_nie_ma_i_strona_o_nic_nie_pyta(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    page.wait_for_timeout(500)
    assert page.query_selector(KROPKA) is None
    context.close()
    assert not bledy
