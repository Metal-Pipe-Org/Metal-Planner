# Zasady

Reguły, które stoją NAD pojedynczymi funkcjami: obowiązują mapę, rozkłady,
tryb podróży i wszystko, co dopiero powstanie. Kontrakt
([FLOW_MAP_CONTRACT.md](FLOW_MAP_CONTRACT.md)) mówi, co ma robić mapa; ten
plik mówi, czym kierujemy się wszędzie.

Nic tu nie jest wymyślone na zapas. Każda zasada jest wyciągnięta z decyzji,
które już zapadły i już są w kodzie — pod każdą stoi miejsce, w którym widać
ją działającą.

> **Ten plik zmienia się wyłącznie na wyraźne polecenie użytkownika** — tak
> samo jak kontrakt, i z tego samego powodu. O tym, czy coś jest już zasadą
> i czy ma tu trafić, rozstrzyga użytkownik; nigdy nie dopisuje się tu nic
> „przy okazji" ani dlatego, że właśnie zostało zaimplementowane. Propozycję
> zgłasza się słowami. Historia i uzasadnienia pojedynczych zmian idą do
> [FLOW_MAP_NOTES.md](FLOW_MAP_NOTES.md).

## 1. Aplikacja nie wybiera za użytkownika

Kondensować trzeba — pokazanie wszystkiego to to samo, co nie pokazanie
niczego. Ale to, czego nie widać, odpada według **jasnej miary**, a nie
według naszego zdania o tym, co dla kogoś ważne. Stąd trzy warunki, które
musi spełnić każde ukrywanie:

1. **Jedna miara, nazwana wprost.** Wiadomo, czym mierzymy i że mierzymy
   tylko tym.
2. **Cięcie jednym progiem, bez dziur.** Jeśli coś o danej jakości jest
   pokazane, to wszystko lepsze też. Żadnych wyjątków „pokaż mimo to" ani
   ukrywania pojedynczych opcji.
3. **Próg zostaje w rękach użytkownika.** Da się go przesunąć, a przesunięcie
   działa przewidywalnie w obie strony.

**Gdzie to widać na mapie.** Miarą jakości jest wyłącznie to, jak blisko dana
opcja dowozi do celu w porównaniu z najszybszą trasą; próg tej miary ustawia
się tak, żeby narysowana sieć miała docelową gęstość, a godzina „mapa pokazuje
do" jest tego skutkiem, nie ustawieniem. „Pokaż więcej" podnosi cel gęstości
o kolejne jednostki. Mapa kończy się w jednym miejscu skali, zamiast wybierać
za pasażera (kontrakt, punkty 2 i 10).

**Gdzie to widać przy autach i rowerach.** Nie wyceniamy, ile dla kogoś warte
jest zachodzenie po auto ani ile złotych z „Ogarniam" równa się minucie — bo
tego nie wiemy i nie mamy prawa zgadywać. Zamiast tego pokazujemy komplet
opcji, których **nic nie bije naraz we wszystkich** wypisanych kryteriach:
zawsze widać każde auto i każdy przejazd rowerem, od którego nie ma
jednocześnie wcześniejszego, bliższego i tańszego. Każde z nich jest w czymś
najlepsze, a wybór między nimi należy do człowieka. Suwak dokłada kolejne,
coraz słabsze — czyli znów: próg, nie decyzja (kontrakt, punkty 15 i 16).

**Czego ta zasada zabrania.** Wagi ustawionej po cichu za użytkownika
(„minuta jest warta tyle złotych", „przesiadka kosztuje tyle komfortu"),
sortowania po wskaźniku sklejonym z kilku rzeczy naraz i chowania
pojedynczych opcji dlatego, że wydają się nam dziwne.

## 2. Nie udajemy wiedzy, której nie mamy

Liczba pokazana jako odczytana ma być odczytana. Godziny pojazdów biorą się
z rozkładu, nigdy z prędkości ani z odległości; wolno wyłącznie interpolować
między dwiema godzinami **tego samego kursu**, proporcjonalnie do przebytej
drogi (kontrakt, punkt 10).

Tam, gdzie odczytać nie ma z czego, szacunek jest widoczny jako szacunek
i zawsze myli się w tę samą, bezpieczną stronę: czas przejścia pieszo liczy
się z odległości i hojnie (kontrakt, punkt 14), a godziny samego przejazdu
rowerem są domyślnie zgaszone — to jedyne liczby na tej mapie, których nie ma
w żadnym rozkładzie, a stojąca obok odległość mówi to samo, nie udając
odczytanej.
