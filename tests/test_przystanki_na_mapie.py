"""Przystanki narysowanych linii i ich nazwy na mapie (zgłoszenia #252, #253).

Mapa pokazuje mijane przystanki wskazanej linii, nazwę najbliższego
przystanku w dymku linii, a od dużego przybliżenia nazwy przystanków. Front
nie zgaduje nazw z położenia - serwer dokłada je do tego, co już wysyła:
`stops_n` kawałka (nazwa miejsca, po kolei, równolegle do `stops_t`),
`stops_pf` (nazwa słupka tam, gdzie jest inna) i `place` kropki przesiadki.
Jak front z tego korzysta, sprawdza tests/test_flow_map_front.py.
"""

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template

import gtfs
import planner
from tests.gtfs_builder import make_day

ROOT = Path(__file__).resolve().parent.parent
WHEN = datetime(2026, 1, 5, 0, 0, 0)

CZWORKA = {"trip_id": "CZWORKA", "label": "Tramwaj 4", "headsign": "OPORÓW",
           "stops": [("S", 0, 0), ("A", 120, 180), ("B", 300, 300), ("E", 500, 500)]}
NAZWY = {"S": "Start", "A": "Alfa", "B": "Beta", "E": "Koniec"}


def _kawalki(wynik):
    return [seg for seg in wynik["segments"] if seg.get("stops_t")]


def test_kawalek_niesie_nazwy_swoich_przystankow(install_day):
    install_day(make_day([CZWORKA], names=NAZWY))

    [kawalek] = _kawalki(planner.plan_flow("Start", "Koniec", WHEN))

    assert kawalek["stops_n"] == ["Start", "Alfa", "Beta", "Koniec"]
    assert len(kawalek["stops_n"]) == len(kawalek["stops_t"])
    # Żaden słupek nie nazywa się inaczej niż jego miejsce - pola nie ma.
    assert "stops_pf" not in kawalek


def test_peron_kierunkowy_podpisuje_sie_nazwa_miejsca(install_day):
    """Jedna nazwa na miejsce, nie na peron: 'Beta W/t' to Beta, a nazwa
    peronu zostaje do dymka."""
    install_day(make_day([CZWORKA], names={**NAZWY, "B": "Beta W/t"}))

    [kawalek] = _kawalki(planner.plan_flow("Start", "Koniec", WHEN))

    assert kawalek["stops_n"] == ["Start", "Alfa", "Beta", "Koniec"]
    assert kawalek["stops_pf"] == [None, None, "Beta W/t", None]


def test_kropka_przesiadki_mowi_jakie_to_miejsce(install_day):
    """Po `place` front nie stawia kropki mijanego przystanku tam, gdzie stoi
    już kropka przesiadki - ta sama nazwa co w stops_n."""
    install_day(make_day([CZWORKA], names={**NAZWY, "S": "Start W/t"}))

    wynik = planner.plan_flow("Start W/t", "Koniec", WHEN)

    assert wynik["nodes"]
    for node in wynik["nodes"]:
        assert node["place"] == gtfs.place_label(node["name"])
    assert any(node["place"] == "Start" for node in wynik["nodes"])


def test_nazwa_miejsca_ucina_tylko_peron():
    assert gtfs.place_label("PL. GRUNWALDZKI W/t") == "PL. GRUNWALDZKI"
    assert gtfs.place_label("Kozanowska") == "Kozanowska"
    assert gtfs.place_label("Bzowa (Centrum Historii Zajezdnia)") == \
        "Bzowa (Centrum Historii Zajezdnia)"


def test_slupki_miasta_niosa_nazwe_miejsca(monkeypatch):
    """Bez narysowanej trasy mapa podpisuje wszystkie przystanki w kadrze -
    też raz na miejsce, więc /api/stops mówi, do jakiego miejsca należy
    każdy słupek."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE stops (stop_id TEXT, stop_name TEXT, stop_lat REAL, stop_lon REAL)")
    db.executemany("INSERT INTO stops VALUES (?, ?, ?, ?)", [
        ("1", "PL. GRUNWALDZKI W/t", 51.11, 17.06),
        ("2", "PL. GRUNWALDZKI", 51.111, 17.061),
        ("3", "Kozanowska", 51.13, 16.97),
    ])
    monkeypatch.setattr(gtfs, "_connect", lambda: db)

    slupki = {s["name"]: s["place"] for s in gtfs.all_stops_geo()}

    assert slupki == {"PL. GRUNWALDZKI W/t": "PL. GRUNWALDZKI",
                      "PL. GRUNWALDZKI": "PL. GRUNWALDZKI",
                      "Kozanowska": "Kozanowska"}


def test_prog_nazw_jest_suwakiem_dev_z_ta_sama_wartoscia_co_front():
    """Próg przybliżenia stoi pod ⚙ w sekcji DEV (nie w .env), a suwak
    zaczyna od tej samej liczby, co domyślna wartość we froncie."""
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    with app.test_request_context("/"):
        html = render_template("index.html", stops=[], abbreviations={},
                               lines=[], data_error=None, form_time="12:00",
                               form_date="2026-10-01", dev_mode=True)
    start = html.index('id="fold-places"')
    sekcja = html[html.rindex("<details", 0, start):html.index("</details>", start)]
    assert "dev-only" in sekcja
    suwak = re.search(r'<input[^>]*id="names-zoom"[^>]*>', sekcja).group(0)
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    domyslny = re.search(r"namesZoom: (\d+)", app_js).group(1)
    assert f'value="{domyslny}"' in suwak
    assert 14 <= int(domyslny) <= 19
