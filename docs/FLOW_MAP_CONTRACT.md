# Kontrakt mapy przepływów

Lista gwarancji zachowania **wyłącznie rysowania mapy przepływów**
(segmenty, jasność, kształt sieci) — nie dotyczy listy „Propozycje tras”
obok mapy, to świadomie osobna sprawa. Uzgodniona z użytkownikiem
2026-08-11. To jest poziom „co mapa obiecuje”, czytelny bez znajomości
kodu; jak dokładnie to jest policzone — patrz
[ROUTING_ALGORITHM.md](ROUTING_ALGORITHM.md) (dokumentacja dla
utrzymujących kod). Ogólny opis projektu — [PROJECT.md](PROJECT.md).

Każdy punkt niżej ma odpowiadający mu test w
[`tests/test_flow_map_contract.py`](../tests/test_flow_map_contract.py) —
uruchom `python -m pytest tests/ -v` (patrz README), żeby zobaczyć je
jeden po drugim.

**Ten plik ma zostać krótki i stabilny.** Historia wdrożeń, audytów i
napraw, otwarte pytania warte pilnowania — to wszystko w
[FLOW_MAP_NOTES.md](FLOW_MAP_NOTES.md), nie tutaj. Naprawienie buga albo
zaimplementowanie punktu nie jest samo w sobie powodem, żeby edytować tę
listę — dopisuj/zmieniaj punkt tylko wtedy, gdy zmienia się sama obietnica
(nowa gwarancja albo realna zmiana sensu istniejącej).

## 1. Cały wachlarz, nie jedna trasa

Mapa pokazuje wszystkie sensowne dojazdy naraz, nie tylko najszybszy.
Jasność linii to ciągła miara jakości (0–1), nie binarne pokaż/ukryj.

Test: `test_whole_fan_shown_with_continuous_brightness_and_window_cutoff`.

## 2. Sensowność względem najlepszej trasy

To, co się liczy jako „sensowne”, jest mierzone tym, jak blisko dana opcja
dociera do celu w porównaniu z najlepszym możliwym (najszybszą trasą).
Okno czasowe to arbitralny, regulowalny próg tego porównania.

Testy: `test_deadline_scales_with_best_route_duration`,
`test_deadline_floor_protects_very_short_routes`,
`test_deadline_cap_limits_very_long_routes`.

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

Testy: `test_single_course_splits_brightness_at_a_real_skipped_transfer`
(dokładnie ten scenariusz), `test_no_flicker_without_a_real_alternative_to_skip`
(bez konkurencyjnej przesiadki — jeden, stały kawałek, zero sztucznego
podziału), `test_exit_brightness_is_non_increasing_along_a_course`
(własność, na której to się opiera: to, co jeszcze osiągalne z danego
miejsca kursu, może się tylko pogarszać albo zostać bez zmian w miarę
jazdy, nigdy poprawić).

## 4. Brak wiszących w powietrzu gałęzi

Cała mapa ma wyglądać jak kształt, który zaczyna się wąsko w punkcie
startowym, rozgałęzia się i poszerza, po czym zwęża z powrotem do punktu
docelowego. Gałąź zaczynająca się w miejscu **nieosiągalnym niczym już
narysowanym**, albo kończąca się w miejscu **niezwiązanym z dotarciem do
celu**, nie ma prawa się pojawić na mapie.

Kryterium jest czysto **fizyczna osiągalność** — czy da się tam realnie,
w czasie, dotrzeć czymś, co mapa już rysuje — nie to, jak jasna jest ta
rzecz, przez którą się dociera. Dlatego początek gałęzi kotwiczy się o
DOWOLNĄ zdążalną przesiadkę z już narysowanego segmentu, choćby bardzo
blada: bladość feedera nie oznacza, że dalsza, jasna część jest
nieosiągalna, tylko że sam dojazd do niej nie jest najlepszą częścią
podróży — a to normalne i zgodne z zasadą punktu 3. Koniec gałęzi ma
dodatkowo (poza samą osiągalnością) wymóg porównywalnej jasności
kontynuacji — to nie jest wymóg tego punktu kontraktu, tylko osobna,
świadoma decyzja porządkowa: nie ciągnąć jasnego korytarza ogonem w bladą,
nieistotną niszę.

Test: `test_dead_end_branch_never_appears`.

## 6. Geometria po realnych ulicach i torach

Ścieżka segmentu to prawdziwa geometria z rozkładu (`shapes.txt`), nie
linia prosta między przystankami. Gdy geometria nie jest dostępna dla
danego kursu, spada to na łamaną po współrzędnych przystanków.

Testy: `test_shape_slice_uses_real_street_geometry_when_available`,
`test_shape_slice_falls_back_to_stop_polyline_without_a_shape`.

## 7. Zawsze wiadomo, co tam jedzie

Zawsze da się jednoznacznie rozpoznać, jaka linia (numer) jedzie na danym
odcinku mapy — nawet gdy kilka linii nakłada się na ten sam korytarz —
żeby przełożyć to na realny pojazd, w który trzeba wsiąść. Strzałki
kierunkowe nie są wymagane (kierunek wynika ze start/celu). Sposób
realizacji jest dowolny; liczy się efekt.

To dotyczy też przypadku, gdy kilka linii jedzie dokładnie tym samym
korytarzem i na mapie leżą jedna na drugiej: najechanie w to miejsce ma
pokazać wszystkie z nich, nie tylko tę narysowaną na wierzchu wiązki.

Bez automatycznego testu — to zachowanie frontendu, wymagałoby testu w
przeglądarce. Zweryfikowane czytaniem kodu.

## 8. Minimalna jasność nigdy nie spada do niewidoczności

Najbledszy koniec skali jasności (punkt 1, q=0) wciąż ma być fizycznie
widoczny na mapie — nie może wyglądać jak przypadkowa, niedokończona
kreska donikąd. Dolny próg opacity i grubości linii (`static/app.js`,
rysowanie wachlarza) jest ustawiony na tyle wysoko, żeby nawet najbledszy
kawałek dało się dostrzec bez najeżdżania na niego myszką.

Bez automatycznego testu — to stała wizualna frontendu, nie wynik
algorytmu (`q` samo w sobie poprawnie schodzi do 0, patrz punkt 1);
weryfikacja przez odczyt kodu i wizualnie na mapie.

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

Testy: `test_brightness_uses_full_range_regardless_of_window_width`,
`test_previously_worst_option_brightens_when_a_new_worse_one_appears`,
oraz pośrednio `test_single_course_splits_brightness_at_a_real_skipped_transfer`
(punkt 3).

## Priorytet: poprawność przed szybkością

Rozsądna szybkość działania jest pożądana, ale nigdy kosztem poprawności.
Wolniejszy, ale dokładny wynik jest lepszy niż szybki, ale niedokładny.
