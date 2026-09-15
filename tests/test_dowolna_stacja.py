"""Dowolna stacja w mieście ("WROCŁAW -") - grupa samych stacji kolejowych,
używana wyłącznie w podróży koleją (patrz gtfs._match_city_group
i planner._city_group_error).

Zgłoszone na żywo: "WROCŁAW -" -> "WARSZAWA -" rysowało tramwaje i autobusy
spod sześciu dworców, regionalne pociągi jeżdżące między wrocławskimi
stacjami na Wrocław Główny, a "WROCŁAW -" -> pl. Grunwaldzki w ogóle dawało
odpowiedź, choć na plac jedzie się tramwajem spod konkretnej stacji.
"""

import datetime

import gtfs
import planner
import traficar
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)   # dep_sec = 0, dla czytelnych liczb


def _day():
    """Alfa ma dwie stacje, Beta jedną. Pociąg jedzie Alfa Zachód -> Alfa
    Główna -> Beta Główna, drugi kończy bieg na Alfie Głównej przed odjazdem
    pierwszego; tramwaj spod Alfy Głównej na plac. Przystanek tramwaju
    i stacja to jedno miejsce - jak Dworzec Główny i Wrocław Główny."""
    trips = [
        {"trip_id": "POCIAG", "label": "KD 1",
         "stops": [("PKP:1", 0, 0), ("PKP:2", 600, 660), ("PKP:3", 3600, 3600)]},
        {"trip_id": "DOJAZD", "label": "KD 2",
         "stops": [("PKP:1", 60, 60), ("PKP:2", 360, 360)]},
        {"trip_id": "TRAMWAJ", "label": "Tramwaj 1",
         "stops": [("DWORZEC", 300, 300), ("PLAC", 900, 900)]},
    ]
    names = {"PKP:1": "Alfa Zachód", "PKP:2": "Alfa Główna", "PKP:3": "Beta Główna",
             "DWORZEC": "Dworzec Alfa", "PLAC": "Plac"}
    day = make_day(trips, names=names)
    day.pkp_stations = [(names[s], s) for s in ("PKP:1", "PKP:2", "PKP:3")]

    station_place = day.place_of["PKP:2"]
    del day.stops_by_place[day.place_of["DWORZEC"]]
    day.stops_by_place[station_place].append("DWORZEC")
    day.place_of["DWORZEC"] = station_place
    return day


def test_grupa_to_same_perony_bez_przystankow_pod_dworcem():
    name, stops, _ = gtfs.match_stop("ALFA -", _day())

    assert name == "ALFA -"
    assert sorted(stops) == ["PKP:1", "PKP:2"]


def test_pojedyncza_stacja_dalej_obejmuje_przystanki_pod_dworcem():
    _, stops, _ = gtfs.match_stop("Alfa Główna", _day())

    assert sorted(stops) == ["DWORZEC", "PKP:2"]


def test_mapa_nie_rysuje_przejazdu_miedzy_stacjami_tego_samego_miasta(
        install_day, pin_deadline):
    """Na Alfie Głównej stoi się od początku, więc pociąg rysuje się od niej,
    a nie od Alfy Zachód, przez którą wcześniej przejeżdża - a pociąg, który
    na Alfę Główną tylko dowozi, nie rysuje się wcale."""
    day = _day()
    install_day(day)
    pin_deadline(3600)

    result = planner.plan_flow("ALFA -", "BETA -", WHEN)

    assert "error" not in result
    pociag = [s for s in result["segments"] if s["num"] == "1"]
    assert pociag
    glowna = [round(c, 5) for c in day.stop_coords["PKP:2"]]
    assert all([round(s["stops_t"][0][0], 5), round(s["stops_t"][0][1], 5)] == glowna
               for s in pociag)
    assert not [s for s in result["segments"] if s["num"] == "2"]


def test_grupa_nie_dziala_z_przystankiem_mpk(install_day):
    install_day(_day())

    for start, end in (("ALFA -", "Plac"), ("Plac", "ALFA -")):
        for result in (planner.plan_route(start, end, WHEN),
                       planner.plan_flow(start, end, WHEN)):
            assert "stacją kolejową" in result["error"]


def test_grupa_nie_dziala_z_punktem_z_mapy(install_day):
    day = _day()
    install_day(day)

    result = planner.plan_flow("ALFA -", "", WHEN, None, day.stop_coords["PLAC"])

    assert "stacją kolejową" in result["error"]


def test_grupa_nie_dziala_ze_stacja_z_tej_samej_grupy(install_day):
    """Na Alfie Głównej stoi się od początku - nie ma dokąd jechać."""
    install_day(_day())

    for start, end in (("ALFA -", "Alfa Główna"), ("Alfa Główna", "ALFA -")):
        for result in (planner.plan_route(start, end, WHEN),
                       planner.plan_flow(start, end, WHEN)):
            assert "jedna ze stacji" in result["error"]


def test_grupa_dziala_ze_stacja_i_z_inna_grupa(install_day):
    install_day(_day())

    for end in ("Beta Główna", "BETA -"):
        assert "error" not in planner.plan_route("ALFA -", end, WHEN)


def test_pojedyncza_stacja_dalej_dziala_z_przystankiem_mpk(install_day):
    install_day(_day())

    assert "error" not in planner.plan_route("Alfa Główna", "Plac", WHEN)


def test_z_grupy_i_do_grupy_nie_idzie_sie_pieszo():
    """Grupa miasta to wybór stacji, a nie miejsce, w którym się stoi - ani
    ze startu, ani do celu nie liczy się dojście pieszo."""
    day = _day()
    grupa = {"PKP:1", "PKP:2"}

    assert planner._origin_walk(day, grupa) == {}
    assert planner._target_reach(day, grupa) == {"PKP:1": (0, "PKP:1"),
                                                  "PKP:2": (0, "PKP:2")}


def test_przy_grupie_nie_ma_aut_ani_rowerow(install_day, pin_deadline, monkeypatch):
    """Decyzja użytkownika: przy dowolnej stacji w mieście aut i rowerów nie ma
    wcale - ani przy Alfie Zachód, ani przy Becie Głównej, do której mapa
    dowozi."""
    day = _day()
    install_day(day)
    pin_deadline(3600)
    zachod, beta = day.stop_coords["PKP:1"], day.stop_coords["PKP:3"]
    auto = {"lat": zachod[0] + 0.0003, "lon": zachod[1], "plate": "PRZY_ZACHODZIE",
            "model": "RENAULT Clio V", "van": False, "where": "", "fuel": 80,
            "range": 300, "ogarniam": []}
    monkeypatch.setattr(traficar, "enabled", lambda: True)
    monkeypatch.setattr(traficar, "car_list", lambda: [
        auto, {**auto, "lat": beta[0] + 0.0003, "lon": beta[1], "plate": "PRZY_BECIE"}])

    result = planner.plan_flow("ALFA -", "BETA -", datetime.datetime.combine(
        datetime.date.today(), datetime.time()))

    assert "error" not in result
    assert result["cars"] == []
    assert result["bike_places"] == []
