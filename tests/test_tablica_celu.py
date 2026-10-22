"""Przystanek docelowy na mapie (zgłoszenie #266) - "o której co dojeżdża".

Kropka celu jest zwykłą kropką przystanku mapy, tylko czerwoną: ta sama
tablica z tego samego węzła (planner._transfer_nodes), z liniami, którymi
się tu przyjeżdża. Kropkę dostaje też każdy przystanek, na którym narysowana
podróż kończy ostatni przejazd i dalej idzie pieszo - wcześniej miejsce,
w którym da się tylko wysiąść, kropki nie miało.

Część przeglądarkowa - tak jak test_uklad_telefonu - na prawdziwej bazie
rozkładów i w prawdziwym Chromium, na komputerze i na telefonie.
"""

import datetime
import json
import threading

import pytest

import gtfs
import planner
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)


def _dzien():
    """START -> CEL, pytanie o 1000.

      - 16 o 1300, w celu 1700 - wprost;
      - 1 o 1000 do X, tam 2 o 1300, w celu 1600 - z przesiadką;
      - 16 o 900 odjechała przed pytaniem - w celu 1300, ale nie ze startu;
      - 7 z INNE o 1200, w celu 1400 - dojeżdża, ale ze startu nie da się
        do niej wsiąść.
    """
    return make_day([
        {"trip_id": "16-wczesniej", "label": "Tramwaj 16", "headsign": "OSOBOWICE",
         "stops": [("START", 900, 900), ("CEL", 1300, 1300)]},
        {"trip_id": "16", "label": "Tramwaj 16", "headsign": "OSOBOWICE",
         "stops": [("START", 1300, 1300), ("CEL", 1700, 1700)]},
        {"trip_id": "1", "label": "Tramwaj 1", "headsign": "X",
         "stops": [("START", 1000, 1000), ("X", 1200, 1200)]},
        {"trip_id": "2", "label": "Autobus 2", "headsign": "KRZYKI",
         "stops": [("X", 1300, 1300), ("CEL", 1600, 1600)]},
        {"trip_id": "7", "label": "Tramwaj 7", "headsign": "OSOBOWICE",
         "stops": [("INNE", 1200, 1200), ("CEL", 1400, 1400)]},
    ])


def _wezel(wynik, nazwa):
    return next(n for n in wynik["nodes"] if n["name"] == nazwa)


def test_cel_to_zwykly_wezel_z_przyjazdami(install_day):
    """Cel jest węzłem jak każdy inny - z flagą `end` i liniami, którymi mapa
    tu dowozi, każda z godziną przyjazdu kursu z mapy."""
    install_day(_dzien())
    wynik = planner.plan_flow("START", "CEL", WHEN + datetime.timedelta(seconds=1000),
                              more=3)

    cel = _wezel(wynik, "CEL")
    assert cel["end"] is True
    assert "start" not in cel
    assert cel["lines"] == [
        {"num": "16", "kind": "tram", "headsign": "OSOBOWICE", "flow": "end", "arrive": 1700},
        {"num": "2", "kind": "bus", "headsign": "KRZYKI", "flow": "end", "arrive": 1600},
    ]
    # Tylko cel jest czerwony.
    assert [n["name"] for n in wynik["nodes"] if n.get("end")] == ["CEL"]
    assert "arrivals" not in wynik


def test_cel_idzie_za_mapa(install_day):
    """Bez „pokaż więcej" mapa nie rysuje 16 - węzeł celu też jej nie zna.
    7 i wcześniejsza 16 dojeżdżają, tylko nie z tego startu i nie o tej
    godzinie - nie ma ich nigdy."""
    install_day(_dzien())
    wynik = planner.plan_flow("START", "CEL", WHEN + datetime.timedelta(seconds=1000))

    assert [(l["num"], l["arrive"]) for l in _wezel(wynik, "CEL")["lines"]] == [("2", 1600)]
    assert {s["num"] for s in wynik["segments"]} == {"1", "2"}


def _dzien_z_dojsciem():
    """GRUNWALDZKI -> CEL: 146 dowozi do WOJSZYCE, stamtąd pieszo do CEL,
    który stoi tuż obok. 9 tylko po to, żeby CEL był przystankiem rozkładu -
    ze startu nie dowiezie."""
    day = make_day([
        {"trip_id": "146", "label": "Autobus 146", "headsign": "GAJ pętla",
         "stops": [("GRUNWALDZKI", 1000, 1000), ("WOJSZYCE", 2000, 2000)]},
        {"trip_id": "9", "label": "Autobus 9", "headsign": "CEL",
         "stops": [("INNE", 500, 500), ("CEL", 600, 600)]},
    ])
    lat, lon = day.stop_coords["WOJSZYCE"]
    day.stop_coords["CEL"] = (lat + 0.0004, lon)
    return day


def test_wysiadka_przed_dojsciem_ma_kropke(install_day):
    """Na WOJSZYCE da się tylko wysiąść, ale to tu kończy się przejazd
    podróży - przystanek dostaje zwykłą kropkę, nie czerwoną."""
    install_day(_dzien_z_dojsciem())
    wynik = planner.plan_flow("GRUNWALDZKI", "CEL", WHEN + datetime.timedelta(seconds=900))

    wysiadka = _wezel(wynik, "WOJSZYCE")
    assert "end" not in wysiadka
    assert wysiadka["lines"] == [
        {"num": "146", "kind": "bus", "headsign": "GAJ pętla", "flow": "end", "arrive": 2000}]
    # Do samego celu nic z mapy nie dojeżdża, więc węzła celu nie ma.
    assert not any(n.get("end") for n in wynik["nodes"])


def test_cel_z_punktu_bez_wezla(install_day):
    """Punkt na mapie nie jest przystankiem: ma swój znacznik, a kropkę
    dostaje przystanek, na którym się wysiada."""
    day = make_day([
        {"trip_id": "146", "label": "Autobus 146", "headsign": "GAJ pętla",
         "stops": [("GRUNWALDZKI", 1000, 1000), ("WOJSZYCE", 2000, 2000)]},
    ])
    install_day(day)
    lat, lon = day.stop_coords["WOJSZYCE"]
    wynik = planner.plan_flow("GRUNWALDZKI", "", WHEN + datetime.timedelta(seconds=900),
                              end_point=(lat + 0.0004, lon))

    assert [(l["num"], l["flow"], l["arrive"]) for l in _wezel(wynik, "WOJSZYCE")["lines"]] \
        == [("146", "end", 2000)]
    assert not any(n.get("end") for n in wynik["nodes"])


# ------------------------------------------------------------ przeglądarka ---

# Z Sosnowieckiej do Rynku część podróży kończy się na Pl. Orląt Lwowskich
# i dalej idzie pieszo - tam da się tylko wysiąść.
TRASA = ("Sosnowiecka", "Rynek")

EKRANY = {"komputer": ((1280, 800), False), "telefon": ((390, 844), True)}

PUSTE_ZRODLA = {
    "**/api/vehicles*": {"vehicles": []},
    "**/api/cars*": {"cars": []},
    "**/api/bikes*": {"stations": [], "free": []},
}

# Zmienne app.js nie są widoczne z page.evaluate, więc mapę Leafleta łapie
# haczyk doklejony przed skrypt aplikacji.
HACZYK_MAPY = "L.Map.addInitHook(function () { window.__mapa = this; });\n"

CZERWONY = "#b71c1c"   # obwódka ENDPOINT_STYLE.end

# Klikalne kropki na mapie: położenie i kolor obwódki. Leaflet rysuje na
# kanwie, więc nie ma elementów, które dałoby się znaleźć selektorem.
KROPKI_JS = """() => {
    const out = [];
    window.__mapa.eachLayer(l => {
        if (l instanceof L.CircleMarker && l.listens('click')) {
            const p = l.getLatLng();
            out.push({lat: p.lat, lon: p.lng, color: l.options.color});
        }
    });
    return out;
}"""

KROPKA_CELU_JS = """(kolor) => {
    const map = window.__mapa;
    let dot = null;
    map.eachLayer(l => {
        if (l.listens && l.listens('click') && l.options.color === kolor) dot = l;
    });
    const p = map.latLngToContainerPoint(dot.getLatLng());
    const box = map.getContainer().getBoundingClientRect();
    return [box.left + p.x, box.top + p.y];
}"""

# Tablica, jaką pokazałaby zwykła kropka tego samego węzła - stawiana tą samą
# drogą, co każda kropka mapy, tylko bez flagi celu.
ZWYKLA_KROPKA_JS = """(n) => {
    const dot = nodeDot({...n, end: undefined}, window.__deadline).addTo(window.__mapa);
    dot.fire('click');
}"""

PANEL_JS = "() => document.querySelector('#flow-panel-body').innerHTML"


def _chromium(playwright):
    from tests.test_uklad_telefonu import _chromium as uruchom
    return uruchom(playwright)


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
    # Bez playwright odpadają tylko testy przeglądarkowe, nie te wyżej.
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        browser = _chromium(p)
        yield browser
        browser.close()


def _odpowiedz(tresc):
    return lambda route: route.fulfill(status=200, content_type="application/json",
                                       body=json.dumps(tresc))


def _z_haczykiem(route):
    odpowiedz = route.fetch()
    route.fulfill(response=odpowiedz, body=HACZYK_MAPY + odpowiedz.text())


def _wybierz(page, pole, nazwa):
    page.fill(pole, nazwa)
    page.wait_for_selector(pole + "-list:not([hidden])")
    page.keyboard.press("Enter")


def _czekaj_na_tablice(page, nazwa):
    page.wait_for_function("""(nazwa) => {
        const body = document.querySelector('#flow-panel-body');
        const stop = body.querySelector('.tip-stop');
        return stop && stop.textContent === nazwa && body.querySelector('.tt-lines');
    }""", arg=nazwa, timeout=10000)


@pytest.fixture(scope="module", params=list(EKRANY), ids=list(EKRANY))
def przebieg(request, adres, przegladarka):
    """Wyszukanie, dotknięcie kropki celu, potem ta sama tablica ze zwykłej
    kropki tego węzła."""
    (width, height), telefon = EKRANY[request.param]
    context = przegladarka.new_context(
        viewport={"width": width, "height": height}, is_mobile=telefon,
        has_touch=telefon, locale="pl-PL", service_workers="block")
    page = context.new_page()
    bledy = []
    page.on("pageerror", lambda e: bledy.append(str(e)))
    for wzor, tresc in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(tresc))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.route("**/static/app.js*", _z_haczykiem)
    mapy = []

    def _zapisz_mape(route):
        odpowiedz = route.fetch()
        mapy.append(odpowiedz.text())
        route.fulfill(response=odpowiedz)
    page.route("**/api/flow*", _zapisz_mape)

    page.goto(adres)
    page.wait_for_selector("#start")
    page.fill("#time", "12:00")
    # Przy domyślnej gęstości 2 ta trasa nie ma przystanku z samą wysiadką -
    # przy 2,5 (dawnej domyślnej) ma go: Pl. Orląt Lwowskich.
    page.evaluate("document.getElementById('density').value = '2.5'")
    _wybierz(page, "#start", TRASA[0])
    _wybierz(page, "#end", TRASA[1])
    if not page.evaluate("document.body.classList.contains('searching')") \
            and page.is_visible("#search"):
        page.click("#search")
    page.wait_for_selector("#time-headline:not([hidden])", timeout=20000)
    page.wait_for_selector("#flow-panel-body .tt-lines", timeout=10000)
    # Odpowiedź przychodzi etapami (NDJSON) - mapa to ich złożenie.
    flow = {}
    for linijka in mapy[-1].splitlines():
        if linijka.strip():
            flow.update(json.loads(linijka))
    kropki = page.evaluate(KROPKI_JS)
    # Kadr na trasę przesuwa mapę z animacją - współrzędne kropki liczone
    # w jej trakcie trafiają obok, a dotknięcie w trakcie Leaflet połyka.
    page.wait_for_function("""() => {
        const m = window.__mapa;
        return !m._animatingZoom && !(m._panAnim && m._panAnim._inProgress);
    }""")
    page.wait_for_timeout(1000)

    wezel_celu = next(n for n in flow["nodes"] if n.get("end"))
    x, y = page.evaluate(KROPKA_CELU_JS, CZERWONY)
    if telefon:
        page.touchscreen.tap(x, y)
    else:
        page.mouse.move(x, y)
        page.mouse.click(x, y)
    _czekaj_na_tablice(page, wezel_celu["name"])
    cel = page.evaluate(PANEL_JS)

    page.evaluate(f"() => {{ window.__deadline = {json.dumps(flow['deadline_sec'])}; }}")
    page.evaluate(PANEL_JS.replace("innerHTML", "innerHTML = ''"))
    page.evaluate(ZWYKLA_KROPKA_JS, wezel_celu)
    _czekaj_na_tablice(page, wezel_celu["name"])
    zwykla = page.evaluate(PANEL_JS)
    context.close()
    return {"cel": cel, "zwykla": zwykla, "flow": flow, "kropki": kropki,
            "wezel_celu": wezel_celu, "bledy": bledy}


def test_kropka_celu_ma_tablice_zwyklej_kropki(przebieg):
    assert przebieg["cel"] == przebieg["zwykla"]
    # ...i to tablica z tym, czym się do celu przyjeżdża.
    assert "tt-flow-end" in przebieg["cel"]


def _ma_kropke(kropki, wezel, kolor=None):
    return any(abs(k["lat"] - lat) < 1e-6 and abs(k["lon"] - lon) < 1e-6
               and (kolor is None or k["color"] == kolor)
               for k in kropki
               for lat, lon in ((wezel["lat"], wezel["lon"]),
                                (wezel["clat"], wezel["clon"])))


def test_kropka_celu_jest_czerwona(przebieg):
    assert _ma_kropke(przebieg["kropki"], przebieg["wezel_celu"], CZERWONY)
    assert sum(k["color"] == CZERWONY for k in przebieg["kropki"]) == 1


def test_kazdy_wezel_ma_kropke_takze_sama_wysiadka(przebieg):
    wezly = przebieg["flow"]["nodes"]
    for wezel in wezly:
        assert _ma_kropke(przebieg["kropki"], wezel), wezel["name"]
    # Bez takiego przystanku test nie sprawdzałby nowej reguły.
    assert any(not n.get("end") and all(l["flow"] == "end" for l in n["lines"])
               for n in wezly)


def test_bez_bledow_javascriptu(przebieg):
    assert not przebieg["bledy"], przebieg["bledy"]
