"""Ostatnie wyszukiwanie nie wraca samo (zgłoszenie #237).

Po otwarciu strony zapamiętana trasa nie zwija już formularza na telefonie -
pod polami pojawia się pytanie, czy do niej wrócić. "Tak" ją wyszukuje,
"Nie" ją zapomina, tak samo jak ✕.

Ta sama przeglądarka i ten sam serwer co test układu telefonu - i tak samo
pomija się bez playwright albo bez bazy rozkładów.
"""

import json

from test_uklad_telefonu import (PUSTE_ZRODLA, TRASA, UKLAD_JS, _odpowiedz,  # noqa: F401
                                 adres, przegladarka)

KLUCZ = "metal-planner:last-search"
ZAPISANA = {"start": TRASA[0], "end": TRASA[1]}


def _otworz(przegladarka, adres):
    context = przegladarka.new_context(
        viewport={"width": 390, "height": 844}, device_scale_factor=2,
        is_mobile=True, has_touch=True, locale="pl-PL", service_workers="block")
    context.add_init_script(
        f"localStorage.setItem({json.dumps(KLUCZ)}, {json.dumps(json.dumps(ZAPISANA))});")
    page = context.new_page()
    bledy = []
    page.on("pageerror", lambda e: bledy.append(str(e)))
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres)
    page.wait_for_selector("#recall-offer", state="visible")
    return context, page, bledy


def test_trasa_czeka_na_pytanie(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    assert TRASA[0] in page.inner_text("#recall-offer-name")
    assert page.input_value("#start") == ""
    assert not page.is_visible(".search-summary")
    assert page.evaluate(UKLAD_JS, True) == []

    page.click("#recall-offer-yes")
    page.wait_for_selector(".search-summary", state="visible", timeout=20000)
    assert page.input_value("#start")
    assert not page.is_visible("#recall-offer")
    context.close()
    assert not bledy


def test_nie_zapomina_trase(przegladarka, adres):
    context, page, bledy = _otworz(przegladarka, adres)
    page.click("#recall-offer-no")
    assert not page.is_visible("#recall-offer")
    assert page.input_value("#start") == ""
    assert page.evaluate(f"localStorage.getItem({json.dumps(KLUCZ)})") is None
    context.close()
    assert not bledy
