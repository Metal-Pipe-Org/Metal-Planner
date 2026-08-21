"""Próg opłacalności przesiadki (planner.TRANSFER_GAIN_SEC, suwak w panelu
deweloperskim jako `transfer_gain_sec`).

W przeciwieństwie do reguł z test_boarding_point.py to NIE jest rozstrzyganie
remisu - tu świadomie oddajemy minutę czy dwie. Nocne linie ruszają z węzła
przesiadkowego tą samą minutą i tą samą ulicą, więc różnica na końcu bywa
zaokrągleniem dwóch rozkładów, a wysiadanie z pojazdu, który sam dowozi do
celu, kosztuje przejście na inny peron i ryzyko utraty połączenia.

Próg NIE kasuje żadnej opcji: oba warianty - z przesiadką i bez - są na
liście propozycji zawsze, a suwak rozstrzyga tylko, który z nich planer
proponuje jako najlepszy (czyli który jest pierwszy i jaśniejszy na mapie).
"""

import datetime

import planner
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)   # dep_sec = 0, czytelne liczby


def _linie(wynik):
    return [leg["line"] for leg in wynik["legs"] if leg["kind"] == "ride"]


def _day(przyjazd_przesiadki=2100, cel_przesiadki="CEL", siblings=None, names=None):
    """Autobus 5 wiezie ze STARTU przez WEZEL prosto do CELU (przyjazd 2400),
    ale stoi w węźle. Autobus 7 rusza z węzła wcześniej i dowozi szybciej."""
    return make_day([
        {"trip_id": "przelot", "label": "Autobus 5", "headsign": "CEL",
         "stops": [("START", 0, 0), ("WEZEL", 1000, 1500), ("CEL", 2400, 2400)]},
        {"trip_id": "obok", "label": "Autobus 7", "headsign": "CEL",
         "stops": [("WEZEL", 1200, 1200),
                   (cel_przesiadki, przyjazd_przesiadki, przyjazd_przesiadki)]},
    ], names=names, siblings=siblings)


def test_zostajemy_w_pojezdzie_gdy_zysk_ponizej_progu(install_day):
    """Przesiadka oszczędza 5 min, próg to 10 - zostajemy w autobusie 5
    i przyjeżdżamy 5 minut później, ale bez przesiadki."""
    install_day(_day())
    wynik = planner.plan_route("START", "CEL", WHEN)      # domyślne 600 s
    assert _linie(wynik) == ["Autobus 5"]
    assert wynik["arrival"] == "00:40"                    # 2400 s, nie 2100 s


def test_przesiadka_wygrywa_gdy_zysk_powyzej_progu(install_day):
    """Ten sam rozkład, próg 3 min - 5-minutowy zysk już się opłaca."""
    install_day(_day())
    wynik = planner.plan_route("START", "CEL", WHEN, transfer_gain_sec=180)
    assert _linie(wynik) == ["Autobus 5", "Autobus 7"]
    assert wynik["arrival"] == "00:35"                    # 2100 s


def test_zero_wylacza_regule(install_day):
    """Próg 0 to dokładnie dawne zachowanie: liczy się sam przyjazd."""
    install_day(_day())
    wynik = planner.plan_route("START", "CEL", WHEN, transfer_gain_sec=0)
    assert _linie(wynik) == ["Autobus 5", "Autobus 7"]


def test_inny_peron_tego_samego_przystanku_tez_sie_liczy(install_day):
    """Na celu relacji pojazd może dojechać na INNY słupek tego samego
    miejsca - dla pasażera to ten sam przystanek, o który pytał. Tak jest
    w nocnym Wrocławiu: 241 zjeżdża na słupek 3512, a 249 na 3519."""
    install_day(_day(
        cel_przesiadki="CEL_B",
        names={"CEL": "CEL", "CEL_B": "CEL"},
        siblings={"CEL": ("CEL_B",), "CEL_B": ("CEL",)},
    ))
    wynik = planner.plan_route("START", "CEL", WHEN)
    assert _linie(wynik) == ["Autobus 5"]
    assert wynik["arrival"] == "00:40"


def test_konieczna_przesiadka_zostaje(install_day):
    """Gdy pojazd z pierwszego etapu NIE dojeżdża do celu, przesiadki nie
    da się zdjąć niezależnie od progu."""
    install_day(make_day([
        {"trip_id": "dowoz", "label": "Autobus 5", "headsign": "WEZEL",
         "stops": [("START", 0, 0), ("WEZEL", 1000, 1000)]},
        {"trip_id": "dalej", "label": "Autobus 7", "headsign": "CEL",
         "stops": [("WEZEL", 1200, 1200), ("CEL", 2100, 2100)]},
    ]))
    wynik = planner.plan_route("START", "CEL", WHEN, transfer_gain_sec=3600)
    assert _linie(wynik) == ["Autobus 5", "Autobus 7"]
    assert wynik["arrival"] == "00:35"


def _propozycje(wynik):
    return [(j["arrival"], j["transfers"],
             [l["line"] for l in j["legs"] if l["kind"] == "ride"])
            for j in wynik["journeys"]]


def test_obie_opcje_zawsze_na_liscie(install_day):
    """Niezależnie od progu widać i jazdę bez przesiadki, i tę z przesiadką."""
    install_day(_day())
    for prog in (0, 180, 600, 1800):
        wynik = planner.plan_flow("START", "CEL", when=WHEN, transfer_gain_sec=prog)
        opcje = {(arr, przes) for arr, przes, _ in _propozycje(wynik)}
        assert opcje == {("00:40", 0), ("00:35", 1)}, f"próg {prog}: {opcje}"


def test_prog_zmienia_tylko_kolejnosc(install_day):
    """Ta sama para propozycji, inna pierwsza - i tyle robi suwak."""
    install_day(_day())
    z_progiem = _propozycje(planner.plan_flow("START", "CEL", when=WHEN,
                                              transfer_gain_sec=600))
    bez_progu = _propozycje(planner.plan_flow("START", "CEL", when=WHEN,
                                              transfer_gain_sec=0))
    assert z_progiem[0][1] == 0 and z_progiem[0][0] == "00:40"   # bez przesiadki
    assert bez_progu[0][1] == 1 and bez_progu[0][0] == "00:35"   # z przesiadką
    assert sorted(z_progiem) == sorted(bez_progu)


def test_wybor_wariantu_niezalezny_od_galezi(install_day):
    """_variants to jedno miejsce, które rozstrzyga "który wariant jest
    proponowany" - i dla listy z mapy, i dla trasy z gałęzi awaryjnej."""
    import gtfs
    day = _day()
    install_day(day)
    surowa = planner._reconstruct(
        day, planner._scan(day, {"START"}, {"CEL"}, 0)[2], "CEL")
    assert planner._transfers(planner._variants(day, surowa, 600)[0]) == 0
    assert planner._transfers(planner._variants(day, surowa, 0)[0]) == 1
