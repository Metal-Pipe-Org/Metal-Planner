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


def _bledy(wartosc, schema, sciezka="$"):
    """Sprawdza wartość względem schemy w zakresie, którego używa settings.schema.json
    (type, required, properties, additionalProperties, items, minLength, pattern) - bez
    biblioteki jsonschema, której nie ma w venv."""
    typy = {"object": dict, "array": list, "string": str}
    if not isinstance(wartosc, typy[schema["type"]]):
        return [f"{sciezka}: zły typ"]
    bledy = []
    if isinstance(wartosc, str):
        if len(wartosc) < schema.get("minLength", 0):
            bledy.append(f"{sciezka}: pusty napis")
        if "pattern" in schema and not re.search(schema["pattern"], wartosc):
            bledy.append(f"{sciezka}: nie pasuje do wzorca {schema['pattern']}")
    elif isinstance(wartosc, list):
        for i, element in enumerate(wartosc):
            bledy += _bledy(element, schema["items"], f"{sciezka}[{i}]")
    else:
        bledy += [f"{sciezka}: brak pola {k}" for k in schema.get("required", []) if k not in wartosc]
        for klucz, element in wartosc.items():
            if klucz in schema["properties"]:
                bledy += _bledy(element, schema["properties"][klucz], f"{sciezka}.{klucz}")
            elif schema.get("additionalProperties") is False:
                bledy.append(f"{sciezka}: nieznane pole {klucz}")
    return bledy


def test_rejestr_jest_poprawnym_jsonem_zgodnym_ze_schema():
    rejestr = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "settings.schema.json").read_text(encoding="utf-8"))
    assert rejestr["$schema"] == "settings.schema.json"
    assert (ROOT / rejestr["$schema"]).is_file()
    assert _bledy(rejestr, schema) == []
    ids = [w["id"] for w in rejestr["settings"]]
    assert ids and len(ids) == len(set(ids))


def test_schema_opisuje_kazde_pole_po_polsku():
    schema = json.loads((ROOT / "settings.schema.json").read_text(encoding="utf-8"))
    wpis = schema["properties"]["settings"]["items"]["properties"]
    assert set(wpis) == {"id", "name", "description", "tags", "obsolete"}
    for nazwa, pole in wpis.items():
        assert pole["description"], nazwa


def test_schema_odrzuca_bledne_wpisy():
    schema = json.loads((ROOT / "settings.schema.json").read_text(encoding="utf-8"))
    dobry = {"id": "x", "name": "X", "tags": ["Mapa"]}

    def wpis(**zmiany):
        return {"settings": [{**dobry, **zmiany}]}

    assert _bledy(wpis(), schema) == []
    assert _bledy(wpis(obsolete="2026-11-15"), schema) == []
    assert _bledy(wpis(obsolete="15.11.2026"), schema)
    assert _bledy(wpis(nieznane=1), schema)
    assert _bledy({"settings": [{"id": "x", "name": "X"}]}, schema)


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
