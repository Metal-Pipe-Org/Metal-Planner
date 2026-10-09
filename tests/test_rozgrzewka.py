"""Wyszukiwanie nie czeka na rowery, auta ani rozkład (zgłoszenie #229,
patrz warmup.py).

Wyszukiwanie czekało na pobranie rowerów i aut z cudzych serwerów, zanim
policzyło cokolwiek. Teraz liczy na tym, co jest w pamięci, choćby starym,
a świeże pobierają się obok - nie częściej niż dotąd. Poza wyszukiwaniem
(warstwy na mapie) wszystko jest po staremu.
"""

import threading
import time
from datetime import date, datetime

import pytest
from flask import Flask

import bikes
import routes
import traficar
import warmup

INFO = {"data": {"stations": [
    {"station_id": "1", "name": "Rynek", "lat": 51.11, "lon": 17.03},
]}}
STATUS = {"data": {"stations": [
    {"station_id": "1", "num_bikes_available": 4, "num_docks_available": 7},
]}}
TYPES = {"data": {"vehicle_types": []}}
AUTA = [{"lat": 51.1, "lon": 17.0, "plate": "DW 1"}]

# Prawdziwe zone() - w testach jest zatkane fixturem (conftest.py), a tu
# chodzi właśnie o nie.
ZONE = traficar.zone


def _bez_sieci(*args):
    raise AssertionError("wyszukiwanie czekało na sieć")


@pytest.fixture
def w_tle(monkeypatch):
    """Zlecone odświeżenia zamiast wątków: lista nazw kanałów."""
    zlecone = []

    def zlec(name, refresh):
        warmup._search.current.stale = True
        zlecone.append(name)
    monkeypatch.setattr(warmup, "refresh_in_background", zlec)
    return zlecone


def _auta(monkeypatch, wiek_sec, generation=1, cars=AUTA):
    monkeypatch.setattr(traficar, "_cars_cache", {
        "at": time.monotonic() - wiek_sec, "cars": cars, "generation": generation})


def test_wyszukiwanie_bierze_stare_auta_i_zleca_swieze(monkeypatch, w_tle):
    _auta(monkeypatch, wiek_sec=600)
    monkeypatch.setattr(traficar, "_fetch", _bez_sieci)
    with warmup.no_waiting() as search:
        assert traficar.car_list() == AUTA
    assert w_tle == ["traficar-cars"]
    assert search.stale


def test_swieze_auta_niczego_nie_zlecaja(monkeypatch, w_tle):
    """Nie częściej niż dotąd: w czasie ważności kanału nikt go nie pobiera."""
    _auta(monkeypatch, wiek_sec=1)
    with warmup.no_waiting() as search:
        assert traficar.car_list() == AUTA
    assert w_tle == []
    assert not search.stale


def test_bez_aut_w_pamieci_nie_ma_propozycji_zamiast_czekania(monkeypatch, w_tle):
    _auta(monkeypatch, wiek_sec=10 ** 6, generation=0, cars=[])
    monkeypatch.setattr(traficar, "_fetch", _bez_sieci)
    with warmup.no_waiting():
        with pytest.raises(traficar.TraficarDataError):
            traficar.car_list()
    assert w_tle == ["traficar-cars"]


def test_poza_wyszukiwaniem_auta_pobieraja_sie_od_razu(monkeypatch):
    """Warstwa aut bez wyszukiwania (/api/cars) - po staremu."""
    _auta(monkeypatch, wiek_sec=600)
    pobrane = []
    monkeypatch.setattr(traficar, "_fetch", lambda url: pobrane.append(url) or {"cars": []})
    monkeypatch.setattr(traficar, "_models", lambda: {})
    assert traficar.car_list() == []
    assert len(pobrane) == 1


def test_wyszukiwanie_bierze_stara_strefe(monkeypatch, w_tle):
    strefa = {"end": [], "no_end": [], "relocation": []}
    monkeypatch.setattr(traficar, "_zone_cache", {"at": 0.0, "zone": strefa})
    monkeypatch.setattr(traficar, "_fetch", _bez_sieci)
    with warmup.no_waiting():
        assert ZONE() is strefa
    assert w_tle == ["traficar-zone"]


def test_wyszukiwanie_bierze_stare_stacje(monkeypatch, w_tle):
    dawno = time.monotonic() - 10 ** 4
    bikes._cache.update({"info": INFO, "status": STATUS, "types": TYPES,
                         "info_at": dawno, "status_at": dawno, "types_at": dawno})
    with warmup.no_waiting() as search:
        stacje = bikes.stations()
    assert [s["bikes"] for s in stacje] == [4]
    assert w_tle and set(w_tle) == {"wrm"}
    assert search.stale


def test_bez_rowerow_w_pamieci_nie_ma_ich_zamiast_czekania(monkeypatch, w_tle):
    monkeypatch.setattr(bikes, "_fetch", _bez_sieci)
    with warmup.no_waiting():
        assert bikes.stations_quiet() == []
    assert w_tle == ["wrm"]


def test_w_tle_pobieraja_sie_tylko_przeterminowane_kanaly(monkeypatch):
    pobrane = []
    monkeypatch.setattr(bikes, "_fetch", lambda url: pobrane.append(url) or {})
    bikes._refetch_stale()
    assert len(pobrane) == len(bikes._FEEDS)
    pobrane.clear()
    bikes._refetch_stale()
    assert pobrane == []
    bikes._cache["status_at"] -= bikes.STATUS_CACHE_SEC
    bikes._refetch_stale()
    assert pobrane == [bikes.STATUS_URL]


def test_ten_sam_kanal_nie_pobiera_sie_dwa_razy_naraz():
    puszczony = threading.Event()
    wywolania = []

    def wolne():
        wywolania.append(1)
        puszczony.wait(5)
    warmup.refresh_in_background("test", wolne)
    warmup.refresh_in_background("test", wolne)
    puszczony.set()
    warmup.wait_for_refreshes(5)
    assert wywolania == [1]


def _api(monkeypatch, stale):
    def plan_flow(*args, **kwargs):
        if stale:
            warmup._search.current.stale = True
        return {"segments": []}
    monkeypatch.setattr(routes, "plan_flow", plan_flow)
    czekania = []
    monkeypatch.setattr(warmup, "wait_for_refreshes", czekania.append)
    app = Flask(__name__)
    routes.init_routes(app)
    return app.test_client(), czekania


def test_odpowiedz_mowi_ze_dane_na_zywo_sie_pobieraja(monkeypatch):
    client, czekania = _api(monkeypatch, stale=True)
    assert client.get("/api/flow?start=A&end=B").get_json()["live_pending"] is True
    assert czekania == []


def test_odpowiedz_na_swiezych_danych_o_niczym_nie_mowi(monkeypatch):
    client, _ = _api(monkeypatch, stale=False)
    assert "live_pending" not in client.get("/api/flow?start=A&end=B").get_json()


def test_dopytanie_czeka_na_swieze_dane(monkeypatch):
    client, czekania = _api(monkeypatch, stale=False)
    client.get("/api/flow?start=A&end=B&fresh=1")
    assert czekania == [routes.LIVE_REFRESH_WAIT_SEC]


@pytest.mark.parametrize("godzina, dni", [
    (22, [date(2026, 10, 7)]),
    (23, [date(2026, 10, 7), date(2026, 10, 8)]),
])
def test_przed_polnoca_czeka_tez_rozklad_dnia_nastepnego(godzina, dni):
    assert warmup.days_to_warm(datetime(2026, 10, 7, godzina, 30)) == dni
