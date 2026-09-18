"""Wybór skanu spośród tras JEDNAKOWO szybkich - punkt wsiadania i remisy.

CSA optymalizuje samą godzinę przyjazdu, więc gdy kilka dróg dojeżdża w tej
samej minucie, o wyniku decyduje kolejność skanowania. Tu pilnujemy, żeby
decydowała liczba przejazdów - tak samo, jak od dawna sortuje propozycje
mapy przepływów (_enumerate_journeys, klucz (arrival, len(chain) - 1, ...)).

Punkt wsiadania w kurs: skan zapisuje pierwszy przystanek kursu, na który
da się zdążyć, a rekonstrukcja musi potem wytłumaczyć, jak się tam trafiło.

Gdy pod początek trasy kursu dowozi nas inny pojazd, a ten sam kurs kilka
przystanków dalej i tak mija nasz przystanek startowy, w trasie lądował etap
"cofnij się dwa przystanki", który niczego nie dawał: ten sam autobus, ten
sam przyjazd. Reguła postępu w planner._cheaper_boarding przesuwa wsiadanie
do przodu, gdy dalej po drodze stoimy MNIEJSZĄ liczbą przejazdów.
"""

import datetime

import planner
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)   # dep_sec = 0, czytelne liczby


def _rides(result):
    return [leg for leg in result["legs"] if leg["kind"] == "ride"]


def _day_z_zawrotka():
    """Linia 9 zawija przez tę samą okolicę w obie strony.

    Kurs "wstecz" wiezie ze STARTU pod ZAJEZDNIĘ; kurs "naprzod" rusza
    z ZAJEZDNI, mija START i jedzie do CELU. Wsiadanie w "naprzod" jest
    możliwe i na ZAJEZDNI (o 21:40, po dojechaniu kursem "wstecz"), i na
    STARCIE (o 22:00, stojąc w miejscu) - przyjazd do celu ten sam.
    """
    return make_day([
        {"trip_id": "wstecz", "label": "Autobus 9", "headsign": "ZAJEZDNIA",
         "stops": [("START", 0, 1200), ("ZAJEZDNIA", 1500, 1500)]},
        {"trip_id": "naprzod", "label": "Autobus 9", "headsign": "CEL",
         "stops": [("ZAJEZDNIA", 2400, 2400), ("START", 3000, 3000),
                   ("SRODEK", 3600, 3600), ("CEL", 4200, 4200)]},
    ])


def test_bez_cofania_sie_pod_poczatek_trasy(install_day):
    """Trasa to jeden przejazd od STARTU, a nie dojazd na ZAJEZDNIĘ i powrót."""
    install_day(_day_z_zawrotka())
    wynik = planner.plan_route("START", "CEL", WHEN)

    przejazdy = _rides(wynik)
    assert len(przejazdy) == 1, [l["from"] for l in przejazdy]
    assert przejazdy[0]["from"] == "START"
    assert przejazdy[0]["from_time"] == "00:50"      # 3000 s, nie 2400 s
    assert wynik["arrival"] == "01:10"               # 4200 s - bez zmian


def test_przyjazd_sie_nie_zmienia(install_day):
    """Reguła dotyka tylko punktu wsiadania - godziny są tego samego pojazdu,
    więc najwcześniejszy przyjazd musi zostać dokładnie ten sam."""
    day = _day_z_zawrotka()
    install_day(day)
    _, best_arr, _ = planner._scan(
        day, {"START"}, {"CEL"}, 0,
    )
    assert best_arr == 4200


def test_realna_przesiadka_zostaje(install_day):
    """Gdy drugi kurs NIE mija przystanku startowego, dojazd do niego jest
    konieczny i musi zostać w trasie - reguła nie może zjadać przesiadek."""
    install_day(make_day([
        {"trip_id": "dowoz", "label": "Autobus 9", "headsign": "ZAJEZDNIA",
         "stops": [("START", 0, 1200), ("ZAJEZDNIA", 1500, 1500)]},
        {"trip_id": "dalej", "label": "Autobus 11", "headsign": "CEL",
         "stops": [("ZAJEZDNIA", 2400, 2400), ("SRODEK", 3600, 3600),
                   ("CEL", 4200, 4200)]},
    ]))
    przejazdy = _rides(planner.plan_route("START", "CEL", WHEN))
    assert [l["line"] for l in przejazdy] == ["Autobus 9", "Autobus 11"]
    assert przejazdy[0]["from"] == "START"


def test_nie_rozcina_jazdy_jednym_pojazdem(install_day):
    """Przystanek osiągnięty PRZEZ TEN kurs ma o przejazd więcej niż punkt
    wsiadania, więc nigdy nie zostanie nowym punktem wsiadania - inaczej
    jeden przejazd rozpadłby się na dwa etapy z przesiadką "sam w siebie"."""
    install_day(make_day([
        {"trip_id": "prosty", "label": "Autobus 9", "headsign": "CEL",
         "stops": [("START", 0, 600), ("SRODEK", 1200, 1200),
                   ("CEL", 1800, 1800)]},
    ]))
    przejazdy = _rides(planner.plan_route("START", "CEL", WHEN))
    assert len(przejazdy) == 1
    assert (przejazdy[0]["from"], przejazdy[0]["to"]) == ("START", "CEL")


def _day_z_dwoma_dojazdami(przyjazd_na_c):
    """Do kursu głównego (Autobus 9) prowadzą dwa dojazdy: przez B dwoma
    przejazdami, przez C jednym. Kurs mija najpierw B, potem C, więc punkt
    wsiadania zapisuje się na B - a C jest tańsze o jeden przejazd."""
    return make_day([
        {"trip_id": "d1", "label": "Autobus 11", "headsign": "A",
         "stops": [("START", 0, 0), ("A", 1000, 1000)]},
        {"trip_id": "d2", "label": "Autobus 12", "headsign": "B",
         "stops": [("A", 1200, 1200), ("B", 2000, 2000)]},
        {"trip_id": "d3", "label": "Autobus 13", "headsign": "C",
         "stops": [("START", 0, 0), ("C", przyjazd_na_c, przyjazd_na_c)]},
        {"trip_id": "glowny", "label": "Autobus 9", "headsign": "CEL",
         "stops": [("B", 2200, 2200), ("C", 2950, 2950), ("CEL", 4200, 4200)]},
    ])


def test_przesuwa_gdy_zdazymy_na_tansze_wsiadanie(install_day):
    """Na C stoimy po jednym przejeździe zamiast dwóch i zdążamy na odjazd
    (2800 + 120 s bufora <= 2950), więc wsiadanie przenosi się na C."""
    install_day(_day_z_dwoma_dojazdami(2800))
    przejazdy = _rides(planner.plan_route("START", "CEL", WHEN))
    assert [l["line"] for l in przejazdy] == ["Autobus 13", "Autobus 9"]
    assert przejazdy[-1]["from"] == "C"


def test_nie_przesuwa_gdy_nie_zdazymy(install_day):
    """Samo "mniej przejazdów" nie wystarcza - na C jesteśmy o 2900, a z
    buforem przesiadki to 3020, czyli po odjeździe o 2950. Wsiadanie zostaje
    na B, mimo że tamta droga kosztuje jeden przejazd więcej."""
    install_day(_day_z_dwoma_dojazdami(2900))
    przejazdy = _rides(planner.plan_route("START", "CEL", WHEN))
    assert [l["line"] for l in przejazdy] == ["Autobus 11", "Autobus 12", "Autobus 9"]
    assert przejazdy[-1]["from"] == "B"


def _day_z_postojem_w_wezle(przyjazd_przesiadki):
    """Autobus 5 jedzie ze STARTU przez WEZEL do CELU, ale stoi w węźle
    (przyjazd 1000, odjazd 1500) - tak jak nocne linie czekają na siebie
    w punkcie przesiadkowym. Autobus 7 rusza z węzła wcześniej (1200)."""
    return make_day([
        {"trip_id": "przelot", "label": "Autobus 5", "headsign": "CEL",
         "stops": [("START", 0, 0), ("WEZEL", 1000, 1500), ("CEL", 2000, 2000)]},
        {"trip_id": "obok", "label": "Autobus 7", "headsign": "CEL",
         "stops": [("WEZEL", 1200, 1200),
                   ("CEL", przyjazd_przesiadki, przyjazd_przesiadki)]},
    ])


def test_przy_remisie_zostajemy_w_pojezdzie(install_day):
    """Oba autobusy są u celu o 2000. Skan ogląda najpierw połączenie
    autobusu 7 (odjazd 1200 < 1500), więc bez reguły remisu wygrywałaby
    przesiadka do pojazdu, który dowozi na miejsce o tej samej minucie."""
    install_day(_day_z_postojem_w_wezle(2000))
    wynik = planner.plan_route("START", "CEL", WHEN)
    przejazdy = _rides(wynik)
    assert [l["line"] for l in przejazdy] == ["Autobus 5"]
    assert wynik["arrival"] == "00:33"           # 2000 s - bez zmian


def test_szybsza_przesiadka_dalej_wygrywa(install_day):
    """Reguła dotyczy WYŁĄCZNIE remisu - gdy przesiadka realnie skraca
    podróż, ma wygrać tak jak dotąd.

    Próg opłacalności przesiadki wyłączony (`transfer_gain_sec=0`), żeby
    testować tu jedną rzecz naraz; sam próg ma własny plik.
    """
    install_day(_day_z_postojem_w_wezle(1900))
    wynik = planner.plan_route("START", "CEL", WHEN, transfer_gain_sec=0)
    assert [l["line"] for l in _rides(wynik)] == ["Autobus 5", "Autobus 7"]
    assert wynik["arrival"] == "00:31"           # 1900 s
