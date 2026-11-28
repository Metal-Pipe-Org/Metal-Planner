"""Podróże z wartości (zgłoszenie #150) - wyszukiwanie, z którego wybiera
mapa z ruchów (planner._moves_map).

Podróż opisują minuty: o której w celu i o której trzeba wyjść (chodzenie
i przesiadki liczą się tylko w nich). Jasności nie ma.
"""

from datetime import datetime

import planner
from tests.gtfs_builder import make_day
from tests import flow

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

    result = flow.plan_flow("S", "E", WHEN)

    assert _numery(result) == ["1", "2", "3"]


def test_nie_ma_jasnosci(install_day):
    install_day(_borek())

    result = flow.plan_flow("S", "E", WHEN)

    assert {seg["w"] for seg in result["segments"]} == {1.0}


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


def test_ten_sam_kurs_zamiast_marszu(install_day):
    """Z Armii Krajowej piątka staje 3 minuty pieszo (P), a ten sam kurs
    dalej, na Krakowskiej (Q), 10 minut pieszo. Mapa rysuje ją od P, bo marsz
    do Q to marsz tam, dokąd dojechałoby się tym kursem (punkty 2 i 14)."""
    install_day(make_day([
        {"trip_id": "AUTOBUS", "label": "Autobus 134",
         "stops": [("S", 0, 0), ("A", 300, 300)]},
        {"trip_id": "PIATKA", "label": "Tramwaj 5",
         "stops": [("P", 600, 600), ("Q", 900, 900), ("E", 1200, 1200)]},
    ], siblings={"A": {"P": 180, "Q": 600}, "P": {"A": 180}, "Q": {"A": 600}}))

    result = flow.plan_flow("S", "E", WHEN)
    assert min(t for seg in result["segments"] if seg["num"] == "5"
               for _, _, t in seg["stops_t"]) == 600

