"""Wybór godziny i dnia na komputerze (#501): okienko przy pastylce z polem
godziny do wpisania i dniami do kliknięcia, zamiast kół z telefonu.

Zegar w teście jest podstawiony (page.clock) na dzisiejsze południe.
"""

import datetime

from test_uklad_telefonu import PUSTE_ZRODLA, _odpowiedz, adres, przegladarka  # noqa: F401


def _otworz(przegladarka, adres, ekran=(1280, 720)):
    context = przegladarka.new_context(viewport={"width": ekran[0], "height": ekran[1]},
                                       locale="pl-PL", service_workers="block")
    page = context.new_page()
    page.clock.install(time=datetime.datetime.combine(datetime.date.today(), datetime.time(12, 0)))
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.goto(adres)
    page.wait_for_selector(".when-pill")
    page.click(".when-pill")
    return context, page


def _pastylka(page):
    return page.inner_text(".when-pill").replace("▾", "").strip()


def test_okienko_bez_kol_z_polem_godziny_i_dniami(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        assert page.query_selector(".when-sheet .when-wheel") is None
        assert page.evaluate("document.activeElement.classList.contains('when-time')")
        pastylka = page.locator(".when-pill").bounding_box()
        okienko = page.locator(".when-sheet").bounding_box()
        assert okienko["y"] >= pastylka["y"] + pastylka["height"]
        assert okienko["y"] + okienko["height"] <= 720
        assert page.inner_text(".when-day.on") == "Dziś"
    finally:
        context.close()


def test_wpisana_godzina_i_enter(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        page.keyboard.type("1745")
        assert _pastylka(page) == "Odjazd 17:45, dziś"
        page.keyboard.press("Enter")
        assert page.is_hidden(".when-sheet")
        assert page.input_value("#time") == "17:45"
    finally:
        context.close()


def test_niewpisana_godzina_zostaje_teraz(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        page.keyboard.press("Enter")
        assert _pastylka(page) == "Odjazd teraz"
    finally:
        context.close()


def test_esc_cofa_zmiany(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        page.keyboard.type("0815")
        page.click('.when-day >> text="Jutro"')
        page.keyboard.press("Escape")
        assert page.is_hidden(".when-sheet")
        assert _pastylka(page) == "Odjazd teraz"
    finally:
        context.close()


def test_strzalki_i_dni(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        page.keyboard.type("2350")
        page.keyboard.press("End")
        page.keyboard.press("ArrowUp")
        assert page.input_value(".when-time") == "23:55"
        page.keyboard.press("ArrowUp")
        # przez północ: następny dzień
        assert _pastylka(page) == "Odjazd 00:00, jutro"
        page.keyboard.press("Home")
        page.keyboard.press("ArrowUp")
        assert page.input_value(".when-time") == "01:00"
        page.click('.when-day >> text="Dziś"')
        assert _pastylka(page) == "Odjazd 01:00, dziś"
        page.keyboard.press("ArrowRight")
        assert _pastylka(page) == "Odjazd 01:00, jutro"
    finally:
        context.close()


def test_tab_nie_ucieka_z_okienka(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        for _ in range(8):
            page.keyboard.press("Tab")
            assert page.evaluate("!!document.activeElement.closest('.when-sheet')")
    finally:
        context.close()


def test_przyjazd_bez_teraz_i_wpisywanie_po_kliknieciu_zakladki(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        page.click('[data-mode="arrive"]')
        assert page.is_hidden("[data-now]")
        assert page.evaluate("!!document.activeElement.closest('.when-sheet')")
        page.fill(".when-time", "930")
        page.press(".when-time", "Enter")
        assert _pastylka(page) == "Przyjazd 09:30, dziś"
    finally:
        context.close()


def test_klik_w_puste_miejsce_nie_gasi_klawiszy(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        page.keyboard.type("0815")
        gotowe = page.locator("[data-done]").bounding_box()
        page.mouse.click(gotowe["x"] - 30, gotowe["y"] + 10)
        page.keyboard.press("Escape")
        assert page.is_hidden(".when-sheet")
        assert _pastylka(page) == "Odjazd teraz"
        page.click(".when-pill")
        page.keyboard.type("0815")
        page.mouse.click(gotowe["x"] - 30, gotowe["y"] + 10)
        page.keyboard.press("Enter")
        assert _pastylka(page) == "Odjazd 08:15, dziś"
    finally:
        context.close()


def test_powrot_do_odjazdu_bez_zmian_oddaje_teraz(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        page.click('[data-mode="arrive"]')
        assert _pastylka(page) == "Przyjazd 13:00, dziś"
        page.click('[data-mode="depart"]')
        page.click("[data-done]")
        assert _pastylka(page) == "Odjazd teraz"
    finally:
        context.close()


def test_pastylka_nie_lapie_godziny_w_trakcie_pisania(przegladarka, adres):
    context, page = _otworz(przegladarka, adres)
    try:
        page.keyboard.type("250")
        assert _pastylka(page) == "Odjazd teraz"
        page.keyboard.type("0")
        assert _pastylka(page) == "Odjazd teraz"
        assert "bad" in page.get_attribute(".when-time", "class")
    finally:
        context.close()


def test_waskie_okno_z_myszka_ma_okienko_a_nie_kola(przegladarka, adres):
    context, page = _otworz(przegladarka, adres, (700, 800))
    try:
        assert page.query_selector(".when-sheet .when-wheel") is None
        assert page.is_visible(".when-sheet .when-time")
        okienko = page.locator(".when-sheet").bounding_box()
        assert okienko["x"] >= 0 and okienko["x"] + okienko["width"] <= 700
    finally:
        context.close()


def test_niskie_okno_nie_zaslania_pastylki(przegladarka, adres):
    for wysokosc in (600, 500):
        context, page = _otworz(przegladarka, adres, (1280, wysokosc))
        try:
            a = page.locator(".when-sheet").bounding_box()
            b = page.locator(".when-pill").bounding_box()
            zachodzi = (a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"]
                        and a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"])
            assert not zachodzi, (wysokosc, a, b)
            assert a["y"] >= 0 and a["y"] + a["height"] <= wysokosc
        finally:
            context.close()
