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


# --- data deployu pod nazwą (#446) -----------------------------------------

def _index_z_deployem(deployed):
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    with app.test_request_context("/"):
        return render_template("index.html", stops=[], abbreviations={},
                               lines=[], data_error=None, form_time="12:00",
                               form_date="2026-10-01", dev_mode=True,
                               deployed=deployed)


def test_deploy_z_pliku_budowy_obrazu(tmp_path, monkeypatch):
    plik = tmp_path / "DEPLOYED_AT"
    plik.write_text("2026-11-09T13:05:00Z\n", encoding="utf-8")
    monkeypatch.setattr(config, "DEPLOYED_AT_PATH", plik)
    when = config.deployed_at()
    assert when.tzinfo is not None
    assert when.timestamp() == 1794229500


def test_lokalnie_bez_pliku_liczy_sie_start_serwera(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEPLOYED_AT_PATH", tmp_path / "brak")
    assert config.deployed_at() == config._STARTED_AT


def test_zepsuty_plik_jak_brak(tmp_path, monkeypatch):
    plik = tmp_path / "DEPLOYED_AT"
    plik.write_text("wczoraj", encoding="utf-8")
    monkeypatch.setattr(config, "DEPLOYED_AT_PATH", plik)
    assert config.deployed_at() == config._STARTED_AT


def test_data_deployu_pod_nazwa():
    from datetime import datetime, timedelta, timezone
    when = datetime(2026, 11, 9, 14, 5, tzinfo=timezone(timedelta(hours=1)))
    html = _index_z_deployem(when)
    assert 'class="deployed-at" datetime="2026-11-09T14:05:00+01:00">Deployed 9.11.2026, 14:05</time>' in html


def test_bez_trybu_deweloperskiego_nie_ma_daty():
    assert "deployed-at" not in _index(dev_mode=False)

