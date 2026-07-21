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
   `stop_times`, `calendar`, `shapes` — geometria tras po ulicach/torach…)
   i buduje `data/gtfs_new.sqlite`.
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
- **`bikes.py`** — analogiczny cache dla stacji roweru miejskiego WRM
  (feed GBFS zamiast SQLite) — patrz sekcja „Warstwa rowerowa (WRM)” niżej.
- **`routes.py`** — endpointy: `/` (strona), `/api/stops`, `/api/bikes`,
  `/api/plan`, `/api/flow` (szczegóły w sekcji API).

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
   przyjazd `earliest[s]` na każdy przystanek + dla każdego kursu miejsce,
   w którym najwcześniej da się do niego wsiąść.
2. **Deadline**: najlepszy przyjazd + 50% czasu podróży (min. 5, maks.
   30 minut). Wszystko, co dociera do celu po deadline, jest bezużyteczne.
3. **Skan wstecz** od celu: najpóźniejszy moment `latest[s]`, w którym można
   być na przystanku `s` i jeszcze zdążyć do celu przed deadline
   (połączenia przetwarzane malejąco po odjeździe).
4. **Jednostką rysowania jest kurs, nie pojedynczy przeskok.** Miejsce
   wsiadania to pierwszy przystanek kursu, na który zdążymy (z buforem
   przesiadki) i którego osiągnięcie **nie wymaga cofnięcia się** —
   oddalenia od celu o więcej niż 2 min (mierzone spadkiem `latest`
   względem startu). To ucina scenariusze "podjedź na pętlę i wracaj tym
   samym wozem". Od wsiadania idziemy wzdłuż kursu i szukamy **wyjść**:
   przystanków `s` o przyjeździe `arr`, gdzie `latest[s]` istnieje,
   `arr ≤ latest[s]` i jazda **przybliżyła** do celu
   (`latest[wyjście] > latest[wsiadanie]` — inaczej kurs jadący w złą
   stronę świeciłby pełną jasnością, bo powrót tym samym wozem daje
   ten sam czas co czekanie).
5. Rysujemy **jeden ciągły segment** od przystanku wsiadania do końca,
   który wyznacza pierwsza z reguł: (a) kurs dojechał do **celu** — cięcie
   dokładnie na celu (koniec z rysowaniem „za punkt docelowy i z powrotem");
   (b) jazda dalej pogarsza najlepszy możliwy przyjazd o ponad 3 minuty —
   cięcie ogona. Kurs bez żadnego użytecznego wyjścia nie jest rysowany wcale.
6. **Intensywność** jest jedna na cały segment i liczona per wyjście:
   wartość wyjścia to najlepszy osiągalny przyjazd do celu. Dla wyjścia
   na cel to po prostu przyjazd (dokładne); dla pozostałych liczymy przez
   KONKRETNE kontynuacje — najbliższy zdążalny odjazd segmentu, w który
   da się wskoczyć, plus najlepsze z jego wyjść ZA punktem wskoczenia
   (sufiks; wyjść sprzed dołączenia nie da się użyć). Punkt stały tej
   rekurencji startuje od segmentów kończących na celu. To omija błąd
   aproksymacji `deadline − latest`, która dla rzadko kursujących linii
   wlicza czekanie „do ostatniego kursu" i zaniżała jasność dowozów.
   Normalizacja: trasa optymalna 1,0, wariant na styk deadline 0,0.
7. **Próg jasności** (suwak w UI, 30–90%, domyślnie 60%): segmenty poniżej
   progu nie są wysyłane; odpowiedź ograniczona do 150 najjaśniejszych.
8. **Spójność sieci**: po odsianiu progiem każdy segment jest przycinany
   z obu stron do zakotwiczonych punktów — początek to start relacji albo
   miejsce, gdzie dołącza inny narysowany segment; koniec to cel albo
   ostatnia przesiadka w porównywalnie jasny (tolerancja 0,1) narysowany
   segment. Segment bez kotwic odpada; punkt stały iteruje, aż nic nie
   wypada. Efekt: żadna linia nie zaczyna się „znikąd" ani nie prowadzi
   „w powietrze", niezależnie od ustawienia suwaka.
9. **Margines przesiadki**: przy każdej kotwicy początku, która jest
   realną przesiadką (nie startem trasy), zapamiętujemy też zapas czasu
   ponad wymagany bufor (`TRANSFER_SEC` na tym samym słupku, `WALK_SEC`
   na sąsiednim) do najwcześniejszego odjazdu, w który jeszcze da się
   wskoczyć — `transfer_margin` w sekundach w odpowiedzi API (`null` przy
   starcie trasy, gdzie bufor nie ma zastosowania; przy remisie pozycji
   wsiadania wygrywa przesiadka z większym zapasem). Na razie to czysto
   rozkładowy zapas — dopiero dane GTFS-RT o opóźnieniach (patrz „Plan
   rozwoju” niżej) pokazałyby realny margines na żywo, a nie tylko
   teoretyczny z rozkładu.
7. **Agregacja**: segmenty o tej samej linii i identycznej ścieżce
   (kolejne kursy w oknie) sklejamy, biorąc maksimum jakości.
9. **Geometria**: ścieżka segmentu to fragment `shapes.txt` (realne ulice
   i tory) wycięty między przystankiem wsiadania a wysiadania — kolejne
   przystanki rzutowane monotonicznie na łamaną shape'a, potem uproszczenie
   ~11 m. Dopasowanie jest walidowane (końce wycinka ≤ ~280 m od
   przystanków, długość w granicach 0,85–3× łamanej po przystankach);
   przy niewiarygodnym dopasowaniu i przy braku shape'a fallbackiem jest
   łamana po przystankach. Wycinki są cache'owane w RAM per wersja bazy.

Dlaczego nie per przeskok? Pierwsza wersja filtrowała każdy przeskok A→B
niezależnie (`earliest[A] ≤ dep` i `arr ≤ latest[B]`). Problem: `latest[]`
nie jest monotoniczne wzdłuż linii (przystanek przed węzłem ma ciasny limit,
sam węzeł luźny), więc środkowe przeskoki kursu potrafiły wypaść z warunku,
choć wcześniejsze i późniejsze przechodziły — linia „mrugała" (dziury na
mostach, konfetti krótkich kresek), a fragmenty pojawiały się w miejscach,
do których nie dało się realnie dojechać z naszego startu.

Rendering (frontend):

- przezroczystość `0,10 + 0,85·w` i grubość `1 + 3,5·w` px — główne
  korytarze jaskrawe i grube, niszowe ledwo widoczne;
- kolor: tramwaj czerwony, autobus niebieski; segmenty z `w ≥ 0,45`
  dostają białą otoczkę (styl mapy tramwajowej), kolejność rysowania:
  blade → otoczki → jaskrawe;
- **hover na linii** podświetla ją, wyciąga na wierzch wiązki
  (`bringToFront`) i pokazuje dymek „Tramwaj 3" — tak rozróżnia się
  linie nachodzące na siebie w jednym korytarzu;
- plakietki z numerem linii na najjaśniejszym segmencie każdej linii
  (długie segmenty 2–3 plakietki), tylko dla linii z jakością ≥ 0,4;
- zwykłe markery przystanków są przygaszane na czas pokazywania przepływu;
- **margines przesiadki**: mała kropka na przystanku wsiadania w segment
  (gdy to realna przesiadka, nie start trasy) — kolor czerwony → zielony
  wg zapasu czasu (0 → 10 min), dymek „X min zapasu na przesiadkę”;
- kadr: najjaśniejsze segmenty (próg 0,7 → 0,45 → wszystko) + zawsze
  start i cel.

Koszt: dwa liniowe skany fragmentu tablicy + jedno przejście po oknie —
~30 ms na cache'owanym dniu, odpowiedź to zwykle kilkaset–2000 krawędzi.

## Warstwa rowerowa (WRM)

Stacje Wrocławskiego Roweru Miejskiego pokazane są na mapie jako osobna,
czysto informacyjna warstwa (checkbox w panelu, domyślnie zaznaczony) —
**nie wpływają na wyszukiwanie połączeń**, `planner.py` w ogóle o nich nie
wie. To celowo płytka integracja: pierwszy krok przed docelowym pomysłem,
w którym rower byłby pełnoprawną krawędzią w CSA (patrz „Plan rozwoju”).

- **Źródło danych**: WRM korzysta z systemu nextbike, który publikuje stan
  sieci jako feed [GBFS](https://gbfs.org/) (General Bikeshare Feed
  Specification) pod system-id `nextbike_pl` (potwierdzone przez
  `systems.csv` z oficjalnego repo GBFS). Ten jeden system obejmuje
  Wrocław **i** gminy ościenne włączone do tej samej sieci (Kobierzyce,
  Wisznia, Kąty Wrocławskie, Siechnice, Czernica) — wszystkie 272 stacje
  należą do WRM, więc nic nie trzeba filtrować po regionie.
- **Trzy pliki GBFS**, łączone w `bikes.py`: `station_information.json`
  (nazwa, współrzędne — zmienia się rzadko, cache 1 h), `station_status.json`
  (liczba wolnych rowerów + rozbicie na typy pojazdów, cache 60 s — tyle
  deklaruje sam feed w polu `ttl`) i `vehicle_types.json` (słownik typów
  pojazdów, cache 1 h) — ten ostatni służy wyłącznie do rozpoznania, które
  `vehicle_type_id` to rower elektryczny (`propulsion_type ==
  "electric_assist"`); nextbike ma kilka różnych modeli e-bike naraz,
  więc ID nie da się zahardkodować jedną liczbą.
- **Cache w pamięci procesu**, ten sam styl co `gtfs.py`: przy wygaśnięciu
  próbujemy odświeżyć, a błąd sieci zostawia stare dane (stacja
  „zestarzeje się” o ~1 min zamiast zniknąć); wyjątek propaguje się dalej
  tylko wtedy, gdy cache stacji/statusów jest jeszcze zupełnie pusty
  (pierwsze zapytanie po starcie serwera) — błąd samego `vehicle_types.json`
  nigdy nie jest fatalny, po prostu żadna stacja nie pokaże elektryków.
- **Frontend**: `GET /api/bikes` zwraca listę stacji, rysowaną jako
  `L.layerGroup` kółek — promień rośnie z liczbą dostępnych rowerów; stacja
  bez rowerów ma celowo inny styl (mała szara obwódka zamiast pełnej
  pomarańczowej kropki), żeby nie dało się jej pomylić z zajętą. Stacje
  z dostępnym rowerem elektrycznym dostają dodatkową małą plakietkę „⚡”.
  Podpowiedź na hover: „nazwa / X rowerów (w tym Y elektrycznych)”.
  Checkbox chowa/pokazuje warstwę bez ponownego pobierania danych.

## API

- `GET /api/stops` — wszystkie słupki: `[{name, lat, lon}, …]`.
- `GET /api/bikes` — stacje WRM z aktualną dostępnością:
  `[{name, lat, lon, bikes, electric}, …]` (`electric` = liczba dostępnych
  rowerów elektrycznych, podzbiór `bikes`). Błąd (feed GBFS niedostępny
  i cache jeszcze pusty) → `{error: "…"}` z kodem 503.
- `GET /api/plan?start=&end=&time=HH:MM` — jedna najszybsza trasa: etapy
  z godzinami, przystankami po drodze i współrzędnymi (`legs[].path`).
  Nieużywany obecnie przez UI, zostaje jako narzędzie/debug.
- `GET /api/flow?start=&end=&time=HH:MM&qmin=0.60` — mapa przepływów:
  `{start, end, departure, best_arrival, deadline, segments: [{path:
  [[lat,lon], …], num: "10", kind: "tram"|"bus"|"other", w: 0..1,
  transfer_margin: 120|null}, …]}`, segmenty posortowane rosnąco po `w`
  (kolejność rysowania); `path` to kolejne przystanki od wsiadania do
  ostatniego użytecznego wyjścia; `transfer_margin` to sekundy zapasu
  ponad wymagany bufor przesiadki przy wsiadaniu w ten segment, `null`
  gdy segment zaczyna się od startu trasy (patrz „Margines przesiadki”
  w sekcji Algorytmy).
- Błędy: `{error: "…", suggestions: […]}` — podpowiedzi przy literówce
  w nazwie przystanku.

## Struktura plików

| Plik | Rola |
|---|---|
| `update_gtfs.py` | pobranie GTFS + budowa SQLite + atomowa podmiana |
| `gtfs.py` | dostęp do bazy, cache dnia, dopasowanie nazw przystanków |
| `planner.py` | CSA (`plan_route`) + mapa przepływów (`plan_flow`) |
| `bikes.py` | cache stacji WRM (GBFS) |
| `routes.py` | endpointy Flaska |
| `app.py` | start aplikacji (port 5001) |
| `templates/index.html` | mapa Leaflet + panel + cały frontendowy JS |
| `static/style.css` | style panelu, plakietek linii itd. |
| `data/gtfs.sqlite` | baza rozkładów (poza gitem) |

## Changelog

- **2026-07-21** — margines przesiadki jako gradient (wersja rozkładowa):
  `plan_flow` zapamiętuje przy każdej realnej przesiadce zapas czasu ponad
  wymagany bufor (`transfer_margin` w `/api/flow`, sekundy, `null` na
  starcie trasy); frontend rysuje kolorową kropkę (czerwona → zielona,
  0–10 min) na przystanku wsiadania w segment, z tooltipem „X min zapasu”.
  Czysto rozkładowe na razie — nie uwzględnia bieżących opóźnień.
- **2026-07-21** — warstwa stacji WRM (rower miejski) na mapie: nowy moduł
  `bikes.py` (cache feedu GBFS `nextbike_pl` — `station_information.json`,
  `station_status.json`, `vehicle_types.json` do rozpoznania rowerów
  elektrycznych), endpoint `/api/bikes`, checkbox warstwy w panelu; puste
  stacje mają odrębny styl (szara obwódka), stacje z dostępnym e-bikiem
  dostają plakietkę „⚡”. Czysto informacyjne, `planner.py`/`gtfs.py` bez
  zmian. Pierwszy krok „Planu rozwoju” niżej.
- **2026-07-18** — spójna sieć przepływów + suwak czułości: jasność liczona
  per wyjście przez konkretne kontynuacje (sufiksy, punkt stały) zamiast
  samej aproksymacji `deadline − latest`; segmenty kotwiczone z obu stron
  (start relacji / widoczna przesiadka), więc nic nie wisi w powietrzu;
  luz 3 min w regule postępu (metryka latest bywa zaszumiona); suwak
  30–90% z przeładowaniem na żywo (`qmin` w API).
- **2026-07-18** — reguły postępu: wsiadanie nie może wymagać cofnięcia się
  o >2 min, a wyjście liczy się tylko, gdy jazda przybliża do celu (koniec
  z "podjedź na pętlę i wracaj"); limit z powrotem 1,5× (maks. +30 min),
  za to segmenty poniżej 20% jasności odpadają; hover wyciąga linię na
  wierzch wiązki.
- **2026-07-18** — poprawka dopasowania geometrii (znaleziona przez przegląd
  agentowy): przy wsiadaniu w środku kursu skan z wczesnym cięciem potrafił
  utknąć w fałszywym minimum (~4% wycinków z końcami setki metrów od
  przystanków); teraz podejrzane minimum wymusza doskanowanie do końca,
  a wynik przechodzi walidację końców i długości z fallbackiem na łamaną
  po przystankach. Do tego higiena cache (czyszczenie po podmianie bazy,
  częściowa ewikcja) i jedno połączenie DB na zapytanie o przepływy.
- **2026-07-17** — czytelność mapy przepływów: geometria z `shapes.txt`
  (linie po realnych ulicach/torach), ciaśniejszy limit (30%, 5–15 min),
  segment linii jadącej do celu ucinany dokładnie na celu, cięcie ogonów
  pogarszających wynik o >3 min, maks. 150 segmentów, biała otoczka
  jaskrawych linii, hover z numerem linii, kadr zawsze ze startem i celem.
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
- Margines przesiadki (kropka na mapie) jest czysto rozkładowy — nie
  wie nic o bieżących opóźnieniach, więc "12 min zapasu" to zapas wg
  rozkładu, nie licząc np. spóźnionego pierwszego kursu.
- Kafelki mapy i biblioteka Leaflet ładowane z internetu (CDN) — a od
  warstwy WRM także **backend** potrzebuje internetu (feed GBFS nextbike);
  wcześniej tylko frontend zależał od sieci.

## Plan rozwoju (uzgodniona kolejność)

Rozszerzenia do zrobienia po kolei, jedno na raz — każde kończone
w całości (i zweryfikowane w przeglądarce) zanim zaczyna się kolejne.
Kolejność ustalona z użytkownikiem 2026-07-21 (kolejność wklejenia pomysłu,
niezależna od trudności zadania czy numerków przy nim). Po zrobieniu kroku:
odhaczyć tutaj i dopisać wpis w Changelogu, żeby plan został aktualny
między sesjami (mogą dzielić je dni).

- [x] **Warstwa stacji WRM na mapie** — informacyjna, bez wpływu na
      wyszukiwanie. Zrobione 2026-07-21 (patrz Changelog i sekcja
      „Warstwa rowerowa (WRM)” wyżej).
- [x] **Margines przesiadki jako gradient**, nie próg zero-jedynkowy —
      zrobione 2026-07-21 jako wersja **rozkładowa** (statyczna): kropka
      na przystanku wsiadania, kolor + tooltip „X min zapasu”. Docelowo
      (z danymi GTFS-RT o opóźnieniach — patrz punkt niżej) margines
      pokazywałby realny zapas na żywo, nie tylko teoretyczny z rozkładu —
      to zostaje jako naturalne rozszerzenie, gdy/jeśli feed RT się
      potwierdzi.
- [ ] **Tablica odjazdów per przystanek** — klik w przystanek pokazuje
      też „co i kiedy stąd odjeżdża najbliżej”, nie tylko ustawia
      start/cel. Dane (`stop_times` per `stop_id`) już są w bazie.
- [ ] **Chodzenie pieszo jako samodzielna opcja**, nie tylko przesiadka
      między słupkami o tej samej nazwie (`WALK_SEC`) — dojście między
      dowolnymi bliskimi punktami, ograniczone promieniem (żeby nie
      rozsadzić liczby połączeń rozważanych przez CSA).
- [ ] **Rzeczywista lokalizacja użytkownika** (`navigator.geolocation`) +
      „ostatnia mila” piesza do/ze stacji/przystanku (haversine + założona
      prędkość marszu, bez zewnętrznego routingu na start).
- [ ] **Rower jako krawędź w CSA** (głęboka integracja) — `plan_flow` /
      `plan_route` proponują „idź do stacji X, jedź rowerem do Y” jako
      opcję obok tramwaju/autobusu. Nowy, czasowo zmienny typ połączenia
      w skanie — trudność wyższa niż reszta.
- [ ] **Car-sharing (Traficar) jako warstwa** — auta Traficar na mapie
      (pozycja, paliwo/zasięg, dostępność), ten sam wzorzec co warstwa WRM
      (poller + `L.layerGroup`). Traficar sam nie ma publicznego API, ale
      `fioletowe.live` (open source, GitHub `divadsn/traficar-map`, GPLv3)
      republikuje jego wewnętrzne API jako udokumentowany REST/JSON bez
      klucza (`/docs/`, `/api/openapi.json`): `GET /api/v1/zones`
      (Wrocław = zoneId 3), `GET /api/v1/cars?zoneId=3`, `GET
      /api/v1/cars/nearby?lat=&lng=&radius=` (promień od razu w API —
      przydatne pod „najbliższy Traficar do celu”). Zastrzeżenie: to
      strona trzecia, nie sam Traficar — może zniknąć bez ostrzeżenia,
      jak feed niżej.
- [ ] **Prawdziwy GTFS-Realtime (opóźnienia na żywo) dla MPK** — feed
      protobuf `https://mapadlugoleka.klosok.eu/vehicle_positions.pb`,
      zarejestrowany w oficjalnym rejestrze odt.org.pl jako GTFS-RT dla
      MPK Wrocław (dodany 19.09.2025). To **nie** jest portal miejski —
      strona trzeciej osoby (wygląda na projekt jednoosobowy — realne
      ryzyko, że zniknie bez ostrzeżenia). Poszlaka, że feed działa i ma
      opóźnienia: `czynaczas.pl` pokazuje po WebSocket payload zgodny
      1:1 z GTFS-RT (`current_status`, `delay`, `trip_id`, `stop_id`…).
      Gdyby się potwierdziło, odblokowuje krok „Margines przesiadki jako
      gradient” wyżej — bez opóźnień na żywo margines jest tylko
      rozkładowy, statyczny.

## Pomysły na dalej

- Dymki na węzłach przesiadkowych: „w co mogę się tu przesiąść i o której".
- Powrót klasycznego widoku jednej trasy jako przełącznika obok przepływów.
- Suwak zakresu (1,5× / 2× / 3×) w panelu.
