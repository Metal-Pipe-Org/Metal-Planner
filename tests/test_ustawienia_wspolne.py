"""Każde ustawienie w ⚙ działa tym samym mechanizmem (zgłoszenie #415).

Testy przechodzą po WSZYSTKICH wierszach listy ustawień, więc nowe ustawienie
jest objęte automatycznie - wystarczy, że ma wiersz w index.html. Opis
ustawienia (rodzaj, domyślna, etykiety, tylko DEV) czytamy z samego HTML-u,
a zachowanie sprawdzamy w prawdziwej przeglądarce: zapamiętanie, „Przywróć
domyślne", pasek „zmienione", kopie pod nagłówkami i statystyki.
"""

import json
import os
from html.parser import HTMLParser

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_uklad_telefonu import (PUSTE_ZRODLA, _odpowiedz, adres,  # noqa: F401
                                 przegladarka)
from test_ustawienia_dev import _index

KLUCZ = "metal-planner:settings"
STATYSTYKI = "metal-planner:settings-stats"


class _Wiersze(HTMLParser):
    """Wiersze `.set-row` z kontrolką wartości: atrybuty wiersza i kontrolki."""

    def __init__(self):
        super().__init__()
        self.wiersze = []
        self._wiersz = None
        self._select = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "set-row" in (a.get("class") or "").split():
            self._wiersz = {"id": a.get("data-setting"), "labels": a.get("data-labels", ""),
                            "dev": "dev-only" in a["class"].split(), "hidden": "hidden" in a,
                            "kind": None, "options": []}
            self.wiersze.append(self._wiersz)
        elif self._wiersz is None:
            return
        elif tag == "input" and a.get("type") != "file" and not self._wiersz["kind"]:
            self._wiersz.update(kind=a["type"], control=a.get("id"), min=a.get("min"),
                                max=a.get("max"), step=a.get("step"), value=a.get("value"),
                                checked="checked" in a)
        elif tag == "select":
            self._wiersz.update(kind="select", control=a.get("id"))
        elif tag == "option":
            self._wiersz["options"].append((a["value"], "selected" in a))


def _ustawienia():
    parser = _Wiersze()
    parser.feed(_index(dev_mode=True))
    return [w for w in parser.wiersze if w["kind"]]


USTAWIENIA = _ustawienia()
IDS = [w["id"] for w in USTAWIENIA]


def _domyslna(w):
    if w["kind"] == "checkbox":
        return w["checked"]
    if w["kind"] == "select":
        return next(v for v, wybrana in w["options"] if wybrana) if any(
            wybrana for _, wybrana in w["options"]) else w["options"][0][0]
    return float(w["value"])


def _inna(w):
    """Wartość różna od domyślnej, którą kontrolka na pewno przyjmie."""
    domyslna = _domyslna(w)
    if w["kind"] == "checkbox":
        return not domyslna
    if w["kind"] == "select":
        return next(v for v, _ in w["options"] if v != domyslna)
    return float(w["min"]) if float(w["min"]) != domyslna else float(w["max"])


USTAW_JS = """([id, value]) => {
    const el = document.getElementById(id);
    if (el.type === 'checkbox') el.checked = value; else el.value = value;
    const events = el.type === 'range' ? ['input', 'change'] : ['change'];
    for (const type of events) el.dispatchEvent(new Event(type, {bubbles: true}));
}"""
ODCZYT_JS = """id => {
    const el = document.getElementById(id);
    return el.type === 'checkbox' ? el.checked : el.tagName === 'SELECT' ? el.value : Number(el.value);
}"""
KOPIA_JS = """([id, value]) => {
    const copy = document.querySelector(`[data-kind=changed] .set-copy[data-copy-of="${id}"]`);
    if (!copy) return false;
    const el = copy.querySelector('input, select');
    if (el.type === 'checkbox') el.checked = value; else el.value = value;
    // Sam `change`: ruch suwaka w kopii do domyślnej zdejmuje kopię już przy `input`.
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
}"""
# Odświeżenie pasków „zmienione" (markChangedSettings) przegląda wiersze listy
# dokładnie raz, więc liczymy te przeglądy.
LICZNIK_JS = """
window.__odswiezenia = 0;
const querySelectorAll = Element.prototype.querySelectorAll;
Element.prototype.querySelectorAll = function (selector) {
    if (this.id === 'settings-list' && selector === '.set-row') window.__odswiezenia++;
    return querySelectorAll.call(this, selector);
};
"""


@pytest.fixture(scope="module", autouse=True)
def tryb_deweloperski():
    stare = os.environ.get("DEV_MODE")
    os.environ["DEV_MODE"] = "1"
    yield
    if stare is None:
        del os.environ["DEV_MODE"]
    else:
        os.environ["DEV_MODE"] = stare


def _otworz(przegladarka, adres, init=None, szerokosc=1200):
    context = przegladarka.new_context(
        viewport={"width": szerokosc, "height": 900}, locale="pl-PL", service_workers="block")
    page = context.new_page()
    page.bledy = []
    page.on("pageerror", lambda e: page.bledy.append(str(e)))
    if init:
        page.add_init_script(init)
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres)
    page.wait_for_selector("#settings-list", state="attached")
    return context, page


@pytest.fixture(scope="module")
def strona(adres, przegladarka):
    context, page = _otworz(przegladarka, adres, LICZNIK_JS)
    yield page
    assert not page.bledy, page.bledy
    context.close()


def _widoczne(strona, ustawienie):
    if strona.evaluate("id => document.querySelector(`[data-setting=\"${id}\"]`).hidden", ustawienie["id"]):
        pytest.skip("strojenie schowane stałą w app.js")


def _statystyka(strona, id):
    stats = json.loads(strona.evaluate(f"localStorage.getItem('{STATYSTYKI}')") or "{}")
    return stats.get("changes", {}).get(id, {}).get("n", 0)


@pytest.mark.parametrize("w", USTAWIENIA, ids=IDS)
def test_opis_ustawienia(w):
    assert w["id"] == w["control"], "data-setting musi być równe id kontrolki"
    assert w["kind"] in ("checkbox", "range", "select")
    assert any(l.strip() for l in w["labels"].split(",")), "brak etykiet"
    assert _inna(w) != _domyslna(w)
    if w["kind"] == "range":
        assert float(w["min"]) <= _domyslna(w) <= float(w["max"])
    if w["kind"] == "select":
        assert len(w["options"]) > 1


@pytest.mark.parametrize("w", USTAWIENIA, ids=IDS)
def test_zapamietane_i_przywracane_do_domyslnej(w, adres, przegladarka):
    context, page = _otworz(przegladarka, adres)
    try:
        _widoczne(page, w)
        page.evaluate(USTAW_JS, [w["id"], _inna(w)])
        assert page.evaluate(f"JSON.parse(localStorage.getItem('{KLUCZ}'))['{w['id']}']") == _inna(w)
        page.reload()
        page.wait_for_selector("#settings-list", state="attached")
        assert page.evaluate(ODCZYT_JS, w["id"]) == _inna(w)
        with page.expect_navigation():
            page.evaluate("document.getElementById('dev-reset').click()")
        page.wait_for_selector("#settings-list", state="attached")
        assert page.evaluate(ODCZYT_JS, w["id"]) == _domyslna(w)
        assert page.evaluate(f"(JSON.parse(localStorage.getItem('{KLUCZ}')) || {{}})['{w['id']}']") is None
        assert not page.bledy, page.bledy
    finally:
        context.close()


@pytest.mark.parametrize("w", USTAWIENIA, ids=IDS)
def test_pasek_kopie_i_statystyki(w, strona):
    _widoczne(strona, w)
    wiersz = f'[data-setting="{w["id"]}"]'
    zmian = _statystyka(strona, w["id"])
    strona.evaluate(USTAW_JS, [w["id"], _inna(w)])
    # Pasek na oryginale, kopia w „Inne niż domyślne", zmiana policzona raz.
    assert "changed" in strona.get_attribute(wiersz, "class").split()
    assert strona.query_selector(f'[data-kind=changed] .set-copy[data-copy-of="{w["id"]}"]')
    assert _statystyka(strona, w["id"]) == zmian + 1
    kopia = f'[data-kind=changed] .set-copy[data-copy-of="{w["id"]}"] :is(input, select)'
    assert strona.evaluate(f"""() => {{
        const el = document.querySelector('{kopia}');
        return el.type === 'checkbox' ? el.checked : el.tagName === 'SELECT' ? el.value : Number(el.value);
    }}""") == _inna(w)

    # Ruch w kopii to jedna interakcja: oryginał dostaje wartość, odświeżenie
    # pasków i kopii i jedno zliczenie dzieją się raz.
    strona.evaluate("window.__odswiezenia = 0")
    assert strona.evaluate(KOPIA_JS, [w["id"], _domyslna(w)])
    assert strona.evaluate(ODCZYT_JS, w["id"]) == _domyslna(w)
    assert "changed" not in strona.get_attribute(wiersz, "class").split()
    assert not strona.query_selector(f'[data-kind=changed] .set-copy[data-copy-of="{w["id"]}"]')
    assert _statystyka(strona, w["id"]) == zmian + 2
    assert strona.evaluate("window.__odswiezenia") == 1


def test_dawne_klucze_sa_przenoszone(adres, przegladarka):
    init = """
        if (!localStorage.getItem('metal-planner:settings')) {
            localStorage.setItem('metal-planner:dev-prefs',
                JSON.stringify({density: '5', 'car-vans': true, 'order-limit': '1d', 'fold:Rower': true}));
            localStorage.setItem('metal-planner:time-prefs', JSON.stringify({ends: true}));
            localStorage.setItem('metal-planner:dot-prefs', JSON.stringify({size: 10, ttPast: false}));
            localStorage.setItem('metal-planner:look-prefs', JSON.stringify({maxWeight: 5, minOpacity: 0.5}));
        }
    """
    context, page = _otworz(przegladarka, adres, init)
    try:
        assert page.evaluate(ODCZYT_JS, "density") == 5
        assert page.evaluate(ODCZYT_JS, "car-vans") is True
        assert page.evaluate(ODCZYT_JS, "order-limit") == "1d"
        assert page.evaluate(ODCZYT_JS, "time-ends") is True
        assert page.evaluate(ODCZYT_JS, "dot-size") == 10
        assert page.evaluate(ODCZYT_JS, "tt-past") is False
        assert page.evaluate(ODCZYT_JS, "look-weight") == 5
        zapisane = json.loads(page.evaluate(f"localStorage.getItem('{KLUCZ}')"))
        assert zapisane["fold:Rower"] is True and "minOpacity" not in zapisane
        for stary in ("dev", "time", "dot", "look", "sound"):
            assert page.evaluate(f"localStorage.getItem('metal-planner:{stary}-prefs')") is None
        assert not page.bledy, page.bledy
    finally:
        context.close()


def test_poza_trybem_deweloperskim_zapis_dev_nie_dziala(adres, przegladarka, monkeypatch):
    monkeypatch.setenv("DEV_MODE", "")
    dev = next(w for w in USTAWIENIA if w["dev"] and w["kind"] == "range" and not w["hidden"])
    zwykle = next(w for w in USTAWIENIA if not w["dev"] and w["kind"] == "checkbox" and not w["hidden"])
    zapis = {dev["id"]: _inna(dev), zwykle["id"]: _inna(zwykle)}
    context, page = _otworz(przegladarka, adres,
                            f"localStorage.setItem('{KLUCZ}', {json.dumps(json.dumps(zapis))})")
    try:
        assert page.evaluate(ODCZYT_JS, dev["id"]) == _domyslna(dev)
        assert page.evaluate(ODCZYT_JS, zwykle["id"]) == _inna(zwykle)
        assert not page.bledy, page.bledy
    finally:
        context.close()
