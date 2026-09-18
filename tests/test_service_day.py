"""Doba rozkładowa: kursy nocne z dnia D-1 obsługują godziny 00:00-06:00 dnia D.

GTFS zapisuje kurs wyjeżdżający o 23:50 i jadący dalej jako 24:10, 24:30
w kalendarzu dnia, w którym wyruszył. Rozkład dnia D musi więc wciągnąć
ogon dnia D-1 przesunięty o -24 h - inaczej po północy nie widać ani jednego
autobusu nocnego (patrz gtfs.PREV_DAY_SEC).
"""

import datetime
import sqlite3

import pytest

import gtfs
import planner
import update_gtfs

SATURDAY = datetime.date(2026, 8, 22)
SUNDAY = datetime.date(2026, 8, 23)

STOPS = ["Alfa", "Beta", "Gamma", "Delta"]

# (trip_id, service_id, [(stop, sekundy), ...])
TRIPS = [
    # Nocny z soboty: rusza 23:50, po północy jedzie jako 24:10 / 24:30 / 24:50.
    ("nocny_sob", "SOBOTA", [("Alfa", 85800), ("Beta", 87000),
                             ("Gamma", 88200), ("Delta", 89400)]),
    # Dzienny niedzielny - punkt odniesienia, że doba D nic nie traci.
    ("dzienny_ndz", "NIEDZIELA", [("Alfa", 18000), ("Beta", 19200),
                                  ("Gamma", 20400), ("Delta", 21600)]),
    # Kursuje codziennie, więc w rozkładzie niedzieli występuje dwa razy:
    # raz jako ogon soboty (00:20), raz jako własna noc niedzieli (24:20).
    ("codzienny", "CODZIENNIE", [("Beta", 87600), ("Gamma", 88800)]),
]


@pytest.fixture
def feed(tmp_path, monkeypatch):
    """Minimalna baza GTFS w tmp_path, podstawiona pod gtfs.DB_PATH."""
    path = tmp_path / "gtfs.sqlite"
    db = sqlite3.connect(path)
    db.executescript(update_gtfs.SCHEMA)
    db.executemany(
        "INSERT INTO stops VALUES (?,?,?,?)",
        [(s, s, 51.10 + i * 0.02, 17.00 + i * 0.02) for i, s in enumerate(STOPS)],
    )
    db.execute("INSERT INTO routes VALUES ('R246','246','',3)")
    db.executemany(
        "INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,'20260801','20260930')",
        [
            ("SOBOTA",     0, 0, 0, 0, 0, 1, 0),
            ("NIEDZIELA",  0, 0, 0, 0, 0, 0, 1),
            ("CODZIENNIE", 1, 1, 1, 1, 1, 1, 1),
        ],
    )
    for trip_id, service_id, stops in TRIPS:
        db.execute("INSERT INTO trips VALUES (?,'R246',?,'',NULL)", (trip_id, service_id))
        db.executemany(
            "INSERT INTO stop_times VALUES (?,?,?,?,?)",
            [(trip_id, i, stop, sec, sec) for i, (stop, sec) in enumerate(stops)],
        )
    db.commit()
    db.close()

    monkeypatch.setattr(gtfs, "DB_PATH", path)
    gtfs._day_cache.clear()
    yield path
    gtfs._day_cache.clear()


def _departures(day, trip_prefix):
    """Odjazdy połączeń danego kursu, w sekundach od północy wczytanego dnia."""
    return sorted(c[0] for c in day.conns if c[4] == trip_prefix)


def test_nocny_z_soboty_jest_w_rozkladzie_niedzieli(feed):
    """Ogon soboty wchodzi w niedzielę przesunięty o -24 h."""
    day = gtfs.load_day(SUNDAY)
    nocny = gtfs.PREV_DAY_PREFIX + "nocny_sob"
    # 24:10 -> 00:10 (600 s) i 24:30 -> 00:30 (1800 s)
    assert _departures(day, nocny) == [600, 1800]


def test_przejazd_sprzed_polnocy_jest_nieosiagalny(feed):
    """Odcinek 23:50 -> 24:10 należy do wczoraj: nie da się do niego wsiąść."""
    day = gtfs.load_day(SUNDAY)
    nocny = gtfs.PREV_DAY_PREFIX + "nocny_sob"
    assert all(c[0] >= 0 for c in day.conns)
    assert not [c for c in day.conns if c[4] == nocny and c[2] == "Alfa"]


def test_kurs_z_obu_dob_to_dwa_rozne_autobusy(feed):
    """Serwis codzienny daje w jednym rozkładzie dwa egzemplarze kursu:
    ogon wczoraj (00:20) i własną noc (24:20). Planner kluczuje przesiadki
    po trip_id, więc muszą mieć różne identyfikatory."""
    day = gtfs.load_day(SUNDAY)
    dzis, wczoraj = "codzienny", gtfs.PREV_DAY_PREFIX + "codzienny"
    assert _departures(day, wczoraj) == [87600 - gtfs.PREV_DAY_SEC]   # 00:20
    assert _departures(day, dzis) == [87600]                          # 24:20
    assert day.trip_info[wczoraj] == day.trip_info[dzis] == ("Autobus 246", "")


def test_doba_wlasna_nic_nie_traci(feed):
    """Kursy dnia D zachowują swoje sekundy - nic ich nie przesuwa."""
    day = gtfs.load_day(SUNDAY)
    assert _departures(day, "dzienny_ndz") == [18000, 19200, 20400]
    # sobota nie widzi jeszcze niedzielnego poranka
    assert not _departures(gtfs.load_day(SATURDAY), "dzienny_ndz")


def test_planner_znajduje_nocny_po_polnocy(feed):
    """Regresja: o 00:05 w niedzielę widać nocny z 00:10, a nie dopiero
    poranny o 05:00."""
    o_polnocy = planner.plan_route("Beta", "Delta", datetime.datetime(2026, 8, 23, 0, 5))
    assert o_polnocy.get("error") is None
    assert o_polnocy["departure"] == "00:10"

    # przed północą ten sam kurs widać jako 23:50, z przyjazdem po północy
    wieczorem = planner.plan_route("Alfa", "Delta", datetime.datetime(2026, 8, 22, 23, 0))
    assert wieczorem["departure"] == "23:50"
    assert wieczorem["arrival"] == "00:50"


def test_trip_path_odkreca_przesuniecie_doby(feed):
    """gtfs.trip_path dostaje identyfikator i czasy z osi wczytanego dnia,
    a w SQLite leżą surowe 24:xx - musi przeliczyć jedno na drugie, inaczej
    nie dopasuje ani kursu, ani przystanku wsiadania."""
    day = gtfs.load_day(SUNDAY)
    nocny = gtfs.PREV_DAY_PREFIX + "nocny_sob"
    assert gtfs.db_trip(nocny) == ("nocny_sob", gtfs.PREV_DAY_SEC)
    assert gtfs.db_trip("dzienny_ndz") == ("dzienny_ndz", 0)

    # Beta 24:10 -> Delta 24:50, czyli na osi niedzieli 00:10 -> 00:50.
    rows = gtfs.trip_path(nocny, "Beta", 600, "Delta", 3000)
    assert [stop for stop, _, _ in rows] == ["Beta", "Gamma", "Delta"]
    assert [dep for _, _, dep in rows] == [600, 1800, 3000]
    assert day.trip_info[nocny][0] == "Autobus 246"


def test_etap_nocny_ma_przystanki_i_geometrie(feed):
    """Regresja: po północy etap trasy nie może wyjść pusty - bez listy
    przystanków nie ma czego narysować na mapie."""
    plan = planner.plan_route("Beta", "Delta", datetime.datetime(2026, 8, 23, 0, 5))
    przejazdy = [leg for leg in plan["legs"] if leg["kind"] == "ride"]
    assert przejazdy, "brak etapu przejazdu"
    for leg in przejazdy:
        assert leg["stops"], "etap bez przystanków - mapa zostanie pusta"
        assert len(leg["path"]) >= 2, "etap bez geometrii"
