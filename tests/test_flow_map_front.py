"""Testy tej połowy kontraktu mapy, która mieszka WE FRONCIE.

Kontrakt (docs/FLOW_MAP_CONTRACT.md) ma 10 punktów, a test_flow_map_contract.py
sięga wyłącznie planner.py. Trzy punkty żyją jednak w całości albo w połowie
w static/app.js i do 2026-08-27 nie były sprawdzane niczym:

  7. zawsze wiadomo, co tam jedzie - kondensacja numerów w grupki, brak
     nachodzenia, kursor nazywający dokładnie jedną linię
     (placeLineLabels, clusterBox, flowHitsAt, corridorOptions);
  8. minimalna jasność nigdy nie spada do niewidoczności
     (LOOK_DEFAULTS i mapowanie w -> krycie/grubość);
 10. mapa mówi, ile to trwa i o której - interpolacja godziny między
     przystankami, kropka na linii (ensurePathMetrics, timeAtPos, timeAtHover).

Jak to jest uruchamiane: tests/js/harness.js podstawia minimalny Leaflet, DOM
i localStorage, po czym uruchamia PRAWDZIWY static/app.js (nie kopię, nie
wycinek) na PRAWDZIWEJ odpowiedzi /api/flow z tests/js/flow_fixture.json.
Sprawdzenia siedzą w tests/js/checks.js i wracają tu jako JSON - asercje są po
stronie Pythona, żeby komunikat błędu pokazywał zmierzoną liczbę.

Silnik JS: JavaScriptCore z systemu, przez `osascript -l JavaScript`. Wybrany
zamiast node+jsdom świadomie: projekt nie ma dziś ŻADNEJ zależności
javascriptowej ani kroku budowania, a na maszynie nie ma node. Cena: te testy
są macOS-owe i gdzie indziej się pominą (patrz pytest.skip niżej); gdyby
kiedyś doszło CI na Linuksie, trzeba będzie tu dołożyć drugi silnik.

Odświeżenie fixture'a po zmianie w plannerze:

    .venv/bin/python -c "
    from datetime import datetime; import planner, json
    r = planner.plan_flow('Sosnowiecka','Wojszyce', datetime(2026,8,27,15,37),
                          extra_pct=125, extra_floor_sec=300, extra_cap_sec=900)
    json.dump(r, open('tests/js/flow_fixture.json','w'),
              ensure_ascii=False, separators=(',',':'))"
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "tests" / "js"


def _bundle_source():
    """Jeden plik do podania JavaScriptCore: wejście, emulator, sprawdzenia."""
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    fixture = (JS_DIR / "flow_fixture.json").read_text(encoding="utf-8")
    return "\n".join([
        "const APP_SOURCE = " + json.dumps(app_js) + ";",
        "const FLOW_FIXTURE = " + fixture + ";",
        (JS_DIR / "harness.js").read_text(encoding="utf-8"),
        (JS_DIR / "checks.js").read_text(encoding="utf-8"),
    ])


@pytest.fixture(scope="session")
def front(tmp_path_factory):
    if shutil.which("osascript") is None:
        pytest.skip("brak osascript - emulator frontu działa na JavaScriptCore (macOS)")
    bundle = tmp_path_factory.mktemp("front") / "bundle.js"
    bundle.write_text(_bundle_source(), encoding="utf-8")
    proc = subprocess.run(
        ["osascript", "-l", "JavaScript", str(bundle)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        pytest.fail("emulator frontu nie wystartował (patrz tests/js/harness.js):\n"
                    + proc.stderr.strip())
    return json.loads(proc.stdout)


def _check(front, name):
    """Zwraca wynik sprawdzenia i asertuje jego `ok`, pokazując liczby."""
    assert name in front, f"brak sprawdzenia {name} w tests/js/checks.js"
    result = front[name]
    assert result["ok"], name + ": " + json.dumps(result, ensure_ascii=False)
    return result


# ----------------------------------------------------------------------- 7 -

def test_the_map_labels_its_corridors_at_all(front):
    """Kontrola samego scenariusza: na tej odpowiedzi w ogóle stają grupki
    numerów - inaczej wszystkie testy punktu 7 przechodziłyby na pusto."""
    result = _check(front, "p7_sa_grupki_numerow")
    assert result["grupek"] >= 5


def test_number_groups_never_overlap(front):
    """Sedno punktu 7: numery mają być czytelne, więc żadne dwie grupki nie
    mogą na siebie wchodzić. Liczone z tego samego pudełka (clusterBox), z
    którego liczy je sam app.js przy stawianiu."""
    _check(front, "p7_grupki_nie_nachodza_na_siebie")


def test_hovering_a_number_names_exactly_that_line(front):
    """Druga połowa punktu 7 (wersja z 2026-08-15): linie wspólnego korytarza
    leżą jedna na drugiej, a rozstrzyga o nich kursor nad KONKRETNYM numerem
    w grupce - i ma wskazać dokładnie tę linię, nie sąsiednią."""
    _check(front, "p7_kursor_nad_numerem_wskazuje_dokladnie_te_linie")


def test_every_group_stands_on_a_drawn_corridor(front):
    """Grupka opisuje skład korytarza z rozkładu - ale musi stać tam, gdzie
    naprawdę coś narysowano, inaczej mówi o linii, której w tym miejscu nie
    ma na mapie."""
    _check(front, "p7_kazda_grupka_opisuje_narysowany_korytarz")


# ----------------------------------------------------------------------- 8 -

def test_brightness_scale_never_reaches_invisibility(front):
    """Punkt 8: dolny kraniec skali to nadal widoczna linia, nie przezroczyste
    nic - i skala rośnie z jasnością, a nie skacze."""
    _check(front, "p8_skala_jasnosci")


def test_nothing_actually_drawn_is_invisible(front):
    """To samo, ale zmierzone na tym, co realnie poszło na mapę z odpowiedzi
    serwera - łącznie z kawałkiem o w = 0."""
    _check(front, "p8_nic_narysowane_nie_jest_niewidoczne")


# ---------------------------------------------------------------------- 10 -

def test_time_at_a_stop_comes_straight_from_the_schedule(front):
    """Punkt 10: na samym przystanku godzina ma być DOKŁADNIE tą z rozkładu -
    interpolacja nie ma prawa jej przesunąć nawet o sekundę."""
    _check(front, "p10_godzina_na_przystanku_jest_z_rozkladu")


def test_stop_anchors_run_along_the_line_in_order(front):
    """Kotwice przystanków na narysowanej łamanej idą po kolei - pętla ani
    nawrót trasy nie mogą cofnąć kolejności (patrz ensurePathMetrics)."""
    _check(front, "p10_kotwice_przystankow_rosna")


def test_time_never_goes_backwards_along_a_piece(front):
    """Między przystankami godzina rośnie monotonicznie - nigdzie na kawałku
    kursor nie może zobaczyć czasu wcześniejszego niż kawałek wstecz."""
    _check(front, "p10_godzina_rosnie_wzdluz_linii")


def test_the_time_dot_sits_on_the_line(front):
    """Kropka pokazuje punkt, którego dotyczy godzina - i leży na narysowanej
    linii, a nie obok niej."""
    _check(front, "p10_kropka_lezy_na_linii")


# ------------------------------------------------------- regresja UI --------

def test_a_dev_toggle_actually_changes_the_drawing(front):
    """Regresja, której do 2026-08-27 nie dało się w ogóle sprawdzić (brak
    node, AppleScript do żywej karty przeglądarki kończył się timeoutem):
    czy przełącznik w panelu deweloperskim zmienia rysunek. Godzina pod
    numerkiem powiększa grupkę w OBU wymiarach - i musi wejść do pomiaru
    kolizji, inaczej grupki zaczęłyby na siebie wchodzić."""
    _check(front, "przelacznik_czasu_zmienia_grupki")


# --------------------------------------------------- tryb awaryjny ---------

def test_fallback_mode_shows_a_notice_on_screen(front):
    """Gdy serwer przyśle mapę w trybie awaryjnym (samą najszybszą trasę
    zamiast wachlarza), ekran ma to powiedzieć - takim samym małym
    komunikatem, jak pozostałe błędy, dopisanym NAD listą, żeby pokazana
    trasa nadal była widoczna. Bez tego rzadka mapa wygląda identycznie jak
    "tędy naprawdę nic nie jedzie"."""
    _check(front, "tryb_awaryjny_mowi_o_sobie_na_ekranie")
