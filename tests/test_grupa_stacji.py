"""Grupa stacji jednego miasta ma być ZNAJDOWALNA pod tą nazwą, którą
użytkownik widzi.

PKP oznacza "dowolną stację w mieście" wpisem "WARSZAWA -" (patrz
update_pkp._is_city_wildcard) i ten myślnik jest u nas ZARAZEM kluczem
dopasowania - gtfs._match_city_group rozpoznaje po nim pytanie o grupę.
Zamiana go na czytelne "(dowolna stacja)" wprost w zwracanej nazwie została
raz zrobiona i cofnięta (2026-09-03): front odsyła nazwę z odpowiedzi
z powrotem przy ponownym wyszukaniu, więc nazwa, która nie pasuje do siebie
samej, dawała fałszywe "nie znaleziono przystanku" przy DRUGIM szukaniu tej
samej trasy. Czytelną etykietę robi więc front (prettyStopName/rawStopName
w static/app.js), a tu pilnujemy dwóch rzeczy: że round-trip trzyma się
nadal, i że obie postaci pytania trafiają w to samo (siatka pod tamtym -
gdyby któreś wywołanie na froncie przegapiło zamianę wstecz).
"""

import gtfs
from gtfs_builder import make_day


def _day_z_grupa():
    """Dzień z trzema stacjami PKP jednego miasta - tak, jak dokłada je
    pkp.augment_day: nazwy w stops_by_key ORAZ osobno w pkp_stations."""
    trips = [{
        "trip_id": "PKP:1", "label": "Pociąg",
        "stops": [("PKP:1", 0, 0), ("PKP:2", 600, 600), ("PKP:3", 1200, 1200)],
    }]
    names = {
        "PKP:1": "Wrocław Główny",
        "PKP:2": "Wrocław Nadodrze",
        "PKP:3": "Wrocław Psie Pole",
    }
    day = make_day(trips, names=names)
    day.pkp_stations = [(name, stop_id) for stop_id, name in names.items()]
    return day


def test_a_city_group_answers_to_the_name_it_gives_back():
    """Round-trip: nazwa zwrócona z dopasowania, podana z powrotem na
    wejściu, daje ten sam wynik. To jest właśnie ten niezmiennik, którego
    złamanie kosztowało nas fałszywe "nie znaleziono" w wersji z 3 września."""
    day = _day_z_grupa()
    name, stops, hints = gtfs.match_stop("Wrocław -", day)
    assert name and hints is None
    znowu_name, znowu_stops, znowu_hints = gtfs.match_stop(name, day)
    assert (znowu_name, sorted(znowu_stops), znowu_hints) == (name, sorted(stops), None)


def test_the_readable_label_finds_the_same_group():
    """Etykieta z frontu ("Wrocław (dowolna stacja)") trafia dokładnie w to
    samo, co kanoniczny myślnik - siatka na wypadek, gdyby na froncie gdzieś
    zabrakło rawStopName."""
    day = _day_z_grupa()
    myslnik = gtfs.match_stop("Wrocław -", day)
    etykieta = gtfs.match_stop(f"Wrocław {gtfs.CITY_GROUP_LABEL}", day)
    assert etykieta[0] == myslnik[0]
    assert sorted(etykieta[1]) == sorted(myslnik[1])


def test_the_group_still_answers_under_its_canonical_name():
    """Kanoniczna postać zostaje z myślnikiem - front trzyma w niej stan
    i zapisuje ostatnie wyszukiwanie w localStorage, więc jej zmiana
    unieważniłaby to, co ludzie mają już zapisane w przeglądarce."""
    day = _day_z_grupa()
    name, stops, _ = gtfs.match_stop(f"Wrocław {gtfs.CITY_GROUP_LABEL}", day)
    assert name == "WROCŁAW -"
    assert len(stops) == 3


def test_the_label_works_without_polish_letters_too():
    """Bez ogonków ("wroclaw (dowolna stacja)") grupa też się znajduje -
    dopasowanie porównuje nazwy po zdjęciu diakrytyków (_strip_diacritics).
    Zwrócona nazwa powtarza pisownię PYTANIA, nie słownika (tak było
    i przed etykietą) - i nadal pasuje sama do siebie, czyli round-trip
    trzyma się również dla niej."""
    day = _day_z_grupa()
    name, stops, _ = gtfs.match_stop("wroclaw (dowolna stacja)", day)
    assert name == "WROCLAW -"
    assert len(stops) == 3
    assert gtfs.match_stop(name, day)[1] and sorted(gtfs.match_stop(name, day)[1]) == sorted(stops)


def test_an_ordinary_stop_is_not_taken_for_a_group():
    """Nazwa bez żadnego z tych dwóch sufiksów nie ma prawa wejść w gałąź
    grupy - zwykłe dopasowanie ma pierwszeństwo i tak zostaje."""
    day = _day_z_grupa()
    name, stops, hints = gtfs.match_stop("Wrocław Główny", day)
    assert name == "Wrocław Główny" and hints is None
    assert stops == ["PKP:1"]
