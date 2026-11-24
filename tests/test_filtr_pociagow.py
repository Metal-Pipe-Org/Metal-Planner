"""Filtr „Pociągi” (zgłoszenie #511): przy wyłączonych pociągach stacja
kolejowa nie istnieje dla wyszukiwarki, ale pasażer ma wiedzieć dlaczego."""

import datetime

import gtfs
import planner
from tests.gtfs_builder import make_day
from tests import flow

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)


def _days(monkeypatch):
    names = {"PKP:1": "Alfa Główna", "PKP:2": "Beta Główna",
             "A": "Przystanek A", "B": "Przystanek B"}
    tram = {"trip_id": "TRAMWAJ", "label": "Tramwaj 1",
            "stops": [("A", 300, 300), ("B", 900, 900)]}
    train = {"trip_id": "POCIAG", "label": "KD 1",
             "stops": [("PKP:1", 0, 0), ("PKP:2", 3600, 3600)]}
    with_trains = make_day([tram, train], names=names)
    with_trains.pkp_stations = [(names[s], s) for s in ("PKP:1", "PKP:2")]
    without_trains = make_day([tram], names={"A": "Przystanek A", "B": "Przystanek B"})
    monkeypatch.setattr(
        gtfs, "load_day", lambda d, trains=True: with_trains if trains else without_trains)


def test_stacja_przy_wylaczonych_pociagach_dostaje_komunikat(monkeypatch):
    _days(monkeypatch)

    result = flow.plan_flow("Przystanek A", "Beta Główna", WHEN, trains=False)

    assert result["error"] == "Masz wyłączone pociągi — to stacja kolejowa."


def test_literowka_przy_wylaczonych_pociagach_dalej_jest_nieznanym_przystankiem(
        monkeypatch):
    _days(monkeypatch)

    result = flow.plan_flow("Przystanek A", "Xyzzy", WHEN, trains=False)

    assert result["error"].startswith("Nie znam przystanku")
