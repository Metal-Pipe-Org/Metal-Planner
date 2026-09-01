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
Okno czasowe to arbitralny, regulowalny próg tego porównania.

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
w czasie, dotrzeć czymś, co mapa już rysuje — nie to, jak jasna jest ta
rzecz, przez którą się dociera. Gałąź kotwiczy więc DOWOLNA zdążalna
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

Suwak okna czasowego reguluje, CO jest w ogóle pokazane (punkt 2), ale
skala jasności nie jest liczona względem pełnej, teoretycznej szerokości
okna, tylko względem najgorszej opcji, która FAKTYCZNIE się w nim
mieści. Najlepsza trasa zawsze świeci pełnym blaskiem (q=1), a najgorsza
opcja, która akurat się zmieściła, zawsze ląduje na dole skali (patrz
punkt 8 w sprawie tego, że dół skali nadal jest widoczny na mapie) —
niezależnie od tego, jak szeroko otwarty jest suwak. Skutek: poszerzenie
suwaka, które nie wprowadza żadnej nowej, gorszej opcji, nie zmienia
jasności tego, co już jest na mapie; ale jeśli wprowadza nową, gorszą
opcję, to poprzednio-najgorsze trasy mogą się realnie rozjaśnić — dół
skali przesunął się niżej. To drugie nie jest błędem, to ta sama zasada
działająca w drugą stronę.

## 10. Mapa mówi, ile to trwa i o której się tam będzie

Mapa odpowiada nie tylko na „jak dojechać”, ale też na „ile to trwa” i
„o której”. Trzema warstwami, od najogólniejszej:

**Bez ruszania myszą** widać czas całej podróży: najszybszy możliwy dojazd
i najpóźniejszy, jaki mapa jeszcze rysuje.

**Pod kursorem, dla punktu pod kursorem** — nie dla całej linii i nie dla
jakiegoś jej kawałka — widać dwie godziny: o której tym pojazdem jest się
dokładnie tutaj, i o której jest się w celu, jadąc dalej najszybszą możliwą
kontynuacją. Do tego ile to jeszcze zajmie.

**Skąd te godziny.** Z rozkładu tego samego kursu, z którego narysowano ten
odcinek. Między dwoma sąsiednimi przystankami mapa **wolno** interpolować —
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

**Godzina, od której liczymy.** Najwcześniejsza, o której według mapy można tu
być — nie „teraz" i nie godzina z formularza. Na osi doby rozkładowej, nie
zegarowej: przesiadka o 24:40 należy do rozkładu dnia poprzedniego.

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

**Czego to nie obiecuje.** Że każda stacja ma przesiadkę do miasta. Dopóki
łączy je wyłącznie nazwa, stykają się rzadko — i to jest znany brak, nie
usterka.

## 13. Zawsze jakaś trasa, choćby za godzinę

**„Nie znaleziono połączenia" to nie odpowiedź na pytanie „jak tam dojadę".**
Jeśli o podaną godzinę nic nie jedzie, mapa pokazuje najbliższą trasę, jaka
jedzie — choćby za godzinę, choćby dopiero rano następnego dnia — i mówi
wprost, o której ona wyrusza. Pusta mapa z komunikatem należy się wyłącznie
relacji, której nie da się przejechać w ogóle.

**Czekanie jest widoczne, nie schowane.** Trasa zaczynająca się później niż
pytanie ma to napisane przy sobie. Mapa nigdy nie udaje, że coś jedzie teraz.

**Okno czasowe liczy się od wyjazdu, nie od pytania.** Wachlarz wariantów
wokół takiej trasy jest tak samo szeroki jak wokół każdej innej — godzina
czekania nie zawęża wyboru, bo nie jest częścią podróży.

## Priorytet: poprawność przed szybkością

Rozsądna szybkość działania jest pożądana, ale nigdy kosztem poprawności.
Wolniejszy, ale dokładny wynik jest lepszy niż szybki, ale niedokładny.
