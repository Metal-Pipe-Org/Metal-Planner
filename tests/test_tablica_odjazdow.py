"""Tablica odjazdów przystanku - to, co pokazuje dymek pod kropką przesiadki.

Kropki na wsiadaniu i wysiadaniu każdego etapu rysowała mapa od dawna, ale
mówiły tylko "tu się przesiadasz". Po najechaniu pokazują teraz, co z tego
przystanku odjeżdża - czyli odpowiadają na pytanie zadawane w tym miejscu
naprawdę: "a jak mi ucieknie, to co dalej?".

Dwie rzeczy, które łatwo tu zepsuć i których nie widać po wyniku na oko:

  - GODZINA JEST NA OSI DOBY ROZKŁADOWEJ, nie na zegarze. Przesiadka o 24:40
    należy do rozkładu dnia poprzedniego (patrz gtfs.load_day); zapytana
    zegarową "00:40" wypisałaby cały dzień od 00:40 rano, czyli odjazdy,
    które dawno odjechały. Dlatego front podaje sekundy wprost z etapu
    (`from_sec`), a nie sformatowaną godzinę.

  - PRZYSTANEK TO MIEJSCE, NIE SŁUPEK. Na węźle pasażer pyta o wszystko, co
    stąd odjeżdża, a nie o peron, przy którym akurat wysiadł.
"""

import datetime

import planner
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)   # doba, nie godzina - tę daje from_sec

DZIEN = 24 * 3600


def _dzien_z_wezlem():
    """Węzeł WEZEL na dwóch słupkach (W1, W2) plus kurs nocny po północy.

    Autobus 300 rusza o 15:00, a nocna 200 o 24:40 - czyli po północy, ale
    wciąż w rozkładzie TEGO dnia. To para, na której widać różnicę między
    pytaniem o sekundy doby a o godzinę z zegara.
    """
    return make_day(
        [
            {"trip_id": "t1", "label": "Autobus 100", "headsign": "PÓŁNOC",
             "stops": [("W1", 1000, 1000), ("KONIEC", 1600, 1600)]},
            {"trip_id": "t2", "label": "Tramwaj 5", "headsign": "POŁUDNIE",
             "stops": [("W2", 1200, 1200), ("KONIEC", 1800, 1800)]},
            {"trip_id": "t3", "label": "Autobus 300", "headsign": "POPOŁUDNIE",
             "stops": [("W1", 54000, 54000), ("KONIEC", 54600, 54600)]},
            {"trip_id": "t4", "label": "Autobus 200", "headsign": "NOC",
             "stops": [("W1", DZIEN + 2400, DZIEN + 2400),
                       ("KONIEC", DZIEN + 3000, DZIEN + 3000)]},
        ],
        names={"W1": "WEZEL", "W2": "WEZEL", "KONIEC": "KONIEC"},
    )


def _godziny(wynik):
    return [d["time"] for d in wynik["departures"]]


def _numery(wynik):
    return [d["num"] for d in wynik["departures"]]


def test_odjazdy_po_kolei_ze_wszystkich_slupkow_miejsca(install_day):
    """Jedna tablica na cały węzeł, posortowana - a nie osobna na peron."""
    install_day(_dzien_z_wezlem())
    wynik = planner.stop_timetable("WEZEL", WHEN, from_sec=0)

    assert wynik["stop"] == "WEZEL"
    assert _godziny(wynik) == ["00:16", "00:20", "15:00", "00:40"]
    # 00:20 to kurs ze słupka W2 - gdyby tablica pytała tylko o W1, wypadłby.
    assert _numery(wynik) == ["100", "5", "300", "200"]


def test_przesiadka_po_polnocy_nie_wypisuje_calego_dnia(install_day):
    """24:40 to koniec doby rozkładowej, nie jej początek.

    Sedno `from_sec`: o tej porze została już tylko nocna 200. Zapytanie
    zegarowe (2400 s = 00:40 rano) dokłada 15:00, które dawno odjechało.
    """
    install_day(_dzien_z_wezlem())

    po_polnocy = planner.stop_timetable("WEZEL", WHEN, from_sec=DZIEN + 2400)
    assert _numery(po_polnocy) == ["200"]
    assert po_polnocy["from_time"] == "00:40"      # tak samo wygląda na zegarze...

    zegarowo = planner.stop_timetable("WEZEL", WHEN, from_sec=2400)
    assert zegarowo["from_time"] == "00:40"        # ...a wypisuje co innego
    assert _numery(zegarowo) == ["300", "200"]


def test_za_ile_liczone_od_pytanej_godziny(install_day):
    install_day(_dzien_z_wezlem())
    wynik = planner.stop_timetable("WEZEL", WHEN, from_sec=1000)

    assert wynik["departures"][0] == {
        "time": "00:16", "sec": 1000, "in_min": 0, "line": "Autobus 100",
        "num": "100", "mode": "bus", "headsign": "PÓŁNOC",
    }
    # `sec` jest po to, żeby dało się porównać odjazd z horyzontem mapy -
    # "00:16" po północy zawija się i do porównań się nie nadaje.
    assert wynik["departures"][1]["in_min"] == 3    # 1200 s, 200 s później


def test_petla_nie_ma_odjazdow(install_day):
    """Na ostatnim przystanku kursu nie ma do czego wsiąść - i tak to mówimy,
    zamiast udawać, że przystanku nie znamy."""
    install_day(_dzien_z_wezlem())
    wynik = planner.stop_timetable("KONIEC", WHEN, from_sec=0)

    assert wynik["stop"] == "KONIEC"
    assert wynik["departures"] == []


def test_limit_bierze_najblizsze_a_nie_pierwszy_slupek(install_day):
    """Przycięcie do `limit` idzie po godzinie na całym miejscu.

    Pułapka implementacji: indeks jest per słupek, więc obcięcie przed
    scaleniem oddałoby najbliższe odjazdy JEDNEGO peronu.
    """
    install_day(_dzien_z_wezlem())
    wynik = planner.stop_timetable("WEZEL", WHEN, from_sec=0, limit=2)

    assert _numery(wynik) == ["100", "5"]           # 5 stoi na W2


def test_nieznany_przystanek(install_day):
    install_day(_dzien_z_wezlem())
    assert "error" in planner.stop_timetable("NIE MA", WHEN, from_sec=0)


def test_etap_niesie_sekundy_obu_koncow(install_day):
    """Kropka pyta o godzinę, o której trasa JEST na tym przystanku, więc
    etap musi oddać i odjazd, i przyjazd w sekundach - `from_time`/`to_time`
    same nie wystarczą, bo po północy zawijają się do 00:xx."""
    install_day(_dzien_z_wezlem())
    wynik = planner.plan_route("WEZEL", "KONIEC", WHEN)

    przejazd = [leg for leg in wynik["legs"] if leg["kind"] == "ride"][0]
    assert przejazd["dep_sec"] == 1000
    assert przejazd["arr_sec"] == 1600
