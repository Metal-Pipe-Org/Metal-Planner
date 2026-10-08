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

import pytest

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


def test_wiersz_odjazdu(install_day):
    install_day(_dzien_z_wezlem())
    wynik = planner.stop_timetable("WEZEL", WHEN, from_sec=1000)

    assert wynik["departures"][0] == {
        "time": "00:16", "sec": 1000, "line": "Autobus 100",
        "num": "100", "mode": "bus", "headsign": "PÓŁNOC",
    }
    # `sec` jest po to, żeby dało się porównać odjazd z horyzontem mapy -
    # "00:16" po północy zawija się i do porównań się nie nadaje.
    assert wynik["departures"][1]["sec"] == 1200


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


def test_do_godziny_zamiast_limitu(install_day):
    """Kropka mapy pyta do końca mapy, nie o `limit` sztuk: na ruchliwym węźle
    limit kończył się, zanim nadeszła godzina, o której pasażer tu staje."""
    install_day(_dzien_z_wezlem())
    wynik = planner.stop_timetable("WEZEL", WHEN, from_sec=0, limit=1, until_sec=54000)

    assert _numery(wynik) == ["100", "5", "300"]     # nocna 200 już za oknem


def test_kurs_przy_dwoch_slupkach_to_jeden_odjazd(install_day):
    """310 na Lutosławskiego staje przy 5700 i 5706 w tej samej minucie -
    to jeden autobus, nie dwa odjazdy."""
    install_day(make_day(
        [{"trip_id": "t310", "label": "Autobus 310", "headsign": "KOZANÓW",
          "stops": [("W1", 1000, 1000), ("W2", 1030, 1030), ("KONIEC", 1600, 1600)]}],
        names={"W1": "WEZEL", "W2": "WEZEL", "KONIEC": "KONIEC"},
    ))

    assert _godziny(planner.stop_timetable("WEZEL", WHEN, from_sec=0)) == ["00:16"]
    assert _godziny(planner.stop_timetable("WEZEL", WHEN, from_sec=0, until_sec=3600)) == ["00:16"]


# --------------------------------------- spóźnione kursy na starcie (#238) ---

def _start_ze_spoznionymi():
    """START -> CEL, pytanie o 1000. Mapa: 16 o 1300 (w celu 1700).

    Poprzednie kursy, sprzed pytania:
      - 16 o 940, w celu 1340 - spóźniona o minutę jest w celu 1400: przechodzi;
      - 4 o 990, przez SRODEK, w celu 1200 - spóźniona o 10 s: przechodzi,
        choć mapa jej nie rysuje;
      - 9 o 400, w celu 1650 - spóźniona o 10 min dopiero 2250: odpada;
      - 2 o 300, bieg skończony o 600 - nie stoi już na przystanku: odpada.
    """
    return make_day(
        [
            {"trip_id": "16-wczesniej", "label": "Tramwaj 16", "headsign": "OSOBOWICE",
             "stops": [("START", 940, 940), ("CEL", 1340, 1340)]},
            {"trip_id": "16", "label": "Tramwaj 16", "headsign": "OSOBOWICE",
             "stops": [("START", 1300, 1300), ("CEL", 1700, 1700)]},
            {"trip_id": "4", "label": "Tramwaj 4", "headsign": "BISKUPIN",
             "stops": [("START", 990, 990), ("SRODEK", 1100, 1100), ("CEL", 1200, 1200)]},
            {"trip_id": "9", "label": "Tramwaj 9", "headsign": "8 MAJA",
             "stops": [("START", 400, 400), ("SRODEK", 1050, 1050), ("CEL", 1650, 1650)]},
            {"trip_id": "2", "label": "Tramwaj 2", "headsign": "KRZYKI",
             "stops": [("START", 300, 300), ("CEL", 600, 600)]},
        ],
    )


def test_start_niesie_poprzednie_kursy_ktore_spoznione_by_dowiozly(install_day):
    """Zgłoszenie #238: tablica dostaje
    przy każdej linii jej poprzedni kurs, jeśli spóźniony o tyle, ile minęło
    od jego odjazdu, dowiózłby wcześniej niż najszybsza trasa. Mapy ani
    najszybszej trasy to nie rusza."""
    install_day(_start_ze_spoznionymi())
    wynik = planner.plan_flow("START", "CEL", WHEN + datetime.timedelta(seconds=1000))

    start = [n for n in wynik["nodes"] if n.get("start")][0]
    assert start["late"] == [
        {"num": "16", "kind": "tram", "headsign": "OSOBOWICE", "sec": 940},
        {"num": "4", "kind": "tram", "headsign": "BISKUPIN", "sec": 990},
    ]
    assert {s["num"] for s in wynik["segments"]} == {"16"}
    assert wynik["fastest"]["arrival"] == "00:28"


def _przesiadka_ze_spoznionymi():
    """START -> X (1000 -> 1300), na X przesiadka na B (1500 -> CEL 1900).

    Na X, przed przyjazdem o 1300, odjechały według rozkładu:
      - C o 1250, w celu 1600 - spóźniona do 1300 jest w celu 1650: przechodzi,
        choć mapa jej nie rysuje;
      - E o 1200, w celu 1800 - spóźniona do 1300 jest w celu 1900, na remis
        z najszybszą trasą: odpada, bo niczego nie wygrywa.
    """
    return make_day(
        [
            {"trip_id": "A", "label": "Tramwaj 1", "headsign": "X",
             "stops": [("START", 1000, 1000), ("X", 1300, 1300)]},
            {"trip_id": "B", "label": "Tramwaj 2", "headsign": "CEL",
             "stops": [("X", 1500, 1500), ("CEL", 1900, 1900)]},
            {"trip_id": "C", "label": "Tramwaj 3", "headsign": "CEL",
             "stops": [("X", 1250, 1250), ("CEL", 1600, 1600)]},
            {"trip_id": "E", "label": "Tramwaj 5", "headsign": "CEL",
             "stops": [("X", 1200, 1200), ("CEL", 1800, 1800)]},
        ],
    )


def test_przesiadka_ma_te_sama_regule_co_start(install_day):
    """Jedna reguła dla każdej tablicy mapy, nie tylko startu: na przesiadce
    poprzedni kurs liczy się sprzed chwili, w której można tu być
    (przyjazd), i musi dowieźć WCZEŚNIEJ niż najszybsza trasa - remis
    odpada."""
    install_day(_przesiadka_ze_spoznionymi())
    wynik = planner.plan_flow("START", "CEL", WHEN + datetime.timedelta(seconds=1000))

    assert wynik["fastest"]["arrival"] == "00:31"
    wezly = {n["name"]: n for n in wynik["nodes"]}
    assert wezly["X"]["late"] == [{"num": "3", "kind": "tram", "headsign": "CEL",
                                   "sec": 1250}]
    assert "late" not in wezly["START"]


def _punkt_pod_grunwaldzkim():
    """Zgłoszone na żywo 8.10: start w punkcie na mapie, kilkadziesiąt metrów
    od GRUNWALDZKI, cel WOJSZYCE. 146 jedzie o 17:51 (w celu 18:20) i o 18:07
    (w celu 18:43); na 17:51 pieszo się już nie zdąży."""
    return make_day(
        [
            {"trip_id": "146-1751", "label": "Autobus 146", "headsign": "GAJ pętla",
             "stops": [("GRUNWALDZKI", 64260, 64260), ("WOJSZYCE", 66000, 66000)]},
            {"trip_id": "146-1807", "label": "Autobus 146", "headsign": "GAJ pętla",
             "stops": [("GRUNWALDZKI", 65220, 65220), ("WOJSZYCE", 67380, 67380)]},
        ],
    )


@pytest.mark.parametrize("minuta", [51, 52])
def test_start_z_punktu_ma_tablice_startu_u_przystanku_do_ktorego_sie_idzie(
        install_day, minuta):
    """Start z punktu na mapie nie jest przystankiem - tablicą startu jest
    przystanek, do którego się z niego dochodzi. Dawniej spóźnione kursy
    dostawał tylko węzeł będący samym startem, więc tu nie dostawał ich nikt:
    o 17:51 szara 17:51 była jeszcze zwykłą szarą godziną (między formularzem
    a mapą), a o 17:52 znikała. Ma być w obu."""
    day = _punkt_pod_grunwaldzkim()
    install_day(day)
    lat, lon = day.stop_coords["GRUNWALDZKI"]
    wynik = planner.plan_flow("", "WOJSZYCE", WHEN.replace(hour=17, minute=minuta),
                              start_point=(lat + 0.0004, lon))

    assert wynik["fastest"]["arrival"] == "18:43"
    wezel = [n for n in wynik["nodes"] if n["name"] == "GRUNWALDZKI"][0]
    assert wezel["late"] == [{"num": "146", "kind": "bus", "headsign": "GAJ pętla",
                              "sec": 64260}]
