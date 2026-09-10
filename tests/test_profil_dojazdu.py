"""Testy profilu dojazdu do celu (planner._target_profile) - warstwy, z której
mapa bierze ODCZYTANĄ, a nie oszacowaną odpowiedź na pytanie "wysiadam tu o tej
godzinie, o której jestem w celu".

Do 2026-08-29 liczyła to iteracja po kontynuacjach widocznych na mapie: brała
gotową wartość sąsiedniego segmentu i przesuwała ją o opóźnienie wsiadania
(`suffix[j] + shift`). Zakładała więc, że sztywny rozkład jest sprężysty -
że cały dalszy łańcuch przesunie się dokładnie o tyle samo. Nie przesuwa się:
późniejszy kurs potrafi zgubić przesiadkę i realny przyjazd skacze o kwadrans,
a nie o minutę spóźnienia. Scenariusz w _zgubiona_przesiadka_day odtwarza to
dokładnie.
"""

import datetime

import planner
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)   # dep_sec = 0, dla czytelnych liczb

# Okno musi zmieścić wariant gorszy o 20 minut - inaczej nie ma czego badać.
SZEROKIE_OKNO = dict(extra_pct=200, extra_floor_sec=0, extra_cap_sec=999999)


def _zgubiona_przesiadka_day():
    """Dwa dojazdy do węzła M, po których rozkład rozchodzi się drastycznie.

        A: S -> M o 600   -> B o 800  -> N o 1000 -> C o 1200 -> E o 1500
        X: S -> M o 900   -> B o 1100 -> N o 1300 -> C o 2400 -> E o 2700

    Obie linie B i C mają po dwa kursy, więc mapa składa je w jeden segment
    z listą odjazdów - i to jest sedno. Wsiadając w PÓŹNIEJSZY kurs B (1100
    zamiast 800, czyli 300 s później) traci się przesiadkę na C o 1200 i
    czeka na następną o 2400. Kara nie wynosi 300 s, tylko 1200 s.

    Dawne `suffix + shift` liczyło dokładnie te 300 s i podawało 1800 zamiast
    2700 - o 15 minut za wcześnie.
    """
    trips = [
        {"trip_id": "A", "label": "Autobus 1", "stops": [("S", 0, 0), ("M", 600, 600)]},
        {"trip_id": "X", "label": "Autobus 2", "stops": [("S", 0, 0), ("M", 900, 900)]},
        {"trip_id": "B1", "label": "Tramwaj 3", "stops": [("M", 800, 800), ("N", 1000, 1000)]},
        {"trip_id": "B2", "label": "Tramwaj 3", "stops": [("M", 1100, 1100), ("N", 1300, 1300)]},
        {"trip_id": "C1", "label": "Tramwaj 4", "stops": [("N", 1200, 1200), ("E", 1500, 1500)]},
        {"trip_id": "C2", "label": "Tramwaj 4", "stops": [("N", 2400, 2400), ("E", 2700, 2700)]},
    ]
    return make_day(trips)


def test_profil_czyta_realny_przyjazd_a_nie_przesuwa_gotowej_wartosci(install_day):
    day = _zgubiona_przesiadka_day()
    install_day(day)
    profile = planner._target_profile(day, {"E"}, 0, 10_000)

    # Z M o 600 zdąża się na B o 800, a z niego na C o 1200.
    assert planner._profile_value(day, profile, "M", 600) == 1500
    # Z M o 900 już nie: zostaje B o 1100, N o 1300 i dopiero C o 2400.
    # Sprężysta zgadywanka dawała tu 1800.
    assert planner._profile_value(day, profile, "M", 900) == 2700
    # Bufor przesiadki jest respektowany co do sekundy: o 980 na B o 1100
    # jeszcze się zdąży (980 + 120 == 1100), o 981 już nie - a wtedy z M nie
    # odjeżdża już nic, co dowozi do celu.
    assert planner._profile_value(day, profile, "M", 980) == 2700
    assert planner._profile_value(day, profile, "M", 981) is planner.INF


def test_profil_jest_niemalejacy_wzgledem_godziny(install_day):
    """Im później się tu stoi, tym mniej kursów zostaje - przyjazd do celu
    może więc tylko się pogorszyć albo zostać. To własność, na której opiera
    się cała reszta (i całe cięcie kursu na kawałki, punkt 3 kontraktu)."""
    day = _zgubiona_przesiadka_day()
    install_day(day)
    profile = planner._target_profile(day, {"E"}, 0, 10_000)
    poprzedni = 0
    for t in range(0, 2600, 10):
        wartosc = planner._profile_value(day, profile, "M", t)
        if wartosc is planner.INF:
            continue
        assert wartosc >= poprzedni, f"o {t} przyjazd {wartosc} < {poprzedni}"
        poprzedni = wartosc


def test_mapa_podaje_odczytana_godzine_gorszego_wariantu(install_day):
    """Ten sam scenariusz na całej mapie: gorszy dojazd do węzła ma dostać
    swoją PRAWDZIWĄ godzinę przyjazdu (2700), nie tę przesuniętą (1800)."""
    day = _zgubiona_przesiadka_day()
    install_day(day)
    wynik = planner.plan_flow("S", "E", when=WHEN, **SZEROKIE_OKNO)

    assert "error" not in wynik
    gorszy = [s for s in wynik["segments"] if s["num"] == "2"]
    assert gorszy, "wolniejszy dojazd do węzła w ogóle nie trafił na mapę"
    assert all(s["arrive"] == 2700 for s in gorszy), \
        [s.get("arrive") for s in gorszy]

    lepszy = [s for s in wynik["segments"] if s["num"] == "1"]
    assert all(s["arrive"] == 1500 for s in lepszy)
    # ...i to ma być widać w jasności, nie tylko w liczbie.
    assert max(s["w"] for s in lepszy) > max(s["w"] for s in gorszy)


def test_zadna_odczytana_wartosc_nie_pobija_optimum(install_day):
    """Wartość wyjścia znaczy "o której jestem w celu, jadąc dalej stąd", a do
    tego wyjścia dojechało się ze STARTU - więc nie ma prawa wypaść przed
    optimum policzonym przez skan CSA dla całej relacji. Wcześniej łamało to
    29% kawałków (relacja LEŚNICA -> BARTOSZOWICE, 2026-08-29): wartość poniżej
    optimum jest obcinana do q=1.0, więc kawałek z niemożliwym przyjazdem
    świecił dokładnie tak jak najszybsza trasa.

    Progu na to nie ma i mieć nie może - to niezmiennik do sprawdzania, nie
    do maskowania."""
    day = _zgubiona_przesiadka_day()
    install_day(day)
    dep_sec = 0
    _, best_arr, _ = planner._scan(day, {"S"}, {"E"}, dep_sec)
    deadline = planner._deadline(best_arr, dep_sec, **SZEROKIE_OKNO)
    earliest, arrived_by, trip_board = planner._forward(day, {"S"}, dep_sec, deadline)
    latest = planner._backward(day, {"E"}, dep_sec, deadline)
    origin_latest = max(latest[s] for s in {"S"} if s in latest)
    segs = planner._discover_segments(
        day, dep_sec, deadline, earliest, arrived_by, trip_board,
        latest, origin_latest, {"E"},
    )
    profile = planner._target_profile(day, {"E"}, dep_sec, deadline)
    planner._refine_brightness(day, segs, {"E"}, deadline, best_arr, profile)

    for seg in segs:
        for wartosc, odczytana in zip(seg["exit_vals"], seg["exit_exact"]):
            if odczytana:
                assert wartosc >= best_arr, f"{seg['label']}: {wartosc} < {best_arr}"
