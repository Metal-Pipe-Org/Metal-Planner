"""Sekcje ⚙ dla każdego i tylko dla dewelopera (zgłoszenie #175).

Sekcja DEV nosi klasę `dev-only` i znaczek; bez `DEV_MODE` strona nie ma
klasy `dev-mode` na <body>, więc style.css ją chowa.
"""

import re
from pathlib import Path

import pytest
from flask import Flask, render_template

ROOT = Path(__file__).resolve().parent.parent

DEV_ROWS = {"density", "car-count", "bike-count", "time-hover", "time-ends",
            "time-chips", "time-ride", "dot-center", "dot-rows", "names-zoom",
            "tip-cursor", "tip-panel", "bike-times", "bike-rides", "car-times",
            "zone-button", "debug-why", "debug-why-seg", "dot-size",
            "look-weight", "look-dim", "look-label-step", "look-label-size",
            "look-label-op", "look-dump", "siechnice-file", "sw-check",
            "transfer-gain", "onboard-slack", "location-refresh", "stats",
            "order-limit", "new-map", "walking",
            "scheme-layout", "scheme-walks-mode", "scheme-walks-count",
            "scheme-walks-style"}
USER_ROWS = {"time-show-headline", "tt-past", "car-vans", "walk-pace",
             "bike-kmh", "bike-overhead", "car-kmh", "car-overhead",
             "bikes-merged", "no-pull-refresh", "start-mode-switch",
             "routes-on", "via-open", "sound-pipe"}


def _index(dev_mode):
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    with app.test_request_context("/"):
        return render_template("index.html", stops=[], abbreviations={},
                               lines=[], data_error=None, form_time="12:00",
                               form_date="2026-10-01", dev_mode=dev_mode)


def _rows(html):
    """data-setting -> klasy wiersza listy ustawień."""
    return {m.group(2): m.group(1).split() for m in re.finditer(
        r'<(?:div|label) class="([^"]*)" data-setting="([^"]*)"', html)}


def test_tryb_deweloperski_na_body():
    assert '<body class="dev-mode">' in _index(dev_mode=True)
    assert "<body>" in _index(dev_mode=False)


@pytest.mark.parametrize("dev_mode", [False, True])
def test_podzial_wierszy_niezaleznie_od_trybu(dev_mode):
    rows = _rows(_index(dev_mode))
    assert {sid for sid, classes in rows.items() if "dev-only" in classes} == DEV_ROWS
    assert set(rows) == DEV_ROWS | USER_ROWS


def test_lista_ma_wyszukiwarke_i_zadnych_sekcji():
    html = _index(dev_mode=True)
    assert 'id="settings-search"' in html
    assert "dev-fold" not in html


def test_ustawienie_eksperymentu_jest_na_liscie():
    assert 'id="location-refresh"' in _index(dev_mode=True)


def test_kazdy_wiersz_ustawien_ma_etykiety():
    """Nagłówki listy składają się z etykiet - wiersz bez nich nie trafiłby do żadnej grupy."""
    tags = re.findall(r'<(?:div|label)\s[^>]*\bset-row\b[^>]*>', _index(dev_mode=True))
    assert tags
    for tag in tags:
        labels = re.search(r'data-labels="([^"]*)"', tag)
        assert labels and any(l.strip() for l in labels.group(1).split(",")), tag
