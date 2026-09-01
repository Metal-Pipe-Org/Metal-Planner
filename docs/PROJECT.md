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
- **`routes.py`** — endpointy: `/` (strona), `/api/stops`, `/api/plan`,
  `/api/flow` (szczegóły w sekcji API).

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

- `GET /api/stops` — wszystkie słupki MPK i (o ile skonfigurowano
  `PKP_API_KEY` i geokodowanie zdążyło już znaleźć współrzędne - patrz
  `pkp.py`/`update_pkp.py`) stacje PKP: `[{name, lat, lon, kind: "stop"|
  "train"}, …]`. `kind` mówi frontowi, jakim stylem narysować marker
  (`static/app.js`) - reszta pól jest taka sama dla obu rodzajów.
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
  użytecznego wyjścia. `nodes` to węzły przesiadkowe pod kropki na mapie:
  `[{name, lat, lon, sec, lines: [{num, kind, headsign}, …]}, …]` — po jednym
  na MIEJSCE, nie na słupek (plac z trzema peronami to jedna kropka), `sec` =
  najwcześniejsza godzina, o której można tam być, `lines` = linie, w które
  MAPA pozwala tam wsiąść, z kierunkiem (ta sama linia mija węzeł w obie
  strony, a mapa proponuje jedną). Węzeł, z którego nie da się w nic wsiąść,
  nie trafia na listę — nie ma tam przesiadki.
  `journeys` posortowane po godzinie przyjazdu; etap
  przejazdu: `{kind: "ride", line, num, mode, headsign, from, from_time,
  to, to_time, minutes, stops, stops_count, path}`; etap pieszy:
  `{kind: "walk", text, minutes, from, to, path}`.
  Jeśli skonfigurowano `PKP_API_KEY`, `journeys` (i `segments`, o ile trasa
  akurat przebiega w pobliżu Wrocławia) mogą zawierać etapy kolejowe
  (`mode: "train"`) — routes.py nie wie o tym nic: `/api/flow` woła
  `plan_flow` dokładnie tak samo, jak gdyby PKP nie istniało. Połączenia
  kolejowe są doklejone WPROST do tablicy połączeń, którą skanuje CSA
  (`gtfs.load_day` → `pkp.augment_day`, patrz `pkp.py`) — dla wyszukiwarki
  stacja PKP to zwykły przystanek, a przesiadka pociąg↔MPK to zwykła
  przesiadka — przez to samo `siblings`, co przejście między słupkami jednego
  miejsca, bo stacja przechodzi przez to samo sklejanie w miejsce
  (`gtfs._build_places`) co każdy słupek: TA SAMA NAZWA = to samo miejsce.
  Własnego promienia przesiadkowego kolej nie ma (usunięty 2026-08-31).
  Efekt: relacja „Warszawa Centralna → Rynek” po prostu działa — jedna trasa,
  etapy kolejowe i miejskie razem, bez żadnego specjalnego pola w odpowiedzi.
  Stacja PKP bez ustalonych współrzędnych (geokodowanie w toku - patrz
  `update_pkp.py`) jest dla wyszukiwarki niewidoczna, a etap kolejowy nie ma
  geometrii trasy (`path: []` na etapie — sama stacja, jeśli ma współrzędne,
  i tak ma marker na mapie, patrz `/api/stops`).
  Jeśli skonfigurowano `PKP_API_KEY`, `journeys` (i `segments`, o ile trasa
  akurat przebiega w pobliżu Wrocławia) mogą zawierać etapy kolejowe
  (`mode: "train"`) — routes.py nie wie o tym nic: `/api/flow` woła
  `plan_flow` dokładnie tak samo, jak gdyby PKP nie istniało. Połączenia
  kolejowe są doklejone WPROST do tablicy połączeń, którą skanuje CSA
  (`gtfs.load_day` → `pkp.augment_day`, patrz `pkp.py`) — dla wyszukiwarki
  stacja PKP to zwykły przystanek, a przesiadka pociąg↔MPK to zwykła
  przesiadka — przez to samo `siblings`, co przejście między słupkami jednego
  miejsca, bo stacja przechodzi przez to samo sklejanie w miejsce
  (`gtfs._build_places`) co każdy słupek: TA SAMA NAZWA = to samo miejsce.
  Własnego promienia przesiadkowego kolej nie ma (usunięty 2026-08-31).
  Efekt: relacja „Warszawa Centralna → Rynek” po prostu działa — jedna trasa,
  etapy kolejowe i miejskie razem, bez żadnego specjalnego pola w odpowiedzi.
  Stacja PKP bez ustalonych współrzędnych (geokodowanie w toku - patrz
  `update_pkp.py`) jest dla wyszukiwarki niewidoczna, a etap kolejowy nie ma
  geometrii trasy (`path: []` na etapie — sama stacja, jeśli ma współrzędne,
  i tak ma marker na mapie, patrz `/api/stops`).
  to, to_time, dep_sec, arr_sec, minutes, stops, stops_count, path}`; etap
  pieszy: `{kind: "walk", text, minutes, from, to, path}`. `dep_sec`/`arr_sec`
  to sekundy na osi doby rozkładowej (mogą przekroczyć 24 h) — `from_time`
  i `to_time` po północy zawijają się do `00:xx` i nie da się z nich odtworzyć
  doby (patrz `/api/timetable`).
- `GET /api/timetable?stop=&date=&from_sec=&limit=` (albo `lat`/`lon`
  zamiast `stop`) — tablica odjazdów jednego przystanku, pod dymek kropki
  przesiadki na mapie:
  `{stop, from_time, departures: [{time, in_min, line, num, mode, headsign},
  …]}`, najbliższe 8, po kolei. Przystanek rozumiany jako MIEJSCE (wszystkie
  słupki, patrz `gtfs.match_stop`) — na węźle pasażer pyta o wszystko, co
  stąd odjeżdża, a nie o peron, przy którym wysiadł. `from_sec` podaje
  godzinę na osi doby rozkładowej; bez niego liczymy od godziny z `time`.
  Ostatni przystanek kursu nie ma odjazdów — `departures` bywa puste.
  Wersji z `lat`/`lon` używa mapa przepływów: kropki stawia z geometrii
  kawałków, więc zna położenie słupka, a nie jego nazwę (`gtfs.stop_at`
  dociąga najbliższy słupek w promieniu 60 m i całe jego miejsce).
  `limit` (domyślnie 8, sufit 60) podnosi mapa przepływów: z tej listy
  zostawia potem tylko linie z `nodes[].lines`, więc musi dostać z zapasem.
- Błędy: `{error: "…", suggestions: […]}` — podpowiedzi przy literówce
  w nazwie przystanku.

## Struktura plików

| Plik | Rola |
|---|---|
| `update_gtfs.py` | pobranie GTFS + budowa SQLite + atomowa podmiana |
| `gtfs.py` | dostęp do bazy, cache dnia, dopasowanie nazw przystanków |
| `planner.py` | CSA (`plan_route`), mapa przepływów + lista propozycji, jedna odpowiedź (`plan_flow`) |
| `pkp.py` | dokleja rozkład PKP wprost do tablicy połączeń MPK (`augment_day`) - jeden CSA widzi obie sieci |
| `routes.py` | endpointy Flaska |
| `app.py` | start aplikacji (port 5001) |
| `templates/index.html` | szkielet strony: mapa, panel, panel deweloperski |
| `static/app.js` | cały frontend: mapa, wyszukiwanie, lista propozycji |
| `static/style.css` | style panelu, kart tras, plakietek linii itd. |
| `static/manifest.webmanifest` | manifest PWA: nazwa, kolory, ikony, tryb okna |
| `static/sw.js` | service worker: cache powłoki, kafelków i statyk (serwowany z `/sw.js`) |
| `static/pwa.js` | rejestracja workera, przycisk instalacji, ciche przejście na nową wersję |
| `static/offline.html` | awaryjna strona, gdy nie ma ani sieci, ani cache'u |
| `static/icons/` | ikony aplikacji (192/512 px, wersje maskowalne, SVG) |
| `data/gtfs.sqlite` | baza rozkładu MPK (poza gitem) |
| `update_pkp.py` | pobranie rozkładu PKP + budowa SQLite + atomowa podmiana + geokodowanie stacji |
| `data/pkp.sqlite` | baza rozkładu PKP, tylko z `PKP_API_KEY` (poza gitem) |
| `data/pkp_station_coords.json` | współrzędne stacji PKP z geokodowania (poza gitem) |
| `docs/` | dokumentacja: ten plik, `ROUTING_ALGORITHM.md`, `FLOW_MAP_CONTRACT.md` |
| `tests/` | testy pytest (patrz `docs/FLOW_MAP_CONTRACT.md`) |

## Changelog

- **2026-08-31** — kolej przestała być doklejką: stacje dokładane do dnia
  PRZED budowaniem miejsc, więc przechodzą przez to samo sklejanie po nazwie
  co przystanki, a ich własny promień przesiadkowy (500 m) usunięty razem
  z funkcją, która go liczyła. Czas kolejowy ucinany do pełnych minut
  (odjazd w dół, przyjazd w górę), bo jedna oś czasu nie może mieć dwóch
  dokładności. Reguła „ta sama nazwa" dostała zabezpieczenie odległością
  (`gtfs._one_spot`) — ogólnopolski słownik stacji sklejał wrocławską
  „Wiśniową" ze stacją 354 km dalej. Skutek uboczny zamierzony: dziś obie
  sieci stykają się w JEDNYM punkcie (Wrocław Szczepin); porządne łączenie
  stacji z przystankami to osobne zadanie.
- **2026-08-31** — skan przestał gubić cel osiągalny wyłącznie przejściem
  pieszo. Pytanie „czy to już cel" zadawało się tylko przy wysiadaniu
  z pojazdu, więc relacja kończąca się przejściem na sąsiedni słupek była
  ogłaszana jako nieistniejąca — mimo policzonej godziny.

- **2026-08-30** — geokodowanie stacji PKP uproszczone na wyraźną prośbę
  użytkownika do DWÓCH źródeł: WYŁĄCZNIE mapa PLK i portal pasażera -
  OpenStreetMap/Overpass i Nominatim (razem z całym mechanizmem "stacje za
  granicą", który na nich polegał) usunięte z `update_pkp.py` całkowicie,
  nie tylko zdegradowane do zapasowych. Powód: oba to zewnętrzne geokodery
  dopasowujące po samej nazwie miejscowości, bez wiedzy o tym, że Polska ma
  wiele miejsc o tej samej nazwie w różnych regionach - historia tego
  pliku to seria takich błędów (Augustów, Widuchowa, Słupca, ~300 innych
  przez pospolite nazwy jak "Chałupy", a nawet sam portal pasażera dla
  zagranicznego "Kolina", patrz wpis niżej) - PLK i portal to jedyne dwa
  źródła PIERWSZOOSOBOWE (dane samego zarządcy sieci / własnego sprzedawcy
  biletów), więc jedyne, które nie zgadują po nazwie. Konsekwencja: stacje
  ZA GRANICĄ (Berlin, Wiedeń, Kijów, ...) nie mają już żadnego źródła
  współrzędnych - żadne z dwóch pozostałych nie obejmuje zagranicy (patrz
  nagłówek `_fetch_portalpasazera_point`) - zostają bez markera zamiast
  zgadywanej pozycji; walidacja tras (`find_suspect_coords`) i mapa PLK/
  portal pasażera zostają bez zmian poza tym. Usunięte też: eksperymentalne
  automatyczne usuwanie ze słownika stacji tych bez żadnego kursu w danym
  oknie rozkładu (`build_database`, wprowadzone i tego samego dnia cofnięte
  na prośbę użytkownika) - `stations` znowu zawiera WSZYSTKO, co zwróci
  słownik API, niezależnie od tego, czy dana stacja ma jakikolwiek `stops`.
  Filtr w `pkp.all_station_names()` (tylko stacje z ustalonymi
  współrzędnymi - patrz wpis niżej) zostaje bez zmian - to inny, celowo
  zachowany mechanizm.

- **2026-08-29** — piąte źródło geokodowania: katalog stacji na
  portalpasazera.pl (oficjalny portal sprzedaży biletów PKP,
  `update_pkp._fetch_portalpasazera_point` - scraping pojedynczej strony,
  brak publicznego API, więc bez zbiorczego zapytania jak PLK/OSM).
  Zgłoszone przez użytkownika na żywo (Góra Śląska, Zwierzyniec - obu nie
  miały ani PLK, ani OSM, ani Nominatim, a portal miał). Kolejność w
  `geocode_missing_stations` zmieniona na wyraźną prośbę użytkownika: oba
  źródła PIERWSZOOSOBOWE PKP/PLK (mapa PLK, potem ten portal) mają
  pierwszeństwo przed zewnętrznymi geokoderami - dopiero to, czego żadne
  z dwóch nie znajdzie, dogania OpenStreetMap, a na końcu Nominatim
  (wcześniej było odwrotnie: OSM i Nominatim przed portalem). Uwaga
  znaleziona przy okazji: portal miewa TĘ SAMĄ pułapkę zbieżności nazw co
  Nominatim/OSM (patrz wpis niżej o Augustowie/Widuchowej) - dla
  zagranicznej stacji „Kolin” (Czechy) portal zwraca współrzędne zupełnie
  innej, przypadkowo tak samo nazwanej polskiej wsi (73-116 Kolin,
  zachodniopomorskie); złapane i odrzucone automatycznie przez istniejącą
  walidację tras (`find_suspect_coords` - sąsiedzi na trasie 350+ km od
  tego wyniku), więc stacja zostaje bez markera zamiast dostać zły.

- **2026-08-29** — `update_pkp.build_database` usuwa ze słownika stacji
  (tabela `stations`) każdą, która nie ma ŻADNEGO wpisu w `stops` w tym
  oknie rozkładu (`DELETE ... WHERE station_id NOT IN (SELECT DISTINCT
  station_id FROM stops)`, tuż po ich wstawieniu). Zgłoszone przez
  użytkownika: taka stacja i tak nigdy nie pojawi się w żadnej realnej
  trasie (żaden kurs przez nią nie przejeżdża), więc nie ma sensu jej
  geokodować (`_read_stations` czyta wprost z tej tabeli) ani pokazywać
  w podpowiedziach wyszukiwarki (`pkp.all_station_names`/`all_stations_geo`,
  ta sama tabela) - jedna zmiana zamiast osobnego filtra w obu miejscach.
  Na żywej bazie usunęło to 59 z 3266 stacji - część to osobne, nieużywane
  wpisy obok innych stacji o tej samej nazwie z realnym kursem (np. "Berlin
  Zoolog Garten"/"Berlin Hbf" - dokładnie te, które wcześniej dostały
  ręczną poprawkę nazwy w `NAME_OVERRIDES`/geokodowanie, na próżno - żaden
  kurs przez nie nie jeździ), część to jawnie syntetyczne wpisy-zaślepki
  ("WARSZAWA -", "BERLIN -", ...). Osierocone wpisy dla usuniętych stacji
  wyczyszczone też z `data/pkp_station_coords.json` (59) i
  `data/pkp_foreign_stations.json` (17) - pokrycie geokodowania: 3185/3207
  (99,3%) na przeciętej już bazie.

- **2026-08-29** — geokodowanie stacji PKP rozszerzone o trzy kolejne
  źródła ponad sam Nominatim (patrz wpis niżej o dwóch pierwszych błędach) -
  OFICJALNA mapa infrastruktury PLK (mapa.plk-sa.pl, warstwa "punkty
  eksploatacyjne", WFS przez GeoServer, `update_pkp._fetch_plk_points`,
  wymaga `pyproj` do konwersji EPSG:2180 → WGS84) jako NAJBARDZIEJ
  wiarygodne źródło (ok. 93% trafień samą nazwą stacji, bez żadnego
  dopasowania rozmytego); zbiorcze zapytanie do OpenStreetMap/Overpass
  (`_fetch_osm_stations`) jako drugie; Nominatim - już istniejący,
  ostrożny, per-stacja - dopiero jako trzecie, dla reszty. Osobny, czwarty
  mechanizm dla stacji ZA GRANICĄ (PKP ma połączenia międzynarodowe) -
  najpierw OSM/Overpass dla sąsiednich krajów (`FOREIGN_AREA_CODES`: DE,
  CZ, SK, AT, HU, SI, LT, UA, HR), potem Nominatim ograniczony do tych
  krajów, z trwałym oznaczeniem w `data/pkp_foreign_stations.json`, żeby
  kolejne uruchomienia nie próbowały ich już (bez sensu) w Polsce.

  Stała, automatyczna walidacja wpięta w `run_geocode()`:
  `find_suspect_coords`/`purge_suspect_coords` porównuje odległość między
  GEOGRAFICZNIE SĄSIADUJĄCYMI stacjami tej samej trasy (po `order_number`) -
  realne sąsiednie przystanki nigdy nie są >100 km od siebie, więc duży
  skok zdradza błędne dopasowanie nazwy (typowy przypadek: popularna nazwa
  miejscowości w dwóch różnych częściach Polski, np. "Chałupy" z półwyspu
  helskiego trafiające na Śląsk). Znaleziono i wyczyszczono tak ok. 300
  błędnych wpisów ze starej bazy; podejrzany wpis skasowany w jednym
  uruchomieniu dostaje szansę na poprawę z lepszego źródła w następnym,
  zamiast trwale zaśmiecać cache błędną współrzędną.

  Kolejność uzgadniania w `run_geocode()` ma znaczenie: OSM najpierw, PLK
  na końcu, żeby ono miało ostatnie słowo (`_reconcile` nadpisuje
  bezwarunkowo) - odwrotna kolejność niż intuicyjna "najpierw najlepsze
  źródło" jest tu celowa; pierwotna wersja (PLK→OSM) miała realnego buga,
  znalezionego i naprawionego w tej samej sesji - mniej wiarygodne OSM
  nadpisywało właśnie ustawione dane PLK przy każdym uruchomieniu.

  Dwa dodatkowe, ręcznie potwierdzone przypadki, których żadne z powyższych
  źródeł nie łapie automatycznie: `NAME_OVERRIDES` (nazwy stacji PKP bywają
  URWANE, nie tylko z literówką - "Berlin Zoolog Garten" zamiast "Berlin
  Zoologischer Garten" nie złapie żadne dopasowanie tekstowe; poprawka na
  wejściu do geokodowania w `_read_stations`, tylko dla zapytań - nazwa
  wyświetlana w aplikacji zostaje bez zmian) i `_name_matches_abroad`
  (dopasowanie zagraniczne bez węzła kolejowego wybierało dotąd PIERWSZY
  wynik rankingu Nominatim bez sprawdzania, czy w ogóle pasuje nazwą -
  "Vac" trafiało na niemieckie lotnisko zamiast węgierskiego Vác, "Rijeka"
  na plac w Neuss zamiast Chorwacji; naprawione filtrem po nazwie bez
  znaków diakrytycznych, z przepustką dla alfabetów niełacińskich, gdzie
  porównanie litera-w-literę jest z definicji niemożliwe - patrz nagłówek
  `_geocode_one_abroad`).

  Wynik: pokrycie 3242/3266 stacji (99,2%).

- **2026-08-29** — plakietka "PKP" przy nazwach stacji kolejowych na liście
  podpowiedzi i na mapie, odróżniająca je od przystanków MPK - wyłącznie
  warstwa wyświetlania (`routes.py`: dane podpowiedzi i markerów niosą
  `{name, kind}` zamiast gołej nazwy; `static/app.js`/`static/style.css`:
  `.ac-tag`) - nazwa w bazie (`stations.name`) zostaje czysta, żeby nie
  zepsuć dopasowywania w wyszukiwarce.

- **2026-08-29** — wyszukiwarka kolejowa (`pkp.py`) przebudowana z osobnego
  silnika (własne zapytania SQL o połączenia bezpośrednie, sklejane
  z wynikiem MPK w `routes.py` specjalnymi gałęziami — `rail_only`,
  „stacja-brama") na PRAWDZIWE połączenie z Connection Scanem w
  `planner.py`: `pkp.augment_day()` dokleja kursy kolejowe wprost do tej
  samej tablicy połączeń (`gtfs.DayData.conns`), którą wczytuje
  `gtfs.load_day()` — dla CSA stacja PKP to od tej pory zwykły przystanek,
  a przesiadka pociąg↔MPK to zwykła przesiadka (przez `siblings`, gdy
  stacja PKP leży bliżej niż `pkp.TRANSFER_RADIUS_M`=500 m od przystanku
  MPK — ten sam mechanizm i ten sam bufor czasowy co przesiadka między
  słupkami jednego miejsca, żaden nowy). Powód: poprzednia wersja umiała
  tylko połączenia BEZPOŚREDNIE i wymagała w `routes.py` trzech osobnych
  gałęzi (`_pkp_point_fallback`, `_combined_journeys`, `_rail_only_result`)
  próbujących z zewnątrz odtworzyć to, co scalony CSA dostaje za darmo -
  w tym przesiadki MIĘDZY pociągami, których stara wersja w ogóle nie
  widziała. `/api/flow` woła teraz `plan_flow` dokładnie tak samo, jak przed
  PKP - żadnego pola `rail_only` w odpowiedzi, żadnej wiedzy o PKP
  w `routes.py`.

  Stacja PKP dostaje syntetyczny stop_id `PKP:<id>` (prefiks jak
  `siechnice.ID_PREFIX`) i wchodzi do rozkładu tylko z ustalonymi
  współrzędnymi (geokodowanie, patrz wpis niżej) - stacje pośrednie kursu
  bez współrzędnych są pomijane w sekwencji (pociąg "przeskakuje" przez nie
  w grafie połączeń; realny rozkład się nie zmienia, zmienia się tylko to,
  co da się pokazać). Czas kursu liczony jest z narastającą korektą +24h przy
  przejściu przez północ (ten sam pomysł co `gtfs.PREV_DAY_SEC`, liczony
  w locie, bo API PKP nie zapisuje godzin >23:59 tak jak GTFS).
  `gtfs.trip_path()` dostał drugie źródło danych dla kursów `PKP:` -
  sekwencję przystanków zapisaną przy doklejaniu (`day.pkp_trip_stops`),
  bez drugiego zapytania do żadnej bazy (patrz `pkp.trip_path`) - dzięki
  temu etap kolejowy w liście propozycji ma teraz dokładną liczbę stacji
  (`stops_count`), a nie zgadywaną.

  Przy okazji naprawiony realny bug znaleziony w trakcie tej przebudowy:
  `gtfs.load_day()` woła teraz `pkp.augment_day()` bezwarunkowo, co bez
  zabezpieczenia sprawiało, że KAŻDY test wołający `load_day` (nie tylko
  testy PKP) sięgał po prawdziwy `data/pkp.sqlite` i prawdziwy
  `PKP_API_KEY` ze środowiska uruchamiającego pytest - łamało to
  hermetyczność całego zestawu testów i spowalniało go dziesięciokrotnie.
  Naprawka: `tests/conftest.py` dostał autouse fixture wyłączający PKP
  domyślnie dla wszystkich testów; testy, którym PKP jest faktycznie
  potrzebne (`tests/test_pkp.py`), same je z powrotem włączają.
- **2026-08-29** — dokładność geokodowania stacji PKP (`update_pkp.py`,
  patrz `_geocode_one`) - dwa niezależne błędy znalezione na żywo, oba
  naprawione zmianą samej strategii zapytań do Nominatim, bez nowej
  zależności:
  1. Samo imię stacji ("Augustów") trafiało w GRANICĘ ADMINISTRACYJNĄ
     miasta (Nominatim zwraca ją jako najważniejszy wynik), nie w sam
     dworzec - dla stacji nazwanych tak samo jak miejscowość (większość)
     błąd bywał rzędu 1-2 km, czasem po drugiej stronie miasta. Naprawione
     pytaniem wprost o STACJĘ KOLEJOWĄ ("stacja kolejowa {name}" - fraza
     specjalna Nominatim, ogranicza wynik do węzłów `railway=station`);
     sama nazwa zostaje tylko awaryjnym drugim poziomem, gdy to nic nie da
     (małe przystanki bywają w OSM otagowane inaczej).
  2. Na tym drugim, awaryjnym poziomie ujawnił się kolejny błąd: miejscowości
     o tej samej nazwie w RÓŻNYCH częściach Polski (sprawdzone na żywo:
     dwie „Widuchowa” - jedna pod Kielcami, druga pod Szczecinem, gdzie
     naprawdę jest ta stacja) - Nominatim ocenia GRANICĘ administracyjną
     złej miejscowości wyżej niż PUNKT osiedla właściwej. Naprawione
     preferencją `class == "place"` nad `class == "boundary"` na tym
     poziomie - dopiero gdy żadnego "place" nie ma, bierzemy cokolwiek się
     trafiło.

  Oba błędy wymagają pełnego przegeokodowania (stara baza
  `data/pkp_station_coords.json` skasowana i budowana od zera - patrz
  `geocode_missing_stations`, uzupełnia tylko BRAKUJĄCE wpisy, więc raz
  zapisany błędny wynik nigdy by się sam nie poprawił). Przy okazji
  znaleziona i naprawiona usterka we własnym kodzie: `_geocode_one` umie
  wysłać DWA zapytania do Nominatim na stację (najpierw o stację kolejową,
  potem - dopiero gdy to nic nie da - o samą nazwę), a limit tempa
  (1 zapytanie/s, polityka użytkowania Nominatim) był pilnowany tylko RAZ
  NA STACJĘ, nie raz na zapytanie - dwa zapytania dla tej samej stacji
  potrafiły więc wyjść bez odstępu. Limit przeniesiony do samego
  `_nominatim_search` (tuż przed wysłaniem żądania), więc pilnuje każdego
  zapytania z osobna, niezależnie od tego, ile ich potrzebuje jedna stacja.
- **2026-08-29** — dwie luki w wyszukiwarce kolejowej (patrz wpisy niżej),
  obie w `routes.py`, obie bez zmian w planner.py:
  1. Wpisanie nazwy, która jest stacją PKP, ale nie przystankiem MPK
     (np. „Warszawa Centralna”), kończyło się błędem „nie znam przystanku” -
     nawet jeśli druga strona relacji była zwykłym przystankiem MPK.
     `plan_flow` już umiał przyjąć PUNKT zamiast nazwy (klik w mapę - MPK
     samo szuka najbliższych swoich przystanków), więc `_pkp_point_fallback`
     po prostu podaje mu współrzędne stacji PKP zamiast nazwy, której MPK
     nie zna - bez dotykania planner.py. Błąd wraca dopiero, gdy ANI MPK
     (nawet po tych współrzędnych), ANI PKP nie znajdą nic dla żadnej ze
     stron.
  2. Relacja, w której start jest stacją PKP bez ŻADNEGO przystanku MPK
     w pobliżu (mała miejscowość bez komunikacji miejskiej), a cel jest
     zwykłym przystankiem MPK bez stacji PKP w pobliżu, nie dawała nic - PKP
     zna tylko połączenia BEZPOŚREDNIE (patrz pkp.py), a taki cel nigdy nie
     jest stacją PKP. `_combined_journeys` szuka więc stacji-BRAMY: spośród
     wszystkich stacji osiągalnych bezpośrednim pociągiem ze startu
     (`pkp._direct_destinations`) bierze te, które mają choć jeden przystanek
     MPK w pobliżu (`gtfs.nearby_stops`), i dokleja do najwcześniejszego
     takiego pociągu najlepszą trasę MPK od bramy do celu (`plan_flow`
     z punktem startowym = współrzędne bramy, wywołane na czas przyjazdu
     pociągu + 8 min zapasu na przesiadkę) - jeden `journey` z etapami obu
     trybów na liście, bez zmian we froncie (etapy już są generyczne po
     `kind`/`mode`). Sprawdzanie samych współrzędnych (tanie: `nearby_stops`)
     jest celowo hojniejsze (do 600 różnych stacji docelowych) niż próby
     pełnego zapytania `plan_flow` do konkretnej bramy (tylko 6, bo to już
     kosztowne) - z dużego węzła (Warszawa Centralna: 431 różnych stacji
     docelowych tego samego dnia) większość bezpośrednich pociągów jedzie
     gdzieś, gdzie MPK w ogóle nie kursuje, więc pierwszy, tańszy sufit musi
     przejrzeć ich sporo, zanim w ogóle trafi na prawdziwą bramę.
- **2026-08-29** — wyszukiwarka kolejowa (patrz wpis niżej) przepięta
  z odpytywania API PKP przy KAŻDYM wyszukiwaniu na lokalny pipeline
  (`update_pkp.py`), tym samym schematem co GTFS dla MPK: jedno zapytanie
  o rozkład całego kraju na okno `SCHEDULE_WINDOW_DAYS` dni (dziś, sprawdzone
  na żywo: ~26 MB, 18,6 tys. tras, 8 s budowy lokalnej bazy `data/pkp.sqlite`)
  zamiast osobnego zapytania na każdą wpisaną parę stacji — `pkp.py` czyta
  już tylko SQL do pliku na dysku, bez sieci. Powód: przy limicie 100
  zapytań/h w planie Basic i suwakach panelu deweloperskiego (każdy ruch
  suwaka odpala `/api/flow` od nowa) osobne zapytanie na wyszukiwanie
  wyczerpałoby limit w kilka minut używania. `operatingDates` w odpowiedzi
  API działa jak GTFS-owy `calendar.txt` (jeden wpis trasy niesie od razu
  wszystkie daty w oknie, więc szersze okno nie mnoży liczby tras) —
  harmonogram odświeżania (`PKP_AUTO_UPDATE_HOUR`/`PKP_UPDATE_ON_START`,
  domyślnie jak GTFS) i atomowa podmiana (`os.replace`) są więc dosłownie
  tym samym mechanizmem co `update_gtfs.py`, tylko osobnym plikiem — bo to
  niezależny pipeline nad niezależnym źródłem.

  Przy okazji: nazwy stacji PKP trafiły do TEJ SAMEJ listy podpowiedzi
  w formularzu co przystanki MPK (`pkp.all_station_names()` dokładane
  w `routes.py`/`index()`) — wcześniej dało się o nie zapytać przez API, ale
  nie dało się ich wpisać z podpowiedzią na stronie. I stacje PKP dostały
  markery na mapie (`/api/stops`, pole `kind: "train"`, styl w
  `static/app.js`) — czego wcześniej nie było wcale, bo słownik stacji PKP
  (jak cała reszta tego API — sprawdzone pole po polu w
  `/api/v1/fields/schedules`) nie ma NIGDZIE współrzędnych. Jedyny sposób,
  żeby jednak je zdobyć, to DRUGIE, niezależne źródło: `update_pkp.py`
  geokoduje nazwy stacji przez publiczne, darmowe Nominatim (OpenStreetMap),
  z limitem uprzejmościowym 1 zapytanie/s i trwałym cache'em na dysku
  (`data/pkp_station_coords.json`) — raz znaleziona stacja nigdy nie jest
  odpytywana drugi raz, więc pierwsze wypełnienie cache'u (~3266 stacji, ok.
  godziny) jest zarazem jedynym kosztownym; kolejne przebudowy rozkładu
  dogadują już tylko naprawdę nowe stacje. Współrzędne z geokodowania są
  przybliżeniem (czasem trafiają w środek miasta zamiast w sam dworzec) -
  stacja bez trafienia po prostu nie ma markera, reszta (wyszukiwanie,
  podpowiedzi) działa dla niej mimo to.
- **2026-08-29** — bezpośrednie połączenia kolejowe (`pkp.py`, PKP PLK
  OpenData API, `pdp-api.plk-sa.pl`) dokładane do tej samej listy propozycji
  co tramwaje i autobusy, bez osobnego formularza — działa na tych samych
  polach „skąd"/„dokąd". Zakres świadomie ograniczony do połączeń BEZ
  przesiadek: to API ma tylko rozkład per stacja i dzień (`/schedules`), nie
  gotowy planer trasy, a własny CSA nad krajowym rozkładem (setki tysięcy
  kursów dziennie) to osobny, dużo większy projekt. Kierunek pociągu
  (start → cel, nie odwrotnie) czyta się z `orderNumber` — pozycji
  przystanku w całej trasie — bo `/schedules?stations=A,B` oddaje wpisy
  tylko dla stacji A i B, nie całą trasę. Etapy kolejowe nie mają geometrii
  (słownik stacji PKP nie niesie współrzędnych) — trafiają na listę z pełnymi
  godzinami i nazwami stacji, ale bez linii na mapie (`static/app.js`
  dostał stąd guard na brakujący `leg.path`, wcześniej zakładany jako zawsze
  obecny). Gdy MPK nie zna żadnej z podanych nazw (typowa relacja
  międzymiastowa, np. do „Warszawa Centralna"), ale PKP zna obie stacje i
  znalazło połączenie, `/api/flow` mimo to zwraca sukces (`rail_only: true`,
  `segments: []`) zamiast błędu „nie znam przystanku" — błąd tramwajowo-
  -autobusowy nie ma prawa ukryć wyniku kolejowego. Słownik stacji PKP
  (3266 pozycji) cache'owany na dysku i odświeżany raz na dobę — przy
  limicie 100 zapytań/h w planie Basic nie da się odpytywać go przy każdym
  wyszukiwaniu; sam rozkład cache'owany w pamięci na kilka minut, bo panel
  deweloperski odpala `/api/flow` przy każdym ruchu suwaka. Brak klucza
  `PKP_API_KEY` (patrz `config.py`) wyłącza funkcję po cichu, tak samo jak
  Siechnice bez `SIECHNICE_ENABLED` — awaria albo brak konfiguracji tego
  kroku nie ma prawa wywrócić wyszukiwania tramwajowo-autobusowego.
- **2026-08-30** — geokodowanie stacji PKP uproszczone na wyraźną prośbę
  użytkownika do DWÓCH źródeł: WYŁĄCZNIE mapa PLK i portal pasażera -
  OpenStreetMap/Overpass i Nominatim (razem z całym mechanizmem "stacje za
  granicą", który na nich polegał) usunięte z `update_pkp.py` całkowicie,
  nie tylko zdegradowane do zapasowych. Powód: oba to zewnętrzne geokodery
  dopasowujące po samej nazwie miejscowości, bez wiedzy o tym, że Polska ma
  wiele miejsc o tej samej nazwie w różnych regionach - historia tego
  pliku to seria takich błędów (Augustów, Widuchowa, Słupca, ~300 innych
  przez pospolite nazwy jak "Chałupy", a nawet sam portal pasażera dla
  zagranicznego "Kolina", patrz wpis niżej) - PLK i portal to jedyne dwa
  źródła PIERWSZOOSOBOWE (dane samego zarządcy sieci / własnego sprzedawcy
  biletów), więc jedyne, które nie zgadują po nazwie. Konsekwencja: stacje
  ZA GRANICĄ (Berlin, Wiedeń, Kijów, ...) nie mają już żadnego źródła
  współrzędnych - żadne z dwóch pozostałych nie obejmuje zagranicy (patrz
  nagłówek `_fetch_portalpasazera_point`) - zostają bez markera zamiast
  zgadywanej pozycji; walidacja tras (`find_suspect_coords`) i mapa PLK/
  portal pasażera zostają bez zmian poza tym. Usunięte też: eksperymentalne
  automatyczne usuwanie ze słownika stacji tych bez żadnego kursu w danym
  oknie rozkładu (`build_database`, wprowadzone i tego samego dnia cofnięte
  na prośbę użytkownika) - `stations` znowu zawiera WSZYSTKO, co zwróci
  słownik API, niezależnie od tego, czy dana stacja ma jakikolwiek `stops`.
  Filtr w `pkp.all_station_names()` (tylko stacje z ustalonymi
  współrzędnymi - patrz wpis niżej) zostaje bez zmian - to inny, celowo
  zachowany mechanizm.

- **2026-08-29** — piąte źródło geokodowania: katalog stacji na
  portalpasazera.pl (oficjalny portal sprzedaży biletów PKP,
  `update_pkp._fetch_portalpasazera_point` - scraping pojedynczej strony,
  brak publicznego API, więc bez zbiorczego zapytania jak PLK/OSM).
  Zgłoszone przez użytkownika na żywo (Góra Śląska, Zwierzyniec - obu nie
  miały ani PLK, ani OSM, ani Nominatim, a portal miał). Kolejność w
  `geocode_missing_stations` zmieniona na wyraźną prośbę użytkownika: oba
  źródła PIERWSZOOSOBOWE PKP/PLK (mapa PLK, potem ten portal) mają
  pierwszeństwo przed zewnętrznymi geokoderami - dopiero to, czego żadne
  z dwóch nie znajdzie, dogania OpenStreetMap, a na końcu Nominatim
  (wcześniej było odwrotnie: OSM i Nominatim przed portalem). Uwaga
  znaleziona przy okazji: portal miewa TĘ SAMĄ pułapkę zbieżności nazw co
  Nominatim/OSM (patrz wpis niżej o Augustowie/Widuchowej) - dla
  zagranicznej stacji „Kolin” (Czechy) portal zwraca współrzędne zupełnie
  innej, przypadkowo tak samo nazwanej polskiej wsi (73-116 Kolin,
  zachodniopomorskie); złapane i odrzucone automatycznie przez istniejącą
  walidację tras (`find_suspect_coords` - sąsiedzi na trasie 350+ km od
  tego wyniku), więc stacja zostaje bez markera zamiast dostać zły.

- **2026-08-29** — `update_pkp.build_database` usuwa ze słownika stacji
  (tabela `stations`) każdą, która nie ma ŻADNEGO wpisu w `stops` w tym
  oknie rozkładu (`DELETE ... WHERE station_id NOT IN (SELECT DISTINCT
  station_id FROM stops)`, tuż po ich wstawieniu). Zgłoszone przez
  użytkownika: taka stacja i tak nigdy nie pojawi się w żadnej realnej
  trasie (żaden kurs przez nią nie przejeżdża), więc nie ma sensu jej
  geokodować (`_read_stations` czyta wprost z tej tabeli) ani pokazywać
  w podpowiedziach wyszukiwarki (`pkp.all_station_names`/`all_stations_geo`,
  ta sama tabela) - jedna zmiana zamiast osobnego filtra w obu miejscach.
  Na żywej bazie usunęło to 59 z 3266 stacji - część to osobne, nieużywane
  wpisy obok innych stacji o tej samej nazwie z realnym kursem (np. "Berlin
  Zoolog Garten"/"Berlin Hbf" - dokładnie te, które wcześniej dostały
  ręczną poprawkę nazwy w `NAME_OVERRIDES`/geokodowanie, na próżno - żaden
  kurs przez nie nie jeździ), część to jawnie syntetyczne wpisy-zaślepki
  ("WARSZAWA -", "BERLIN -", ...). Osierocone wpisy dla usuniętych stacji
  wyczyszczone też z `data/pkp_station_coords.json` (59) i
  `data/pkp_foreign_stations.json` (17) - pokrycie geokodowania: 3185/3207
  (99,3%) na przeciętej już bazie.

- **2026-08-29** — geokodowanie stacji PKP rozszerzone o trzy kolejne
  źródła ponad sam Nominatim (patrz wpis niżej o dwóch pierwszych błędach) -
  OFICJALNA mapa infrastruktury PLK (mapa.plk-sa.pl, warstwa "punkty
  eksploatacyjne", WFS przez GeoServer, `update_pkp._fetch_plk_points`,
  wymaga `pyproj` do konwersji EPSG:2180 → WGS84) jako NAJBARDZIEJ
  wiarygodne źródło (ok. 93% trafień samą nazwą stacji, bez żadnego
  dopasowania rozmytego); zbiorcze zapytanie do OpenStreetMap/Overpass
  (`_fetch_osm_stations`) jako drugie; Nominatim - już istniejący,
  ostrożny, per-stacja - dopiero jako trzecie, dla reszty. Osobny, czwarty
  mechanizm dla stacji ZA GRANICĄ (PKP ma połączenia międzynarodowe) -
  najpierw OSM/Overpass dla sąsiednich krajów (`FOREIGN_AREA_CODES`: DE,
  CZ, SK, AT, HU, SI, LT, UA, HR), potem Nominatim ograniczony do tych
  krajów, z trwałym oznaczeniem w `data/pkp_foreign_stations.json`, żeby
  kolejne uruchomienia nie próbowały ich już (bez sensu) w Polsce.

  Stała, automatyczna walidacja wpięta w `run_geocode()`:
  `find_suspect_coords`/`purge_suspect_coords` porównuje odległość między
  GEOGRAFICZNIE SĄSIADUJĄCYMI stacjami tej samej trasy (po `order_number`) -
  realne sąsiednie przystanki nigdy nie są >100 km od siebie, więc duży
  skok zdradza błędne dopasowanie nazwy (typowy przypadek: popularna nazwa
  miejscowości w dwóch różnych częściach Polski, np. "Chałupy" z półwyspu
  helskiego trafiające na Śląsk). Znaleziono i wyczyszczono tak ok. 300
  błędnych wpisów ze starej bazy; podejrzany wpis skasowany w jednym
  uruchomieniu dostaje szansę na poprawę z lepszego źródła w następnym,
  zamiast trwale zaśmiecać cache błędną współrzędną.

  Kolejność uzgadniania w `run_geocode()` ma znaczenie: OSM najpierw, PLK
  na końcu, żeby ono miało ostatnie słowo (`_reconcile` nadpisuje
  bezwarunkowo) - odwrotna kolejność niż intuicyjna "najpierw najlepsze
  źródło" jest tu celowa; pierwotna wersja (PLK→OSM) miała realnego buga,
  znalezionego i naprawionego w tej samej sesji - mniej wiarygodne OSM
  nadpisywało właśnie ustawione dane PLK przy każdym uruchomieniu.

  Dwa dodatkowe, ręcznie potwierdzone przypadki, których żadne z powyższych
  źródeł nie łapie automatycznie: `NAME_OVERRIDES` (nazwy stacji PKP bywają
  URWANE, nie tylko z literówką - "Berlin Zoolog Garten" zamiast "Berlin
  Zoologischer Garten" nie złapie żadne dopasowanie tekstowe; poprawka na
  wejściu do geokodowania w `_read_stations`, tylko dla zapytań - nazwa
  wyświetlana w aplikacji zostaje bez zmian) i `_name_matches_abroad`
  (dopasowanie zagraniczne bez węzła kolejowego wybierało dotąd PIERWSZY
  wynik rankingu Nominatim bez sprawdzania, czy w ogóle pasuje nazwą -
  "Vac" trafiało na niemieckie lotnisko zamiast węgierskiego Vác, "Rijeka"
  na plac w Neuss zamiast Chorwacji; naprawione filtrem po nazwie bez
  znaków diakrytycznych, z przepustką dla alfabetów niełacińskich, gdzie
  porównanie litera-w-literę jest z definicji niemożliwe - patrz nagłówek
  `_geocode_one_abroad`).

  Wynik: pokrycie 3242/3266 stacji (99,2%).

- **2026-08-29** — plakietka "PKP" przy nazwach stacji kolejowych na liście
  podpowiedzi i na mapie, odróżniająca je od przystanków MPK - wyłącznie
  warstwa wyświetlania (`routes.py`: dane podpowiedzi i markerów niosą
  `{name, kind}` zamiast gołej nazwy; `static/app.js`/`static/style.css`:
  `.ac-tag`) - nazwa w bazie (`stations.name`) zostaje czysta, żeby nie
  zepsuć dopasowywania w wyszukiwarce.

- **2026-08-29** — wyszukiwarka kolejowa (`pkp.py`) przebudowana z osobnego
  silnika (własne zapytania SQL o połączenia bezpośrednie, sklejane
  z wynikiem MPK w `routes.py` specjalnymi gałęziami — `rail_only`,
  „stacja-brama") na PRAWDZIWE połączenie z Connection Scanem w
  `planner.py`: `pkp.augment_day()` dokleja kursy kolejowe wprost do tej
  samej tablicy połączeń (`gtfs.DayData.conns`), którą wczytuje
  `gtfs.load_day()` — dla CSA stacja PKP to od tej pory zwykły przystanek,
  a przesiadka pociąg↔MPK to zwykła przesiadka (przez `siblings`, gdy
  stacja PKP leży bliżej niż `pkp.TRANSFER_RADIUS_M`=500 m od przystanku
  MPK — ten sam mechanizm i ten sam bufor czasowy co przesiadka między
  słupkami jednego miejsca, żaden nowy). Powód: poprzednia wersja umiała
  tylko połączenia BEZPOŚREDNIE i wymagała w `routes.py` trzech osobnych
  gałęzi (`_pkp_point_fallback`, `_combined_journeys`, `_rail_only_result`)
  próbujących z zewnątrz odtworzyć to, co scalony CSA dostaje za darmo -
  w tym przesiadki MIĘDZY pociągami, których stara wersja w ogóle nie
  widziała. `/api/flow` woła teraz `plan_flow` dokładnie tak samo, jak przed
  PKP - żadnego pola `rail_only` w odpowiedzi, żadnej wiedzy o PKP
  w `routes.py`.

  Stacja PKP dostaje syntetyczny stop_id `PKP:<id>` (prefiks jak
  `siechnice.ID_PREFIX`) i wchodzi do rozkładu tylko z ustalonymi
  współrzędnymi (geokodowanie, patrz wpis niżej) - stacje pośrednie kursu
  bez współrzędnych są pomijane w sekwencji (pociąg "przeskakuje" przez nie
  w grafie połączeń; realny rozkład się nie zmienia, zmienia się tylko to,
  co da się pokazać). Czas kursu liczony jest z narastającą korektą +24h przy
  przejściu przez północ (ten sam pomysł co `gtfs.PREV_DAY_SEC`, liczony
  w locie, bo API PKP nie zapisuje godzin >23:59 tak jak GTFS).
  `gtfs.trip_path()` dostał drugie źródło danych dla kursów `PKP:` -
  sekwencję przystanków zapisaną przy doklejaniu (`day.pkp_trip_stops`),
  bez drugiego zapytania do żadnej bazy (patrz `pkp.trip_path`) - dzięki
  temu etap kolejowy w liście propozycji ma teraz dokładną liczbę stacji
  (`stops_count`), a nie zgadywaną.

  Przy okazji naprawiony realny bug znaleziony w trakcie tej przebudowy:
  `gtfs.load_day()` woła teraz `pkp.augment_day()` bezwarunkowo, co bez
  zabezpieczenia sprawiało, że KAŻDY test wołający `load_day` (nie tylko
  testy PKP) sięgał po prawdziwy `data/pkp.sqlite` i prawdziwy
  `PKP_API_KEY` ze środowiska uruchamiającego pytest - łamało to
  hermetyczność całego zestawu testów i spowalniało go dziesięciokrotnie.
  Naprawka: `tests/conftest.py` dostał autouse fixture wyłączający PKP
  domyślnie dla wszystkich testów; testy, którym PKP jest faktycznie
  potrzebne (`tests/test_pkp.py`), same je z powrotem włączają.
- **2026-08-29** — dokładność geokodowania stacji PKP (`update_pkp.py`,
  patrz `_geocode_one`) - dwa niezależne błędy znalezione na żywo, oba
  naprawione zmianą samej strategii zapytań do Nominatim, bez nowej
  zależności:
  1. Samo imię stacji ("Augustów") trafiało w GRANICĘ ADMINISTRACYJNĄ
     miasta (Nominatim zwraca ją jako najważniejszy wynik), nie w sam
     dworzec - dla stacji nazwanych tak samo jak miejscowość (większość)
     błąd bywał rzędu 1-2 km, czasem po drugiej stronie miasta. Naprawione
     pytaniem wprost o STACJĘ KOLEJOWĄ ("stacja kolejowa {name}" - fraza
     specjalna Nominatim, ogranicza wynik do węzłów `railway=station`);
     sama nazwa zostaje tylko awaryjnym drugim poziomem, gdy to nic nie da
     (małe przystanki bywają w OSM otagowane inaczej).
  2. Na tym drugim, awaryjnym poziomie ujawnił się kolejny błąd: miejscowości
     o tej samej nazwie w RÓŻNYCH częściach Polski (sprawdzone na żywo:
     dwie „Widuchowa” - jedna pod Kielcami, druga pod Szczecinem, gdzie
     naprawdę jest ta stacja) - Nominatim ocenia GRANICĘ administracyjną
     złej miejscowości wyżej niż PUNKT osiedla właściwej. Naprawione
     preferencją `class == "place"` nad `class == "boundary"` na tym
     poziomie - dopiero gdy żadnego "place" nie ma, bierzemy cokolwiek się
     trafiło.

  Oba błędy wymagają pełnego przegeokodowania (stara baza
  `data/pkp_station_coords.json` skasowana i budowana od zera - patrz
  `geocode_missing_stations`, uzupełnia tylko BRAKUJĄCE wpisy, więc raz
  zapisany błędny wynik nigdy by się sam nie poprawił). Przy okazji
  znaleziona i naprawiona usterka we własnym kodzie: `_geocode_one` umie
  wysłać DWA zapytania do Nominatim na stację (najpierw o stację kolejową,
  potem - dopiero gdy to nic nie da - o samą nazwę), a limit tempa
  (1 zapytanie/s, polityka użytkowania Nominatim) był pilnowany tylko RAZ
  NA STACJĘ, nie raz na zapytanie - dwa zapytania dla tej samej stacji
  potrafiły więc wyjść bez odstępu. Limit przeniesiony do samego
  `_nominatim_search` (tuż przed wysłaniem żądania), więc pilnuje każdego
  zapytania z osobna, niezależnie od tego, ile ich potrzebuje jedna stacja.
- **2026-08-29** — dwie luki w wyszukiwarce kolejowej (patrz wpisy niżej),
  obie w `routes.py`, obie bez zmian w planner.py:
  1. Wpisanie nazwy, która jest stacją PKP, ale nie przystankiem MPK
     (np. „Warszawa Centralna”), kończyło się błędem „nie znam przystanku” -
     nawet jeśli druga strona relacji była zwykłym przystankiem MPK.
     `plan_flow` już umiał przyjąć PUNKT zamiast nazwy (klik w mapę - MPK
     samo szuka najbliższych swoich przystanków), więc `_pkp_point_fallback`
     po prostu podaje mu współrzędne stacji PKP zamiast nazwy, której MPK
     nie zna - bez dotykania planner.py. Błąd wraca dopiero, gdy ANI MPK
     (nawet po tych współrzędnych), ANI PKP nie znajdą nic dla żadnej ze
     stron.
  2. Relacja, w której start jest stacją PKP bez ŻADNEGO przystanku MPK
     w pobliżu (mała miejscowość bez komunikacji miejskiej), a cel jest
     zwykłym przystankiem MPK bez stacji PKP w pobliżu, nie dawała nic - PKP
     zna tylko połączenia BEZPOŚREDNIE (patrz pkp.py), a taki cel nigdy nie
     jest stacją PKP. `_combined_journeys` szuka więc stacji-BRAMY: spośród
     wszystkich stacji osiągalnych bezpośrednim pociągiem ze startu
     (`pkp._direct_destinations`) bierze te, które mają choć jeden przystanek
     MPK w pobliżu (`gtfs.nearby_stops`), i dokleja do najwcześniejszego
     takiego pociągu najlepszą trasę MPK od bramy do celu (`plan_flow`
     z punktem startowym = współrzędne bramy, wywołane na czas przyjazdu
     pociągu + 8 min zapasu na przesiadkę) - jeden `journey` z etapami obu
     trybów na liście, bez zmian we froncie (etapy już są generyczne po
     `kind`/`mode`). Sprawdzanie samych współrzędnych (tanie: `nearby_stops`)
     jest celowo hojniejsze (do 600 różnych stacji docelowych) niż próby
     pełnego zapytania `plan_flow` do konkretnej bramy (tylko 6, bo to już
     kosztowne) - z dużego węzła (Warszawa Centralna: 431 różnych stacji
     docelowych tego samego dnia) większość bezpośrednich pociągów jedzie
     gdzieś, gdzie MPK w ogóle nie kursuje, więc pierwszy, tańszy sufit musi
     przejrzeć ich sporo, zanim w ogóle trafi na prawdziwą bramę.
- **2026-08-29** — wyszukiwarka kolejowa (patrz wpis niżej) przepięta
  z odpytywania API PKP przy KAŻDYM wyszukiwaniu na lokalny pipeline
  (`update_pkp.py`), tym samym schematem co GTFS dla MPK: jedno zapytanie
  o rozkład całego kraju na okno `SCHEDULE_WINDOW_DAYS` dni (dziś, sprawdzone
  na żywo: ~26 MB, 18,6 tys. tras, 8 s budowy lokalnej bazy `data/pkp.sqlite`)
  zamiast osobnego zapytania na każdą wpisaną parę stacji — `pkp.py` czyta
  już tylko SQL do pliku na dysku, bez sieci. Powód: przy limicie 100
  zapytań/h w planie Basic i suwakach panelu deweloperskiego (każdy ruch
  suwaka odpala `/api/flow` od nowa) osobne zapytanie na wyszukiwanie
  wyczerpałoby limit w kilka minut używania. `operatingDates` w odpowiedzi
  API działa jak GTFS-owy `calendar.txt` (jeden wpis trasy niesie od razu
  wszystkie daty w oknie, więc szersze okno nie mnoży liczby tras) —
  harmonogram odświeżania (`PKP_AUTO_UPDATE_HOUR`/`PKP_UPDATE_ON_START`,
  domyślnie jak GTFS) i atomowa podmiana (`os.replace`) są więc dosłownie
  tym samym mechanizmem co `update_gtfs.py`, tylko osobnym plikiem — bo to
  niezależny pipeline nad niezależnym źródłem.

  Przy okazji: nazwy stacji PKP trafiły do TEJ SAMEJ listy podpowiedzi
  w formularzu co przystanki MPK (`pkp.all_station_names()` dokładane
  w `routes.py`/`index()`) — wcześniej dało się o nie zapytać przez API, ale
  nie dało się ich wpisać z podpowiedzią na stronie. I stacje PKP dostały
  markery na mapie (`/api/stops`, pole `kind: "train"`, styl w
  `static/app.js`) — czego wcześniej nie było wcale, bo słownik stacji PKP
  (jak cała reszta tego API — sprawdzone pole po polu w
  `/api/v1/fields/schedules`) nie ma NIGDZIE współrzędnych. Jedyny sposób,
  żeby jednak je zdobyć, to DRUGIE, niezależne źródło: `update_pkp.py`
  geokoduje nazwy stacji przez publiczne, darmowe Nominatim (OpenStreetMap),
  z limitem uprzejmościowym 1 zapytanie/s i trwałym cache'em na dysku
  (`data/pkp_station_coords.json`) — raz znaleziona stacja nigdy nie jest
  odpytywana drugi raz, więc pierwsze wypełnienie cache'u (~3266 stacji, ok.
  godziny) jest zarazem jedynym kosztownym; kolejne przebudowy rozkładu
  dogadują już tylko naprawdę nowe stacje. Współrzędne z geokodowania są
  przybliżeniem (czasem trafiają w środek miasta zamiast w sam dworzec) -
  stacja bez trafienia po prostu nie ma markera, reszta (wyszukiwanie,
  podpowiedzi) działa dla niej mimo to.
- **2026-08-29** — bezpośrednie połączenia kolejowe (`pkp.py`, PKP PLK
  OpenData API, `pdp-api.plk-sa.pl`) dokładane do tej samej listy propozycji
  co tramwaje i autobusy, bez osobnego formularza — działa na tych samych
  polach „skąd"/„dokąd". Zakres świadomie ograniczony do połączeń BEZ
  przesiadek: to API ma tylko rozkład per stacja i dzień (`/schedules`), nie
  gotowy planer trasy, a własny CSA nad krajowym rozkładem (setki tysięcy
  kursów dziennie) to osobny, dużo większy projekt. Kierunek pociągu
  (start → cel, nie odwrotnie) czyta się z `orderNumber` — pozycji
  przystanku w całej trasie — bo `/schedules?stations=A,B` oddaje wpisy
  tylko dla stacji A i B, nie całą trasę. Etapy kolejowe nie mają geometrii
  (słownik stacji PKP nie niesie współrzędnych) — trafiają na listę z pełnymi
  godzinami i nazwami stacji, ale bez linii na mapie (`static/app.js`
  dostał stąd guard na brakujący `leg.path`, wcześniej zakładany jako zawsze
  obecny). Gdy MPK nie zna żadnej z podanych nazw (typowa relacja
  międzymiastowa, np. do „Warszawa Centralna"), ale PKP zna obie stacje i
  znalazło połączenie, `/api/flow` mimo to zwraca sukces (`rail_only: true`,
  `segments: []`) zamiast błędu „nie znam przystanku" — błąd tramwajowo-
  -autobusowy nie ma prawa ukryć wyniku kolejowego. Słownik stacji PKP
  (3266 pozycji) cache'owany na dysku i odświeżany raz na dobę — przy
  limicie 100 zapytań/h w planie Basic nie da się odpytywać go przy każdym
  wyszukiwaniu; sam rozkład cache'owany w pamięci na kilka minut, bo panel
  deweloperski odpala `/api/flow` przy każdym ruchu suwaka. Brak klucza
  `PKP_API_KEY` (patrz `config.py`) wyłącza funkcję po cichu, tak samo jak
  Siechnice bez `SIECHNICE_ENABLED` — awaria albo brak konfiguracji tego
  kroku nie ma prawa wywrócić wyszukiwania tramwajowo-autobusowego.
- **2026-08-29** — dźwięk spadającej metalowej rury po znalezieniu trasy
  (`static/sounds/`). Nagranie, nie synteza. Dwa formaty, bo jeden nie
  wystarcza: Ogg Opus i AAC w m4a dla Safari, wybór przez `canPlayType`;
  oba w powłoce service workera, więc grają offline. Przyciszone do 0.35,
  bo nagranie ma szczyt ponad 0 dBFS. Milczy przy `prefers-reduced-motion`.
  Gra tylko po WYSZUKANIU — suwaki w panelu ⚙ też wołają `loadPlan`
  i mają zostać ciche. Ustawienie jest schowane (`SOUND_TUNING = false`,
  ten sam układ co `LOOK_TUNING`).

- **2026-08-29** — mocna wersja reguły „tylko to, co jeszcze zdąży"
  (`planner._line_deadlines`). Dotąd dymek odsiewał odjazdy warunkiem
  KONIECZNYM — „czy odjazd mieści się w oknie mapy" — a to za mało:
  autobus ruszający minutę przed zamknięciem okna do celu w nim nie dowiezie.
  Teraz węzeł niesie przy każdej linii `depart_by`: ostatni odjazd, którym
  DA SIĘ dojechać, odczytany z profilu (wsiadam w ten kurs tutaj — o której
  jestem w celu). Jedna liczba na linię wystarcza, bo późniejszy kurs tej
  samej linii w tę samą stronę nie dojedzie wcześniej.
  `LEŚNICA → BARTOSZOWICE, 16:44`: stara reguła zostawiała 245 odjazdów,
  nowa 115 — **130 wierszy udawało opcję, nie będąc nią**. Podobnie na
  innych relacjach (118 z 223, 361 z 712). Linia, którą stąd nie dojedzie
  się już wcale, znika z listy w całości.

- **2026-08-29** — narysowany kurs urywa się tam, gdzie zawraca (punkt 4
  kontraktu). Zgłoszone na autobusie 102 pod Kosmonautów: mapa rysowała
  wjazd na pętlę i powrót tą samą ulicą. Porównujemy MIEJSCA, nie słupki —
  pętla nawrotowa ma zwykle osobny słupek w każdą stronę. Ale dwa SĄSIEDNIE
  przystanki jednego miejsca to nie zawrócenie, tylko grubsze grupowanie:
  tramwaj 7 mija tak dwa razy Kamieńskiego, jadąc prosto na Klecinę, a
  ucięcie go odbierało jedynemu dojazdowi jego wyjście i cała relacja
  POŚWIĘTNE → KLECINA spadała do trybu awaryjnego. Zawróceniem jest dopiero
  powrót o co najmniej dwa kroki. Po zmianie: 569 narysowanych kursów na
  24 kombinacjach relacja/godzina, **zero powrotów**, bez straty pokrycia
  (te same liczby kawałków, linii i punktów geometrii) i bez trybu
  awaryjnego.

- **2026-08-29** — tablica odjazdów: rytm powtórzeń („co X min") stoi w tej
  samej linii co „za ile", a nie pod nim — osobny wiersz podnosił wysokość
  akurat tym pozycjom, które się powtarzają, i tablica przestawała być
  równa (wszystkie wiersze mają teraz 22,8 px). Najbliższy odjazd pisze się
  „0 min", nie „teraz": nagłówek mówi „od 16:57", a to nie jest godzina
  zegarowa, tylko najwcześniejsza, o której da się tu być — „teraz" obok
  niej znaczyło co innego, niż czytelnik zakładał. Dymek 300 → 340 px, bo
  prawa kolumna urosła i zjadała kierunek.

- **2026-08-29** — jasność mapy ODCZYTANA, nie oszacowana
  (`planner._target_profile`). Wartość każdego wyjścia („wysiadam tu o tej
  godzinie — o której jestem w celu”) liczył dotąd punkt stały po
  kontynuacjach widocznych na mapie: `join_value` brało gotową wartość
  sąsiedniego segmentu i przesuwało ją o opóźnienie wsiadania
  (`other["suffix"][j] + shift`). Zakładało więc, że sztywny rozkład jest
  sprężysty — że cały dalszy łańcuch przesunie się dokładnie o tyle samo.
  Nie przesuwa się: późniejszy kurs gubi przesiadkę i przyjazd skacze
  o kwadrans, nie o minutę. Wynik był przy tym znakowany jako ODCZYTANY,
  choć powstał z oszacowania — stąd sprzeczność widoczna gołym okiem:
  kawałek podawał godzinę przyjazdu, a jego jedyna kontynuacja nie znała
  żadnej.

  Teraz liczy to profilowy CSA: jeden skan wstecz po tych samych
  połączeniach daje odpowiedź dla KAŻDEGO przystanku w oknie, bez iteracji
  i bez ograniczania się do tego, co narysowane. Przy okazji zniknął próg
  `best_arr` na wartościach odczytanych — niepotrzebny, bo żadna z nich
  optimum nie pobija (pilnuje tego test), a maskowałby prawdziwą
  sprzeczność, gdyby wróciła. Zostaje tylko na surowej aproksymacji.

  `LEŚNICA → BARTOSZOWICE, 16:44`: 116 z 232 wyjść obiecywało przyjazd
  wcześniejszy, niż da się osiągnąć (do 10 min za wcześnie); **0 kawałków
  bez godziny przyjazdu (było 39 na 151)**, a jasność wreszcie różnicuje —
  dwie realne klasy, 17:57 i 18:07, zamiast 74 kawałków na `w = 1.00`.
  Przemiat 20 relacji: zero kawałków bez godziny, zero trybu awaryjnego.
  Koszt: 23 ms na zapytanie. Testy: `tests/test_profil_dojazdu.py`.

  „Tu nie da się wysiąść” znaczy teraz INF, a nie wymyśloną karę: wartość
  takiej POZYCJI bierze się z sufiksu (z dalszych wyjść tego samego kursu,
  czyli z jazdy dalej). Wcześniej kar było więcej niż odczytów, więc to ONE
  wyznaczały skalę jasności całej mapy. Surowa aproksymacja została tylko
  tam, gdzie naprawdę nic już nie prowadzi dalej — ogon za ostatnim
  użytecznym wyjściem — i dalej nie jest pokazywana jako godzina.

- **2026-08-29** — naprawiona przyczyna sprzecznych godzin. Wartość wyjścia
  („o której jest się w celu, jadąc dalej stąd”) dostała próg: nie może być
  wcześniejsza niż `best_arr` ze skanu CSA, bo do tego wyjścia dojechało się
  ze STARTU i nie ma prawa pobić optimum całej relacji. Bez progu przybliżenie
  z `join_value` schodziło poniżej, a `q_of` obcinało wynik do 1.0 — kawałek
  z niemożliwym przyjazdem świecił tak samo jak najlepsza trasa.
  Na relacji LEŚNICA → BARTOSZOWICE (13:32): **105 z 354 kawałków obiecywało
  przyjazd wcześniejszy niż możliwy — teraz 0.**

  Węzeł przestał też proponować kursy WIOZĄCE Z POWROTEM (`_rides_back`):
  jeśli tam, dokąd kurs wiezie, dało się być wcześniej niż tam, gdzie stoimy,
  to nie jest to opcja. Mierzone `earliest` ze skanu w przód, nie geometrią
  i nie luzem z `_backward` — luz się do tego nie nadaje, bo w szerokim oknie
  objazd o przystanek kosztuje minutę terminu (Kamiennogórska: 13:52 → 13:51)
  i próg cofnięcia go nie łapie. Dotyczy wyłącznie tego, co węzeł proponuje;
  rysowanej mapy i stabilności jasności nie rusza.
  Kamiennogórska nie oferuje już tramwaju 3 na Leśnicę osobie, która właśnie
  z Leśnicy przyjechała; węzłów 78 → 56, ofert 323 → 249.


- **2026-08-29** — kropki są też na SAMEJ mapie przepływów, nie tylko na
  wybranej trasie: stoją na końcach kawałków, czyli tam, gdzie mapa widzi
  sensowne wysiadanie — a więc dokładnie tam, gdzie da się przesiąść
  (`flowStopDots`). Nazwy przystanku front tam nie zna, bo kropki bierze
  z geometrii, więc `/api/timetable` przyjmuje też `lat`/`lon`.

  Węzły liczy backend (`_transfer_nodes`, pole `nodes`), a nie front z
  geometrii, i to z dwóch powodów, których front nie umiałby rozstrzygnąć sam.
  Po pierwsze **jedna kropka na MIEJSCE**: plac z trzema peronami dostawał
  wcześniej trzy kropki, każdą z inną zawartością — grupowanie po miejscu jest
  w rozkładzie (`gtfs._build_places`), więc front nie ma go z czego odtworzyć
  i nie powinien zgadywać po odległości na ekranie (168 kropek → 86 na relacji
  Księże Małe → Leśnica). Po drugie **tylko to, w co mapa pozwala tu wsiąść**:
  dymek na Pilczycach wypisywał wszystko, co przez nie przejeżdża, razem
  z tramwajem jadącym dokładnie tam, skąd się przyjechało. Kierunek jest
  częścią tożsamości linii, więc `lines` niosą headsign — samo „122" nie
  wystarczy, bo mapa proponuje jedną stronę, a przez węzeł jadą obie.

  Przy okazji naprawione: w jednym miejscu dymek potrafił pokazać dwie różne
  godziny „tu jesteś" (13:02 albo 13:07, zależnie od drgnięcia kursora o
  piksel). Pod kursorem leży kilka kawałków TEJ SAMEJ linii — to różne kursy,
  a wybierany był ten bliższy w pikselach. Teraz wygrywa kurs dowożący do celu
  najwcześniej, jednym wspólnym wyborem dla dymka i dla godzin w grupce
  numerów (`hitFor`) — wcześniej te dwa miejsca mogły wskazać różne kursy.

- **2026-08-29** — kropki na wsiadaniu i wysiadaniu każdego etapu są do
  najechania: dymek pokazuje TABLICĘ ODJAZDÓW tego przystanku (`stopDot`
  w `app.js`, `/api/timetable` → `planner.stop_timetable` →
  `gtfs.stop_departures`). Mapa rysowała te kropki od dawna, ale mówiły
  tylko „tu się przesiadasz" — a pytanie zadawane w tym miejscu brzmi „a jak
  mi ucieknie, to co dalej?". Godzina idzie z etapu w SEKUNDACH doby
  rozkładowej (`dep_sec`/`arr_sec`), nie jako „HH:MM": przesiadka o 24:40
  należy do rozkładu dnia poprzedniego, a zapytana zegarową „00:40"
  wypisałaby cały dzień od rana — czyli odjazdy dawno odjechane. Indeks
  słupek → odjazdy powstaje leniwie przy pierwszym najechaniu (~30 ms raz na
  dzień, tak jak `conns_by_trip`), więc skan CSA nic za to nie płaci.
  Klik w kropkę nie zamyka trasy, choć klik obok niej nadal zamyka: na
  telefonie dotknięcie jest jedynym sposobem otwarcia dymka i nie może przy
  okazji sprzątać tego, czego dotyczy.

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
- Połączenia kolejowe (`pkp.py`) obejmują przesiadki - między pociągami też,
  bo CSA widzi jedną tablicę połączeń (patrz wpis w changelogu) - ale bez
  opóźnień na żywo (API ma je w `/operations`, ale wyszukiwarka czyta tylko
  rozkład planowy z `/schedules`). Wymaga skonfigurowanego `PKP_API_KEY` —
  bez klucza ta część wyników po prostu nie dochodzi. Stacje mają markery na
  mapie (geokodowanie DWOMA źródłami - wyłącznie mapa PLK i portal
  pasażera, oba oficjalne/PKP-owe, na wyraźną prośbę użytkownika - patrz
  wpis w changelogu i nagłówek `update_pkp.py`), ale sama trasa przejazdu
  (`legs[].path`) nie ma geometrii — słownik stacji PKP nie ma współrzędnych
  torów, tylko nazwy, więc geokodowanie daje punkt stacji, nie kształt
  trasy. Pokrycie ok. 98% (3202/3266 na 2026-08-30); stacja ZA GRANICĄ
  (żadne z dwóch źródeł nie obejmuje zagranicy) albo stacja, której żadne
  z dwóch źródeł nie ma (lub znajdzie coś, co automatyczna walidacja
  odrzuci jako niespójne z sąsiadami na trasie - patrz
  `find_suspect_coords`), po prostu nie ma markera, zamiast dostać
  zgadniętą, złą pozycję.

## Pomysły na dalej

- Więcej odjazdów tej samej trasy na liście („następny kurs o…").
- GTFS-RT: opóźnienia i pozycje pojazdów na żywo (portal je udostępnia).
- Opóźnienia pociągów na żywo z `/api/v1/operations` (patrz `pkp.py`) -
  dziś wyszukiwarka kolejowa czyta tylko rozkład planowy.
