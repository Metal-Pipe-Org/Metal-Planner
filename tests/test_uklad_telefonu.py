"""Układ na telefonie (zgłoszenie #117) - sprawdzany w PRAWDZIWEJ przeglądarce.

Telefon ma własny arkusz i własny skrypt (static/phone.css, static/phone.js),
ale zmiany robi się zwykle pod komputer i to one psuły telefon po cichu:
przycisk wystający poza kartę, pasek schowany pod okienkiem, przyciski +/−
pod zakładkami. Ten test zamiast pamiętania: otwiera stronę w rozmiarze
dwóch telefonów i jednego obróconego poziomo (ma zostać układem telefonu),
przechodzi przez wyszukanie trasy, zakładki i rozkład przystanku i w każdym
stanie mierzy, czy

  - strony nie da się przewinąć w bok,
  - żaden przycisk ani pole nie wystaje poza ekran ani poza swoją kartę,
  - na ekranie mapy nakładki (panel, warstwy, pasek z czasem, okienko
    z rozkładem, zakładki) nie leżą jedna na drugiej,

a po wyszukaniu - czy formularz zwinął się do jednej linijki. Do tego rozkład:
z pustego zakładka „Mapa" wychodzi sama, a z wybranym zostaje na mapie
z „Zamknij rozkład".

Trasa jest liczona na prawdziwej bazie rozkładów (o 12:00, żeby zawsze coś
jechało); żywe źródła - pojazdy, auta, rowery - dostają puste listy, żeby
wynik nie zależał od internetu. Leaflet i tak idzie z sieci.

Pomija się bez playwright, bez przeglądarki albo bez bazy rozkładów - tak
samo, jak emulator frontu pomija się bez osascript. Przeglądarka: ta od
playwright, a gdy jej wersja się nie zgadza, dowolny Chromium z jego cache'u.
"""

import glob
import json
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import gtfs

TELEFONY = {"iphone": (390, 844), "maly-android": (360, 640),
            "iphone-poziomo": (844, 390)}

TRASA = ("Sosnowiecka", "Wojszyce")
PRZYSTANEK = "Sosnowiecka"

PUSTE_ZRODLA = {
    "**/api/vehicles*": {"vehicles": []},
    "**/api/cars*": {"cars": []},
    "**/api/bikes*": {"stations": [], "free": []},
}

# Pomiar całego ekranu naraz, po stronie przeglądarki. Zwraca listę opisów
# tego, co jest nie tak - pusta lista to dobry układ.
UKLAD_JS = """async (mapView) => {
    // Jedna klatka na przestawienie się układu po kliknięciu (phone.js liczy
    // miejsce w ResizeObserverze, który odpala tuż przed rysowaniem).
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    const problems = [];
    const W = innerWidth, H = innerHeight;
    const visible = el => {
        if (!el || !el.getClientRects().length) return false;
        const s = getComputedStyle(el);
        const b = el.getBoundingClientRect();
        return s.visibility !== 'hidden' && b.width > 0 && b.height > 0;
    };
    const name = el => el.id ? '#' + el.id
        : el.className ? el.tagName.toLowerCase() + '.' + String(el.className).split(' ')[0]
        : el.tagName.toLowerCase();
    const EPS = 0.5;
    const inside = (a, b) => a.left >= b.left - EPS && a.right <= b.right + EPS
                          && a.top >= b.top - EPS && a.bottom <= b.bottom + EPS;

    if (document.documentElement.scrollWidth > W) {
        problems.push('strona przewija się w bok: ' + document.documentElement.scrollWidth
                      + ' px przy ekranie ' + W + ' px');
    }

    // Listy wyników i panel ⚙ przewijają się w sobie - to, co jest pod ich
    // krawędzią, jest po prostu niżej na liście, a nie wystaje.
    const controls = [...document.querySelectorAll('button, input, select, .tab')]
        .filter(el => visible(el) && !el.closest('.results, #tt-results, #dev-panel, .leaflet-container'));
    const screen = {left: 0, top: 0, right: W, bottom: H};
    for (const el of controls) {
        const box = el.getBoundingClientRect();
        if (!inside(box, screen)) problems.push(name(el) + ' wystaje poza ekran');
        const card = el.closest('#sidebar .card');
        if (card && !inside(box, card.getBoundingClientRect())) {
            problems.push(name(el) + ' wystaje poza swoją kartę');
        }
    }

    if (mapView) {
        const overlays = ['#sidebar', '.layer-bar', '#time-headline', '#flow-panel',
                          '#tt-results', '#view-tabs']
            .map(sel => document.querySelector(sel)).filter(el => visible(el) && !el.hidden);
        for (let i = 0; i < overlays.length; i++) {
            for (let j = i + 1; j < overlays.length; j++) {
                const a = overlays[i].getBoundingClientRect(), b = overlays[j].getBoundingClientRect();
                if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) {
                    problems.push(name(overlays[i]) + ' nachodzi na ' + name(overlays[j]));
                }
            }
        }
    }
    return problems;
}"""


def _chromium(playwright):
    try:
        return playwright.chromium.launch()
    except sync_api.Error:
        pass
    for path in sorted(glob.glob(str(Path.home() / "Library/Caches/ms-playwright/chromium-*"
                                     "/chrome-mac*/Google Chrome for Testing.app/Contents"
                                     "/MacOS/Google Chrome for Testing")), reverse=True):
        return playwright.chromium.launch(executable_path=path)
    pytest.skip("brak przeglądarki dla playwright (playwright install chromium)")


@pytest.fixture(scope="module")
def adres():
    try:
        gtfs.all_stop_names()
    except FileNotFoundError:
        pytest.skip("brak bazy rozkładów (data/gtfs.sqlite) - strona pokazałaby sam błąd")
    from werkzeug.serving import make_server
    import app as aplikacja
    serwer = make_server("127.0.0.1", 0, aplikacja.app, threaded=True)
    threading.Thread(target=serwer.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{serwer.server_port}/"
    serwer.shutdown()


@pytest.fixture(scope="module")
def przegladarka():
    with sync_api.sync_playwright() as p:
        browser = _chromium(p)
        yield browser
        browser.close()


def _odpowiedz(tresc):
    return lambda route: route.fulfill(status=200, content_type="application/json",
                                       body=json.dumps(tresc))


def _wybierz(page, pole, nazwa):
    page.fill(pole, nazwa)
    page.wait_for_selector(pole + "-list:not([hidden])")
    page.keyboard.press("Enter")


@pytest.fixture(scope="module", params=list(TELEFONY), ids=list(TELEFONY))
def przebieg(request, adres, przegladarka):
    """Jedno przejście przez aplikację na danym telefonie; zwraca pomiary
    z każdego stanu ekranu i błędy JS, które po drodze wypadły."""
    width, height = TELEFONY[request.param]
    context = przegladarka.new_context(
        viewport={"width": width, "height": height}, device_scale_factor=2,
        is_mobile=True, has_touch=True, locale="pl-PL", service_workers="block")
    page = context.new_page()
    bledy = []
    page.on("pageerror", lambda e: bledy.append(str(e)))
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())

    uklad = {}
    page.goto(adres)
    page.wait_for_selector("#start")
    telefon = page.is_visible("#view-tabs")
    uklad["start"] = page.evaluate(UKLAD_JS, True)

    page.fill("#time", "12:00")
    _wybierz(page, "#start", TRASA[0])
    _wybierz(page, "#end", TRASA[1])
    if not page.is_disabled("#search") and page.is_visible("#search"):
        page.click("#search")
    page.wait_for_selector("#time-headline:not([hidden])", timeout=20000)
    page.wait_for_timeout(1500)       # tablica przystanku startowego dochodzi osobno
    uklad["po wyszukaniu"] = page.evaluate(UKLAD_JS, True)
    zwiniety = {
        "linijka": page.is_visible(".search-summary"),
        "pola": page.is_visible("#start"),
        "wysokosc": page.evaluate("document.querySelector('.search-card').getBoundingClientRect().height"),
    }

    page.click(".search-summary")
    uklad["rozwinięty formularz"] = page.evaluate(UKLAD_JS, True)

    page.click('#view-tabs [data-view="list"]')
    uklad["zakładka Trasy"] = page.evaluate(UKLAD_JS, False)

    w_rozkladzie = "document.body.classList.contains('mode-timetable')"
    page.click('#view-tabs [data-view="timetable"]')
    uklad["zakładka Rozkład, nic nie wybrano"] = page.evaluate(UKLAD_JS, False)
    page.click('#view-tabs [data-view="map"]')
    z_pustego = {"wciaz_rozklad": page.evaluate(w_rozkladzie),
                 "wyszukiwarka": page.is_visible(".search-card")}

    page.click('#view-tabs [data-view="timetable"]')
    _wybierz(page, "#tt-query", PRZYSTANEK)
    page.wait_for_selector("#tt-results .tt-rows, #tt-results .card")
    page.wait_for_timeout(1000)
    uklad["zakładka Rozkład, przystanek"] = page.evaluate(UKLAD_JS, False)
    page.click('#view-tabs [data-view="map"]')
    z_wybranego = {"wciaz_rozklad": page.evaluate(w_rozkladzie),
                   "zamknij": page.is_visible("#tt-close")}
    uklad["rozkład na mapie"] = page.evaluate(UKLAD_JS, True)

    context.close()
    return {"telefon": telefon, "uklad": uklad, "zwiniety": zwiniety,
            "z_pustego": z_pustego, "z_wybranego": z_wybranego, "bledy": bledy}


def test_uklad_telefonu_obowiazuje(przebieg):
    """Kontrola samego scenariusza - także telefon obrócony poziomo ma układ
    telefonu (zakładki na dole), a nie komputerowy."""
    assert przebieg["telefon"]


def test_nic_nie_wystaje_ani_nie_nachodzi(przebieg):
    """Sedno #117: w żadnym stanie ekranu nic nie wystaje poza ekran ani poza
    kartę, a nakładki na mapie nie leżą jedna na drugiej."""
    problemy = {stan: p for stan, p in przebieg["uklad"].items() if p}
    assert not problemy, json.dumps(problemy, ensure_ascii=False, indent=2)


def test_wyszukanie_zwija_formularz_do_jednej_linijki(przebieg):
    """Po wyszukaniu formularz ustępuje mapie: zostaje jedna linijka
    „skąd → dokąd, godzina", pól nie widać."""
    z = przebieg["zwiniety"]
    assert z["linijka"] and not z["pola"], z
    assert z["wysokosc"] <= 64, z


def test_mapa_zamyka_pusty_rozklad(przebieg):
    """W rozkładzie nic nie wybrano - „Mapa" wraca od razu do wyszukiwania
    tras, bez zamykania czegoś, w czym nic nie ma."""
    p = przebieg["z_pustego"]
    assert not p["wciaz_rozklad"] and p["wyszukiwarka"], p


def test_wybrany_rozklad_zostaje_na_mapie(przebieg):
    """Z wybranym rozkładem „Mapa" pokazuje go na mapie, a wyjście to
    „Zamknij rozkład"."""
    p = przebieg["z_wybranego"]
    assert p["wciaz_rozklad"] and p["zamknij"], p


def test_bez_bledow_javascriptu(przebieg):
    """Błąd w skrypcie potrafi zostawić układ w połowie drogi - i przejść
    niezauważony, bo strona dalej się wyświetla."""
    assert not przebieg["bledy"], przebieg["bledy"]
