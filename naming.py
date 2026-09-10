"""Ręczne poprawki nazewnictwa przystanków - wiedza o mieście, nie algorytm.

Rozkłady same nie wiedzą wszystkiego o nazwach: to samo miejsce nosi w GTFS
i w słowniku PKP dwie różne nazwy, a pasażer wpisuje trzecią. Reguł, które
dałoby się z danych wyprowadzić (perony kierunkowe, polskie znaki), pilnuje
gtfs.py - tutaj leży to, czego wyprowadzić się NIE da i co ktoś musiał
sprawdzić na mapie.

Dlatego osobny plik: gtfs.py opisuje, JAK powstaje miejsce i jak działa
wyszukiwanie, a to jest słownik jednego miasta. Zmiana nazwy przystanku
przez MPK ma być poprawką w tabeli poniżej, a nie wejściem w moduł
z Connection Scanem. Sam plik nic nie robi i nic nie importuje - reguły
(z ich zabezpieczeniami) zostają po stronie gtfs.py.
"""

# Stacja kolejowa i stojący przy niej przystanek MPK to dla pasażera JEDNO
# miejsce, ale prawie nigdy nie mają wspólnej nazwy ("Wrocław Główny" vs
# "DWORZEC GŁÓWNY", "Wrocław Leśnica" vs "Rubczaka (Stacja kolejowa)"), więc
# grupowanie po nazwie ich nie skleja i obie sieci stykały się dotąd
# w pojedynczych punktach (patrz nagłówek pkp.py - to jest ta "osobna
# robota", którą tam zapowiedziano). Klucz to nazwa MPK, wartość - nazwa
# stacji; scala je gtfs._merge_named_places.
#
# Lista jest RĘCZNA, nie liczona z odległości, i to nie jest lenistwo:
# najbliższym słupkiem Wrocławia Głównego jest DWORZEC AUTOBUSOWY (94 m),
# bliżej niż DWORZEC GŁÓWNY (185 m) i SUCHA (193 m). Automat po promieniu
# wsysałby dworzec autobusowy do kolejowego, a `place_of` przypisuje słupek
# do dokładnie jednego miejsca, więc takiego sklejenia nie da się potem
# odkręcić. Sześć par niżej to te, w których MPK ma przy stacji własny
# przystanek pod INNĄ nazwą - reszta wrocławskich stacji albo nazywa się
# tak samo jak przystanek (skleja się sama), albo nie ma przy sobie nic
# bliżej niż kilkaset metrów.
#
# Ręczne pochodzenie nie daje jednak taryfy ulgowej: scalenie i tak
# przechodzi przez próg odległości (gtfs.PLACE_MAX_SPAN_M), więc literówka
# kończy się BRAKIEM scalenia, a nie trzyminutowym przejściem przez pół
# Polski. Wpis o nazwie, której nie ma w rozkładzie (zmiana nazwy
# przystanku, wyłączona kolej), jest po cichu pomijany.
#
# Nazwy piszemy tak, jak stoją w danych - wielkość liter bez znaczenia,
# porównanie idzie po casefold.
PLACE_MERGES = {
    "DWORZEC GŁÓWNY": "Wrocław Główny",
    "DWORZEC NADODRZE": "Wrocław Nadodrze",
    "Rubczaka (Stacja kolejowa)": "Wrocław Leśnica",
    "Awicenny (Stacja kolejowa)": "Wrocław Zachodni",
    "Tarczyński Arena (Lotnicza)": "Wrocław Stadion",
    "KUŹNIKI (Stacja kolejowa)": "Wrocław Kuźniki",
}
