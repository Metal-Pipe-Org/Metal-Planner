"""Tryb „być na" (zgłoszenie #265): godzina z formularza to przyjazd do celu.

Lustro zwykłego wyszukiwania: pasek mówi o najpóźniejszym wyjściu, które
jeszcze dowozi na czas, a na mapie nie ma niczego, co dowozi później.
"""
from datetime import date, datetime

import pytest

import gtfs
from tests import flow
from tests.gtfs_builder import make_day
from tests.test_mapa_ruchy import PIATEK, SOSNOWIECKA, _real

M = 60
# Godzina, od której liczą minuty _bus: rano, z dala od północy (tę
# sprawdzają osobne testy niżej).
H = 8 * 3600


def _bus(prefix, label, rows, base=H):
    """Kursy z S do E: rows = [(minuta odjazdu, minuta przyjazdu)] od `base`."""
    return [{"trip_id": f"{prefix}{k}", "label": label,
             "stops": [("S", base + dep * M, base + dep * M),
                       ("E", base + arr * M, base + arr * M)]}
            for k, (dep, arr) in enumerate(rows)]


DAY = _bus("a", "Autobus 1", [(20, 40), (30, 50), (40, 60)])


def test_najpozniejsze_wyjscie_ktore_zdaza(install_day):
    install_day(make_day(DAY))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 8, 55), arrive_by=True)

    assert result["starts"] == "08:30"
    assert result["best_arrival"] == "08:50"
    assert result["arrive_by"] == "08:55"


def test_na_mapie_nic_nie_dowozi_po_godzinie(install_day):
    install_day(make_day(DAY + _bus("b", "Autobus 2", [(33, 58)])))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 8, 55), arrive_by=True)

    assert result["scheme"]["moves"]
    assert all(move["arrival_sec"] <= H + 55 * M for move in result["scheme"]["moves"])
    assert "2" not in {s["num"] for s in result["segments"]}


def test_bez_polaczenia_mowi_o_przyjezdzie(install_day):
    install_day(make_day(DAY))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 8, 35), arrive_by=True)

    assert result["error"] == "Nie znaleziono połączenia S → E z przyjazdem do 08:35 tego dnia."


def test_dzien_za_rozkladem_mowi_ze_rozkladu_jeszcze_nie_ma(install_day, monkeypatch):
    """Tak samo jak przy odjeździe: za ostatnim dniem paczki połączeń nie ma,
    bo rozkładu jeszcze nie opublikowano - a nie dlatego, że nic nie dowozi."""
    install_day(make_day(DAY))
    monkeypatch.setattr(gtfs, "last_service_date", lambda: date(2026, 1, 4))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 0, 35), arrive_by=True)

    assert result["error"] == "Rozkładu na 05.01 jeszcze nie opublikowano — obecny sięga do 04.01."


def test_odjazd_za_rozkladem_mowi_to_od_razu_bez_szukania(monkeypatch):
    """Odjazd tak jak przyjazd: komunikat bez przeszukiwania kolejnych pustych dób."""
    monkeypatch.setattr(gtfs, "last_service_date", lambda: date(2026, 1, 4))

    def nie_szukaj(*args, **kwargs):
        raise AssertionError("dzień za rozkładem nie powinien wczytywać rozkładu")
    monkeypatch.setattr(gtfs, "load_day", nie_szukaj)

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 0, 35))

    assert result["error"] == "Rozkładu na 05.01 jeszcze nie opublikowano — obecny sięga do 04.01."


def test_zwykle_wyszukiwanie_bez_zmian(install_day):
    install_day(make_day(DAY))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 8, 25))

    assert result["starts"] == "08:30"
    assert "arrive_by" not in result


def _teraz(monkeypatch, kiedy):
    """Dziś = dzień pytania, a teraz = `kiedy` (planner pyta zegara)."""
    import datetime as dt
    import planner

    class _Dzis(dt.date):
        @classmethod
        def today(cls):
            return kiedy.date()

    class _Teraz(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return kiedy
    monkeypatch.setattr(planner, "date", _Dzis)
    monkeypatch.setattr(planner, "datetime", _Teraz)


def test_dzis_nie_ma_wyjsc_ktore_juz_minely(install_day, monkeypatch):
    install_day(make_day(DAY + _bus("b", "Autobus 2", [(33, 58)])))
    _teraz(monkeypatch, datetime(2026, 1, 5, 8, 45))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 9, 0), arrive_by=True)

    # 8:33 odjechało ponad 10 min temu, 8:40 jeszcze zdąży
    assert [j["departure"] for j in result["journeys"]] == ["08:40"]
    assert all(move["board_sec"] >= H + 35 * M for move in result["scheme"]["moves"])


def test_dzis_gdy_nic_juz_nie_zdazy_mowi_to_wprost(install_day, monkeypatch):
    install_day(make_day(DAY))
    _teraz(monkeypatch, datetime(2026, 1, 5, 8, 52))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 9, 0), arrive_by=True)

    assert result["error"] == "Na przyjazd do 09:00 nie zdążysz już żadnym połączeniem."


def test_odjazd_z_minionej_godziny_dalej_dziala(install_day, monkeypatch):
    install_day(make_day(DAY))
    _teraz(monkeypatch, datetime(2026, 1, 5, 8, 52))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 8, 25))

    assert result["starts"] == "08:30"


# Tuż po północy wyjście wypada często poprzedniego wieczoru: kurs nocny
# z 23:50 należy do kalendarza doby, w której wyruszył (jego przyjazd to
# 24:15), a poranek nowej doby bywa zapisany w jej własnym kalendarzu
# (Siechnice: 00:05, nie 24:05). Rozkład dnia sam w sobie zaczyna się
# o północy, więc nie widzi ani jednego z tych wyjść przed nią.
WIECZOR = _bus("w", "Autobus 3", [(23 * 60 + 10, 23 * 60 + 40),
                                   (23 * 60 + 50, 24 * 60 + 15)], base=0)
PORANEK = _bus("r", "Autobus 4", [(5, 25)], base=0)
DZIEN = datetime(2026, 1, 6)


def _dwie_doby(monkeypatch):
    """Doba z pytania (6.01) ma swój poranek, poprzednia (5.01) - wieczór."""
    import gtfs
    days = {DZIEN.date(): make_day(PORANEK),
            datetime(2026, 1, 5).date(): make_day(WIECZOR)}
    monkeypatch.setattr(gtfs, "load_day", lambda d, trains=True: days[d])


def test_po_polnocy_wyjscie_poprzedniego_wieczoru(monkeypatch):
    _dwie_doby(monkeypatch)

    result = flow.plan_flow("S", "E", DZIEN.replace(minute=20), arrive_by=True)

    assert result["starts"] == "23:50"
    assert result["best_arrival"] == "00:15"
    assert result["arrive_by"] == "00:20"
    # Godziny są na osi doby poprzedniej - i odpowiedź mówi, której.
    assert result["day_offset"] == -1
    assert result["scheme"]["moves"]
    assert all(move["arrival_sec"] <= 24 * 3600 + 20 * M
               for move in result["scheme"]["moves"])


def test_po_polnocy_dowozi_tez_poranek_nowej_doby(monkeypatch):
    _dwie_doby(monkeypatch)

    result = flow.plan_flow("S", "E", DZIEN.replace(minute=30), arrive_by=True)

    assert result["starts"] == "00:05"
    assert result["best_arrival"] == "00:25"
    assert {j["departure"] for j in result["journeys"]} >= {"00:05", "23:50"}


def test_wieczorem_na_jutro_po_polnocy_nie_ma_wyjsc_ktore_juz_minely(monkeypatch):
    _dwie_doby(monkeypatch)
    _teraz(monkeypatch, datetime(2026, 1, 5, 23, 30))

    result = flow.plan_flow("S", "E", DZIEN.replace(minute=20), arrive_by=True)

    # 23:10 odjechało 20 min temu, choć pytanie jest o jutro
    assert [j["departure"] for j in result["journeys"]] == ["23:50"]


def test_po_polnocy_gdy_wieczorny_juz_odjechal_mowi_to_wprost(monkeypatch):
    _dwie_doby(monkeypatch)
    _teraz(monkeypatch, datetime(2026, 1, 6, 0, 5))

    result = flow.plan_flow("S", "E", DZIEN.replace(minute=20), arrive_by=True)

    assert result["error"] == "Na przyjazd do 00:20 nie zdążysz już żadnym połączeniem."


def _kurs(trip_id, label, stops):
    """Jeden kurs: stops = [(przystanek, minuta po 8:00)]."""
    return {"trip_id": trip_id, "label": label,
            "stops": [(stop, H + minute * M, H + minute * M) for stop, minute in stops]}


# Dwa dojścia do tej samej dziewiątki: dwójką z wyjściem o 8:10 i trójką
# o 8:05 (dalej na trasie dziewiątki, w B). Obie dowożą o 8:55.
DOJSCIA = [
    _kurs("d2", "Autobus 2", [("S", 10), ("A", 20)]),
    _kurs("d3", "Autobus 3", [("S", 5), ("B", 22)]),
    _kurs("d9", "Autobus 9", [("A", 25), ("B", 30), ("E", 55)]),
]


def test_dojscie_do_tego_samego_pojazdu_nie_kaze_wyjsc_wczesniej(install_day):
    # Lustro reguły z „wyjścia o": dojście do tego samego ostatniego pojazdu
    # nie może dowozić później niż ruch, a przy „być na" - kazać wyjść
    # wcześniej. Trójka z 8:05 to ten sam przyjazd, tylko wcześniejsze wyjście:
    # przy „być na" nie ma po co jej rysować obok dwójki z 8:10.
    install_day(make_day(DOJSCIA))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 9, 0), arrive_by=True)

    assert result["starts"] == "08:10"
    assert {s["num"] for s in result["segments"]} == {"2", "9"}


def test_tablica_przystanku_jak_przy_wyjsciu_o(install_day):
    # Tablica pod kropką pokazuje odjazdy od chwili, w której mapa stawia tu
    # pasażera (`sec`), do ostatniego, który jeszcze zdąży (`depart_by`). Przy
    # „wyjściu o" to zwykle jedna godzina na linię - przy „być na" też: mapa
    # stawia pasażera na starcie na najpóźniejsze wyjście, a nie na całą
    # zbitkę wcześniejszych kursów tej samej linii.
    install_day(make_day(_bus("c", "Autobus 1", [(k, k + 30) for k in range(0, 25, 5)])))

    odjazd = flow.plan_flow("S", "E", datetime(2026, 1, 5, 8, 0))
    przyjazd = flow.plan_flow("S", "E", datetime(2026, 1, 5, 8, 52), arrive_by=True)

    def okno(result):
        start = next(n for n in result["nodes"] if n.get("start"))
        return [(start["sec"], line["depart_by"]) for line in start["lines"]]
    assert okno(odjazd) == [(H, H)]
    assert okno(przyjazd) == [(H + 20 * M, H + 20 * M)]


def _pasek_jest_na_mapie_i_nic_nie_dowozi_po_godzinie(result, arrive_sec):
    """Pasek mówi o podróży, którą mapa rysuje (każda jego linia jest na
    mapie), a żadna propozycja nie dowozi po godzinie z formularza."""
    bar = [leg["num"] for leg in result["fastest"]["legs"] if leg["kind"] != "walk"]
    assert set(bar) <= {s.get("num") for s in result["segments"]}, bar
    late = [(j["departure"], j["arrival"]) for j in result["journeys"]
            if j["arrival_sec"] > arrive_sec]
    assert not late


# Najpóźniejsze wyjście, które jeszcze zdąża, to cztery autobusy na styk
# (3, 4, 5 i 6 z 8:05) - takiej podróży mapa nie rysuje, bo to objazd
# miasta, a nie ruch. Pasek mówi wtedy o najpóźniejszym wyjściu z mapy:
# jedynką z przesiadką na siódemkę o 8:00.
CZTERY_NA_STYK = [
    _kurs("p1", "Autobus 1", [("S", 0), ("A", 10)]),
    _kurs("p7", "Autobus 7", [("A", 15), ("E", 40)]),
    _kurs("q3", "Autobus 3", [("S", 5), ("B", 8)]),
    _kurs("q4", "Autobus 4", [("B", 10), ("C", 13)]),
    _kurs("q5", "Autobus 5", [("C", 15), ("D", 18)]),
    _kurs("q6", "Autobus 6", [("D", 20), ("E", 50)]),
]


def test_pasek_mowi_o_wyjsciu_z_mapy_a_nie_o_objezdzie_miasta(install_day):
    install_day(make_day(CZTERY_NA_STYK))

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 9, 0), arrive_by=True)

    assert result["starts"] == "08:00"
    assert result["best_arrival"] == "08:40"
    _pasek_jest_na_mapie_i_nic_nie_dowozi_po_godzinie(result, H + 60 * M)


def test_propozycja_nie_dowozi_po_godzinie_dojsciem_z_przesiadki(install_day):
    # Mapa rysuje jedynkę do A i dziewiątkę z A do celu (8:50). A leży
    # niecałe półtora kilometra od celu, więc z narysowanych kawałków da się
    # złożyć też jedynkę i dojście pieszo - tyle że to jest w celu po 9:00.
    day = make_day([
        _kurs("j1", "Autobus 1", [("S", 0), ("A", 40)]),
        _kurs("j9", "Autobus 9", [("A", 42), ("E", 50)]),
    ])
    lat, lon = day.stop_coords["E"]
    day.stop_coords["A"] = (lat - 0.0108, lon)
    install_day(day)

    result = flow.plan_flow("S", "E", datetime(2026, 1, 5, 9, 0), arrive_by=True)

    _pasek_jest_na_mapie_i_nic_nie_dowozi_po_godzinie(result, H + 60 * M)


# Na prawdziwym rozkładzie: spod Radwanic na Plac Grunwaldzki na 21:00 pasek
# mówił o czterech pojazdach na styk (87+870, 134, 143, 4), których na mapie
# nie było, a lista proponowała 114 z trójką z przyjazdem 21:14.
@pytest.mark.parametrize("start,end,day,hhmm", [
    ((51.0589, 17.0990), "PL. GRUNWALDZKI", PIATEK, "21:00"),
    (None, SOSNOWIECKA, PIATEK, "19:00"),
    ("Pl. Jana Pawła II", "Krzyki", PIATEK, "06:15"),
    ("Katedra", "Wojszyce", date(2026, 11, 10), "15:00"),
])
def test_na_prawdziwym_rozkladzie_pasek_na_mapie_i_nic_po_godzinie(start, end, day, hhmm):
    point = {"start_point": start} if isinstance(start, tuple) else {}
    result = _real("" if point else start, end, day, hhmm, arrive_by=True, **point)

    hour, minute = map(int, hhmm.split(":"))
    _pasek_jest_na_mapie_i_nic_nie_dowozi_po_godzinie(result, hour * 3600 + minute * 60)
