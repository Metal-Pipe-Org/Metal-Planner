"""Wynik wyszukiwania przychodzi etapami (zgłoszenie #229).

Pasek z najszybszą trasą jest znany po ułamku sekundy, mapa z listą po nim, a auta
i rowery na końcu - i tak też idą do strony, po jednej linijce JSON na etap
(patrz planner.plan_flow_stages i routes.api_flow). Sklejone etapy muszą
dawać dokładnie tę odpowiedź, którą daje plan_flow, a strona, która przestała
czytać, nie może kazać serwerowi liczyć dalej.
"""

import json
from datetime import datetime

from flask import Flask

import planner
import routes
import warmup
from tests.gtfs_builder import make_day

WHEN = datetime(2026, 1, 5, 0, 0, 0)


def _dzien():
    return make_day([
        {"trip_id": "PIATKA", "label": "Tramwaj 5",
         "stops": [("S", 0, 0), ("B", 600, 600)]},
        {"trip_id": "SIEDEMNASTKA", "label": "Tramwaj 17",
         "stops": [("B", 900, 900), ("X", 1100, 1100), ("E", 1500, 1500)]},
        {"trip_id": "DWOJKA", "label": "Autobus 2",
         "stops": [("S", 300, 300), ("E", 1800, 1800)]},
    ])


def _z_autem(arrival_sec):
    """Propozycja kończąca się autem - tyle, ile potrzeba do ustawienia jej
    na liście (patrz planner._journey_key)."""
    return {"departure_sec": 0, "arrival_sec": arrival_sec, "transfers": 1,
            "legs": [], "traficar": True}


def _klient(monkeypatch):
    czekania = []
    monkeypatch.setattr(warmup, "wait_for_refreshes", czekania.append)
    app = Flask(__name__)
    routes.init_routes(app)
    return app.test_client()


def _linijki(response):
    return [json.loads(line) for line in response.get_data(as_text=True).splitlines()]


def test_etapy_ida_po_kolei(install_day):
    install_day(_dzien())
    assert [name for name, _ in planner.plan_flow_stages("S", "E", WHEN)] == \
        ["fastest", "map", "extras"]


def test_pierwszy_etap_to_sam_pasek(install_day):
    """Tylko to, co pokazuje pasek „Najszybciej…" - mapa i lista pokazują się
    dopiero z mapą przepływów, więc w tym etapie nie ma ani jednej, ani
    drugiej."""
    install_day(_dzien())
    name, first = next(planner.plan_flow_stages("S", "E", WHEN))

    assert name == "fastest"
    assert set(first) == {"best_arrival", "best_sec", "departure", "fastest", "starts", "ride_sec"}
    assert first["fastest"]["arrival"] == first["best_arrival"]


def test_sklejone_etapy_to_cala_odpowiedz(install_day):
    """plan_flow to te same etapy, sklejone - ostatni etap nie gubi ani nie
    dokłada niczego względem odpowiedzi w jednym kawałku."""
    install_day(_dzien())
    merged = {}
    for _, delta in planner.plan_flow_stages("S", "E", WHEN, use_bikes=True):
        merged.update(delta)
    assert merged == planner.plan_flow("S", "E", WHEN, use_bikes=True)
    assert {"segments", "nodes", "journeys", "cars", "bike_places",
            "fastest", "deadline", "bikes"} <= set(merged)


def test_mapa_przychodzi_bez_aut_i_rowerow(install_day, monkeypatch):
    """Auta i rowery zależą od tego, co mapa rysuje, więc liczą się po niej
    i dochodzą w następnym etapie. Etap mapy nie ma tych pól wcale - strona
    zostawia do tego czasu te, które już pokazuje (a pusta lista kazałaby
    jej je zdjąć)."""
    install_day(_dzien())
    monkeypatch.setattr(planner, "_traficar_journeys", lambda *a: [_z_autem(1000)])
    stages = dict(planner.plan_flow_stages("S", "E", WHEN))

    live = {"cars", "cars_dest_in_zone", "bike_places", "bike_places_live"}
    assert not live & set(stages["map"])
    assert live <= set(stages["extras"])
    assert not any(j.get("traficar") for j in stages["map"]["journeys"])
    assert any(j.get("traficar") for j in stages["extras"]["journeys"])


def test_swieze_dane_przychodza_ostatnim_etapem(install_day, monkeypatch):
    """Dane na żywo były stare: po wyniku liczy się je jeszcze raz i dosyła
    jako "fresh" - ale tylko wtedy, gdy coś zmieniły."""
    install_day(_dzien())
    przyjazdy = iter([1000, 1200])
    monkeypatch.setattr(planner, "_traficar_journeys",
                        lambda *a: [_z_autem(next(przyjazdy))])
    stages = list(planner.plan_flow_stages("S", "E", WHEN, live_refreshed=lambda: True))
    assert [name for name, _ in stages] == ["fastest", "map", "extras", "fresh"]
    fresh = stages[-1][1]
    assert [j["arrival_sec"] for j in fresh["journeys"] if j.get("traficar")] == [1200]

    monkeypatch.setattr(planner, "_traficar_journeys", lambda *a: [_z_autem(1000)])
    names = [name for name, _ in planner.plan_flow_stages(
        "S", "E", WHEN, live_refreshed=lambda: True)]
    assert names == ["fastest", "map", "extras"]


def test_przerwane_czytanie_nie_liczy_mapy(install_day, monkeypatch):
    """Strona pyta już o co innego i przestaje czytać: serwer kończy na
    granicy etapu, zamiast liczyć mapę, której nikt nie zobaczy."""
    install_day(_dzien())
    mapy = []
    value_map = planner._value_map
    monkeypatch.setattr(planner, "_value_map",
                        lambda *a, **k: mapy.append(1) or value_map(*a, **k))
    stages = planner.plan_flow_stages("S", "E", WHEN)
    next(stages)
    stages.close()
    assert mapy == []


def test_endpoint_wysyla_etapy_linijkami(install_day, monkeypatch):
    install_day(_dzien())
    response = _klient(monkeypatch).get("/api/flow?start=S&end=E&date=2026-01-05&time=00:00")

    assert response.mimetype == "application/x-ndjson"
    # Bez tego nginx przed serwerem oddałby wszystkie etapy naraz, na końcu.
    assert response.headers["X-Accel-Buffering"] == "no"
    lines = _linijki(response)
    assert [line["stage"] for line in lines] == ["fastest", "map", "extras"]

    merged = {}
    for line in lines:
        merged.update({k: v for k, v in line.items() if k != "stage"})
    assert merged == json.loads(json.dumps(planner.plan_flow("S", "E", WHEN)))


def test_przelacznik_nowej_mapy_dochodzi_do_plannera(monkeypatch):
    uzyte = []
    monkeypatch.setattr(routes, "plan_flow_stages",
                        lambda *args, **kwargs: uzyte.append(kwargs["new_map"]) or [])
    klient = _klient(monkeypatch)
    for query in ("", "&newmap=0", "&newmap=1"):
        klient.get("/api/flow?start=S&end=E" + query).get_data()
    assert uzyte == [False, False, True]


def test_blad_to_jedna_linijka(install_day, monkeypatch):
    install_day(_dzien())
    lines = _linijki(_klient(monkeypatch).get("/api/flow?start=Nikt&end=E"))
    assert len(lines) == 1
    assert lines[0]["stage"] == "error" and "Nikt" in lines[0]["error"]
