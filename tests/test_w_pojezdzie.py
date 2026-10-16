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
# Pytanie zadane wtedy, gdy „nasz" 146 jest według rozkładu przy WSIADAM -
# pojazd jedzie o czasie, więc liczy się wprost z rozkładu.
NA_CZAS = WHEN.replace(minute=10)


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


def _plan(install_day, **kwargs):
    install_day(_day())
    return planner.plan_flow("", "CEL", NA_CZAS, in_vehicle=_w_pojezdzie(**kwargs),
                             density=planner.MAX_MAP_DENSITY)


# ----------------------------------------------------------- rozpoznanie kursu


def test_rozpoznaje_najblizszy_kurs_linii():
    """Z dwóch kursów 146 tym, którym się jedzie, jest ten, który z WSIADAM
    rusza najbliżej godziny pytania - nie ten późniejszy."""
    kurs = onboard.find_ride(_day(), "146", "bus", "WSIADAM", 540)

    assert kurs["trip"] == "nasz"
    assert kurs["sec"] == 600
    assert kurs["at"] == "00:10"
    assert kurs["late_sec"] == 0
    assert kurs["num"] == "146" and kurs["mode"] == "bus"
    assert kurs["headsign"] == "CEL"


def test_najblizszy_jest_kurs_przeszly_gdy_blizej():
    """Zgłoszenie #231: o 00:15 „nasz" minął WSIADAM według rozkładu pięć
    minut temu, a „nastepny" będzie tam dopiero o 00:40. Bliżej jest ten
    przeszły - najpewniej spóźniony pojazd, w którym pasażer siedzi. Pasażer
    jest w nim teraz, więc kurs jedzie od teraz: pięć minut po rozkładzie."""
    kurs = onboard.find_ride(_day(), "146", "bus", "WSIADAM", 900)

    assert kurs["trip"] == "nasz"
    assert kurs["late_sec"] == 300
    assert kurs["sec"] == 900 and kurs["at"] == "00:15"
    assert kurs["at_sched"] == "00:10"


def test_najblizszy_jest_kurs_przyszly_gdy_blizej():
    """O 00:27 „nasz" minął WSIADAM 17 minut temu, a „nastepny" będzie tam
    za 13 - wygrywa przyszły, jadący 13 minut przed rozkładem."""
    kurs = onboard.find_ride(_day(), "146", "bus", "WSIADAM", 1620)

    assert kurs["trip"] == "nastepny"
    assert kurs["late_sec"] == -780
    assert kurs["sec"] == 1620 and kurs["at_sched"] == "00:40"


def test_lekkie_spoznienie_liczy_sie_z_rozkladu():
    """Kurs złapany przed przystankiem jest według rozkładu zawsze trochę
    spóźniony - dopóki mieści się w zapasie, liczymy wprost z rozkładu.
    Sekunda dalej wchodzi tryb względny."""
    w_zapasie = 600 + onboard.OFF_SCHEDULE_SLACK_SEC
    kurs = onboard.find_ride(_day(), "146", "bus", "WSIADAM", w_zapasie)
    assert kurs["trip"] == "nasz" and kurs["late_sec"] == 0 and kurs["sec"] == 600
    za_progiem = onboard.find_ride(_day(), "146", "bus", "WSIADAM", w_zapasie + 1)
    assert za_progiem["late_sec"] == w_zapasie + 1 - 600
    assert za_progiem["sec"] == w_zapasie + 1


def test_pierwszy_kurs_dnia_nie_ma_przeszlego():
    """Bez kursu wcześniejszego bierzemy przyszły, choćby był daleko."""
    kurs = onboard.find_ride(_rzadki_day(), "146", "bus", "WSIADAM", 0)

    assert kurs["trip"] == "rzadki" and kurs["late_sec"] < 0


def test_petla_koncowa_tez_jest_przystankiem():
    """Na ostatnim przystanku kursu nie ma odjazdu (patrz gtfs.stop_departures),
    a mimo to „jadę na pętlę, co dalej" jest pytaniem jak każde inne - kurs
    rozpoznajemy wtedy po przyjeździe."""
    kurs = onboard.find_ride(_day(), "146", "bus", "CEL", 2340)

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


def test_kurs_ktory_dopiero_przyjedzie_jedzie_od_teraz():
    """Zgłoszenie #231: o 00:00 najbliższy 146 będzie przy WSIADAM dopiero
    o 01:10. Pasażer jest w pojeździe teraz, więc kurs jedzie od teraz,
    a rozkładowa 01:10 zostaje w odpowiedzi - po niej front mówi, że to
    najpewniej pomyłka w linii, kierunku albo przystanku."""
    kurs = onboard.find_ride(_rzadki_day(), "146", "bus", "WSIADAM", 0)

    assert kurs["trip"] == "rzadki"
    assert kurs["late_sec"] == -4200
    assert kurs["sec"] == 0 and kurs["at"] == "00:00"
    assert kurs["at_sched"] == "01:10"


def test_zapas_jest_ten_sam_w_przod_co_wstecz():
    """Przed rozkładem tryb względny wchodzi od tego samego zapasu co po
    nim: liczonego od rozkładowej godziny przy wskazanym przystanku."""
    w_zapasie = 600 - onboard.OFF_SCHEDULE_SLACK_SEC
    kurs = onboard.find_ride(_day(), "146", "bus", "WSIADAM", w_zapasie)
    assert kurs["trip"] == "nasz" and kurs["late_sec"] == 0
    assert onboard.find_ride(_day(), "146", "bus", "WSIADAM",
                             w_zapasie - 1)["late_sec"] == -181


def test_prog_trybu_wzglednego_da_sie_zmienic():
    """Próg trybu względnego to suwak w ⚙ (sekcja „Jestem w pojeździe")."""
    assert onboard.find_ride(_day(), "146", "bus", "WSIADAM", 900,
                             slack_sec=300)["late_sec"] == 0
    assert onboard.find_ride(_day(), "146", "bus", "WSIADAM", 900,
                             slack_sec=0)["late_sec"] == 300


# ------------------------------------------------------- start z pokładu


def test_wyszukiwanie_rusza_spod_nastepnego_przystanku(install_day):
    """Startem jest słupek, przy którym pojazd zaraz stanie, a godziną -
    sekunda, o której z niego rusza. Nie godzina z formularza: przed nią nie
    da się zrobić niczego, bo drzwi są zamknięte."""
    wynik = _plan(install_day)

    assert wynik["start"] == "WSIADAM"
    assert wynik["departure"] == "00:10"
    assert wynik["onboard"]["line"] == "Autobus 146"
    assert wynik["onboard"]["headsign"] == "CEL"
    assert wynik["onboard"]["stop_name"] == "WSIADAM"
    assert wynik["onboard"]["late_sec"] == 0


def test_spozniony_kurs_jedzie_dalej_od_teraz(install_day):
    """Zgłoszenie #231: o 00:15 najbliższy jest „nasz", który według rozkładu
    ruszył spod WSIADAM o 00:10. Pasażer w nim siedzi, więc pojazd jedzie
    pięć minut po rozkładzie - aż do celu, z tymi samymi odstępami między
    przystankami. Tramwaj 7 (00:11) już odjechał i na liście go nie ma."""
    install_day(_day())
    wynik = planner.plan_flow("", "CEL", WHEN.replace(minute=15),
                              in_vehicle=_w_pojezdzie(),
                              density=planner.MAX_MAP_DENSITY)

    assert wynik["departure"] == "00:15"
    assert wynik["onboard"]["late_sec"] == 300
    assert wynik["onboard"]["at"] == "00:15"
    assert wynik["onboard"]["at_sched"] == "00:10"
    jazda = wynik["journeys"][0]
    assert jazda["legs"][0]["num"] == "146" and jazda["legs"][0]["onboard"] is True
    assert jazda["onboard"] == {"stop": "CEL", "stops": 2, "time": "00:45",
                                "transfer": False}
    assert [p["t"] for p in jazda["legs"][0]["via"]] == ["00:25"]


def test_rozkladowego_egzemplarza_naszego_kursu_juz_nie_ma(install_day):
    """Przesunięty kurs zastępuje rozkładowy: to ten sam pojazd, więc nie da
    się do niego „przesiąść o czasie" kilka przystanków dalej. Tu tramwaj 8
    dowozi z WSIADAM do SRODEK na 00:18, a rozkładowy „nasz" rusza stamtąd
    o 00:20 - ale naprawdę będzie tam dopiero o 00:30, razem z pasażerem."""
    install_day(make_day([
        {"trip_id": "nasz", "label": "Autobus 146", "headsign": "CEL",
         "stops": [("POCZATEK", 0, 0), ("WSIADAM", 600, 600),
                   ("SRODEK", 1200, 1200), ("CEL", 2400, 2400)]},
        {"trip_id": "skrot", "label": "Tramwaj 8", "headsign": "SRODEK",
         "stops": [("WSIADAM", 960, 960), ("SRODEK", 1080, 1080)]},
    ]))
    wynik = planner.plan_flow("", "CEL", WHEN.replace(minute=15),
                              in_vehicle=_w_pojezdzie(),
                              density=planner.MAX_MAP_DENSITY)

    assert all(j["arrival"] == "00:45" for j in wynik["journeys"])


def test_kazda_propozycja_mowi_gdzie_wysiasc(install_day):
    """To jest cały sens tego trybu: pasażer w pojeździe nie pyta „czym
    jechać", tylko „gdzie wysiąść"."""
    wynik = _plan(install_day)

    assert wynik["journeys"]
    for propozycja in wynik["journeys"]:
        assert propozycja["onboard"]["stop"]
        assert propozycja["onboard"]["stops"] >= 0


def test_jazda_dalej_tym_samym_pojazdem(install_day):
    """Zostanie w pojeździe to dla skanu zwykłe wsiadanie w ten kurs na tym
    przystanku - a dla pasażera „siedź jeszcze dwa przystanki"."""
    wynik = _plan(install_day)

    dalej = [j for j in wynik["journeys"]
             if j["legs"][0].get("num") == "146"]
    assert dalej, [j["legs"][0] for j in wynik["journeys"]]
    jazda = dalej[0]
    assert jazda["legs"][0]["onboard"] is True
    assert jazda["onboard"] == {"stop": "CEL", "stops": 2, "time": "00:40",
                                "transfer": False}
    assert jazda["transfers"] == 0


def test_przesiadka_od_razu_to_zero_przystankow(install_day):
    """Propozycja, która NIE jedzie dalej naszym kursem, zaczyna się wysiadką
    na najbliższym przystanku - i tak właśnie ma być opisana, a nie milczeniem
    o tym, że trzeba wysiąść."""
    wynik = _plan(install_day)

    tramwaj = [j for j in wynik["journeys"] if j["legs"][0].get("num") == "7"]
    assert tramwaj, [j["legs"][0] for j in wynik["journeys"]]
    assert tramwaj[0]["onboard"] == {"stop": "WSIADAM", "stops": 0,
                                     "time": "00:10", "transfer": True}
    assert "onboard" not in tramwaj[0]["legs"][0]
    assert tramwaj[0]["transfers"] == 1


def test_bez_pojazdu_odpowiedz_sie_nie_zmienia(install_day):
    """Zwykłe wyszukiwanie ma wyglądać dokładnie tak, jak wyglądało - pole
    `onboard` pojawia się tylko wtedy, gdy ktoś o nie poprosił."""
    install_day(_day())
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


def test_przesiadka_na_starcie_z_pokladu_wymaga_zapasu(install_day):
    """Z pokładu na przystanek się PRZYJEŻDŻA, więc tramwaj, który rusza pół
    minuty po naszym autobusie, jest przesiadką bez zapasu - mapa go nie
    rysuje. Stojąc na tym przystanku (zwykłe wyszukiwanie) zdąży się na niego
    bez problemu i tam ma zostać."""
    install_day(_dzien_z_tramwajem(630, 1500))

    z_pokladu = planner.plan_flow("", "CEL", NA_CZAS, in_vehicle=_w_pojezdzie(),
                                  density=planner.MAX_MAP_DENSITY)
    assert _linie_na_mapie(z_pokladu) == {"146"}

    z_przystanku = planner.plan_flow("WSIADAM", "CEL", WHEN.replace(minute=10))
    assert "7" in _linie_na_mapie(z_przystanku)


def test_mapa_z_pokladu_liczy_pojazd_w_ktorym_sie_siedzi(install_day):
    """Na mapie z wartości podróży przesiadka z naszego autobusu w tramwaj to
    dwa pojazdy, a dalsza jazda - jeden. Wyjście jest jedno dla wszystkich:
    pasażer już jedzie, więc późniejsze „wyjście" niczego mu nie daje."""
    install_day(_dzien_z_tramwajem(700, 2100))

    wynik = planner.plan_flow("", "CEL", NA_CZAS, in_vehicle=_w_pojezdzie(),
                              density=planner.MAX_MAP_DENSITY)

    podroze = {seg["num"]: seg["why"]["journeys"][0] for seg in wynik["segments"]}
    assert podroze["146"]["transfers"] == 0
    assert podroze["7"]["transfers"] == 1
    assert podroze["146"]["dep"] == podroze["7"]["dep"] == 600


def test_dalsza_jazda_wygrywa_z_przesiadka_ktora_malo_daje(install_day):
    """Wysiadka i zmiana pojazdu to przesiadka, więc musi się opłacić tak jak
    każda inna (TRANSFER_GAIN_SEC). Tramwaj szybszy o pięć minut nie wyprzedza
    więc dalszej jazdy autobusem, w którym się siedzi."""
    install_day(_dzien_z_tramwajem(700, 2100))

    wynik = planner.plan_flow("", "CEL", NA_CZAS, in_vehicle=_w_pojezdzie(),
                              density=planner.MAX_MAP_DENSITY)

    assert _linie_na_mapie(wynik) == {"146", "7"}
    assert wynik["journeys"][0]["legs"][0]["num"] == "146"
    assert wynik["journeys"][0]["onboard"]["transfer"] is False


def test_przesiadka_ktora_sie_oplaca_dalej_wygrywa(install_day):
    """Tramwaj szybszy o pół godziny (z _day()) wyprzedza jazdę dalej mimo
    przesiadki - próg ma odsiewać drobne zyski, a nie każdą zmianę pojazdu."""
    wynik = _plan(install_day)

    assert wynik["journeys"][0]["legs"][0]["num"] == "7"


def test_indeksy_dnia_z_przesunietym_kursem_zgadzaja_sie_z_tablica():
    """Dzień z przesuniętym kursem nie przebudowuje indeksów od zera (patrz
    gtfs._ShiftedTripIndex) - a mają mówić dokładnie to, co powiedziałby
    indeks zbudowany od nowa z jego tablicy połączeń."""
    import gtfs

    day = _day()
    for late in (300, -420):
        dzien, kurs = gtfs.with_shifted_trip(day, "nasz", "WSIADAM", late)

        od_nowa = {}
        for i, conn in enumerate(dzien.conns):
            od_nowa.setdefault(conn[4], []).append(i)
        assert dzien.dep_times == sorted(dzien.dep_times)
        assert dzien.dep_times == [c[0] for c in dzien.conns]
        assert "nasz" not in od_nowa
        for trip in {*od_nowa, "nasz"}:
            assert list(gtfs.trip_conns(dzien, trip)) == od_nowa.get(trip, []), trip

        odjazdy = {}
        for dep, _arr, skad, _dokad, trip in dzien.conns:
            odjazdy.setdefault(skad, []).append((dep, trip))
        assert {k: v for k, v in dzien.deps_by_stop.items() if v} == odjazdy
        assert [dzien.conns[i][0] for i in gtfs.trip_conns(dzien, kurs)] == [
            600 + late, 1200 + late]


def test_petla_koncowa_poza_rozkladem_nie_kasuje_kursu(install_day):
    """Ocena PR #307: „jadę na pętlę" z pojazdem ponad próg od rozkładu.
    Na pętli kurs nie ma już odjazdu, więc nie ma czego przesuwać - dzień ma
    zostać nietknięty, a nie stracić cały kurs."""
    import gtfs

    day = _day()
    przed = list(gtfs.trip_conns(day, "nasz"))
    dzien, kurs = gtfs.with_shifted_trip(day, "nasz", "CEL", 600)
    assert dzien is day and kurs == "nasz"
    assert list(gtfs.trip_conns(dzien, "nasz")) == przed

    # Cała ścieżka: o 00:50 „nasz" według rozkładu był na pętli CEL o 00:40.
    install_day(make_day([
        {"trip_id": "nasz", "label": "Autobus 146", "headsign": "CEL",
         "stops": [("POCZATEK", 0, 0), ("WSIADAM", 600, 600),
                   ("SRODEK", 1200, 1200), ("CEL", 2400, 2400)]},
        {"trip_id": "dalej", "label": "Tramwaj 7", "headsign": "DALEJ",
         "stops": [("CEL", 3300, 3300), ("DALEJ", 3900, 3900)]},
    ]))
    wynik = planner.plan_flow("", "DALEJ", WHEN.replace(minute=50),
                              in_vehicle=_w_pojezdzie(stop="CEL"),
                              density=planner.MAX_MAP_DENSITY)
    assert wynik["onboard"]["late_sec"] == 600
    assert wynik["journeys"] and wynik["journeys"][0]["arrival"] == "01:05"


# ------------------------------------------------- pociągi (zgłoszenie #167)


def _dzien_z_pociagiem():
    """Pociąg KD 64402 z A przez B do C, i autobus 644 przez B - żeby było
    widać, że numer pociągu nie łapie linii o tych samych cyfrach."""
    day = make_day([
        {"trip_id": "PKP:1:1", "label": "Pociąg KD 64402", "headsign": "C",
         "stops": [("A", 600, 600), ("B", 1200, 1260), ("C", 2400, 2400)]},
        {"trip_id": "autobus", "label": "Autobus 644", "headsign": "C",
         "stops": [("B", 1260, 1260), ("C", 3000, 3000)]},
    ])
    day.pkp_trip_stops["PKP:1:1"] = [("A", 600, 600), ("B", 1200, 1260),
                                     ("C", 2400, 2400)]
    return day


def test_pociag_rozpoznaje_sie_po_samych_cyfrach():
    """Na bilecie stoi „64402", w rozkładzie „KD 64402" - oba trafiają."""
    for numer in ("64402", "KD 64402", "kd64402"):
        kurs = onboard.find_ride(_dzien_z_pociagiem(), numer, "train", "B", 1260)
        assert kurs["trip"] == "PKP:1:1", numer
        assert kurs["mode"] == "train"
    assert "error" in onboard.find_ride(_dzien_z_pociagiem(), "6440", "train", "B", 1260)


def test_podpowiedzi_pociagow_po_poczatku_numeru():
    day = _dzien_z_pociagiem()
    assert onboard.trains(day, "6") == []          # za mało cyfr
    assert onboard.trains(day, "644") == [
        {"num": "64402", "label": "KD 64402", "headsign": "C"}]
    assert onboard.trains(day, "645") == []


def test_kierunek_i_stacje_pociagu():
    """Kierunek pociągu to jego stacja docelowa, a stacje - cały przebieg."""
    wynik = onboard._train_directions("64402", _dzien_z_pociagiem())
    assert wynik["mode"] == "train" and wynik["num"] == "64402"
    [kierunek] = wynik["directions"]
    assert kierunek["headsign"] == "C"
    assert [s["id"] for s in kierunek["stops"]] == ["A", "B", "C"]
    assert "error" in onboard._train_directions("11111", _dzien_z_pociagiem())


def test_brak_bazy_pociagow_nie_wypuszcza_tresci_wyjatku(monkeypatch):
    """Bez data/gtfs.sqlite podpowiedzi i kierunki pociągu mówią, że nie ma
    danych - ale bez treści wyjątku (ścieżki pliku), bo ta idzie do klienta."""
    import gtfs
    from app import app

    def bez_bazy(day):
        raise FileNotFoundError("/sekretna/sciezka/gtfs.sqlite")

    monkeypatch.setattr(gtfs, "load_day", bez_bazy)
    client = app.test_client()

    podpowiedzi = client.get("/api/onboard/trains?q=644")
    assert podpowiedzi.status_code == 503
    kierunki = client.get("/api/onboard?num=64402&mode=train")
    assert "error" in kierunki.get_json()
    for odpowiedz in (podpowiedzi, kierunki):
        assert "sekretna" not in odpowiedz.get_data(as_text=True)
