# Kontrakt mapy przepływów

Gwarancje zachowania **rysowania mapy przepływów** (segmenty, próg,
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

Mapa pokazuje wszystkie sensowne dojazdy naraz, nie tylko najszybszy. Każdy
dojazd to cała podróż od startu do celu, opisana dwiema liczbami: o której
jest się w celu i o której trzeba wyjść. Na mapie jest to, co w którejś z nich
jest najlepsze albo mieści się w progu (punkt 2). Każda narysowana linia
wygląda tak samo — o tym, czy coś jest na mapie, rozstrzyga próg.

## 2. Sensowność względem najlepszej trasy

To, co się liczy jako „sensowne”, jest mierzone tym, **o ile dłużej trwa
podróż**. Każda podróż ma tolerancję w minutach, a to SUMA dwóch strat:
o ile później jest w celu niż najszybsza i o ile wcześniej wychodzi niż
podróż, która w celu nie jest później — wcześniejsze wyjście, żeby i tak nie
być wcześniej, to czekanie. Przy tolerancji 16 minut są więc na mapie podróże
najwyżej 16 minut dłuższe. To jedyna miara — regulować wolno tylko to, gdzie na niej stoi
próg.

**Przesiadki i chodzenie liczą się tylko w minutach.** Nie są osobną wartością
i niczego same nie rozstrzygają: trasa z dwiema przesiadkami 5 minut później
wchodzi tak samo jak każda inna 5 minut później. Dłuższy marsz to po prostu
późniejszy przyjazd albo wcześniejsze wyjście.

**Próg wynika z czytelności, nie z minut.** Pokazanie za dużo to to samo, co
nie pokazanie nic. Próg przesuwa się więc tak, żeby narysowana sieć
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
mapie jest opcja o danej tolerancji, jest na niej każda o mniejszej. Nie ma
wyjątków „pokaż mimo to” ani ukrywania pojedynczych opcji — mapa kończy się
w jednym miejscu skali, zamiast wybierać za pasażera. Jedyne, czego nie ma
przy żadnym progu, to trasy, które niczego nie dają (niżej).

**Te same numery to ta sama trasa.** Trasa jadąca tymi samymi numerami linii
co podróż nie gorsza w minutach — albo tymi samymi i jeszcze innymi — nie
pojawia się przy żadnym progu: to ta sama podróż, tylko gorzej. Tak odpada
przesiadka w pojazd, który i tak zaraz przyjedzie (tramwaj 14 na Borku:
piątka i siedemnastka z dołożoną czternastką, te same godziny). Z dróg tymi
samymi numerami o tych samych minutach rysuje się najkrótsza — przejechanie
przystanku przesiadki i powrót tym samym kursem to nie wariant, tylko
ogonek. Z jednym wyjątkiem: droga, która idzie pieszo tam, dokąd dowiózłby
ten sam kurs — złapany wcześniej albo opuszczony później — przegrywa z drogą,
która tym kursem jedzie (punkt 14). Objazd zmienia dwa przejazdy naraz, więc
tym wyjątkiem nie wraca. Trasa INNYMI numerami wchodzi, nawet gdy jest
dłuższa, a nie szybsza: to już inna trasa.

**„Pokaż więcej” zawsze coś dokłada.** Przycisk to zwykły licznik: da się go
kliknąć trzy razy, niezależnie od tego, ile jeszcze zostało do pokazania.
Każde kliknięcie podnosi cel gęstości o jedną wyjściową porcję: pierwsze do
dwukrotności, drugie do trzykrotności, trzecie do czterokrotności. Sam cel
tego jednak nie gwarantuje, bo mapa gęstnieje falami, a nie płynnie: dwa
kolejne cele potrafią wypaść w tej samej dziurze i dać tę samą mapę, a
przycisk wygląda wtedy na zepsuty. Dlatego
kliknięcie przesuwa próg o co najmniej minutę dalej niż poprzednie, a jeśli to
nic nie zmienia — aż do pierwszej minuty, która naprawdę coś dokłada, choćby
przekroczyła cel gęstości. Dziury to nie robi: tnie się nadal jednym progiem,
tylko postawionym za najbliższą nową rzeczą. Tym samym kliknięciem rośnie
liczba aut i przejazdów rowerem (punkty 15 i 16), a ich reguła luzuje się
o co najmniej jeden poziom. Przedłużenie żyje do następnego wyszukiwania.

## 4. Brak wiszących w powietrzu gałęzi

Cała mapa ma wyglądać jak kształt, który zaczyna się wąsko w punkcie
startowym, rozgałęzia się i poszerza, po czym zwęża z powrotem do punktu
docelowego. Gałąź zaczynająca się w miejscu **nieosiągalnym niczym już
narysowanym**, albo kończąca się w miejscu **niezwiązanym z dotarciem do
celu**, nie ma prawa się pojawić na mapie.

Każdy narysowany kawałek jest częścią całej podróży od startu do celu.
Kryterium jest czysto **fizyczna osiągalność** — czy da się tam realnie,
w czasie, dotrzeć: czymś, co mapa już rysuje, albo **pieszo** (punkt 14).
Gałąź, do której się dochodzi, wygląda więc na mapie jak zaczynająca się obok
reszty rysunku, bo przejść pieszo nie rysujemy. To jest świadomy wybór:
kreska przy każdym przejściu zaśmiecałaby mapę bardziej, niż tłumaczy, a że
z jednego przystanku da się dojść do drugiego o dwieście metrów dalej, widać
na mapie samemu.

**Żadnych kikutów.** Ogon kończy się dopiero tam, gdzie stoi coś, co
naprawdę prowadzi **dalej**. „Dalej" znaczy dwie rzeczy naraz:

1. **Nie z powrotem po naszych własnych śladach.** Kurs zawracający na
   JAKIKOLWIEK przystanek, przez który już przejechaliśmy — nie tylko na
   ten ostatni — jest drogą powrotną, nie kontynuacją. Inaczej mapa wjeżdża
   na pętlę końcową tylko po to, żeby zaraz z niej wrócić.
2. **Kontynuacja musi sama być narysowana dalej.** To, że jedzie dalej w
   rozkładzie, nie wystarcza — inaczej dwa ogony podpierają się nawzajem i
   spotykają się tam, skąd nic nie odjeżdża.

Ogonka „tam i z powrotem tym samym kursem" nie ma — to ta sama podróż
tymi samymi numerami, rysowana najkrótszą drogą (punkt 2).

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
  kursorem — a podpowiedź podaje jej numer wprost. Żeby wskazać inną,
  najeżdża się na jej numer w grupce.

## 10. Mapa mówi, ile to trwa i o której się tam będzie

Mapa odpowiada nie tylko na „jak dojechać”, ale też na „ile to trwa” i
„o której”. Trzema warstwami, od najogólniejszej:

**Bez ruszania myszą** widać czas całej podróży: o której wyjść na najszybszą
trasę, o której się nią dojedzie i od kiedy do kiedy sięga mapa. Wyjście to
najpóźniejsza chwila, z której wciąż dojeżdża się najszybciej — wcześniejsze
kazałoby tylko gdzieś czekać. Przyjazd najszybszą trasą ma przy sobie „za ile",
liczone od godziny z formularza — czekanie na pierwszy pojazd jest w tej
liczbie zawarte, bo pasażer i tak czeka.

**Pod kursorem, dla punktu pod kursorem** — nie dla całej linii i nie dla
jakiegoś jej kawałka — widać dwie godziny: o której tym pojazdem jest się
dokładnie tutaj, i o której jest się w celu, jadąc dalej najszybszą możliwą
kontynuacją. Do tego ile to jeszcze zajmie.

**Skąd te godziny.** Z rozkładu tego samego kursu, z którego narysowano ten
odcinek. Zakaz szacowania dotyczy POJAZDÓW: godziny kursu nie wolno wyliczać
z prędkości ani z odległości, bo rozkład je zna. Czasy na mapie, które nie
są odczytane, to wyłącznie te, dla których rozkładu nie ma: przejście PIESZO
(punkt 14), jazda autem (punkt 15) i przejazd rowerem (punkt 16) — liczone
z odległości, hojnie i zawsze w tę samą stronę. Między dwoma sąsiednimi przystankami mapa **wolno** interpolować —
proporcjonalnie do przebytej drogi, nie średnią: bliżej następnego
przystanku znaczy bliżej jego godziny. Wolno wyłącznie to: interpolacja
**między dwiema godzinami odczytanymi z rozkładu tego samego kursu**. Nie
wolno szacować ze średniej prędkości, z odległości w linii prostej ani
sklejać czasów z dwóch różnych kursów.

**Czego czas nie rusza.** Nie zmienia wyglądu linii — ani grubości, ani
koloru. Wchodzi wyłącznie jako liczba dopisana obok. Wyłączenie czasu
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

**Co pokazuje.** Jeden wiersz na linię i kierunek: numer, kierunek i po kolei
godziny — nic więcej: bez „za ile", bez „co N min" i bez godziny
w nagłówku. Rząd godzin mówi o takcie linii to, co trzeba, bez uogólniania.
Wiersze idą po czasie. To nie jest lista samych
odjazdów: pojazd, którym się tu przyjeżdża, jest częścią odpowiedzi na „gdzie
ja jestem", nawet gdy się nim dalej nie jedzie — a jego godzina to godzina
PRZYJAZDU, nie najbliższego odjazdu tej linii.

**Tylko to, o czym mapa coś wie.** Ani odjazd, którego mapa stąd nie proponuje,
ani przyjazd, którym mapa tu nie dowozi — wypisane, wyglądają jak część
podróży, a nią nie są. Kierunek jest częścią tożsamości linii: ta sama linia
mija węzeł w obie strony, a mapa mówi o jednej.

**Tylko to, co jeszcze zdąży.** Odjazd, którym nie da się dojechać do celu
w oknie, które mapa rysuje, to szum udający opcję. Linia, którą stąd już się
nie dojedzie, przestaje być odjazdem — ale jeśli mapa nią tu dowozi, zostaje
jako przyjazd.

**Powtórzenia to jeden wiersz.** Kolejne kursy tej samej linii nie są kolejnymi
opcjami, tylko jedną: jej godziny stoją obok siebie w jednym wierszu, do
ostatniego kursu, którym jeszcze się dojedzie. Linia, którą mapa tu dowozi
i wiezie dalej, to też jeden wiersz — ze znakiem „możesz już nim jechać",
a nie osobny przyjazd i osobny odjazd.

**Godzina, od której liczymy.** Ta z formularza — bo tylko o niej pasażer wie,
że jest prawdziwa. Godzina „będziesz tu o" jest wyłącznie tym, co mapa
policzyła z tego, co sama narysowała, i bywa za późna: kto dojdzie tu pieszo,
dojedzie rowerem albo złapie linię spod progu, stoi tu wcześniej — a tablica
liczona od tamtej godziny zabrałaby mu nie kilka wierszy, tylko całą
odpowiedź.

Odjazdy sprzed chwili, w której mapa stawia tu pasażera, są więc na liście
celowo — jako szare godziny. Linia, na którą według mapy już się nie zdąży,
idzie na koniec, żeby na ruchliwym węźle nie wypchnęła tych, po które się tu
przyszło. Szare godziny da się wyłączyć w ustawieniach (domyślnie są) — wtedy
tablica zaczyna się od chwili z mapy. Wszystko na osi doby rozkładowej, nie
zegarowej: przesiadka o 24:40 należy do rozkładu dnia poprzedniego.

**Ten sam punkt mówi zawsze to samo.** Drgnięcie kursora o piksel nie zmienia
ani godziny, ani listy. Gdy leży tu kilka kawałków tej samej linii — a to różne
kursy — rozstrzyga jedna, jawna reguła, nie to, który jest bliżej w pikselach.

**Czego kropka nie rusza.** Tak jak czas (punkt 10): nie zmienia wyglądu
linii. Zdjęcie kropek zostawia mapę dokładnie taką, jaka była.

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
rusza dopiero innego dnia albo czeka się na nią dłużej niż 20 minut, mapa mówi
to jeszcze osobno, komunikatem nad wynikami — godzinę na pasku łatwo
przeoczyć. Mapa nigdy nie udaje, że coś jedzie teraz.

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
przystanku, do którego jest bliżej. Tak samo w przesiadce: po tramwaj nie idzie
się dziesięć minut wzdłuż jego trasy, skoro staje trzy minuty od autobusu, i
z autobusu nie wysiada się przystanek wcześniej, żeby dalej iść tam, dokąd sam
dowiezie.

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

**Auto porównuje się trzema liczbami — trzema powodami, dla których się je
bierze:** o której się przy nim jest (wsiądę od razu), ile daje za nie program
„Ogarniam” (zarobię) i o której dowiezie do celu (dojadę szybciej). Auto bije
inne, jeśli jest co najmniej tak dobre we wszystkich trzech naraz i w którejś
lepsze. Porównuje się z tą dokładnością, z jaką liczby są wypisane — ta sama
minuta to remis, a nie wygrana o sekundy. Dlatego widać też auto, do którego
jest się później i które nic nie daje, jeśli dowiezie szybciej: tramwaj od
razu na Księże Małe, tam auto do Radwanic.

**Sama odległość od auta do celu NIE jest kryterium** — nagradzałaby auta
stojące tuż przy celu. Przyjazd tej wady nie ma: do auta przy celu dociera się
wtedy, kiedy i tak prawie jest się na miejscu, a ruszenie i parkowanie zjadają
resztę, więc takie auto niczego nie wygrywa.

**Zawsze widać każde auto, którego nie bije żadne inne** (zbiór Pareto). Każde z nich jest najlepsze w czymś, a wybór
między nimi zostaje przy pasażerze — mapa nie waży minut przeciw złotówkom.
Auto z „Ogarniam” nie ma osobnej reguły: widać je dokładnie wtedy, gdy nie ma
auta, przy którym jest się nie później, które daje co najmniej tyle samo
i dowozi nie później.

**Więcej aut to luźniejsza reguła, nie wybrane auta** (k-skyband, którego
zbiór Pareto jest pierwszym poziomem). Suwak mówi, ile aut mapa ma pokazać.
Dokłada się wtedy auta pobite przez najwyżej jedno inne, potem przez
najwyżej dwa i tak dalej, aż uzbiera się tyle, ile ustawiono. Kolejny poziom
wchodzi w całości, choćby przekroczył liczbę z suwaka, bo ucięcie go
w środku wymagałoby zważenia kryteriów. Kliknięcie luzuje regułę o co najmniej jeden poziom także wtedy, gdy liczba
z suwaka jest już przekroczona: poziom, którego nic nie bije, bywa sam
liczniejszy niż suwak, a wtedy podniesienie samej liczby nie zmieniałoby nic. Skutek
jest ten sam co przy liniach (punkt 2): nigdy nie widać auta, gdy schowane
jest inne, które je bije. „Pokaż więcej” mnoży liczbę z suwaka tak samo jak
gęstość linii.

**Auto mówi to samo, co przystanek: o której się przy nim jest.** Godzina
z rozkładu plus marsz, razem z tym, skąd ten marsz prowadzi.

**O jeździe autem mapa wie jedno: o której mniej więcej dowiezie do celu.**
Auto nie ma rozkładu, a routingu samochodowego tu nie ma, więc czasu jazdy nie
ma skąd odczytać — wolno go policzyć, tak jak marsz i rower (punkty 10, 14,
16). Jedna prędkość w linii prostej do celu, zaokrąglana w górę, plus stały
narzut na ruszenie i parkowanie; obie liczby pasażer ustawia sobie sam.
Rozbicia na prędkość i krętość nie ma, bo mapa nie zna przebiegu trasy.
Przebiegu, długości trasy ani korków mapa nie udaje.

**Szacunek decyduje zawsze, widać go na życzenie.** Wybór aut idzie po nim
zawsze. W dymku domyślnie stoi odległość celu w linii prostej; szacowaną
godzinę dopisuje dopiero przełącznik, osobny od rowerowego, i podpisuje ją
jako przybliżoną. Szacunek bywa bardzo zły — i to jest w porządku: pasażer
widzi odległość, wie, ile u niego trwa jazda, i taką opcję zignoruje. Lepiej
pokazać auto, które może się opłacić, niż schować je, bo nie da się tego
policzyć dokładnie.

**Auto nie jest kursem:** nie ma linii, nie wchodzi do
wachlarza i niczego w nim nie przestawia.

**Tylko dzisiaj.** Auta stoją tam, gdzie stoją w tej chwili. Przy pytaniu
o inny dzień nie pokazujemy ich wcale — godzina „będziesz przy nim” byłaby
wtedy zgadywaniem podanym jako fakt. Brak aut nigdy nie jest błędem
wyszukiwania: milczące źródło zabiera znaczniki, nie odpowiedź.

## 16. Rower miejski to przejście o innym tempie

**Rower nie jest kursem.** Nie ma rozkładu, nie ma numeru i nie wchodzi do wachlarza. Jest tym, czym pieszo (punkt 14), tylko szybszym:
sposobem przemieszczenia się między dwoma miejscami, którego rozkład nie zna.
Zdjęcie roweru zostawia mapę dokładnie taką, jaka była.

**Widać tylko to, na czym da się WSIĄŚĆ.** Kandydatem jest rower, do którego
mapa dowozi jednym dojściem — tą samą regułą co każde inne (punkt 14) — od
czegoś, co mapa rysuje, albo od samego startu. Stacja bez rowerów nie jest
miejscem, z którego da się wyjechać. Kropkę dostaje stacja (albo rower luzem),
która przeszła wybór (niżej). Drugi koniec przejazdu własnej kropki nie
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
rower włączonego rodzaju; zgaszenie obu gasi rowery w całości.
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

**Rower ocenia się w całej podróży, nie jako sam odcinek.** Z samego odcinka
wiadomo tylko, jak jest długi; godzina w celu i przesiadki istnieją dopiero
dla drogi od startu do celu. Każda podróż przez rower ma trzy liczby, wszystkie
z tej samej drogi:
1. **O której jest się przy rowerze** — wcześniej znaczy lepiej. To
   kryterium dla kogoś, kto po prostu chce jechać rowerem: ma dostać rower
   najszybciej osiągalny ze startu, a nie taki, do którego najpierw idzie się
   albo jedzie w złą stronę. Godzina pochodzi z tej samej podróży co dwie
   pozostałe liczby. Wypożyczenie roweru nie jest przesiadką.
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
nią z przejazdami, które naprawdę coś dają. Ten sam przejazd bywa więc kilkoma
podróżami; na mapie to wciąż jeden przejazd.

**Wybór idzie w dwóch etapach: najpierw stacje, potem przejazdy na stacji.**
„O której jest się przy rowerze” mówi coś o stacji, nie o przejeździe z niej
— wszystkie przejazdy z jednej stacji mają tę samą godzinę — więc to stacje,
a nie przejazdy, konkurują między sobą. Stacją jest tu każde miejsce, z którego
się wyjeżdża, także rower luzem.

1. **Które stacje — jak przy autach** (punkt 15). Podróż jest co najmniej tak
   dobra jak inna, gdy jest taka we wszystkich trzech liczbach naraz,
   z dokładnością, z jaką liczby są wypisane. Stacja bije inną, gdy daje
   KAŻDĄ jej podróż co najmniej tak samo dobrze, a tamta jej nie. Stacja po
   drodze przegrywa więc ze stacją przy starcie dopiero wtedy, gdy ta daje
   wszystko, co ona, a nie wtedy, gdy jest osiągalna wcześniej — wystarczy, że
   stacja po drodze ma najszybszą podróż przy swojej liczbie przesiadek. Zawsze
   widać stacje, których nic nie bije; więcej rowerów to kolejne poziomy tej
   samej reguły, wchodzące w całości. Rowery mają WŁASNY suwak pod zębatką,
   liczący stacje, osobny od aut; „Pokaż więcej” mnoży go tak samo jak gęstość
   linii i liczbę aut.
2. **Które przejazdy na stacji — każda stacja osobno.** Ich łączna liczba nie
   ma znaczenia, bo widać je dopiero pod kursorem. Na stacji zostają przejazdy,
   których nie bije inny przejazd z tej samej stacji: w celu nie później i nie
   więcej przesiadek. Kilka przejazdów z tym samym wynikiem, tylko do różnych
   stacji, zostaje na razie wszystkich — to osobny, jeszcze nierozstrzygnięty
   problem.

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
o którą się pyta — tylko przejazdów, które stacja pokazuje, a nie do każdej
stacji, do której z tej kropki dałoby się dojechać. Obok drugiego końca stoi odległość w linii prostej — nie
godzina: godzina jest policzona, a wyglądałaby na odczytaną. Dopisuje ją
dopiero przełącznik, osobny od aut (punkt 15).

**Tylko dzisiejszy stan.** Ile rowerów stoi w stojaku, wiadomo z tej chwili
i tylko z tej chwili. Przy pytaniu o inny dzień kropki zostają — stacja stoi
tam zawsze — ale mapa mówi wprost, że stanu nie zna, zamiast podać dzisiejszą
liczbę jako jutrzejszą. Milczące źródło zabiera kropki, nigdy odpowiedź na
pytanie „jak tam dojadę".

## Priorytet: poprawność przed szybkością

Rozsądna szybkość działania jest pożądana, ale nigdy kosztem poprawności.
Wolniejszy, ale dokładny wynik jest lepszy niż szybki, ale niedokładny.
