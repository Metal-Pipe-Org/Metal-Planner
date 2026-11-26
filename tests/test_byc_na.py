"""Tryb „być na" (zgłoszenie #265): godzina z formularza to przyjazd do celu.

Lustro zwykłego wyszukiwania: pasek mówi o najpóźniejszym wyjściu, które
jeszcze dowozi na czas, a na mapie nie ma niczego, co dowozi później.
"""
from datetime import date, datetime

import gtfs
from tests import flow
from tests.gtfs_builder import make_day

M = 60


def _bus(prefix, label, rows):
    """Kursy z S do E: rows = [(minuta odjazdu, minuta przyjazdu)]."""
    return [{"trip_id": f"{prefix}{k}", "label": label,
             "stops": [("S", dep * M, dep * M), ("E", arr * M, arr * M)]}
            for k, (dep, arr) in enumerate(rows)]


DAY = _bus("a", "Autobus 1", [(20, 40), (30, 50), (40, 60)])


def test_najpozniejsze_wyjscie_ktore_zdaza(install_day):
    install_day(make_day(DAY))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 0, 55), arrive_by=True)

    assert result["starts"] == "00:30"
    assert result["best_arrival"] == "00:50"
    assert result["arrive_by"] == "00:55"


def test_na_mapie_nic_nie_dowozi_po_godzinie(install_day):
    install_day(make_day(DAY + _bus("b", "Autobus 2", [(33, 58)])))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 0, 55), arrive_by=True)

    assert result["scheme"]["moves"]
    assert all(move["arrival_sec"] <= 55 * M for move in result["scheme"]["moves"])
    assert "2" not in {s["num"] for s in result["segments"]}


def test_bez_polaczenia_mowi_o_przyjezdzie(install_day):
    install_day(make_day(DAY))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 0, 35), arrive_by=True)

    assert result["error"] == "Nie znaleziono połączenia S → E z przyjazdem do 00:35 tego dnia."


def test_dzien_za_rozkladem_mowi_ze_rozkladu_jeszcze_nie_ma(install_day, monkeypatch):
    """Tak samo jak przy odjeździe: za ostatnim dniem paczki połączeń nie ma,
    bo rozkładu jeszcze nie opublikowano - a nie dlatego, że nic nie dowozi."""
    install_day(make_day(DAY))
    monkeypatch.setattr(gtfs, "last_service_date", lambda: date(2026, 1, 4))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 0, 35), arrive_by=True)

    assert result["error"] == "Rozkładu na 05.01 jeszcze nie opublikowano — obecny sięga do 04.01."


def test_odjazd_za_rozkladem_mowi_to_od_razu_bez_szukania(monkeypatch):
    """Odjazd tak jak przyjazd: komunikat bez przeszukiwania kolejnych pustych dób."""
    monkeypatch.setattr(gtfs, "last_service_date", lambda: date(2026, 1, 4))

    def nie_szukaj(*args, **kwargs):
        raise AssertionError("dzień za rozkładem nie powinien wczytywać rozkładu")
    monkeypatch.setattr(gtfs, "load_day", nie_szukaj)

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 0, 35))

    assert result["error"] == "Rozkładu na 05.01 jeszcze nie opublikowano — obecny sięga do 04.01."


def test_zwykle_wyszukiwanie_bez_zmian(install_day):
    install_day(make_day(DAY))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 0, 25))

    assert result["starts"] == "00:30"
    assert "arrive_by" not in result


def _teraz(monkeypatch, kiedy):
    """Dziś = dzień pytania, a teraz = `kiedy` (planner pyta zegara)."""
    import datetime as dt
    import planner

    class _Dzis(dt.date):
        @classmethod
        def today(cls):
            return kiedy.date()

    class _Teraz(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return kiedy
    monkeypatch.setattr(planner, "date", _Dzis)
    monkeypatch.setattr(planner, "datetime", _Teraz)


def test_dzis_nie_ma_wyjsc_ktore_juz_minely(install_day, monkeypatch):
    install_day(make_day(DAY + _bus("b", "Autobus 2", [(33, 58)])))
    _teraz(monkeypatch, datetime(2026, 1, 5, 0, 45))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 1, 0), arrive_by=True)

    # 0:33 odjechało ponad 10 min temu, 0:40 jeszcze zdąży
    assert [j["departure"] for j in result["journeys"]] == ["00:40"]
    assert all(move["board_sec"] >= 35 * M for move in result["scheme"]["moves"])


def test_dzis_gdy_nic_juz_nie_zdazy_mowi_to_wprost(install_day, monkeypatch):
    install_day(make_day(DAY))
    _teraz(monkeypatch, datetime(2026, 1, 5, 0, 52))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 1, 0), arrive_by=True)

    assert result["error"] == "Na przyjazd do 01:00 nie zdążysz już żadnym połączeniem."


def test_odjazd_z_minionej_godziny_dalej_dziala(install_day, monkeypatch):
    install_day(make_day(DAY))
    _teraz(monkeypatch, datetime(2026, 1, 5, 0, 52))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 0, 25))

    assert result["starts"] == "00:30"
