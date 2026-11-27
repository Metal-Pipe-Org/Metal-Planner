"""Sekcje ⚙ dla każdego i tylko dla dewelopera (zgłoszenie #175).

Sekcja DEV nosi klasę `dev-only` i znaczek; bez `DEV_MODE` strona nie ma
klasy `dev-mode` na <body>, więc style.css ją chowa.
"""

import re
from pathlib import Path

import pytest
import settings_registry
from flask import Flask, render_template

ROOT = Path(__file__).resolve().parent.parent

DEV_ROWS = {"car-count", "bike-count", "time-hover", "time-ends",
            "time-chips", "time-ride", "dot-center", "dot-rows", "names-zoom",
            "tip-cursor", "tip-panel", "walk-time", "bike-rides", "car-times",
            "zone-button", "debug-why", "dot-size",
            "look-weight", "look-dim", "look-label-step", "look-label-size",
            "look-label-op", "look-dump", "siechnice-file", "dev-access", "pushward", "pushward-link", "sw-check",
            "transfer-gain", "onboard-slack", "location-refresh", "stats",
            "order-limit", "walking", "headline-map-range",
            "vehicles-direction", "vehicles-stats", "server-logs"}
USER_ROWS = {"time-show-headline", "tt-past", "car-vans", "cars-horizon", "walk-pace",
             "bike-kmh", "bike-overhead", "car-kmh", "car-overhead",
             "bikes-merged", "no-pull-refresh", "start-mode-switch",
             "routes-on", "via-open", "sound-level", "line-colors", "scheme-on",
             "map-after-search"}


def _index(dev_mode):
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    with app.test_request_context("/"):
        return render_template("index.html", stops=[], abbreviations={},
                               lines=[], data_error=None, form_time="12:00",
                               form_date="2026-10-01", settings=settings_registry.view(), dev_mode=dev_mode)


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


def test_kazdy_wiersz_ustawien_ma_atrybut_etykiet():
    """Nagłówki listy składają się z etykiet; wiersz z pustą listą tagów jest dozwolony
    (ląduje w „Inne”), ale atrybut `data-labels` musi być."""
    tags = re.findall(r'<(?:div|label)\s[^>]*\bset-row\b[^>]*>', _index(dev_mode=True))
    assert tags
    for tag in tags:
        labels = re.search(r'data-labels="([^"]*)"', tag)
        assert labels, tag
