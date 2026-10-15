# Metal-Planner — dokumentacja projektu

Mini-wiki: co to jest, jak jest zbudowane i jak działają algorytmy; historią
zmian jest lista commitów. Instrukcja uruchomienia, konfiguracja i wdrożenie są w
[README.md](../README.md); gwarancje zachowania mapy przepływów —
w [FLOW_MAP_CONTRACT.md](FLOW_MAP_CONTRACT.md), a testy, które ich pilnują — w
[FLOW_MAP_NOTES.md](FLOW_MAP_NOTES.md).

## O projekcie

Webowa wyszukiwarka połączeń komunikacji miejskiej Wrocławia (MPK: autobusy
i tramwaje, do tego kolej PKP, rower miejski i auta Traficara). Zamiast
pokazywać jedną wyliczoną trasę, aplikacja pokazuje **mapę przepływów**
(„symulację mrówek"): cały wachlarz podróży, które są w czymś najlepsze —
o której jest się w celu albo o której trzeba wyjść — i tych najwyżej o kilka
minut dłuższych. Ile tych minut, mapa dobiera sama, żeby pozostała czytelna.
Użytkownik widzi możliwości i sam wybiera.

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
- **`bikes.py`** — trzecie źródło danych: stacje Wrocławskiego Roweru
  Miejskiego oraz rowery stojące poza stojakami, z kanału GBFS operatora
  (`nextbike_pl`, licencja CC0-1.0), razem z modelem czasu tego środka
  transportu (dojście, odblokowanie, przejazd, zwrot) — osobnym dla
  propozycji i dla mapy, patrz `map_places`. W przeciwieństwie do GTFS i PKP nie trafia do żadnej
  bazy — to stan sprzed minuty, nie rozkład — więc żyje wyłącznie w pamięci
  procesu, z krótkim cache, tak jak `vehicles.py`. Opis niżej.
- **`onboard.py`** — start podróży Z POKŁADU pojazdu („jestem w 145,
  następny przystanek Sucha"). Rozpoznaje z trójki linia + kierunek +
  następny przystanek jeden konkretny KURS rozkładu i sprowadza cały tryb do
  zwykłego wyszukiwania: podróż zaczyna się na tym przystanku, w sekundzie,
  o której pojazd z niego rusza. Dokłada też do gotowych propozycji to, czego
  ten pasażer naprawdę potrzebuje — gdzie wysiąść i za ile to przystanków.
  Opis niżej.
- **`vehicles.py`** — pozycje autobusów i tramwajów na żywo z kanału MPK
  (`mpk.wroc.pl`), pod warstwę ◉. Tak jak rowery: tylko w pamięci, z krótkim
  cache. Rozpoznawanie, którym kursem rozkładu jedzie dany pojazd, czeka
  w pliku zakomentowane.
- **`routes.py`** — endpointy: `/` (strona), `/sw.js`, `/healthz`,
  `/api/stops`, `/api/plan`, `/api/flow`, `/api/timetable`, `/api/line`,
  `/api/onboard`, `/api/stop_board`, `/api/trip`, `/api/vehicles`,
  `/api/cars`, `/api/traficar-zone`, `/api/bikes` (szczegóły w `routes.py`).

### 3. Frontend — `templates/index.html` + `static/app.js` + `static/style.css`

Jedna strona: pełnoekranowa mapa Leaflet (kafelki OpenStreetMap — wymaga
internetu), wszystkie słupki jako markery na canvasie, panel boczny z lewej
(chowany przyciskiem ☰). Klik 1 = start (zielony), klik 2 = cel (czerwony)
i wyszukiwanie odpala się samo; można też wpisać nazwy ręcznie. Klikanie
uzupełnia tylko brakujący koniec relacji — **gotowego wyszukiwania nie
kasuje żaden klik w mapę, tylko przycisk ✕**, żeby przypadkowe kliknięcie
nie zabrało wyników sprzed chwili.

Nad polami stoi przełącznik **Stoję tutaj / Jestem w pojeździe**. Drugi tryb
zamienia „skąd" na trzy pola — linia, kierunek i najbliższy przystanek — bo
pasażer w jadącym autobusie nie stoi w żadnym miejscu, które dałoby się wpisać
w „skąd" (patrz sekcja „Start z pokładu pojazdu" i `onboard.py`). Każde z nich
ma PODPIS nad sobą, nie tylko placeholder: ten znika razem z wpisaniem
wartości, a wtedy „113 / KRZYKI / Wojszyce" to trzy nazwy, o których nie
wiadomo, która jest którą. Podpis dostaje też pole celu — w zwykłym trybie
mówi to szyna z kropkami, a z pokładu jej nie ma. Znika wtedy
szyna z kropką startu i ⇅ (nie da się zamienić miejscami pojazdu z celem),
a klik w mapę ustawia sam cel. Komplet zwija się do JEDNEJ linijki
(„113 · Wojszyce · w stronę GALERIA DOMINIKAŃSKA · zmień") — trzy pola są
potrzebne, dopóki się wybiera, a potem niosą jedno zdanie i zabierają trzy
rzędy panelu, przez które nie widać propozycji; klik rozwija je z zachowanym
wyborem. W zwiniętej linijce rolę podpisów grają plakietka linii, znaczek
słupka (ten sam, co przy przystankach na liście podpowiedzi) i słowa
„w stronę". Zwijamy wyłącznie wybór KOMPLETNY, więc zmiana linii pokazuje pola
z powrotem sama. Wybór pojazdu nie przeżywa odświeżenia strony — sam
przełącznik owszem.

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
pyta się o lokalizację. Każde kliknięcie prosi o **świeżą** pozycję, nie
o zapamiętaną przez przeglądarkę (#236). Przeglądarki dają pozycję **tylko po HTTPS** (wyjątek:
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

  **Przystanki po drodze** (zgłoszenie #169): liczba przystanków przy
  przejeździe („7 przystanków · 12 min”) rozwija w osi mijane przystanki
  z godzinami — domyślnie zwinięte, a opcja „Zawsze pokazuj przystanki po
  drodze” w Wyglądzie aplikacji rozwija je od razu. Rozwinięcie przełącza
  schowane wiersze w miejscu (`toggleVia`), bez przerysowania listy, które
  przewinęłoby ją do początku karty. Na mapie wybrana trasa ma na każdym
  z nich obwódkę w kolorze linii (`viaDot`) z nazwą i godziną w dymku —
  zawsze, niezależnie od opcji; podgląd spod kursora ich nie ma.

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

Na końcu dochodzi **drugie złożenie**: bez kropek i z rozwiniętymi skrótami
(`pl.` → `plac`), żeby „Plac Grunwaldzki" podpowiadało `PL. GRUNWALDZKI`.
Osobny przebieg, nie zamiennik pierwszego — rozwinięcie zmienia długość
napisu, więc trafienie nie ma jak wskazać fragmentu do podświetlenia. Tabelę
skrótów front dostaje z serwera (`#stop-abbrev`, `naming.ABBREVIATIONS`),
żeby ta sama reguła nie istniała w dwóch kopiach: tej samej używa
wyszukiwarka po stronie serwera (`gtfs._alias_key`).

**Telefon (≤ 760 px) dostaje zakładki** zamiast panelu nachodzącego na mapę:
dolny pasek „Mapa / Trasy (n) / Rozkład" przełącza to, co pod kartą
wyszukiwania — albo lista propozycji na cały ekran, albo sama mapa (wtedy z panelu zostaje
tylko karta „skąd/dokąd", bez nagłówka i podpowiedzi, żeby nie zjadały
ekranu). Lista leży na własnym, jednolitym tle — mapa **zostaje pod spodem
w DOM**, bo Leaflet musi znać rozmiar swojego kontenera, żeby poprawnie
kadrować także wtedy, gdy patrzymy na listę (przy `display: none` dostałby
zerowy kontener i policzył bezsensowny kadr); po prostu jej nie widać. Po znalezieniu połączeń widok przeskakuje na listę, a ✕ wraca na
mapę, bo tam wybiera się nową relację. Zakładki zastępują ☰ (dwa mechanizmy
chowania panelu naraz tylko by myliły).

**Mapa pod palcem (#274).** Najechania palcem nie ma, więc to, co na komputerze robi kursor nad linią przepływu albo nad numerem w grupce, robi stuknięcie (`tapFlow` i klik w `bindCluster`, app.js) — z szerszym marginesem trafienia (`FLOW_TAP_SLACK_PX`), bo palec trafia kilkanaście pikseli obok cienkiej linii. Czy wskazuje palec, rozstrzyga ostatnie `pointerdown`, a nie szerokość ekranu; udawany ruch myszy, który przeglądarka dosyła po stuknięciu, jest wtedy pomijany. `#map` ma wyłączone zaznaczanie tekstu i menu przytrzymania (`-webkit-touch-callout`), żeby przytrzymanie nie wyciągało „Kopiuj / Przetłumacz".

Trzecia zakładka, „Rozkład", to ta sama lista w **drugim trybie panelu**
(rozkłady jazdy — patrz niżej) — na telefonie jedyne wejście w ten tryb, bo
pływające ◷ jest tam schowane razem z ☰. Zakładek jest więc trzy, a stanu
dwa wymiary: widok (`view-map`/`view-list`) i tryb (`mode-timetable`).
Podświetlenie liczy się z obu klas na `<body>` (`syncTabs` w `app.js`),
a nie z ostatniego kliknięcia — w rozkłady wchodzi się także spoza paska
(przycisk „trasa" przy propozycji) i zakładki mają za tym nadążyć.
„Mapa" trybu nie rusza: w rozkładach mapa to sposób wybrania przystanku
(klik w słupek), a nie wyjście z nich; wychodzi się dopiero „Trasami",
które przywracają wachlarz propozycji bez ponownego szukania. A że „Trasy"
przy okazji zabierają z mapy na listę, karta rozkładu dostaje w tym jednym
widoku (telefon + mapa) przycisk **„✕ Zamknij rozkład"** — to samo wyjście
z trybu, tyle że zostawia na ekranie mapę, na którą się właśnie patrzyło.
Stoi w miejscu podpowiedzi, która w widoku mapy i tak jest schowana. Klasy `view-map`/`view-list` na
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

W nagłówku stoi **pasek warstw** — same ikonki, z pełnym zdaniem
w podpowiedzi: ◉ pojazdy na żywo, 🚗 wolne auta Traficara, 🚲 rowery zwykłe
i ⚡ elektryczne. Strefę oddawania aut (zgłoszenie #157) pokazuje najechanie
na auto; przycisk 🅿 trzymający ją na stałe i jeden wspólny przycisk rowerów
zamiast dwóch włącza się w sekcji „Layout" pod ⚙. Bez wyszukiwania warstwy pokazują wszystko,
co jest w mieście (`/api/vehicles`, `/api/cars`, `/api/bikes`); przy
narysowanej mapie zawężają się do niej — pojazdy do linii z tej mapy, auta
i rowery do tych, do których mapa dowozi (punkty 15 i 16 kontraktu). Rower to
wybór PASAŻERA („mam konto w WRM" albo nie), a nie strojenie algorytmu,
dlatego stoi tu, a nie pod ⚙: włączony 🚲 albo ⚡ dokłada też do listy
propozycje z rowerem (`bikes=1` w `/api/flow`). Stan przycisków przeżywa
odświeżenie strony (`localStorage`), a ich zmiana przelicza gotowy wynik bez
ruszania kadru — relacja się nie zmieniła.

Gdy rower jest włączony, a propozycji z rowerem nie ma, pod listą staje
kartka mówiąca dlaczego (`bikeNoteHtml`) — brak wyniku i zepsuta funkcja
wyglądają inaczej tylko wtedy, gdy ktoś to powie.

Ustawienia Developerskie są schowane za przyciskiem ⚙. Czysty JS bez
frameworka, cała logika w `static/app.js`. Sekcje po kolei: „Co pokazuje
mapa" (gęstość, liczba aut i przejazdów rowerem), „Czas na mapie",
„Przystanki, rowery i Traficary" (m.in. godziny przejazdu rowerem i dojazdu
autem w dymku, przejazdy rowerem zawsze widoczne, dostawczaki), „Założenia
czasowe" (tempo marszu, prędkość i narzut roweru oraz auta), „Debug" (podglądy „dlaczego kawałek / rower / auto jest na mapie"),
schowane „Wygląd mapy", „Dźwięk" i „Layout", „Wersja aplikacji", „Funkcje"
z przełącznikami „Stoję tutaj / Jestem w pojeździe" i „Propozycje tras" (zgaszone to `routes=0` w `/api/flow` —
serwer listy wtedy w ogóle nie składa), a na samym
dole, zwinięte, „Propozycje tras" z **progiem opłacalności
przesiadki** (`transfer_gain_sec`, domyślnie 10 min — patrz opis skanu
wyżej). Wartości lądują w `localStorage`, a te, od których zależy odpowiedź
serwera, w query stringu `/api/flow`.
`value`/`checked` w `index.html` są wartościami domyślnymi: opcja różna od
nich dostaje pasek z boku, a nagłówek sekcji — liczbę takich opcji. Jeden
przycisk „Przywróć domyślne" kasuje wszystkie zapamiętane ustawienia
i przeładowuje stronę.

#### Rozkłady jazdy — drugi tryb panelu (`static/timetable.js`)

Obok wyszukiwarki połączeń panel ma drugi tryb, przełączany przyciskiem
**◷ Rozkłady** tuż obok ← (chowania panelu); na telefonie jest to trzecia
zakładka na dole, „Rozkład", obok „Mapy" i „Tras". W widoku mapy karta
rozkładu ma przycisk **„✕ Zamknij rozkład"** — wychodzi z trybu bez
schodzenia z mapy. Oba tryby zajmują to samo miejsce — wejście w rozkłady
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

W drugą stronę wiedzie przycisk **„trasa"**, stojący tam, gdzie widać sam
numer linii: przy przejeździe w rozwiniętej propozycji, w tablicy odjazdów
pod kropką przystanku na mapie i pod rozwiniętym kursem tablicy. Otwiera
rozkład tej linii — i od razu na kierunku, którym jedzie ten konkretny kurs,
a nie na tym, który ma więcej kursów w ciągu dnia. Powrót przyciskiem ←
przywraca wachlarz połączeń i wybraną propozycję bez ponownego szukania,
więc zajrzenie w trasę nic nie kosztuje. Linie, których nie ma w rozkładzie
(pociągi PKP), przycisku nie dostają.

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
  na jego przystanku odpowiednio wcześnie (bufor przesiadki 1 min, do
  2026-09-24 były to 2; start
  i dojście piesze bez bufora — trzyminutowa podłoga przejścia jest od bufora
  większa, więc go w sobie mieści);
- między słupkami leżącymi blisko siebie przechodzi się PIESZO, bez względu na
  nazwę i na to, czyja to sieć (`gtfs._nearby_bridges`, promień
  `WALK_M` = 600 m — ten sam na starcie, w przesiadce, u celu i przy punkcie
  klikniętym na mapie). Czas przejścia liczy się z samej odległości w linii
  prostej (0,7 m/s, w górę do pełnych minut, nie mniej niż 3) i krawędź niesie
  go ze sobą — stałej czasu przejścia nie ma. Tempo pytający może zmienić pod
  zębatką na jedno z trzech (`gtfs.WALK_PACES`: 0,55 / 0,7 / 0,9 m/s); każde
  inne niż domyślne to osobna kopia dnia z przeliczonymi krawędziami
  (`gtfs.with_pace`), liczona raz i trzymana razem z dniem. Tą samą drogą łączą się kolej
  i MPK: dworzec i stojące pod nim przystanki nazywają się inaczej, a i tak
  dzieli je kilka minut marszu. Przejście relaksuje się po wysiadaniu
  z pojazdu albo z samego startu relacji, zawsze o JEDEN krok — nie da się iść
  pieszo dwa razy pod rząd. Do kursu, który i tak zatrzymuje się tam, gdzie
  stoimy, nie wolno dojść pieszo wcześniej (`_cheaper_boarding`): marsz ma
  otwierać nowy kurs, a nie dosiadać nas do własnego;
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
na ten sam próg mapy (gęstość, „Pokaż więcej") — dawniej to nie było prawdą
(lista miała własne, niezależne od mapy okno).

1. **Graf przesiadek** (`_extract_transfer_graph`): węzły to już narysowane,
   przycięte segmenty mapy; krawędzie to miejsca, gdzie z wyjścia jednego
   segmentu da się zdążalnie wskoczyć w drugi — dokładnie ten sam warunek
   porównywalnej jasności, którego `_select_and_anchor` już użył, żeby
   uznać koniec segmentu za zakotwiczony. Punkty startowe to segmenty
   zaczynające się na starcie relacji.
2. **Przeszukiwanie** (`_enumerate_journeys`): w przód od startu, najpierw
   najjaśniejsze gałęzie, z sufitami kosztu (limit etapów na propozycję,
   limit zebranych wariantów, limit odwiedzonych węzłów) — duże miasto przy
   gęstej mapie mogłoby inaczej dać kombinatoryczną eksplozję.
3. Odpada powtórzony układ (te same linie wsiadane na tych samych
   przystankach); ranking po przyjeździe, potem liczbie przesiadek, potem
   czasie oczekiwania — tak jak dawniej.
4. **Zabezpieczenie**: gdy mapa nie wybierze niczego, `plan_flow`
   dorysowuje najszybszą trasę (już policzoną przez `_scan`) wprost - i do
   mapy, i do listy, tym samym zrekonstruowanym przejazdem, więc obie
   odpowiedzi nadal się zgadzają.

Etapy dostają geometrię wprost z segmentu (ten sam `gtfs.shape_slice`, jedno
połączenie do bazy na całe zapytanie - i mapę, i listę), więc wybrana
propozycja rysuje się po realnych ulicach i torach.

#### Ostatni etap Traficarem (`traficar.py`)

Do tej samej listy dokładają się **propozycje kończące się wynajętym autem**:
komunikacja dowozi w okolicę celu, a ostatni kawałek — ten, na który nie ma
już sensownej linii — jedzie się Traficarem. Propozycja ma cztery części po
kolei: przejazd(y) komunikacją → dojście z przystanku do auta → pięć minut na
odbiór i start → jazda do celu.

Skąd auta: `fioletowe.live` (open source, GPLv3, `divadsn/traficar-map`)
republikuje wewnętrzne API Traficara jako REST/JSON bez klucza; Wrocław to
`zoneId=3`. To **strona trzecia**, nie sam operator, więc feed może zniknąć
bez ostrzeżenia — stąd wyłącznik `TRAFICAR=0` i zasada, że każdy błąd tego
źródła jest brakiem propozycji z autem, nigdy błędem wyszukiwania.

Skąd kandydaci: NIE z grafu segmentów mapy (ten prowadzi do celu, a tu trzeba
czegoś innego — dojazdu w okolicę auta), tylko wprost ze śladu skanu CSA,
który `plan_flow` i tak już ma policzony na potrzeby deadline'u
(`_reached_times` czyta z niego godziny bez drugiego skanu). Dla każdej pary
„przystanek wysiadania + auto" liczy się godzina dotarcia do celu; zostają
najwyżej dwie, po jednej na miejsce i na auto, i tylko te mieszczące się
w tym samym oknie czasowym co reszta listy. Odpada auto dalej niż 600 m od
przystanku oraz takie, którego zasięg nie pokrywa przejazdu z zapasem.
Długość samej jazdy nie ma progów (do 2026-09-15: 1,5–25 km) — o tym, czy
auto się opłaca, rozstrzyga godzina przyjazdu.

**Czas jazdy jest szacowany i jest to powiedziane wprost.** Auto nie ma
rozkładu, a routingu samochodowego w projekcie nie ma — czas i dystans
wychodzą z odległości w linii prostej: krętość ×1,30, 34 km/h. Obie stałe są
**zmierzone**, nie wzięte z głowy: 30 losowych par przystanków przepuszczonych
jednorazowo (poza aplikacją, w runtime nic takiego nie jest wołane) przez
prawdziwy routing samochodowy OSRM. Mediana krętości wyszła 1,30, mediana
prędkości 36 km/h; bierzemy 34, bo profil OSRM liczy jazdę swobodną, bez
korków, a plan ma bywać pesymistyczny (punkt 12 kontraktu).

Ile to warte: błąd czasu 19% (mediana), 34% (90. centyl), zakres −23%…+54%,
zawyżenie w 21 z 30 przypadków — czyli najczęściej w bezpieczną stronę. Błąd
dystansu 10% / 22%. Stałe sprzed pomiaru (1,35 i 27 km/h) myliły się co do
czasu o 47% mediany i zawyżały w 30 z 30 przypadków, w skrajnym dwukrotnie.

Ten rozrzut dyktuje sposób pokazywania: dystans w **pełnych kilometrach**
(przy 22% rozrzutu „7,7 km" udawałoby odczyt z licznika), wszystko z „ok.",
czas podróży na karcie też z „ok." i przygaszony, a rozwinięta karta mówi
wprost „czas i dystans szacowane z odległości — auto nie ma rozkładu".
Szacunki idą w górę do pełnej minuty.

**Czego ten szacunek nie dotyka: mapy przepływów.** Mapa rysuje wyłącznie
godziny odczytane z rozkładu (punkt 10 kontraktu), a ta jedna odczytana nie
jest. Propozycja z autem rysuje się więc dopiero po jej wybraniu, tak jak
każda inna trasa z listy: kreskowaną linią bez otoczki, bo odcinek auto → cel
jest prostą, a nie przebiegiem ulicami.

#### Auto jako miejsce na mapie (`traficar.map_cars`)

Auto jest na mapie od 2026-09-12 (punkt 15 kontraktu). Mapa traktuje je
tak, jak traktuje przystanek: to **miejsce, do którego da się dojść**. Wolne
auto oddalone o jedno dojście (`gtfs.WALK_M`, czas z `gtfs.walk_time_sec` —
ta sama reguła co wszędzie, punkt 14) od czegoś, co mapa NARYSOWAŁA
(`planner._drawn_reach`) albo od samego startu, jest kandydatem i mówi,
o której da się przy nim być.

Które auta stają na mapie, rozstrzygają trzy liczby — trzy powody, dla
których bierze się auto (zgłoszenie #150): o której się przy nim jest, ile
daje za nie program „Ogarniam" i o której **mniej więcej** dowiezie do celu.
Ta ostatnia to szacunek prostszy niż w propozycjach: jedna prędkość w linii
prostej do celu (domyślnie 22 km/h — pomiar OSRM bez korków, pomniejszony
o jedną piątą) plus stały narzut na ruszenie i parkowanie (domyślnie 8 min);
oba pasażer ustawia pod ⚙. Zostaje zbiór Pareto, a suwak „ile aut" dokłada
kolejne poziomy w całości (`traficar.map_skyband`). Dostawczaki to osobny
wybór, domyślnie wyłączony. W dymku domyślnie stoi odległość do celu w linii
prostej; szacowaną godzinę dopisuje przełącznik „Godzina dojazdu autem".

Znaczniki pojawiają się wyłącznie przy pytaniu o dziś (auta stoją tam, gdzie
stoją teraz — ta sama zasada, co przy stojakach rowerowych), a milczący feed
albo `TRAFICAR=0` po prostu je zabiera.

#### Rower jako miejsce na mapie (`bikes.map_places`, punkt 16 kontraktu)

Od 2026-09-12. Wzorzec ten sam, co przy aucie — kropka, do której mapa
dowozi jednym dojściem — z jedną różnicą: **z roweru się JEDZIE**, więc mapa
mówi też, dokąd i ile to trwa. Rower jest przejściem o innym tempie i wolno
go liczyć z tego samego powodu, z którego wolno liczyć marsz (punkt 10: zakaz
szacowania dotyczy pojazdów, które mają rozkład).

**Reguła sensu: przejazd zostaje, jeżeli po zsiadaniu zdąży się jeszcze
wsiąść w coś, co mapa RYSUJE** (albo dojechać pod sam cel). Nie musi być
szybszy niż tramwaj — ktoś może chcieć jechać rowerem właśnie dlatego, że woli
rower — ale musi prowadzić do czegoś widocznego. Technicznie: `planner` podaje
`_drawn_boardings` (najpóźniejsza godzina, o której mapa pozwala na danym
słupku wsiąść w kawałek jadący DALEJ) plus krańce relacji liczone do
deadline'u, a przejazd przeżywa, gdy dla najbliższego słupka przy stacji
docelowej `przyjazd + dojście ≤ board[słupek]`.

Pierwsza wersja brała tu `latest` ze skanu wstecz i to był błąd tej samej
klasy, co kiedyś przy autach: skan zna pół miasta, więc rower proponował
przejazd „pod przystanek, z którego mapa nie rysuje ani jednego odjazdu".
Efekt był drastyczny — na Kozanowie o 21:07 kropek było kilkadziesiąt, po
poprawce jest dwanaście.

Model czasu jest tu JEDNĄ prędkością liczoną po linii prostej
(`MAP_RIDE_MPS`, 10 km/h — to 14 km/h realnej jazdy podzielone przez 1,35
krętości miasta, zaokrąglone w dół) plus stały narzut na wypożyczenie
i oddanie (`MAP_OVERHEAD_SEC`, 2 min). Rozbicie na prędkość × krętość ma sens
tylko tam, gdzie zna się przebieg trasy — mapa zna wyłącznie odległość
w linii prostej, więc dwie liczby udawałyby wiedzę, której nie ma. Propozycje
tras liczą tym samym modelem, a dojście do stacji zwykłym marszem
(`gtfs.WALK_M`, `gtfs.walk_time_sec`) — od zgłoszenia #151 nie ma już osobnego
modelu propozycji.

Przejazd zaczyna się na stacji **albo przy rowerze stojącym luzem**
(`free_bike_status` — we Wrocławiu jest ich ponad sto naraz), a kończy zawsze
na stacji: za zostawienie roweru poza stojakiem operator liczy osobno i dużo.
Przy pytaniu o inną dobę kropki zostają, znika tylko stan stojaka i rowery
luzem.

Wielkości z pomiaru (2026-09-12, 17:30): Kozanów → pl. Grunwaldzki 95 kropek
i 807 przejazdów, Osobowice → Biskupin 68 i 567, Katedra → Leśnica 53 i 198,
Wojszyce → Dworzec Główny zero. To ostatnie **nie** dlatego, że na południu nie
ma stacji WRM — są, 31 poniżej Wojszyc, aż po Siechnice i Bielany. Powód jest
inny: przy samych Wojszycach żadna stacja nie leży w promieniu dojścia od
narysowanych przystanków, a te 16 stacji, do których ta mapa dociera, stoi
dopiero przy samym celu — tam okno jest już wyczerpane i żaden przejazd się
w nim nie mieści.

Stąd decyzja, że mapa rysuje same kropki, a kreski przejazdów pokazuje pod
kursorem — kilkaset kresek naraz zasłania mapę, a dwie kropki i tak mówią to
samo, co kreska między nimi. Przy drugim końcu przejazdu widać domyślnie SAMĄ
odległość: godzina jest policzona, nie odczytana, a odległość mówi to samo,
nie udając rozkładu. Oba zachowania da się zmienić pod ⚙, w sekcji
„Przystanki, rowery i Traficary": „Godziny przejazdu rowerem" i „Przejazdy
rowerem zawsze widoczne" (obie domyślnie wyłączone).

Ile kropek stoi na mapie, rozstrzyga reguła z punktu 16 kontraktu: najpierw
stacje (o której jest się przy rowerze, o której w celu, ile przesiadek —
zbiór Pareto i kolejne poziomy, osobny suwak „Ile stacji z rowerem"), potem
przejazdy na każdej stacji osobno. Rodzaj roweru (zwykły, elektryczny) to
odsiew miejsc, nie zmiana wyceny.

### Rower miejski w trasie (`bikes.py` + sekcja ROWER MIEJSKI w `planner.py`)

Włączany przyciskami 🚲 i ⚡ w pasku warstw (`bikes=1` w `/api/flow`); bez
nich odpowiedź jest co do pola taka sama jak przed dodaniem tej warstwy.
Rower jest wyborem pasażera, nie ustawieniem serwera — bez konta w WRM
propozycja z rowerem jest bezużyteczna.

Dane: kanał **GBFS** operatora (nextbike, `system_id` `nextbike_pl`) — ten
sam, który widnieje w oficjalnym rejestrze systemów MobilityData; licencja
podana w kanale to **CC0-1.0**, więc inaczej niż przy Siechnicach nie ma
powodu trzymać źródła wyłączonego. Bierzemy `station_information` (gdzie
stoją stacje, cache na godzinę), `station_status` (ile rowerów i wolnych
miejsc, cache 60 s — tyle deklaruje sam kanał), `free_bike_status` (rowery
luzem) i `vehicle_types` (który rower jest elektryczny). Nic z tego nie
trafia do bazy rozkładów: to stan sprzed minuty, nie rozkład. Wyłącznik:
`WRM_ENABLED=off`; `WRM_GBFS_URL` podmienia adres kanału.

Rower jeździ **między stacjami**, bo tak działa ten system: wypożyczenie
zaczyna się i kończy w stojaku. Wolno mu przy tym stać w **dowolnym miejscu
trasy** — i to bez czterech osobnych algorytmów, bo `plan_flow` ma już
policzone obie połówki odpowiedzi:

- `earliest[słupek]` — o której najwcześniej da się tu być, startując
  z relacji (skan w przód, `_forward`);
- `profile(słupek, t)` — stojąc tu o godzinie `t`, o której jest się w celu
  (profilowy skan wstecz, `_target_profile`).

Wstawienie roweru to złożenie tych dwóch funkcji przez parę stacji (A, B):

```
earliest[X] → dojście X→A → ODBLOKOWANIE → przejazd A→B → zwrot
            → dojście B→Y → profile(Y, …)
```

Skrajne przypadki wychodzą z tego samego wzoru, nie z osobnego kodu: X bywa
samym punktem startu (rower na POCZĄTKU), Y samym celem (na KOŃCU), obie
naraz (sam rower), obie w środku sieci (rower w ŚRODKU — skrót między dwiema
liniami). Pętla po parach stacji (`_bike_candidates`) zostawia po jednym,
najlepszym przedstawicielu każdego z tych czterech **kształtów** — bez tego
lista dostałaby kilka niemal identycznych propozycji z sąsiednich stacji tej
samej okolicy. Odsiewanie par jest geometryczne i robi się przed dotknięciem
rozkładu (dolne ograniczenia: minimalny sensowny przejazd, dojście od stacji
B, odległość B od celu w linii prostej ÷ prędkość najszybszego pojazdu
w sieci), więc 273 × 273 pary kosztują kilka milisekund.

Wybrane pary są dopiero **odtwarzane** etap po etapie dwoma zwykłymi skanami
CSA (`_scan` + `_reconstruct`) — po jednym na każdą połowę trasy. Godziny na
karcie liczone są przy tym od nowa, do przodu, z faktycznie odtworzonych
etapów, a nie przepisane z pętli po parach: tamte są dobre do WYBORU pary,
ale kartę ogląda się minuta po minucie, więc musi się zgadzać sama ze sobą.

Czas etapu rowerowego to dwie rzeczy, widoczne na karcie osobno: **jazda**
i **narzut** na wypożyczenie ze zwrotem — ten sam model co na mapie
(`bikes.ride_time_sec`: jedna prędkość w linii prostej, domyślnie 10 km/h,
plus stały narzut, domyślnie 2 min; oba pasażer ustawia pod ⚙, zgłoszenie
#151). Narzut jest osobno, bo to jedyny moment podróży, w którym stoi się
przed maszyną, a nie czeka na rozkład. Dojście do stacji i od stacji to
zwykłe przejścia piesze (`gtfs.WALK_M`, `gtfs.walk_time_sec`), jak każde
inne. Wszystkie te czasy zaokrągla się **w górę do pełnych minut**
(`bikes._whole_minutes`): karta pokazuje godziny z dokładnością do minuty,
więc przy sekundach „13:03 przyjazd, 1 min dojścia, 13:03 odjazd", a suma
części przejazdu nie równa się jego długości. W górę, bo każde z tych
zaokrągleń jest marginesem.

**Żadnego osobnego progu.** Rower podlega dokładnie tej samej regule co
wszystko inne na liście: mieści się w oknie czasowym mapy (`_deadline`) —
wchodzi, i staje tam, gdzie mu wypada wg wspólnego klucza (`_journey_key`).
Gorsza opcja ląduje na dole, a nie znika.

Kuszące jest dołożyć drugi próg („rower wchodzi tylko wtedy, gdy **wygrywa**
z najlepszym dojazdem bez niego"), bo rower kosztuje osobno: konto, dojście
do stojaka, wypożyczenie, pedałowanie. Odpada z dwóch powodów. Zgoda na ten
koszt **już padła** — włączenie 🚲 albo ⚡ jest właśnie nią. A przede wszystkim taki
próg sprawdzano by na danych zmieniających się **co minutę** (stan stojaków),
podczas gdy kandydaci potrafią stać dokładnie na jego styku: dwa wyszukania
tej samej relacji w odstępie minuty dawałyby raz propozycję z rowerem, raz
żadną. Z zewnątrz jest to nieodróżnialne od zepsutej funkcji (zmierzone na
relacji Wojszyce → pl. Grunwaldzki: `bikes = 0` i `bikes = 1` w odstępie
dwóch sekund).

**Gdy roweru nie ma, front mówi dlaczego** (`bikeNoteHtml` w `app.js`).
Trzy zera wyglądają identycznie, a znaczą co innego: „policzone, nic się nie
mieści w oknie", „kanał operatora milczy" i „pytasz o inny dzień". Pole
`bikes` w odpowiedzi rozróżnia je (`journeys`/`stations`/`live`), więc
wystarczy je wypisać — bez tego brak propozycji jest nieodróżnialny od
zepsutej funkcji.

**Na mapie przepływów** rower jest od 2026-09-12 jako miejsce, nie jako
kurs — patrz „Rower jako miejsce na mapie" wyżej. Wybrana propozycja
z rowerem rysuje się w całości, tak jak każda inna; sam przejazd kreską
**przerywaną**, bo znamy obie stacje, ale nie przebieg ulicami, a ciągła
linia obiecywałaby geometrię, której nikt nie policzył.

Awaria kanału operatora ma jeden skutek: propozycji z rowerem po prostu nie
ma (`bikes.stations_quiet` oddaje pustą listę zamiast rzucać), a reszta
wyszukiwarki działa bez zmian. Odpowiedź niesie wtedy `bikes.stations == 0`,
żeby front miał po czym odróżnić „policzone, rower nic tu nie daje" od „nie
było czego liczyć".

### Start z pokładu pojazdu (`onboard.py`)

Pytanie „skąd" zakłada, że pasażer gdzieś STOI. Siedzący w autobusie nie stoi
nigdzie — jedzie — i pyta o co innego: nie „czym dojechać", tylko **„gdzie
wysiąść"**. Przycisk **Jestem w pojeździe** nad polami zamienia więc „skąd" na
trzy pola: linia, kierunek z czoła pojazdu i przystanek, który ma się przed
sobą (kierunek i przystanek z list — treść jest skończona i znana, a wpisywanie
nazwy w jadącym autobusie to proszenie się o literówkę).

Cała sztuczka jest w jednym zdaniu: **z pokładu podróż zaczyna się na NASTĘPNYM
przystanku, w chwili, gdy pojazd z niego rusza**. Wszystko, co pasażer może
zrobić, zaczyna się właśnie tam i wtedy — zostać w pojeździe (dla skanu:
wsiadanie w ten sam kurs na tym przystanku, z zerowym czekaniem),
wysiąść i przesiąść się (wsiadanie w inny kurs stamtąd), wysiąść i pójść
pieszo, po rower albo do auta (zwykłe przejście stamtąd). `plan_flow` dostaje
słupek i sekundę jak przy każdym innym wyszukiwaniu, więc WRM, Traficar, kolej
i mapa przepływów działają tu tak samo jak wszędzie indziej.

Jedna różnica wobec stania na przystanku (zgłoszenie #67): na ten przystanek
się PRZYJEŻDŻA. Dalsza jazda naszym kursem nie wymaga zapasu, ale każdy inny
pojazd to już przesiadka — dostaje minutę bufora (`TRANSFER_SEC`, reguła
w `planner._board_buffer`, ta sama w skanie, w mapie i przy wyborze miejsca
wsiadania), a w liście propozycji liczy się jako przesiadka, więc musi się
opłacić (`TRANSFER_GAIN_SEC`), żeby wyprzedzić jazdę dalej.

Mapa przepływów jest tu tą samą mapą z wartości podróży co wszędzie
(zgłoszenie #187). Dwie rzeczy liczą się inaczej: pojazd, w którym się
siedzi, jest jednym z pojazdów podróży (wysiadka i przesiadka to dwa), a
godzina wyjścia nie jest wartością — pasażer już jedzie, więc każda podróż
wychodzi o tej samej sekundzie i rozstrzyga sam przyjazd.

Zostają więc dwie rzeczy do zrobienia:

1. **Rozpoznanie kursu** (`find_ride`) — z trójki linia + kierunek + przystanek
   ten kurs, który z tego słupka rusza najbliżej godziny pytania — w przód
   ALBO WSTECZ (zgłoszenie #231): kurs, który według rozkładu minął przystanek
   5 min temu, gdy następny jest za pół godziny, to najpewniej spóźniony
   pojazd, w którym pasażer siedzi. Spóźnienia nie znamy (danych na żywo
   ten wybór nie używa), więc mapa liczy się od takiego kursu według jego
   rozkładu, jakby jechał o czasie. Idzie
   indeksem odjazdów dnia (`gtfs.departures_between`), więc kosztuje
   przeszukanie binarne; kurs KOŃCZĄCY bieg na wskazanym przystanku odjazdu nie
   ma wcale, więc „jadę na pętlę, co dalej" znajduje się osobną, wolniejszą
   drogą — po przyjeździe, w oknie połączeń. Nierozpoznany kurs to komunikat
   („linia X nie przejeżdża już dziś przez Y"), a nie pusta lista: pomyłka
   w którymś z trzech pól ma wyglądać jak pomyłka. Gdy rozkładowa godzina
   kursu przy wskazanym przystanku odjeżdża od godziny pytania o więcej niż
   3 min (`OFF_SCHEDULE_SLACK_SEC`, ten sam zapas w obie strony — do tylu
   kurs uchodzi za punktualny), nad polem celu staje ostrzeżenie: dla kursu
   przyszłego (`not_yet`) „134 według rozkładu będzie na przystanku …
   o 16:28. Godziny na mapie liczą się od 16:28, a nie od teraz.", dla
   przeszłego (`late`) „134 według rozkładu była na przystanku … o 15:55.
   Godziny na mapie liczą się od 15:55, a nie od teraz."
   Oba ostrzeżenia to nie pytania: mały „×" w rogu tylko je chowa, znikają też samo przy zmianie linii, kierunku albo przystanku,
   a wybór pojazdu poprawia się przez „zmień".
2. **Opis wysiadki** (`mark_journeys`) — każda propozycja zaczyna się na tym
   samym słupku, więc przypadki są dokładnie dwa: pierwszy etap jedzie NASZYM
   kursem (siedzimy dalej, wysiadka `stops` przystanków dalej) albo czymkolwiek
   innym (wysiadka na najbliższym przystanku, `stops` = 0). „Nasz kurs"
   poznajemy po tym, że pierwszy przejazd rusza z naszego słupka, naszą linią,
   o naszej sekundzie — ta sama linia nie odjeżdża stamtąd dwa razy w tej samej
   chwili.

Na ekranie wysiadka jest pierwszą rzeczą na karcie propozycji (nad plakietkami
linii — z listy siedmiu wariantów czyta się najpierw ją, a dopiero potem
godziny), a na osi trasy etap, w którym się już siedzi, ma plakietkę „jedziesz
tym pojazdem" i wyróżniony przystanek wysiadania. Nad listą stoi nagłówek „145
w stronę BARTOSZOWICE — najbliższy przystanek Świeradowska o 13:29": pomyłka
w kierunku albo przystanku daje przecież wyniki wyglądające równie sensownie,
tylko dla kogoś innego, więc musi być gdzie sprawdzić, co serwer rozpoznał.

Czego ten tryb świadomie NIE robi: nie szuka w kolejnych dobach (pytanie
dotyczy tego przejazdu, a on kończy się dzisiaj) i nie zapamiętuje się między
wizytami (kurs sprzed odświeżenia strony dawno odjechał — wraca sam
przełącznik, nie wybór pojazdu).

### Mapa przepływów (`plan_flow`)

Jak mapa wybiera, co narysować (wartości podróży, tolerancja, reguła numerów, próg z gęstości), opisuje krok po kroku [ROUTING_ALGORITHM.md](ROUTING_ALGORITHM.md); co mapa obiecuje — [FLOW_MAP_CONTRACT.md](FLOW_MAP_CONTRACT.md).

## Znane ograniczenia

- Auta i rowery na mapie biorą zasięg z tego, co mapa RYSUJE. Mapa z wartości
  jest rzadsza, więc dojazd do auta „po drodze" (autobus o jeden przystanek do
  Traficara) ma tu nie większe szanse niż wcześniej — do decyzji, czy dojazd
  do aut i rowerów ma iść po całej sieci (patrz [FLOW_MAP_NOTES.md](FLOW_MAP_NOTES.md#otwarte-pytania)).
- Bufor przesiadki w skanie wstecz jest stosowany jednolicie (1 min),
  nieco ostrożniej niż w skanie w przód.
- Chodzenie ma zasięg JEDNEGO kroku w promieniu 600 m — z przystanku
  startowego (`_origin_walk`), po wysiadaniu z pojazdu albo z punktu
  klikniętego na mapie. Nie ma więc łańcucha dwóch przejść pod rząd (nie
  dojdziesz „przez" przystanek pośredni do trzeciego).
- Nie ma prawdziwego routingu po chodnikach: znamy współrzędne słupków, nie
  ulice, więc czas marszu bierze się z odległości w LINII PROSTEJ, a cały
  zapas (obejście kwartału, światła, przejście podziemne) siedzi w jednej,
  hojnej prędkości 0,7 m/s. Gdzieniegdzie będzie to wyraźnie za dużo (prosty
  chodnik wzdłuż ulicy), i tak ma być — plan woli nie pokazać przesiadki, niż
  pokazać taką, na którą się nie zdąży. Uczciwie byłoby policzyć te czasy raz,
  prawdziwym routerem po OpenStreetMap, i wstawić gotowe (17 tys. par słupków
  przy 600 m).
- Samo dojście pieszo NIGDY nie jest propozycją trasy: wyszukiwarka planuje
  przejazdy, a trasa bez ani jednego przejazdu nie ma godziny wyjazdu, na
  której opiera się okno mapy (`_journey_start`).
- Mapa przepływów pozwala wsiąść w segment WYŁĄCZNIE w jego zakotwiczonym
  początku (`_extract_transfer_graph`), a kotwica to najwcześniejsze
  zdążalne dołączenie. Gęstsza siatka przejść pieszych przesuwa te kotwice
  wstecz i zabiera ze sobą punkty przesiadki, więc na części relacji lista
  propozycji bywa krótsza, niż mogłaby być. Próba dopuszczenia wsiadania
  w KAŻDYM narysowanym punkcie segmentu (2026-09-10) skończyła się
  wycofaniem: graf puchnie do zera propozycji, a wsiadanie w środku segmentu
  wywraca `arr_times` — jeden punkt wsiadania jest nośny dla kilku struktur
  naraz. Sama mapa i `plan_route` tego nie mają — to ograniczenie wyłącznie
  listy propozycji.
- Scalanie stacji PKP z przystankiem MPK o INNEJ nazwie w jedno miejsce
  (`naming.PLACE_MERGES`) to ręczna tabela sześciu wrocławskich par —
  pozostałe takie pary czekają na dopisanie. Przesiadki to nie blokuje
  (most pieszy bierze się z odległości, nie z nazwy), dotyczy wyłącznie
  tego, co wyszukiwarka rozwija jako jedno miejsce.
- Przejazd rowerem nie ma geometrii: znamy obie stacje, długość trasy jest
  szacowana (odległość w linii prostej × 1,35), a na mapie rysuje się
  odcinek między stacjami, kreską przerywaną. Prawdziwy przebieg wymagałby
  routera rowerowego, którego projekt nie ma.
- Stan stojaków jest sprzed najwyżej minuty, ale nie jest przewidywaniem:
  stacja z jednym rowerem może być pusta, zanim się do niej dojdzie. Karta
  pokazuje więc liczbę rowerów i wolnych miejsc wprost, zamiast udawać, że
  to gwarancja.
- Kafelki mapy i biblioteka Leaflet ładowane z internetu (CDN).
- Połączenia kolejowe (`pkp.py`) obejmują przesiadki - między pociągami też,
  bo CSA widzi jedną tablicę połączeń (patrz historia commitów `pkp.py`) - ale bez
  opóźnień na żywo (API ma je w `/operations`, ale wyszukiwarka czyta tylko
  rozkład planowy z `/schedules`). Wymaga skonfigurowanego `PKP_API_KEY` —
  bez klucza ta część wyników po prostu nie dochodzi. Stacje mają markery na
  mapie (geokodowanie DWOMA źródłami - wyłącznie mapa PLK i portal
  pasażera, oba oficjalne/PKP-owe, na wyraźną prośbę użytkownika - patrz
  historia commitów i nagłówek `update_pkp.py`), ale sama trasa przejazdu
  (`legs[].path`) nie ma geometrii — słownik stacji PKP nie ma współrzędnych
  torów, tylko nazwy, więc geokodowanie daje punkt stacji, nie kształt
  trasy. Pokrycie ok. 98% (3202/3266 na 2026-08-30); stacja ZA GRANICĄ
  (żadne z dwóch źródeł nie obejmuje zagranicy) albo stacja, której żadne
  z dwóch źródeł nie ma (lub znajdzie coś, co automatyczna walidacja
  odrzuci jako niespójne z sąsiadami na trasie - patrz
  `find_suspect_coords`), po prostu nie ma markera, zamiast dostać
  zgadniętą, złą pozycję.
