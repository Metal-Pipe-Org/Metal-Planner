"""Opóźnienia pociągów na żywo (zgłoszenie #71, patrz pkp_live.py).

API pytamy pociąg po pociągu, więc pilnujemy trzech rzeczy: że opóźnienie
liczy się względem NASZEGO rozkładu, że ten sam pociąg nie idzie do API
częściej niż raz na TTL_SEC i że własne sufity zapytań naprawdę trzymają.
"""

from datetime import date, datetime

import pytest

import gtfs
import pkp
import pkp_live

DZIS = date.today()
TRIP = "PKP:2026:111"


def _dzien(trips=(TRIP,)):
    day = gtfs.DayData()
    day.stop_names.update({"PKP:1": "Stacja A", "PKP:2": "Stacja B"})
    for trip in trips:
        day.pkp_trip_stops[trip] = [("PKP:1", 36000, 36000), ("PKP:2", 37800, 37800)]
    return day


@pytest.fixture
def api(monkeypatch):
    """Kolej włączona, czysty stan modułu i licznik zapytań do „API"."""
    monkeypatch.setattr(pkp, "enabled", lambda: True)
    monkeypatch.setattr(pkp_live, "_cache", {})
    monkeypatch.setattr(pkp_live, "_hour", pkp_live.deque())
    monkeypatch.setattr(pkp_live, "_day", [None, 0])
    calls = []

    def fake_fetch(schedule_id, order_id, when):
        calls.append((schedule_id, order_id, when))
        base = datetime.combine(when, datetime.min.time())
        return {1: (None, base.replace(hour=10, minute=3)),        # +3 przy odjeździe
                2: (base.replace(hour=10, minute=37), None)}       # +7 przy przyjeździe
    monkeypatch.setattr(pkp_live, "_fetch", fake_fetch)
    return calls


def test_opoznienie_wzgledem_naszego_rozkladu(api):
    assert pkp_live.delays(_dzien(), [TRIP], DZIS) == {TRIP: {
        "Stacja A": {"arr": None, "dep": 3},
        "Stacja B": {"arr": 7, "dep": None},
    }}
    assert api == [("2026", "111", DZIS)]


def test_ten_sam_pociag_raz_na_ttl(api):
    for _ in range(5):
        pkp_live.delays(_dzien(), [TRIP], DZIS)
    assert len(api) == 1


def test_sufit_godzinowy_trzyma(api, monkeypatch):
    """Po wyczerpaniu sufitu opóźnień nie ma - i API nie dostaje ani jednego
    zapytania więcej."""
    monkeypatch.setattr(pkp_live, "HOURLY_LIMIT", 2)
    for n in range(4):
        pkp_live.delays(_dzien([f"PKP:2026:{n}"]), [f"PKP:2026:{n}"], DZIS)
    assert len(api) == 2


def test_sufit_dobowy_trzyma(api, monkeypatch):
    monkeypatch.setattr(pkp_live, "DAILY_LIMIT", 3)
    for n in range(5):
        pkp_live.delays(_dzien([f"PKP:2026:{n}"]), [f"PKP:2026:{n}"], DZIS)
    assert len(api) == 3


def test_tylko_dzis_i_tylko_kolej(api, monkeypatch):
    assert pkp_live.delays(_dzien(), [TRIP], date(2020, 1, 1)) == {}
    assert pkp_live.delays(_dzien(), ["zwykly-kurs"], DZIS) == {}
    monkeypatch.setattr(pkp, "enabled", lambda: False)
    assert pkp_live.delays(_dzien(), [TRIP], DZIS) == {}
    assert api == []
