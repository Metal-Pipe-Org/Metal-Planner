"""Wybór z RUCHÓW (moves.py) - tylko pod „Nową mapą" (eksperyment, new_map).

Opcja to „stąd wsiadam i dojeżdżam tam", a nie linia ani odjazd: zbitka linii
jednego ruchu to jedna nitka na mapie, a pierwszy widok to kilka różnych,
czytelnych ruchów (moves.READABLE_MOVES). Co się nie mieści, nie jest rysowane
- dostęp zostaje przez kliknięcie w przystanek (istniejąca tablica odjazdów).
Stara mapa zostaje taka, jaka była.

Część pierwsza: syntetyczny dzień, deterministyczne minuty. Część druga:
prawdziwy rozkład (data/gtfs.sqlite) - scenariusze A/B/C z opisu użytkownika;
sprawdzają obecność i nieobecność, nie dokładne minuty. Część trzecia: rowery
i Traficar na zapisanej próbce danych (tests/probka.py), bez sieci.
"""

from datetime import date, datetime

import pytest

import gtfs
import moves
import planner
from tests import probka
from tests.gtfs_builder import make_day

WHEN = datetime(2026, 1, 5, 0, 10, 0)
M = 60


def _flow(start, end, when=WHEN, **kwargs):
    return planner.plan_flow(start, end, when, new_map=True, **kwargs)


def _nums(result):
    return sorted({seg["num"] for seg in result["segments"]})


def _trips(label, rows, prefix, via=None):
    """Kilka kursów jednej linii: rows = [(minuta odjazdu z S, minuty jazdy)]."""
    out = []
    for k, (m, d) in enumerate(rows):
        stops = [("S", (m + 10) * M, (m + 10) * M)]
        if via:
            stops.append((via, (m + 10 + d // 2) * M, (m + 10 + d // 2) * M))
        stops.append(("E", (m + 10 + d) * M, (m + 10 + d) * M))
        out.append({"trip_id": f"{prefix}{k}", "label": label, "stops": stops})
    return out


def test_zbitka_linii_jednego_ruchu_to_jedna_nitka(install_day):
    """Trzy linie z S do E jadą tym samym ruchem - na mapie jest jedna z nich,
    nie trzy nitki ułożone na sobie."""
    install_day(make_day(
        _trips("Autobus 1", [(2, 20)], "a") + _trips("Autobus 2", [(3, 20)], "b")
        + _trips("Autobus 3", [(4, 20)], "c")))

    result = _flow("S", "E")

    assert len(_nums(result)) == 1


def test_stara_mapa_rysuje_wszystkie_bliznicze_linie(install_day):
    install_day(make_day(
        _trips("Autobus 1", [(2, 20)], "a") + _trips("Autobus 2", [(2, 20)], "b")))

    result = planner.plan_flow("S", "E", WHEN)

    assert _nums(result) == ["1", "2"]


def test_bezposredni_i_z_przesiadka_sa_oba(install_day):
    """Jazda bez przesiadki liczy się sama w sobie, mimo późniejszego przyjazdu."""
    install_day(make_day([
        {"trip_id": "t1", "label": "Tramwaj 4", "stops": [("S", 600, 600), ("B", 1200, 1200)]},
        {"trip_id": "t2", "label": "Tramwaj 5", "stops": [("B", 1500, 1500), ("E", 1800, 1800)]},
        {"trip_id": "t3", "label": "Autobus 151",
         "stops": [("S", 700, 700), ("X", 1500, 1500), ("E", 2300, 2300)]},
    ]))

    assert _nums(_flow("S", "E")) == ["151", "4", "5"]


def test_pierwszy_widok_ma_tylko_kilka_ruchow(install_day):
    """Osiem różnych korytarzy do celu - mapa rysuje najwyżej READABLE_MOVES."""
    trips = []
    for k in range(8):
        via = f"V{k}"
        trips.append({"trip_id": f"k{k}", "label": f"Autobus {k + 1}",
                      "stops": [("S", 600, 600), (via, 900 + 60 * k, 900 + 60 * k),
                                ("E", 1500 + 60 * k, 1500 + 60 * k)]})
    install_day(make_day(trips))

    result = _flow("S", "E")

    assert 1 <= len(_nums(result)) <= moves.READABLE_MOVES


def test_te_same_pojazdy_z_innym_miejscem_przesiadki_to_jedna_podroz(install_day):
    install_day(make_day([
        {"trip_id": "t1", "label": "Tramwaj 5",
         "stops": [("S", 600, 600), ("B", 900, 900), ("C", 1000, 1000)]},
        {"trip_id": "t2", "label": "Autobus 125",
         "stops": [("B", 1500, 1500), ("C", 1560, 1560), ("E", 1700, 1700)]},
    ]))

    result = _flow("S", "E")

    assert _nums(result) == ["125", "5"]
    assert len([s for s in result["segments"] if s["num"] == "125"]) == 1


def test_nowa_mapa_nie_zmienia_staremu_progowi(install_day):
    """Wybór z ruchów żyje tylko pod new_map: bez niej wynik jest ten sam,
    co przed wprowadzeniem ruchów."""
    install_day(make_day(_trips("Autobus 1", [(2, 20)], "a")))

    assert planner.plan_flow("S", "E", WHEN) == planner.plan_flow(
        "S", "E", WHEN, new_map=False)


# --- prawdziwy rozkład -------------------------------------------------------

START = (51.1103533, 17.0586404)     # Plac Grunwaldzki 13
SOSNOWIECKA, SKRAJNA = "Sosnowiecka", "Radwanice - ul. Skrajna"
PIATEK = date(2026, 11, 6)


def _real(start, end, day, hhmm, new_map=True, **kwargs):
    if not gtfs.DB_PATH.exists():
        pytest.skip("brak data/gtfs.sqlite")
    last = gtfs.last_service_date()
    if last is None or day > last:
        pytest.skip("dnia nie ma w paczce GTFS")
    hour, minute = map(int, hhmm.split(":"))
    when = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    point = {"start_point": START} if start is None else {}
    return planner.plan_flow(start or "", end, when, new_map=new_map, **point, **kwargs)


def _threads(result):
    return len([s for s in result["segments"] if s.get("num")])


def test_scenariusz_c_z_petli_jeden_ruch_to_jedna_nitka():
    """Z pętli KSIĘŻE MAŁE o 19:30 jedzie dziewięć linii tym samym ruchem:
    stara mapa rysuje dziewięć nitek, nowa jedną."""
    nowa = _real("KSIĘŻE MAŁE", SOSNOWIECKA, PIATEK, "19:30")
    stara = _real("KSIĘŻE MAŁE", SOSNOWIECKA, PIATEK, "19:30", new_map=False)

    assert _threads(nowa) == 1
    assert _threads(stara) >= 5


@pytest.mark.parametrize("hhmm", ["19:00", "15:00", "21:30"])
def test_scenariusz_a_pierwszy_widok_jest_skromny_i_ma_siechnickiego(hhmm):
    result = _real(None, SOSNOWIECKA, PIATEK, hhmm)

    assert "810+81" in _nums(result), hhmm
    assert _threads(result) <= 16, hhmm


def test_scenariusz_a_o_19_nie_ma_wszystkiego_naraz():
    """Pierwszy widok to kilka ruchów: 151 (dalszy, wolniejszy) się nie mieści."""
    result = _real(None, SOSNOWIECKA, PIATEK, "19:00")

    assert "151" not in _nums(result)


def test_scenariusz_b_siechnicki_jest_na_mapie():
    assert "810+81" in _nums(_real(None, SKRAJNA, PIATEK, "19:00"))


def test_scenariusz_b_przy_siechnickim_na_styk_mapa_pokazuje_tez_ruch_z_zapasem():
    result = _real(None, SKRAJNA, PIATEK, "18:55")

    assert len(_nums(result)) >= 2


# --- rowery i Traficar na zapisanej próbce -----------------------------------

def test_rowery_i_traficar_z_probki_w_nowej_mapie_nie_przybywa(monkeypatch):
    """Rowery i auta biorą zasięg z narysowanych ruchów: nowa mapa ma ich
    pierwszy widok skromny (rowerów mniej niż stara, aut tyle, ile nic nie
    bije), a sama próbka naprawdę je wnosi."""
    if not gtfs.DB_PATH.exists():
        pytest.skip("brak data/gtfs.sqlite")
    probka.podlacz(monkeypatch)
    nowa = _real(None, SOSNOWIECKA, PIATEK, "19:00", use_bikes=True)
    stara = _real(None, SOSNOWIECKA, PIATEK, "19:00", new_map=False, use_bikes=True)

    assert nowa["bike_places_live"] is True
    assert nowa["cars"] and stara["cars"]
    assert len(nowa["cars"]) <= 3
    assert 1 <= len(nowa["bike_places"]) < len(stara["bike_places"])


def test_ten_sam_ostatni_pojazd_to_jedna_podroz(install_day):
    """Dwie drogi kończą się tym samym kursem autobusu, tylko wsiadanym w innym
    miejscu: na mapie leżałyby jedna na drugiej, więc jest jedna."""
    install_day(make_day([
        {"trip_id": "t1", "label": "Tramwaj 5", "stops": [("S", 600, 600), ("B", 900, 900)]},
        {"trip_id": "t2", "label": "Tramwaj 6", "stops": [("S", 610, 610), ("C", 1000, 1000)]},
        {"trip_id": "t3", "label": "Autobus 125",
         "stops": [("B", 1200, 1200), ("C", 1300, 1300), ("E", 1500, 1500)]},
    ]))

    result = _flow("S", "E")

    assert len([s for s in result["segments"] if s["num"] == "125"]) == 1
    assert len({s["num"] for s in result["segments"]} & {"5", "6"}) == 1


def test_najszybsza_trasa_z_paska_jest_na_mapie_i_w_schemacie():
    """Pasek i schemat mówią o najszybszej podróży - nowa mapa jej nie gubi,
    nawet gdy inny ruch dowozi tą samą minutą."""
    result = _real("Katedra", "Wojszyce", date(2026, 11, 10), "15:00")

    fastest = {leg["num"] for leg in result["fastest"]["legs"] if leg.get("num")}
    assert fastest <= {s["num"] for s in result["segments"]}
    assert fastest <= {line["num"] for e in result["scheme"]["edges"] for line in e["lines"]}


def test_ruch_nie_wjezdza_w_klamre_przejscia_innego_ruchu():
    """Tramwaj 2 dowozi pod MDK, stamtąd 97 m pieszo na Dworzec Główny i 113.
    Nocny N kończy się na Dworcu Głównym (a inne jadą tamtędy przelotem) - taki
    ruch wjeżdżałby w klamrę przejścia i schemat nie narysuje go prosto przez
    pudełko, więc w pierwszym widoku go nie ma."""
    result = _real("Katedra", "Wojszyce", date(2026, 11, 10), "15:00", scheme_extra=2)

    assert "N" not in _nums(result)
    assert {"2", "113"} <= set(_nums(result))


# --- schemat = dokładnie ruchy z mapy ----------------------------------------

VEHICLE_LABELS = {"rower", "Traficar"}


def _scheme_nums(result):
    return {line["num"] for e in result["scheme"]["edges"] for line in e["lines"]}


@pytest.mark.parametrize("start,end,day,hhmm", [
    (None, SOSNOWIECKA, PIATEK, "19:00"),
    (None, SKRAJNA, PIATEK, "19:00"),
    ("KSIĘŻE MAŁE", SOSNOWIECKA, PIATEK, "19:30"),
    ("Katedra", "Wojszyce", date(2026, 11, 10), "15:00"),
])
def test_schemat_ma_te_same_linie_co_mapa_nawet_z_scheme_extra(start, end, day, hhmm):
    """Schemat powstaje z DOKŁADNIE tych ruchów, które rysuje mapa: scheme_extra
    niczego nie dokłada, więc rozjazd jest niemożliwy z konstrukcji."""
    result = _real(start, end, day, hhmm, scheme_extra=2)

    assert _scheme_nums(result) == set(_nums(result))
    assert 1 <= len(result["scheme"]["moves"]) <= moves.READABLE_MOVES


def test_schemat_mowi_ile_trwa_kazdy_ruch():
    result = _real(None, SOSNOWIECKA, PIATEK, "19:00")

    for move in result["scheme"]["moves"]:
        assert move["lines"] and move["minutes"] > 0
        assert move["departure"] < move["arrival"] or move["arrival"] < "03:00"


def test_przesiadka_po_marszu_o_dwie_minuty_za_krotkiej_jest_na_styk(install_day):
    """Szacunek marszu jest luźny: do WALK_MARGIN_SEC brakującego czasu przesiadka
    nie jest chowana, tylko ma ujemny zapas. Dłuższy brak ją odrzuca."""
    def day_with_bus_at(second):
        return make_day([
            {"trip_id": "t1", "label": "Tramwaj 4", "stops": [("S", 600, 600), ("B", 900, 900)]},
            {"trip_id": "t2", "label": "Autobus 9", "stops": [("C", second, second), ("E", second + 400, second + 400)]},
            {"trip_id": "t3", "label": "Autobus 7", "stops": [("S", 1200, 1200), ("E", 2400, 2400)]},
        ], names={"B": "Przystanek B", "C": "Przystanek C"}, siblings={"B": {"C": 180}, "C": {"B": 180}})

    install_day(day_with_bus_at(900 + 180 - 120))
    short = _flow("S", "E")
    install_day(day_with_bus_at(900 + 180 - 160))
    way_short = _flow("S", "E")

    move = next(m for m in short["scheme"]["moves"] if [l["num"] for l in m["lines"]] == ["4", "9"])
    assert move["tight"] is True and move["slack_min"] < 0
    assert "9" not in _scheme_nums(way_short)


def test_rower_i_traficar_z_probki_sa_odcinkami_ruchu(monkeypatch):
    """Rower (od startu do celu) stoi na schemacie jak ruch: ma swoją linijkę
    z czasem, a pierwszy widok nadal ma najwyżej READABLE_MOVES ruchów."""
    if not gtfs.DB_PATH.exists():
        pytest.skip("brak data/gtfs.sqlite")
    probka.podlacz(monkeypatch)
    result = _real(None, SOSNOWIECKA, PIATEK, "19:00", use_bikes=True)

    labels = {line["num"] for move in result["scheme"]["moves"] for line in move["lines"]}
    assert labels & VEHICLE_LABELS
    assert len(result["scheme"]["moves"]) <= moves.READABLE_MOVES
    assert _scheme_nums(result) - VEHICLE_LABELS == set(_nums(result))


def test_start_cofa_sie_do_rownie_bliskiego_przystanku_tego_samego_kursu():
    """Most Grunwaldzki obok Pl. Grunwaldzkiego: kurs, który mija bliższy start
    wcześniej, rysuje się od niego (inny moment złapania tych samych linii)."""
    day = make_day([
        {"trip_id": "t1", "label": "Tramwaj 4",
         "stops": [("M", 1000, 1000), ("P", 1060, 1060), ("B", 1500, 1500)]},
    ], names={"M": "Most", "P": "Plac", "B": "Dalej"})
    conns = day.conns
    plac_to_b = next(i for i, c in enumerate(conns) if c[2] == "P")
    inst = {"rides": ((plac_to_b, plac_to_b),)}

    extended = moves.extend_start(day, inst, {"M": 180, "P": 180}, 0)

    assert conns[extended["rides"][0][0]][2] == "M"
    assert moves.extend_start(day, inst, {"M": 900, "P": 180}, 0)["rides"] == inst["rides"]
