# Notatki do kontraktu mapy przepływów

Historia wdrożeń, audytów, napraw i otwarte pytania dla
[FLOW_MAP_CONTRACT.md](FLOW_MAP_CONTRACT.md). **Ten plik ma rosnąć** —
tu lądują dopiski przy każdej zmianie dotykającej kontrakt, żeby sam
kontrakt zostawał krótki. Numeracja punktów odpowiada numeracji w
kontrakcie.

## Testy

Plik: [`tests/test_flow_map_contract.py`](../tests/test_flow_map_contract.py)
(`python -m pytest tests/ -v`). Numeracja odpowiada punktom kontraktu.

- **1** — `test_whole_fan_shown_with_continuous_brightness_and_window_cutoff`
- **2** — `test_deadline_scales_with_best_route_duration`,
  `test_deadline_floor_protects_very_short_routes`,
  `test_deadline_cap_limits_very_long_routes`
- **3** — `test_single_course_splits_brightness_at_a_real_skipped_transfer`
  (sedno punktu), `test_no_flicker_without_a_real_alternative_to_skip`
  (bez konkurencyjnej przesiadki — zero sztucznego podziału),
  `test_exit_brightness_is_non_increasing_along_a_course` (własność, na
  której to się opiera),
  `test_no_relative_progress_gate_at_boarding_stop`
  (regresja 2026-08-12 — patrz log niżej)
- **4** — `test_dead_end_branch_never_appears`,
  `test_backtrack_reference_ignores_unrelated_faster_option_from_origin`
  (regresja 2026-08-12 — patrz log niżej)
- **6** — `test_shape_slice_uses_real_street_geometry_when_available`,
  `test_shape_slice_falls_back_to_stop_polyline_without_a_shape`
- **7** — `test_lines_sharing_a_corridor_each_carry_the_whole_corridor`
  (sedno punktu), `test_corridor_numbers_follow_one_global_order_everywhere`,
  `test_a_piece_never_claims_a_corridor_it_has_already_left`,
  `test_solo_line_never_gets_a_corridor_list`. Testy pokrywają to, co liczy
  backend (skład korytarza); samo rysowanie grupek numerów i przełączanie
  pod kursorem to frontend — wymagałoby testu w przeglądarce, więc jest
  sprawdzone czytaniem kodu i symulacją rozstawiania grupek na żywych
  danych (patrz log 2026-08-15, czwarte przejście).
- **8** — brak automatycznego testu (stała wizualna frontendu, nie wynik
  algorytmu). Zweryfikowane czytaniem kodu i wizualnie na mapie.
- **9** — `test_brightness_uses_full_range_regardless_of_window_width`,
  `test_previously_worst_option_brightens_when_a_new_worse_one_appears`,
  oraz pośrednio `test_single_course_splits_brightness_at_a_real_skipped_transfer`

## Otwarte pytania

**Punkt 4 — kotwiczenie końca gałęzi porównuje się do niewłaściwej
jasności?** Od wdrożenia punktu 3 segment ma własną jasność zależną od
pozycji, ale reguła kotwiczenia końca wciąż porównuje się do jasności
segmentu **od wsiadania** (najlepszej, jaką segment kiedykolwiek
osiąga), nie do jego **lokalnej** jasności w miejscu, które akurat
rozważamy jako koniec. W praktyce to raczej ostrożniejsze niż błędne
(ucina ogony wcześniej, nie później, niż lokalne porównanie by
pozwoliło) — ale jeśli kiedyś okaże się, że sensowne, ciemniejące ogony
znikają za wcześnie, to jest dokładnie to miejsce do przejrzenia.

## Punkt 5 — dlaczego usunięty

Był tu punkt „twarda gwarancja, że najszybsza trasa i tak się pojawi,
nawet gdy reszta zawiedzie” — świadomie usunięty z listy: celem jest
„wszystko ma działać poprawnie”, nie osobna siatka bezpieczeństwa na
wypadek, gdyby coś innego nie zadziałało. Istniejący w kodzie mechanizm
zabezpieczający (na ekstremalny przypadek, gdyby kotwiczenie przycięło
wszystko do zera) został w kodzie — usunięta jest tylko obietnica z tej
listy, nie samo zabezpieczenie.

## Log wdrożeń i audytów

**2026-08-11** — Punkty 1, 2, 4, 6: zgodne z kodem bez zmian (audyt).

**2026-08-11** — Punkt 3: wymagał realnej zmiany w `planner.py`
(`_refine_brightness`, `_finalize_segments`) — zaimplementowany i
przetestowany.

**2026-08-11** — Punkt 7: audyt przyjął zgodność za dobrą monetę
czytaniem kodu, ale w praktyce zawiódł tam, gdzie kilka linii nakłada
się na ten sam korytarz — Leaflet dawał `mouseover`/`click` tylko tej
narysowanej na wierzchu, reszta była pod spodem niedostępna. Naprawione
(`static/app.js`): segmenty wachlarza są teraz nieinteraktywne, a
kursor jest łapany globalnie na mapie i sprawdzany przeciw geometrii
wszystkich kursów naraz — najechanie w nałożone miejsce pokazuje i
podświetla je wszystkie, klik otwiera propozycję najbliższej z nich.

**2026-08-11** — Punkt 8: dopisany i zaimplementowany (`static/app.js`,
dolny próg opacity/grubości) po zgłoszeniu, że najbledszy koniec skali
bywał praktycznie niewidoczny.

**2026-08-11** — Punkt 9: dopisany i zaimplementowany (`planner.py`,
`_finalize_segments` — przeskalowanie na samym końcu, PO ustaleniu przez
`_select_and_anchor`, co się w ogóle pokazuje, żeby mianownik liczyć
względem najgorszej faktycznie pokazanej opcji, nie pełnej szerokości
okna) po zgłoszeniu, że szerokie okno czasowe spłaszczało różnice między
trasami w stronę samej góry skali.

**2026-08-11** — Ten plik wydzielony z kontraktu: kontrakt rósł przy
każdej naprawie (status, otwarte pytania, dopiski w punktach), co
sprzeciwiało się jego celowi jako krótkiej, stabilnej listy obietnic.

**2026-08-12** — Zgłoszenie użytkownika: "trasy przez Gaj znikają przy
zwiększeniu okna czasowego" (130%→135%), a osobno - ten sam objaw na
zupełnie innej parze punktów (Księże Wielkie → okolice Ostrowa Tumskiego),
gdzie mapa przy 190% wyglądała prawie tak samo ubogo jak przy 135%. Dwie
NIEZALEŻNE przyczyny w `_discover_segments`, obie tego samego rodzaju:
lokalna decyzja o pojedynczym kursie/przystanku porównywała się do
wartości policzonej względem AKTUALNEGO `deadline` (rośnie wraz z suwakiem
okna, niezależnie od tego, co akurat rozważamy), zamiast do czegoś
stałego - więc gdziekolwiek w mieście ujawniona przez szersze okno,
zupełnie niepowiązana, szybka trasa mogła zawyżyć punkt odniesienia i
skasować realnego kandydata na zupełnie innym, wolniejszym korytarzu.

1. `best_latest_seen` (reguła postępu, punkt 3) była zaczynana od
   `stop_latest` - najlepszego `latest` osiągalnego w miejscu wsiadania
   PRZEZ JAKĄKOLWIEK INNĄ przesiadkę, nie od czegoś "osiągniętego NA TYM
   KURSIE", jak głosił własny docstring funkcji. Naprawa: zaczyna się od
   `None` (brak punktu odniesienia na starcie kursu).
2. `origin_latest` (reguła cofnięcia przy wyborze miejsca wsiadania,
   punkt 4) była liczona z `latest` policzonego względem bieżącego
   `deadline`. Naprawa: osobne, ciasne wywołanie `_backward` względem
   `best_arr` (stałej, patrz też odniesienie do `best_arr` zamiast
   `deadline` w `_refine_brightness`) - tak liczone `origin_latest` może z
   oknem tylko złagodnieć (bo `stop_latest`, z którym się porównuje,
   nadal liczy się względem `deadline` i tylko rośnie), nigdy zaostrzeć.

Zweryfikowane na żywych danych GTFS (Księże Wielkie → Gaj, 17:00): przed
naprawą linie K/18/21/2 znikały między 130% a 135%; po naprawie zestaw linii
rośnie monotonicznie od 125% do 190% bez ani jednej utraty. Druga para
punktów (Księże Wielkie → Pl. Grunwaldzki) poprawiła się z 2 praktycznie
niezmiennych linii przy 135–190% do zdrowego, rosnącego wachlarza (16→31
linii). Dwa nowe testy (`test_progress_rule_ignores_unrelated_faster_option_at_boarding_stop`,
`test_backtrack_reference_ignores_unrelated_faster_option_from_origin`) -
każdy odtwarza jedną z dwóch przyczyn w minimalnym scenariuszu i był
ręcznie zweryfikowany jako czerwony na kodzie sprzed naprawy. Pozostał
osobny, mniejszy, nie w pełni naprawiony problem tej samej rodziny - patrz
drugie przejście niżej (ten sam dzień).

**2026-08-12, drugie przejście (ten sam dzień)** — użytkownik: suwak
"Tolerancja regresji" ma zostać na zawsze 0 (podnoszenie go łagodzi objaw,
ale "otwiera cały szereg problemów" gdy jest wyżej niż zero - potwierdzone
w pierwszym przejściu: 90s naprawiało jeden scenariusz testowy, a w
gęstszej siatce ZWIĘKSZAŁO liczbę regresji), więc trzeba naprawić to
inaczej, u źródła, zgodnie z kontraktem. Pełny biało-skrzynkowy audyt
`_discover_segments`/`_select_and_anchor` na żywych danych ujawnił DWA
kolejne, tym razem PRAWDZIWIE usunięte źródła:

1. **Reguła postępu w `_discover_segments` (dawny `best_latest_seen`/
   `prior_best`) usunięta CAŁKOWICIE**, nie tylko poprawiona. Powód:
   porównywała `latest[]` dwóch sąsiednich przystanków TEGO SAMEGO kursu -
   każdy rośnie monotonicznie z oknem, ale w RÓŻNYM tempie (zależnie od
   tego, jaka akurat alternatywa jest w danym miejscu "widoczna" w oknie),
   więc ich względna kolejność potrafiła się odwrócić przy samym tylko
   poszerzeniu suwaka (zweryfikowane na żywo: linia "124" Księże Wielkie →
   Grunwaldzki znikała między 145% a 150% mimo naprawy z pierwszego
   przejścia). Punkt 3 kontraktu (jasność spada po minięciu realnej,
   lepszej przesiadki) NIE WYMAGA tego filtra - `_refine_brightness` i tak
   liczy jasność KAŻDEGO zdążalnego wyjścia z osobna przez suffix-min
   najlepszej REALNIE znalezionej kontynuacji, a to jest dowodliwie
   monotoniczne i stabilne względem okna (już testowane -
   `test_exit_brightness_is_non_increasing_along_a_course`). Filtr "z
   góry" był więc nadmiarowy wobec już poprawnej maszynerii niżej w
   potoku - i to on był źródłem niestabilności, nie ona. Suwak
   "Tolerancja regresji" (`progress_tol_sec`) - stał się bez zastosowania
   po tej zmianie - USUNIĘTY w całości: stała `PROGRESS_TOL_SEC` i pochodne
   z `planner.py`, parametr z `plan_flow`/`_discover_segments`, pole `tol`
   z `/api/flow` w `routes.py`, kontrolka z `templates/index.html` i jej
   okablowanie w `static/app.js`.

2. **Kotwica końca w `_select_and_anchor` i `_extract_transfer_graph`
   naprawiona**: porównanie `other["q"] + Q_ANCHOR_TOL >= seg["q"]`
   zamienione na `... >= seg["exit_q"][j]` (jasność LOKALNA w konkretnym,
   rozważanym miejscu wyjścia, nie najlepsza jasność segmentu OD
   WSIADANIA). To dokładnie ten sam mechanizm co punkt 3 już stosuje przy
   RYSOWANIU (per-pozycja) - kotwica końca po prostu go nie używała.
   Matematycznie to zawsze rozluźnienie, nigdy zaostrzenie (`exit_q[j] <=
   seg["q"]` zawsze, z tej samej monotoniczności suffix-min) - poszerzenie
   okna, które gdzieś DALEJ na tym samym fizycznym kursie odkrywało nową,
   świetną okazję, podbijało `seg["q"]` (najlepsze OD WSIADANIA) i tym
   samym zawyżało próg dla zupełnie NIEZWIĄZANYCH, wcześniejszych wyjść na
   TYM SAMYM kursie - teraz każde wyjście trzyma się własnego progu.
   `_extract_transfer_graph` (mechanika Propozycje tras) naprawiona tym
   samym patchem dla spójności - z tych samych powodów, nie jako osobna
   praca nad listą propozycji (patrz [[feedback-skip-propozycje-tras]]):
   inaczej lista mogłaby milczeć o przesiadce, którą mapa już rysuje,
   łamiąc własną, udokumentowaną w `plan_flow` obietnicę.

Zweryfikowane na żywych danych GTFS w SZEŚCIU niezależnych scenariuszach
(różne pary przystanków, różne godziny) przez pełny zamiatający sweep
suwaka 110%→200% co 5 punktów procentowych, porównujący dokładnie to, co
`/api/flow` faktycznie zwraca: **zero przypadków linii znikającej z mapy w
całości w żadnym z sześciu scenariuszy** (wcześniej, po samym pierwszym
przejściu, linia "124" wciąż znikała w jednym z nich). Dokładne granice
POJEDYNCZYCH narysowanych kawałków (gdzie dokładnie tnie się jasność) wciąż
mogą się nieznacznie przesuwać między szerokościami okna - to oczekiwane,
kosmetyczne zachowanie punktu 3 (granica cięcia zależy od tego, co akurat
jest odkryte), nie regresja - zweryfikowano, że to WYŁĄCZNIE przesunięcie
granicy tego samego kawałka, nigdy zniknięcie samej linii.

Dwa testy z pierwszego przejścia zaktualizowane pod nowe sygnatury (bez
`progress_tol_sec`); `test_progress_rule_ignores_unrelated_faster_option_at_boarding_stop`
przemianowany na `test_no_relative_progress_gate_at_boarding_stop` i
zaktualizowany, żeby opisywać CAŁKOWITE usunięcie filtra, nie tylko
poprawkę jego zalążka. Nowej, minimalnej syntetycznej regresji dla punktu
2 (kotwica końca) świadomie NIE dopisano - konstrukcja scenariusza, w
którym `seg["q"]` i `exit_q[j]` naprawdę się rozjeżdżają w sposób
odtwarzalny małym `make_day`, okazała się nietrywialna (kilka nieudanych
prób z błędną arytmetyką czasów), a matematyczna własność, na której fix
się opiera (`exit_q` niemalejąco nierosnące - `test_exit_brightness_is_non_increasing_along_a_course`)
już jest pokryta testem, więc dodatkowa, kosztowna próba nie wydawała się
warta dalszego czasu wobec już solidnej weryfikacji na żywych danych.
14/14 testów zielonych.

Doc `ROUTING_ALGORITHM.md` (kroki 4, 5, 6, 7, 8) zaktualizowany w tym
samym przejściu - opisywał usuniętą regułę postępu i starą, globalną
wersję kotwicy końca jako aktualne zachowanie.

## 2026-08-15 — trzecie przejście: odnoga na pętlę (punkt 4)

Zgłoszenie: na mapie wystaje gałąź wjeżdżająca na pętlę końcową tylko po
to, żeby zaraz z niej wrócić ("co to za odnoga?"). Regresja po drugim
przejściu: usunięta wtedy w całości "reguła postępu" była — obok swojej
znanej wady — JEDYNYM miejscem, które takie wjazdy na pętlę ucinało.

Sedno pomyłki drugiego przejścia: reguła postępu miała słuszną INTENCJĘ
(ogon ma prowadzić w stronę celu), ale mierzyła ją przez `latest` — "jak
późno mogę stąd wyjechać". Ta wartość jest wysoka na węźle przesiadkowym z
powodu GĘSTOŚCI KURSÓW, nie bliskości celu, więc reguła kasowała pół mapy
razem z pętlami (pomiar na żywych danych: 42 zamiast 83 kawałków na
Dworzec Główny → Krzyki przy 200%, w tym oczywiste trasy wprost do celu).
Dlatego jej usunięcie wyglądało na poprawę, a strojenie jej tolerancji
(dawny suwak "Tolerancja regresji") nigdy nie mogło zadziałać: przesuwało
tylko próg szumu.

Poprawka: intencja wraca, miara się zmienia. Kotwica końca w
`_select_and_anchor` sprawdza teraz przez `_leads_onward`, czy kurs stojący
na końcu ogona prowadzi DALEJ, czy zawraca na przystanek, który dopiero co
minęliśmy. Kryterium jest czysto topologiczne — kolejność przystanków w
rozkładzie — więc nie zależy od zegara, szerokości okna ani częstotliwości.

Odrzucone po drodze (obie zmierzone, obie gorsze):
- **czysta miara bliskości celu** (Dijkstra po grafie czasów przejazdu,
  bez zegara). Miara sama w sobie jest czysta i tania (0,02 s), ale nie
  odróżnia pętli od uczciwego DOWOZU: dowóz w bok, żeby złapać szybszą
  linię, też oddala od celu, a ma prawo być na mapie. Złapał to
  `test_backtrack_reference_ignores_unrelated_faster_option_from_origin`.
- **łańcuch uziemienia do celu** (najmniejszy punkt stały zamiast
  największego, żeby dwa ogony nie podpierały się nawzajem). Wycina więcej
  wystających końcówek (21 zamiast 27 na sześciu relacjach), ale kosztem
  22 zamiast 5 znikających odcinków przy poszerzaniu okna — czyli oddaje z
  powrotem to, co naprawiły dwa poprzednie przejścia. Zostawione jako
  znany, świadomie niewykorzystany wariant.

Zmierzone na sześciu relacjach przy 200%, na wewnętrznym potoku (nie na
współrzędnych — te zmienia równoległa przebudowa wiązek):
- wystające końcówki (ogon, z którego nic narysowanego nie prowadzi
  dalej): **94 → 27** łącznie; w dwóch relacjach zero.
- odcinki znikające przy poszerzaniu okna 100%→200%: **5** łącznie na
  ~1000 (poprzednio 0) — wszystkie to same wystające kikuty, które reguła
  ucina dopiero przy szerszym oknie.

Reszta wystających końcówek została dociągnięta zaraz potem — patrz
następny wpis.

Usunięty suwak "Ile propozycji tras szukać" (na prośbę użytkownika) —
backend nadal używa swojego `DEFAULT_JOURNEY_LIMIT`.

18/18 testów zielonych; nowy `test_tail_onto_a_terminus_loop_is_not_anchored_by_the_way_back`
zweryfikowany jako faktycznie czerwony bez poprawki.

## 2026-08-15 — czwarte przejście: zero kikutów (punkt 4)

Zgłoszenie: "nie chcę NIGDY żadnych kikutów". Zastrzeżenie z poprzedniego
wpisu ("kursy jadą dalej, ale dalsza część nie mieści się w oknie") okazało
się PRZY POMIARZE fałszywym tropem — z oknem nie miało to nic wspólnego.
Wariant "kotwica musi mieć w oknie wyjście za tym przystankiem" zmierzony
jako pierwszy: 27 → 26 kikutów, za to 5 → 13 znikających odcinków. Dopiero
wypisanie trzech konkretnych przypadków z nazwami przystanków pokazało
prawdziwe przyczyny — dwie, obie topologiczne:

1. **Zawrócenie dalej niż o jeden przystanek.** `_leads_onward` porównywał
   kontynuację tylko z POPRZEDNIM przystankiem. Tramwaj 1 dojeżdżał więc do
   pętli Kamieńskiego "zakotwiczony" o piętnastkę, która zaraz wraca przez
   Bałtycką i Kleczkowską — czyli dokładnie tam, skąd przyjechaliśmy, tylko
   nie na ostatnim kroku. Analogicznie tramwaj 7 zaczynał przystanek przed
   Dworcem Głównym i jechał OD dworca na Renomę i Operę, bo z Opery
   ósemka wozi z powrotem przez Arkady na dworzec. Poprawka: `behind` to
   cała przejechana droga kursu, nie jeden przystanek. **27 → 17 kikutów,
   znikające bez zmian (5).**
2. **Dwa ogony podpierające się nawzajem.** Po powyższym KAŻDY pozostały
   kikut miał ten sam kształt: kotwica sama kończyła się w tym samym
   miejscu (tramwaj 1 i tramwaj 7 wskazujące na siebie na Bałtyckiej).
   Poprawka: kontynuacja musi być narysowana ZA tym przystankiem, nie
   tylko jechać dalej w rozkładzie. **17 → 0 kikutów, ale 32 znikające** —
   dokładnie ten wariant, który w trzecim przejściu odrzuciłem jako
   "oddaje z powrotem monotoniczność".

Rozwiązanie tego konfliktu: usunięty **filtr jasności z kotwicy końca**
(`other["q"] + Q_ANCHOR_TOL >= exit_q[j]`). To była jedyna składowa kotwicy
zależna od szerokości okna — jasność jest skalowana względem najgorszej
opcji, która AKURAT mieści się w oknie (punkt 9), więc obie strony tego
porównania ruszają się przy każdym ruchu suwaka i potrafią rozjechać się w
przeciwne strony. Przy luźnej kotwicy taki przeskok kosztował jedną
przesiadkę; przy ostrej — szedł kaskadą przez cały łańcuch. **Bez niego: 0
kikutów i 0 znikających odcinków, przy WIĘKSZEJ liczbie narysowanych
kawałków (1020 zamiast 1014).** Ten sam filtr zniknął z
`_extract_transfer_graph`, żeby lista propozycji nie zaczęła milczeć o
przesiadkach, które mapa rysuje.

Warto zapamiętać kolejność, w jakiej to wyszło: dwa pierwsze pomysły
(oba "okienne") były zmierzone i oba nietrafione, a diagnoza przyszła
dopiero z wypisania konkretnych przypadków z nazwami przystanków. To samo
zdarzyło się przejście wcześniej. Wniosek na przyszłość: przy kikutach
najpierw wypisz trzy przykłady z nazwami, potem stawiaj hipotezę.

Zmierzone na 14 relacjach × 14 szerokości okna (100%–300%): **0 kikutów
przy KAŻDEJ szerokości okna, 0 znikających odcinków**; 3664 kawałki przy
200%. Na żywym serwerze Biskupin → Dworzec Główny: 8 / 45 / 262 segmenty
przy 110% / 150% / 200%.

21/21 testów zielonych. Dwa nowe testy, każdy zweryfikowany jako czerwony
po wyłączeniu dokładnie tej jednej reguły, której pilnuje:
`test_tail_is_not_anchored_by_a_course_turning_back_further_up_the_line`
oraz `test_two_tails_propping_each_other_up_are_both_cut_back`.

## 2026-08-15 — punkt 7: cztery podejścia, trzy odrzucone

Punkt 7 („zawsze wiadomo, co tam jedzie") kosztował cztery podejścia i trzy
odrzucenia użytkownika z rzędu. Warto zapisać, co dokładnie w każdym z nich
było nie tak, bo pomyłki nie były tego samego rodzaju.

**Podejście 1 — gęstsze plakietki.** Numery linii dostawały zbiorczą
plakietkę zbieraną po ODLEGŁOŚCI NA EKRANIE. Odrzucone: przy widoku całego
miasta kilka pikseli to ponad sto metrów, więc plakietka doliczała linie z
sąsiednich ulic i wypisywała „13 linii" tam, gdzie realnie jadą dwie
(zmierzone na żywych danych: najgęstszy odcinek Wrocławia ma 8 linii —
Galeria Dominikańska → Pl. Nowy Targ — a 138 z 245 odcinków ma dokładnie
jedną). Do tego plakietki „N linii" nie dało się rozwinąć, bo marker był
`interactive: false`.

**Podejście 2 — przesunięcie w metrach.** Linie wspólnego korytarza
rozsuwane o 3,5 m wprost we współrzędnych. Odrzucone: 3,5 m to przy widoku
miasta ~0,2 piksela, czyli praca niewidoczna poza maksymalnym przybliżeniem,
a przy nim z kolei przesadnie szeroka. Dodatkowo łamało to punkt 6
(geometria po realnych torach).

**Podejście 3 — pasma w pikselach (LOOM).** Rozsuwanie liczone w pikselach
ekranu przez front, z globalnie stabilną kolejnością pasm (wzorowane na
LOOM Bast/Brosi i na `line-offset` w MapLibre GL). Technicznie działało —
zero kolizji pasm i zero przecięć na żywych danych — i mimo to zostało
odrzucone jako dalej nieczytelne. Wniosek, który z tego został: sam fakt,
że rozwiązanie jest poprawne i ma pokrycie w literaturze, nie znaczy, że
rozwiązuje problem, który użytkownik naprawdę widzi.

**Podejście 4 — to, co zostało (kierunek od użytkownika).** Rozsuwanie
usunięte w całości; geometria wraca do prawdziwej i linie wspólnego
korytarza znów leżą jedna na drugiej — bo to nigdy nie było źródłem
nieczytelności. Nieczytelne były NUMERY: stały w losowych odstępach (w
ułamkach długości kawałka, a kawałki mają skrajnie różne długości) i
nachodziły na siebie. Trzy zmiany:

- **kondensacja** — wspólny korytarz dostaje JEDNĄ grupkę ze wszystkimi
  swoimi numerami obok siebie (skład z rozkładu, `planner._corridor_lines`,
  pole `corridor`), a nie osobny numer na linię;
- **równe odstępy** — kolejne grupki co stałą liczbę PIKSELÓW wzdłuż
  korytarza, nie w ułamkach kawałka;
- **zero nachodzenia** — kolizje liczone prostokątem o realnej szerokości
  grupki (grupka pięciu numerów jest kilka razy szersza niż jeden numer),
  z pierwszeństwem dla najjaśniejszych; grupki powyżej pięciu numerów łamią
  się na wiersze.

Pod kursorem podświetla się WYŁĄCZNIE jedna linia, a podpowiedź podaje jej
numer wprost. Domyślnie najjaśniejsza z korytarza. Numery w grupce są też
klikalne — najechanie wskazuje dokładnie tę linię, kliknięcie otwiera jej
propozycję. (Przełączanie prawym przyciskiem myszy istniało tu do
2026-08-15 — patrz wpis niżej.)

Zmierzone (symulacja rozstawiania grupek: ta sama projekcja co w Leaflet,
kadr 1200×800, pięć relacji, w tym Leśnica → Sępolno = 1105 kawałków):
- **par nachodzących na siebie grupek: 0** w każdej relacji i na każdym
  powiększeniu — to jest ta własność, o którą poszło;
- numery zajmują 1–9% powierzchni kadru (najgorszy przypadek: widok całego
  miasta);
- 9–179 RÓŻNYCH składów korytarza na relację, przy 45–1105 kawałkach —
  czyli kondensacja zmniejsza liczbę rzeczy do opisania kilkukrotnie.

Odrzucone po drodze (zmierzone): **próg jasności dla numerów**
(`LABEL_MIN_W`). Wyglądał na naturalny sposób na przerzedzenie mapy, ale
kolejność zajmowania miejsca, kolizje i sufit i tak przycinają gęstość —
próg wycinał więc numery także tam, gdzie było pusto: w przybliżonym widoku
rzadkiej okolicy schodził z 57% opisanych korytarzy na 14%, nie
oszczędzając ani procenta ekranu. Usunięty.

19/19 testów zielonych.

## 2026-08-15 — widoczność dołu skali, całe linie pod kursorem, koniec prawego przycisku

Cztery rzeczy naraz, wszystkie po stronie rysowania (planner nietknięty,
21/21 testów zielonych).

**Dół skali był niewidoczny — i to on udawał „linie donikąd" (punkt 8).**
Najbledszy kawałek (w=0) miał `opacity 0.35` i `1,5 px`. Na tle kafelków OSM
wychodziła z tego szara nitka nie do odróżnienia od zwykłej ulicy. Skutek
sięgał dalej niż estetyka: **zakotwiczone** ogony wyglądały jak urwane w
powietrzu, bo widać było jasny koniec, a nie widać było tego, co go
przedłuża. Nazwany przypadek (KSIĘŻE MAŁE → Wojszyce, 21:32, okno 200%):
tramwaj 15 kończy się na PARKU POŁUDNIOWYM i jest tam zakotwiczony
przesiadką w autobus 612, który jedzie stamtąd dalej — przez Bielany, Ślęzę
i Wysoką — do samych Wojszyc, z przyjazdem 22:38, czyli dokładnie na
granicy okna. Sprawdzone w kodzie (`_select_and_anchor` → `_leads_onward` +
`_joins`), nie zgadywane: kotwica jest, kontynuacja jest narysowana, tylko
że `exit_q` schodzi po drodze do 0.00 i po przeskalowaniu wychodzi z tego
w=0 — czyli ta właśnie, niewidoczna nitka. Punkt 4 nie był złamany; złamany
był punkt 8, a wyglądało to jak złamany punkt 4.

Wartość progu wybrał potem użytkownik suwakami, na realnej mapie — patrz
wpis niżej (te `0.35 / 1.5 px` były po prostu za mało widoczne przy
ówczesnej, grubszej reszcie skali; sam próg nie ma w kontrakcie żadnej
liczby, punkt 8 mówi tylko „ma być widoczne bez najeżdżania myszką").

**Suwaki wyglądu (TYMCZASOWE).** Dziewięć liczb, którymi rysuje się mapę
(dół i góra skali krycia i grubości, próg białej otoczki, przygaszenie pod
wybraną trasą, odstęp/wielkość/krycie grupek numerów), siedzi w
`LOOK_DEFAULTS` w `app.js` i jednocześnie na suwakach w panelu
deweloperskim, sekcja „Wygląd mapy". Suwaki nie ruszają serwera —
przemalowują mapę z ostatniej odpowiedzi (`lastFlow`), więc dobiera się je
na żywo, na realnej mapie, zamiast zgadywać w kodzie. Po ustaleniu wartości:
wpisać je w `LOOK_DEFAULTS` i skasować sekcję (znaczniki TYMCZASOWE są w
`app.js`, `index.html` i `style.css`).

**Pod kursorem podświetla się CAŁA LINIA, nie kawałek.** Jeden fizyczny kurs
bywa pocięty na kilkanaście kawałków (jasność — punkt 3, skład korytarza —
punkt 7), więc rozjaśnianie jednego z nich odpowiadało na pytanie „gdzie
dokładnie stoi kursor" zamiast na to, o które chodzi: „dokąd stąd jedzie ta
linia". Teraz podświetlenie to OSOBNA warstwa dokładana na wierzch
wszystkiego (ciemna otoczka + pełne krycie), a nie przemalowanie warstw w
miejscu — inaczej „na wierzchu" zależy od kolejności rysowania i jasna linia
obok potrafi przykryć tę wskazaną.

**Prawy przycisk myszy — usunięty.** Przechodził na następną linię
korytarza. Wybieranie linii jest już w grupce numerów (najechanie na numer
wskazuje dokładnie tę linię), więc przełączanie było drugą drogą do tego
samego, tyle że przez zgadywanie, ile razy kliknąć. Menu kontekstowe
przeglądarki wraca do normy.

**Przy okazji: dymek nad korytarzem nigdy nie działał.** `L.tooltip(opts)
.addTo(map)` bez wcześniejszego `setLatLng` rzuca wyjątkiem w środku
`addTo` (Leaflet od razu liczy pozycję dymka), więc `flowTooltip` nigdy nie
powstawał, a każdy ruch myszy nad korytarzem próbował go stworzyć od nowa i
wysypywał się w tym samym miejscu. Błąd jest starszy niż punkt 7 — siedział
w kodzie od czasów, gdy dymek pokazywał listę linii. Naprawione:
`setLatLng` przed `addTo`. Bez tego cała podpowiedź „na czym stoisz" (numer
linii, skład korytarza, „kliknij, aby otworzyć trasę") była martwym kodem.

## 2026-08-15, drugie przejście: dobrane wartości wyglądu + skąd bierze się gęstość mapy

**Wartości wyglądu dobrane przez użytkownika na żywo** i wpisane jako
`LOOK_DEFAULTS`: krycie 0.1 → 1, grubość 0.5 → 3 px, otoczka od 0.45,
przygaszenie 0.22, grupki numerów co 200 px, numery 0.8×, krycie numerów 1.
Sekcja suwaków ZOSTAJE w kodzie, ale jest schowana — przełącznik
`LOOK_TUNING` w `app.js` (`false`) chowa ją i każe ignorować zapamiętane w
`localStorage` ustawienia, żeby czyjeś stare wartości nie przykryły
