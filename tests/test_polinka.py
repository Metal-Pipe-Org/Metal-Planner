"""Polinka - kolej gondolowa nad Odrą, wpisana ręcznie (zgłoszenie #165).

Od zgłoszenia #364 Polinka nie jest kursem, tylko przeprawą: przejściem
o innym tempie, jak rower na mapie. Nie ma rozkładu - kabiny
kursują w kółko, przeprawa trwa trzy minuty w godzinach otwarcia - i nie
wchodzi do wyszukiwania. Dokłada się na gotową mapę komunikacji tą samą
regułą co rower: zostaje, gdy mapa do niej dowozi, a po drugiej stronie
rzeki zdąży się jeszcze wsiąść w coś, co mapa rysuje, albo dojść do celu.
Dlatego nigdy nie jest jedyną opcją.
"""

from datetime import date, datetime

import gtfs
import planner
import polinka
from tests.gtfs_builder import make_day

WW, NG = polinka.ID_PREFIX + "WW", polinka.ID_PREFIX + "NG"
WTOREK = date(2026, 10, 13)
_, *WW_LL = polinka.STATIONS[WW]
_, *NG_LL = polinka.STATIONS[NG]


def test_wielkanoc_i_swieta_ruchome():
    """Wielkanoc 2026 to 5 kwietnia; Boże Ciało 60 dni później."""
    assert polinka._easter(2026) == date(2026, 4, 5)
    swieta = polinka.holidays(2026)
    assert {date(2026, 4, 6), date(2026, 6, 4), date(2026, 11, 11),
            date(2026, 12, 24)} <= swieta


def test_jezdzi_tylko_w_dni_robocze():
    assert polinka.runs_on(WTOREK)
    assert not polinka.runs_on(date(2026, 10, 17))     # sobota
    assert not polinka.runs_on(date(2026, 11, 11))     # środa, święto


def test_wsiada_sie_od_razu_tylko_w_godzinach_otwarcia():
    """Rozkładu nie ma - na kabinę się nie czeka. Przed otwarciem i w przerwie
    czeka się na otwarcie; kabina, która nie zdąży przed przerwą albo przed
    18:00, nie rusza."""
    h = 3600
    assert polinka.boarding_at(6 * h) == 7 * h
    assert polinka.boarding_at(10 * h + 7 * 60) == 10 * h + 7 * 60
    assert polinka.boarding_at(13 * h + 27 * 60) == 13 * h + 27 * 60
    assert polinka.boarding_at(13 * h + 28 * 60) == 14 * h
    assert polinka.boarding_at(17 * h + 57 * 60) == 17 * h + 57 * 60
    assert polinka.boarding_at(17 * h + 58 * 60) is None


def test_stacje_zawsze_kursow_nigdy():
    """Do stacji da się dojść i szukać ich po nazwie każdego dnia. Kursów nie
    ma żadnego dnia - przeprawa nie jest kursem."""
    sobota, wtorek = gtfs.DayData(), gtfs.DayData()
    polinka.augment_day(sobota, date(2026, 10, 17))
    polinka.augment_day(wtorek, WTOREK)

    assert set(sobota.stop_names) == set(wtorek.stop_names) == {WW, NG}
    assert sobota.conns == wtorek.conns == []


# ------------------------------------------------ reguła sensu przeprawy ----

# Słupki przy stacjach: H ~250 m od Na Grobli, K ~270 m od Wybrzeża
# Wyspiańskiego - każdy dalej niż jedno dojście od drugiej stacji - i P ~370 m
# od Na Grobli (pl. Wróblewskiego).
H = (51.1050, 17.0500)
K = (51.1090, 17.0610)
P = (51.10488, 17.04834)


def _dzien_przy_stacjach():
    day = make_day([], names={})
    for stop, name, coords in (("H", "Hydropolis", H), ("K", "Politechnika", K),
                               ("P", "Pl. Wróblewskiego", P)):
        day.stop_names[stop] = name
        day.stop_coords[stop] = coords
    return day


def _przeprawy(arrive, onward, targets, dzien=WTOREK):
    return polinka.map_crossings(_dzien_przy_stacjach(), dzien, lambda: arrive,
                                 lambda: onward, targets)


def test_przeprawa_prowadzi_do_celu_pieszo():
    """Mapa dowozi pod Na Grobli, a z Wybrzeża dochodzi się do celu."""
    przeprawy = _przeprawy({"H": [(10 * 3600, 0)]}, {}, {"K"})

    przeprawa, = przeprawy
    # Kawałek jak każdy narysowany: plakietka „Polinka" w kolorze Polinki.
    assert (przeprawa["num"], przeprawa["kind"]) == ("Polinka", "cable")
    assert przeprawa["stops_n"] == [polinka.STATIONS[NG][0], polinka.STATIONS[WW][0]]
    (_, _, wsiada), (_, _, wysiada) = przeprawa["stops_t"]
    assert wysiada - wsiada == polinka.RIDE_SEC
    assert wsiada > 10 * 3600 and przeprawa["arrive"] > wysiada
    # Dymek jak przy aucie: przyjazd do celu i czas przeprawy (bez godzin samej
    # przeprawy), a pod nimi godziny otwarcia, bilet i data danych - nigdzie
    # indziej ich nie widać.
    assert przeprawa["tip"]["title"] == "Polinka"
    jazda, = przeprawa["tip"]["rides"]
    assert (jazda["from"], jazda["to"]) == ("Na Grobli", "Wybrzeże Wyspiańskiego")
    assert (jazda["sec"], jazda["arrival"]) == (polinka.RIDE_SEC, przeprawa["arrive"])
    # Jak „jesteś przy nim" przy aucie: o której przy stacji i skąd pieszo.
    assert jazda["walk_from"] == "Hydropolis"
    assert jazda["at"] == 10 * 3600 + jazda["walk_sec"] <= wsiada
    notatki = " ".join(przeprawa["tip"]["lines"])
    assert "7:00–18:00" in notatki and "13:30–14:00" in notatki
    assert "nie MPK" in notatki and f"{polinka.UPDATED:%d.%m.%Y}" in notatki


def test_przeprawa_prowadzi_do_narysowanego_odjazdu():
    """Po drugiej stronie stoi narysowany odjazd, na który się zdąży."""
    przeprawy = _przeprawy({"H": [(10 * 3600, 0)]},
                           {"K": [(10 * 3600 + 20 * 60, 11 * 3600, 1)]}, {"Z"})

    assert przeprawy[0]["arrive"] == 11 * 3600


def test_przeprawa_bez_dalszej_drogi_nie_istnieje():
    """„Zdążę na drugi brzeg" to nie „stamtąd dojadę": bez narysowanego
    odjazdu i bez celu obok przeprawy nie ma."""
    assert _przeprawy({"H": [(10 * 3600, 0)]}, {}, {"Z"}) == []
    # Odjazd, który ucieka, zanim się dojdzie, też niczego nie otwiera.
    assert _przeprawy({"H": [(10 * 3600, 0)]},
                      {"K": [(10 * 3600 + 3 * 60, 11 * 3600, 1)]}, {"Z"}) == []


def test_przeprawa_do_ktorej_mapa_nie_dowozi_nie_istnieje():
    assert _przeprawy({}, {}, {"K"}) == []


def test_w_dzien_bez_kursowania_przepraw_nie_ma():
    assert _przeprawy({"H": [(10 * 3600, 0)]}, {}, {"K"},
                      dzien=date(2026, 10, 17)) == []


def test_w_przerwie_czeka_sie_na_otwarcie():
    przeprawy = _przeprawy({"H": [(13 * 3600 + 29 * 60, 0)]}, {}, {"K"})

    assert przeprawy[0]["stops_t"][0][2] == polinka.BREAK_SEC[1]


# ----------------------------------------------- cała mapa (plan_flow) ----

PONIEDZIALEK = date(2026, 10, 19)


def _dzien_kontrolny():
    """Hydropolis -> Politechnika: autobus naokoło pół godziny, a przez
    Polinkę - kilka minut."""
    day = make_day([
        {"trip_id": "naokolo", "label": "Autobus 146", "headsign": "Politechnika",
         "stops": [("H", 10 * 3600 + 120, 10 * 3600 + 120),
                   ("K", 10 * 3600 + 1920, 10 * 3600 + 1920)]},
    ], names={"H": "Hydropolis", "K": "Politechnika"})
    day.stop_coords.update({"H": H, "K": K})
    polinka.augment_day(day, PONIEDZIALEK)
    return day


def test_polinka_jako_skrot_obok_komunikacji(install_day):
    """Przypadek kontrolny: tam, gdzie Polinka naprawdę skraca drogę, stoi na
    mapie od razu - ale obok komunikacji, nie zamiast niej."""
    install_day(_dzien_kontrolny())
    wynik = planner.plan_flow("Hydropolis", "Politechnika",
                              datetime(2026, 10, 19, 10, 0))

    assert sorted(s["num"] for s in wynik["segments"]) == ["146", "Polinka"]
    assert [r["num"] for j in wynik["journeys"] for r in j["legs"]
            if r["kind"] == "ride"] == ["146"]
    przeprawa, = [s for s in wynik["segments"] if s["num"] == "Polinka"]
    assert przeprawa["stops_n"][-1] == polinka.STATIONS[WW][0]
    assert przeprawa["arrive"] < 10 * 3600 + 1920


def test_polinka_nie_jezdzi_w_sobote(install_day):
    install_day(_dzien_kontrolny())
    wynik = planner.plan_flow("Hydropolis", "Politechnika",
                              datetime(2026, 10, 17, 10, 0))

    assert {s["num"] for s in wynik["segments"]} == {"146"}


def _dzien_ze_zrzutu():
    """Zgłoszenie #364: Wybrzeże Wyspiańskiego -> Radwanice, pn 14:35.
    Wszystko kończy się tym samym autobusem 810 z Karwińskiej. Komunikacją
    dojeżdża się do niego czwórką; przez Polinkę - piątką z pl. Wróblewskiego,
    której mapa komunikacji nie rysuje, bo pl. Wróblewskiego leży za Odrą,
    daleko od startu."""
    s, x, e = (51.1074, 17.0612), (51.0814, 17.0782), (51.0608, 17.1015)
    day = make_day([
        {"trip_id": "czworka", "label": "Tramwaj 4", "headsign": "Karwińska",
         "stops": [("S", 53040, 53040),
                   ("X", 54840, 54840)]},
        {"trip_id": "piatka", "label": "Tramwaj 5", "headsign": "Karwińska",
         "stops": [("P", 54240, 54240), ("X", 54840, 54840)]},
        {"trip_id": "810", "label": "Autobus 810+81", "headsign": "Siechnice",
         "stops": [("X", 55080, 55080), ("E", 55440, 55440)]},
    ], names={"S": "Wybrzeże Wyspiańskiego", "P": "Pl. Wróblewskiego",
              "X": "Karwińska NŻ", "E": "Radwanice - ul. Skrajna"})
    day.stop_coords.update({"S": s, "P": P, "X": x, "E": e})
    polinka.augment_day(day, PONIEDZIALEK)
    return day


def test_zrzut_z_364_polinka_nie_jest_jedyna_opcja(install_day):
    """Na zrzucie mapa pokazywała samą trasę przez Polinkę: wygrywała minutą
    później wyjścia. Teraz mapa i lista są z komunikacji, a przeprawa nie
    staje, bo po drugiej stronie nie ma w co narysowanego wsiąść."""
    install_day(_dzien_ze_zrzutu())
    wynik = planner.plan_flow("Wybrzeże Wyspiańskiego", "Radwanice - ul. Skrajna",
                              datetime(2026, 10, 19, 14, 35))

    assert {s["num"] for s in wynik["segments"]} == {"4", "810+81"}
    assert all(leg.get("mode") != "cable"
               for j in wynik["journeys"] for leg in j["legs"])
    assert wynik["journeys"]
