"""Pasek z najszybszym dojazdem zawsze stoi na samej górze (zgłoszenie #291).

Okienko z tablicą odjazdów stoi w tym samym górnym rzędzie co pasek. Gdy pasek
nie mieścił się obok niego, schodził pod okienko i po każdym otwarciu tablicy
skakał w dół. Test szuka, na komputerze w kilku szerokościach okna, paska
przy otwartej i zamkniętej tablicy: pasek ma stać w tym samym miejscu, a
tablica pod nim, nie na nim.

Ta sama przeglądarka i ten sam serwer co test układu telefonu - i tak samo
pomija się bez playwright albo bez bazy rozkładów.
"""

import pytest

from test_uklad_telefonu import (PUSTE_ZRODLA, TRASA, _odpowiedz, _wybierz,  # noqa: F401
                                 adres, przegladarka)

OKNA = {"szerokie": (1600, 900), "komputer": (1280, 800), "waskie": (900, 700)}

PASEK_I_TABLICA_JS = """() => {
    const box = id => {
        const el = document.getElementById(id);
        const r = el.getBoundingClientRect();
        return {top: r.top, bottom: r.bottom, left: r.left, right: r.right,
                shown: !el.hidden};
    };
    return {bar: box('time-headline'), board: box('flow-panel')};
}"""


@pytest.fixture(params=list(OKNA), ids=list(OKNA))
def strona(request, przegladarka, adres):
    szerokosc, wysokosc = OKNA[request.param]
    context = przegladarka.new_context(locale="pl-PL", service_workers="block",
                                       viewport={"width": szerokosc, "height": wysokosc})
    page = context.new_page()
    bledy = []
    page.on("pageerror", lambda e: bledy.append(str(e)))
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres)
    page.wait_for_selector("#start")
    page.fill("#time", "12:00")
    _wybierz(page, "#start", TRASA[0])
    _wybierz(page, "#end", TRASA[1])
    page.wait_for_selector("#time-headline:not([hidden])", timeout=20000)
    page.wait_for_timeout(1500)       # tablica przystanku startowego dochodzi osobno
    yield page
    context.close()
    assert not bledy


def test_tablica_nie_spycha_paska(strona):
    page = strona
    page.evaluate("hideSidePanel()")
    bez = page.evaluate(PASEK_I_TABLICA_JS)
    assert not bez["board"]["shown"]

    page.evaluate("showSidePanel('<b>Katedra</b>' + '<p>odjazd</p>'.repeat(6))")
    z = page.evaluate(PASEK_I_TABLICA_JS)
    assert z["board"]["shown"]

    assert z["bar"]["top"] == bez["bar"]["top"] == 16
    assert z["board"]["top"] >= z["bar"]["bottom"]
