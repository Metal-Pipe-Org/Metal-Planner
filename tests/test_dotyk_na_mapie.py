"""Mapa przepływów pod palcem (zgłoszenie #274).

Na komputerze linię wskazuje kursor: najechanie na linię albo na jej numer
w grupce podświetla ją i otwiera dymek z jej numerem. Palcem najechać się nie
da, więc na telefonie to samo robi stuknięcie - i to stuknięcie trochę obok,
bo palec zasłania to, w co celuje. Każda kropka ma cel wielkości palca, a gdy
jedno stuknięcie sięga kilku rzeczy, wygrywa ważniejsza: kropka zawsze przed
linią. Stuknięcie w linię nie może przy tym przestawić celu podróży.
Przytrzymanie palcem na mapie nie wyciąga menu zaznaczania tekstu.

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

# Tyle pikseli obok linii albo kropki trafia zwykły palec - stuknięcie tak
# daleko ma jeszcze trafić.
OBOK_PX = 12

# Miejsce do stuknięcia, szukane w tym, co Leaflet naprawdę narysował (linie
# i kropki są na płótnie, więc po warstwach z mostka aplikacji). Zawsze
# w kadrze, z dala od nakładek, numerów i kropek, które nie są celem:
#   {tryb: 'obok', odl}      - `odl` px od najbliższej linii przepływu,
#   {tryb: 'puste', odl}     - co najmniej `odl` px od wszystkiego,
#   {tryb: 'kropka', rodzaj, odl, naLinii}
#                            - `odl` px od środka kropki danego rodzaju
#                              ('przesiadka' albo 'przystanek' wskazanej
#                              linii); z naLinii - na samej linii.
SZUKAJ = """({tryb, odl, rodzaj, naLinii}) => {
    const {map, LINE_COLORS} = window.plannerBridge;
    const colors = new Set(Object.values(LINE_COLORS));
    const box = document.getElementById('map').getBoundingClientRect();
    const segs = [], dots = [];
    map.eachLayer(l => {
        if (l instanceof L.CircleMarker) {
            const o = l.options;
            if (o.interactive === false && o.radius < 4) return;
            let kind = null;
            if (o.interactive !== false && o.fillColor === '#fff' && o.color === '#263238') {
                kind = 'przesiadka';
            } else if (o.interactive !== false && o.fillColor === '#fff' && o.radius === 4.5) {
                kind = 'przystanek';
            }
            dots.push({at: map.latLngToContainerPoint(l.getLatLng()), kind, big: o.radius >= 8, small: o.radius < 3.5,
                       keep: o.radius >= 3.5 ? 30 : 6});
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
    const chips = [...document.querySelectorAll('.line-chip-slot')].map(el => {
        const r = el.getBoundingClientRect();
        return {left: r.left - box.left, right: r.right - box.left,
                top: r.top - box.top, bottom: r.bottom - box.top};
    });
    // Przy szukaniu miejsca obok kropki inne kropki mogą stać blisko, byle
    // dalej niż ona (przy równej randze wygrywa bliższa), a start i cel - poza
    // zasięgiem palca, bo mają pierwszeństwo przed wszystkim. Numery
    // w grupkach mają rangę niższą od każdej kropki.
    const free = (p, own) => {
        if (p.y < 230 || p.y > 620 || p.x < 30 || p.x > box.width - 80) return false;
        const keep = d => (own ? (d.big ? 26 : d.small ? 4 : odl + 3) : d.keep);
        if (dots.some(d => d !== own && Math.hypot(d.at.x - p.x, d.at.y - p.y) < keep(d))) return false;
        if (!own && chips.some(c => p.x > c.left - 30 && p.x < c.right + 30
                                    && p.y > c.top - 30 && p.y < c.bottom + 30)) return false;
        const el = document.elementFromPoint(p.x + box.left, p.y + box.top);
        return !!el && (el.tagName === 'CANVAS' || (!!own && !!el.closest('.line-cluster')));
    };
    const out = p => ({x: p.x + box.left, y: p.y + box.top});
    if (tryb === 'obok') {
        for (const [a, b] of segs) {
            const len = Math.hypot(b.x - a.x, b.y - a.y);
            if (len < 4) continue;
            const n = {x: -(b.y - a.y) / len, y: (b.x - a.x) / len};
            for (const t of [0.5, 0.25, 0.75]) {
                const on = {x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t};
                for (const side of [1, -1]) {
                    const p = {x: on.x + side * n.x * odl, y: on.y + side * n.y * odl};
                    const d = nearest(p);
                    if (d >= odl - 0.5 && d <= odl + 3 && free(on) && free(p)) return out(p);
                }
            }
        }
    } else if (tryb === 'puste') {
        for (let y = 240; y < 620; y += 7) {
            for (let x = 40; x < box.width - 80; x += 7) {
                const p = {x, y};
                if (nearest(p) >= odl && free(p)
                    && !dots.some(d => Math.hypot(d.at.x - x, d.at.y - y) < odl)) return out(p);
            }
        }
    } else {
        for (const dot of dots.filter(d => d.kind === rodzaj)) {
            const ring = [];
            if (naLinii) {
                for (const [a, b] of segs) {
                    for (let t = 0; t <= 1; t += 0.02) {
                        ring.push({x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t});
                    }
                }
            } else {
                for (let k = 0; k < 16; k++) {
                    const ang = k * Math.PI / 8;
                    ring.push({x: dot.at.x + odl * Math.cos(ang), y: dot.at.y + odl * Math.sin(ang)});
                }
            }
            for (const p of ring) {
                const d = Math.hypot(p.x - dot.at.x, p.y - dot.at.y);
                if (Math.abs(d - odl) <= 1.5 && free(p, dot)) return out(p);
            }
        }
    }
    return null;
}"""

DYMEK_LINII = ".leaflet-tooltip .flow-tip-line"
DYMEK_TABLICY = ".leaflet-tooltip.timetable-tip"
TABLICA = "#flow-panel-body"


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


def _szukaj(page, **co):
    punkt = page.evaluate(SZUKAJ, {"tryb": "obok", "odl": OBOK_PX, "rodzaj": None,
                                   "naLinii": False, **co})
    assert punkt, f"na mapie nie ma miejsca do stuknięcia: {co}"
    return punkt


def _stuknij(page, punkt):
    page.touchscreen.tap(punkt["x"], punkt["y"])


def _najedz(page, punkt):
    """Kursor na punkt - ale dopiero, gdy Leaflet znowu słucha ruchu myszy.
    Płótno przepuszcza najechanie raz na 32 ms, a ruch w tym oknie gubi
    bez śladu (bez ostatniego ruchu na końcu), więc ruch tuż po poprzednim
    bywa cichy i dymek zostaje po zjechaniu kursora z linii."""
    page.wait_for_function("""() => {
        const renderers = new Set();
        window.plannerBridge.map.eachLayer(l => l._renderer && renderers.add(l._renderer));
        return [...renderers].every(r => !r._mouseHoverThrottled);
    }""", timeout=2000)
    page.mouse.move(punkt["x"], punkt["y"])


def _tablica_przeszla(page, przed):
    """Na telefonie tablica odjazdów kropki nie wisi w dymku, tylko w okienku
    nad zakładkami - stuknięta kropka podmienia jego treść."""
    page.wait_for_function("""([sel, przed]) => {
        const el = document.querySelector(sel);
        return !el.closest('[hidden]') && el.innerText !== przed
            && !el.innerText.includes('Ładowanie');
    }""", arg=[TABLICA, przed], timeout=5000)


@pytest.fixture
def telefon(przegladarka, adres):
    context, page, bledy = _wyszukaj(przegladarka, adres, TELEFON)
    yield page
    context.close()
    assert not bledy


def test_stukniecie_obok_linii_wskazuje_ja(telefon):
    """Sedno #274: stuknięcie palcem w linię - choćby kawałek obok - robi to,
    co na komputerze najechanie: dymek z numerem linii. Cel podróży zostaje."""
    cel = telefon.input_value("#end")

    _stuknij(telefon, _szukaj(telefon))

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
    _stuknij(telefon, _szukaj(telefon))
    telefon.wait_for_selector(DYMEK_LINII, timeout=2000)
    cel = telefon.input_value("#end")

    _stuknij(telefon, _szukaj(telefon, tryb="puste", odl=40))

    telefon.wait_for_selector(DYMEK_LINII, state="detached", timeout=2000)
    assert telefon.input_value("#end") == cel


def test_stukniecie_obok_malej_kropki_trafia_ja(telefon):
    """Kropka przesiadki ma kilka pikseli, a cel wielkości palca: stuknięcie
    obok niej otwiera jej tablicę odjazdów, jak najechanie kursorem."""
    przed = telefon.inner_text(TABLICA)

    _stuknij(telefon, _szukaj(telefon, tryb="kropka", rodzaj="przesiadka", odl=OBOK_PX))

    _tablica_przeszla(telefon, przed)
    assert not telefon.is_visible(DYMEK_LINII)


def test_przesiadka_wygrywa_z_linia(telefon):
    """Przesiadka leży na linii. Stuknięcie w linię tuż przy niej sięga obu -
    wygrywa przesiadka, bo kropka zawsze idzie przed linią."""
    przed = telefon.inner_text(TABLICA)

    _stuknij(telefon, _szukaj(telefon, tryb="kropka", rodzaj="przesiadka",
                              odl=OBOK_PX, naLinii=True))

    _tablica_przeszla(telefon, przed)
    assert not telefon.is_visible(DYMEK_LINII)


def test_przystanek_wygrywa_z_linia(telefon):
    """Wskazana linia pokazuje swoje przystanki. Stuknięcie w nią tuż przy
    przystanku otwiera dymek przystanku, a linia zostaje wskazana - tak jak
    przy kursorze, który z linii zjeżdża na jej przystanek."""
    _stuknij(telefon, _szukaj(telefon))
    telefon.wait_for_selector(DYMEK_LINII, timeout=2000)

    _stuknij(telefon, _szukaj(telefon, tryb="kropka", rodzaj="przystanek",
                              odl=8, naLinii=True))

    telefon.wait_for_selector(".leaflet-tooltip:has(b):not(:has(.flow-tip-line))", timeout=2000)
    # Dymek linii znika jak przy zjechaniu kursorem na przystanek.
    telefon.wait_for_selector(DYMEK_LINII, state="detached", timeout=2000)
    # Podświetlenie linii zostaje - przystanki są jej częścią. Leży w osobnej
    # warstwie z ciemną otoczką (patrz showLineHighlight).
    assert telefon.evaluate("""() => {
        let halo = false;
        window.plannerBridge.map.eachLayer(l => {
            if (l instanceof L.Polyline && l.options.color === '#111') halo = true;
        });
        return halo;
    }""")


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
    """Komputer bez zmian: linię i kropkę wskazuje najechanie z wąskim
    marginesem kursora, a klik obok nie wskazuje ich jak stuknięcie."""
    context, page, bledy = _wyszukaj(przegladarka, adres, KOMPUTER)
    punkt = _szukaj(page, odl=2)
    _najedz(page, punkt)
    page.wait_for_selector(DYMEK_LINII, timeout=2000)
    # Palcem wskazałoby linię tak daleko - kursorem już nie.
    daleko = _szukaj(page)
    _najedz(page, daleko)
    page.wait_for_selector(DYMEK_LINII, state="detached", timeout=2000)
    page.mouse.click(daleko["x"], daleko["y"])
    page.wait_for_timeout(300)
    assert not page.is_visible(DYMEK_LINII)
    # To samo przy kropce przesiadki: palec by ją trafił, kursor nie.
    obok = _szukaj(page, tryb="kropka", rodzaj="przesiadka", odl=OBOK_PX)
    przed = page.inner_text(TABLICA)
    _najedz(page, obok)
    page.mouse.click(obok["x"], obok["y"])
    page.wait_for_timeout(500)
    assert not page.is_visible(DYMEK_TABLICY)
    assert page.inner_text(TABLICA) == przed
    context.close()
    assert not bledy
