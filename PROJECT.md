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
  przystankami, posortowanych po odjeździe) i cache'uje ją. Tam samo liczy
  piesich sąsiadów każdego przystanku (patrz „Piesi sąsiedzi przystanku"
  w Algorytmach) - kubełkowanie zamiast pełnego porównania każdy-z-każdym,
  bo przy ~2500 słupkach to byłoby ~6 mln par. `nearest_stops(lat, lon, …)`
  robi to samo dla DOWOLNEGO punktu (np. lokalizacji użytkownika) - liniowy
  skan, bo to jedno zapytanie naraz, nie wszystkie pary. Klucz cache
  zawiera mtime pliku bazy, więc po nocnej podmianie dane przeładują się
  same — bez restartu Flaska.
- **`planner.py`** — dwa algorytmy na tej samej tablicy połączeń:
  `plan_route` (jedna najszybsza trasa, CSA) i `plan_flow` (mapa przepływów) —
  opis niżej.
- **`bikes.py`** — analogiczny cache dla stacji roweru miejskiego WRM
  (feed GBFS zamiast SQLite) — patrz sekcja „Warstwa rowerowa (WRM)” niżej.
- **`bike_transfer.py`** — rower WRM jako transfer w `plan_flow` (nie tylko
  warstwa informacyjna) - statyczna geometria (przystanek↔stacja,
  stacja↔stacja) cache'owana jak `day.siblings`, dostępność sprawdzana na
  żywo per zapytanie; patrz „Warstwa rowerowa (WRM)” niżej.
- **`routes.py`** — endpointy: `/` (strona), `/api/stops`, `/api/bikes`,
  `/api/departures`, `/api/plan`, `/api/flow` (szczegóły w sekcji API).

### 3. Frontend — `templates/index.html`

Jedna strona: pełnoekranowa mapa Leaflet (kafelki OpenStreetMap — wymaga
internetu), wszystkie słupki jako markery na canvasie, chowany panel boczny
(przycisk ☰). Klik 1 = start (zielony), klik 2 = cel (czerwony) i wyszukiwanie
odpala się samo. Wynik jest **wyłącznie graficzny** — mapa przepływów, bez
tekstowej listy etapów. Czysty JS bez frameworka.

Przycisk „Użyj mojej lokalizacji" (`navigator.geolocation.getCurrentPosition`,
jednorazowo — to jednorazowe wyszukiwanie, nie ciągłe śledzenie, więc
`watchPosition` nie jest potrzebny) ustawia prawdziwy punkt GPS jako start
zamiast nazwy przystanku: fioletowy marker na mapie, pole „Przystanek
początkowy" pokazuje „Twoja lokalizacja", zapytanie idzie do `/api/flow`
jako `start_lat`/`start_lon` zamiast `start`. Wymaga zgody przeglądarki
(i HTTPS na produkcji — `localhost` jest zwolniony z tego wymogu, patrz
„Znane ograniczenia"); odmowa/brak/timeout pokazują czytelny komunikat
zamiast się wywalać. Ręczne wpisanie nazwy albo klik w przystanek na
mapie czyści lokalizację i wraca do zwykłego wyszukiwania po nazwie.

Przycisk **ukryty w UI** (`display:none`) od 2026-07-21 — funkcjonalność
zostaje w kodzie, ale czeka na pomysł „tryb planowania vs tryb podróży"
(patrz „Plan rozwoju”), zanim wróci widoczny.

## Algorytmy

### CSA — Connection Scan Algorithm (`plan_route`)

Nie budujemy grafu. „Połączenie" to jeden przejazd między dwoma sąsiednimi
przystankami konkretnego kursu. Wszystkie połączenia dnia leżą w tablicy
posortowanej po czasie odjazdu; jeden liniowy skan od godziny odjazdu
wystarczy, by policzyć najwcześniejszy przyjazd wszędzie:

- do połączenia można „wsiąść", jeśli już siedzimy w tym kursie, albo jesteśmy
  na jego przystanku odpowiednio wcześnie (bufor przesiadki 2 min; start
  i dojście piesze bez bufora);
- **piesi sąsiedzi przystanku** (`gtfs.py`, `data.siblings`) - dwa źródła
  scalone w jedną relację `stop_id -> [(sąsiad, sek_dojścia), …]`: słupki
  o tej samej nazwie (stały bufor 3 min - to więcej niż geometria, bo
  zwykle trzeba przejść na drugą stronę torów/ulicy) oraz DOWOLNE inne
  przystanki w promieniu 400 m (haversine / ~4,7 km/h, min. 60 s) - „po
  prostu idź" zamiast tylko przesiadki między bliźniaczymi słupkami.
  Jeden skok pieszy na raz (bez łańcuchów A→pieszo→B→pieszo→C), stosowany
  identycznie na starcie, w środku trasy i na końcu;
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
   samym wozem" - ale NIE dotyczy słupków samego punktu startowego (patrz
   Changelog 2026-07-21): tam nie ma z czego się cofać, tylko wybór między
   kilkoma słupkami tego samego miejsca. Od wsiadania idziemy wzdłuż kursu
   i szukamy **wyjść**:
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
   z obu stron do zakotwiczonych punktów — początek to start relacji **albo
   jego pieszy sąsiad** (`start_walkable` - dojście na piechotę z punktu
   startowego liczy się jak bycie na miejscu; przy starcie z prawdziwej
   lokalizacji, patrz Frontend, to najbliższe przystanki z
   `gtfs.nearest_stops`, nie słupki dopasowanej nazwy), albo miejsce, gdzie dołącza
   inny narysowany segment; koniec to cel **lub jego pieszy sąsiad**
   (`target_walkable`, symetrycznie) albo ostatnia przesiadka w porównywalnie
   jasny (tolerancja 0,1) narysowany segment. Segment bez kotwic odpada;
   punkt stały iteruje, aż nic nie wypada. Efekt: żadna linia nie zaczyna
   się „znikąd" ani nie prowadzi „w powietrze", niezależnie od ustawienia
   suwaka — i trasy, które wymagają dojścia pieszo do/od prawdziwego
   punktu startu/celu, też się pokazują.
9. **Margines przesiadki**: przy każdej kotwicy początku, która jest
   realną przesiadką (nie startem trasy), zapamiętujemy też zapas czasu
   ponad wymagany bufor (`TRANSFER_SEC` na tym samym słupku, indywidualny
   czas dojścia na sąsiedni - patrz „Piesi sąsiedzi przystanku" wyżej) do
   najwcześniejszego odjazdu, w który jeszcze da się wskoczyć —
   `transfer_margin` w sekundach w odpowiedzi API, plus `board_time`
   (godzina tego odjazdu) i `board_stop` (nazwa przystanku przesiadki) -
   frontend grupuje po `board_stop`, żeby jedna kropka pokazywała WSZYSTKIE
   linie odjeżdżające z tego węzła w bieżącej mapie przepływów, nie tylko tę
   jedną (patrz Rendering niżej). Wszystkie trzy pola `null` przy starcie
   trasy, gdzie bufor nie ma zastosowania; przy remisie pozycji wsiadania
   wygrywa przesiadka z większym zapasem. Na razie to czysto rozkładowy
   zapas — dopiero dane GTFS-RT o opóźnieniach (patrz „Plan rozwoju” niżej)
   pokazałyby realny margines na żywo, a nie tylko teoretyczny z rozkładu.
10. **Piesze odcinki jako segmenty**: dojście pieszo, które kotwiczy
    start/koniec segmentu (do/od prawdziwego startu, celu, albo między
    dwoma segmentami przy przesiadce - wszystkie trzy przypadki z punktów
    8/9) jest teraz NARYSOWANE, nie tylko policzone wewnętrznie - osobny
    segment `kind:"walk"` z własną ścieżką (`path`, dwa punkty: skąd, dokąd)
    i `walk_sec`. Bez tego linia transportu, do której trzeba dojść pieszo,
    „zaczynała się znikąd" na mapie. Zduplikowane dojścia (używane przez
    kilka segmentów) rysowane raz, jasnością najjaśniejszego z nich.
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

### Rower WRM jako transfer (`bike_transfer.py`)

Rower jest teraz PEŁNOPRAWNYM transferem w `plan_flow` - jak piesi sąsiedzi
(patrz CSA wyżej), nie tylko informacyjną warstwą na mapie (patrz „Warstwa
rowerowa (WRM)” niżej). Różnica od pieszych sąsiadów: dostępność (rower na
stacji startowej, wolny dok na docelowej) zmienia się z minuty na minutę,
więc nie da się jej prekomputować raz razem z dniem rozkładu jak
`day.siblings`. Rozwiązanie - dwie warstwy cache'owania:

1. **Geometria (statyczna, cache'owana)**: dla każdego przystanku - do 2
   najbliższych stacji WRM (promień 400 m, jak piesi sąsiedzi); dla każdej
   stacji - do 6 najbliższych INNYCH stacji w rozsądnym zasięgu roweru
   (4 km, ~15 km/h) i do 3 najbliższych przystanków. Liczone raz i
   cache'owane, dopóki pozycje stacji się nie zmienią (`bikes.
   stations_generation()`, ta sama koncepcja co `gtfs.geo_generation()`).
2. **Dostępność (żywa, cache 60 s)**: `bikes.station_availability()` -
   `num_bikes_available`/`num_docks_available` z feedu GBFS (`is_renting`/
   `is_returning` na False liczy się jak zero). Dopiero WYNIK tego
   sprawdzenia, scalony z geometrią, daje gotowe krawędzie przystanek→
   przystanek (`build_bike_edges`) w kształcie identycznym jak
   `day.siblings` - `plan_flow` scala je (`merge_siblings`) w jedną relację
   i przekazuje do `_scan`/`_forward`/`_backward` przez opcjonalny parametr
   `siblings` (domyślnie `None` = czysto piesza `day.siblings`, więc
   `plan_route` i istniejące wywołania bez roweru zachowują się identycznie).

**Kierunkowość** - kluczowa różnica od pieszych sąsiadów, którzy są zawsze
symetryczni (blisko A ⇔ blisko B): dostępność roweru NIE jest symetryczna
(stacja A ma rower ≠ stacja B ma rower). `build_bike_edges` zwraca więc
DWIE relacje - `edges` („dokąd stąd można dojechać”, dla `_scan`/`_forward`
i kotwicy początku segmentu) i `reverse_edges` („kto stąd dotrze do celu”,
dla `_backward` i dojścia DO celu) - użycie tej samej relacji w obu
miejscach dawałoby błędny kierunek dla asymetrycznych krawędzi rowerowych
(dla pieszych siblings to bez różnicy, bo są symetryczne).

**Wydajność**: doprecyzowanie jasności (`join_value`/`candidates_at`,
wołane setki tysięcy razy w typowym zapytaniu) i kotwica końca segmentu
(przesiadka na inny narysowany segment) celowo NIE widzą roweru - używają
czystej `day.siblings`. Rower ma dużo szerszy zasięg (4 km vs 400 m), więc
wpuszczenie go tam powiększało koszt kombinatorycznie (zmierzone: ~3× na
typowym zapytaniu) bez odpowiadającej korzyści - to tylko doprecyzowanie
JUŻ narysowanych segmentów, nie decyzja, czy dana trasa w ogóle się pokaże
(o tym decydują `_scan`/`_forward`/`_backward` i kotwica POCZĄTKU, które
znają rower). Rendering: dojście stop→stacja→stacja→stop rysowane jak
dojście pieszo (prosta linia, nie prawdziwa trasa rowerowa), ale osobnym
`kind:"bike"` (pomarańcz, kropkowana kreska) z dymkiem „Rower WRM: stacja
X → stacja Y”; to samo uproszczenie co przy pieszych dojściach (patrz
„Znane ograniczenia”).

Rendering (frontend):

- przezroczystość `0,10 + 0,85·w` i grubość `1 + 3,5·w` px — główne
  korytarze jaskrawe i grube, niszowe ledwo widoczne;
- kolor: tramwaj czerwony, autobus niebieski, pieszo szary, rower
  pomarańcz (ten sam co warstwa stacji WRM - spójność „to jest rower”
  niezależnie od warstwy); pieszo i rower przerywaną kreską (linia prosta,
  nie prawdziwa trasa uliczna/rowerowa - przerywanie od razu to
  sygnalizuje), różny wzór (pieszo `4,6`, rower kropkowane `1,6`) - odróżnia
  się nawet bez najeżdżania; segmenty z `w ≥ 0,45` dostają białą otoczkę
  (styl mapy tramwajowej), kolejność rysowania: blade → otoczki → jaskrawe;
- **hover na linii** podświetla ją, wyciąga na wierzch wiązki
  (`bringToFront`) i pokazuje dymek „Tramwaj 3” (albo „Pieszo, ok. X min”
  dla dojścia, albo „Rower WRM: stacja X → stacja Y” dla przejazdu) — tak
  rozróżnia się linie nachodzące na siebie w jednym korytarzu;
- plakietki z numerem linii na najjaśniejszym segmencie każdej linii
  (długie segmenty 2–3 plakietki), tylko dla linii z jakością ≥ 0,4;
  segmenty piesze nie dostają plakietki (pusty `num`);
- zwykłe markery przystanków są przygaszane na czas pokazywania przepływu;
- **margines przesiadki**: jedna kropka PER PRZYSTANEK przesiadkowy (nie
  per linia — segmenty z tym samym `board_stop` grupowane w jedną),
  większa niż zwykły marker i kolorowana bursztyn → zielony wg zapasu
  czasu (0 → 10 min) — **celowo nie czerwono-zielony**, bo czerwień miesza
  się z kolorem linii tramwajowej. Dymek po najechaniu to mini-tablica
  odjazdów ograniczona do linii faktycznie widocznych na aktualnej mapie:
  godzina + numer linii + własny kolorowy „chip” z zapasem dla każdej;
- kadr: najjaśniejsze segmenty (próg 0,7 → 0,45 → wszystko) + zawsze
  start i cel.

Koszt: dwa liniowe skany fragmentu tablicy + jedno przejście po oknie —
~30 ms na cache'owanym dniu, odpowiedź to zwykle kilkaset–2000 krawędzi.
Dla słabo skomunikowanego celu (bardzo rzadkie kursy) okno `[dep_sec,
deadline]` potrafi rozciągnąć się na godziny, a z nim liczba segmentów w
tysiące - `KEPT_CAP` (400) ucina wtedy do najjaśniejszych PRZED drogimi
pętlami (doprecyzowanie jasności, spójność sieci), żeby zapytanie nie
liczyło się dziesiątkami sekund (patrz Changelog i „Znane ograniczenia”).

## Warstwa rowerowa (WRM)

Stacje Wrocławskiego Roweru Miejskiego pokazane są na mapie jako osobna
warstwa (checkbox w panelu, domyślnie zaznaczony) - czysto wizualna, ale
od 2026-07-22 **rower jest też pełnoprawnym transferem w `plan_flow`**
(patrz „Rower WRM jako transfer” w Algorytmach) - `planner.py` bierze pod
uwagę tę samą dostępność, którą widać na tej warstwie, przy wyszukiwaniu
połączeń, nie tylko przy rysowaniu stacji. Włączenie/wyłączenie checkboxa
zmienia wyłącznie WIDOCZNOŚĆ warstwy stacji - nie wyłącza roweru jako
transferu w wynikach wyszukiwania (to świadome rozdzielenie: warstwa to
podgląd stanu sieci, transfer to osobna decyzja algorytmu).

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
- **Transfer** (`bike_transfer.py`, wołany z `plan_flow`): `bikes.
  station_positions()`/`station_availability()` (ID, lokalizacja, wolne
  rowery/doki - osobno od `station_list()` używanego przez warstwę
  wyżej, bo transfer potrzebuje ID i doków, warstwa tylko nazwy/liczb
  do wyświetlenia) zasilają statyczną geometrię i żywe sprawdzenie
  dostępności - szczegóły w „Rower WRM jako transfer” w Algorytmach.

## Warstwa car-sharing (Traficar)

Auta Traficar na mapie - **czysto informacyjna warstwa**, ten sam wzorzec co
WRM wyżej (checkbox w panelu, domyślnie zaznaczony, `L.layerGroup` kółek,
chowanie bez ponownego pobierania). W odróżnieniu od WRM **nie jest** (i nie
jest planowana jako) transfer w `plan_flow` - to celowo płytka integracja,
bo Traficar to auta na minuty/kilometry (nie stacje z siecią pieszych/
rowerowych dojść jak WRM), więc sensowna integracja z CSA wymagałaby zupełnie
innego modelu (koszt, nie tylko czas) - poza obecnym zakresem.

- **Źródło danych**: Traficar sam nie publikuje żadnego oficjalnego API.
  [`fioletowe.live`](https://fioletowe.live) (open source, GitHub
  `divadsn/traficar-map`, licencja GPLv3) republikuje jego wewnętrzne API
  jako udokumentowany REST/JSON bez klucza (`/docs/`, `/api/openapi.json`).
  **To strona trzecia, nie sam Traficar** - może zniknąć albo zmienić
  kształt bez ostrzeżenia (to samo zastrzeżenie co przy GTFS-Realtime niżej).
  Potwierdzone na żywo przed implementacją: `GET /api/v1/zones` zwraca
  Wrocław jako `zoneId=3`; `GET /api/v1/cars?zoneId=3` zwraca ~90-100 aut
  z polami `lat`/`lng` (stringi!), `location`, `regPlate`, `fuel` (% baku),
  `range` (km), `available` (bool). Feed deklaruje `Cache-Control:
  max-age=12` (własny cache po stronie fioletowe.live) - `traficar.py`
  odpytuje rzadziej (cache 20 s), żeby nie nadużywać cudzego serwisu.
- **`traficar.py`**: ten sam styl cache'owania co `bikes.py` - błąd sieci
  zostawia stare dane, wyjątek (`TraficarDataError`) propaguje się tylko przy
  zupełnie pustym cache'u (pierwsze zapytanie po starcie serwera).
- **Frontend**: `GET /api/traficar` zwraca listę aut, rysowaną jako
  `L.layerGroup` kółek w fiolecie marki (kolor z nazwy źródła danych);
  wynajęte auto (`available: false`) ma przygaszony, szary styl - nie da się
  go pomylić z wolnym, ten sam pomysł co puste stacje WRM. Dymek: numer
  rejestracyjny + (dla wolnych) % paliwa i zasięg w km, albo „obecnie
  wynajęte” dla zajętych.

## API

- `GET /api/stops` — wszystkie słupki: `[{name, lat, lon}, …]`.
- `GET /api/bikes` — stacje WRM z aktualną dostępnością:
  `[{name, lat, lon, bikes, electric}, …]` (`electric` = liczba dostępnych
  rowerów elektrycznych, podzbiór `bikes`). Błąd (feed GBFS niedostępny
  i cache jeszcze pusty) → `{error: "…"}` z kodem 503.
- `GET /api/traficar` — auta Traficar we Wrocławiu:
  `[{lat, lon, fuel, range, plate, available}, …]` (`fuel` = % baku,
  `range` = zasięg w km, `available` = czy wolne do wynajęcia). Błąd (API
  fioletowe.live niedostępne i cache jeszcze pusty) → `{error: "…"}`
  z kodem 503.
- `GET /api/departures?stop=&time=HH:MM` — tablica odjazdów: najbliższe
  odjazdy z przystanku (wszystkich jego słupków) od podanej godziny,
  `{stop, departures: [{time, line, kind, headsign}, …]}` (maks. 24,
  najbliższy pierwszy). Czysto rozkładowe - bez statusu „na czas/opóźniony”
  (patrz „Znane ograniczenia”).
- `GET /api/plan?start=&end=&time=HH:MM` — jedna najszybsza trasa: etapy
  z godzinami, przystankami po drodze i współrzędnymi (`legs[].path`).
  Nieużywany obecnie przez UI, zostaje jako narzędzie/debug.
- `GET /api/flow?start=&end=&time=HH:MM&qmin=0.60` — mapa przepływów:
  `{start, end, departure, best_arrival, deadline, segments: [{path:
  [[lat,lon], …], num: "10", kind: "tram"|"bus"|"walk"|"bike"|"other",
  w: 0..1, transfer_margin: 120|null, board_time: "18:36"|null, board_stop:
  "Pułaskiego"|null, walk_sec: 180|null, bike_stations: ["Stacja A",
  "Stacja B"]|null}, …]}`, segmenty posortowane rosnąco po `w` (kolejność
  rysowania); `path` to kolejne przystanki od wsiadania do ostatniego
  użytecznego wyjścia (dla `kind:"walk"`/`"bike"` - tylko dwa punkty, skąd
  i dokąd). `transfer_margin`/`board_time`/`board_stop` to razem: ile
  sekund zapasu, o której odjeżdża connection, i nazwa przystanku
  przesiadki - wszystkie `null`, gdy segment zaczyna się od startu trasy
  (patrz „Margines przesiadki” w Algorytmach). `walk_sec` to czas dojścia
  w sekundach dla `kind:"walk"` ALBO całkowity czas (dojście+rower+dojście)
  dla `kind:"bike"` (patrz „Piesze odcinki jako segmenty” i „Rower WRM jako
  transfer”); `bike_stations` to nazwy stacji początkowej/końcowej, tylko
  dla `kind:"bike"`. Zamiast `start=` można podać `start_lat=&start_lon=`
  (prawdziwa lokalizacja zamiast nazwy przystanku - patrz Frontend); wtedy
  `start` w odpowiedzi to zawsze „Twoja lokalizacja”. Błąd, gdy w promieniu
  1 km nie ma żadnego przystanku: `{error: "…"}`.
- Błędy: `{error: "…", suggestions: […]}` — podpowiedzi przy literówce
  w nazwie przystanku.

## Struktura plików

| Plik | Rola |
|---|---|
| `update_gtfs.py` | pobranie GTFS + budowa SQLite + atomowa podmiana |
| `gtfs.py` | dostęp do bazy, cache dnia, dopasowanie nazw przystanków |
| `planner.py` | CSA (`plan_route`) + mapa przepływów (`plan_flow`) |
| `bikes.py` | cache stacji WRM (GBFS) |
| `bike_transfer.py` | rower WRM jako transfer w `plan_flow` (geometria statyczna + dostępność na żywo) |
| `traficar.py` | cache aut car-sharing Traficar (przez fioletowe.live) |
| `routes.py` | endpointy Flaska |
| `app.py` | start aplikacji (port 5001) |
| `templates/index.html` | mapa Leaflet + panel + cały frontendowy JS |
| `static/style.css` | style panelu, plakietek linii itd. |
| `data/gtfs.sqlite` | baza rozkładów (poza gitem) |

## Changelog

- **2026-07-22** — zweryfikowany i ODRZUCONY pomysł „prawdziwy GTFS-Realtime
  dla MPK” (ostatni punkt „Planu rozwoju”): feed
  `mapadlugoleka.klosok.eu/vehicle_positions.pb` jest żywy i ma poprawny
  kształt GTFS-RT (sparsowany biblioteką `gtfs-realtime-bindings`), ale
  0/8 aktualizacji kursu ma `trip_id`, 0/8 ma pole `delay`, 0/8 ma
  prawdziwy `stop_id` (wszystkie: placeholder „Niewybrany przystanek”) -
  bezużyteczny do dopasowania z naszym rozkładem i policzenia opóźnienia,
  mimo poprawnej struktury. Do tego certyfikat TLS serwera jest źle
  skonfigurowany (brak pośredniego certyfikatu). Szczegóły i uzasadnienie
  w „Plan rozwoju” niżej - zapisane, żeby nikt nie sprawdzał tego samego
  od zera. Biblioteka `gtfs-realtime-bindings` (instalowana do weryfikacji)
  odinstalowana z powrotem - nieużywana w kodzie.
- **2026-07-22** — warstwa car-sharing Traficar na mapie: nowy moduł
  `traficar.py` (cache aut z `fioletowe.live`, republikującego wewnętrzne
  API Traficara - strona trzecia, zweryfikowana na żywo przed
  implementacją: Wrocław to `zoneId=3`, ~90-100 aut na raz), endpoint
  `/api/traficar`, checkbox warstwy w panelu (domyślnie zaznaczony); auta
  wynajęte (`available: false`) mają przygaszony styl, ten sam pomysł co
  puste stacje WRM. Czysto informacyjne, jak WRM przed integracją z
  `plan_flow` - `planner.py` o autach nie wie i nie będzie (Traficar to
  koszt za minutę/km, nie sieć stacji z dojściami jak WRM, więc sensowna
  integracja z CSA wymagałaby innego modelu niż transfer czasowy).
- **2026-07-22** — rower WRM jako pełnoprawny transfer w `plan_flow` (item 6
  „Planu rozwoju”, przeprojektowany po cofnięciu pierwszej wersji-mostu
  2026-07-21 - patrz ten wpis niżej). Nowy moduł `bike_transfer.py`: statyczna
  geometria (przystanek↔stacja, stacja↔stacja) cache'owana jak
  `day.siblings`, dostępność (`bikes.station_availability()` - nowa funkcja,
  zwraca ID/doki, w odróżnieniu od `station_list()` dla warstwy
  informacyjnej) sprawdzana na żywo (cache 60 s) przy KAŻDYM zapytaniu i
  scalana z pieszymi sąsiadami w jedną relację, przekazywaną do
  `_scan`/`_forward`/`_backward` przez nowy opcjonalny parametr `siblings`
  (domyślnie `None` = bez zmian, `plan_route` nieporuszony). Dostępność
  roweru jest KIERUNKOWA (stacja A ma rower ≠ stacja B ma rower) - w
  odróżnieniu od zawsze symetrycznych pieszych sąsiadów - stąd osobna
  relacja odwrotna (`reverse_siblings`) dla `_backward`/dojścia do celu, żeby
  nie pomylić kierunku (patrz „Rower WRM jako transfer” w Algorytmach).
  Rendering: nowy `kind:"bike"` (pomarańcz, kropkowana kreska), dymek ze
  stacjami. Feed GBFS niedostępny → zwykli piesi sąsiedzi, wyszukiwanie nie
  pada (zweryfikowane: symulacja padniętego feedu nadal zwraca poprawną
  trasę, tylko bez segmentów rowerowych).

  **Przy okazji, dwa poważne błędy znalezione podczas weryfikacji:**
  1. Doprecyzowanie jasności (`join_value`/`candidates_at`, setki tysięcy
     wywołań na typowe zapytanie) spowalniało się ~3× przy włączeniu roweru
     do tej samej relacji sąsiedztwa - rower ma dużo szerszy zasięg (4 km)
     niż piesi sąsiedzi (400 m), więc rozgałęzienie rosło kombinatorycznie
     bez odpowiadającej korzyści (to tylko doprecyzowanie JUŻ narysowanych
     segmentów). Naprawiono: te dwie funkcje i kotwica końca segmentu celowo
     zostały przy czystej `day.siblings` - tylko `_scan`/`_forward`/
     `_backward` i kotwica POCZĄTKU (gdzie decyduje się, czy dana trasa
     w ogóle się pokaże) znają rower.
  2. Znacznie poważniejszy, PRZEDISTNIEJĄCY (niezwiązany z rowerem) błąd
     wydajności znaleziony przy okazji testów regresji: kotwica początku
     segmentu robiła pełny skan wszystkich wyjść wszystkich innych segmentów
     OSOBNO dla każdego segmentu - O(liczba segmentów²) × wyjścia × sąsiedzi.
     Dla dobrze skomunikowanych par przystanków (mało segmentów) niezauważalne,
     ale dla słabo skomunikowanego celu (bardzo rzadkie kursy → okno czasu
     `[dep_sec, deadline]` na GODZINY → tysiące segmentów) zapytanie
     `Chińska → Biskupice Podg. DSC Poland/Top Run Poland` (17:30) liczyło
     się **33 sekundy** (z rowerem: nie skończyło się w rozsądnym czasie w
     ogóle). Naprawiono dwiema poprawkami: (a) indeks „skąd da się dojść do
     stopu X” budowany RAZ na iterację pętli spójności, nie osobno dla
     każdego segmentu - usuwa czynnik kwadratowy; (b) `KEPT_CAP` (400) -
     twardy limit liczby segmentów wchodzących w drogie pętle
     (doprecyzowanie + spójność), ucinający do najjaśniejszych PRZED nimi,
     nie po. Efekt: ten sam przykład **4,3 s** zamiast 33 s+ - wciąż wolniej
     niż typowe zapytanie (~30–200 ms), ale ograniczone, nie rozjeżdżające
     się bez końca; `best_arrival` (z osobnego, nieograniczonego skanu)
     pozostaje dokładny niezależnie od cięcia. Test regresji: >60 zapytań
     (ustalony zestaw + losowe próbki, różne pory dnia) - zero przypadków
     "poprawny best_arrival, zero segmentów" i zero zapytań wolniejszych niż
     kilka sekund poza tym jednym, ekstremalnym przykładem.
- **2026-07-21** — poważniejszy, osobny błąd znaleziony przy weryfikacji
  poprawek niżej: reguła "bez cofania się" (`BACKTRACK_TOL_SEC`) potrafiła
  wyciąć z mapy przepływów CAŁĄ, poprawną, bezpośrednią trasę - nie tylko
  osłabić jej jasność. `origin_latest` to MAKSIMUM `latest[]` po WSZYSTKICH
  słupkach przystanku startowego, ale każdy kurs wsiada z JEDNEGO, konkretnego
  słupka - gdy ten akurat miał `latest[]` gorszy o >2 min od NAJLEPSZEGO
  słupka startu (typowe przy węźle z kilkoma niezależnymi liniami, patrz
  przykład niżej), reguła traktowała to jak "cofnięcie się" i odrzucała kurs
  w całości, mimo że to dosłownie pierwszy przystanek - nie ma z czego się
  cofać. Znalezione przez przypadek przy teście regresji: zapytanie
  Broniewskiego → FAT zwracało **0 segmentów** mimo poprawnego
  `best_arrival` (14:34, `/api/plan` znajdował trasę 129→607 przez Kwiskę
  bez trudu) - okazało się, że kurs 129 wsiada ze słupka o `latest[]`=14:06,
  a najlepszy słupek Broniewskiego (inna, niepowiązana linia) ma
  `latest[]`=14:09 - różnica 3 min > próg 2 min, więc CAŁY kurs 129 (a z nim
  cała trasa) znikał z wyników. Naprawiono: reguła nie dotyczy już słupków
  należących do prawdziwego punktu startowego (`origin_stops` - nazwane
  przystanki i/lub „ostatnia mila" z prawdziwej lokalizacji) - tam nie ma
  pojęcia cofania się, tylko wybór MIĘDZY słupkami tego samego miejsca.
  Zweryfikowane: to samo zapytanie teraz zwraca 14 segmentów z pełną trasą
  129→607 (jasność 1,0); dodatkowo automatyczny test 30 losowych par
  przystanek→przystanek (dwie różne pory dnia) nie znalazł ani jednego
  przypadku "poprawny `best_arrival`, zero pasujących segmentów" - wcześniej
  nie sprawdzone systematycznie, więc nieznana skala problemu, ale biorąc
  pod uwagę, że dotyczy KAŻDEGO węzła z ≥2 niezależnymi liniami i realnie
  różnym `latest[]` między słupkami, było to prawdopodobnie częste.
- **2026-07-21** — dwa błędy w kotwiczeniu segmentów mapy przepływów,
  zgłoszone po żywym użyciu ("każe wysiąść wcześniej i iść pieszo, choć
  dało się dojechać wprost", "chodzenie z przypadkowych, niepowiązanych
  punktów", "kropki przesiadek czasem nie pokazują się wcale"). Oba miały
  ten sam kształt: pętla kotwicząca segment WYBIERAŁA KANDYDATA PO KOLEJNOŚCI
  ITERACJI zamiast po jakości, więc gorszy kandydat znaleziony później
  bezwarunkowo nadpisywał lepszego znalezionego wcześniej:
  1. **Kotwica POCZĄTKU** (gdzie realnie wsiada się w segment) wybierała
     najwcześniejszą pozycję w trasie (`p < start_pos`) NIEZALEŻNIE OD TEGO,
     czy wymagała chodzenia. Efekt: gdy dało się dojechać WPROST (bez
     chodzenia) do przystanku, na którym dany kurs i tak się zatrzymuje,
     algorytm i tak potrafił wybrać wcześniejszą, ale pieszą alternatywę,
     bo to dawało formalnie "więcej narysowanego odcinka". Naprawiono:
     priorytet najpierw brak chodzenia, dopiero POTEM najwcześniejsza
     pozycja, dopiero potem zapas czasu.
  2. **Kotwica KOŃCA** (gdzie segment przestaje być rysowany) nadpisywała
     punkt cięcia i dojście pieszo do celu przy KAŻDYM kolejnym pasującym
     wyjściu, bez porównania jakości - kto ostatni w pętli po wyjściach
     kursu, ten wygrywał. Kurs, który dojechał DOKŁADNIE do celu, potrafił
     zostać "przedłużony" za cel do gorszej, dalszej przesiadki albo do
     przypadkowego pieszego sąsiada celu napotkanego później na trasie -
     to właśnie wyglądało jak "dojście z przypadkowego, niepowiązanego
     punktu". Naprawiono systemem priorytetów (tier): dotarcie na cel BEZ
     chodzenia > dotarcie na cel PIESZO > przesiadka na inny narysowany
     segment; w obrębie tego samego priorytetu wygrywa dalsza pozycja (jak
     dotąd - więcej odcinka pokazane).
  Przy okazji (3): dojścia pieszo do rysowania grupowane teraz PO NAZWACH
  przystanków, nie po dokładnych współrzędnych słupków - duży węzeł (patrz
  „8 Maja” niżej, węzeł z 6 słupkami) potrafił wygenerować 2-3 prawie
  równoległe kreski między tymi samymi dwoma miejscami (różne słupki tej
  samej nazwy na obu końcach); teraz rysuje się jedna, najjaśniejsza.
  Zweryfikowane na żywych przykładach (serwer deweloperski, zapytania przez
  `/api/flow`): Leśnica-Hermes→Psie Pole spadło z 82 do 27 segmentów pieszych
  (206→113 segmentów łącznie), Dworzec Główny (MDK)→8 Maja z 17 do 11
  (44→32 łącznie) - w obu przypadkach `best_arrival`/`deadline` (a więc sama
  najszybsza trasa) bez zmian, zmieniła się tylko jakość dodatkowych
  segmentów mapy przepływów. Sprawdzone też wizualnie w przeglądarce - linie
  i kropki przesiadek renderują się poprawnie, bez błędów w konsoli.
- **2026-07-21** — poprawka reguły postępu (`PROGRESS_TOL_SEC`) w kotwicy
  końca: dotąd sprawdzana tylko względem NATURALNEGO wsiadania kursu
  (ustalonego przy budowie `raw[]`), więc gdy segment kotwiczono gdzie
  indziej (np. przez pieszy "most" od innego segmentu), późniejsze wyjścia
  mogły jechać w złą stronę bez wykrycia - żaden z nich nie był
  rewalidowany względem FAKTYCZNEGO miejsca wsiadania. Teraz `latest[]`
  każdego kandydata na cięcie jest porównywane z `latest[]` rzeczywistego
  punktu startu segmentu, nie tylko z jego oryginalnym. Znaleziono i
  zweryfikowano na żywym przykładzie (przystanek "8 Maja", węzeł z 6
  słupkami): kurs jadący w przeciwną stronę (Autobus 133 → BROCHÓW) był
  kotwiczony piechotą z zupełnie innego autobusu, mimo że w miejscu
  dojścia dostępny był bezpośrednio (bez chodzenia) kurs 133 w PRAWIDŁOWĄ
  stronę. Poprawka to zawęża, ale (patrz „Znane ograniczenia” niżej) nie
  usuwa w pełni na bardzo gęstych węzłach — potrzebna głębsza poprawka,
  która porównywałaby wariant pieszy z prostszą alternatywą "poczekaj w
  miejscu", nie tylko sprawdzała kierunek.
- **2026-07-21** — dwie poprawki po przeglądzie na żywo: (1) dojście pieszo,
  które kotwiczy segment (start/koniec trasy albo przesiadka - patrz punkty
  8/9/10 w Algorytmach), jest teraz RYSOWANE jako osobny segment `kind:
  "walk"`, nie tylko liczone wewnętrznie - wcześniej linia transportu,
  do której trzeba było dojść pieszo, „zaczynała się znikąd" na mapie;
  (2) kropki marginesu przesiadki zgrupowane PER PRZYSTANEK (jedna kropka
  na węzeł, nie jedna na linię), większe, i przekolorowane z czerwono-
  zielonego na bursztynowo-zielony (czerwień myliła się z kolorem linii
  tramwajowej) - dymek to teraz mini-tablica odjazdów ograniczona do linii
  widocznych na aktualnej mapie, z godziną i własnym zapasem dla każdej.
  `/api/flow` zyskało pola `board_time`/`board_stop`/`walk_sec`. Przy
  okazji: pierwsza wersja „rower jako krawędź w CSA" (prosty most start→
  cel) została zaimplementowana, ale COFNIĘTA (`git stash`) po przeglądzie
  - użytkownik chce szerszego zakresu (rower jako pełny transfer, nie
  tylko most), patrz „Plan rozwoju”.
- **2026-07-21** — prawdziwa lokalizacja użytkownika jako start: przycisk
  „Użyj mojej lokalizacji” (`navigator.geolocation.getCurrentPosition`,
  jednorazowo) + `gtfs.nearest_stops(lat, lon, …)` na „ostatnią milę” (do 5
  najbliższych przystanków w promieniu 1 km, haversine + prędkość marszu).
  `_scan`/`_forward`/`plan_flow` przyjmują teraz opcjonalny `source_walk`
  (lista `(stop_id, sek)` zamiast/obok nazwanych `source_stops`) - ten sam
  mechanizm co „ostatnia mila” między przystankami z poprzedniego kroku,
  tylko z punktu ad hoc zamiast z prekomputowanego `day.siblings`.
  `/api/flow` przyjmuje `start_lat`/`start_lon` zamiast `start`. Fioletowy
  marker na mapie; ręczne wpisanie nazwy albo klik w przystanek czyści
  lokalizację. Tylko start (patrz „Plan rozwoju” - cel świadomie pominięty).
- **2026-07-21** — chodzenie pieszo jako samodzielna opcja: `gtfs.py`
  liczy piesich sąsiadów każdego przystanku - ta sama nazwa (bufor stały,
  jak dotąd) ORAZ dowolny inny przystanek w promieniu 400 m (haversine +
  ~4,7 km/h, kubełkowanie zamiast n² porównań, ~2500 słupków). `siblings`
  niesie teraz `(sąsiad, sek_dojścia)` zamiast samego stop_id - `_scan`,
  `_forward`, `_backward`, `plan_flow` (`joins`/`join_value`/kotwice
  segmentów) czytają czas z krotki zamiast stałej `WALK_SEC`. Dodano
  jawną relaksację pieszą ze startu i do celu (poprzednio działała tylko
  "za darmo" dla tej samej nazwy, bo `match_stop` zwraca od razu wszystkie
  jej słupki) oraz `start_walkable`/`target_walkable` przy kotwiczeniu
  segmentów, żeby trasa wymagająca dojścia pieszo do/od prawdziwego
  punktu startu/celu nie odpadała jako "nieprzycumowana". Zweryfikowane:
  przypadek Katedra→pl. Grunwaldzki (dawniej 0 segmentów) teraz pokazuje
  realną opcję; czas odpowiedzi bez zmian (~30 ms na cache'owanym dniu).
- **2026-07-21** — tablica odjazdów per przystanek: `stop_departures` w
  `planner.py` (skan `day.conns` od podanej godziny, filtr po `stop_id`),
  endpoint `/api/departures`, prawy klik na przystanek otwiera dymek
  z najbliższymi 24 odjazdami (godzina, linia, kierunek). Niezależne od
  lewego kliknięcia (start/cel) - osobne zdarzenie Leaflet (`contextmenu`).
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
- Na bardzo gęstych węzłach (przystanek z wieloma słupkami i dziesiątkami
  linii, np. duże skrzyżowanie/pętla) algorytm czasem i tak pokazuje
  przejście piesze na kurs, który wymaga więcej zachodu niż prostsza
  alternatywa "poczekaj w TYM SAMYM miejscu na kolejny, późniejszy odjazd
  tej czy innej linii" — poprawki kotwiczenia wyżej (2026-07-21) eliminują
  chodzenie tam, gdzie dało się dojechać wprost (bez wysiadania) do tego
  samego przystanku, ale nie porównują wariantu pieszego z „w ogóle nie
  ruszaj się, poczekaj" - to inny, trudniejszy przypadek (wymaga wiedzieć,
  co jeszcze odjeżdża z miejsca, w którym się już jest, nie tylko co da się
  osiągnąć pieszo) i zostaje odłożony na później.
- Wyszukiwanie działa w ramach jednej doby rozkładowej: zapytanie o 0:30
  nie widzi końcówek wczorajszych kursów (24:xx widać wieczorem).
- Dojście pieszo (patrz „Piesi sąsiedzi przystanku" w Algorytmach) to
  jeden skok naraz, prosta linia (haversine), stała prędkość marszu —
  nie prawdziwa sieć ulic pieszych (chodniki, przejścia, zakazy). Dobre
  jako "czy w ogóle warto rozważyć dojście", nie jako dokładna nawigacja.
  Promień 400 m / ~4,7 km/h to arbitralne, rozsądne wartości - nie z badania
  zachowań użytkowników. To samo ograniczenie dotyczy „ostatniej mili"
  z prawdziwej lokalizacji (promień 1 km).
- „Użyj mojej lokalizacji" wymaga bezpiecznego kontekstu przeglądarki
  (HTTPS) na produkcji — na `localhost` działa bez tego (zwolnione ze
  specyfikacji), ale wdrożenie na zwykłym HTTP straciłoby tę funkcję
  (przeglądarka po cichu odrzuci `getCurrentPosition`).
- Margines przesiadki (kropka na mapie) jest czysto rozkładowy — nie
  wie nic o bieżących opóźnieniach, więc "12 min zapasu" to zapas wg
  rozkładu, nie licząc np. spóźnionego pierwszego kursu.
- Tablica odjazdów per przystanek pokazuje tylko rozkład — bez statusu
  „na czas / X min opóźnienia” (to samo ograniczenie co wyżej: wymaga
  danych GTFS-RT, których na razie nie ma).
- Kafelki mapy i biblioteka Leaflet ładowane z internetu (CDN) — a od
  warstwy WRM także **backend** potrzebuje internetu (feed GBFS nextbike);
  wcześniej tylko frontend zależał od sieci.
- Rower jako transfer (patrz „Rower WRM jako transfer” w Algorytmach) sprawdza
  tylko TOP-K najbliższych stacji/sąsiadów na każdym poziomie (2 stacje per
  przystanek, 6 sąsiednich stacji per stacja, 3 przystanki per stacja) - to
  nie jest wyczerpujące przeszukanie wszystkich możliwych kombinacji, więc
  teoretycznie może pominąć jakiś rzadki, ale lepszy wariant. Dostępność ma
  do 60 s opóźnienia (cache feedu) - rower pokazany jako dostępny mógł już
  zniknąć. Cięcie na 4 km jazdy - dłuższe przejazdy rowerem między stacjami
  nie są brane pod uwagę (założenie: powyżej tego dystansu transport
  publiczny prawie zawsze wygrywa). Rysowanie to prosta linia
  przystanek→przystanek (jak piesze dojścia), nie prawdziwa trasa uliczna
  ani sieć dróg rowerowych - to samo uproszczenie i ta sama motywacja co
  przy pieszych dojściach.
- Zapytania o cel bardzo słabo skomunikowany transportem publicznym (bardzo
  rzadkie kursy, okno czasu na godziny) są ograniczone przez `KEPT_CAP`
  (patrz Changelog 2026-07-22) - mogą liczyć się do kilku sekund zamiast
  typowych dziesiątek/setek milisekund, i w skrajnym przypadku mapa
  przepływów może pominąć jakiś marginalny wariant, który przy pełnym
  (nieograniczonym) przeliczeniu zmieściłby się w progu jasności.
  `best_arrival`/`deadline` (najszybsza trasa) są zawsze dokładne, niezależnie
  od tego ograniczenia - dotyczy tylko DODATKOWYCH segmentów mapy przepływów.

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
- [x] **Tablica odjazdów per przystanek** — zrobione 2026-07-21: prawy
      klik na przystanek (lewy nadal ustawia start/cel — osobne akcje,
      bez konfliktu) otwiera dymek z najbliższymi odjazdami (`stop_times`
      już w bazie, nic nowego nie trzeba było pobierać). Status „na
      czas/opóźniony” zostaje na później — patrz „Znane ograniczenia”.
- [x] **Chodzenie pieszo jako samodzielna opcja** — zrobione 2026-07-21:
      `gtfs.py` liczy piesich sąsiadów każdego przystanku (ta sama nazwa +
      dowolny inny w promieniu 400 m, kubełkowanie zamiast n² porównań);
      cały CSA (`_scan`/`_forward`/`_backward`/`plan_flow`) czyta czas
      dojścia z tej samej relacji zamiast stałej `WALK_SEC`, więc "po
      prostu idź" działa jednolicie na starcie, w środku trasy i na końcu.
- [x] **Rzeczywista lokalizacja użytkownika** — zrobione 2026-07-21:
      przycisk „Użyj mojej lokalizacji" (`navigator.geolocation`,
      jednorazowo) + `gtfs.nearest_stops` na „ostatnią milę” (haversine +
      założona prędkość marszu, bez zewnętrznego routingu). Tylko start,
      nie cel - "gdzie stoję → cel" było jedynym artykułowanym przypadkiem
      użycia; geolokalizacja celu (dokąd idę → gdzie akurat jestem) to
      rzadki, odwrotny scenariusz, świadomie pominięty.
- [x] **Rower jako krawędź w CSA** (głęboka integracja) — zrobione
      2026-07-22 jako pełny transfer w `plan_flow` (nie tylko most
      start→cel jak pierwsza, cofnięta wersja z 2026-07-21): statyczna
      geometria cache'owana, dostępność sprawdzana na żywo (patrz „Rower
      WRM jako transfer” w Algorytmach i sekcja „Warstwa rowerowa (WRM)”).
      `plan_route` świadomie NIE dostał tej integracji (narzędzie debug,
      nieużywane przez UI - patrz API) - zakres ograniczony do `plan_flow`,
      który jest jedyną ścieżką faktycznie widoczną dla użytkownika.
- [x] **Car-sharing (Traficar) jako warstwa** — zrobione 2026-07-22: auta
      Traficar na mapie (pozycja, paliwo/zasięg, dostępność) przez
      `fioletowe.live` (patrz „Warstwa car-sharing (Traficar)” wyżej),
      ten sam wzorzec co warstwa WRM. `GET /api/v1/cars/nearby?lat=&lng=
      &radius=` (promień od razu w API) NIE wykorzystane - niepotrzebne
      przy obecnym zakresie (cała lista Wrocławia to tylko ~100 aut,
      filtrowanie po stronie klienta by starczyło, gdyby było potrzebne).
- [x] **Prawdziwy GTFS-Realtime (opóźnienia na żywo) dla MPK** —
      ZWERYFIKOWANE i ODRZUCONE 2026-07-22 (item nie zniknął z listy, bo
      to wciąż wartościowy wynik - żeby nikt później nie sprawdzał tego
      samego od zera). Feed protobuf
      `https://mapadlugoleka.klosok.eu/vehicle_positions.pb` (strona
      trzeciej osoby, **nie** portal miejski - wygląda na projekt
      jednoosobowy) faktycznie ODPOWIADA i faktycznie jest ŻYWY (znacznik
      czasu w nagłówku ~2 min od chwili sprawdzenia) - ale po pobraniu
      i sparsowaniu (biblioteka `gtfs-realtime-bindings`, poprawny
      protobuf `FeedMessage`) okazuje się bezużyteczny do celu, dla
      którego był rozważany:
      - **0 z 8** aktualizacji kursu (`trip_update`) ma wypełnione
        `trip.trip_id` - bez tego nie da się dopasować wpisu z feedu do
        KONKRETNEGO kursu w naszym rozkładzie GTFS (statycznym), czyli
        nie da się policzyć, o ile kurs jest spóźniony względem rozkładu;
      - **0 z 8** ma jakiekolwiek pole `delay` (ani `arrival.delay`, ani
        `departure.delay`) - feed nie publikuje opóźnień, mimo że pole
        istnieje w schemacie GTFS-RT;
      - **0 z 8** ma prawdziwy `stop_id` - wszystkie mają dosłowny tekst
        `"Niewybrany przystanek"` („nie wybrano przystanku") - stub/
        placeholder, nie prawdziwe dane;
      - tylko **8 pojazdów** widocznych na raz (całe MPK Wrocław ma w
        ruchu w danej chwili setki), a prędkości pojazdów wyglądają na
        niewiarygodne (4 różne pojazdy z DOKŁADNIE tą samą prędkością
        „18”, jeden z prędkością „59893” - fizycznie niemożliwe dla
        autobusu/tramwaju);
      - do tego: certyfikat TLS serwera jest źle skonfigurowany (brak
        pośredniego certyfikatu w łańcuchu - `openssl s_client` zwraca
        `unable to verify the first certificate` niezależnie od klienta) -
        kolejna, niezależna poszlaka niskiej jakości utrzymania tego
        źródła, zgodna z podejrzeniem „projekt jednoosobowy, realne
        ryzyko zniknięcia” wyżej.
      Wniosek: feed ma POPRAWNY KSZTAŁT GTFS-RT, ale w praktyce nie niesie
      żadnej z informacji (opóźnienie, dopasowanie do kursu), których
      potrzebowałby krok „Margines przesiadki jako gradient” do przejścia
      z wersji rozkładowej na żywą - inwestowanie w integrację byłoby
      pracą bez efektu na obecnym stanie tego źródła. Gdyby kiedyś
      pojawił się INNY, faktycznie działający feed GTFS-RT dla MPK
      Wrocław, ta ocena dotyczy tylko TEGO konkretnego adresu, nie
      pomysłu jako takiego.

## Pomysły na dalej

- Dymki na węzłach przesiadkowych: „w co mogę się tu przesiąść i o której".
- Powrót klasycznego widoku jednej trasy jako przełącznika obok przepływów.
- Suwak zakresu (1,5× / 2× / 3×) w panelu.
