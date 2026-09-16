"""Testy aut car-sharingu NA MAPIE przepływów (punkt 15 kontraktu).

To jest coś innego niż tests/test_traficar.py: tamte pilnują propozycji
kończącej się jazdą autem, te - samego znacznika na mapie. Mapa o jeździe
autem nie mówi nic. Mówi tylko dwie rzeczy: o której da się być PRZY aucie
(dojazd z rozkładu plus dojście liczone tą samą regułą, co każde inne -
punkt 14) i ile stąd do celu w linii prostej.

Feed fioletowe.live jest tu zawsze podstawiony (patrz tests/conftest.py).
"""

import random
from datetime import date, datetime, time

import gtfs
import planner
import traficar
from tests.gtfs_builder import make_day

# Auta stoją tam, gdzie stoją TERAZ, więc mapa pokazuje je wyłącznie przy
# pytaniu o dziś - stąd dzisiejsza data zamiast stałej z pozostałych testów.
# Północ, żeby dep_sec wyszło zerem i liczby dały się czytać w głowie.
WHEN = datetime.combine(date.today(), time())

# Trzy punkty na jednym południku: 0,01° szerokości to ~1113 m.
S = (51.10, 17.00)          # start
M = (51.12, 17.00)          # przystanek w połowie
E = (51.16, 17.00)          # cel

# Auto ~33 m od M - poniżej minimalnego czasu dojścia (gtfs.WALK_MIN_SEC).
CAR = {"lat": 51.1203, "lon": 17.00, "plate": "WE1AA11", "model": "Renault Clio",
       "van": False, "where": "ul. Testowa", "fuel": 80, "range": 300, "ogarniam": []}


def _day():
    """Tramwaj S -> M -> E (1200 s) i wolny autobus S -> E (4000 s)."""
    trips = [
        {"trip_id": "SZYBKI", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("M", 600, 600), ("E", 1200, 1200)]},
        {"trip_id": "WOLNY", "label": "Autobus 2",
         "stops": [("S", 0, 0), ("E", 4000, 4000)]},
    ]
    day = make_day(trips, names={"S": "Start", "M": "Środek", "E": "Cel"})
    day.stop_coords.update({"S": S, "M": M, "E": E})
    return day


def _cars(monkeypatch, cars):
    monkeypatch.setattr(traficar, "enabled", lambda: True)
    monkeypatch.setattr(traficar, "car_list", lambda: cars)


def _flow(install_day, monkeypatch, cars=(CAR,), day=None):
    install_day(day or _day())
    _cars(monkeypatch, list(cars))
    result = planner.plan_flow("Start", "Cel", WHEN)
    assert "error" not in result
    return result


# ------------------------------------------------ co w ogóle trafia na mapę ----

def test_auto_przy_narysowanym_przystanku_trafia_na_mape(install_day, monkeypatch):
    result = _flow(install_day, monkeypatch)

    assert len(result["cars"]) == 1
    car = result["cars"][0]
    assert car["plate"] == "WE1AA11"
    assert car["from"] == "Środek"


def test_auto_dalej_niz_jedno_dojscie_nie_istnieje(install_day, monkeypatch):
    """Promień jest ten sam, co przy przejściu między przystankami - auto nie
    ma własnej, hojniejszej miary (punkt 14: jedna zasada na cały system)."""
    daleko = {**CAR, "lat": 51.13}      # ~1,1 km od M, powyżej gtfs.WALK_M
    assert _flow(install_day, monkeypatch, [daleko])["cars"] == []


def test_auto_przy_starcie_widac_od_razu(install_day, monkeypatch):
    """Do auta stojącego pod nosem idzie się bez wsiadania w cokolwiek -
    godzina przy nim to sama godzina wyjazdu plus marsz."""
    przy_starcie = {**CAR, "lat": 51.1003}

    car = _flow(install_day, monkeypatch, [przy_starcie])["cars"][0]

    assert car["from"] == "Start"
    assert car["at"] == car["walk_sec"]      # dep_sec = 0


def test_liczy_sie_to_co_mapa_RYSUJE_a_nie_cale_miasto(monkeypatch):
    """Zasięg podaje mapa (planner._drawn_reach). Przystanek, którego na niej
    nie ma, nie stawia auta na mapie, choćby leżał tuż obok niego."""
    day = _day()
    _cars(monkeypatch, [CAR])
    assert traficar.map_cars(day, {"M": 600}, E) != []
    assert traficar.map_cars(day, {"S": 0}, E) == []


# --------------------------------------------------- co auto o sobie mówi ----

def test_godzina_przy_aucie_to_dojazd_plus_dojscie(install_day, monkeypatch):
    """Marsz liczony tą samą funkcją, co wszędzie indziej - nie osobną
    prędkością dla aut."""
    dalej = {**CAR, "lat": 51.1235}          # ~390 m od M

    car = _flow(install_day, monkeypatch, [dalej])["cars"][0]

    metry = gtfs._haversine_m(*M, dalej["lat"], dalej["lon"])
    assert car["walk_sec"] == gtfs.walk_time_sec(metry)
    assert car["at"] == 600 + car["walk_sec"]


def test_najkrotsze_dojscie_wygrywa(install_day, monkeypatch):
    """Auto w zasięgu dwóch narysowanych przystanków opisuje się tym, z
    którego jest się przy nim NAJWCZEŚNIEJ."""
    day = _day()
    day.stop_coords["S"] = (51.1190, 17.00)   # start ~145 m od auta, M ~33 m

    car = _flow(install_day, monkeypatch, day=day)["cars"][0]

    # Ze startu wychodzi się o zerowej godzinie, więc mimo dłuższego dojścia
    # jest się przy aucie wcześniej niż tramwajem o 600.
    assert car["from"] == "Start"
    assert car["at"] == car["walk_sec"]


def test_do_celu_tylko_w_linii_prostej(install_day, monkeypatch):
    """Czasu jazdy autem nie ma skąd wziąć i mapa go nie zgaduje - podaje samą
    odległość, resztę zostawia pasażerowi."""
    car = _flow(install_day, monkeypatch)["cars"][0]

    assert car["to_dest_m"] == round(
        gtfs._haversine_m(CAR["lat"], CAR["lon"], *E))
    assert "drive_sec" not in car
    assert "drive_m" not in car


def test_auto_nie_jest_kursem_na_mapie(install_day, monkeypatch):
    """Znacznik auta niczego w wachlarzu nie przestawia: te same kawałki,
    te same jasności, te same węzły, co bez aut."""
    z_autem = _flow(install_day, monkeypatch)
    bez_auta = _flow(install_day, monkeypatch, cars=[])

    assert bez_auta["cars"] == []
    assert z_autem["segments"] == bez_auta["segments"]
    assert z_autem["nodes"] == bez_auta["nodes"]


# ------------------------------------------------------------- Ogarniam ----

def test_co_jest_do_wziecia_dojezdza_do_mapy(install_day, monkeypatch):
    """Program "Ogarniam": przy niektórych autach Traficar płaci za zajęcie
    się nimi. Feed to podaje, więc mapa ma to przekazać bez zmian - z kwotą
    i z tym, za co."""
    do_wziecia = {**CAR, "ogarniam": [{"co": "Sprzątanie", "ile": 30},
                                      {"co": "Tankowanie", "ile": 15}]}

    car = _flow(install_day, monkeypatch, [do_wziecia])["cars"][0]

    assert car["ogarniam"] == [{"co": "Sprzątanie", "ile": 30},
                               {"co": "Tankowanie", "ile": 15}]


def test_auto_bez_ogarniania_mowi_to_pusta_lista(install_day, monkeypatch):
    """Pusta lista, nie brak pola: "nic tu nie ma" i "nie wiadomo" to dwie
    różne odpowiedzi, a front pisze w dymku jedną z nich."""
    assert _flow(install_day, monkeypatch)["cars"][0]["ogarniam"] == []


def test_ogarniam_czytamy_z_feedu_takie_jakie_jest(monkeypatch):
    """Jedyne miejsce, w którym zamieniamy kształt źródła na własny: w feedzie
    to `discounts` z `name`/`amount` (patrz CarDiscountV1). Auto bez nagrody
    ma tam `null`, nie pustą listę."""
    feed = {"cars": [
        {"lat": "51.12", "lng": "17.00", "regPlate": "WE1AA11", "modelId": 1,
         "location": "Wrocław, ul. Testowa", "fuel": 80.0, "range": 300,
         "available": True,
         "discounts": [{"name": "Relokacja", "amount": 30}]},
        {"lat": "51.13", "lng": "17.00", "regPlate": "WE2BB22", "modelId": 1,
         "location": "Wrocław", "fuel": 50.0, "range": 200,
         "available": True, "discounts": None},
    ]}
    monkeypatch.setattr(traficar, "_fetch", lambda url: feed)
    monkeypatch.setattr(traficar, "_models", lambda: {1: ("Renault Clio", False)})
    monkeypatch.setattr(traficar, "_cars_cache",
                        {"at": 0.0, "cars": [], "generation": 0})

    z_nagroda, bez_nagrody = traficar.car_list()

    assert z_nagroda["ogarniam"] == [{"co": "Relokacja", "ile": 30}]
    assert bez_nagrody["ogarniam"] == []


# ------------------------------------------------- które auta pokazać ----

def _auto(tablica, at, skad, ogarniam=0, do_celu=3000):
    return {**CAR, "plate": tablica, "at": at, "from_place": skad,
            "to_dest_m": do_celu,
            "ogarniam": [{"co": "Tankowanie", "ile": ogarniam}] if ogarniam else []}


def _tablice(cars):
    return sorted(car["plate"] for car in cars)


def test_zawsze_widac_auta_ktorych_nic_nie_bije():
    """Każde auto najlepsze w czymś zostaje, nawet ponad suwak - wybór między
    minutami a złotówkami należy do pasażera. Auto z „Ogarniam" nie ma osobnej
    reguły: wygrywa kwotą i tyle."""
    wczesne = _auto("A", 600, "S")
    platne = _auto("C", 1800, "X", ogarniam=20)
    pobite = _auto("D", 1800, "Y")           # później niż A i bez nagrody

    pokazane = traficar.map_skyband([wczesne, platne, pobite], 1)

    assert _tablice(pokazane) == ["A", "C"]


def test_odleglosc_do_celu_nie_jest_kryterium():
    """Auto tuż przy celu nie wygrywa samym położeniem: czy jazda autem się
    opłaca, mapa nie rozstrzyga, bo czasu jazdy nie da się rzetelnie
    oszacować (korki)."""
    wczesne_daleko = _auto("A", 600, "S", do_celu=9000)
    pozne_przy_celu = _auto("B", 1200, "M", do_celu=300)

    pokazane = traficar.map_skyband([wczesne_daleko, pozne_przy_celu], 1)

    assert _tablice(pokazane) == ["A"]


def test_auta_spod_tego_samego_miejsca_to_jeden_wybor():
    """Przy włączonym grupowaniu trzy auta, do których idzie się z tego
    samego miejsca, to jeden wybór: zostaje najwcześniejsze - i to z nagrodą,
    bo w nagrodzie jest lepsze. Suwak nie dokłada pozostałych, choć miejsca
    na nie jest dosyć."""
    pierwsze = _auto("A", 600, "M")
    drugie = _auto("B", 700, "M")
    trzecie = _auto("C", 800, "M")
    z_nagroda = _auto("D", 900, "M", ogarniam=15)

    pokazane = traficar.map_skyband([pierwsze, drugie, trzecie, z_nagroda], 30,
                                    groups=True)

    assert _tablice(pokazane) == ["A", "D"]


def test_bez_grupowania_auta_obok_siebie_konkuruja_jak_kazde_inne():
    """Domyślnie grupowania nie ma: ktoś może polować na konkretny model, więc
    auto stojące obok innego wraca z suwakiem jak każde inne."""
    auta = [_auto("A", 600, "M"), _auto("B", 700, "M"), _auto("C", 800, "M")]

    assert _tablice(traficar.map_skyband(auta, 1)) == ["A"]
    assert _tablice(traficar.map_skyband(auta, 3)) == ["A", "B", "C"]


def test_ta_sama_wypisana_minuta_to_remis():
    """Porównuje się z dokładnością, z jaką mapa liczby wypisuje: ta sama
    minuta to remis, nie wygrana o sekundy."""
    o_sekundy_pozniej = _auto("A", 625, "S")
    wczesniej = _auto("B", 600, "M")         # ta sama wypisana minuta

    assert _tablice(traficar.map_skyband([o_sekundy_pozniej, wczesniej], 1)) \
        == ["A", "B"]


def test_poziom_wchodzi_w_calosci_choc_przekracza_suwak():
    """Suwak na 2: jedno auto nie jest pobite przez nic, trzy - przez dokładnie
    jedno. Wchodzą wszystkie cztery, bo wybranie jednego z trzech wymagałoby
    zważenia minut przeciw złotówkom. Auto pobite przez dwa zostaje schowane."""
    najlepsze = _auto("A", 600, "S", ogarniam=40)
    wczesne = _auto("B", 1200, "M", ogarniam=30)
    srodkowe = _auto("C", 1800, "X", ogarniam=35)
    pozne = _auto("D", 2400, "Y", ogarniam=38)
    slabsze = _auto("E", 1300, "Z", ogarniam=5)     # bite przez A i przez B

    pokazane = traficar.map_skyband(
        [najlepsze, wczesne, srodkowe, pozne, slabsze], 2)

    assert _tablice(pokazane) == ["A", "B", "C", "D"]


def test_poszerzanie_nie_dokłada_auta_z_tej_samej_grupy():
    """„Pokaż więcej" poszerza wybór MIĘDZY grupami. Kolejne auto spod tego
    samego przystanku nie wraca nigdy - inaczej już pierwsze „więcej"
    przywracałoby auta stojące obok siebie."""
    auta = [_auto(f"M{i}", 600 + 60 * i, "M") for i in range(5)]
    auta += [_auto("S", 1200, "S")]

    for suwak in range(1, 31):
        pokazane = _tablice(traficar.map_skyband(auta, suwak, groups=True))
        assert set(pokazane) <= {"M0", "S"}, suwak
    assert _tablice(traficar.map_skyband(auta, 1, groups=True)) == ["M0"]
    assert _tablice(traficar.map_skyband(auta, 30, groups=True)) == ["M0", "S"]


def test_schowane_auto_nigdy_nie_zostawia_nic_lepszego_w_ukryciu():
    """Gwarancja z punktu 15, sprawdzona na losowym mieście i każdym
    położeniu suwaka. Schowany zwycięzca grupy nie bije żadnego pokazanego
    auta. Schowane auto spoza zwycięzców może bić pokazane, ale wtedy w jego
    grupie stoi pokazane auto, które bije je samo - pasażer i tak widzi coś
    lepszego z tego samego miejsca."""
    los = random.Random(15)
    auta = [_auto(str(i), los.randrange(0, 3600), los.choice("ABCDE"),
                  los.choice([0, 0, 0, 15, 20, 30]))
            for i in range(30)]

    def bije(a, b):
        return traficar._beats(traficar._shown_as(a), traficar._shown_as(b))

    for suwak in range(1, 31):
        pokazane = traficar.map_skyband(auta, suwak, groups=True)
        schowane = [a for a in auta if a not in pokazane]
        for s in schowane:
            if any(bije(s, p) for p in pokazane):
                assert any(p["from_place"] == s["from_place"] and bije(p, s)
                           for p in pokazane), suwak
        bez_grup = traficar.map_skyband(auta, suwak)
        assert not any(bije(s, p) for s in auta if s not in bez_grup
                       for p in bez_grup), suwak


def test_suwak_i_pokaz_wiecej_mnoza_liczbe_aut(install_day, monkeypatch):
    """Auto przy starcie i auto przy M - do każdego idzie się skądinąd, więc
    to dwie grupy. Przy starcie jest się wcześniej, więc bije tamto: suwak na
    1 pokazuje jedno, „pokaż więcej" dokłada drugie."""
    install_day(_day())
    _cars(monkeypatch, [{**CAR, "plate": "PRZY_M"},
                        {**CAR, "plate": "PRZY_STARCIE", "lat": 51.1003}])

    jedno = planner.plan_flow("Start", "Cel", WHEN, car_count=1)
    dwa = planner.plan_flow("Start", "Cel", WHEN, car_count=1, more=1)

    assert _tablice(jedno["cars"]) == ["PRZY_STARCIE"]
    assert _tablice(dwa["cars"]) == ["PRZY_M", "PRZY_STARCIE"]


def test_trzy_auta_przy_jednym_przystanku_to_jedno_auto_na_mapie(install_day,
                                                               monkeypatch):
    """Trzy auta obok siebie przy M, grupowanie włączone: nawet „pokaż więcej"
    trzy razy pokazuje jedno - to, do którego dochodzi się najszybciej. Bez
    grupowania (domyślnie) wracają wszystkie trzy."""
    install_day(_day())
    _cars(monkeypatch, [{**CAR, "plate": "BLISKO"},
                        {**CAR, "plate": "DALEJ", "lat": 51.1170},
                        {**CAR, "plate": "NAJDALEJ", "lat": 51.1150}])

    wynik = planner.plan_flow("Start", "Cel", WHEN, car_count=1, more=3,
                              car_groups=True)
    domyslnie = planner.plan_flow("Start", "Cel", WHEN, car_count=1, more=3)

    assert _tablice(wynik["cars"]) == ["BLISKO"]
    assert _tablice(domyslnie["cars"]) == ["BLISKO", "DALEJ", "NAJDALEJ"]


def test_rodzaj_auta_czytamy_z_listy_modeli(monkeypatch):
    """Dostawczak to `type` 2 w liście modeli, nie zgadywanie po nazwie. Auto
    o nieznanym modelu liczy się jako osobowe."""
    feed = {"cars": [
        {"lat": "51.12", "lng": "17.00", "regPlate": plate, "modelId": model,
         "location": "Wrocław", "fuel": 80.0, "range": 300, "available": True,
         "discounts": None}
        for plate, model in (("OSOBOWE", 1), ("DOSTAWCZE", 2), ("NIEZNANE", 99))
    ]}
    monkeypatch.setattr(traficar, "_fetch", lambda url: feed)
    monkeypatch.setattr(traficar, "_models", lambda: {1: ("RENAULT Clio V", False),
                                                      2: ("RENAULT Master", True)})
    monkeypatch.setattr(traficar, "_cars_cache",
                        {"at": 0.0, "cars": [], "generation": 0})

    osobowe, dostawcze, nieznane = traficar.car_list()

    assert (osobowe["model"], osobowe["van"]) == ("RENAULT Clio V", False)
    assert (dostawcze["model"], dostawcze["van"]) == ("RENAULT Master", True)
    assert (nieznane["model"], nieznane["van"]) == ("Traficar", False)


def test_dostawczakow_domyslnie_nie_ma_wcale():
    auta = [_auto("A", 600, "S"), {**_auto("V", 300, "M"), "van": True}]

    assert _tablice(traficar.map_choice(auta, 30)) == ["A"]


def test_dostawczak_nie_konkuruje_z_osobowka():
    """Po włączeniu dostawczaki wybiera się tylko między sobą: dostawczak
    wcześniej i z nagrodą nie chowa osobówki, a osobówka nie chowa
    dostawczaka. Każdy rodzaj dostaje tę samą liczbę z suwaka."""
    osobowka = _auto("A", 900, "S")
    pobita_osobowka = _auto("B", 1200, "X")
    dostawczak = {**_auto("V", 300, "M", ogarniam=20), "van": True}
    pobity_dostawczak = {**_auto("W", 1500, "Y"), "van": True}

    pokazane = traficar.map_choice(
        [osobowka, pobita_osobowka, dostawczak, pobity_dostawczak], 1, vans=True)

    assert _tablice(pokazane) == ["A", "V"]


def test_przelacznik_dostawczakow_dochodzi_do_mapy(install_day, monkeypatch):
    install_day(_day())
    _cars(monkeypatch, [CAR, {**CAR, "plate": "MASTER", "van": True}])

    domyslnie = planner.plan_flow("Start", "Cel", WHEN)
    z_dostawczakami = planner.plan_flow("Start", "Cel", WHEN, car_vans=True)

    assert _tablice(domyslnie["cars"]) == ["WE1AA11"]
    assert _tablice(z_dostawczakami["cars"]) == ["MASTER", "WE1AA11"]


# ------------------------------------------------ kiedy aut nie ma w ogóle ----

def test_pytanie_o_inny_dzien_nie_pokazuje_aut(install_day, monkeypatch):
    """Auto stoi tam, gdzie stoi teraz - nie tam, gdzie będzie stało we
    wtorek. Godzina "będziesz przy nim" byłaby wtedy zgadywaniem podanym
    jako fakt (ta sama zasada, co przy stojakach rowerowych)."""
    install_day(_day())
    _cars(monkeypatch, [CAR])

    result = planner.plan_flow("Start", "Cel", datetime(2026, 1, 5, 0, 0, 0))

    assert result["cars"] == []


def test_wylacznik_gasi_auta_na_mapie(install_day, monkeypatch):
    install_day(_day())
    _cars(monkeypatch, [CAR])
    monkeypatch.setattr(traficar, "enabled", lambda: False)

    assert planner.plan_flow("Start", "Cel", WHEN)["cars"] == []


def test_padniety_feed_to_brak_aut_a_nie_blad(install_day, monkeypatch):
    """Traficar jest dodatkiem. Milczący feed ma zabrać znaczniki, nie
    odpowiedź na pytanie "jak tam dojadę"."""
    install_day(_day())
    monkeypatch.setattr(traficar, "enabled", lambda: True)

    def padnij():
        raise traficar.TraficarDataError("brak sieci")

    monkeypatch.setattr(traficar, "car_list", padnij)

    result = planner.plan_flow("Start", "Cel", WHEN)
    assert result["cars"] == []
    assert result["segments"]
