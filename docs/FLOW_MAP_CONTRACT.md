# Kontrakt mapy przepływów

Gwarancje zachowania **rysowania mapy przepływów** (segmenty, jasność,
kształt sieci) — nie dotyczy listy „Propozycje tras” obok mapy. Testy dla
każdego punktu, historia wdrożeń i otwarte pytania są w
[FLOW_MAP_NOTES.md](FLOW_MAP_NOTES.md); jak to jest policzone — w
[ROUTING_ALGORITHM.md](ROUTING_ALGORITHM.md). Edytuj tę listę tylko
wtedy, gdy zmienia się sama obietnica, nie przy okazji naprawiania buga.

> **Ten plik zmienia się WYŁĄCZNIE na wyraźne polecenie użytkownika.**
> Nigdy z własnej inicjatywy: ani „przy okazji", ani żeby dopisać to, co
> właśnie zostało zaimplementowane, ani żeby odświeżyć opis implementacji,
> który się zdezaktualizował. Jeśli uważasz, że coś tu wymaga zmiany —
> zgłoś propozycję i czekaj na zgodę. Nieaktualny akapit w tym pliku jest
> mniejszym problemem niż kontrakt przepisujący się sam. Wszystko inne
> (historia, pomiary, szczegóły implementacji) idzie do
> [FLOW_MAP_NOTES.md](FLOW_MAP_NOTES.md), który wolno dopisywać zawsze.

## 1. Cały wachlarz, nie jedna trasa

Mapa pokazuje wszystkie sensowne dojazdy naraz, nie tylko najszybszy.
Jasność linii to ciągła miara jakości (0–1), nie binarne pokaż/ukryj.

## 2. Sensowność względem najlepszej trasy

To, co się liczy jako „sensowne”, jest mierzone tym, jak blisko dana opcja
dociera do celu w porównaniu z najlepszym możliwym (najszybszą trasą).
To jedyna miara jakości linii — regulować wolno tylko to, gdzie na niej
stoi próg.

**Próg wynika z czytelności, nie z minut.** Pokazanie za dużo to to samo, co
nie pokazanie nic. Próg jakości przesuwa się więc tak, żeby narysowana sieć
miała docelową gęstość: ile RÓŻNYCH korytarzy leży w kadrze, w którym mapa
pokazuje relację, w stosunku do jego boku (pierwiastka z powierzchni). Liczy
się gęstość na ekranie, a nie w mieście: każdy kadr jest wpasowany w to samo
okno, a kreska ma zawsze tę samą grubość, więc kadr dziesięć razy szerszy
jest na ekranie ciaśniejszy dziesięć razy, nie sto. Linie leżące na sobie
(punkt 7) liczą się raz, bo w oku są jedną kreską — dwadzieścia numerów
jednym korytarzem nie zajmuje miejsca innego korytarza. Trasa przez całe
miasto ma większy kadr, więc mieści więcej niż trasa na kilometr. Docelowa
gęstość to jedyne pokrętło progu i jest suwakiem. Godzina, do której mapa
rysuje (punkt 10), jest skutkiem progu, a nie jego ustawieniem.

**Jeden próg, bez dziur.** Tnie się wyłącznie tym jednym progiem. Jeśli na
mapie jest opcja o danej jakości, jest na niej każda lepsza. Nie ma wyjątków
„pokaż mimo to” ani ukrywania pojedynczych opcji — mapa kończy się w jednym
miejscu skali, zamiast wybierać za pasażera.

**„Pokaż więcej” zawsze coś dokłada.** Każde kliknięcie podnosi cel gęstości
o jedną wyjściową porcję: pierwsze do dwukrotności, drugie do trzykrotności,
kolejne do czterokrotności. Sam cel tego jednak nie gwarantuje, bo mapa
gęstnieje falami, a nie płynnie: dwa kolejne cele potrafią wypaść w tej samej
dziurze i dać tę samą mapę, a przycisk wygląda wtedy na zepsuty. Dlatego
kliknięcie przesuwa próg o co najmniej minutę dalej niż poprzednie, a jeśli to
nic nie zmienia — aż do pierwszej minuty, która naprawdę coś dokłada, choćby
przekroczyła cel gęstości. Dziury to nie robi: tnie się nadal jednym progiem,
tylko postawionym za najbliższą nową rzeczą. Tym samym kliknięciem rośnie
liczba aut i przejazdów rowerem (punkty 15 i 16), a ich reguła luzuje się
o co najmniej jeden poziom. Przedłużenie żyje do następnego wyszukiwania.

## 3. Jasność w każdym punkcie kursu, nie jedna na cały kurs

Ten sam korytarz, którym i tak każdy by pojechał, nie ma prawa migać —
losowo ciemnieć i jaśnieć między sąsiednimi przystankami bez powodu. Ale
to nie znaczy, że jeden, fizyczny kurs musi mieć jedną, stałą jasność od
wsiadania do wysiadania.

Jasność w danym punkcie kursu odzwierciedla, jak dobrym wyborem jest
siedzieć w tym pojeździe **właśnie tutaj** — nie jak dobrym wyborem było
wsiadanie do niego na starcie. Przykład: jedziemy przez korytarz, po
drodze mijamy przystanek, z którego dałoby się przesiąść na wyraźnie
szybszą linię do celu — jeśli się NIE przesiadamy i jedziemy dalej tym
samym pojazdem, dalszy odcinek tego samego, fizycznego kursu ma być
rysowany **ciemniej** niż odcinek przed tą przesiadką. Jeden kurs może
więc wyjść na mapie jako kilka kolejnych kawałków o różnej jasności,
cięte dokładnie w miejscach realnych, pominiętych, lepszych przesiadek —
i tylko tam, gdzie coś naprawdę się zmienia.

## 4. Brak wiszących w powietrzu gałęzi

Cała mapa ma wyglądać jak kształt, który zaczyna się wąsko w punkcie
startowym, rozgałęzia się i poszerza, po czym zwęża z powrotem do punktu
docelowego. Gałąź zaczynająca się w miejscu **nieosiągalnym niczym już
narysowanym**, albo kończąca się w miejscu **niezwiązanym z dotarciem do
celu**, nie ma prawa się pojawić na mapie.

Kryterium jest czysto **fizyczna osiągalność** — czy da się tam realnie,
w czasie, dotrzeć: czymś, co mapa już rysuje, albo **pieszo** (punkt 14) —
nie to, jak jasna jest ta rzecz, przez którą się dociera. Gałąź, do której
się dochodzi, wygląda więc na mapie jak zaczynająca się obok reszty rysunku,
bo przejść pieszo nie rysujemy. To jest świadomy wybór: kreska przy każdym
przejściu zaśmiecałaby mapę bardziej, niż tłumaczy, a że z jednego
przystanku da się dojść do drugiego o dwieście metrów dalej, widać na mapie
samemu. Gałąź kotwiczy więc DOWOLNA zdążalna
przesiadka z narysowanego segmentu, choćby bardzo blada: bladość dojazdu
nie znaczy, że dalsza, jasna część jest nieosiągalna. Tak samo jest na
końcu — jasność kontynuacji nie ma tu nic do rzeczy.

**Żadnych kikutów.** Ogon kończy się dopiero tam, gdzie stoi coś, co
naprawdę prowadzi **dalej**. „Dalej" znaczy dwie rzeczy naraz:

1. **Nie z powrotem po naszych własnych śladach.** Kurs zawracający na
   JAKIKOLWIEK przystanek, przez który już przejechaliśmy — nie tylko na
   ten ostatni — jest drogą powrotną, nie kontynuacją. Inaczej mapa wjeżdża
   na pętlę końcową tylko po to, żeby zaraz z niej wrócić.
2. **Kontynuacja musi sama być narysowana dalej.** To, że jedzie dalej w
   rozkładzie, nie wystarcza — inaczej dwa ogony podpierają się nawzajem i
   spotykają się tam, skąd nic nie odjeżdża.

To NIE to samo, co minięcie lepszej przesiadki i jazda dalej (punkt 3) —
tam jedzie się w stronę celu, tylko nie najlepiej, więc odcinek zostaje
narysowany, po prostu ciemniej.

Kierunek („czy to zawrócenie") czytamy z kolejności przystanków w
rozkładzie, nie z tego, co akurat mieści się w oknie czasowym — inaczej
przesunięcie suwaka okna zmieniałoby odpowiedź i kasowało gałęzie widoczne
przy węższym oknie (patrz punkt 9).

## 6. Geometria po realnych ulicach i torach

Ścieżka segmentu to prawdziwa geometria z rozkładu (`shapes.txt`), nie
linia prosta między przystankami. Gdy geometria nie jest dostępna dla
danego kursu, spada to na łamaną po współrzędnych przystanków.

## 7. Zawsze wiadomo, co tam jedzie

Zawsze da się jednoznacznie rozpoznać, jaka linia (numer) jedzie na danym
odcinku mapy — nawet gdy kilka linii nakłada się na ten sam korytarz —
żeby przełożyć to na realny pojazd, w który trzeba wsiąść. Strzałki
kierunkowe nie są wymagane (kierunek wynika ze start/celu). Sposób
realizacji jest dowolny; liczy się efekt.

To dotyczy też przypadku, gdy kilka linii jedzie dokładnie tym samym
korytarzem i na mapie leżą jedna na drugiej: najechanie w to miejsce ma
pokazać wszystkie z nich, nie tylko tę narysowaną na wierzchu.

Rozsuwania linii nie ma — geometria jest prawdziwa (punkt 6), więc linie
wspólnego korytarza leżą jedna na drugiej. Czytelność robią NUMERY:

- **Skład korytarza z rozkładu, nie z ekranu.** To, które linie jadą danym
  odcinkiem, rozstrzygają wspólne przystanki, nie odległość w pikselach.
- **Numery skondensowane.** Wspólny korytarz dostaje JEDNĄ grupkę ze
  wszystkimi swoimi numerami obok siebie, a nie osobny numer na linię —
  w równych odstępach wzdłuż korytarza i bez nachodzenia na siebie.
- **Kursor nazywa jedną linię.** Pod kursorem podświetla się WYŁĄCZNIE
  jedna linia — na CAŁEJ swojej narysowanej długości, nie tylko kawałek pod
  kursorem — a podpowiedź podaje jej numer wprost; domyślnie najjaśniejsza
  z korytarza. Żeby wskazać inną, najeżdża się na jej numer w grupce.

## 8. Minimalna jasność nigdy nie spada do niewidoczności

Najbledszy koniec skali jasności (punkt 1, q=0) wciąż ma być fizycznie
widoczny na mapie — nie może wyglądać jak przypadkowa, niedokończona
kreska donikąd. Dolny próg opacity i grubości linii jest ustawiony na
tyle wysoko, żeby nawet najbledszy kawałek dało się dostrzec bez
najeżdżania na niego myszką.

## 9. Pełny zakres jasności zawsze wykorzystany

Próg z punktu 2 reguluje, CO jest w ogóle pokazane, ale skala jasności
nie jest liczona względem teoretycznego miejsca progu, tylko względem
najgorszej opcji, która FAKTYCZNIE jest pokazana. Najlepsza trasa zawsze
świeci pełnym blaskiem (q=1), a najgorsza opcja, która akurat się
zmieściła, zawsze ląduje na dole skali (patrz punkt 8 w sprawie tego, że
dół skali nadal jest widoczny na mapie) — niezależnie od tego, gdzie stoi
próg. Skutek: przesunięcie progu, które nie wprowadza żadnej nowej,
gorszej opcji, nie zmienia jasności tego, co już jest na mapie; ale jeśli
wprowadza nową, gorszą opcję, to poprzednio-najgorsze trasy mogą się
realnie rozjaśnić — dół
skali przesunął się niżej. To drugie nie jest błędem, to ta sama zasada
działająca w drugą stronę.

## 10. Mapa mówi, ile to trwa i o której się tam będzie

Mapa odpowiada nie tylko na „jak dojechać”, ale też na „ile to trwa” i
„o której”. Trzema warstwami, od najogólniejszej:

**Bez ruszania myszą** widać czas całej podróży: o której wyjść na najszybszą
trasę, o której się nią dojedzie i od kiedy do kiedy sięga mapa. Wyjście to
najpóźniejsza chwila, z której wciąż dojeżdża się najszybciej — wcześniejsze
kazałoby tylko gdzieś czekać. Godziny przyjazdu mają przy sobie „za ile",
liczone od godziny z formularza — czekanie na pierwszy pojazd jest w tej
liczbie zawarte, bo pasażer i tak czeka.

**Pod kursorem, dla punktu pod kursorem** — nie dla całej linii i nie dla
jakiegoś jej kawałka — widać dwie godziny: o której tym pojazdem jest się
dokładnie tutaj, i o której jest się w celu, jadąc dalej najszybszą możliwą
kontynuacją. Do tego ile to jeszcze zajmie.

**Skąd te godziny.** Z rozkładu tego samego kursu, z którego narysowano ten
odcinek. Zakaz szacowania dotyczy POJAZDÓW: godziny kursu nie wolno wyliczać
z prędkości ani z odległości, bo rozkład je zna. Jedyny czas na mapie, który
nie jest odczytany, to czas przejścia PIESZO (punkt 14) — rozkładu marszu nie
ma, więc liczy się go z odległości, hojnie i zawsze w tę samą stronę. Między dwoma sąsiednimi przystankami mapa **wolno** interpolować —
proporcjonalnie do przebytej drogi, nie średnią: bliżej następnego
przystanku znaczy bliżej jego godziny. Wolno wyłącznie to: interpolacja
**między dwiema godzinami odczytanymi z rozkładu tego samego kursu**. Nie
wolno szacować ze średniej prędkości, z odległości w linii prostej ani
sklejać czasów z dwóch różnych kursów.

**Czego czas nie rusza.** Nie zajmuje żadnego kanału zarezerwowanego dla
jakości trasy — nie zmienia jasności, grubości ani koloru linii (punkty 1,
6, 8, 9). Wchodzi wyłącznie jako liczba dopisana obok. Wyłączenie czasu
zostawia mapę dokładnie taką, jaka była, zanim czas się na niej pojawił.

## 11. Mapa pokazuje, gdzie się przesiąść — i co się tu z każdą linią dzieje

**Gdzie stoi kropka.** Tam, gdzie mapa widzi sensowne wysiadanie — nie na
każdym mijanym przystanku. Miejsce, z którego mapa już nigdzie dalej nie
wiezie, nie jest przesiadką i kropki nie dostaje, choćby coś tam przyjeżdżało.
Tak samo miejsce, przez które wszystko tylko przejeżdża: skoro nic się tu nie
staje dostępne ani nie przestaje, nie ma o czym decydować.
**Jedna na MIEJSCE, nie na słupek:** plac z trzema peronami to jedna
przesiadka, a grupowanie jest to samo, którym rozkład rozpoznaje miejsce —
nie odległość na ekranie.

**Trzy rzeczy, nie jedna.** O każdej linii trzeba tu wiedzieć jedno z trzech —
i to ma być widać, zanim się przeczyta godzinę:

- **wsiadasz tu pierwszy raz** — mapa wcześniej tą linią nie wiozła, więc nie
  było jak wsiąść przed tym miejscem;
- **możesz już nim jechać** — mapa dowozi tu tą linią i wiezie nią dalej, więc
  wsiadanie tutaj jest jedną z możliwości, a nie jedyną;
- **tu z niego wysiadasz** — mapa dowozi tu tą linią i dalej nią nie wiezie.

Znaki są jedną rodziną, czytaną zawsze tak samo: lewy koniec mówi, skąd ten
pojazd tu jest, prawy — co z nim dalej.

**Co pokazuje.** Godzinę, linię, kierunek i za ile — wszystko w jednej
kolejności, po czasie. To nie jest lista samych odjazdów: pojazd, którym się
tu przyjeżdża, jest częścią odpowiedzi na „gdzie ja jestem", nawet gdy się nim
dalej nie jedzie — a jego godzina to godzina PRZYJAZDU, nie najbliższego
odjazdu tej linii.

**Tylko to, o czym mapa coś wie.** Ani odjazd, którego mapa stąd nie proponuje,
ani przyjazd, którym mapa tu nie dowozi — wypisane, wyglądają jak część
podróży, a nią nie są. Kierunek jest częścią tożsamości linii: ta sama linia
mija węzeł w obie strony, a mapa mówi o jednej.

**Tylko to, co jeszcze zdąży.** Odjazd, którym nie da się dojechać do celu
w oknie, które mapa rysuje, to szum udający opcję. Linia, którą stąd już się
nie dojedzie, przestaje być odjazdem — ale jeśli mapa nią tu dowozi, zostaje
jako przyjazd.

**Powtórzenia to jeden wiersz.** Kolejne kursy tej samej linii nie są kolejnymi
opcjami, tylko rytmem jednej: najbliższy odjazd i „co X min". Ani wypisywania
wszystkich, ani gubienia części. Przyjazd nie jest powtórzeniem odjazdu tej
samej linii — to dwa różne zdarzenia i dwa wiersze.

**Godzina, od której liczymy.** Ta z formularza — bo tylko o niej pasażer wie,
że jest prawdziwa. Godzina „będziesz tu o" jest wyłącznie tym, co mapa
policzyła z tego, co sama narysowała, i bywa za późna: kto dojdzie tu pieszo,
dojedzie rowerem albo złapie linię spod progu, stoi tu wcześniej — a tablica
liczona od tamtej godziny zabierała mu nie kilka wierszy, tylko całą
odpowiedź. Przy każdym wierszu stoi też „za ile", liczone od tej samej
godziny.

Odjazdy sprzed chwili, w której mapa stawia tu pasażera, są więc na liście
celowo. Oddziela je widoczna kreska „tu według mapy jesteś" i zajmują najwyżej
połowę wierszy, żeby na ruchliwym węźle nie wypchnęły tych, po które się tu
przyszło. Przy odpowiedzi z kolejnej doby (punkt 13) zostaje godzina mapy:
pytanie sprzed doby nie mówi już nic o tamtym dniu. Wszystko na osi doby
rozkładowej, nie zegarowej: przesiadka o 24:40 należy do rozkładu dnia
poprzedniego.

**Ten sam punkt mówi zawsze to samo.** Drgnięcie kursora o piksel nie zmienia
ani godziny, ani listy. Gdy leży tu kilka kawałków tej samej linii — a to różne
kursy — rozstrzyga jedna, jawna reguła, nie to, który jest bliżej w pikselach.

**Czego kropka nie rusza.** Tak jak czas (punkt 10): nie zajmuje żadnego
kanału zarezerwowanego dla jakości trasy — nie zmienia jasności, grubości ani
koloru. Zdjęcie kropek zostawia mapę dokładnie taką, jaka była.

## 12. Jeden rodzaj rzeczy: tramwaj, autobus, pociąg

**Tramwaj, autobus, pociąg to ten sam rodzaj rzeczy.** Wyszukiwanie nie ma
i nie będzie miało gałęzi „a jeśli pociąg". Kurs to kurs, przystanek to
przystanek, przesiadka to przesiadka — niezależnie od tego, z którego źródła
przyszły.

**Osobne jest wyłącznie pobieranie.** Każde źródło ma swoje API, swój klucz
i swój aktualizator, i to jedyne miejsce, w którym wolno wiedzieć, skąd dane
pochodzą. Poniżej importu nie ma już typów transportu, są kursy. Typ pojazdu
zostaje tylko jako etykieta do pokazania — nigdy jako powód, żeby policzyć
coś inaczej.

**Jedna oś czasu, jedna dokładność.** Wszystkie godziny to pełne minuty.
Źródło podające sekundy jest do nich ucinane ostrożnie — odjazd w dół,
przyjazd w górę — żeby plan bywał pesymistyczny co do sekund, nigdy
optymistyczny.

**Jedno miejsce to jedno miejsce.** Słupki i stacje o tej samej nazwie są tym
samym miejscem, o ile naprawdę stoją obok siebie. Ta sama reguła dla
wszystkich źródeł: nazwa mówi, że to może być to samo, odległość rozstrzyga,
czy jest. Nazwa bez odległości robi z „Mokrej" trzyminutowy spacer przez pół
Polski.

**Po co jeszcze jest wspólne miejsce.** Nie po to, żeby kolej stykała się
z miastem — to robi dziś przejście pieszo, liczone z odległości, bez oglądania
się na nazwę (punkt 14). Wspólne miejsce rozstrzyga o czym innym: co znaczy
wskazany start i cel. Pytając o „Dworzec Główny", pyta się o wszystkie jego
perony naraz, a nie o jeden słupek.

## 13. Zawsze jakaś trasa, choćby za godzinę

**„Nie znaleziono połączenia" to nie odpowiedź na pytanie „jak tam dojadę".**
Jeśli o podaną godzinę nic nie jedzie, mapa pokazuje najbliższą trasę, jaka
jedzie — choćby za godzinę, choćby dopiero rano następnego dnia — i mówi
wprost, o której ona wyrusza. Pusta mapa z komunikatem należy się wyłącznie
relacji, której nie da się przejechać w ogóle.

**Czekanie jest widoczne, nie schowane.** Godzina wyjazdu stoi na pasku nad
mapą, więc trasa ruszająca później niż pytanie ma to napisane przy sobie. Gdy
rusza dopiero innego dnia, mapa mówi to osobno, bo sama godzina tego nie
zdradza. Mapa nigdy nie udaje, że coś jedzie teraz.

**Okno czasowe liczy się od wyjazdu, nie od pytania.** Wachlarz wariantów
wokół takiej trasy jest tak samo szeroki jak wokół każdej innej — godzina
czekania nie zawęża wyboru, bo nie jest częścią podróży.

## 14. Pieszo to też droga

**Jedna zasada na cały system.** Pieszo przechodzi się między dowolnymi
przystankami leżącymi blisko siebie — bez względu na nazwę i na to, czyja to
sieć — i kosztuje to tyle samo na starcie relacji, w przesiadce, u celu oraz
przy punkcie wskazanym kliknięciem w mapę. Jeden promień, jedna cena. Punkt
z mapy jest krańcem relacji jak każdy inny: przystanki wokół niego nie są
„dostępne od razu", tylko oddalone o tyle a tyle minut marszu.

**Przesiadka na dokładnie tym samym słupku to nie przejście.** Nie ma dokąd
iść, więc nie kosztuje marszu, tylko minutę zapasu — nie zero, bo na mapie nie
widać, ile czasu zostaje na przesiadkę, i pasażer nie oceni tego sam. Każdy
inny słupek, także o tej samej nazwie, to już przejście wyceniane jak każde
inne.

**Czas przejścia liczy się z odległości i jest zawyżony.** Rozkład czasu
marszu nie zna, a my znamy tylko odległość w linii prostej — nie chodniki,
nie światła, nie przejścia podziemne. Cały ten brak wiedzy siedzi w jednej,
hojnej prędkości, zaokrąglonej w górę do pełnych minut i nigdy krótszej niż
trzy. Zasada nadrzędna: **lepiej nie pokazać przesiadki, niż pokazać taką, na
którą pasażer nie zdąży**, bo zaniżyliśmy marsz.

**Przejście musi coś OTWIERAĆ.** Marsz ma sens tylko wtedy, gdy daje dostęp do
kursu, którego inaczej nie da się złapać — choćby dojazd nie był przez to
szybszy, byle mieścił się w oknie (punkt 2). Kurs, który zatrzymuje się BLIŻEJ
nas, takim kursem nie jest: po ten sam pojazd nie chodzi się dalej, niż
trzeba. Ani ze wskazanego przystanku — skoro autobus i tak po nas przyjedzie —
ani z punktu na mapie: z dwóch jego przystanków wybiera się ten z krótszym
dojściem, nawet jeśli kurs mija ten dalszy wcześniej. Mapa rysuje taki kurs od
przystanku, do którego jest bliżej.

**Jeden krok.** Pieszo idzie się raz: ze startu albo po wysiadaniu z pojazdu.
Nie ma łańcucha dwóch przejść pod rząd — inaczej „dojście" zaczęłoby znaczyć
spacer przez pół dzielnicy.

**Samo przejście nie jest trasą.** Ta wyszukiwarka planuje przejazdy, a trasa
bez ani jednego przejazdu nie ma godziny wyjazdu, na której opiera się okno
mapy. Relacja, którą da się przejść pieszo, nie dostaje propozycji „po prostu
idź".

**Przejść na mapie nie widać** — patrz punkt 4.

## 15. Auto na wynajem to miejsce, nie kurs

**Widać tylko te auta, do których mapa dowozi.** Wolne auto car-sharingu staje
na mapie wtedy, gdy da się do niego dojść jednym dojściem — tą samą regułą co
każde inne (punkt 14) — od czegoś, co mapa rysuje: od przystanku, na który
dowozi, albo od samego startu. Auto stojące gdzieś w mieście, bez związku z tą
relacją, nie jest częścią odpowiedzi.

**Osobówki i dostawczaki to dwa osobne wybory.** Dostawczaków domyślnie nie
ma na mapie wcale — pokazuje je dopiero przełącznik w ustawieniach. Wtedy
wszystko niżej dotyczy każdego rodzaju osobno: dostawczak nigdy nie chowa
osobówki ani osobówka dostawczaka, bo kto wiezie szafę, nie weźmie Clio, a kto
jedzie sam, nie chce Mastera. Każdy rodzaj dostaje tę samą liczbę z suwaka.
Który to rodzaj, mówi sam Traficar, a nie zgadywanie po nazwie modelu.

**Auto porównuje się dwiema liczbami: o której się przy nim jest i ile daje
za nie program „Ogarniam”.** Auto bije inne, jeśli jest co najmniej tak dobre
w obu naraz i w którymś lepsze. Porównuje się z tą dokładnością, z jaką
liczby są wypisane — ta sama minuta to remis, a nie wygrana o sekundy.
Odległość od auta do celu NIE jest kryterium: nagradzałaby auta stojące tuż
przy celu, a rozstrzygnąć, czy jazda autem się opłaca, mapa nie potrafi —
czasu jazdy autem nie da się rzetelnie oszacować, bo zależy od korków.
Czy auto się opłaca, rozstrzyga pasażer.

**Auta spod tego samego miejsca to jeden wybór.** Grupę tworzą auta, do
których mapa prowadzi dojście z tego samego miejsca: ze startu albo z tego
samego przystanku. W grupie widać tylko auta, których nic w tej grupie nie
bije — zwykle jedno, najwyżej dwa, gdy przy jednym jest się wcześniej, a
drugie daje więcej z „Ogarniam”. Trzy auta stojące obok siebie przy starcie
to dla pasażera jeden wybór, więc mapa nie pokazuje trzech. Granica grupy
nie jest promieniem wymyślonym za pasażera, tylko wynika z reguły dojścia
(punkt 14): mapa i tak mówi, skąd do auta się idzie.

**Zawsze widać każde auto, którego nie bije zwycięzca żadnej innej grupy**
(zbiór Pareto zwycięzców grup). Każde z nich jest najlepsze w czymś, a wybór
między nimi zostaje przy pasażerze — mapa nie waży minut przeciw złotówkom.
Auto z „Ogarniam” nie ma osobnej reguły: widać je dokładnie wtedy, gdy nie ma
auta, przy którym jest się nie później i które daje co najmniej tyle samo.

**Więcej aut to luźniejsza reguła, nie wybrane auta** (k-skyband, którego
zbiór Pareto jest pierwszym poziomem) — **i tylko między grupami.** Suwak
mówi, ile aut mapa ma pokazać. Spośród zwycięzców grup dokłada się wtedy
auta pobite przez najwyżej jedno inne, potem przez najwyżej dwa i tak dalej,
aż uzbiera się tyle, ile ustawiono. Kolejny poziom wchodzi w całości, choćby
przekroczył liczbę z suwaka, bo ucięcie go w środku wymagałoby zważenia
kryteriów. Poszerzanie nigdy nie dokłada kolejnego auta z tej samej grupy —
inaczej już pierwsze „więcej” przywracałoby auta stojące obok siebie.
Kliknięcie luzuje regułę o co najmniej jeden poziom także wtedy, gdy liczba
z suwaka jest już przekroczona: poziom, którego nic nie bije, bywa sam
liczniejszy niż suwak, a wtedy podniesienie samej liczby nie zmieniałoby nic. Skutek
jest ten sam co przy liniach (punkt 2): nigdy nie widać auta, gdy schowane
jest inne, które je bije. „Pokaż więcej” mnoży liczbę z suwaka tak samo jak
gęstość linii.

**Auto mówi to samo, co przystanek: o której się przy nim jest.** Godzina
z rozkładu plus marsz, razem z tym, skąd ten marsz prowadzi.

**O jeździe autem mapa nie mówi NIC.** Auto nie ma rozkładu, a routingu
samochodowego tu nie ma — więc nie ma czasu jazdy, przebiegu ani godziny
dotarcia do celu. Zostaje odległość celu w linii prostej i decyzja pasażera.
Auto nie jest kursem: nie ma linii, nie ma jasności, nie wchodzi do wachlarza
i niczego w nim nie przestawia.

**Tylko dzisiaj.** Auta stoją tam, gdzie stoją w tej chwili. Przy pytaniu
o inny dzień nie pokazujemy ich wcale — godzina „będziesz przy nim” byłaby
wtedy zgadywaniem podanym jako fakt. Brak aut nigdy nie jest błędem
wyszukiwania: milczące źródło zabiera znaczniki, nie odpowiedź.

## 16. Rower miejski to przejście o innym tempie

**Rower nie jest kursem.** Nie ma rozkładu, nie ma numeru, nie ma jasności
i nie wchodzi do wachlarza. Jest tym, czym pieszo (punkt 14), tylko szybszym:
sposobem przemieszczenia się między dwoma miejscami, którego rozkład nie zna.
Zdjęcie roweru zostawia mapę dokładnie taką, jaka była.

**Widać tylko to, na czym da się WSIĄŚĆ.** Kandydatem jest rower, do którego
mapa dowozi jednym dojściem — tą samą regułą co każde inne (punkt 14) — od
czegoś, co mapa rysuje, albo od samego startu. Stacja bez rowerów nie jest
miejscem, z którego da się wyjechać. Kropkę dostaje początek przejazdu, który
przeszedł wybór (niżej). Drugi koniec przejazdu własnej kropki nie
dostaje: pokazuje się razem z nim, bo opisuje ten przejazd, a nie siebie. Jeśli
sam jest początkiem wybranego przejazdu, stoi na mapie z własnego tytułu.

**Przejazd musi prowadzić do celu — przez to, co mapa RYSUJE.** Zostaje wtedy,
gdy po zsiadaniu zdąży się jeszcze wsiąść w kawałek, który mapa pokazuje i
który wiezie dalej, albo dojechać pod sam cel. Nie musi być szybszy niż
tramwaj: ktoś może chcieć jechać rowerem dlatego, że woli rower. Musi natomiast
dowozić naprawdę. Przystanek, na który da się zdążyć, ale z którego mapa nie
rysuje ani jednego odjazdu, niczego nie otwiera — „zdążę tam" to nie to samo,
co „stamtąd dojadę". Tak samo nie liczy się przystanek, na którym narysowany
kawałek się kończy: tam się wysiada.

**Rodzaj roweru to osobny wybór.** Elektryczny i zwykły mają własne przyciski
w pasku warstw, obok siebie — kto chce elektryka, nie weźmie
zwykłego, i odwrotnie. Miejsce jest kandydatem, gdy stoi w nim choć jeden
rower włączonego rodzaju; zgaszenie obu znaczy to samo, co dawniej zgaszony
jeden przycisk „Rowery”.
To odsiew MIEJSC, a nie zmiana wyceny przejazdu: rower ma jedną prędkość
niezależnie od rodzaju, więc odhaczenie jednego nie przesuwa na mapie żadnej
godziny. Dotyczy wsiadania — stacja, na której przejazd się kończy, żadnego
roweru mieć nie musi. Przy pytaniu o inny dzień stan stojaków jest nieznany,
więc rodzaj nie odsiewa wtedy nic, zamiast udawać tę wiedzę.

**Rowery dokłada się na gotową mapę.** Najpierw powstaje mapa komunikacji,
z progiem z punktu 2, i rower niczego w niej nie przestawia. Każdy przejazd
rowerem ocenia się na tej mapie BEZ innych rowerów: dojazd do niego i dalsza
droga po nim idą tym, co mapa rysuje. Dwa rowery w jednej podróży to więc
dwa niezależne przejazdy — każdy jest na mapie, jeśli sam się broni.

**Przejazd rowerem ocenia się w całej podróży, nie jako sam odcinek.**
Z samego odcinka wiadomo tylko, jak jest długi; godzina w celu i przesiadki
istnieją dopiero dla drogi od startu do celu. Ocenia się trzema liczbami,
wszystkimi z tej samej podróży:
1. **Ile jedzie się rowerem** (długość przejazdu w linii prostej) — więcej
   znaczy lepiej, bo ktoś może chcieć przejechać rowerem jak najwięcej.
2. **O której jest się w celu** — wcześniej znaczy lepiej. Godzina zsiadania
   jest policzona (wyjątek niżej), ale dalsza droga jest odczytana z rozkładu.
   Ta jedna liczba mówi zarówno „rower jest szybszy”, jak i „rower traci
   najmniej”, gdy żaden nie jest szybszy od samej komunikacji.
3. **Ile jest przesiadek** — przed rowerem i po nim razem; mniej znaczy lepiej.
   Przesiadka NIE jest przeliczana na minuty: jej kosztem jest ryzyko, że
   kurs się nie zjawi albo się nie zdąży, a tego rozkład nie zawiera — kara
   w minutach byłaby ważeniem kryteriów.

**Każda para godziny i przesiadek to jedna prawdziwa podróż.** Najszybsza
droga po rowerze bywa inna niż ta z najmniejszą liczbą przesiadek, więc ten
sam przejazd daje zwykle kilka par — np. „w celu 15:30, dwie przesiadki”
i „w celu 15:35, jedna”. Nie wolno wziąć najlepszej godziny z jednej drogi
i najmniej przesiadek z drugiej: takiej podróży nie ma, a przejazd wygrywałby
nią z przejazdami, które naprawdę coś dają. Ten sam przejazd może przejść
wybór więcej niż raz; na mapie to wciąż jeden przejazd.

**Wybór jak przy autach** (punkt 15). Podróż bije inną, jeśli jest co
najmniej tak dobra we wszystkich trzech liczbach naraz i w którejś lepsza,
z dokładnością, z jaką liczby są wypisane. Zawsze widać przejazdy z podróży,
których nic nie bije, a więcej rowerów to kolejne poziomy tej samej reguły,
wchodzące w całości. Rowery mają WŁASNY suwak liczby pod zębatką, osobny od
aut; „Pokaż więcej” mnoży go tak samo jak gęstość linii i liczbę aut.

**Skąd rower się bierze i gdzie wraca.** Wypożyczyć da się ze stojaka albo
wprost z ulicy, bo rower stojący luzem wypożycza się tak samo jak ten ze
stacji — więc jako początek przejazdu liczy się na równi z nią. Oddać już nie:
przejazd kończy się zawsze na stacji, bo zostawienie roweru poza nią jest u
operatora osobną, wysoką opłatą, a mapa nie ma prawa proponować czegoś, za co
pasażer zapłaci, nie wiedząc o tym.

**Czas przejazdu wolno policzyć, bo nie ma go skąd odczytać.** To ten sam
wyjątek, co przy marszu (punkt 10): zakaz szacowania dotyczy pojazdów, które
mają rozkład. Jedna prędkość, mierzona w linii prostej, zaokrąglana w górę,
z osobnym narzutem na wypożyczenie i oddanie. Rozbicia na prędkość i krętość
nie ma, bo mapa nie zna przebiegu trasy — dwie liczby udawałyby wiedzę, której
nie ma.

**Przejazdu mapa nie rysuje.** Dwie kropki mówią to samo, co kreska między
nimi, a kresek byłyby setki. Kreski pojawiają się pod kursorem, przy kropce,
o którą się pyta — tylko przejazdów, które przeszły wybór, a nie do każdej
stacji, do której z tej kropki dałoby się dojechać. Obok drugiego końca stoi odległość w linii prostej — nie
godzina: godzina byłaby jedyną liczbą na tej mapie policzoną, a wyglądającą
na odczytaną.

**Tylko dzisiejszy stan.** Ile rowerów stoi w stojaku, wiadomo z tej chwili
i tylko z tej chwili. Przy pytaniu o inny dzień kropki zostają — stacja stoi
tam zawsze — ale mapa mówi wprost, że stanu nie zna, zamiast podać dzisiejszą
liczbę jako jutrzejszą. Milczące źródło zabiera kropki, nigdy odpowiedź na
pytanie „jak tam dojadę".

## Priorytet: poprawność przed szybkością

Rozsądna szybkość działania jest pożądana, ale nigdy kosztem poprawności.
Wolniejszy, ale dokładny wynik jest lepszy niż szybki, ale niedokładny.
