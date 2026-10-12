"""Przycisk „trasa" w propozycji otwiera wariant TEGO kursu (zgłoszenie #270).

Etap przejazdu niesie `trip` - kurs, którym jedzie - bo napis na czole
wariantu nie wskaże: zjazdy do zajezdni z dwóch różnych pętli noszą ten sam.
Front podaje go dalej do /api/line, a to, że rozkład linii trafia po nim
w wariant, sprawdza tests/test_rozklady.py.
"""

from datetime import datetime

import gtfs
import planner
from tests.gtfs_builder import make_day

WHEN = datetime(2026, 1, 5, 0, 0, 0)

# Dwa zjazdy z jednym napisem, z różnych pętli, przez ten sam przystanek.
Z_KRZYKOW = {"trip_id": "Z_KRZYKOW", "label": "Autobus 112", "headsign": "Zajezdnia",
             "stops": [("K", 0, 0), ("S", 300, 300), ("Z", 600, 600)]}
Z_CENTRUM = {"trip_id": "Z_CENTRUM", "label": "Autobus 112", "headsign": "Zajezdnia",
             "stops": [("C", 900, 900), ("S", 1200, 1200), ("Z", 1500, 1500)]}


def test_etap_niesie_kurs_ktorym_jedzie(install_day):
    install_day(make_day([Z_KRZYKOW, Z_CENTRUM]))

    wynik = planner.plan_flow("S", "Z", WHEN.replace(minute=10))

    [etap] = [leg for journey in wynik["journeys"] for leg in journey["legs"]
              if leg["kind"] == "ride"]
    assert etap["headsign"] == "Zajezdnia"
    assert etap["trip"] == "Z_CENTRUM"


def test_etap_z_przebiegu_kursu_tez_go_niesie(monkeypatch):
    """Drugie miejsce, w którym powstaje etap przejazdu (trasa awaryjna,
    sklejanie przesiadek w _seated_legs) - kurs nie może tam zginąć razem
    z polami roboczymi zdejmowanymi przed odpowiedzią."""
    day = make_day([Z_KRZYKOW, Z_CENTRUM])
    monkeypatch.setattr(gtfs, "trip_path", lambda *args, **kwargs: Z_KRZYKOW["stops"])

    etap = planner._drop_private([planner._ride_leg(day, "Z_KRZYKOW", "K", 0, "Z", 600)])[0]

    assert etap["trip"] == "Z_KRZYKOW"
