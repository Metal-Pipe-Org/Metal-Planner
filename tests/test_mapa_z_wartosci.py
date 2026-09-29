"""Mapa z wartości podróży (zgłoszenie #150) - PRÓBA pod zębatką.

Podróż opisują minuty: o której w celu i o której trzeba wyjść (chodzenie
i przesiadki liczą się tylko w nich). Na mapie jest to, co jest w czymś
najlepsze; gęstość i „pokaż więcej" wybaczają podróże o tyle minut dłuższe.
Przesiadkę, która niczego nie daje, wycinają numery linii. Jasności nie ma.
"""

from datetime import datetime

import planner
from tests.gtfs_builder import make_day

WHEN = datetime(2026, 1, 5, 0, 0, 0)


def _numery(result):
    return sorted({seg["num"] for seg in result["segments"]})


def _borek():
    """Relacja ze zgłoszenia (Pl. Zgody -> Klecina), w pigułce: piątką na
    Borek, stamtąd siedemnastka pod sam cel. Czternastka rusza z Borku
    wcześniej i dowozi przystanek dalej, na który ta sama siedemnastka
    i tak zaraz przyjedzie."""
    return make_day([
        {"trip_id": "PIATKA", "label": "Tramwaj 5",
         "stops": [("S", 0, 0), ("B", 600, 600)]},
        {"trip_id": "SIEDEMNASTKA", "label": "Tramwaj 17",
         "stops": [("B", 900, 900), ("X", 1100, 1100), ("E", 1500, 1500)]},
        {"trip_id": "CZTERNASTKA", "label": "Tramwaj 14",
         "stops": [("B", 720, 720), ("X", 900, 900), ("Y", 1200, 1200)]},
    ])


def test_przesiadka_w_pojazd_ktory_i_tak_przyjedzie_nie_pojawia_sie_nigdy(
        install_day):
    """Czternastka daje ten sam wyjazd i ten sam przyjazd, co czekanie na
    siedemnastkę na Borku - to piątka i siedemnastka z dołożoną czternastką.
    Numery ją chowają: nie ma jej przy żadnym „więcej"."""
    install_day(_borek())

    for more in range(planner.MAX_MAP_MORE + 1):
        result = planner.plan_flow("S", "E", WHEN, value_map=True, more=more)
        assert _numery(result) == ["17", "5"], more
    # Zwykła mapa ją rysuje - to z nią jest problem, który próba rozwiązuje.
    assert "14" in _numery(planner.plan_flow("S", "E", WHEN))


def test_blizniaki_rysuja_sie_razem(install_day):
    """Dwie linie dowożą na przesiadkę o tej samej minucie: to jeden wybór
    „wsiądź w to, co przyjedzie pierwsze", a nie jedna linia zbędna."""
    install_day(make_day([
        {"trip_id": "JEDYNKA", "label": "Autobus 1",
         "stops": [("S", 0, 0), ("B", 600, 600)]},
        {"trip_id": "DWOJKA", "label": "Autobus 2",
         "stops": [("S", 0, 0), ("B", 600, 600)]},
        {"trip_id": "TROJKA", "label": "Tramwaj 3",
         "stops": [("B", 700, 700), ("E", 1300, 1300)]},
    ]))

    result = planner.plan_flow("S", "E", WHEN, value_map=True)

    assert _numery(result) == ["1", "2", "3"]


def test_nie_ma_jasnosci(install_day):
    install_day(_borek())

    result = planner.plan_flow("S", "E", WHEN, value_map=True)

    assert {seg["w"] for seg in result["segments"]} == {1.0}


def test_podglad_mowi_dlaczego_kawalek_jest_na_mapie(install_day):
    """Debug pod zębatką: podróże przez ten kawałek, każda z numerkami,
    godzinami i tolerancją, od której jest na mapie. Bez próby tego pola nie
    ma - zwykła mapa nie wybiera podróży."""
    install_day(_borek())

    z_proba = planner.plan_flow("S", "E", WHEN, value_map=True)
    bez_proby = planner.plan_flow("S", "E", WHEN)

    why = z_proba["segments"][0]["why"]
    journey = why["journeys"][0]
    assert journey["lines"] == ["5 → 17"]
    assert (journey["dep"], journey["arr"], journey["transfers"]) == (0, 1500, 1)
    assert journey["entry"] == 0
    assert why["fastest"] == 1500
    assert all("why" not in seg for seg in bez_proby["segments"])


def test_podglad_mowi_przez_co_podroz_weszla_pozniej(install_day):
    """Tolerancja to suma dwóch strat: spóźnienia w celu i wcześniejszego
    wyjścia niż podróż, która w celu nie jest później - i tę podróż podgląd
    wymienia z nazwy."""
    install_day(make_day([
        {"trip_id": "JEDYNKA", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("E", 600, 600)]},
        {"trip_id": "DWOJKA", "label": "Tramwaj 2",
         "stops": [("S", 120, 120), ("T", 300, 300), ("E", 900, 900)]},
        {"trip_id": "TROJKA", "label": "Tramwaj 3",
         "stops": [("S", 420, 420), ("E", 900, 900)]},
    ]))

    result = planner.plan_flow("S", "E", WHEN, value_map=True,
                               more=planner.MAX_MAP_MORE)

    why = next(seg["why"] for seg in result["segments"] if seg["num"] == "2")
    dwojka = why["journeys"][0]
    assert (dwojka["late"], dwojka["early"], dwojka["entry"]) == (5, 5, 10)
    assert dwojka["early_by"]["lines"] == "3"


def test_tolerancja_to_o_ile_dluzej_trwa_podroz():
    """Próg wejścia każdej podróży, w minutach. Klucz: (minuta w celu,
    minuta wyjścia, pojazdy). Przesiadki niczego nie rozstrzygają: trasa
    z dwiema przesiadkami 5 minut później wchodzi jak każda 5 minut później."""
    podroze = {
        "najszybsza": (100, 50, 2),
        "pozniej_w_celu": (105, 50, 2),
        "wczesniej_wyjscie": (100, 45, 2),
        "pozniej_wyjscie_i_w_celu": (106, 60, 2),
        "pozniej_w_celu_i_wczesniej_wyjscie": (105, 45, 2),
        "dwie_przesiadki_pozniej": (105, 50, 3),
        "te_same_minuty_przesiadka_wiecej": (100, 50, 3),
    }
    classes = [{"key": key, "name": name} for name, key in podroze.items()]

    planner._value_entries(classes, 100 * 60)

    entry = {c["name"]: c["entry"] for c in classes}
    assert entry["najszybsza"] == 0
    assert entry["pozniej_w_celu"] == 5
    assert entry["wczesniej_wyjscie"] == 5
    assert entry["pozniej_wyjscie_i_w_celu"] == 6
    # Obie straty naraz - tolerancja to ich suma: o tyle dłużej trwa podróż.
    assert entry["pozniej_w_celu_i_wczesniej_wyjscie"] == 10
    assert entry["dwie_przesiadki_pozniej"] == 5
    assert entry["te_same_minuty_przesiadka_wiecej"] == 0


def test_podglad_mowi_przez_co_czternastki_nie_ma(install_day):
    """Podgląd mówi, przez co czternastki nie ma: jedzie piątką
    i siedemnastką z dołożoną czternastką."""
    install_day(_borek())

    result = planner.plan_flow("S", "E", WHEN, value_map=True)

    assert _numery(result) == ["17", "5"]
    why = result["segments"][0]["why"]
    assert why["journeys"][0]["lines"] == ["5 → 17"]
    assert why["hidden"][0]["lines"] == "5 → 14 → 17"
    assert why["hidden"][0]["by"]["lines"] == "5 → 17"


def test_rozne_trasy_o_tej_samej_minucie_zostaja_obie(install_day):
    """Dwie całkiem różne trasy, te same godziny, jedna z przesiadką więcej:
    żadna nie jedzie częścią numerów drugiej, więc są na mapie obie."""
    install_day(make_day([
        {"trip_id": "TROJKA", "label": "Tramwaj 3",
         "stops": [("S", 0, 0), ("E", 900, 900)]},
        {"trip_id": "JEDYNKA", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("B", 300, 300)]},
        {"trip_id": "DWOJKA", "label": "Tramwaj 2",
         "stops": [("B", 400, 400), ("E", 900, 900)]},
    ]))

    result = planner.plan_flow("S", "E", WHEN, value_map=True)

    assert _numery(result) == ["1", "2", "3"]


def test_z_tych_samych_numerow_rysuje_sie_tylko_najkrotszy_wariant(install_day):
    """Ogonek z mapy Księże Małe -> Pl. Grunwaldzki: trójką za przystanek
    przesiadki i z powrotem tym samym kursem dziesiątki. Te same minuty, te
    same numery - to ta sama podróż z nadłożeniem drogi, rysuje się krótsza."""
    install_day(make_day([
        {"trip_id": "TROJKA", "label": "Tramwaj 3",
         "stops": [("S", 0, 0), ("A", 300, 300), ("W", 400, 400)]},
        {"trip_id": "DZIESIATKA", "label": "Tramwaj 10",
         "stops": [("W", 500, 500), ("A", 650, 650), ("E", 900, 900)]},
    ]))

    result = planner.plan_flow("S", "E", WHEN, value_map=True)

    times = {seg["num"]: [t for _, _, t in seg["stops_t"]]
             for seg in result["segments"]}
    assert max(times["3"]) <= 300
    assert min(times["10"]) >= 650


def test_przegrana_z_tymi_samymi_numerami_znika_mimo_tolerancji(install_day):
    """Wcześniejsza trójka na tę samą dziesiątkę: te same numery, ten sam
    przyjazd, wcześniejsze wyjście - nie ma jej przy żadnym „więcej"."""
    install_day(make_day([
        {"trip_id": "TROJKA_1", "label": "Tramwaj 3",
         "stops": [("S", 0, 0), ("A", 300, 300)]},
        {"trip_id": "TROJKA_2", "label": "Tramwaj 3",
         "stops": [("S", 400, 400), ("A", 700, 700)]},
        {"trip_id": "DZIESIATKA", "label": "Tramwaj 10",
         "stops": [("A", 800, 800), ("E", 1100, 1100)]},
    ]))

    for more in range(planner.MAX_MAP_MORE + 1):
        result = planner.plan_flow("S", "E", WHEN, value_map=True, more=more)
        times = [t for seg in result["segments"] if seg["num"] == "3"
                 for _, _, t in seg["stops_t"]]
        assert min(times) == 400, more


def test_ten_sam_kurs_zamiast_marszu(install_day):
    """Z Armii Krajowej piątka staje 3 minuty pieszo (P), a ten sam kurs
    dalej, na Krakowskiej (Q), 10 minut pieszo. Mapa rysuje ją od P, bo marsz
    do Q to marsz tam, dokąd dojechałoby się tym kursem (punkty 2 i 14);
    „Stare bliźniaki" (same_vehicle False) wracają do krótszej jazdy - od Q."""
    install_day(make_day([
        {"trip_id": "AUTOBUS", "label": "Autobus 134",
         "stops": [("S", 0, 0), ("A", 300, 300)]},
        {"trip_id": "PIATKA", "label": "Tramwaj 5",
         "stops": [("P", 600, 600), ("Q", 900, 900), ("E", 1200, 1200)]},
    ], siblings={"A": {"P": 180, "Q": 600}, "P": {"A": 180}, "Q": {"A": 600}}))

    def first_tram_stop(same_vehicle):
        result = planner.plan_flow("S", "E", WHEN, value_map=True,
                                   same_vehicle=same_vehicle)
        return min(t for seg in result["segments"] if seg["num"] == "5"
                   for _, _, t in seg["stops_t"])

    assert first_tram_stop(False) == 900
    assert first_tram_stop(True) == 600


def test_ten_sam_kurs_nie_wpuszcza_objazdu(install_day):
    """Objazd zmienia DWA przejazdy naraz (trójką za przystanek i dziesiątką
    z powrotem), żeby nie przejść między peronami A1 i A2 - reguła „tym samym
    kursem zamiast pieszo" go nie wpuszcza, zostaje krótsza jazda z przejściem."""
    install_day(make_day([
        {"trip_id": "TROJKA", "label": "Tramwaj 3",
         "stops": [("S", 0, 0), ("A1", 300, 300), ("W", 400, 400)]},
        {"trip_id": "DZIESIATKA", "label": "Tramwaj 10",
         "stops": [("W", 500, 500), ("A2", 650, 650), ("E", 900, 900)]},
    ], siblings={"A1": {"A2": 180}, "A2": {"A1": 180}}))

    result = planner.plan_flow("S", "E", WHEN, value_map=True)

    times = {seg["num"]: [t for _, _, t in seg["stops_t"]]
             for seg in result["segments"]}
    assert max(times["3"]) <= 300
    assert min(times["10"]) >= 650
