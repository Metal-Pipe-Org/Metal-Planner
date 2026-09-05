# Metal-Planner — dokumentacja projektu

Mini-wiki: co to jest, jak jest zbudowane, jak działają algorytmy i co się
zmieniało. Instrukcja uruchomienia jest w [README.md](../README.md); gwarancje
zachowania mapy przepływów i testy, które je pilnują, są w
[FLOW_MAP_CONTRACT.md](FLOW_MAP_CONTRACT.md).

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
3. Dokleja do niej rozkład gminy Siechnice (`siechnice.py`), jeśli jest
   włączony — patrz niżej.
4. Atomowo podmienia bazę (`os.replace`) na `data/gtfs.sqlite` — działająca
   aplikacja nigdy nie widzi wpół zapisanego pliku, a gdy pobieranie padnie,
   wczorajsza baza zostaje nietknięta.

#### Drugie źródło — `siechnice.py`

Autobusów gminy Siechnice nie ma w żadnym otwartym zbiorze: ani we
wrocławskim GTFS, ani na dane.gov.pl, ani w Krajowym Punkcie Dostępowym.
Jedyne strukturalne źródło to niedokumentowane API systemu kiedyPrzyjedzie,
z którego `siechnice.py` składa kompletne kursy: odjazdy o tym samym
`trip_id`, ułożone po `index`, to jeden przejazd — czyli dokładnie `trip`
+ `stop_times`. Numer linii wychodzi z przecięcia zbiorów linii obsługujących
kolejne słupki kursu.

Słupki wspólne z Wrocławiem (Bardzka, Sucha, Iwiny — tamtędy jadą 800/810)
sklejają się z istniejącymi po **zgodnej nazwie i bliskości**, więc kurs
z Siechnic wjeżdża na ten sam `stop_id` co tramwaj: przesiadka bez kary za
przejście i jeden marker na mapie zamiast dwóch.

Każda pobrana data dostaje własny `service_id` i wpis w `calendar_dates`
(`exception_type=1`), bo API oddaje rozkład per konkretny dzień — kalendarz
odtworzony z reguły tygodniowej byłby zgadywaniem.

Całość jest **domyślnie wyłączona** (`SIECHNICE_ENABLED=on` włącza):
`robots.txt` tego serwisu to `Disallow: /` i nie ma tam regulaminu ani zgody
na ponowne wykorzystanie. Awaria tego kroku nie przerywa aktualizacji —
rozkład Wrocławia wjeżdża na miejsce niezależnie. Rozpoznanie źródeł, powody
i wzór pisma do gminy o eksport GTFS: [SIECHNICE_DANE.md](SIECHNICE_DANE.md).

Ma trzy punkty wejścia: wywołanie ręczne (albo z crona), start serwera
i codzienny harmonogram. Ten ostatni to `start_daily_scheduler()` — wątek
uruchamiany z hooka `on_starting` w `gunicorn.conf.py` (proces master, przed
forkiem workerów, więc jeden na kontener) i z `app.py` przy starcie lokalnym.
O godzinie z `GTFS_AUTO_UPDATE_HOUR` odpala `update_gtfs.py` jako podproces —
`fork+exec` zamiast wątku, bo budowa SQLite w wątku procesu, który forkuje
workery, to prosta droga do zakleszczenia świeżo zforkowanego dziecka.
Bez harmonogramu kontener stojący dłużej niż okno ważności paczki (~3 tygodnie)
przestałby znajdować jakiekolwiek kursy.

Odpala się też sam przy starcie serwera — bez bazy blokująco (nie ma czego
serwować), z bazą w tle, więc serwer wstaje od razu na starych danych,
a atomowa podmiana + mtime w kluczu cache'a w `gtfs.py` sprawiają, że świeży
rozkład wchodzi bez restartu. Dwie ścieżki, bo dwa punkty wejścia:
`docker/entrypoint.sh` (kontener, przed gunicornem) i `refresh_on_start()`
wywołane z `app.py` (uruchomienie lokalne). Lokalna dodatkowo pomija bazę
młodszą niż `GTFS_MAX_AGE_HOURS` (12 h) — reloader Flaska restartuje serwer
po każdym zapisie pliku. Wyłącznik obu: `GTFS_UPDATE_ON_START=off`.

### 2. Backend — Flask

- **`gtfs.py`** — dostęp do SQLite. Przy pierwszym zapytaniu danego dnia
  wyznacza kursujące tego dnia kursy (logika `calendar.txt`), buduje w RAM
  tablicę ~1 mln „połączeń" (pojedynczych przejazdów między sąsiednimi
  przystankami, posortowanych po odjeździe) i cache'uje ją. Klucz cache
  zawiera mtime pliku bazy, więc po nocnej podmianie dane przeładują się
  same — bez restartu Flaska.
- **`planner.py`** — dwa algorytmy na tej samej tablicy połączeń:
  `plan_route` (jedna najszybsza trasa, CSA) i `plan_flow` (mapa przepływów
  — zwraca teraz w jednej odpowiedzi i segmenty do rysowania, i listę
  gotowych propozycji, czytaną wprost z tego samego grafu) — opis niżej.
- **`timetables.py`** — drugi tryb aplikacji: rozkład linii i tablica
  odjazdów z przystanku. Rozkład linii idzie wprost z SQLite (kursy doby
  rozkładowej, grupowane po ciągu przystanków), tablica przystanku — z tej
  samej tablicy połączeń dnia co planer, bo odjazd z przystanku to po
  prostu połączenie, które się w nim zaczyna.
- **`routes.py`** — endpointy: `/` (strona), `/api/stops`, `/api/plan`,
  `/api/flow`, `/api/line`, `/api/stop_board`, `/api/trip` (szczegóły
  w sekcji API).

### 3. Frontend — `templates/index.html` + `static/app.js` + `static/style.css`

Jedna strona: pełnoekranowa mapa Leaflet (kafelki OpenStreetMap — wymaga
internetu), wszystkie słupki jako markery na canvasie, panel boczny z lewej
(chowany przyciskiem ☰). Klik 1 = start (zielony), klik 2 = cel (czerwony)
i wyszukiwanie odpala się samo; można też wpisać nazwy ręcznie. Klikanie
uzupełnia tylko brakujący koniec relacji — **gotowego wyszukiwania nie
kasuje żaden klik w mapę, tylko przycisk ✕**, żeby przypadkowe kliknięcie
nie zabrało wyników sprzed chwili.

W polu „skąd" siedzi przycisk **◎ — moja lokalizacja** (Geolocation API
przeglądarki). Pozycja z GPS-a wchodzi tam jako zwykły punkt mapy, nie nazwa
przystanku, więc backend sam znajdzie wokół niej słupki (ten sam zasięg
z panelu ⚙, co przy kliknięciu w pustą przestrzeń). W odróżnieniu od klikania
przycisk **nadpisuje** start, który już był — o to się prosi, klikając go —
ale celu nie rusza: gdy „dokąd" jest wypełnione, od razu szuka, a gdy nie,
przesuwa mapę na lokalizację, żeby wybrać cel z okolicy. Odmowa zgody, brak
sygnału i timeout (10 s) mówią, co się stało, w linijce pod polami — świadomie
nie w panelu wyników, bo te komunikaty nie mogą kasować gotowej listy tras,
i nie jako `.hint`, bo te na telefonie znikają w widoku mapy, a to właśnie tam
pyta się o lokalizację. Przeglądarki dają pozycję **tylko po HTTPS** (wyjątek:
`localhost`) — bez tego `navigator.geolocation` nie istnieje i przycisk się
chowa.

Wynik ma **dwie warstwy tej samej odpowiedzi**:

- na mapie — przepływy, czyli cały wachlarz sensownych dojazdów naraz;
- w panelu — lista **propozycji tras** w stylu klasycznej wyszukiwarki:
  godziny odjazd–przyjazd, czas przejazdu, plakietki linii, liczba
  przesiadek. Kliknięcie propozycji rozwija oś czasu etapów (przystanki,
  kierunki, przejścia między stanowiskami), rysuje ją grubo na mapie
  i przygasza resztę przepływu; ponowne kliknięcie — albo klik w mapę obok
  trasy — wraca do całego wachlarza. Najechanie na propozycję pokazuje ją
  na mapie w podglądzie.

Działa to też w drugą stronę: **kliknięcie linii na mapie otwiera
propozycję**, która nią jedzie (spośród kilku — tę najbliższą klikniętemu
miejscu, przy remisie najlepszą z listy). Karta rozwija się i przewija do
widoku, a kadr zostaje na miejscu, o ile trasa się w nim mieści — klik
w mapę nie ma wyrywać widoku spod kursora. Linia, do której nie ma
propozycji, nie przechwytuje kliknięcia: leci ono dalej do zwykłej obsługi
kliknięcia w mapę (podpowiedź pod kursorem mówi, która linia jest klikalna).

Nazwy przystanków podpowiada **własna lista** (`attachAutocomplete`), nie
`<datalist>`: natywnej nie da się ostylować, wygląda inaczej w każdej
przeglądarce i wymaga dokładnych ogonków. Własna szuka po nazwie złożonej
(bez ogonków i wielkości liter, ale znak w znak — dzięki temu pozycja
trafienia wskazuje ten sam fragment oryginału i da się go podświetlić),
trafienia od początku nazwy pokazuje przed trafieniami w środku, obsługuje
↑/↓/Enter/Esc i po wyborze od razu szuka, jeśli druga strona relacji jest
już wypełniona. Lista nazw jedzie w stronie jako JSON (`#stop-names`) —
to ta sama lista, którą wcześniej dostawał `<datalist>`, bez dodatkowego
zapytania.

**Telefon (≤ 760 px) dostaje zakładki** zamiast panelu nachodzącego na mapę:
dolny pasek „Mapa / Trasy (n)" przełącza to, co pod kartą wyszukiwania —
albo lista propozycji na cały ekran, albo sama mapa (wtedy z panelu zostaje
tylko karta „skąd/dokąd", bez nagłówka i podpowiedzi, żeby nie zjadały
ekranu). Lista leży na własnym, jednolitym tle — mapa **zostaje pod spodem
w DOM**, bo Leaflet musi znać rozmiar swojego kontenera, żeby poprawnie
kadrować także wtedy, gdy patrzymy na listę (przy `display: none` dostałby
zerowy kontener i policzył bezsensowny kadr); po prostu jej nie widać. Po znalezieniu połączeń widok przeskakuje na listę, a ✕ wraca na
mapę, bo tam wybiera się nową relację. Zakładki zastępują ☰ (dwa mechanizmy
chowania panelu naraz tylko by myliły). Klasy `view-map`/`view-list` na
`<body>` na szerokim ekranie nie robią nic — tam widać oba widoki naraz.
Kadr mapy liczy się zawsze pod widok mapy, także gdy patrzymy na listę: to
ten kadr zobaczymy po przełączeniu zakładki.

Na czas szukania (dwa zapytania naraz: przepływy + lista) leci **kółko
ładowania** w dwóch miejscach, bo w każdym widoku widać co innego: w
komunikacie „Szukam połączeń…" i na przycisku „Szukaj" — na telefonie w
widoku mapy panel wyników jest schowany, więc samo pierwsze byłoby niewidoczne
akurat tam, gdzie wyszukiwanie odpala się samo po drugim kliknięciu w mapę.
Gasi je tylko odpowiedź na aktualne zapytanie (`requestToken`), a ✕ w trakcie
szukania podbija token — porzucone zapytanie nie dorysuje już wyników relacji,
której nie ma na ekranie.

Panel deweloperski (suwaki strojenia algorytmu) jest schowany za przyciskiem
⚙ w nagłówku. Czysty JS bez frameworka, cała logika w `static/app.js`.
Suwaki: trzy od okna czasowego mapy, zasięg szukania punktu oraz **próg
opłacalności przesiadki** (`transfer_gain_sec`, domyślnie 10 min — patrz opis
skanu wyżej; nie kasuje żadnej opcji, tylko decyduje, która jest proponowana
jako najlepsza). Wartości lądują w `localStorage` i w query
stringu `/api/flow`.

### 4. PWA — `static/manifest.webmanifest` + `static/sw.js` + `static/pwa.js`

Aplikacja instaluje się na telefonie i na pulpicie (ikona, własne okno bez
paska adresu, ekran startowy). Manifest opisuje nazwę, kolory i ikony;
service worker odpowiada za start bez sieci; `pwa.js` spina to z UI —
rejestruje workera, pokazuje przycisk ⤓ (tylko wtedy, gdy przeglądarka
faktycznie proponuje instalację) i podmienia front na nowy.

O nowej wersji nie pytamy. Worker robi `skipWaiting()` przy instalacji, więc
przejmuje stronę od razu, a `pwa.js` łapie `controllerchange` i przeładowuje
kartę — kod na ekranie jest w tym momencie i tak już stary. Robimy to tylko
wtedy, gdy nie ma czego zgubić: dopóki użytkownik niczego nie kliknął ani nie
wpisał. Przeglądarka sprawdza workera właśnie przy wejściu na stronę, więc
przeładowanie wypada ułamek sekundy po odświeżeniu i wygląda jak jego część.
Jeśli ktoś zdążył już czegoś szukać, zostawiamy go w spokoju — nowa wersja
wjedzie przy następnym wejściu, a wywalony w pół drogi formularz jest gorszy
niż jedno wyszukiwanie na starym froncie. Wymusić sprawdzenie można z panelu
⚙, który porównuje wersję działającą z tą wydawaną przez serwer.

Service worker jedzie z **`/sw.js`**, a nie ze `/static/` — zasięg workera
to jego katalog, więc z `/static/sw.js` nie objąłby strony głównej. Stąd
osobny endpoint w `routes.py`. Strategie są dobrane per rodzaj zasobu:

| Zasób | Strategia | Dlaczego |
|---|---|---|
| strona `/` | sieć, offline ostatnia znana kopia | rozkład ma być świeży, ale bez sieci lepiej pokazać powłokę niż dinozaura |
| `/api/*` | tylko sieć | odpowiedź zależy od godziny; offline zwracamy `{error: …}`, które UI pokazuje jak każdy inny błąd |
| statyki i Leaflet | cache od razu, odświeżenie w tle | start bez czekania na sieć |
| kafelki mapy | cache od razu, limit 400 | raz obejrzana okolica działa offline |

Kafelki pobieramy z `crossOrigin` (`static/app.js`), żeby worker dostawał
normalną odpowiedź zamiast nieprzejrzystej — przeglądarka rozlicza te drugie
z limitu miejsca po ~7 MB za sztukę niezależnie od tego, że kafelek waży
kilkanaście kilobajtów.

**Wersji cache'ów nie podbija się ręcznie.** `VERSION` w `sw.js` to
placeholder, w który Flask przy serwowaniu `/sw.js` wstawia skrót SHA-256
z zawartości `static/` i szablonu (`_frontend_digest` w `routes.py`).
Zmiana dowolnego pliku frontu zmienia treść workera, więc przeglądarka
widzi nową wersję, a ta przy aktywacji kasuje cache'e po poprzedniej.
To odpowiednik hashowanych nazw plików z bundlerów (Vite, Workbox), tylko
liczony w locie — bez build stepu, którego ten projekt poza tym nie ma.
Skrót liczymy z zawartości, nie z dat: w kontenerze po każdym buildzie daty
są nowe, a pliki te same. Koszt to ~0,3 ms na żądanie `/sw.js`.

Panel ⚙ pokazuje wersję działającą w przeglądarce i pozwala **wymusić
sprawdzenie aktualizacji** (`registration.update()`). Przycisk odpowiada
w jednym z trzech stanów: wszystko aktualne, jest nowa wersja (instaluje
się, wejdzie po odświeżeniu strony) albo — i po to głównie powstał —
**awaria**: serwer wydaje inną wersję, niż ta, na której chodzi aplikacja,
a przeglądarka mimo wymuszenia nie widzi aktualizacji. To sygnał, że
`/sw.js` jest gdzieś po drodze cache'owany (proxy, CDN, nagłówki) i
użytkownicy zostają na starym froncie. Bez tej diagnostyki taka awaria jest
niewidoczna: wszystko działa, tylko nikt nie dostaje poprawek. Sam `/sw.js`
jest z tego powodu wyłączony z cache'owania w workerze — pytanie o wersję
musi trafiać do serwera.

Wyjątkiem jest cache kafelków
(`planer-tiles-1`): jest poza wersjonowaniem, bo mapa nie zmienia się razem
z aplikacją i szkoda ściągać jej po każdej poprawce w CSS.

Bez sieci **i** bez zapamiętanej powłoki (np. pierwsze wejście offline)
zostaje `static/offline.html` — strona bez żadnych zależności.

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
- kursy po północy mają w GTFS godziny 24:xx+ i liczą się do doby, w której
  wyruszyły, więc rozkład dnia D obejmuje też ogon dnia D-1 przesunięty
  o -24 h — to on obsługuje godziny 00:00-06:00 (patrz `gtfs.PREV_DAY_SEC`);
- punkt wsiadania w kurs zapisuje się przy pierwszym jego przystanku, na
  który zdążymy, ale jest przesuwany dalej, jeśli po drodze mijamy przystanek
  osiągalny MNIEJSZĄ liczbą przejazdów (w skrajnym przypadku sam start).
  Godziny to ten sam pojazd, więc przyjazd się nie zmienia — znika za to
  etap "dojedź pod początek trasy tego autobusu, który i tak zaraz Cię
  minie" (patrz `planner._cheaper_boarding`; odpowiednik reguły postępu,
  którą mapa przepływów ma u siebie od 2026-07-18);
- przy REMISIE na godzinie przyjazdu wygrywa droga z mniejszą liczbą
  przejazdów. Bez tego o wyniku decyduje kolejność skanowania i potrafi
  wyjść "wysiądź i przesiądź się do sąsiedniego autobusu, który dowozi
  o tej samej minucie". Lista propozycji z mapy sortuje tak od dawna
  (`_enumerate_journeys`), skan dostał to samo;
- gdy pojazd, którym już jedziemy, sam dowozi do celu, obok trasy ze skanu
  staje jej wariant **bez tej przesiadki** (`_seated_legs`; na celu relacji
  liczy się też inny słupek tego samego przystanku — nocne linie zjeżdżają
  na różne perony jednego dworca). Obie opcje idą na listę propozycji;
  `TRANSFER_GAIN_SEC` (domyślnie 10 min, suwak w panelu ⚙) rozstrzyga
  wyłącznie, która jest **proponowana jako najlepsza** — przesiadka musi
  tyle oszczędzić, żeby wyprzedzić jazdę bez niej. Nic nie znika: przy
  progu 0 kolejność wraca do samego przyjazdu. Okno czasowe mapy jest
  nietknięte, `best_arr` zostaje najwcześniejszym możliwym przyjazdem;
- trasę odtwarzamy wstecz po zapisanych wskaźnikach (które połączenie
  poprawiło który przystanek).

Pierwsze zapytanie dnia kosztuje ~1 s (ładowanie tablicy do RAM),
kolejne są natychmiastowe (~30 ms).

### Lista propozycji tras (część `plan_flow`, nie osobny algorytm)

Mapa przepływów pokazuje, **czym w ogóle da się dojechać**; lista nazywa
z tego kilka gotowych wariantów z godzinami. To już NIE jest osobny skan —
lista to po prostu ścieżki przeczytane wprost z tego samego grafu segmentów,
który mapa właśnie narysowała (`_extract_transfer_graph` +
`_enumerate_journeys`, wołane na końcu `plan_flow`, po `_select_and_anchor`).
Efekt: lista nigdy nie pokaże przesiadki, której nie ma na mapie, i reaguje
na te same suwaki (tolerancja regresji, okno czasowe) — dawniej to nie było
prawdą (lista miała własne, niezależne od suwaków okno).

1. **Graf przesiadek** (`_extract_transfer_graph`): węzły to już narysowane,
   przycięte segmenty mapy; krawędzie to miejsca, gdzie z wyjścia jednego
   segmentu da się zdążalnie wskoczyć w drugi — dokładnie ten sam warunek
   porównywalnej jasności, którego `_select_and_anchor` już użył, żeby
   uznać koniec segmentu za zakotwiczony. Punkty startowe to segmenty
   zaczynające się na starcie relacji.
2. **Przeszukiwanie** (`_enumerate_journeys`): w przód od startu, najpierw
   najjaśniejsze gałęzie, z sufitami kosztu (limit etapów na propozycję,
   limit zebranych wariantów, limit odwiedzonych węzłów) — duże miasto przy
   niskim progu jasności mógłby inaczej dać kombinatoryczną eksplozję.
3. Odpada powtórzony układ (te same linie wsiadane na tych samych
   przystankach); ranking po przyjeździe, potem liczbie przesiadek, potem
   czasie oczekiwania — tak jak dawniej.
4. **Zabezpieczenie**: `_select_and_anchor` potrafi przyciąć najszybsze
   segmenty z powodów niezwiązanych z progiem jasności (reguła kotwicy) -
   `plan_flow` sprawdza, czy najszybsza trasa (już policzona przez `_scan`
   na potrzeby deadline'u) rzeczywiście pojawia się w wyniku grafu; jeśli
   nie, dorysowuje ją wprost - i do mapy, i do listy, tym samym
   zrekonstruowanym przejazdem, więc obie odpowiedzi nadal się zgadzają.

Etapy dostają geometrię wprost z segmentu (ten sam `gtfs.shape_slice`, jedno
połączenie do bazy na całe zapytanie - i mapę, i listę), więc wybrana
propozycja rysuje się po realnych ulicach i torach.

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
7. **Próg jasności** (suwak w UI, 0–100%, domyślnie 60%): segmenty poniżej
   progu nie są wysyłane.
8. **Spójność sieci**: po odsianiu progiem każdy segment jest przycinany
   z obu stron do zakotwiczonych punktów — początek to start relacji albo
   miejsce, gdzie dołącza inny narysowany segment; koniec to cel albo
   ostatnia przesiadka w porównywalnie jasny (tolerancja 0,1) narysowany
   segment. Segment bez kotwic odpada; punkt stały iteruje, aż nic nie
   wypada. Efekt: żadna linia nie zaczyna się „znikąd" ani nie prowadzi
   „w powietrze", niezależnie od ustawienia suwaka.
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
- kadr: najjaśniejsze segmenty (próg 0,7 → 0,45 → wszystko) + zawsze
  start i cel.

Koszt: dwa liniowe skany fragmentu tablicy + jedno przejście po oknie —
~30 ms na cache'owanym dniu, odpowiedź to zwykle kilkaset–2000 krawędzi.

## API

- `GET /api/stops` — wszystkie słupki: `[{name, lat, lon}, …]`.
- `GET /api/plan?start=&end=&time=HH:MM` — jedna najszybsza trasa: etapy
  z godzinami, przystankami po drodze i współrzędnymi (`legs[].path`).
  Nieużywany obecnie przez UI, zostaje jako narzędzie/debug.
- `GET /api/flow?start=&end=&time=HH:MM&extra_sec=600` (albo `start_lat`/
  `start_lon`, `end_lat`/`end_lon` i `range_m` zamiast nazw) — JEDNA
  odpowiedź niesie i mapę, i listę propozycji (dawniej dwa osobne
  zapytania/endpointy — `/api/journeys` zniknął, patrz wyżej dlaczego):
  `{start, end, departure, best_arrival, deadline, segments: [{path:
  [[lat,lon], …], num: "10", kind: "tram"|"bus"|"other", w: 0..1}, …],
  journeys: [{departure, arrival, duration_min, wait_min, transfers,
  legs: [...]}, …]}`. `segments` posortowane rosnąco po `w` (kolejność
  rysowania); `path` to kolejne przystanki od wsiadania do ostatniego
  użytecznego wyjścia. `journeys` posortowane po godzinie przyjazdu; etap
  przejazdu: `{kind: "ride", line, num, mode, headsign, from, from_time,
  to, to_time, minutes, stops, stops_count, path}`; etap pieszy:
  `{kind: "walk", text, minutes, from, to, path}`.
- `GET /api/line?num=17&mode=tram&date=YYYY-MM-DD` — rozkład jednej linii:
  `{num, mode, label, date, variants: [{headsign, from, to, stops:
  [{id, name, lat, lon}, …], path: [[lat,lon], …], trips: [{id, dep: "05:12",
  sec, times: ["05:12", …]}, …]}, …]}`. Wariant = jeden ciąg przystanków
  (kierunek albo kurs skrócony), warianty posortowane po liczbie kursów
  malejąco; `times` ma tyle pozycji, co `stops`. `stops[].id` to słupek —
  tym samym identyfikatorem posługuje się `points` z `/api/stop_board`, więc
  front umie przeskoczyć z rozkładu linii na tablicę tej właśnie krawędzi.
- `GET /api/stop_board?stop=&date=YYYY-MM-DD` — tablica odjazdów:
  `{stop, date, center: [lat, lon], points: [{id, name, lat, lon}, …],
  lines: [{mode, num, headsign, count}, …], departures: [{line, t: "12:03",
  sec, trip, stop, platform?}, …]}`. `departures[].line` to indeks w `lines`.
  `points` to słupki miejsca, z których cokolwiek odjeżdża — same nazwy
  i współrzędne, bo co z którego jedzie, widać już po `departures[].stop`;
  front składa z tego wybór „skąd dokładnie". Odjazdy obejmują jedną dobę
  ZEGAROWĄ: ogon nocy z dnia poprzedniego wchodzi (autobus o 00:20 należy do
  kalendarza soboty, ale odjeżdża w niedzielę), a nadwyżka tej doby
  rozkładowej ponad 24 h już nie — stoi na tablicy następnego dnia. Bez tego
  linia nocna byłaby na jednej tablicy dwa razy. Bez geometrii
  — na dużym węźle jest ponad sto par (linia, kierunek), a naraz widać z
  nich parę; trasy dociąga front przez `/api/trip`, dla linii faktycznie
  zaznaczonych.
- `GET /api/trip?trip=&date=&stop=&dep=` — jeden kurs: `{trip, num, mode,
  line, headsign, board_index, stops: [{name, lat, lon, t, sec}, …],
  path: [[lat,lon], …], tail: [[lat,lon], …]}`. `tail` to przebieg od
  słupka podanego w `stop`/`dep` do końca kursu — front rysuje go
  jaskrawo, a resztę `path` blado.
- Błędy: `{error: "…", suggestions: […]}` — podpowiedzi przy literówce
  w nazwie przystanku albo numerze linii.

## Struktura plików

| Plik | Rola |
|---|---|
| `update_gtfs.py` | pobranie GTFS + budowa SQLite + atomowa podmiana |
| `gtfs.py` | dostęp do bazy, cache dnia, dopasowanie nazw przystanków |
| `planner.py` | CSA (`plan_route`), mapa przepływów + lista propozycji, jedna odpowiedź (`plan_flow`) |
| `timetables.py` | rozkład linii i tablica odjazdów z przystanku |
| `routes.py` | endpointy Flaska |
| `app.py` | start aplikacji (port 5001) |
| `templates/index.html` | szkielet strony: mapa, panel, panel deweloperski |
| `static/app.js` | frontend wyszukiwarki: mapa, wyszukiwanie, lista propozycji |
| `static/timetable.js` | frontend rozkładów (drugi tryb panelu, po moście z `app.js`) |
| `static/style.css` | style panelu, kart tras, plakietek linii itd. |
| `static/manifest.webmanifest` | manifest PWA: nazwa, kolory, ikony, tryb okna |
| `static/sw.js` | service worker: cache powłoki, kafelków i statyk (serwowany z `/sw.js`) |
| `static/pwa.js` | rejestracja workera, przycisk instalacji, ciche przejście na nową wersję |
| `static/offline.html` | awaryjna strona, gdy nie ma ani sieci, ani cache'u |
| `static/icons/` | ikony aplikacji (192/512 px, wersje maskowalne, SVG) |
| `data/gtfs.sqlite` | baza rozkładów (poza gitem) |
| `docs/` | dokumentacja: ten plik, `ROUTING_ALGORITHM.md`, `FLOW_MAP_CONTRACT.md` |
| `tests/` | testy pytest (patrz `docs/FLOW_MAP_CONTRACT.md`) |

## Changelog

- **2026-09-05** — z rozkładu linii da się przejść **wprost na tablicę
  przystanku**: przy każdym przystanku trasy (poza ostatnim — tam kurs się
  kończy) stoi przycisk „odjazdy", który otwiera tablicę tego przystanku
  z wybranym słupkiem i zaznaczoną tylko tą linią. Słupek bierze się z `id`
  w wariancie, nie z nazwy — oba słupki „RYNKU" nazywają się tak samo,
  a linia jedzie jednym z nich. Wybór nakłada się po odpowiedzi serwera: gdy
  tej linii albo tego słupka na tablicy nie ma (inny dzień, kurs wycofany),
  zostaje zwykła tablica całego przystanku. Przy okazji przełącznik trybu
  dostał podpis „Rozkłady" — sama tarcza zegara nie mówiła, dokąd się klika.
- **2026-09-05** — **godzina obok daty** i **pełny rozkład**. Pole godziny
  jest jedno na oba rozkłady i nic nie dociąga (odpowiedź niesie całą dobę):
  przesuwa tylko to, od czego zaczyna się tablica, i to, który kurs linii jest
  „ten najbliższy". Zastąpiło regułę „teraz, ale tylko dla dzisiaj" — rozkład
  na czwartek za tydzień też ma się od czegoś zaczynać. Przy okazji dzień
  i godzina dostały ramkę pola czasu wyszukiwarki; surowy `<input type="date">`
  na tle karty wyglądał jak nie z tej aplikacji.

  Przełącznik tablicy to teraz **„podana godzina" / „pełny rozkład"** zamiast
  „od teraz / cała doba". Pełny rozkład to zapis ze słupka: wiersz na godzinę,
  w wierszu minuty kolejnych kursów — doba mieści się na jednym ekranie. Ma to
  sens tylko dla JEDNEJ linii (pod zlanym ciągiem minut nie wiadomo, co
  podjedzie), więc przełączenie zawęża zaznaczenie do jednej, a plakietki
  linii stają się wyborem jednokrotnym; poprzednie zaznaczenie wraca przy
  powrocie do listy. Kierunek odróżniają odnośniki, tak jak na papierze: kurs
  jadący tam, gdzie większość, jest bez znaczka, każdy inny dostaje swój,
  rozwinięty w legendzie pod siatką. Klik w minutę rozwija kurs, tak samo jak
  klik w wiersz listy.

  Ta siatka od razu pokazała błąd, którego w liście nie było widać: linia
  nocna stała na tablicy **dwa razy**. Oś doby sięga poza 24 h, bo kurs
  wyjeżdżający o 25:10 musi dać się doskanować wyszukiwarce — ale on odjeżdża
  już następnego dnia i to tam ma stać, jako ogon nocy. `stop_board` tnie więc
  odjazdy na 24 h (`gtfs.PREV_DAY_SEC`); rozkładu LINII to nie dotyczy, bo tam
  jednostką jest doba rozkładowa, a nie kalendarzowa.
- **2026-09-05** — tablica odjazdów daje się zawęzić do jednego **słupka**.
  Nazwa taka jak „Wojszyce" to jedno miejsce scalone z kilku słupków (patrz
  `gtfs._build_places`), a z każdego jedzie się gdzie indziej — tablica ze
  wszystkich odpowiadała na „co tu odjeżdża", ale nie na „co odjeżdża stąd,
  gdzie stoję". Karta „Słupki" wypisuje je kierunkami, nie nazwami, bo
  nazywają się zwykle tak samo; nazwa dochodzi do podpisu tylko tam, gdzie
  faktycznie rozróżnia (nazwane perony kierunkowe węzła). Wybór zawęża
  tablicę, listę linii i trasy na mapie, a słupki są też punktami na mapie —
  klik w przygaszony przełącza tablicę na niego. Serwer dokłada do
  odpowiedzi samo `points` (nazwa + współrzędne); liczniki, kierunki i numery
  liczy front z tablicy, którą i tak ma w całości.

- **2026-08-29** — drugi tryb panelu: **rozkłady jazdy**, przełączane
  przyciskiem ◷ obok chowania panelu (na telefonie w pasku zakładek).
  Rozkład linii (kierunki, kursy, przebieg na mapie) i tablica odjazdów
  z przystanku, w której plakietkami linii składa się rozkład z dowolnego
  ich podzbioru; trasy zaznaczonych linii — od tego przystanku dalej — idą
  na mapę, a kliknięcie odjazdu pokazuje ten jeden kurs z godzinami.
  Backend: `timetables.py` + `/api/line`, `/api/stop_board`, `/api/trip`.
  Front: `static/timetable.js`, wpięty w mapę wyszukiwarki wąskim mostem
  (`window.plannerBridge` na końcu `app.js`) — wejście w rozkłady chowa
  wachlarz połączeń, wyjście odtwarza go z pamięci, bez ponownego zapytania.

  Trzy decyzje z UI, bo każda odwraca to, co napisało się najpierw:
  **jedno pole** zamiast zakładek „Linia / Przystanek" — zakładka kazała
  wybrać rodzaj rozkładu, zanim było wiadomo, czego się szuka, a „17"
  i „Katedra" i tak nie da się pomylić; rodzaj widać na liście podpowiedzi
  (plakietka linii / znaczek słupka) i tam wystarczy. **Brak przycisku
  „Pokaż"** — wybór podpowiedzi ładuje od razu, Enter też. **Rozkład linii
  otwiera się na najbliższym kursie**, a nie na liście przystanków bez
  godzin: pytanie brzmi „o której to jedzie", więc jakaś godzina musi być
  na ekranie od razu, a pasek godzin sam przewija się na tę porę.
  `attachAutocomplete` dostało przy okazji haki `suggest`/`render`, więc
  klawiatura i ARIA listy podpowiedzi są nadal w jednym miejscu, a rozkłady
  podstawiają tylko własne szukanie i własny wiersz.

  Przy okazji: pole daty wyszukiwarki dostało wartość w formacie ISO —
  `<input type="date">` innego nie przyjmuje, więc do tej pory startowało
  puste.
- **2026-08-15** — przycisk ◎ „moja lokalizacja" w polu „skąd": pozycja
  z Geolocation API ląduje jako punkt mapy (backend znajduje słupki wokół
  niej), z wypełnionym celem odpala wyszukiwanie od razu, bez celu przesuwa
  mapę na okolicę. Błędy zgody/sygnału w osobnej linijce pod polami, żeby nie
  kasować gotowej listy tras; bez HTTPS (poza `localhost`) przycisk się chowa.
  Do tego kółko ładowania na czas szukania — w komunikacie „Szukam połączeń…"
  i na przycisku „Szukaj" (na telefonie w widoku mapy wyników nie widać, więc
  samo pierwsze by nie wystarczyło). ✕ w trakcie wyszukiwania porzuca
  zapytanie w locie zamiast czekać, aż dorysuje nieaktualne wyniki.
- **2026-08-11** — planer jako PWA: manifest z ikonami, service worker
  (`/sw.js`) z osobną strategią dla strony, API, statyk i kafelków mapy,
  przycisk instalacji w nagłówku i pasek „jest nowa wersja". Bez sieci
  aplikacja wstaje z cache'u, mapa pokazuje raz obejrzaną okolicę, a
  wyszukiwanie mówi wprost, że wymaga połączenia. Wersja cache'ów to skrót
  z zawartości frontu wstrzykiwany przy serwowaniu `/sw.js`, więc niczego
  nie trzeba podbijać ręcznie; panel ⚙ pokazuje tę wersję, pozwala wymusić
  sprawdzenie aktualizacji i krzyczy, gdy serwer wydaje co innego, niż
  chodzi w przeglądarce. Kafelki chodzą teraz
  przez CORS — nieprzejrzyste odpowiedzi zjadały ~7 MB limitu miejsca za
  sztukę. `viewport-fit=cover` włącza wreszcie wcięcia ekranu, o które
  style i tak już się upominały (`env(safe-area-inset-*)`).
- **2026-08-09** — lista propozycji tras przestała być osobnym algorytmem:
  `plan_journeys` (metoda zakazu linii) usunięty, `/api/journeys` usunięty.
  Propozycje to teraz ścieżki czytane wprost z grafu segmentów, który
  `plan_flow` i tak już liczy dla mapy (`_extract_transfer_graph` +
  `_enumerate_journeys`) - jedno zapytanie/`fetch` niesie obie rzeczy naraz,
  więc mapa i lista nie mogą się już rozjechać (dawniej potrafiły: lista
  ignorowała próg jasności i inne suwaki mapy).

  Przy okazji znaleziona i naprawiona prawdziwa przyczyna (nie łatka na
  objawie) przypadku, w którym najszybsza trasa potrafiła w ogóle nie
  pojawić się ani na mapie, ani na liście: reguła cofnięcia w
  `_discover_segments` porównywała KAŻDY kandydujący przystanek wsiadania
  do NAJLEPSZEGO `latest[]` spośród wszystkich rozwiniętych przystanków
  startowych (klik w punkt mapy rozwija się w kilka fizycznie różnych
  słupków w zasięgu) - więc wsiadanie na jednym z nich odpadało, jeśli
  akurat INNY przystanek startowy miał lepsze dalsze połączenia, mimo że
  bycie na którymkolwiek z własnych przystanków startowych nigdy nie jest
  "cofnięciem". Naprawione: reguła cofnięcia pomija teraz przystanki
  startowe (`arrived_by == "origin"`) całkowicie. Zostaje jedno,
  niezależne od tego zabezpieczenie w `plan_flow`: gdyby `_select_and_anchor`
  i tak przycięło WSZYSTKIE segmenty do zera (skrajny przypadek, nie
  pojedynczy brakujący segment), dorysowuje najszybszą trasę wprost.

  Cztery nakładające się na siebie suwaki okna czasowego (próg jasności +
  mnożnik wydłużenia + widełki min/maks zapasu) zastąpione jednym: "ile
  dłużej niż najszybsza trasa" (`extra_sec`, wprost w minutach, 0–60 min,
  domyślnie 30). Ich łączny efekt matematycznie zawsze sprowadzał się do
  jednej liczby (okno × (1 − próg jasności)), więc żadna kombinacja starych
  czterech suwaków nie dawała niczego, czego nie dałoby się osiągnąć jednym.
  Próg jasności zniknął też z `_select_and_anchor` - okno czasowe
  (`_deadline`) samo w sobie wyznacza, co jest brane pod uwagę; `q` zostaje
  tylko jako ciągła wartość do intensywności rysowania.
- **2026-08-04** — lista propozycji tras obok mapy przepływów: nowy
  `plan_journeys` (warianty metodą zakazu użytych linii, wspólne okno
  czasowe z przepływami) i `/api/journeys`; karty tras w stylu klasycznej
  wyszukiwarki, z rozwijaną osią czasu etapów i podświetleniem wybranej
  trasy na mapie. Kliknięcie linii na mapie otwiera propozycję, która nią
  jedzie (linia bez propozycji nadal przepuszcza klik do wyboru punktu).
  Klik w mapę nie kasuje już gotowego wyszukiwania — uzupełnia tylko
  brakujący koniec relacji, a przy wybranej trasie odznacza ją; czyści
  wyłącznie przycisk ✕. Podpowiedzi nazw przystanków to własna lista
  zamiast `<datalist>` (szukanie bez ogonków, podświetlanie trafienia,
  obsługa klawiatury). Na telefonie dolne zakładki „Mapa / Trasy" zamiast
  panelu nachodzącego na mapę. Do tego przebudowa UI: panel z lewej
  (nagłówek, karta wyszukiwania „skąd/dokąd" z zamianą stron, lista
  wyników), suwaki deweloperskie schowane pod przyciskiem ⚙, cały JS
  w `static/app.js`.
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
- Segment pokazuje też objazdy „w bok", jeśli mieszczą się w oknie czasowym
  (suwak „ile dłużej niż najszybsza trasa" w panelu, domyślnie 30 min, do
  60 min) — to celowe (niszowe opcje mają być widoczne), ale przy szerokim
  oknie bywa tego sporo.
- Bufor przesiadki w skanie wstecz jest stosowany jednolicie (2 min),
  nieco ostrożniej niż w skanie w przód.
- Brak tras pieszych po mieście — przesiadka tylko między słupkami
  o identycznej nazwie przystanku.
- Kafelki mapy i biblioteka Leaflet ładowane z internetu (CDN).

## Pomysły na dalej

- Dymki na węzłach przesiadkowych: „w co mogę się tu przesiąść i o której".
- Więcej odjazdów tej samej trasy na liście („następny kurs o…").
- GTFS-RT: opóźnienia i pozycje pojazdów na żywo (portal je udostępnia).
