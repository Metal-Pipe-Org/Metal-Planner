"""Stacja kolejowa jako miejsce razem z przystankami pod dworcem - i brak
"dowolnej stacji w mieście" ("WROCŁAW -"): wyszukiwarka celowo jej nie
obsługuje, więc taka nazwa jest nieznana jak każda inna (patrz
gtfs.match_stop).
"""

import datetime

import gtfs
import planner
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)   # dep_sec = 0, dla czytelnych liczb


def _day():
    """Alfa ma dwie stacje, Beta jedną. Pociąg jedzie Alfa Zachód -> Alfa
    Główna -> Beta Główna, drugi kończy bieg na Alfie Głównej przed odjazdem
    pierwszego; tramwaj spod Alfy Głównej na plac. Przystanek tramwaju
    i stacja to jedno miejsce - jak Dworzec Główny i Wrocław Główny."""
    trips = [
        {"trip_id": "POCIAG", "label": "KD 1",
         "stops": [("PKP:1", 0, 0), ("PKP:2", 600, 660), ("PKP:3", 3600, 3600)]},
        {"trip_id": "DOJAZD", "label": "KD 2",
         "stops": [("PKP:1", 60, 60), ("PKP:2", 360, 360)]},
        {"trip_id": "TRAMWAJ", "label": "Tramwaj 1",
         "stops": [("DWORZEC", 300, 300), ("PLAC", 900, 900)]},
    ]
    names = {"PKP:1": "Alfa Zachód", "PKP:2": "Alfa Główna", "PKP:3": "Beta Główna",
             "DWORZEC": "Dworzec Alfa", "PLAC": "Plac"}
    day = make_day(trips, names=names)
    day.pkp_stations = [(names[s], s) for s in ("PKP:1", "PKP:2", "PKP:3")]

    station_place = day.place_of["PKP:2"]
    del day.stops_by_place[day.place_of["DWORZEC"]]
    day.stops_by_place[station_place].append("DWORZEC")
    day.place_of["DWORZEC"] = station_place
    return day


def test_pojedyncza_stacja_dalej_obejmuje_przystanki_pod_dworcem():
    _, stops, _ = gtfs.match_stop("Alfa Główna", _day())

    assert sorted(stops) == ["DWORZEC", "PKP:2"]


def test_pojedyncza_stacja_dalej_dziala_z_przystankiem_mpk(install_day):
    install_day(_day())

    assert "error" not in planner.plan_route("Alfa Główna", "Plac", WHEN)


def test_dowolna_stacja_w_miescie_to_nieznana_nazwa():
    for query in ("ALFA -", "Alfa (dowolna stacja)"):
        name, stops, _ = gtfs.match_stop(query, _day())
        assert name is None and stops is None, query
