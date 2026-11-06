"""Mapa i lista z RUCHÓW (moves.py): opcja to „stąd wsiadam i dojeżdżam tam",
a nie linia ani odjazd.

Część pierwsza: syntetyczny dzień, deterministyczne minuty - każda heurystyka
użytkownika osobno. Część druga: prawdziwy rozkład (data/gtfs.sqlite), trzy
scenariusze z opisu - sprawdzają OBECNOŚĆ i NIEOBECNOŚĆ ruchów, nie dokładne
minuty, bo rozkład się zmienia (pomijane, gdy dnia nie ma w paczce GTFS).
"""

from datetime import date, datetime

import pytest

import gtfs
import planner
from tests.gtfs_builder import make_day

WHEN = datetime(2026, 1, 5, 0, 10, 0)     # 00:10, żeby odjazd sprzed chwili miał sens
M = 60


def _flow(start, end, when=WHEN, **kwargs):
    return planner.plan_flow(start, end, when, by_moves=True, **kwargs)


def _lines(journey):
    return [leg["num"] for leg in journey["legs"] if leg["kind"] == "ride"]


def _departures(journey):
    return [d["departure"] for d in journey["move"]["departures"]]


def _trips(label, rows, prefix):
    """Kilka kursów jednej linii: rows = [(minuta odjazdu z S, minuty jazdy)]."""
    return [{"trip_id": f"{prefix}{k}", "label": label,
             "stops": [("S", (m + 10) * M, (m + 10) * M), ("E", (m + 10 + d) * M, (m + 10 + d) * M)]}
            for k, (m, d) in enumerate(rows)]


def test_rozne_linie_tym_samym_ruchem_to_jeden_wpis(install_day):
    """Heurystyka 1: z S do E jadą trzy linie - to jeden ruch, nie trzy wpisy."""
    install_day(make_day(
        _trips("Autobus 1", [(2, 20)], "a") + _trips("Autobus 2", [(3, 20)], "b")
        + _trips("Autobus 3", [(4, 20)], "c")))

    journeys = _flow("S", "E")["journeys"]

    assert len(journeys) == 1
    assert set(d["lines"] for d in journeys[0]["move"]["departures"]) == {"1", "2", "3"}


def test_zbitka_odjazdow_do_pierwszej_duzej_przerwy_i_jeden_po_niej(install_day):
    """Heurystyka 2: odjechał minutę temu, trzy w ciągu trzech minut, następny
    za kwadrans, potem jeszcze jeden - pokazane są pierwsze cztery i ten po
    przerwie, ale nie ten dalej."""
    install_day(make_day(
        _trips("Autobus 1", [(-1, 20), (1, 20), (2, 20), (4, 20), (19, 20), (50, 20)], "a")))

    journeys = _flow("S", "E")["journeys"]

    assert len(journeys) == 1
    assert _departures(journeys[0]) == ["00:09", "00:11", "00:12", "00:14", "00:29"]
    assert journeys[0]["move"]["departures"][0]["just_left"] is True


def test_bezposredni_liczy_sie_sam_w_sobie_mimo_pozniejszego_przyjazdu(install_day):
    """Heurystyka 3: szybszy z przesiadką i wolniejszy bez - oba są w repertuarze."""
    install_day(make_day([
        {"trip_id": "t1", "label": "Tramwaj 4", "stops": [("S", 600, 600), ("B", 1200, 1200)]},
        {"trip_id": "t2", "label": "Tramwaj 5",
         "stops": [("B", 1500, 1500), ("E", 1800, 1800)]},
        {"trip_id": "t3", "label": "Autobus 151",
         "stops": [("S", 700, 700), ("X", 1500, 1500), ("E", 2300, 2300)]},
    ]))

    journeys = _flow("S", "E")["journeys"]

    assert sorted(map(tuple, map(_lines, journeys))) == [("151",), ("4", "5")]


def test_przesiadka_na_styk_podaje_zapas_i_nastepny(install_day):
    """Heurystyka 4: zapas w minutach i kiedy następny, gdyby ten uciekł."""
    install_day(make_day([
        {"trip_id": "t1", "label": "Tramwaj 4", "stops": [("S", 600, 600), ("B", 1200, 1200)]},
        {"trip_id": "t2", "label": "Autobus 8",
         "stops": [("B", 1260, 1260), ("E", 1500, 1500)]},
        {"trip_id": "t3", "label": "Autobus 8",
         "stops": [("B", 3060, 3060), ("E", 3300, 3300)]},
    ]))

    journey = _flow("S", "E")["journeys"][0]

    assert journey["move"]["slack_min"] == 1
    assert journey["move"]["tight"] is True
    assert journey["move"]["if_missed"]["after_min"] == 30


def test_te_same_pojazdy_z_innym_miejscem_przesiadki_to_jedna_podroz(install_day):
    """Tramwaj, który mija B i C, i autobus, który mija B i C: przesiadka w B
    albo w C to ta sama podróż, nie dwa ruchy."""
    install_day(make_day([
        {"trip_id": "t1", "label": "Tramwaj 5",
         "stops": [("S", 600, 600), ("B", 900, 900), ("C", 1000, 1000)]},
        {"trip_id": "t2", "label": "Autobus 125",
         "stops": [("B", 1500, 1500), ("C", 1560, 1560), ("E", 1700, 1700)]},
    ]))

    journeys = _flow("S", "E")["journeys"]

    assert len(journeys) == 1


def test_bez_trybu_ruchow_zostaje_stara_mapa(install_day):
    install_day(make_day(_trips("Autobus 1", [(2, 20)], "a")))

    result = planner.plan_flow("S", "E", WHEN)

    assert all("move" not in j for j in result["journeys"])


# --- prawdziwy rozkład -------------------------------------------------------

START = (51.1103533, 17.0586404)     # Plac Grunwaldzki 13
SOSNOWIECKA, SKRAJNA = "Sosnowiecka", "Radwanice - ul. Skrajna"


def _real(start, end, day, hhmm, **kwargs):
    if not gtfs.DB_PATH.exists():
        pytest.skip("brak data/gtfs.sqlite")
    last = gtfs.last_service_date()
    if last is None or day > last:
        pytest.skip("dnia nie ma w paczce GTFS")
    hour, minute = map(int, hhmm.split(":"))
    when = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    point = {"start_point": START} if start is None else {}
    return planner.plan_flow(start or "", end, when, by_moves=True, **point, **kwargs)


def _lines_of(result):
    return [tuple(_lines(j)) for j in result["journeys"]]


def _any_ride(result, *numbers):
    return any(set(numbers) <= set(_lines(j)) for j in result["journeys"])


@pytest.mark.parametrize("hhmm", ["19:00", "15:00", "21:30"])
def test_scenariusz_a_dojazd_z_pl_grunwaldzkiego_na_sosnowiecka(hhmm):
    """Piątek: Siechnicki z Kościuszki (tramwaj 4/16 i autobus 810+81) i
    wariant z jednym przystankiem tramwajowym mniej są w repertuarze, a z
    pętli nie wychodzi dziewięć wpisów jednego ruchu."""
    result = _real(None, SOSNOWIECKA, date(2026, 11, 6), hhmm)

    assert result["journeys"], hhmm
    assert _any_ride(result, "810+81"), hhmm
    moves_from_loop = [j for j in result["journeys"]
                       if j["legs"][-1].get("from") == "KSIĘŻE MAŁE"]
    assert len(moves_from_loop) <= 1
    for journey in result["journeys"]:
        assert len(journey["move"]["departures"]) <= 8


def test_scenariusz_a_wieczorem_jest_tez_bezposredni_i_wariant_z_przesiadka():
    result = _real(None, SOSNOWIECKA, date(2026, 11, 6), "19:00")

    assert ("151",) in _lines_of(result)
    assert any(len(lines) >= 2 for lines in _lines_of(result))
    assert len(result["journeys"]) >= 4


def test_scenariusz_a_dwie_minuty_wczesniej_nadal_sa_ruchy():
    result = _real(None, SOSNOWIECKA, date(2026, 11, 6), "18:55")

    assert len(result["journeys"]) >= 2
    assert _any_ride(result, "810+81")


def test_scenariusz_b_siechnicki_na_koscizuszki_jest_na_liscie():
    result = _real(None, SKRAJNA, date(2026, 11, 6), "19:00")

    kosciuszki = [j for j in result["journeys"]
                  if any(leg["kind"] == "ride" and leg["line"] == "Autobus 810+81"
                         and leg["from"] == "Kościuszki" for leg in j["legs"])]
    assert kosciuszki
    move = kosciuszki[0]["move"]
    assert move["slack_min"] is not None
    assert move["if_missed"] is not None


def test_scenariusz_c_z_petli_jeden_ruch_ze_zbitka_odjazdow():
    result = _real("KSIĘŻE MAŁE", SOSNOWIECKA, date(2026, 11, 6), "19:30")

    assert len(result["journeys"]) == 1
    departures = result["journeys"][0]["move"]["departures"]
    assert 3 <= len(departures) <= 8
    assert len({d["lines"] for d in departures}) >= 3
