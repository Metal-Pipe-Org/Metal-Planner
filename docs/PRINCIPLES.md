# Zasady

Reguły nad pojedynczymi funkcjami — obowiązują mapę, rozkłady i wszystko, co
dopiero powstanie. Kontrakt ([FLOW_MAP_CONTRACT.md](FLOW_MAP_CONTRACT.md))
mówi, co ma robić mapa; ten plik — czym kierujemy się wszędzie.

> **Zmienia się wyłącznie na wyraźne polecenie użytkownika**, tak jak
> kontrakt. Propozycje zgłasza się słowami; historią zmian jest lista
> commitów.

## 1. Aplikacja nie wybiera za użytkownika

Pokazanie wszystkiego to to samo, co niepokazanie niczego, więc ukrywać
trzeba. Ale to, czego nie widać, odpada według jednej, nazwanej wprost miary,
jednym progiem bez dziur — jeśli coś jest pokazane, to wszystko lepsze też —
a próg zostaje w rękach użytkownika.

Gdy rzeczy różnią się kilkoma cechami naraz, nie ważymy ich za pasażera:
pokazujemy każdą, której nic nie bije we wszystkich cechach jednocześnie,
a wybór między nimi zostawiamy człowiekowi. Na mapie miarą jest to, o ile
dłużej trwa podróż (kontrakt, punkt 2); przy autach i rowerach — komplet
opcji, z których każda jest w czymś najlepsza (punkty 15 i 16).

Zakazane: ciche wagi („minuta jest warta tyle złotych", „przesiadka kosztuje
tyle minut"), sortowanie po wskaźniku sklejonym z kilku rzeczy i chowanie
pojedynczych opcji dlatego, że wydają się nam dziwne.

## 2. Nie udajemy wiedzy, której nie mamy

Liczba pokazana jako odczytana ma być odczytana: godziny pojazdów biorą się
z rozkładu, nigdy z prędkości ani odległości (kontrakt, punkt 10).

Gdzie odczytać nie ma z czego — marsz, rower, auto — szacunek wygląda na
szacunek i myli się zawsze w bezpieczną stronę. Godziny przejazdu rowerem
i dojazdu autem są domyślnie schowane; obok stoi odległość, która mówi to
samo, nie udając rozkładu.

## 3. Mechanizmy są tak proste, jak się da

Każdy algorytm i mechanizm aplikacji ma być najprostszy, jaki działa: jedna ogólna reguła, którą da się wytłumaczyć jednym zdaniem i przewidzieć bez czytania kodu. Tak działa każde dobre oprogramowanie — prosty mechanizm łatwo zrozumieć, sprawdzić i zmienić, a stos wyjątków w końcu przestaje być zrozumiały nawet dla autora.

Gdy reguła w jakiejś sytuacji daje mniej, niż się spodziewamy — na przykład pustą tablicę albo brak kropki — to jest jej wynik i tak zostaje. Jeśli wynik jest naprawdę zły, zmienia się regułę dla wszystkich przypadków naraz, a nie łata ten jeden.

Zakazane: osobne ścieżki dla rzadkich sytuacji, zastępcze treści „żeby nie było pusto” i warunki dopisywane pod pojedynczy przykład.
