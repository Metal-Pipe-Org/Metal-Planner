"""Przystanki narysowanych linii i ich nazwy na mapie (zgłoszenia #252, #253).

Mapa pokazuje mijane przystanki wskazanej linii, nazwę najbliższego
przystanku w dymku linii, a od dużego przybliżenia nazwy przystanków. Front
nie zgaduje nazw z położenia - serwer dokłada je do tego, co już wysyła:
`stops_n` kawałka (nazwa miejsca, po kolei, równolegle do `stops_t`),
`stops_pf` (nazwa słupka tam, gdzie jest inna) i `place` kropki przesiadki.
Jak front z tego korzysta, sprawdza tests/test_flow_map_front.py.
"""

import re
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


def _z_miejscami(day, obok):
    """Miejsca złożone tak, jak w gtfs.load_day (perony kierunkowe, scalenia
    z naming.PLACE_MERGES) - make_day grupuje tylko po identycznej nazwie.
    `obok`: {stop_id: (nazwa, stop_id, przy którym stoi)} - dodatkowy słupek
    kilkadziesiąt metrów od istniejącego, bez kursów."""
    for stop_id, (name, przy) in obok.items():
        lat, lon = day.stop_coords[przy]
        day.stop_names[stop_id] = name
        day.stop_coords[stop_id] = (lat + 0.0003, lon)
        gtfs._register_stop_name(day, stop_id, name)
    day.stops_by_place = gtfs._build_places(day.stop_names, day.stop_coords,
                                            day.stops_by_key)
    day.place_of = {sid: key for key, ids in day.stops_by_place.items() for sid in ids}
    return day


def test_peron_kierunkowy_podpisuje_sie_nazwa_miejsca(install_day):
    """Jedna nazwa na miejsce, nie na peron: 'Beta W/t' przy słupku 'Beta' to
    Beta, a nazwa peronu zostaje do dymka."""
    install_day(_z_miejscami(make_day([CZWORKA], names={**NAZWY, "B": "Beta W/t"}),
                             {"B0": ("Beta", "B")}))

    [kawalek] = _kawalki(planner.plan_flow("Start", "Koniec", WHEN))

    assert kawalek["stops_n"] == ["Start", "Alfa", "Beta", "Koniec"]
    assert kawalek["stops_pf"] == [None, None, "Beta W/t", None]


def test_stacja_scalona_z_przystankiem_ma_jego_nazwe(install_day):
    """Wrocław Główny i DWORZEC GŁÓWNY to jedno miejsce (naming.PLACE_MERGES),
    więc i jedna nazwa - nie dwa podpisy na największym węźle miasta."""
    install_day(_z_miejscami(
        make_day([CZWORKA], names={**NAZWY, "B": "Wrocław Główny"}),
        {"DG": ("DWORZEC GŁÓWNY", "B")}))

    [kawalek] = _kawalki(planner.plan_flow("Start", "Koniec", WHEN))

    assert kawalek["stops_n"] == ["Start", "Alfa", "DWORZEC GŁÓWNY", "Koniec"]
    assert kawalek["stops_pf"] == [None, None, "Wrocław Główny", None]


def test_kropka_przesiadki_mowi_jakie_to_miejsce(install_day):
    """Po `place` front nie stawia kropki mijanego przystanku tam, gdzie stoi
    już kropka przesiadki - ta sama nazwa co w stops_n."""
    install_day(_z_miejscami(make_day([CZWORKA], names={**NAZWY, "S": "Start W/t"}),
                             {"S0": ("Start", "S")}))

    wynik = planner.plan_flow("Start W/t", "Koniec", WHEN)

    [kawalek] = _kawalki(wynik)
    assert wynik["nodes"]
    for node in wynik["nodes"]:
        assert node["place"] in kawalek["stops_n"]
    assert any(node["place"] == "Start" for node in wynik["nodes"])


def test_slupki_miasta_niosa_nazwe_miejsca():
    """Bez narysowanej trasy mapa podpisuje wszystkie przystanki w kadrze -
    też raz na miejsce, więc /api/stops mówi, do jakiego miejsca należy
    każdy słupek, z tym samym grupowaniem co przy trasie: peron kierunkowy
    i stacja scalona z przystankiem MPK dostają nazwę miejsca."""
    slupki = gtfs.with_place_names([
        {"name": "PL. GRUNWALDZKI W/t", "lat": 51.11, "lon": 17.06},
        {"name": "PL. GRUNWALDZKI", "lat": 51.111, "lon": 17.061},
        {"name": "DWORZEC GŁÓWNY", "lat": 51.09919, "lon": 17.03433},
        {"name": "Kozanowska", "lat": 51.13, "lon": 16.97},
        {"name": "Wrocław Główny", "lat": 51.09805, "lon": 17.03627, "kind": "train"},
    ])

    assert {s["name"]: s["place"] for s in slupki} == {
        "PL. GRUNWALDZKI W/t": "PL. GRUNWALDZKI",
        "PL. GRUNWALDZKI": "PL. GRUNWALDZKI",
        "DWORZEC GŁÓWNY": "DWORZEC GŁÓWNY",
        "Kozanowska": "Kozanowska",
        "Wrocław Główny": "DWORZEC GŁÓWNY",
    }
    assert slupki[-1]["kind"] == "train"


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
