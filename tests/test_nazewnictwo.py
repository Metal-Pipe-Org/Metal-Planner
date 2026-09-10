"""To samo miejsce pod dwiema nazwami (issue #65).

Dwie strony tej samej sprawy, obie w naming.py:

1. Stacja kolejowa i przystanek MPK stojący przy niej to dla pasażera jedno
   miejsce, ale nazywają się różnie ("Wrocław Główny" vs "DWORZEC GŁÓWNY") -
   scala je ręczna tabela PLACE_MERGES.
2. Ten sam przystanek bywa zapisany i wpisywany na kilka sposobów
   ("PL. GRUNWALDZKI" vs "Plac Grunwaldzki") - godzi je REGUŁA rozwijania
   skrótów (ABBREVIATIONS + gtfs._alias_key), nie lista wyjątków.

W obu wypadkach pilnujemy, żeby robiły dokładnie to i nic więcej - a przede
wszystkim, żeby nie odbierały trafienia dokładniejszemu dopasowaniu.

Dane są syntetyczne (bez SQLite, bez PKP_API_KEY) - poza testami kształtu
samych tabel, które patrzą na prawdziwe wpisy.
"""

import pytest

import gtfs
import naming
import planner
from gtfs_builder import make_day

# Prawdziwe współrzędne z Wrocławia - odległości mają być realne, bo cała
# rzecz opiera się na progu PLACE_MAX_SPAN_M.
_STACJA = (51.09805, 17.03627)       # Wrocław Główny
_PRZYSTANEK = (51.09919, 17.03433)   # DWORZEC GŁÓWNY, ~185 m od stacji
_SASIEDNI = (51.10000, 17.03531)     # drugi słupek tego samego przystanku
_DALEKO = (51.500, 19.500)           # ~200 km - literówka w tabeli

_MPK = "DWORZEC GŁÓWNY"
_KOLEJ = "Wrocław Główny"


@pytest.fixture
def scalenie(monkeypatch):
    """Tabela zawężona do jednej pary - test nie zależy od tego, co akurat
    stoi w prawdziwym PLACE_MERGES."""
    monkeypatch.setattr(naming, "PLACE_MERGES", {_MPK: _KOLEJ})


def _miejsca(coords_stacji=_STACJA):
    """(stop_names, stop_coords, stops_by_key) dla dwóch słupków MPK i stacji."""
    stop_names = {"MPK1": _MPK, "MPK2": _MPK, "PKP:1": _KOLEJ}
    stop_coords = {"MPK1": _PRZYSTANEK, "MPK2": _SASIEDNI, "PKP:1": coords_stacji}
    stops_by_key = {
        _MPK.casefold(): ["MPK1", "MPK2"],
        _KOLEJ.casefold(): ["PKP:1"],
    }
    return stop_names, stop_coords, stops_by_key


def _dzien(coords_stacji=_STACJA):
    """DayData z tymi słupkami, przepuszczony przez budowę miejsc - to samo,
    co robi gtfs.load_day, tylko bez bazy."""
    stop_names, stop_coords, stops_by_key = _miejsca(coords_stacji)
    day = gtfs.DayData()
    day.stop_names = dict(stop_names)
    day.stop_coords = dict(stop_coords)
    for stop_id, name in stop_names.items():
        gtfs._register_stop_name(day, stop_id, name)
    day.stops_by_place = gtfs._build_places(stop_names, stop_coords, stops_by_key)
    day.place_of = {
        sid: key for key, ids in day.stops_by_place.items() for sid in ids
    }
    return day


# ---- scalanie ------------------------------------------------------------

def test_a_station_and_the_stop_beside_it_become_one_place(scalenie):
    """Sedno sprawy: dwie nazwy, jedno miejsce."""
    places = gtfs._build_places(*_miejsca())
    assert list(places) == [_MPK.casefold()], "klucz stacji został osierocony"
    assert sorted(places[_MPK.casefold()]) == ["MPK1", "MPK2", "PKP:1"]


def test_the_merged_place_is_what_gives_the_transfer(scalenie):
    """Scalenie nie jest kosmetyką nazw - ma dać most pieszy pociąg <-> tramwaj.
    Innego mechanizmu przesiadki między sieciami nie ma (patrz pkp.py)."""
    places = gtfs._build_places(*_miejsca())
    bridges = gtfs._walking_bridges(places.values())
    assert sorted(bridges["PKP:1"]) == ["MPK1", "MPK2"]


def test_a_merge_across_town_is_refused(scalenie):
    """Ręczna lista nie jest powodem, żeby ufać bardziej niż automatowi.
    Literówka albo dopisanie rozkładu innego miasta ma skończyć się BRAKIEM
    scalenia, a nie trzyminutowym przejściem przez pół Polski."""
    places = gtfs._build_places(*_miejsca(coords_stacji=_DALEKO))
    assert sorted(places[_MPK.casefold()]) == ["MPK1", "MPK2"]
    assert places[_KOLEJ.casefold()] == ["PKP:1"], "stacja zniknęła zamiast zostać osobno"


def test_a_name_that_is_not_in_the_timetable_is_ignored(monkeypatch):
    """Wpis o nazwie, której w rozkładzie nie ma (zmiana nazwy przystanku,
    wyłączona kolej) ma być po cichu pominięty, a nie wywrócić budowę dnia."""
    monkeypatch.setattr(naming, "PLACE_MERGES", {
        _MPK: _KOLEJ,
        "Przystanek widmo": "Stacja widmo",
    })
    stop_names, stop_coords, stops_by_key = _miejsca()
    del stops_by_key[_KOLEJ.casefold()]
    del stop_names["PKP:1"]
    del stop_coords["PKP:1"]
    places = gtfs._build_places(stop_names, stop_coords, stops_by_key)
    assert sorted(places[_MPK.casefold()]) == ["MPK1", "MPK2"]


# ---- co widzi wyszukiwarka -----------------------------------------------

def test_both_names_lead_to_the_whole_place(scalenie):
    """Obie nazwy mają dawać CAŁE miejsce - dojazd pociągiem liczy się jako
    dojazd na "DWORZEC GŁÓWNY" i odwrotnie."""
    day = _dzien()
    for query in (_MPK, _KOLEJ):
        _, stops, suggestions = gtfs.match_stop(query, day)
        assert suggestions is None, f"{query!r} przestało być znaną nazwą"
        assert sorted(stops) == ["MPK1", "MPK2", "PKP:1"]


def test_neither_name_is_swallowed_by_the_other(scalenie):
    """Scalenie miejsc NIE przemianowuje przystanków: każda nazwa wraca ze
    swoją pisownią. Nazwa zwrócona przez match_stop bywa podawana z powrotem
    na wejściu (front odsyła ją przy ponownym wyszukaniu), więc musi trafiać
    w to samo miejsce - ten sam niezmiennik round-tripu, co przy "Miasto -"
    (patrz gtfs._match_city_group)."""
    day = _dzien()
    for query in (_MPK, _KOLEJ):
        name, stops, _ = gtfs.match_stop(query, day)
        assert name == query
        assert gtfs.match_stop(name, day)[1] == stops


def test_the_table_pairs_two_different_names():
    """Kształt PRAWDZIWEJ tabeli - dlatego bez fixture'a `scalenie`, który ją
    podmienia. Para z tą samą nazwą po obu stronach nic by nie scaliła, a dwa
    przystanki wskazujące tę samą stację to pomyłka przy kopiowaniu - żadnej
    z nich nie widać po danych, bo scalenie po prostu po cichu nie zachodzi."""
    keys = [k.casefold() for k in naming.PLACE_MERGES]
    values = [v.casefold() for v in naming.PLACE_MERGES.values()]
    assert len(set(keys)) == len(keys)
    assert len(set(values)) == len(values)
    assert not set(keys) & set(values)


# ---- co czyta pasażer ----------------------------------------------------

def _przejscie(nazwa_z, nazwa_do):
    day = make_day(
        [{"trip_id": "T1", "label": "Tramwaj 1", "stops": [("A", 0, 0), ("B", 60, 60)]}],
        names={"A": nazwa_z, "B": nazwa_do},
    )
    return planner._walk_leg(day, "A", "B")["text"]


def test_a_walk_between_platforms_still_reads_as_one():
    assert _przejscie(_MPK, _MPK).startswith("Zmiana stanowiska na przystanku")


def test_a_walk_between_two_names_says_where_to_go():
    """Wysiadającemu z pociągu na "Wrocław Główny" zdanie o zmianie stanowiska
    nic nie mówi - on ma dojść do "DWORZEC GŁÓWNY"."""
    text = _przejscie(_KOLEJ, _MPK)
    assert _KOLEJ in text and _MPK in text
    assert "stanowisk" not in text


# ---- ta sama nazwa, inna pisownia ----------------------------------------

def _dzien_z_nazwami(*nazwy):
    """DayData ze słupkami o podanych nazwach, po pełnej rejestracji kluczy."""
    day = gtfs.DayData()
    for i, nazwa in enumerate(nazwy):
        stop_id = f"S{i}"
        day.stop_names[stop_id] = nazwa
        day.stop_coords[stop_id] = (51.11 + i * 0.001, 17.03)
        gtfs._register_stop_name(day, stop_id, nazwa)
    day.stops_by_place = gtfs._build_places(
        day.stop_names, day.stop_coords, day.stops_by_key)
    day.place_of = {
        sid: key for key, ids in day.stops_by_place.items() for sid in ids
    }
    return day


@pytest.mark.parametrize("query", [
    "PL. GRUNWALDZKI",     # dokładnie jak w danych
    "pl. grunwaldzki",     # inna wielkość liter
    "Plac Grunwaldzki",    # skrót rozwinięty - o to prosi issue #65
    "plac grunwaldzki",
    "pl grunwaldzki",      # bez kropki
    "Pl Grunwaldzki",
])
def test_every_spelling_of_the_abbreviation_finds_the_stop(query):
    day = _dzien_z_nazwami("PL. GRUNWALDZKI")
    name, stops, suggestions = gtfs.match_stop(query, day)
    assert suggestions is None, f"{query!r} nie trafiło w przystanek"
    assert name == "PL. GRUNWALDZKI", "wróciła inna pisownia niż z rozkładu"
    assert stops == ["S0"]


def test_the_expansion_works_the_other_way_round_too():
    """Dane bywają zapisane pełnym słowem, a użytkownik pisze skrótem -
    reguła składa OBIE strony, więc działa w obie strony."""
    day = _dzien_z_nazwami("Osiedle Przyjaźni")
    assert gtfs.match_stop("os. przyjazni", day)[0] == "Osiedle Przyjaźni"


def test_the_exact_spelling_still_wins():
    """Nowy poziom wchodzi na DOLE kaskady. Gdyby pytać o niego wcześniej,
    odbierałby trafienie nazwie pasującej dokładniej."""
    day = _dzien_z_nazwami("Plac Zabaw", "PL. ZABAW")
    name, stops, _ = gtfs.match_stop("Plac Zabaw", day)
    assert (name, stops) == ("Plac Zabaw", ["S0"]), "alias przebił dokładną pisownię"


def test_an_abbreviation_only_expands_as_a_whole_word():
    """Rozwijamy SŁOWA, nie fragmenty - inaczej "Plateau" zrobiłoby się
    "placateau", a "Oleśnica" zaczęłaby pasować do czegokolwiek z "os"."""
    assert gtfs._alias_key("Plateau") == "plateau"
    assert gtfs._alias_key("Oleśnica") == "olesnica"
    assert gtfs._alias_key("Pl. Nowy Targ") == "plac nowy targ"


def test_a_dot_without_a_space_still_splits_words():
    """"C.H.Korona" i "C. H. Korona" mają dać ten sam klucz - kropka leci
    przez spację, nie przez pustkę."""
    assert gtfs._alias_key("C.H.Korona") == gtfs._alias_key("C. H. Korona")


def test_a_half_typed_query_suggests_through_the_expansion():
    """"plac grun" nie występuje w żadnej surowej nazwie, więc bez poziomu
    aliasowego nie podpowiadałoby NICZEGO."""
    day = _dzien_z_nazwami("PL. GRUNWALDZKI")
    name, stops, _ = gtfs.match_stop("plac grun", day)
    assert (name, stops) == ("PL. GRUNWALDZKI", ["S0"])


def test_saint_stays_untouched():
    """"św." to rzeczownik odmienny (świętego/świętej), więc jedno rozwinięcie
    rozjechałoby przypadki zamiast je scalić - w tabeli go celowo nie ma."""
    assert "sw" not in naming.ABBREVIATIONS
    assert gtfs._alias_key("Rondo Św. Ojca Pio") == "rondo sw ojca pio"


def test_rail_stations_go_through_the_same_indexes():
    """Nazwy PKP rejestruje dziś ta sama funkcja co nazwy MPK (pkp.augment_day
    nie ma już własnej kopii) - inaczej stacje wypadłyby z nowego poziomu."""
    day = _dzien_z_nazwami("Wrocław Główny")
    assert gtfs.match_stop("wroclaw glowny", day)[0] == "Wrocław Główny"
    assert day.stops_by_alias_key["wroclaw glowny"] == ["S0"]
