"""Którym kursem jedzie ten pojazd - i co z tego wynika dla mapy.

Feed MPK mówi tylko „linia 146 jest tutaj". To za mało, żeby odpowiedzieć na
pytanie, które naprawdę się zadaje: czy TYM pojazdem dojadę tam, dokąd
prowadzi mapa. Nie wiadomo z tego nawet, w którą stronę on jedzie - a właśnie
dlatego warstwa pokazywała autobusy jadące w przeciwną (zgłoszone 2026-09-11).

vehicles.with_courses dokłada brakujące: kurs z rozkładu, którym pojazd
najpewniej jedzie, wraz z jego dalszymi przystankami i godzinami. Front
odsiewa po tym rozkładzie, a nie po odległości.

ODSTAWIONE 2026-09-13: warstwa wróciła do pokazywania wszystkich pojazdów
linii, które są na mapie, więc dopasowanie czeka zakomentowane w vehicles.py.
Testy zostają gotowe do odkomentowania razem z nim - opisują zachowanie, które
było zmierzone i działało, a napisanie ich od nowa kosztowałoby tyle samo, co
za pierwszym razem.
"""

import datetime
import sqlite3

import pytest

import gtfs
import update_gtfs
import vehicles

pytest.skip("dopasowanie pojazdu do kursu jest odstawione - patrz vehicles.py",
            allow_module_level=True)

DAY = datetime.date(2026, 9, 14)          # poniedziałek
NOON = datetime.datetime(2026, 9, 14, 12, 0, 0)
NOON_SEC = 12 * 3600

# Cztery przystanki w jednej linii prostej, co ~2,2 km.
STOPS = ["Alfa", "Beta", "Gamma", "Delta"]
COORDS = {name: (51.10 + i * 0.02, 17.00) for i, name in enumerate(STOPS)}

# Dwa kursy tej samej linii, jadące w przeciwne strony. Godziny tak dobrane,
# żeby o 12:00 stały w RÓŻNYCH miejscach trasy: „tam" w połowie między Betą
# a Gammą (51,13), „z powrotem" dwie trzecie drogi z Delty do Gammy (51,1467).
TRIPS = [
    ("tam", [("Alfa", NOON_SEC - 1200), ("Beta", NOON_SEC - 600),
             ("Gamma", NOON_SEC + 600), ("Delta", NOON_SEC + 1200)]),
    ("z_powrotem", [("Delta", NOON_SEC - 600), ("Gamma", NOON_SEC + 300),
                    ("Beta", NOON_SEC + 900), ("Alfa", NOON_SEC + 1500)]),
]

TAM_O_POLUDNIU = 51.130        # gdzie powinien być kurs „tam" o 12:00
Z_POWROTEM_O_POLUDNIU = 51.1467


@pytest.fixture
def feed(tmp_path, monkeypatch):
    path = tmp_path / "gtfs.sqlite"
    db = sqlite3.connect(path)
    db.executescript(update_gtfs.SCHEMA)
    db.executemany(
        "INSERT INTO stops VALUES (?,?,?,?)",
        [(s, s, *COORDS[s]) for s in STOPS],
    )
    db.execute("INSERT INTO routes VALUES ('R8','8','',3)")
    db.execute("INSERT INTO calendar VALUES "
               "('CODZIENNIE',1,1,1,1,1,1,1,'20260901','20261031')")
    for trip_id, stops in TRIPS:
        db.execute("INSERT INTO trips VALUES (?,'R8','CODZIENNIE','',NULL)",
                   (trip_id,))
        db.executemany(
            "INSERT INTO stop_times VALUES (?,?,?,?,?)",
            [(trip_id, i, stop, sec, sec) for i, (stop, sec) in enumerate(stops)],
        )
    db.commit()
    db.close()

    monkeypatch.setattr(gtfs, "DB_PATH", path)
    vehicles._trips_cache.clear()
    yield path
    vehicles._trips_cache.clear()


def _vehicle(lat, lon):
    return {"line": "8", "kind": "bus", "lat": lat, "lon": lon}


def test_a_vehicle_gets_the_course_it_is_actually_running(feed):
    """Pojazd stojący tam, gdzie o tej godzinie powinien być kurs „tam", jedzie
    właśnie nim - i to jego dalsze przystanki dostaje. Bez tego nie da się
    odróżnić „przyjedzie po mnie" od „właśnie mnie minął, jadąc w przeciwną
    stronę"."""
    [pojazd] = vehicles.with_courses([_vehicle(TAM_O_POLUDNIU, 17.00)],
                                     {"bus 8"}, NOON)
    dalej = [sec for _, _, sec in pojazd["stops"]]
    assert dalej == sorted(dalej)
    # Kurs „tam" ma przed sobą Gammę (12:10) i Deltę (12:20).
    assert dalej[-1] == NOON_SEC + 1200
    # Delta leży na północ - jedzie w tę stronę.
    assert pojazd["stops"][-1][0] == pytest.approx(COORDS["Delta"][0], abs=1e-4)


def test_the_opposite_direction_is_a_different_course(feed):
    """Ta sama linia, kawałek dalej na tej samej trasie - ale to drugi kurs,
    więc dalsze przystanki idą w przeciwną stronę. To jest cała różnica,
    której sama odległość od przystanku nie widzi."""
    [pojazd] = vehicles.with_courses([_vehicle(Z_POWROTEM_O_POLUDNIU, 17.00)],
                                     {"bus 8"}, NOON)
    assert pojazd["stops"][-1][0] == pytest.approx(COORDS["Alfa"][0], abs=1e-4)


def test_two_vehicles_never_share_one_course(feed):
    """Dwa pojazdy tej samej linii nie jadą tym samym kursem: kurs bierze ten,
    który jest bliżej, a drugi zostaje bez rozkładu, zamiast dostać cudzy.
    Bez rozdania jeden do jednego mapa mówiłaby o jednym z nich nieprawdę."""
    blisko, dalej = TAM_O_POLUDNIU, TAM_O_POLUDNIU + 0.004
    pojazdy = vehicles.with_courses(
        [_vehicle(dalej, 17.00), _vehicle(blisko, 17.00)], {"bus 8"}, NOON)
    assert [bool(p.get("stops")) for p in pojazdy] == [False, True]


def test_a_vehicle_far_from_every_course_stays_unknown(feed):
    """Pojazd, którego nie da się przypiąć do żadnego kursu (stoi kilometry od
    miejsca, w którym powinien być którykolwiek), wraca BEZ rozkładu - front
    pokazuje taki zamiast go ukrywać, bo „nie wiadomo" to nie „nie dotyczy"."""
    [pojazd] = vehicles.with_courses([_vehicle(51.50, 17.60)], {"bus 8"}, NOON)
    assert "stops" not in pojazd


def test_lines_nobody_asked_about_are_left_alone(feed):
    """Rozkład dokłada się tylko dla linii, o które pyta mapa - rozwijanie
    całego miasta kosztowałoby przy każdym odświeżeniu warstwy, a i tak nie
    miałoby czego rozstrzygać."""
    beta_lat = COORDS["Beta"][0]
    [pojazd] = vehicles.with_courses([_vehicle(beta_lat + 0.002, 17.00)],
                                     set(), NOON)
    assert "stops" not in pojazd
