"""Wyłączone propozycje tras (opcja pod zębatką, zgłoszenie #171).

Wyłączona lista nie jest liczona wcale - serwer jej nie składa, żeby
wyszukiwanie trwało krócej. Mapa ma przy tym zostać dokładnie taka sama.
"""

from datetime import datetime

import planner
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


def test_bez_propozycji_lista_jest_pusta_a_mapa_ta_sama(install_day):
    install_day(_dzien())

    z_lista = planner.plan_flow("S", "E", WHEN, value_map=True)
    bez = planner.plan_flow("S", "E", WHEN, value_map=True, with_journeys=False)

    assert z_lista["journeys"]
    assert bez["journeys"] == []
    assert {**bez, "journeys": None} == {**z_lista, "journeys": None}


def test_rower_nie_mowi_o_liscie_ktorej_nie_ma(install_day):
    """Stan rowerowych propozycji opisuje listę - bez listy go nie ma, żeby
    front nie pisał, że rower nic tu nie daje."""
    install_day(_dzien())

    bez = planner.plan_flow("S", "E", WHEN, value_map=True, use_bikes=True,
                            with_journeys=False)

    assert "bikes" not in bez
