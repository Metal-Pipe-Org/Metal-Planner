"""Start podróży z POKŁADU pojazdu - „jestem w 146, następny przystanek WSIADAM".

Pasażer w autobusie nie stoi w żadnym miejscu, więc nie ma czego wpisać
w „skąd". Cały tryb sprowadza się do jednego zdania (patrz onboard.py):
podróż zaczyna się na NASTĘPNYM przystanku, w chwili, gdy pojazd z niego
rusza - a wtedy „zostaję w środku" jest dla skanu zwykłym wsiadaniem w ten
sam kurs, bez czekania.

Tu pilnujemy trzech rzeczy: że rozpoznany zostaje właściwy kurs, że
wyszukiwanie faktycznie rusza spod tego przystanku i o tej sekundzie, i że
każda propozycja mówi, GDZIE WYSIĄŚĆ - bo to jedyna rzecz, którą pasażer
w pojeździe musi zrobić, a o którą zwykła lista tras nigdy nie pytała.
"""

import datetime

import onboard
import planner
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)   # dep_sec = 0, czytelne liczby


def _day():
    """Autobus 146 wiezie z POCZATEK do CELU przez WSIADAM i SRODEK; z tego
    samego WSIADAM rusza minutę później tramwaj 7, który jest w CELU o pół
    godziny wcześniej. Pasażer 146 ma więc obie sensowne opcje naraz: zostać
    w środku albo wysiąść i przesiąść się od razu."""
    return make_day([
        {"trip_id": "nasz", "label": "Autobus 146", "headsign": "CEL",
         "stops": [("POCZATEK", 0, 0), ("WSIADAM", 600, 600),
                   ("SRODEK", 1200, 1200), ("CEL", 2400, 2400)]},
        {"trip_id": "nastepny", "label": "Autobus 146", "headsign": "CEL",
         "stops": [("POCZATEK", 1800, 1800), ("WSIADAM", 2400, 2400),
                   ("SRODEK", 3000, 3000), ("CEL", 4200, 4200)]},
        {"trip_id": "szybki", "label": "Tramwaj 7", "headsign": "CEL",
         "stops": [("WSIADAM", 660, 660), ("CEL", 1500, 1500)]},
    ])


def _w_pojezdzie(stop="WSIADAM", num="146", mode="bus", headsign=None):
    return {"num": num, "mode": mode, "stop": stop, "headsign": headsign}


def _plan(install_day, pin_deadline, **kwargs):
    install_day(_day())
    pin_deadline(3000)
    return planner.plan_flow("", "CEL", WHEN, in_vehicle=_w_pojezdzie(**kwargs))


# ----------------------------------------------------------- rozpoznanie kursu


def test_rozpoznaje_najblizszy_kurs_linii():
    """Z dwóch kursów 146 tym, którym się jedzie, jest ten, który z WSIADAM
    rusza najbliżej godziny pytania - nie ten późniejszy."""
    kurs = onboard.find_ride(_day(), "146", "bus", "WSIADAM", 0)

    assert kurs["trip"] == "nasz"
    assert kurs["sec"] == 600
    assert kurs["at"] == "00:10"
    assert kurs["num"] == "146" and kurs["mode"] == "bus"
    assert kurs["headsign"] == "CEL"


def test_najblizszy_jest_kurs_przeszly_gdy_blizej():
    """Zgłoszenie #231: o 00:15 „nasz" minął WSIADAM według rozkładu pięć
    minut temu, a „nastepny" będzie tam dopiero o 00:40. Bliżej jest ten
    przeszły - najpewniej spóźniony pojazd, w którym pasażer siedzi. Mapa
    liczy się od jego rozkładu, a flaga `late` każe frontowi to powiedzieć."""
    kurs = onboard.find_ride(_day(), "146", "bus", "WSIADAM", 900)

    assert kurs["trip"] == "nasz"
    assert kurs["sec"] == 600
    assert kurs["late"] is True
    assert kurs["not_yet"] is False


def test_najblizszy_jest_kurs_przyszly_gdy_blizej():
    """O 00:27 „nasz" minął WSIADAM 17 minut temu, a „nastepny" będzie tam
    za 13 - wygrywa przyszły i nie jest spóźniony."""
    kurs = onboard.find_ride(_day(), "146", "bus", "WSIADAM", 1620)

    assert kurs["trip"] == "nastepny"
    assert kurs["sec"] == 2400
    assert kurs["late"] is False


def test_lekkie_spoznienie_nie_jest_ostrzezeniem():
    """Kurs złapany przed przystankiem jest według rozkładu zawsze trochę
    spóźniony - o tym nie ma co mówić, dopóki mieści się w zapasie."""
    w_zapasie = 600 + onboard.OFF_SCHEDULE_SLACK_SEC
    kurs = onboard.find_ride(_day(), "146", "bus", "WSIADAM", w_zapasie)
    assert kurs["trip"] == "nasz" and kurs["late"] is False
    assert onboard.find_ride(_day(), "146", "bus", "WSIADAM", w_zapasie + 1)["late"] is True


def test_pierwszy_kurs_dnia_nie_ma_przeszlego():
    """Bez kursu wcześniejszego bierzemy przyszły, choćby był daleko."""
    kurs = onboard.find_ride(_rzadki_day(), "146", "bus", "WSIADAM", 0)

    assert kurs["trip"] == "rzadki" and kurs["late"] is False


def test_petla_koncowa_tez_jest_przystankiem():
    """Na ostatnim przystanku kursu nie ma odjazdu (patrz gtfs.stop_departures),
    a mimo to „jadę na pętlę, co dalej" jest pytaniem jak każde inne - kurs
    rozpoznajemy wtedy po przyjeździe."""
    kurs = onboard.find_ride(_day(), "146", "bus", "CEL", 0)

    assert kurs["trip"] == "nasz"
    assert kurs["sec"] == 2400


def test_zla_linia_mowi_co_jest_nie_tak():
    """Linia, która przez ten przystanek nie przejeżdża, to pomyłka
    w którymkolwiek z trzech pól - i komunikat ma o tym powiedzieć, zamiast
    pokazać pustą listę."""
    blad = onboard.find_ride(_day(), "999", "bus", "WSIADAM", 0)

    assert "999" in blad["error"] and "WSIADAM" in blad["error"]


def test_kierunek_zaweza_wybor():
    """Napis z czoła pojazdu, gdy jest podany, musi się zgadzać."""
    assert "error" in onboard.find_ride(_day(), "146", "bus", "WSIADAM", 0,
                                        headsign="ZAJEZDNIA")
    assert onboard.find_ride(_day(), "146", "bus", "WSIADAM", 0,
                             headsign="cel")["trip"] == "nasz"


def _rzadki_day():
    """Jedyny 146 dnia rusza z POCZATEK o 01:00 - wcześniejszego nie ma, więc
    najbliższym jest on, choćby pytanie padło dużo wcześniej."""
    return make_day([
        {"trip_id": "rzadki", "label": "Autobus 146", "headsign": "CEL",
         "stops": [("POCZATEK", 3600, 3600), ("WSIADAM", 4200, 4200),
                   ("CEL", 6000, 6000)]},
    ])


def test_kurs_ktory_dopiero_przyjedzie_budzi_watpliwosc():
    """Zgłoszenie #231: o 00:00 najbliższy 146 będzie przy WSIADAM dopiero
    o 01:10. Kurs zostaje rozpoznany (liczymy od niego), ale z flagą, po
    której front ostrzega, że godziny na mapie liczą się od 01:10."""
    kurs = onboard.find_ride(_rzadki_day(), "146", "bus", "WSIADAM", 0)

    assert kurs["trip"] == "rzadki"
    assert kurs["not_yet"] is True


def test_zapas_jest_ten_sam_w_przod_co_wstecz():
    """„Będzie" ostrzega od tego samego zapasu co „była": liczonego od
    rozkładowej godziny przy wskazanym przystanku."""
    w_zapasie = 600 - onboard.OFF_SCHEDULE_SLACK_SEC
    kurs = onboard.find_ride(_day(), "146", "bus", "WSIADAM", w_zapasie)
    assert kurs["trip"] == "nasz" and kurs["not_yet"] is False
    assert onboard.find_ride(_day(), "146", "bus", "WSIADAM", w_zapasie - 1)["not_yet"] is True


# ------------------------------------------------------- start z pokładu


def test_wyszukiwanie_rusza_spod_nastepnego_przystanku(install_day, pin_deadline):
    """Startem jest słupek, przy którym pojazd zaraz stanie, a godziną -
    sekunda, o której z niego rusza. Nie godzina z formularza: przed nią nie
    da się zrobić niczego, bo drzwi są zamknięte."""
    wynik = _plan(install_day, pin_deadline)

    assert wynik["start"] == "WSIADAM"
    assert wynik["departure"] == "00:10"
    assert wynik["onboard"]["line"] == "Autobus 146"
    assert wynik["onboard"]["headsign"] == "CEL"
    assert wynik["onboard"]["stop_name"] == "WSIADAM"
    assert wynik["onboard"]["late"] is False


def test_spozniony_kurs_liczy_mape_od_rozkladu(install_day, pin_deadline):
    """O 00:15 najbliższy jest „nasz", który według rozkładu ruszył spod
    WSIADAM o 00:10. Mapa liczy się od tej rozkładowej sekundy - tak, jakby
    jechał o czasie - a odpowiedź mówi, że jest spóźniony."""
    install_day(_day())
    pin_deadline(3000)
    wynik = planner.plan_flow("", "CEL", WHEN.replace(minute=15),
                              in_vehicle=_w_pojezdzie())

    assert wynik["departure"] == "00:10"
    assert wynik["onboard"]["late"] is True
    assert wynik["onboard"]["at"] == "00:10"


def test_kazda_propozycja_mowi_gdzie_wysiasc(install_day, pin_deadline):
    """To jest cały sens tego trybu: pasażer w pojeździe nie pyta „czym
    jechać", tylko „gdzie wysiąść"."""
    wynik = _plan(install_day, pin_deadline)

    assert wynik["journeys"]
    for propozycja in wynik["journeys"]:
        assert propozycja["onboard"]["stop"]
        assert propozycja["onboard"]["stops"] >= 0


def test_jazda_dalej_tym_samym_pojazdem(install_day, pin_deadline):
    """Zostanie w pojeździe to dla skanu zwykłe wsiadanie w ten kurs na tym
    przystanku - a dla pasażera „siedź jeszcze dwa przystanki"."""
    wynik = _plan(install_day, pin_deadline)

    dalej = [j for j in wynik["journeys"]
             if j["legs"][0].get("num") == "146"]
    assert dalej, [j["legs"][0] for j in wynik["journeys"]]
    jazda = dalej[0]
    assert jazda["legs"][0]["onboard"] is True
    assert jazda["onboard"] == {"stop": "CEL", "stops": 2, "time": "00:40",
                                "transfer": False}
    assert jazda["transfers"] == 0


def test_przesiadka_od_razu_to_zero_przystankow(install_day, pin_deadline):
    """Propozycja, która NIE jedzie dalej naszym kursem, zaczyna się wysiadką
    na najbliższym przystanku - i tak właśnie ma być opisana, a nie milczeniem
    o tym, że trzeba wysiąść."""
    wynik = _plan(install_day, pin_deadline)

    tramwaj = [j for j in wynik["journeys"] if j["legs"][0].get("num") == "7"]
    assert tramwaj, [j["legs"][0] for j in wynik["journeys"]]
    assert tramwaj[0]["onboard"] == {"stop": "WSIADAM", "stops": 0,
                                     "time": "00:10", "transfer": True}
    assert "onboard" not in tramwaj[0]["legs"][0]
    assert tramwaj[0]["transfers"] == 1


def test_bez_pojazdu_odpowiedz_sie_nie_zmienia(install_day, pin_deadline):
    """Zwykłe wyszukiwanie ma wyglądać dokładnie tak, jak wyglądało - pole
    `onboard` pojawia się tylko wtedy, gdy ktoś o nie poprosił."""
    install_day(_day())
    pin_deadline(3000)
    wynik = planner.plan_flow("WSIADAM", "CEL", WHEN)

    assert "onboard" not in wynik
    assert all("onboard" not in j for j in wynik["journeys"])


# ------------------------------------------- mapa: siedzisz już w pojeździe


def _dzien_z_tramwajem(odjazd, przyjazd):
    """Nasz autobus 146 jak w _day(), a z WSIADAM tramwaj 7 o `odjazd`,
    w CELU o `przyjazd`."""
    return make_day([
        {"trip_id": "nasz", "label": "Autobus 146", "headsign": "CEL",
         "stops": [("POCZATEK", 0, 0), ("WSIADAM", 600, 600),
                   ("SRODEK", 1200, 1200), ("CEL", 2400, 2400)]},
        {"trip_id": "szybki", "label": "Tramwaj 7", "headsign": "CEL",
         "stops": [("WSIADAM", odjazd, odjazd), ("CEL", przyjazd, przyjazd)]},
    ])


def _linie_na_mapie(wynik):
    return {s["num"] for s in wynik["segments"]}


def test_przesiadka_na_starcie_z_pokladu_wymaga_zapasu(install_day, pin_deadline):
    """Z pokładu na przystanek się PRZYJEŻDŻA, więc tramwaj, który rusza pół
    minuty po naszym autobusie, jest przesiadką bez zapasu - mapa go nie
    rysuje. Stojąc na tym przystanku (zwykłe wyszukiwanie) zdąży się na niego
    bez problemu i tam ma zostać."""
    install_day(_dzien_z_tramwajem(630, 1500))
    pin_deadline(3000)

    z_pokladu = planner.plan_flow("", "CEL", WHEN, in_vehicle=_w_pojezdzie())
    assert _linie_na_mapie(z_pokladu) == {"146"}

    z_przystanku = planner.plan_flow("WSIADAM", "CEL", WHEN.replace(minute=10))
    assert "7" in _linie_na_mapie(z_przystanku)


def test_dalsza_jazda_wygrywa_z_przesiadka_ktora_malo_daje(install_day,
                                                            pin_deadline):
    """Wysiadka i zmiana pojazdu to przesiadka, więc musi się opłacić tak jak
    każda inna (TRANSFER_GAIN_SEC). Tramwaj szybszy o pięć minut nie wyprzedza
    więc dalszej jazdy autobusem, w którym się siedzi."""
    install_day(_dzien_z_tramwajem(700, 2100))
    pin_deadline(3000)

    wynik = planner.plan_flow("", "CEL", WHEN, in_vehicle=_w_pojezdzie())

    assert _linie_na_mapie(wynik) == {"146", "7"}
    assert wynik["journeys"][0]["legs"][0]["num"] == "146"
    assert wynik["journeys"][0]["onboard"]["transfer"] is False


def test_przesiadka_ktora_sie_oplaca_dalej_wygrywa(install_day, pin_deadline):
    """Tramwaj szybszy o pół godziny (z _day()) wyprzedza jazdę dalej mimo
    przesiadki - próg ma odsiewać drobne zyski, a nie każdą zmianę pojazdu."""
    wynik = _plan(install_day, pin_deadline)

    assert wynik["journeys"][0]["legs"][0]["num"] == "7"
