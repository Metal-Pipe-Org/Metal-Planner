"""Kolej, tramwaj i autobus mają być tym samym rodzajem rzeczy.

Tu pilnujemy trzech granic, na których było inaczej (2026-08-31):
dotarcie do celu, sklejanie słupków w miejsce i oś czasu.
"""

import json
import sqlite3
from datetime import date

import pytest

import gtfs
import planner
import pkp
from gtfs_builder import make_day


# ---- cel osiągnięty przejściem pieszo ------------------------------------

def _day_z_celem_za_przejsciem(przyjazd_pojazdem=None):
    """Do CELU nie dojeżdża nic - stoi się na nim, przechodząc z SĄSIADA.

    `przyjazd_pojazdem` dokłada wolniejszy kurs dojeżdżający do CELU wprost;
    służy do sprawdzenia, że wcześniejsze dojście pieszo go nie zasłania.
    """
    trips = [{
        "trip_id": "T1", "label": "Tramwaj 1",
        "stops": [("START", 0, 0), ("SASIAD", 600, 600)],
    }]
    if przyjazd_pojazdem is not None:
        trips.append({
            "trip_id": "T2", "label": "Autobus 2",
            "stops": [("START", 0, 60), ("CEL", przyjazd_pojazdem, przyjazd_pojazdem)],
        })
    day = make_day(trips)
    # CEL bywa słupkiem, przez który nic nie przejeżdża - make_day zna tylko
    # przystanki z kursów, więc trzeba go dopisać ręcznie.
    if "CEL" not in day.stop_names:
        day.stop_names["CEL"] = "CEL"
        day.stop_coords["CEL"] = (51.111, 17.031)
    day.siblings = {"SASIAD": ("CEL",), "CEL": ("SASIAD",)}
    return day


def test_a_target_reached_only_on_foot_is_still_a_connection():
    """Cel, na którym staje się WYŁĄCZNIE przechodząc z sąsiedniego słupka,
    ma być znaleziony. Skan pytał "czy to już cel" tylko przy wysiadaniu
    z pojazdu, więc taką relację ogłaszał jako nieistniejącą - a miał jej
    godzinę policzoną (stacja kolejowa obok przystanku, 2026-08-31)."""
    day = _day_z_celem_za_przejsciem()
    stop, arr, _ = planner._scan(day, ["START"], ["CEL"], 0)
    assert stop == "CEL", "cel za przejściem pieszo ogłoszony jako nieosiągalny"
    assert arr == 600 + planner.WALK_SEC


def test_the_walk_does_not_hide_a_later_ride_to_the_target():
    """Wcześniejsze dojście pieszo wygrywa z późniejszym dojazdem - ale
    ogłoszone ma być JAKIEKOLWIEK dotarcie. Zanim przejście zaczęło się
    liczyć, jego godzina i tak lądowała w tabeli najwcześniejszych dojazdów
    i blokowała późniejszy kurs, który BYŁBY zauważony: wychodziło z tego
    "nie znaleziono połączenia" mimo dwóch dobrych dróg."""
    pozniej = 600 + planner.WALK_SEC + 60
    day = _day_z_celem_za_przejsciem(przyjazd_pojazdem=pozniej)
    stop, arr, _ = planner._scan(day, ["START"], ["CEL"], 0)
    assert stop == "CEL"
    assert arr == 600 + planner.WALK_SEC, "wygrać ma szybsze dotarcie, nie to pojazdem"


def test_a_walk_arrival_can_be_reconstructed_into_legs():
    """Trasa kończąca się przejściem ma się dać opowiedzieć etapami - inaczej
    znalezienie jej tylko przesuwa błąd o jeden krok dalej."""
    day = _day_z_celem_za_przejsciem()
    stop, _, journey = planner._scan(day, ["START"], ["CEL"], 0)
    legs = planner._reconstruct(day, journey, stop)
    assert [leg["kind"] for leg in legs] == ["ride", "walk"]


# ---- jedno miejsce to jedno miejsce ---------------------------------------

_BLISKO = (51.110, 17.030)
_OBOK = (51.1105, 17.0305)      # ~65 m
_DALEKO = (51.500, 19.500)      # ~200 km


def test_the_same_name_far_away_is_not_the_same_place():
    """Identyczna nazwa wystarczała, dopóki dane były z jednego miasta.
    Ogólnopolski słownik stacji łamie to założenie - a sklejone miejsce
    znaczy trzyminutowe przejście, więc byłby to spacer przez pół Polski."""
    coords = {"A": _BLISKO, "DALEKA": _DALEKO}
    assert gtfs._one_spot(["A", "DALEKA"], coords) != ["A", "DALEKA"]


def test_the_far_stop_is_dropped_not_the_whole_place():
    """Odstający wypada sam. Dwa wrocławskie słupki tej samej nazwy mają
    zostać jednym miejscem także wtedy, gdy do ich nazwy dopisze się stacja
    spod Kielc - inaczej kolej rozbija miejsca, których nie dotyczy."""
    coords = {"A": _BLISKO, "B": _OBOK, "DALEKA": _DALEKO}
    assert gtfs._one_spot(["A", "B", "DALEKA"], coords) == ["A", "B"]


def test_stops_that_really_stand_together_stay_one_place():
    coords = {"A": _BLISKO, "B": _OBOK}
    assert gtfs._one_spot(["A", "B"], coords) == ["A", "B"]


# ---- jedna oś czasu -------------------------------------------------------

def test_rail_times_are_cut_to_whole_minutes_the_safe_way():
    """Rozkład miejski nie zna sekund, kolejowy tak - jedna oś czasu nie może
    mieć dwóch dokładności. Ucięcie jest ostrożne: odjazd w dół, przyjazd
    w górę, żeby nigdy nie obiecać sekund, których nie ma."""
    assert pkp._sec_of("11:15:42") == 11 * 3600 + 15 * 60
    assert pkp._sec_of("11:21:42", round_up=True) == 11 * 3600 + 22 * 60
    assert pkp._sec_of("11:21:00", round_up=True) == 11 * 3600 + 21 * 60


def _kolejowa_baza(tmp_path, postoj):
    """Mała baza kolejowa: jeden kurs, dwie stacje, `postoj` = (przyjazd,
    odjazd) na stacji pośredniej."""
    db_path = tmp_path / "pkp.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript(
        "CREATE TABLE stations (station_id INTEGER, name TEXT);"
        "CREATE TABLE routes (schedule_id INTEGER, order_id INTEGER, name TEXT,"
        " carrier_code TEXT, national_number TEXT, category TEXT);"
        "CREATE TABLE stops (schedule_id INTEGER, order_id INTEGER,"
        " station_id INTEGER, order_number INTEGER, arrival_time TEXT,"
        " departure_time TEXT);"
        "CREATE TABLE operating_dates (schedule_id INTEGER, order_id INTEGER, date TEXT);"
    )
    db.execute("INSERT INTO stations VALUES (1, 'Stacja A')")
    db.execute("INSERT INTO stations VALUES (2, 'Stacja B')")
    db.execute("INSERT INTO stations VALUES (3, 'Stacja C')")
    db.execute("INSERT INTO routes VALUES (7, 1, 'Kurs', 'PR', '111', 'Os')")
    db.execute("INSERT INTO operating_dates VALUES (7, 1, '2026-08-31')")
    db.execute("INSERT INTO stops VALUES (7, 1, 1, 1, NULL, '10:00:00')")
    db.execute("INSERT INTO stops VALUES (7, 1, 2, 2, ?, ?)", postoj)
    db.execute("INSERT INTO stops VALUES (7, 1, 3, 3, '10:30:00', NULL)")
    db.commit()
    db.close()
    return db_path


@pytest.fixture
def kolej(tmp_path, monkeypatch):
    """Kolej włączona, z bazą i współrzędnymi w katalogu tymczasowym."""
    coords_path = tmp_path / "coords.json"
    coords_path.write_text(json.dumps(
        {"1": [51.110, 17.030], "2": [51.120, 17.040], "3": [51.130, 17.050]}))
    monkeypatch.setattr(pkp, "COORDS_PATH", coords_path)
    monkeypatch.setattr(pkp, "enabled", lambda: True)
    monkeypatch.setattr(pkp, "_stations_cache", {})
    return coords_path


def _kursy(day):
    return [c for c in day.conns if str(c[4]).startswith("PKP:")]


def test_a_dwell_shorter_than_a_minute_does_not_rewind_the_clock(kolej, tmp_path,
                                                                 monkeypatch):
    """Przyjazd w górę i odjazd w dół potrafią się na jednej stacji minąć
    (10:14:42 -> 10:15 przyjazdu, 10:14:48 -> 10:14 odjazdu). Cofnięcie czasu
    jest niżej brane za przejście przez północ, więc nietknięte dokładałoby
    do kursu całą dobę. Dotyczy 14% postojów w prawdziwych danych."""
    monkeypatch.setattr(pkp, "DB_PATH",
                        _kolejowa_baza(tmp_path, ("10:14:42", "10:14:48")))
    day = gtfs.DayData()
    pkp.augment_day(day, date(2026, 8, 31))
    kursy = _kursy(day)
    assert kursy, "kurs kolejowy w ogóle nie doklejony"
    assert all(c[0] < 24 * 3600 for c in kursy), "postój dorzucił całą dobę"
    assert all(c[0] <= c[1] for c in kursy), "odjazd przed przyjazdem"


def test_rail_connections_land_on_whole_minutes(kolej, tmp_path, monkeypatch):
    monkeypatch.setattr(pkp, "DB_PATH",
                        _kolejowa_baza(tmp_path, ("10:14:42", "10:15:30")))
    day = gtfs.DayData()
    pkp.augment_day(day, date(2026, 8, 31))
    kursy = _kursy(day)
    assert kursy
    assert all(c[0] % 60 == 0 and c[1] % 60 == 0 for c in kursy)


def test_a_rail_station_gets_no_transfers_of_its_own(kolej, tmp_path, monkeypatch):
    """Stacja nie dostaje sąsiadów z własnego promienia - przesiadka bierze
    się wyłącznie z tego, że stacja przechodzi przez to samo sklejanie
    w miejsce co przystanek (2026-08-31, usunięty drugi mechanizm)."""
    monkeypatch.setattr(pkp, "DB_PATH",
                        _kolejowa_baza(tmp_path, ("10:15:00", "10:16:00")))
    day = gtfs.DayData()
    day.stop_names["MIEJSKI"] = "Przystanek"
    day.stop_coords["MIEJSKI"] = (51.1101, 17.0301)   # ~15 m od Stacji A
    pkp.augment_day(day, date(2026, 8, 31))
    assert day.siblings == {}, "kolej dorobiła sobie własne przesiadki"
