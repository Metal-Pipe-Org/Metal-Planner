"""Okienka nowości: rejestr nowosci.json (schemat nowosci.schema.json) i zapis pierwszego zobaczenia."""

import json
import re
from pathlib import Path

import nowosci

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
REGISTRY = json.loads(nowosci.REGISTRY_PATH.read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "nowosci.schema.json").read_text(encoding="utf-8"))
POPUPS = REGISTRY["popups"]


def _bledy(wartosc, schema, sciezka="$"):
    """Sprawdza wartość względem schemy w zakresie, którego używa nowosci.schema.json
    (type, required, properties, additionalProperties, items, minLength, minimum,
    const, anyOf) - bez biblioteki jsonschema, której nie ma w venv."""
    if "anyOf" in schema:
        if any(not _bledy(wartosc, wariant, sciezka) for wariant in schema["anyOf"]):
            return []
        return [f"{sciezka}: nie pasuje do żadnego z wariantów"]
    if "const" in schema:
        return [] if wartosc == schema["const"] else [f"{sciezka}: oczekiwano {schema['const']!r}"]
    typy = {"object": dict, "array": list, "string": str, "integer": int}
    if not isinstance(wartosc, typy[schema["type"]]) or isinstance(wartosc, bool):
        return [f"{sciezka}: zły typ"]
    bledy = []
    if isinstance(wartosc, str):
        if len(wartosc) < schema.get("minLength", 0):
            bledy.append(f"{sciezka}: pusty napis")
    elif isinstance(wartosc, int):
        if wartosc < schema.get("minimum", wartosc):
            bledy.append(f"{sciezka}: poniżej {schema['minimum']}")
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


def _place_exists(place):
    # Selektory spoza prostych (#id, .klasa) trzeba sprawdzić w przeglądarce.
    if re.fullmatch(r"#[\w-]+", place):
        return f'id="{place[1:]}"' in INDEX
    if re.fullmatch(r"\.[\w-]+", place):
        return re.search(r'class="[^"]*\b' + re.escape(place[1:]) + r'\b', INDEX) is not None
    return True


def test_rejestr_jest_zgodny_ze_schema():
    assert REGISTRY["$schema"] == "nowosci.schema.json"
    assert (ROOT / REGISTRY["$schema"]).is_file()
    assert _bledy(REGISTRY, SCHEMA) == []


def test_schema_odrzuca_bledne_wpisy():
    dobry = {"id": "x", "title": "X", "description": "Y", "place": "#z"}

    def wpis(**zmiany):
        return {"popups": [{**dobry, **zmiany}]}

    assert _bledy(wpis(), SCHEMA) == []
    assert _bledy(wpis(expiry=14), SCHEMA) == []
    assert _bledy(wpis(expiry="never"), SCHEMA) == []
    assert _bledy(wpis(expiry=13), SCHEMA)
    assert _bledy(wpis(expiry=14.5), SCHEMA)
    assert _bledy(wpis(expiry=True), SCHEMA)
    assert _bledy(wpis(expiry="always"), SCHEMA)
    assert _bledy(wpis(nieznane=1), SCHEMA)
    assert _bledy({"popups": [{"id": "x", "title": "X", "description": "Y"}]}, SCHEMA)


def test_id_sa_unikalne():
    ids = [entry["id"] for entry in POPUPS]
    assert len(ids) == len(set(ids))


def test_place_wskazuje_element_szablonu():
    for entry in POPUPS:
        assert _place_exists(entry["place"]), f"wpis {entry['id']}: {entry['place']} nie ma w szablonie"


def test_pierwsze_zobaczenie_nie_nadpisuje_daty(tmp_path, monkeypatch):
    seen_path = tmp_path / "nowosci-widziane.json"
    monkeypatch.setattr(nowosci, "SEEN_PATH", seen_path)
    monkeypatch.setattr(nowosci.time, "time", lambda: 1000)
    assert nowosci.first_seen(["a"]) == {"a": 1000000}
    monkeypatch.setattr(nowosci.time, "time", lambda: 2000)
    assert nowosci.first_seen(["a", "b"]) == {"a": 1000000, "b": 2000000}
    assert json.loads(seen_path.read_text()) == {"a": 1000000, "b": 2000000}
