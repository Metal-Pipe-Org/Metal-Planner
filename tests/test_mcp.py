"""Endpoint MCP (mcp_server.py, mcp_tools.py): protokół JSON-RPC w wersji
bezstanowej i narzędzia, które oddają to samo co /api/*, tylko bez geometrii.

Rozkład jest syntetyczny (tests/gtfs_builder.py), więc testy nie potrzebują
data/gtfs.sqlite ani sieci.
"""

import json

import pytest

import mcp_tools
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
    assert {t["name"] for t in narzedzia} == {"plan_trip", "find_stop", "departures", "trip", "line"}
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
