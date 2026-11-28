"""Endpoint MCP (mcp_server.py, mcp_tools.py): protokół JSON-RPC w wersji
bezstanowej i narzędzia, które oddają to samo co /api/*, tylko bez geometrii.

Rozkład jest syntetyczny (tests/gtfs_builder.py), więc testy nie potrzebują
data/gtfs.sqlite ani sieci.
"""

import json
from datetime import date

import pytest

import bikes
import gtfs
import mcp_tools
import onboard
import pkp_live
import timetables
import traficar
import vehicles
from tests.gtfs_builder import make_day

DZIEN = {"date": "2026-01-05", "time": "00:00"}


@pytest.fixture
def klient(install_day):
    install_day(make_day([
        {"trip_id": "PIATKA", "label": "Tramwaj 5", "headsign": "BISKUPIN",
         "stops": [("S", 0, 0), ("B", 600, 600)]},
        {"trip_id": "SIEDEMNASTKA", "label": "Tramwaj 17",
         "stops": [("B", 900, 900), ("X", 1100, 1100), ("E", 1500, 1500)]},
        {"trip_id": "DWOJKA", "label": "Autobus 2",
         "stops": [("S", 300, 300), ("E", 1800, 1800)]},
    ], names={"S": "Start", "B": "Most", "X": "Środek", "E": "Cel"}))
    import app as aplikacja
    return aplikacja.app.test_client()


def _rpc(klient, method, params=None, **opcje):
    odpowiedz = klient.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method,
                                          "params": params or {}}, **opcje)
    return odpowiedz.status_code, odpowiedz.get_json()


def _narzedzie(klient, name, arguments, **opcje):
    status, odpowiedz = _rpc(klient, "tools/call", {"name": name, "arguments": arguments}, **opcje)
    assert status == 200
    wynik = odpowiedz["result"]
    return wynik["isError"], json.loads(wynik["content"][0]["text"])


def _klucze(value):
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _klucze(v)}
    if isinstance(value, list):
        return {k for v in value for k in _klucze(v)}
    return set()


# --- protokół ----------------------------------------------------------------------------


def test_initialize_oddaje_wersje_o_ktora_prosi_klient(klient):
    _, odpowiedz = _rpc(klient, "initialize", {"protocolVersion": "2025-03-26"})
    wynik = odpowiedz["result"]
    assert wynik["protocolVersion"] == "2025-03-26"
    assert wynik["capabilities"] == {"tools": {"listChanged": False}}
    assert wynik["instructions"] == mcp_tools.INSTRUCTIONS


def test_initialize_z_nieznana_wersja_dostaje_najnowsza(klient):
    _, odpowiedz = _rpc(klient, "initialize", {"protocolVersion": "1999-01-01"})
    assert odpowiedz["result"]["protocolVersion"] == "2025-06-18"


def test_wszystkie_narzedzia_tylko_czytaja(klient):
    _, odpowiedz = _rpc(klient, "tools/list")
    narzedzia = odpowiedz["result"]["tools"]
    assert {t["name"] for t in narzedzia} == {
        "plan_trip", "find_stop", "departures", "stop_schedule", "trip", "line", "train",
        "vehicles", "bikes_near", "cars_near"}
    for narzedzie in narzedzia:
        assert narzedzie["annotations"]["readOnlyHint"] is True
        assert narzedzie["annotations"]["destructiveHint"] is False
        assert narzedzie["inputSchema"]["additionalProperties"] is False


def test_powiadomienie_nie_dostaje_odpowiedzi(klient):
    odpowiedz = klient.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert odpowiedz.status_code == 202
    assert odpowiedz.data == b""


def test_paczka_wiadomosci_dostaje_paczke_odpowiedzi(klient):
    odpowiedz = klient.post("/mcp", json=[
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ])
    assert [a["id"] for a in odpowiedz.get_json()] == [1, 2]


def test_bez_strumienia_get_i_delete_to_405(klient):
    assert klient.get("/mcp").status_code == 405
    assert klient.delete("/mcp").status_code == 405


def test_zepsute_wiadomosci(klient):
    assert klient.post("/mcp", data="{nie json").status_code == 400
    assert klient.post("/mcp", json=[]).status_code == 400
    _, odpowiedz = _rpc(klient, "nie/ma/takiej")
    assert odpowiedz["error"]["code"] == -32601
    _, odpowiedz = _rpc(klient, "tools/call", {"name": "nie_ma", "arguments": {}})
    assert odpowiedz["error"]["code"] == -32602
    _, odpowiedz = _rpc(klient, "tools/call", {"name": "find_stop", "arguments": []})
    assert odpowiedz["error"]["code"] == -32602


def test_obca_strona_w_przegladarce_nie_dostaje_odpowiedzi(klient):
    status, _ = _rpc(klient, "ping", headers={"Origin": "https://obca.example"})
    assert status == 403
    status, _ = _rpc(klient, "ping", headers={"Origin": "http://localhost"})
    assert status == 200


def test_awaria_narzedzia_nie_klasie_serwera(klient, monkeypatch):
    def _awaria(arguments):
        raise RuntimeError("bum")
    monkeypatch.setitem(mcp_tools._TOOLS["find_stop"], "handler", _awaria)
    _, odpowiedz = _rpc(klient, "tools/call", {"name": "find_stop", "arguments": {"query": "x"}})
    assert odpowiedz["error"]["code"] == -32603
    assert "bum" not in json.dumps(odpowiedz)


# --- narzędzia ---------------------------------------------------------------------------


def test_plan_trip_oddaje_trasy_bez_geometrii_i_link_do_mapy(klient):
    blad, wynik = _narzedzie(klient, "plan_trip", {"from": "Start", "to": "Cel", **DZIEN})
    assert not blad
    assert (wynik["start"], wynik["end"]) == ("Start", "Cel")
    assert wynik["journeys"]
    linie = {etap.get("line") for trasa in wynik["journeys"] for etap in trasa["legs"]}
    assert "Tramwaj 5" in linie
    assert not _klucze(wynik) & {"path", "lat", "lon", "dep_sec", "arr_sec", "departure_sec"}
    assert wynik["link"] == "http://localhost/?from=Start&to=Cel&date=2026-01-05&time=00%3A00"


def test_link_za_proxy_https_zostaje_na_https(klient):
    _, wynik = _narzedzie(klient, "plan_trip", {"from": "Start", "to": "Cel", **DZIEN},
                          headers={"X-Forwarded-Proto": "https"})
    assert wynik["link"].startswith("https://localhost/?")


def test_plan_trip_odmawia_zlej_daty_zamiast_brac_dzisiaj(klient):
    blad, wynik = _narzedzie(klient, "plan_trip", {"from": "Start", "to": "Cel", "date": "5.01.2026"})
    assert blad
    assert "YYYY-MM-DD" in wynik["error"]
    blad, wynik = _narzedzie(klient, "plan_trip", {"from": "Start", "to": "Cel", "time": "25:00"})
    assert blad
    assert "HH:MM" in wynik["error"]


def test_plan_trip_z_nieznanym_przystankiem(klient):
    blad, wynik = _narzedzie(klient, "plan_trip", {"from": "Nigdzie", "to": "Cel", **DZIEN})
    assert blad
    assert "Nigdzie" in wynik["error"]


def test_plan_trip_bez_wymaganego_argumentu(klient):
    blad, wynik = _narzedzie(klient, "plan_trip", {"to": "Cel"})
    assert blad
    assert "from" in wynik["error"]


def test_find_stop(klient):
    blad, wynik = _narzedzie(klient, "find_stop", {"query": "  most ", **DZIEN})
    assert not blad
    assert wynik["name"] == "Most"
    blad, wynik = _narzedzie(klient, "find_stop", {"query": "Nigdzie", **DZIEN})
    assert blad


def test_departures_bez_sekund_i_slupkow(klient):
    blad, wynik = _narzedzie(klient, "departures", {"stop": "Start", **DZIEN, "limit": 1})
    assert not blad
    assert len(wynik["departures"]) == 1
    odjazd = wynik["departures"][0]
    assert odjazd["line"] == "Tramwaj 5"
    assert not set(odjazd) & {"sec", "stop_id"}


def test_departures_limit_ma_sufit(klient):
    blad, wynik = _narzedzie(klient, "departures", {"stop": "Start", **DZIEN, "limit": 10_000})
    assert not blad
    blad, _ = _narzedzie(klient, "departures", {"stop": "Start", **DZIEN, "limit": 0})
    assert blad


def test_trip_nieznanego_kursu(klient):
    blad, wynik = _narzedzie(klient, "trip", {"trip": "NIE_MA", **DZIEN})
    assert blad
    assert "kursu" in wynik["error"]


# --- rozkład z przystanku ----------------------------------------------------------------


@pytest.fixture
def tablica(klient, monkeypatch):
    """Tablica z przystanku na syntetycznym dniu: stop_board woła load_day
    z drugim argumentem, a kalendarz powtórzeń czyta SQLite - obu tu nie ma."""
    day = make_day([
        {"trip_id": f"T{i}", "label": "Tramwaj 3", "headsign": "KSIĘŻE MAŁE",
         "stops": [("S", sec, sec), ("E", sec + 300, sec + 300)]}
        for i, sec in enumerate((7 * 3600 + 5 * 60, 7 * 3600 + 25 * 60, 8 * 3600))
    ] + [
        {"trip_id": "N1", "label": "Autobus N", "headsign": "JAGODNO",
         "stops": [("S", 9 * 3600, 9 * 3600), ("E", 9 * 3600 + 60, 9 * 3600 + 60)]},
        {"trip_id": "PKP:1", "label": "Pociąg IC 6123", "headsign": "Kraków Główny",
         "stops": [("S", 10 * 3600, 10 * 3600), ("E", 11 * 3600, 11 * 3600)]},
    ], names={"S": "Rynek", "E": "Cel"})
    monkeypatch.setattr(gtfs, "load_day", lambda *a, **k: day)
    monkeypatch.setattr(timetables, "repeat_note", lambda stop_ids, d: "w dni robocze")
    return klient


def test_stop_schedule_bez_linii_wypisuje_linie(tablica):
    blad, wynik = _narzedzie(tablica, "stop_schedule", {"stop": "rynek", **DZIEN})
    assert not blad
    assert wynik["repeats"] == "w dni robocze"
    assert {(l["num"], l["headsign"], l["count"]) for l in wynik["lines"]} >= {
        ("3", "KSIĘŻE MAŁE", 3), ("N", "JAGODNO", 1)}
    assert "schedules" not in wynik


def test_stop_schedule_z_linia_to_godziny_jak_na_slupku(tablica):
    blad, wynik = _narzedzie(tablica, "stop_schedule",
                             {"stop": "Rynek", "line": "3", "headsign": "księże", **DZIEN})
    assert not blad
    [rozklad] = wynik["schedules"]
    assert rozklad["hours"] == {"07": "05 25", "08": "00"}


def test_stop_schedule_pociag_po_samym_numerze(tablica):
    blad, wynik = _narzedzie(tablica, "stop_schedule", {"stop": "Rynek", "line": "6123", **DZIEN})
    assert not blad
    assert wynik["schedules"][0]["hours"] == {"10": "00"}


def test_stop_schedule_nieznana_linia_mowi_co_odjezdza(tablica):
    blad, wynik = _narzedzie(tablica, "stop_schedule", {"stop": "Rynek", "line": "33", **DZIEN})
    assert blad
    assert "3" in wynik["error"] and "N" in wynik["error"]


# --- pociągi i opóźnienia ----------------------------------------------------------------


def test_train_po_numerze_z_opoznieniem_dzis(tablica, monkeypatch):
    monkeypatch.setattr(timetables, "trip_detail", lambda trip, d, *a: {
        "trip": trip, "line": "Pociąg IC 6123",
        "stops": [{"id": "S", "name": "Rynek", "lat": 1, "lon": 2, "t": "10:00", "sec": 36000},
                  {"id": "E", "name": "Cel", "lat": 1, "lon": 2, "t": "11:00", "sec": 39600}]})
    monkeypatch.setattr(pkp_live, "delays", lambda day, trips, when: {
        "PKP:1": {"Cel": {"arr": 7, "dep": None}}})
    blad, wynik = _narzedzie(tablica, "train", {"num": "IC 6123", "date": date.today().isoformat()})
    assert not blad
    [kurs] = wynik["runs"]
    assert [s["name"] for s in kurs["stops"]] == ["Rynek", "Cel"]
    assert "delay_min" not in kurs["stops"][0]
    assert kurs["stops"][1]["delay_min"] == {"arr": 7, "dep": None}
    assert not _klucze(kurs) & {"lat", "lon", "sec", "id"}


def test_train_opoznien_nie_ma_dla_innego_dnia(tablica, monkeypatch):
    monkeypatch.setattr(timetables, "trip_detail", lambda trip, d, *a: {
        "trip": trip, "stops": [{"name": "Cel", "t": "11:00"}]})
    monkeypatch.setattr(pkp_live, "delays", lambda *a: pytest.fail("pytanie o inny dzień"))
    blad, wynik = _narzedzie(tablica, "train", {"num": "6123", **DZIEN})
    assert not blad
    assert "delay_min" not in wynik["runs"][0]["stops"][0]


def test_train_nieznany_numer(tablica):
    blad, wynik = _narzedzie(tablica, "train", {"num": "9999", **DZIEN})
    assert blad
    assert "9999" in wynik["error"]


# --- start z pokładu pojazdu -------------------------------------------------------------


def _kierunki(monkeypatch):
    monkeypatch.setattr(onboard, "directions", lambda num, d, mode=None: {
        "num": "5", "mode": "tram", "label": "Tramwaj 5", "directions": [
            {"headsign": "BISKUPIN", "stops": [{"id": "S", "name": "Start"}, {"id": "B", "name": "Most"}]},
            {"headsign": "OPORÓW", "stops": [{"id": "B", "name": "Most"}, {"id": "S", "name": "Start"}]},
        ]})


def test_plan_trip_z_pokladu_szuka_slupka_po_nazwie(klient, monkeypatch):
    _kierunki(monkeypatch)
    pojazd = {}
    oryginal = mcp_tools.plan_flow

    def plan_flow(*args, **kwargs):
        pojazd.update(kwargs["in_vehicle"] or {})
        return oryginal(*args, **kwargs)
    monkeypatch.setattr(mcp_tools, "plan_flow", plan_flow)

    blad, wynik = _narzedzie(klient, "plan_trip", {
        "to": "Cel", "onboard_line": "5", "onboard_next_stop": "start",
        "onboard_headsign": "bisk", **DZIEN})
    assert not blad, wynik
    assert pojazd == {"num": "5", "mode": "tram", "headsign": "BISKUPIN", "stop": "S",
                      "slack_sec": None}
    assert "link" not in wynik


def test_plan_trip_z_pokladu_bez_kierunku_pyta_o_niego(klient, monkeypatch):
    _kierunki(monkeypatch)
    blad, wynik = _narzedzie(klient, "plan_trip", {
        "to": "Cel", "onboard_line": "5", "onboard_next_stop": "Most", **DZIEN})
    assert blad
    assert "BISKUPIN" in wynik["error"] and "OPORÓW" in wynik["error"]


def test_plan_trip_przekazuje_ustawienia_spod_zebatki(klient, monkeypatch):
    dostal = {}
    monkeypatch.setattr(mcp_tools, "plan_flow", lambda *a, **k: dostal.update(k) or {"journeys": []})
    blad, _ = _narzedzie(klient, "plan_trip", {
        "from": "Start", "to": "Cel", "walk_pace": "wolno", "walking": False,
        "vans": True, "bike_kmh": 20, **DZIEN})
    assert not blad
    assert (dostal["walk_pace"], dostal["walking"], dostal["car_vans"],
            dostal["bike_kmh"]) == ("wolno", False, True, 20.0)
    blad, wynik = _narzedzie(klient, "plan_trip", {"from": "Start", "to": "Cel", "walk_pace": "biegiem"})
    assert blad


# --- na żywo -----------------------------------------------------------------------------

PRZYSTANKI = [{"name": "Rynek", "lat": 51.11, "lon": 17.03},
              {"name": "Dworzec", "lat": 51.10, "lon": 17.04}]


def test_vehicles_z_najblizszym_przystankiem(klient, monkeypatch):
    monkeypatch.setattr(vehicles, "get_vehicles", lambda: [
        {"line": "3", "kind": "tram", "lat": 51.1101, "lon": 17.0301},
        {"line": "33", "kind": "bus", "lat": 51.10, "lon": 17.04},
    ])
    monkeypatch.setattr(gtfs, "all_stops_geo", lambda: PRZYSTANKI)
    blad, wynik = _narzedzie(klient, "vehicles", {"line": "3"})
    assert not blad
    [pojazd] = wynik["vehicles"]
    assert pojazd["near_stop"] == "Rynek"
    assert pojazd["near_m"] < 50


def test_vehicles_awaria_feedu_to_odmowa(klient, monkeypatch):
    def _awaria():
        raise OSError("timeout")
    monkeypatch.setattr(vehicles, "get_vehicles", _awaria)
    blad, wynik = _narzedzie(klient, "vehicles", {"line": "3"})
    assert blad
    assert "timeout" in wynik["error"]


def test_bikes_near_tylko_w_promieniu_od_najblizszych(klient, monkeypatch):
    monkeypatch.setattr(bikes, "stations", lambda: [
        {"name": "Daleko", "lat": 51.20, "lon": 17.03, "bikes": 1},
        {"name": "Dalej", "lat": 51.113, "lon": 17.03, "bikes": 2},
        {"name": "Blisko", "lat": 51.1101, "lon": 17.03, "bikes": 3, "electric": 1},
    ])
    monkeypatch.setattr(bikes, "free_bikes", lambda: [{"lat": 51.1102, "lon": 17.03, "electric": True}])
    monkeypatch.setattr(gtfs, "all_stops_geo", lambda: PRZYSTANKI)
    blad, wynik = _narzedzie(klient, "bikes_near", {"place": "51.11,17.03"})
    assert not blad
    assert [s["name"] for s in wynik["stations"]] == ["Blisko", "Dalej"]
    assert wynik["free_bikes"][0]["near_stop"] == "Rynek"


def test_cars_near_bez_dostawczakow(klient, monkeypatch):
    monkeypatch.setattr(traficar, "car_list", lambda: [
        {"lat": 51.1101, "lon": 17.03, "plate": "VAN1", "van": True},
        {"lat": 51.1102, "lon": 17.03, "plate": "OSO1", "van": False, "range": 200},
    ])
    blad, wynik = _narzedzie(klient, "cars_near", {"place": "51.11,17.03"})
    assert not blad
    assert [c["plate"] for c in wynik["cars"]] == ["OSO1"]
    assert wynik["cars"][0]["range_km"] == 200
    _, wynik = _narzedzie(klient, "cars_near", {"place": "51.11,17.03", "vans": True})
    assert [c["plate"] for c in wynik["cars"]] == ["VAN1", "OSO1"]
