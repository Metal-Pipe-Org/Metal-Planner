"""Mapa przepływów pod palcem (zgłoszenie #274).

Na komputerze linię wskazuje kursor: najechanie na linię albo na jej numer
w grupce podświetla ją i otwiera dymek z jej numerem. Palcem najechać się nie
da, więc na telefonie to samo robi stuknięcie - i to stuknięcie trochę obok,
bo palec zasłania cienką linię, w którą celuje. Stuknięcie w linię nie może
przy tym przestawić celu podróży. Przytrzymanie palcem na mapie nie wyciąga menu zaznaczania tekstu.

Ta sama przeglądarka i ten sam serwer co test układu telefonu - i tak samo
pomija się bez playwright albo bez bazy rozkładów.
"""

import re
from pathlib import Path

import pytest

from test_uklad_telefonu import (PUSTE_ZRODLA, TRASA, _odpowiedz, _wybierz,  # noqa: F401
                                 adres, przegladarka)

TELEFON = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2,
           "is_mobile": True, "has_touch": True}
KOMPUTER = {"viewport": {"width": 1280, "height": 800}}
ARKUSZ = Path(__file__).resolve().parent.parent / "static" / "style.css"

# Tyle pikseli obok linii trafia zwykły palec - stuknięcie tak daleko ma
# jeszcze wskazać linię.
OBOK_PX = 12

# Punkt na ekranie, który leży `obok` pikseli od najbliższej narysowanej linii
# przepływu (między `obok` a `obok` + 3) - z dala od kropek (one mają własne
# dymki) i od nakładek. Linie mapy są na płótnie, więc szuka się ich po
# warstwach Leafleta z mostka aplikacji.
PUNKT_OBOK_LINII = """(obok) => {
    const {map, LINE_COLORS} = window.plannerBridge;
    const colors = new Set(Object.values(LINE_COLORS));
    const box = document.getElementById('map').getBoundingClientRect();
    const segs = [], dots = [];
    map.eachLayer(l => {
        if (l instanceof L.CircleMarker) {
            if (l.options.interactive !== false) {
                dots.push({at: map.latLngToContainerPoint(l.getLatLng()),
                           keep: l.options.radius >= 4 ? 30 : 6});
            }
        } else if (l instanceof L.Polyline && colors.has(l.options.color)) {
            const pts = l.getLatLngs().map(ll => map.latLngToContainerPoint(ll));
            for (let i = 1; i < pts.length; i++) segs.push([pts[i - 1], pts[i]]);
        }
    });
    const distSeg = (p, [a, b]) => {
        const dx = b.x - a.x, dy = b.y - a.y, len2 = dx * dx + dy * dy;
        const t = len2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2)) : 0;
        return Math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy);
    };
    const nearest = p => Math.min(...segs.map(s => distSeg(p, s)));
    const free = p => {
        if (p.y < 250 || p.y > 600 || p.x < 30 || p.x > box.width - 80) return false;
        if (dots.some(d => Math.hypot(d.at.x - p.x, d.at.y - p.y) < d.keep)) return false;
        const el = document.elementFromPoint(p.x + box.left, p.y + box.top);
        return !!el && el.tagName === 'CANVAS';
    };
    for (const [a, b] of segs) {
        const len = Math.hypot(b.x - a.x, b.y - a.y);
        if (len < 4) continue;
        const n = {x: -(b.y - a.y) / len, y: (b.x - a.x) / len};
        for (const t of [0.5, 0.25, 0.75]) {
            const on = {x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t};
            for (const side of [1, -1]) {
                const p = {x: on.x + side * n.x * obok, y: on.y + side * n.y * obok};
                const d = nearest(p);
                if (d >= obok - 0.5 && d <= obok + 3 && free(on) && free(p)) {
                    return {x: p.x + box.left, y: p.y + box.top};
                }
            }
        }
    }
    return null;
}"""

DYMEK_LINII = ".leaflet-tooltip .flow-tip-line"


def _wyszukaj(przegladarka, adres, ekran):
    context = przegladarka.new_context(locale="pl-PL", service_workers="block", **ekran)
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
    return context, page, bledy


@pytest.fixture
def telefon(przegladarka, adres):
    context, page, bledy = _wyszukaj(przegladarka, adres, TELEFON)
    yield page
    context.close()
    assert not bledy


def test_stukniecie_obok_linii_wskazuje_ja(telefon):
    """Sedno #274: stuknięcie palcem w linię - choćby kawałek obok - robi to,
    co na komputerze najechanie: dymek z numerem linii. Cel podróży zostaje."""
    punkt = telefon.evaluate(PUNKT_OBOK_LINII, OBOK_PX)
    assert punkt, "na mapie nie ma wolnego kawałka linii do stuknięcia"
    cel = telefon.input_value("#end")

    telefon.touchscreen.tap(punkt["x"], punkt["y"])

    telefon.wait_for_selector(DYMEK_LINII, timeout=2000)
    assert telefon.input_value("#end") == cel


def test_stukniecie_w_numer_wskazuje_te_linie(telefon):
    """Stuknięcie w numer w grupce wskazuje DOKŁADNIE tę linię - jak najechanie
    na numer kursorem."""
    numer = telefon.locator(".line-cluster .line-chip-slot").first
    tekst = numer.locator(".line-chip").inner_text()
    cel = telefon.input_value("#end")

    numer.tap()

    telefon.wait_for_selector(DYMEK_LINII, timeout=2000)
    assert telefon.inner_text(DYMEK_LINII).split()[-1] == tekst
    assert telefon.input_value("#end") == cel


def test_stukniecie_w_pusta_mape_gasi_wskazana_linie(telefon):
    """Zjechanie kursorem z linii gasi ją - palcem to stuknięcie obok. Gasi
    tylko ją: cel podróży nie przeskakuje w stuknięte miejsce."""
    punkt = telefon.evaluate(PUNKT_OBOK_LINII, OBOK_PX)
    telefon.touchscreen.tap(punkt["x"], punkt["y"])
    telefon.wait_for_selector(DYMEK_LINII, timeout=2000)
    puste = telefon.evaluate(PUNKT_OBOK_LINII, 40)
    assert puste, "na mapie nie ma pustego miejsca obok linii"
    cel = telefon.input_value("#end")

    telefon.touchscreen.tap(puste["x"], puste["y"])

    telefon.wait_for_selector(DYMEK_LINII, state="detached", timeout=2000)
    assert telefon.input_value("#end") == cel


def test_przytrzymanie_na_mapie_nie_zaznacza_tekstu(telefon):
    """Przytrzymanie palcem na mapie nie zaznacza napisów i nie wyciąga menu
    „Kopiuj / Przetłumacz". Menu to `-webkit-touch-callout` - zna je tylko
    Safari, więc Chromium wyrzuca je przy czytaniu arkusza i sprawdza się je
    w samym arkuszu."""
    styl = telefon.evaluate("""() => {
        const s = getComputedStyle(document.getElementById('map'));
        return [s.userSelect, s.webkitUserSelect];
    }""")
    assert styl == ["none", "none"]
    regula = re.search(r"^#map \{(.*?)^\}", ARKUSZ.read_text(), re.S | re.M)
    assert re.search(r"-webkit-touch-callout:\s*none;", regula.group(1))


def test_na_komputerze_dalej_wskazuje_kursor(przegladarka, adres):
    """Komputer bez zmian: linię wskazuje najechanie z wąskim marginesem
    kursora, a klik obok linii nie wskazuje jej jak stuknięcie."""
    context, page, bledy = _wyszukaj(przegladarka, adres, KOMPUTER)
    punkt = page.evaluate(PUNKT_OBOK_LINII, 2)
    assert punkt
    page.mouse.move(punkt["x"], punkt["y"])
    page.wait_for_selector(DYMEK_LINII, timeout=2000)
    # Palcem wskazałoby linię tak daleko - kursorem już nie.
    daleko = page.evaluate(PUNKT_OBOK_LINII, OBOK_PX)
    page.mouse.move(daleko["x"], daleko["y"])
    page.wait_for_selector(DYMEK_LINII, state="detached", timeout=2000)
    page.mouse.click(daleko["x"], daleko["y"])
    page.wait_for_timeout(300)
    assert not page.is_visible(DYMEK_LINII)
    context.close()
    assert not bledy
