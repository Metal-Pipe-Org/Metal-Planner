"""Tryb „być na" (zgłoszenie #265): godzina z formularza to przyjazd do celu.

Lustro zwykłego wyszukiwania: pasek mówi o najpóźniejszym wyjściu, które
jeszcze dowozi na czas, a na mapie nie ma niczego, co dowozi później.
"""
from datetime import datetime

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


def test_zwykle_wyszukiwanie_bez_zmian(install_day):
    install_day(make_day(DAY))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 0, 25))

    assert result["starts"] == "00:30"
    assert "arrive_by" not in result
