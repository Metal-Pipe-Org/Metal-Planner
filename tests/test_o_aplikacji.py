"""Karta „O aplikacji” i tryb deweloperski (zgłoszenie #173).

Karta jest dla wszystkich; linki zespołu dokłada do niej dopiero `DEV_MODE=1`,
a brak wpisu ma znaczyć produkcję.
"""

from pathlib import Path

from flask import Flask, render_template

import config

ROOT = Path(__file__).resolve().parent.parent


def _index(dev_mode):
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    with app.test_request_context("/"):
        return render_template("index.html", stops=[], abbreviations={},
                               lines=[], data_error=None, form_time="12:00",
                               form_date="2026-10-01", dev_mode=dev_mode)


def test_tryb_deweloperski_domyslnie_wylaczony(monkeypatch):
    monkeypatch.delenv("DEV_MODE", raising=False)
    assert config.dev_mode() is False

    monkeypatch.setenv("DEV_MODE", "")
    assert config.dev_mode() is False


def test_tryb_deweloperski_wlacza_jedynka(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "1")
    assert config.dev_mode() is True


def test_karta_dla_wszystkich_bez_linkow_zespolu():
    html = _index(dev_mode=False)
    assert 'id="about"' in html
    assert "Zgłoś problem" in html
    assert "nieoficjalna" in html
    # Regulamin API Open Data PLK (pkt 3.3) wymaga tej nazwy przy publikacji danych.
    assert "PKP Polskie Linie Kolejowe S.A." in html
    assert "Tryb deweloperski</h3>" not in html


def test_tryb_deweloperski_dokłada_linki_zespolu():
    html = _index(dev_mode=True)
    assert "Tryb deweloperski</h3>" in html
    assert "https://metal-testing.sze.one/" in html
