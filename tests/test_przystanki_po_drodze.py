"""Przystanki po drodze w etapie przejazdu (zgłoszenie #169).

Etap niesie `via` - mijane przystanki z godziną i współrzędnymi, bez
wsiadania i wysiadania, które ma już w `from`/`to`. Front wypisuje je
w rozwiniętej propozycji (domyślnie zwinięte, opcja „Zawsze pokazuj
przystanki po drodze" w ⚙) i stawia na mapie wybranej trasy - to sprawdza
tests/test_flow_map_front.py.
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

# Na Alfie tramwaj stoi minutę: przyjeżdża 00:02, odjeżdża 00:03.
CZWORKA = {"trip_id": "CZWORKA", "label": "Tramwaj 4", "headsign": "OPORÓW",
           "stops": [("S", 0, 0), ("A", 120, 180), ("B", 300, 300), ("E", 500, 500)]}
NAZWY = {"S": "Start", "A": "Alfa", "B": "Beta", "E": "Koniec"}


def _przejazdy(wynik):
    return [leg for journey in wynik["journeys"] for leg in journey["legs"]
            if leg["kind"] == "ride"]


def test_etap_wypisuje_mijane_przystanki(install_day):
    day = make_day([CZWORKA], names=NAZWY)
    install_day(day)

    [etap] = _przejazdy(planner.plan_flow("Start", "Koniec", WHEN))

    assert [s["name"] for s in etap["via"]] == ["Alfa", "Beta"]
    # Odjazd z przystanku, nie przyjazd - jak w kursie z rozkładu.
    assert [s["t"] for s in etap["via"]] == ["00:03", "00:05"]
    assert [(s["lat"], s["lon"]) for s in etap["via"]] == [
        tuple(round(c, 5) for c in day.stop_coords[stop]) for stop in ("A", "B")]
    assert etap["stops_count"] == len(etap["via"]) + 1


def test_przejazd_o_jeden_przystanek_nie_ma_czego_wypisac(install_day):
    install_day(make_day([{"trip_id": "JEDYNKA", "label": "Autobus 1",
                           "stops": [("S", 0, 0), ("E", 300, 300)]}]))

    [etap] = _przejazdy(planner.plan_flow("S", "E", WHEN))

    assert etap["via"] == []


def test_etap_z_przebiegu_kursu_tez_je_ma(monkeypatch):
    """Drugie miejsce, w którym powstaje etap przejazdu - z przebiegu kursu
    w rozkładzie (trasa awaryjna, sklejanie przesiadek w _seated_legs)."""
    day = make_day([CZWORKA], names=NAZWY)
    monkeypatch.setattr(gtfs, "trip_path", lambda *args, **kwargs: CZWORKA["stops"])

    etap = planner._ride_leg(day, "CZWORKA", "S", 0, "E", 500)

    assert [(s["name"], s["t"]) for s in etap["via"]] == [("Alfa", "00:03"),
                                                          ("Beta", "00:05")]


def test_przystanek_bez_wspolrzednych_zostaje_na_liscie():
    """Stacja PKP bywa bez współrzędnych: na mapie nie ma gdzie stanąć, ale
    na liście przystanków po drodze ma swoje miejsce."""
    day = make_day([CZWORKA], names=NAZWY)
    del day.stop_coords["A"]

    assert planner._via_stops(day, [("A", 180)]) == [{"name": "Alfa", "t": "00:03"}]


def test_opcja_w_wygladzie_aplikacji_domyslnie_zgaszona():
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    with app.test_request_context("/"):
        html = render_template("index.html", stops=[], abbreviations={},
                               lines=[], data_error=None, form_time="12:00",
                               form_date="2026-10-01", dev_mode=False)
    start = html.index('id="fold-layout"')
    layout = html[start:html.index("</details>", start)]
    tag = re.search(r'<input[^>]*id="via-open"[^>]*>', layout).group(0)
    assert "checked" not in tag
