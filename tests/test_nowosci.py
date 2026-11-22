"""Okienka nowości: rejestr static/nowosci/nowosci.json i zapis pierwszego zobaczenia."""

import json
import re
from pathlib import Path

import nowosci

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
POPUPS = json.loads(nowosci.REGISTRY_PATH.read_text(encoding="utf-8"))["popups"]


def _place_exists(place):
    # Selektory spoza prostych (#id, .klasa) trzeba sprawdzić w przeglądarce.
    if re.fullmatch(r"#[\w-]+", place):
        return f'id="{place[1:]}"' in INDEX
    if re.fullmatch(r"\.[\w-]+", place):
        return re.search(r'class="[^"]*\b' + re.escape(place[1:]) + r'\b', INDEX) is not None
    return True


def test_wpisy_maja_wymagane_pola():
    for entry in POPUPS:
        for field in ("id", "title", "description", "place"):
            assert entry.get(field), f"wpis {entry.get('id')}: brak pola {field}"


def test_id_sa_unikalne():
    ids = [entry["id"] for entry in POPUPS]
    assert len(ids) == len(set(ids))


def test_expiry_to_najmniej_14_dni_albo_never():
    for entry in POPUPS:
        expiry = entry.get("expiry", 30)
        assert expiry == "never" or (
            isinstance(expiry, (int, float)) and not isinstance(expiry, bool) and expiry >= 14
        ), f"wpis {entry['id']}: zły expiry {expiry!r}"


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
