# Metal-Planner — dokumentacja projektu

Mini-wiki: co to jest, jak jest zbudowane, jak działają algorytmy i co się
zmieniało. Instrukcja uruchomienia jest w [README.md](README.md).

## O projekcie

Webowa wyszukiwarka połączeń komunikacji miejskiej Wrocławia (MPK: autobusy
i tramwaje). Zamiast pokazywać jedną wyliczoną trasę, aplikacja pokazuje
**mapę przepływów** („symulację mrówek"): wszystkie linie, które prowadzą
w stronę celu, z intensywnością zależną od tego, jak dobre są — główne
korytarze jaskrawe, niszowe objazdy ledwo widoczne. Użytkownik widzi
możliwości i sam wybiera.

## Architektura

Trzy warstwy:

### 1. Pipeline danych — `update_gtfs.py`

Uruchamiany ręcznie albo z crona (nie przez Flaska). Kolejno:

1. Odpytuje [portal Otwartych Danych Wrocławia](https://open-data.cui.wroclaw.pl/hdb/metadane/13/)
   o listę paczek GTFS i wybiera najnowszą, która **już obowiązuje**
   (portal wystawia też paczki z przyszłą datą startu — te pomijamy).
2. Pobiera zip (~12 MB), parsuje pliki CSV (`stops`, `routes`, `trips`,
   `stop_times`, `calendar`…) i buduje `data/gtfs_new.sqlite`.
3. Atomowo podmienia bazę (`os.replace`) na `data/gtfs.sqlite` — działająca
   aplikacja nigdy nie widzi wpół zapisanego pliku, a gdy pobieranie padnie,
   wczorajsza baza zostaje nietknięta.

### 2. Backend — Flask

- **`gtfs.py`** — dostęp do SQLite. Przy pierwszym zapytaniu danego dnia
  wyznacza kursujące tego dnia kursy (logika `calendar.txt`), buduje w RAM
  tablicę ~1 mln „połączeń" (pojedynczych przejazdów między sąsiednimi
  przystankami, posortowanych po odjeździe) i cache'uje ją. Klucz cache
  zawiera mtime pliku bazy, więc po nocnej podmianie dane przeładują się
  same — bez restartu Flaska.
- **`planner.py`** — dwa algorytmy na tej samej tablicy połączeń:
  `plan_route` (jedna najszybsza trasa, CSA) i `plan_flow` (mapa przepływów) —
  opis niżej.
- **`routes.py`** — endpointy: `/` (strona), `/api/stops`, `/api/plan`,
  `/api/flow` (szczegóły w sekcji API).

### 3. Frontend — `templates/index.html`

Jedna strona: pełnoekranowa mapa Leaflet (kafelki OpenStreetMap — wymaga
internetu), wszystkie słupki jako markery na canvasie, chowany panel boczny
(przycisk ☰). Klik 1 = start (zielony), klik 2 = cel (czerwony) i wyszukiwanie
odpala się samo. Wynik jest **wyłącznie graficzny** — mapa przepływów, bez
tekstowej listy etapów. Czysty JS bez frameworka.

## Algorytmy

### CSA — Connection Scan Algorithm (`plan_route`)

Nie budujemy grafu. „Połączenie" to jeden przejazd między dwoma sąsiednimi
przystankami konkretnego kursu. Wszystkie połączenia dnia leżą w tablicy
posortowanej po czasie odjazdu; jeden liniowy skan od godziny odjazdu
wystarczy, by policzyć najwcześniejszy przyjazd wszędzie:

- do połączenia można „wsiąść", jeśli już siedzimy w tym kursie, albo jesteśmy
  na jego przystanku odpowiednio wcześnie (bufor przesiadki 2 min; start
  i dojście piesze bez bufora);
- słupki o tej samej nazwie przystanku traktujemy jak jeden węzeł połączony
  przejściem 3 min;
- kursy po północy mają w GTFS godziny 24:xx+ i „po prostu działają";
- trasę odtwarzamy wstecz po zapisanych wskaźnikach (które połączenie
  poprawiło który przystanek).

Pierwsze zapytanie dnia kosztuje ~1 s (ładowanie tablicy do RAM),
kolejne są natychmiastowe (~30 ms).

### Mapa przepływów / „symulacja mrówek" (`plan_flow`)

Cel: pokazać **wszystkie** użyteczne opcje naraz, z intensywnością malejącą
od najlepszych do ledwo sensownych. Nie symulujemy dosłownie agentów —
ten sam efekt daje analiza dwóch skanów:

1. **Skan w przód** od przystanku startowego: najwcześniejszy możliwy
   przyjazd `earliest[s]` na każdy przystanek.
2. **Deadline**: czas najszybszej trasy × 1,5 (min. +5 minut). Wszystko,
   co dociera do celu po deadline, uznajemy za bezużyteczne.
3. **Skan wstecz** od celu: najpóźniejszy moment `latest[s]`, w którym można
   być na przystanku `s` i jeszcze zdążyć do celu przed deadline
   (połączenia przetwarzane malejąco po odjeździe).
4. **Jednostką rysowania jest kurs, nie pojedynczy przeskok.** Dla każdego
   kursu, do którego skan w przód znalazł wsiadanie (`trip_board[kurs]` =
   pierwsze połączenie, na które zdążymy z naszego startu, z buforem
   przesiadki), idziemy wzdłuż kursu i szukamy **wyjść**: przystanków `s`
   o przyjeździe `arr`, gdzie `latest[s]` istnieje i `arr ≤ latest[s]`
   (stąd wciąż da się dojechać do celu przed deadline).
5. Rysujemy **jeden ciągły segment** od przystanku wsiadania do OSTATNIEGO
   użytecznego wyjścia — nic przed miejscem, gdzie realnie można wsiąść,
   nic za miejscem, za którym kurs przestaje pomagać. Kurs bez żadnego
   użytecznego wyjścia nie jest rysowany wcale.
6. **Intensywność** jest jedna na cały segment: największy zapas
   `latest[s] − arr` po wszystkich wyjściach, znormalizowany tak, że trasa
   optymalna ma 1,0, a wariant „na styk przed deadline" 0,0.
7. **Agregacja**: segmenty o tej samej linii i identycznej ścieżce
   (kolejne kursy w oknie) sklejamy, biorąc maksimum jakości.

Dlaczego nie per przeskok? Pierwsza wersja filtrowała każdy przeskok A→B
niezależnie (`earliest[A] ≤ dep` i `arr ≤ latest[B]`). Problem: `latest[]`
nie jest monotoniczne wzdłuż linii (przystanek przed węzłem ma ciasny limit,
sam węzeł luźny), więc środkowe przeskoki kursu potrafiły wypaść z warunku,
choć wcześniejsze i późniejsze przechodziły — linia „mrugała" (dziury na
mostach, konfetti krótkich kresek), a fragmenty pojawiały się w miejscach,
do których nie dało się realnie dojechać z naszego startu.

Rendering (frontend):

- przezroczystość `0,10 + 0,80·w` i grubość `1,5 + 4,5·w` px — główne
  korytarze jaskrawe i grube, niszowe ledwo widoczne;
- kolor: tramwaj czerwony, autobus niebieski;
- plakietki z numerem linii na najjaśniejszym odcinku każdej linii
  (dłuższe linie dostają 2–3 plakietki), tylko dla linii z jakością ≥ 0,35;
- zwykłe markery przystanków są przygaszane na czas pokazywania przepływu;
- kadr dopasowuje się do najjaśniejszych krawędzi (próg 0,75 → 0,5 → wszystko).

Koszt: dwa liniowe skany fragmentu tablicy + jedno przejście po oknie —
~30 ms na cache'owanym dniu, odpowiedź to zwykle kilkaset–2000 krawędzi.

## API

- `GET /api/stops` — wszystkie słupki: `[{name, lat, lon}, …]`.
- `GET /api/plan?start=&end=&time=HH:MM` — jedna najszybsza trasa: etapy
  z godzinami, przystankami po drodze i współrzędnymi (`legs[].path`).
  Nieużywany obecnie przez UI, zostaje jako narzędzie/debug.
- `GET /api/flow?start=&end=&time=HH:MM` — mapa przepływów:
  `{start, end, departure, best_arrival, deadline, segments: [{path:
  [[lat,lon], …], num: "10", kind: "tram"|"bus"|"other", w: 0..1}, …]}`,
  segmenty posortowane rosnąco po `w` (kolejność rysowania); `path` to
  kolejne przystanki od wsiadania do ostatniego użytecznego wyjścia.
- Błędy: `{error: "…", suggestions: […]}` — podpowiedzi przy literówce
  w nazwie przystanku.

## Struktura plików

| Plik | Rola |
|---|---|
| `update_gtfs.py` | pobranie GTFS + budowa SQLite + atomowa podmiana |
| `gtfs.py` | dostęp do bazy, cache dnia, dopasowanie nazw przystanków |
| `planner.py` | CSA (`plan_route`) + mapa przepływów (`plan_flow`) |
| `routes.py` | endpointy Flaska |
| `app.py` | start aplikacji (port 5001) |
| `templates/index.html` | mapa Leaflet + panel + cały frontendowy JS |
| `static/style.css` | style panelu, plakietek linii itd. |
| `data/gtfs.sqlite` | baza rozkładów (poza gitem) |

## Changelog

- **2026-07-17** — przepływy per kurs zamiast per przeskok: ciągłe segmenty
  od wsiadania do ostatniego użytecznego wyjścia (koniec „mrugających" linii
  i fragmentów nieosiągalnych ze startu); jedna intensywność na segment;
  podświetlenie startu/celu działa też przy ręcznym wpisaniu nazw.
- **2026-07-16** — tryb „mrówkowy": mapa przepływów zastępuje pojedynczą
  trasę; skan wstecz, `/api/flow`, plakietki linii, przygaszanie przystanków.
- **2026-07-16** — interaktywna mapa (Leaflet): wybór przystanków
  kliknięciem, trasa rysowana na mapie, panel boczny, `/api/stops` + `/api/plan`.
- **2026-07-16** — pipeline GTFS (portal open data → SQLite z atomową
  podmianą) + planer CSA z przesiadkami i kursami po północy; venv
  z Pythonem 3.11 (systemowy 3.8 jest za stary dla Flask 3.x).
- **2026-07-14** — szkielet aplikacji Flask (formularz + zaślepka planera).
- **2026-07-11** — start repozytorium.

## Znane ograniczenia

- Intensywność w trybie przepływów to przybliżenie (zapas czasu najlepszego
  wyjścia względem deadline) — bywa, że rzadko kursująca, ale dobra linia
  wyjdzie bledsza, niż powinna.
- Segment pokazuje też objazdy „w bok", jeśli mieszczą się w limicie 1,5× —
  to celowe (niszowe opcje mają być widoczne), ale przy szerokim limicie
  bywa tego sporo; ewentualny suwak zakresu jest na liście pomysłów.
- Bufor przesiadki w skanie wstecz jest stosowany jednolicie (2 min),
  nieco ostrożniej niż w skanie w przód.
- Wyszukiwanie działa w ramach jednej doby rozkładowej: zapytanie o 0:30
  nie widzi końcówek wczorajszych kursów (24:xx widać wieczorem).
- Brak tras pieszych po mieście — przesiadka tylko między słupkami
  o identycznej nazwie przystanku.
- Linie rysowane po prostej między przystankami (nie po torach/ulicach) —
  dokładna geometria wymagałaby wczytania `shapes.txt`.
- Kafelki mapy i biblioteka Leaflet ładowane z internetu (CDN).

## Pomysły na dalej

- Geometria tras z `shapes.txt` (linie po torach/ulicach).
- Dymki na węzłach przesiadkowych: „w co mogę się tu przesiąść i o której".
- Powrót klasycznego widoku jednej trasy jako przełącznika obok przepływów.
- Suwak zakresu (1,5× / 2× / 3×) w panelu.
- GTFS-RT: opóźnienia i pozycje pojazdów na żywo (portal je udostępnia).
