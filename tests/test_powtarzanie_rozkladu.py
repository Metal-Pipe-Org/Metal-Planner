"""Napis „Ten rozkład obowiązuje ...” nad tablicą przystanku (#439).

Pasażer, który patrzy na rozkład przystanku na jedną datę, chce wiedzieć, czy
ten sam rozkład będzie też jutro i za tydzień. Odpowiedź wynika z kalendarza
GTFS, ale porównujemy KURSY, nie numery usług: ten sam rozkład bywa zapisany
pod różnymi service_id, a święto z niedzielnym kursowaniem potrafi wskazać
usługę niedzielną w środku tygodnia.

Baza jest prawdziwym SQLite, bo napis czyta kalendarz wprost.
"""

import datetime
import sqlite3

import pytest

import gtfs
import timetables
import update_gtfs

MONDAY = datetime.date(2026, 8, 17)
WEDNESDAY = datetime.date(2026, 8, 19)
SATURDAY = datetime.date(2026, 8, 22)
SUNDAY = datetime.date(2026, 8, 23)
HOLIDAY = datetime.date(2026, 8, 26)       # środa kursująca jak niedziela


@pytest.fixture
def feed(tmp_path, monkeypatch):
    path = tmp_path / "gtfs.sqlite"
    db = sqlite3.connect(path)
    db.executescript(update_gtfs.SCHEMA)
    db.executemany("INSERT INTO stops VALUES (?,?,?,?)", [
        ("A", "PLAC", 51.10, 17.00), ("B", "KONIEC", 51.12, 17.02),
    ])
    db.execute("INSERT INTO routes VALUES ('R1','1','',3)")
    db.executemany(
        "INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,'20260801','20260930')",
        [
            ("ROBOCZY", 1, 1, 1, 1, 1, 0, 0),
            # Dwie usługi o tej samej treści: weekend zapisany osobno na
            # sobotę i niedzielę nie może rozbijać rozkładu na dwa.
            ("SOB", 0, 0, 0, 0, 0, 1, 0),
            ("NIEDZ", 0, 0, 0, 0, 0, 0, 1),
        ],
    )
    trips = [
        ("r1", "ROBOCZY", 21600), ("r2", "ROBOCZY", 25200),
        ("s1", "SOB", 28800), ("n1", "NIEDZ", 28800),
        ("jeden", "TYLKO_DZIS", 36000),
    ]
    for trip_id, service, sec in trips:
        db.execute("INSERT INTO trips VALUES (?,?,?,?,NULL)",
                   (trip_id, "R1", service, "KONIEC"))
        db.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?)",
                       [(trip_id, 0, "A", sec, sec), (trip_id, 1, "B", sec + 600, sec + 600)])
    db.executemany("INSERT INTO calendar_dates VALUES (?,?,?)", [
        ("ROBOCZY", HOLIDAY.strftime("%Y%m%d"), 2),
        ("NIEDZ", HOLIDAY.strftime("%Y%m%d"), 1),
        ("TYLKO_DZIS", "20260820", 1),
    ])
    db.commit()
    db.close()

    monkeypatch.setattr(gtfs, "DB_PATH", path)
    gtfs._day_cache.clear()
    yield path
    gtfs._day_cache.clear()


def _napis(day):
    return timetables.stop_board("PLAC", day)["repeats"]


def test_dzien_roboczy_z_swietem_w_oknie_ma_wyjatek(feed):
    """Poniedziałek ma ten sam rozkład co reszta tygodnia, ale święto w środę
    okna go nie ma - napis nie może obiecać, że jest tak codziennie."""
    assert _napis(MONDAY) == "w dni robocze (z wyjątkami)"


def test_sobota_i_niedziela_o_tej_samej_tresci_to_jeden_rozklad(feed):
    """Soboty i niedziele mają różne service_id, ale te same kursy, więc
    razem są weekendem; środowe święto kursuje jak niedziela."""
    assert _napis(SUNDAY) == "w weekendy i święta"
    assert _napis(SATURDAY) == "w weekendy i święta"


def test_swieto_pokazuje_rozklad_niedzielny(feed):
    assert _napis(HOLIDAY) == "w weekendy i święta"


def test_dzien_z_jednorazowym_kursem_obowiazuje_tylko_raz(feed):
    assert _napis(datetime.date(2026, 8, 20)) == "tylko w ten dzień"


@pytest.mark.parametrize("days, expected", [
    ({0, 1, 2, 3, 4, 5, 6}, "codziennie"),
    ({0, 1, 2, 3, 4}, "w dni robocze"),
    ({5, 6}, "w weekendy"),
    ({5}, "w soboty"),
    ({0, 1, 2, 3}, "pon–czw"),
    ({0, 2, 4}, "w poniedziałki, środy i piątki"),
    ({0, 1, 2, 3, 4, 6}, "codziennie oprócz sobót"),
])
def test_zwrot_o_dniach_tygodnia(days, expected):
    assert timetables._weekday_phrase(days) == expected
