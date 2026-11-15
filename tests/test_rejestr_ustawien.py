"""Rejestr ustawień `settings.json` (zgłoszenie #478): jeden plik z nazwami, opisami i tagami."""

import json
import re
from datetime import date
from pathlib import Path

import pytest
from flask import Flask, render_template

import settings_registry

ROOT = Path(__file__).resolve().parent.parent


def _strona(dzis=date(2026, 11, 15), dev_mode=True):
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    with app.test_request_context("/"):
        return render_template("index.html", stops=[], abbreviations={}, lines=[],
                               data_error=None, form_time="12:00", form_date="2026-10-01",
                               settings=settings_registry.view(dzis), dev_mode=dev_mode)


def _wiersze_szablonu():
    szablon = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    return re.findall(r"\{\{ attrs\('([^']+)'\) \}\}", szablon)


def test_rejestr_jest_poprawnym_jsonem():
    wpisy = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    assert wpisy
    for wpis in wpisy:
        assert wpis["id"] and wpis["name"], wpis
        assert isinstance(wpis["tags"], list) and all(isinstance(t, str) for t in wpis["tags"]), wpis
        assert set(wpis) <= {"id", "name", "description", "tags", "obsolete"}, wpis
    ids = [w["id"] for w in wpisy]
    assert len(ids) == len(set(ids))


def test_kazdy_wiersz_ma_wpis_i_odwrotnie():
    wiersze = _wiersze_szablonu()
    ids = [w["id"] for w in settings_registry.load()]
    assert sorted(wiersze) == sorted(ids)
    assert wiersze == ids, "kolejność wpisów ma być kolejnością wierszy"


def test_obsolete_ma_poprawna_date():
    for wpis in settings_registry.load():
        if "obsolete" in wpis:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", wpis["obsolete"]), wpis
            date.fromisoformat(wpis["obsolete"])


def test_teksty_na_stronie_pochodza_z_rejestru():
    html = _strona()
    assert "Szare godziny w tablicy" in html
    assert 'data-tip="Odjazdy, na które już nie zdążysz' in html
    assert re.search(r'class="[^"]*dev-only[^"]*" data-setting="density" data-labels="Zagęszczenie, Mapa"', html)
    assert 'data-setting="tt-past" data-labels="Rozkład, Czas"' in html


@pytest.fixture
def rejestr(monkeypatch):
    def _ustaw(*wpisy):
        monkeypatch.setattr(settings_registry, "load", lambda: list(wpisy))
    return _ustaw


def test_nowy_tag_trafia_do_etykiet_bez_zmian_w_kodzie(rejestr):
    rejestr({"id": "x", "name": "X", "tags": ["Mapa", "rare", "dev"]})
    widok = settings_registry.view(date(2026, 1, 1))["x"]
    assert widok["labels"] == "Mapa, rare"
    assert widok["dev"] is True


def test_obsolete_ignoruje_inne_tagi_i_dev(rejestr):
    rejestr({"id": "x", "name": "X", "tags": ["Mapa", "dev"], "obsolete": "2026-11-15"})
    widok = settings_registry.view(date(2026, 11, 15))["x"]
    assert widok["labels"] == "obsolete"
    assert widok["dev"] is False
    assert not widok["expired"]
    assert widok["issue_url"].startswith("https://github.com/Metal-Pipe-Org/Metal-Planner/issues/new?title=")
    assert "Do+dyskusji%3A+ustawienie+X+oznaczone+jako+obsolete" in widok["issue_url"]


@pytest.mark.parametrize("dzis,znika", [
    (date(2026, 12, 14), False),
    (date(2026, 12, 15), True),
    (date(2027, 1, 20), True),
])
def test_obsolete_znika_z_menu_po_miesiacu(rejestr, dzis, znika):
    rejestr({"id": "x", "name": "X", "tags": ["Mapa"], "obsolete": "2026-11-15"})
    assert settings_registry.view(dzis)["x"]["expired"] is znika


def test_miesiac_na_koncu_miesiaca():
    assert settings_registry.one_month_later(date(2026, 1, 31)) == date(2026, 2, 28)
    assert settings_registry.one_month_later(date(2026, 12, 20)) == date(2027, 1, 20)


def _strona_z_obsolete(monkeypatch, dzis):
    wpisy = settings_registry.load()
    for wpis in wpisy:
        if wpis["id"] == "tt-past":
            wpis["obsolete"] = "2026-11-15"
    monkeypatch.setattr(settings_registry, "load", lambda: wpisy)
    return _strona(dzis=dzis)


def test_strona_obsolete_widoczny_z_ikonka_potem_schowany(monkeypatch):
    html = _strona_z_obsolete(monkeypatch, date(2026, 11, 15))
    wiersz = re.search(r'<label[^>]*data-setting="tt-past"[^>]*>', html).group(0)
    assert 'data-obsolete="2026-11-15"' in wiersz and "hidden" not in wiersz
    assert "dev-only" not in wiersz
    assert 'class="obsolete-info"' in html and 'target="_blank"' in html
    html = _strona_z_obsolete(monkeypatch, date(2026, 12, 20))
    wiersz = re.search(r'<label[^>]*data-setting="tt-past"[^>]*>', html).group(0)
    assert "data-expired" in wiersz and "hidden" in wiersz
