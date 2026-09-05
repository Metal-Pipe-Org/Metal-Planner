# Metal-Planner

Webowa wyszukiwarka połączeń komunikacji miejskiej Wrocławia. Zamiast jednej
wyliczonej trasy pokazuje na mapie **wszystkie sensowne dojazdy naraz** —
główne korytarze jaskrawo, niszowe objazdy ledwo widocznie — a obok, w panelu,
**listę gotowych propozycji** z godzinami, liniami i przesiadkami. Wybór
propozycji podświetla ją na mapie, a kliknięcie linii na mapie otwiera
propozycję, która nią jedzie. Start można ustawić przyciskiem ◎ na aktualną
lokalizację. Na telefonie mapa i lista przełączają się dolnymi zakładkami.

Pełny opis projektu, architektury i algorytmów: **[docs/PROJECT.md](docs/PROJECT.md)**
(szczegóły samego algorytmu mapy przepływów: **[docs/ROUTING_ALGORITHM.md](docs/ROUTING_ALGORITHM.md)**,
gwarancje zachowania mapy, które to sprawdzają: **[docs/FLOW_MAP_CONTRACT.md](docs/FLOW_MAP_CONTRACT.md)**).

## Szybki start

Wymagany Python ≥ 3.9 (Flask 3.x nie działa na 3.8).

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python update_gtfs.py
.venv/bin/python app.py
```

`update_gtfs.py` pobiera rozkład (~12 MB) i buduje z niego bazę — zajmuje to
około 10 sekund. `app.py` wystawia serwer na http://localhost:5001; port to
domyślnie 5001 (5000 zajmuje AirPlay na macOS), można go zmienić zmienną
`PORT`.

## Rozkłady jazdy — linie i przystanki

Obok wyszukiwarki połączeń panel ma drugi tryb, przełączany przyciskiem
**◷ Rozkłady** tuż obok ← (chowania panelu); na telefonie ten sam przycisk
jest w pasku zakładek na dole. Oba tryby zajmują to samo miejsce — wejście w rozkłady
chowa okienko wyszukiwania, wyjście przywraca je razem z tym, co było
narysowane na mapie, bez ponownego szukania.

Jest **jedno pole** na jedno i drugie: „17" to linia, „Katedra" to
przystanek. Rodzaj widać na liście podpowiedzi — linie mają plakietkę
w kolorze pojazdu, przystanki znaczek słupka — czyli tam, gdzie faktycznie
jest potrzebny, a nie w przełączniku ustawianym przed wpisaniem czegokolwiek.

Pod polem stoi **dzień i godzina**. Godzina jest jedna na oba rozkłady
i nic nie dociąga — odpowiedź niesie całą dobę, więc jej zmiana tylko
przesuwa to, od czego zaczyna się tablica, i to, który kurs linii jest
„ten najbliższy".

**Linia** odpowiada na „którędy jedzie": wybór wariantu i lista jego
przystanków, z przebiegiem na mapie. Kierunki podstawowe stoją obok siebie,
a kursy skrócone (do zajezdni, z pętli w połowie trasy) chowają się pod
rozwijaniem — jest ich zwykle więcej niż samych kierunków i równorzędnie
pokazane zamieniają wybór kierunku w szukanie w liście. Przy każdym
kierunku widać, ile kursów nim jedzie — to po tym poznaje się ten, którym
linia jeździ cały dzień.

Godzin tu nie ma i to jest wybór, nie brak: rozkład wisi na słupku, a nie
na trasie, więc „o której to jedzie" ma sens dopiero razem z „skąd" — i tam
się o nie pyta, przyciskiem opisanym niżej.

Kursy są grupowane po ciągu przystanków, nie po samym napisie na czole:
zlanie ich w jedną listę dałoby trasę, przez połowę której połowa tych
kursów nie przejeżdża.

Kliknięcie przystanku na liście przybliża go na mapie, a przycisk
**„odjazdy"** z prawej strony wiersza przeskakuje na tablicę tego
przystanku — od razu z właściwym słupkiem i samą tą linią, bez odhaczania
reszty ręcznie. Krawędź bierze się z rozkładu, więc trafia w tę stronę,
którą linia faktycznie jedzie.

**Przystanek** — po nazwie albo kliknięciem słupka na mapie. Tablica
odjazdów wszystkich linii, z plakietkami do odhaczania: zostaje rozkład
złożony dokładnie z tych linii, które zaznaczysz — od jednej do wszystkich
naraz. Trasy zaznaczonych linii, **od tego przystanku dalej**, rysują się
na mapie (do ośmiu linii, wyżej z węzła robi się kłębek); kliknięcie
konkretnego odjazdu pokazuje ten jeden kurs z godzinami na każdym
przystanku.

Jedna nazwa to zwykle kilka **słupków**, a z każdego jedzie się w inną
stronę — karta „Słupki" wypisuje je kierunkami (nazywają się przecież tak
samo) i zawęża do jednego tablicę, listę linii i trasy na mapie. Słupki są
też punktami na mapie: klik w przygaszony przełącza tablicę na niego.

Tablicę czyta się na dwa sposoby: **„podana godzina"** to lista najbliższych
odjazdów, a **„pełny rozkład"** to zapis ze słupka — wiersz na godzinę,
w wierszu minuty kursów, cała doba na jednym ekranie. Ten drugi dotyczy
jednej linii naraz (pod zlanym ciągiem minut nie wiadomo, co podjedzie),
a kierunek odróżniają odnośniki, tak jak na papierze.

## Instalacja jako aplikacja (PWA)

Planer instaluje się jak zwykła aplikacja — na telefonie „Dodaj do ekranu
głównego", na pulpicie ikoną ⤓ w nagłówku albo z paska adresu przeglądarki.
Działa wtedy we własnym oknie, a raz odwiedzona okolica mapy jest dostępna
też bez internetu (samo wyszukiwanie połączeń wymaga sieci).

Instalację przeglądarki proponują wyłącznie po **HTTPS**; wyjątkiem jest
`localhost`, więc lokalnie działa to od ręki.

## Codzienna aktualizacja rozkładu

Serwer robi to sam: raz na dobę o godzinie z `GTFS_AUTO_UPDATE_HOUR`
(w `docker-compose.yml` domyślnie 3:00, wg strefy czasowej kontenera).
Harmonogram żyje w procesie głównym serwera — jeden niezależnie od liczby
workerów — a samą aktualizację odpala jako osobny proces, więc budowa bazy
nie rośnie w pamięci serwera. Pusta wartość wyłącza harmonogram.

To nie jest wygoda, tylko warunek działania: paczka GTFS ma okno ważności
rzędu trzech tygodni, więc kontener stojący dłużej bez restartu dojechałby
do jego końca i przestał znajdować **jakiekolwiek** połączenia.

Gdy pobieranie się nie powiedzie, stara baza zostaje nietknięta — aplikacja
działa dalej na wczorajszych danych i przeładuje nowe sama, bez restartu.

Poza Dockerem to samo załatwia cron, np. o 3:00:

```
0 3 * * * cd /sciezka/do/Metal-Planner && .venv/bin/python update_gtfs.py >> logs/update.log 2>&1
```

### Odświeżanie przy starcie serwera

Cron nie jest potrzebny do jednego przypadku: **każdy start serwera odświeża
rozkład sam** — i w kontenerze (`docker/entrypoint.sh`, przed gunicornem),
i lokalnie (`python app.py`). Zasada jest ta sama: brakującą bazę serwer
pobiera blokująco, przed startem, a istniejącą odświeża w tle — wstaje
natychmiast na dotychczasowych danych i podmienia je w locie.

Różnica jest jedna. Lokalnie obowiązuje **próg świeżości**: baza młodsza niż
`GTFS_MAX_AGE_HOURS` (domyślnie 12 h) nie jest ruszana. Bez tego reloader
Flaska, który restartuje serwer po każdym zapisie pliku, ciągnąłby 12 MB po
każdym Ctrl+S. Kontener restartuje się rzadko, więc tam progu nie ma —
aktualizuje zawsze.

`GTFS_UPDATE_ON_START=off` wyłącza to w obu miejscach (w kontenerze przez
`docker-compose.yml`).

### Autobusy gminy Siechnice

Linii 800, 810, 83–89, 860, 870, 890 **nie ma w żadnych otwartych danych** —
ani we wrocławskim GTFS, ani na dane.gov.pl, ani w Krajowym Punkcie
Dostępowym. Jedyne strukturalne źródło to niedokumentowane API systemu
kiedyPrzyjedzie, którym gmina obsługuje informację pasażerską; `siechnice.py`
umie z niego złożyć kompletne kursy i dokleić je do bazy.

Jest to **domyślnie wyłączone**, bo `robots.txt` tego serwisu to `Disallow: /`
i nie ma tam żadnego regulaminu ani zgody na ponowne wykorzystanie danych.
Włącza się świadomie:

```
SIECHNICE_ENABLED=on .venv/bin/python update_gtfs.py
```

Kosztuje to ok. 1,5 minuty na każdy dzień rozkładu (237 słupków × jedno
zapytanie), czyli ok. 10 minut przy domyślnym oknie tygodniowym. Awaria tego
kroku nie przerywa aktualizacji — rozkład Wrocławia wjeżdża na miejsce
niezależnie.

Docelowe rozwiązanie to poprosić gminę o eksport GTFS — dlaczego to prośba
o włączenie istniejącej funkcji, do kogo pisać i co w tym piśmie napisać,
opisuje [docs/SIECHNICE_DANE.md](docs/SIECHNICE_DANE.md).

## Testy

Sprawdzają gwarancje z [docs/FLOW_MAP_CONTRACT.md](docs/FLOW_MAP_CONTRACT.md)
na syntetycznych danych (bez SQLite, bez zależności od daty) - patrz ten
plik po opis, co dokładnie który test sprawdza.

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -v
```

## Konwencja - branche
- dla każdej funkcji tworzymy indywidualne branche w konwencji `username/feature`
- tworzymy pull reguesty do testing gdy zmiany są gotowe
- gdy zmiany są zebrane, przetestowane i gotowe to stworzenia następnego relase'a tworzymy pull request z `testing` do `main` 
