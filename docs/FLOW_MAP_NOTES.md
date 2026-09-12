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
- **7** — backend: `test_lines_sharing_a_corridor_each_carry_the_whole_corridor`
  (sedno punktu), `test_corridor_numbers_follow_one_global_order_everywhere`,
  `test_a_piece_never_claims_a_corridor_it_has_already_left`,
  `test_solo_line_never_gets_a_corridor_list`. Front (od 2026-08-27, przez
  emulator — patrz niżej): `test_number_groups_never_overlap`,
  `test_hovering_a_number_names_exactly_that_line`,
  `test_every_group_stands_on_a_drawn_corridor`,
  `test_the_map_labels_its_corridors_at_all`
- **8** — front, przez emulator (od 2026-08-27):
  `test_brightness_scale_never_reaches_invisibility`,
  `test_nothing_actually_drawn_is_invisible`. Wcześniej: brak testu, samo
  czytanie kodu i oglądanie mapy.
- **10** — front, przez emulator (od 2026-08-27):
  `test_time_at_a_stop_comes_straight_from_the_schedule`,
  `test_stop_anchors_run_along_the_line_in_order`,
  `test_time_never_goes_backwards_along_a_piece`,
  `test_the_time_dot_sits_on_the_line`
- **9** — `test_brightness_uses_full_range_regardless_of_window_width`,
  `test_previously_worst_option_brightens_when_a_new_worse_one_appears`,
  oraz pośrednio `test_single_course_splits_brightness_at_a_real_skipped_transfer`
- **11** — backend (od 2026-08-30):
  `test_a_node_says_which_of_three_things_happens_with_each_line` (sedno
  punktu), `test_only_boardable_lines_carry_a_deadline_and_only_arrivals_a_time`,
  `test_the_node_hour_is_the_earliest_you_can_be_here`,
  `test_a_place_where_you_only_get_off_is_still_not_a_transfer`.
  Front, przez emulator: `test_each_row_says_what_happens_here_with_that_line`,
  `test_lines_that_only_bring_you_here_get_their_own_row`,
  `test_an_arrival_never_masquerades_as_a_departure`,
  `test_an_arrival_and_a_departure_are_not_one_cadence`,
  `test_the_board_mixes_arrivals_into_the_departures_by_time`.
  Wcześniej (od 2026-08-29) tylko odsiew oferty: `..._only_what_the_map_offers_here`,
  `..._departures_that_cannot_make_it_are_dropped`, `..._past_the_map_horizon_are_dropped`
- **15** — `tests/test_trafikary_na_mapie.py` w całości. Front, przez emulator:
  `test_a_car_is_a_place_on_the_map_not_a_ride`,
  `test_the_car_says_what_is_there_to_earn`
- **16** — `tests/test_rowery_na_mapie.py` w całości; sedno punktu to
  `test_przystanek_bez_narysowanego_odjazdu_niczego_nie_otwiera`
  i `test_przejazd_nie_musi_byc_szybszy_niz_tramwaj`. Front, przez emulator:
  `test_a_bike_shows_its_rides_only_under_the_cursor`,
  `test_a_bike_on_another_day_does_not_pretend_to_know`

## Otwarte pytania

**Przesiadka kotwiczy się „jakimś" kursem linii, a uzasadnia ją konkretny
(2026-08-15, DO ZROBIENIA).** Co jest zagwarantowane i zmierzone: każdy
narysowany kawałek leży na realnej trasie start → cel dowożącej PRZED
deadline'em — wsiadanie sprawdza skan w przód (`earliest`), wysiadanie skan
wstecz (`latest`), zero naruszeń na 101/101 i 268/268 kawałków (patrz log
niżej). Czego NIE ma: gwarancji, że dowolne złożenie narysowanych kawałków
„na oko" też się w oknie zmieści. Kotwica końca pyta przez `_joins`, czy
JAKIŚ kurs tego wzorca odjeżdża stąd w ciągu `WAIT_CAP_SEC` (20 min) — a
kawałek, do którego się przesiadamy, jest narysowany na podstawie
KONKRETNEGO kursu i to jego godziny przeszły test okna. Złapanie
późniejszego odjazdu tej samej linii może więc wyprowadzić poza deadline.

Ta sama rzecz mierzona miarą listy propozycji (`_can_board` — ten jeden
kurs): 20 ze 101 kawałków przy 125%. Do rozstrzygnięcia: czy przesiadka na
mapie ma być weryfikowana FAKTYCZNYM najbliższym odjazdem (i czy wtedy
ponownie sprawdzać deadline dla tak złożonego łańcucha), czy „jakiś kurs w
ciągu 20 min" zostaje świadomym uproszczeniem mapy linii. Uwaga na koszt:
zaostrzenie do `_can_board` obcina mapę do ~20% obecnej zawartości, co samo
w sobie łamie punkt 1 (mapa ma pokazywać wachlarz, nie jedną trasę) —
właściwym kierunkiem jest raczej sprawdzanie deadline'u dla realnego
najbliższego odjazdu niż podmiana miary kotwiczenia.

Poprzednie wpisy (niestabilność reguły postępu; kotwiczenie końca
porównujące się do niewłaściwej jasności) okazały się tym samym zjawiskiem
widzianym z dwóch stron i zostały naprawione u źródła 2026-08-12 - patrz
log niżej.

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
domyślnych bez żadnej kontrolki, którą dałoby się to cofnąć.

**Skąd bierze się „ogromna ilość tras" (pytanie użytkownika, zmierzone).**
Relacja KSIĘŻE MAŁE → Wojszyce, okno 125%, `/api/flow`:

| kiedy | najszybsza trasa | okno | kawałki | linie |
|---|---|---|---|---|
| 08:00 | 21 min | 10 min (dobija podłoga) | 2 | 2 |
| 17:00 | 32 min | 10 min (dobija podłoga) | 2 | 2 |
| 22:37 | **63 min** | **15 min** | 298 | 44 |

Pierwsza przyczyna jest dokładnie taka, jak zgadywał użytkownik: **w nocy
najszybsza trasa jest trzykrotnie dłuższa** (63 min vs 21 min rano), więc
ten sam procent kupuje półtora raza szersze okno w minutach — a przy
wszystkim jednakowo wolnym mieści się w nim mnóstwo wariantów.

Druga przyczyna to **nieskomitowane zmiany w `planner.py`** (z sesji
2026-08-12 i 2026-08-15, nie z tego przejścia). Ta sama relacja, 22:37:

| kod | 125% | 200% |
|---|---|---|
| HEAD (3d1494a) | 37 kawałków / 30 linii | 107 / 42 |
| drzewo robocze | 298 / 44 | 1289 / 82 |

Atrybucja przez wyłączanie pojedynczych zmian:
- **usunięta „reguła postępu"** w `_discover_segments` to główna przyczyna
  wzrostu LICZBY LINII: HEAD z rozluźnionym `progress_tol_sec` (600 s)
  daje dokładnie 44 linie przy 125% — czyli tyle, co dzisiejszy kod.
  To nie jest regresja, tylko zapłacona cena: ta reguła była źródłem
  „trasy znikają przy poszerzeniu okna" (patrz log 2026-08-12);
- `origin_latest` liczone względem `best_arr` — drobny wkład (przy 200%:
  1289 → 1118 kawałków, 82 → 75 linii; przy 125% zero różnicy);
- filtr jasności w kotwicy końca — **żadnego** wkładu (298 → 298 przy 125%,
  1289 → 1281 przy 200%), więc jego usunięcie nie „rozlało" mapy;
- reszta wzrostu KAWAŁKÓW (37 → 298) to w większości cięcie po składzie
  korytarza z punktu 7: jeden kurs wychodzi teraz jako kilkanaście
  kawałków. Liczba kawałków NIE jest miarą liczby tras — do porównań
  gęstości używać liczby linii.

**Czy te linie to błąd? (weryfikacja punktu 4 na żywych danych).** Pytanie
użytkownika po powyższym: skoro przy 125% wychodzą 44 linie, to czy mapa
czegoś nie rysuje na darmo. Sprawdzone wprost: dla każdego narysowanego
odcinka policzona osiągalność celu po krawędziach „wyjście → zdążalny,
narysowany dalej kurs" (`_joins` na narysowanych częściach — dokładnie ta
miara, którą kotwiczy `_select_and_anchor`).

| relacja / okno | narysowane | prowadzi do celu | wisi w powietrzu |
|---|---|---|---|
| 22:37, 125% | 101 odc. / 44 linie | **101 / 44** | 0 |
| 22:37, 200% | 268 / 82 | **268 / 82** | 0 |
| 17:00, 200% | 90 / 41 | **90 / 41** | 0 |
| 08:00, 200% | 15 / 12 | **15 / 12** | 0 |

Punkt 4 trzyma. Gęstość nocnej mapy to skutek szerokiego okna w minutach
(63-minutowa najszybsza trasa), nie rysowania odcinków donikąd.

Ta sama rachuba miarą LISTY propozycji (`_can_board` — ten jeden, konkretny
kurs zamiast „jakikolwiek kurs tego wzorca w ciągu 20 min") daje 20 ze 101
odcinków przy 125%. To nie sprzeczność, tylko zmierzona różnica między
pytaniami, na które odpowiadają mapa i lista — warto ją mieć zapisaną,
zanim ktoś uzna jedną z tych liczb za błąd drugiej.

**Twardsze sprawdzenie tego samego, wprost na warunkach okna** (bo pierwsza
wersja tej notatki twierdziła błędnie, że łańcuch przesiadek może wyjść poza
deadline): dla każdego zatrzymanego segmentu sprawdzone osobno, czy
wsiadanie w jego kurs jest osiągalne skanem w przód (`earliest + bufor <=
odjazd`) i czy narysowany koniec spełnia `arr_t <= latest[przystanek]`,
czyli czy stamtąd wciąż da się zdążyć do celu przed deadline'em. Wynik:
**0 naruszeń na 101 segmentów (125%) i 0 na 268 (200%)**. Najpóźniejszy
narysowany przyjazd to 23:46 przy deadline 23:55 oraz 00:08 przy 00:10.
Innymi słowy każdy narysowany kawałek jest przejeżdżany przez kompletną,
realną trasę mieszczącą się w oknie — to gwarancja z konstrukcji (skan w
przód + skan wstecz), nie tylko obserwacja.

Co z tego NIE wynika i zostało jako otwarte pytanie (patrz „Otwarte
pytania" na górze pliku): że dowolne złożenie narysowanych kawałków też się
w oknie zmieści — kotwica przesiadki pyta o JAKIŚ kurs linii w ciągu 20
minut, a okno przeszły godziny konkretnego kursu.

## Tryb awaryjny: przyczyna znaleziona i naprawiona (2026-08-27)

Wyszło z pomiaru spisanego w `docs/TODO_EMULATOR_I_TRYB_AWARYJNY.md`:
`plan_flow` ma gałąź `else` na wypadek, gdyby `_select_and_anchor` przyciął
wszystkie segmenty do zera, i rysuje wtedy samą najszybszą trasę z
jasnościami `1.0`/`0.6` wpisanymi na sztywno. Komentarz nazywał to
„skrajnym, rzadkim przypadkiem". Nie było ani skrajne, ani rzadkie:
Sosnowiecka → Wojszyce o 15:37 przy domyślnym oknie dawała **31 kandydatów
i 0 zatrzymanych**.

**Przyczyna — kotwica początku pytała o złą rzecz.** Pytała „czy kurs
ZACZYNA się na przystanku startowym" (`seg["stops"][0] in source_stops`),
a powinna „czy da się do niego wsiąść NA przystanku startowym". Miejsce
wsiadania wybrane w `_discover_segments` to najwcześniejsze możliwe, nie
jedyne. Na Sosnowieckiej wygląda to tak:

- Autobus 124 wiezie ze Sosnowieckiej (słupek 3019) przez Brochowską na
  pętlę KSIĘŻE WIELKIE i tam kończy — to jedyny segment startujący na
  przystanku startowym;
- z pętli wyjeżdża 124/134 i wraca tą samą ulicą: Brochowska (drugi słupek,
  424) → Sosnowiecka (drugi słupek, 3020) → Zagłębiowska → miasto. Jego
  segment ZACZYNA się na pętli, bo tam wypada najwcześniejsze wsiadanie
  (dojechawszy tam 124-tką), a przystanek startowy mija w swoim środku.

Kotwica końca słusznie zabijała pierwszy segment: jedyna kontynuacja z
pętli wraca po jego własnych śladach (`_leads_onward`, punkt 4). Ale wtedy
drugi tracił JEDYNĄ kotwicę początku, jaką kotwica umiała zobaczyć — choć
pasażer po prostu wsiada do niego obok, na drugim słupku Sosnowieckiej.
Punkt stały rozplątywał się dalej kaskadą: 31 → 28 → 24 → 16 → 11 → 5 → 2
→ 1 → 0 w ośmiu iteracjach.

**Naprawa** (`_select_and_anchor`): pozycja, na której segment stoi na
przystanku startowym i ma stamtąd odjazd, jest sama w sobie kotwicą
początku. To samo pytanie zadaje teraz `_extract_transfer_graph`
(`origin_ids` liczone z zakotwiczonej pozycji, nie z `stops[0]`) — inaczej
mapa się rysowała, a lista propozycji wychodziła pusta, bo szukała ścieżek
od segmentów „zaczynających się" na starcie.

**Zmierzone, przemiat 64 relacji × godzin (16 miejsc, 4 pory dnia):**

| | przed | po |
|---|---|---|
| relacji w trybie awaryjnym | 2 | **0** |
| map, które straciły choć jeden segment | — | **0** |
| map z większą liczbą zatrzymanych segmentów | — | 3 |
| map rysowanych dłużej (segment od startu, nie od pętli) | — | 16 |

Sosnowiecka → Wojszyce 15:37: 0 → **25 zatrzymanych segmentów, 57 kawałków,
18 linii, pełna skala jasności 0,0–1,0, 5 propozycji tras** (przedtem: jedna
trasa z trybu awaryjnego). Ubytek liczby *kawałków* w 10 przypadkach to nie
strata: sprawdzone na Wojszyce → Biskupin 07:15, gdzie linia 113 przestała
być łamana na dwa kawałki, a 903 jest teraz rysowana od samego startu (14
punktów zamiast 6).

**Los trybu awaryjnego.** Zostaje jako zabezpieczenie — konstrukcyjnie wciąż
jest osiągalny (kontynuacja zawraca po własnych śladach, a przesiadka piętro
niżej wypada poza `WAIT_CAP_SEC`) — ale przestał udawać zwykłą mapę:
odpowiedź `/api/flow` ma teraz pole `degraded`, a front na jego podstawie
dopisuje NAD listą mały komunikat w tym samym stylu, co pozostałe błędy
(„Tryb awaryjny: nie udało się ułożyć wachlarza połączeń…") — na wyraźną
prośbę użytkownika, 2026-08-27. Nad listą, nie zamiast niej: pokazana trasa
ma zostać widoczna. Test: `test_fallback_mode_shows_a_notice_on_screen`
(sprawdzony mutacją: po usunięciu wywołania test pada). Testy zaplecza:
`test_fallback_map_admits_that_it_is_a_fallback`,
`test_a_normal_map_is_not_marked_as_a_fallback`,
`test_course_passing_the_origin_after_a_loop_is_anchored_at_the_origin`
(ten ostatni to odtworzony układ z Sosnowieckiej).

## Emulator frontu: punkty 7, 8 i 10 wreszcie testowane (2026-08-27)

Do tej pory testy sięgały wyłącznie `planner.py`, więc trzy punkty
kontraktu żyjące w `static/app.js` nie były sprawdzane niczym — zmiana w
froncie mogła po cichu złamać kontrakt. Doszedł emulator:

- `tests/js/harness.js` — minimalny Leaflet (rzut Web Mercator jak w
  oryginale, `latLngToContainerPoint`, `fitBounds` z marginesami panelu,
  `moveend`), minimalny DOM i `localStorage`, po czym uruchamia **prawdziwy
  `static/app.js`** — nie kopię i nie wycinek. Kod mapy siedzi w app.js
  wewnątrz bloku `if (startInput) { … }`, więc harness dokleja eksport
  swoich uchwytów przed ostatnią klamrą pliku; gdyby ten blok zmienił
  kształt, harness pada z komunikatem zamiast po cichu przestać sprawdzać.
- `tests/js/flow_fixture.json` — prawdziwa odpowiedź `/api/flow`
  (Sosnowiecka → Wojszyce 15:37, 57 kawałków, 32 z korytarzem).
- `tests/js/checks.js` + `tests/test_flow_map_front.py` — 11 testów.

**Silnik: JavaScriptCore przez `osascript -l JavaScript`**, nie node+jsdom.
Powód: projekt nie ma dziś żadnej zależności javascriptowej ani kroku
budowania, na maszynie nie ma `node`, a CI nie ma w ogóle. Cena: testy są
macOS-owe i gdzie indziej się pomijają (`pytest.skip`). Cały pakiet chodzi
poniżej 0,1 s.

**Zmierzone na fixture:** 22 grupki numerów, 0 kolizji; 34 najechania na
numer, 0 pomyłek wskazania; 157 godzin na przystankach, największy błąd
interpolacji **0 s**; 3477 próbek wzdłuż linii, 0 cofnięć czasu; kropka
**0 m** od linii; najmniejsze krycie 0,4 przy grubości 3 px.

**Czy te testy potrafią paść** — sprawdzone pięcioma mutacjami `app.js`,
każda złapana przez właściwy test i tylko przez niego:

| mutacja | złapana przez |
|---|---|
| wyłączone odrzucanie kolidujących grupek | `..._groups_never_overlap` (+ test przełącznika) |
| interpolacja przesunięta o 30 s | `..._comes_straight_from_the_schedule`, `..._never_goes_backwards` |
| `minOpacity` 0,4 → 0,02 | `..._never_reaches_invisibility`, `..._nothing_actually_drawn_is_invisible` |
| kropka czasu 55 m obok linii | `..._time_dot_sits_on_the_line` |
| grupka ignoruje, który numer wskazano | `..._names_exactly_that_line` |

Świadome ograniczenia atrapy (spisane też w harnessie): brak prawdziwego
układu CSS — rozmiar grupki bierze się z `clusterBox`, nie z pomiaru tekstu
przez przeglądarkę; zdarzenia myszy wywoływane wprost, nie przez propagację
DOM; `fetch` nigdy nie woła callbacków, więc nic nie dzieje się
asynchronicznie.

## Pomysł do zrobienia kiedyś: znaczek „nowe", który gaśnie sam (2026-08-27)

Sekcje panelu deweloperskiego dostają ręcznie dopisywane znaczki —
`tymczasowe` przy „Wyglądzie mapy", `nowe` przy „Czasie na mapie". Problem
jest zawsze ten sam: nikt nie pamięta, żeby je później skasować, więc „nowe"
wisi pół roku i przestaje cokolwiek znaczyć.

Pomysł użytkownika: zamiast wpisywać sam napis, wpisywać przy nim DATĘ
wprowadzenia, a znaczek renderować tylko wtedy, gdy od tej daty minęło mniej
niż ~7 dni. Wtedy „nowe" gaśnie samo i nie zostawia po sobie długu.

Nie zrobione — zanotowane na wyraźną prośbę, do decyzji później.

## Tablica przesiadki przestaje być listą samych odjazdów (2026-08-30)

**Czego brakowało.** Kropka przesiadki odpowiadała wyłącznie na „czym stąd
pojechać". Człowiek, który na nią patrzy, stoi jednak w konkretnym miejscu i
w konkretnej sytuacji: czymś tu przyjechał, coś go tu mija, a w coś dopiero
wsiada — i te trzy rzeczy znaczą dla niego co innego. Tablica zlewała je w
jedno („odjazdy"), a pojazd, którym się tu dojechało, w ogóle w niej nie
istniał, bo przyjazd nie jest odjazdem i w rozkładzie przystanku go nie ma.

**Co zrobiono.** Węzeł (`planner._transfer_nodes`) niesie teraz przy każdej
linii pole `flow` o trzech wartościach:

| `flow` | znaczy | skąd się bierze |
|---|---|---|
| `start` | wsiadasz tu pierwszy raz | kawałek tej linii tu się ZACZYNA i nigdzie wcześniej mapa nią nie wiozła |
| `through` | możesz już nim jechać | kawałek tu się KOŃCZY i ZACZYNA |
| `end` | tu wysiadasz | kawałek tu się tylko KOŃCZY |

Do `end` dochodzi `arrive` — godzina przyjazdu z rozkładu TEGO kursu, z
którego narysowano kawałek (front nie ma jej skąd wziąć: w tablicy odjazdów
przystanku tej godziny nie ma). `depart_by` zostaje przy `start`/`through`,
bo odpowiada na inne pytanie („którym ostatnim odjazdem jeszcze zdążę").
Kilka kawałków tej samej linii kończących się w węźle daje jedną godzinę —
NAJWCZEŚNIEJSZĄ, tą samą zasadą, co `sec` całego węzła.

Linia, którą stąd już się nie dojedzie (brak `depart_by`), przestaje być
ofertą do wsiadania — ale jeśli mapa nią tu dowozi, nie znika, tylko schodzi
do `end`. To zamiana jednego zdania w tablicy na inne, nie skasowanie wiersza.

**Front.** `keepOfferedLines` wypuszcza `end` z listy odjazdów (wypisana z
najbliższym odjazdem udawałaby opcję, której mapa nie proponuje), a
`withArrivals` dokłada jej własny wiersz z `arrive` — PO obu sitach, bo oba
pytają „czy tym odjazdem jeszcze się dojedzie", a przyjazd nie jest odjazdem.
Kolejność w tablicy robi `sec`, więc przyjazd stoi tam, gdzie wypada na osi
czasu, a nie doklejony na końcu. `summariseRepeats` ma `flow` w kluczu grupy:
przyjazd i odjazd tej samej linii to dwa zdarzenia, zwinięte w jeden wiersz
udawałyby takt kursowania, którego nie ma.

**Znaki.** Jedna rodzina, czytana zawsze tak samo — lewy koniec mówi, skąd
ten pojazd tu jest (kreska „stąd rusza", grot „już jedzie"), prawy mówi, co
z nim dalej (grot „jedzie dalej", kreska „tu koniec jazdy"):
`|→` start, `→→` through, `→|` end. Wąska kolumna PRZED godziną, bo
odpowiada wcześniej niż ona. Kolumna pojawia się tylko tam, gdzie jest czym
ją wypełnić: tablica pod kropką WYBRANEJ trasy pyta o cały przystanek, a nie
o węzeł mapy, więc nie wie, co się tu z którą linią dzieje — i zostaje bez
znaków, zamiast je zgadywać. Tak samo odpowiedź bez `flow` (cache sprzed
zmiany, tryb awaryjny): brak znaku, nie znak domyślny.

**Czego to NIE zmieniło.** Kropek nie przybyło. Miejsce, w którym da się
tylko wysiąść, nadal nie jest przesiadką i kropki nie dostaje
(`test_a_place_where_you_only_get_off_is_still_not_a_transfer`) — zmieniło
się to, co kropka mówi, a nie to, gdzie stoi. Rysowanej mapy (jasność,
grubość, geometria) nie tknięto.

**Testy:** 141 przechodzi (było 132; +4 backend, +5 front).

**Tekst punktu 11 kontraktu** — draft przekazany użytkownikowi i zatwierdzony
2026-08-30, wpisany do FLOW_MAP_CONTRACT.md. Na jego prośbę bez zdania
otwierającego („wachlarz rysuje przejazdy, ale przesiadka jest w nim
punktem…") — punkt zaczyna się od razu od „Gdzie stoi kropka". Tytuł też
nowy: obietnicą nie jest już samo „w co się przesiąść", tylko „co się tu
z każdą linią dzieje".

## Kropki przestają dziedziczyć cięcia rysowania (2026-08-31)

**Zgłoszenie.** Trzy rzeczy naraz, na trasie Galeria Dominikańska →
pl. Grunwaldzki: (1) przez Urząd Wojewódzki (Muzeum Narodowe) mapa rysuje
autobus N, ale kropka go nie wymieniała; (2) koło Katedry nie było kropki,
choć piątką dojeżdża się tam wyłącznie po to, żeby przesiąść się dalej;
(3) na Urzędzie Wojewódzkim (Impart) kropka stała i nie miała nic do
powiedzenia — D i 146 tylko tamtędy przejeżdżały.

**Jedna przyczyna, nie trzy.** `_transfer_nodes` czytał wyłącznie KOŃCE
narysowanych kawałków. Z tego wynikało wszystko:

- kawałek N to `GALERIA DOMINIKAŃSKA → Urząd Wojewódzki → Katedra` — urząd
  leży w jego ŚRODKU, więc dla węzła ta linia tam nie istniała;
- na Katedrze kończyły się kawałki 5 i N, a 10 i 111 tylko przejeżdżały
  (`Pl. Bema → Ogród Botaniczny → Katedra → Reja → PL. GRUNWALDZKI`) — skoro
  nic się tam nie ZACZYNAŁO, reguła „tylko się tu wysiada" kasowała kropkę
  razem z całą przesiadką;
- na Impart kropka stała, bo tam był KONIEC kawałka. Tyle że kawałki tnie
  również zmiana składu korytarza (`crosses` w _refine_brightness, punkt 7) —
  sprawa czysto rysunkowa. Widać to po jasnościach: D dostał tam szew przy
  w=1.00 po OBU stronach, 146 przy 0.40 po obu. Nic się nie zmieniało.

**Naprawa.** Węzeł czyta teraz KAŻDY przystanek kawałka i pyta o dwie rzeczy
osobno: czy mapa tą linią DO tego miejsca dowozi (jest wcześniejszy przystanek
w kawałku) i czy wiezie DALEJ (jest późniejszy). Z tego wychodzą trzy `flow`
bez zmiany ich znaczenia. Kropka stoi tam, gdzie coś się ZACZYNA albo KOŃCZY —
gdzie linia staje się dostępna albo przestaje. Miejsce, przez które wszystko
tylko przejeżdża, kropki nie dostaje, choćby leżało na styku dwóch kawałków:
linia przecięta z powodu korytarza wychodzi jako „through" i sama z siebie
kropki nie stawia. Placement przestał więc zależeć od tego, gdzie cutter
akurat postawił szew.

**Pułapka po drodze (Reja).** Pierwsze podejście pytało `_rides_back`
o drogę OD TEGO przystanku do końca kawałka. Ta funkcja uznaje za cofnięcie
także RÓWNE godziny, a na Rei „najwcześniej tutaj" i „najwcześniej u celu"
wypadały identycznie (11:01) — więc 111 zostawał ogłoszony jako kończący się
na Rei, choć jedzie stamtąd jeszcze przystanek do celu, i pojawiała się kropka
na wyssanej z palca przesiadce. `_rides_back` została NIETKNIĘTA (pilnuje
przypadku Kamiennogórskiej z 2026-08-29); pytamy nią o kawałek jako całość,
dokładnie jak przedtem. Kawałek zawracający i tak nie ma prawa być narysowany
(punkt 4), więc miara na całości niczego nie przepuszcza.

**Wynik na zgłoszonej trasie:** 7 kropek → 6. Doszła Katedra
(10 i 111 „through", 5 i N „end"), Urząd Wojewódzki (Muzeum Narodowe) dostał
brakujące N, zniknęły Urząd Wojewódzki (Impart) i most Grunwaldzki — oba
były szwami korytarza D/146. Ogród Botaniczny i Reja, mijane w środku
kawałków, kropki nadal nie dostają.

**Testy:** 149 przechodzi (było 145). Cztery nowe, sprawdzone trzema
mutacjami `planner.py` — każda złapana przez właściwy test:

| mutacja | złapana przez |
|---|---|
| czytaj tylko końce kawałków (stan sprzed naprawy) | `..._passing_through_the_middle_of_a_piece_is_still_listed`, `..._gets_a_dot_even_if_nothing_starts_there`, `..._does_not_claim_the_line_ends_there` |
| kropka dziedziczy każdy szew rysowania | `..._a_seam_between_two_pieces_is_not_a_transfer` |
| `_rides_back` pytany o drogę od tego przystanku | `..._the_stop_before_the_target_does_not_claim_the_line_ends_there` |

**Kontrakt.** Punkt 11 opisywał to dobrze od początku („mapa dowozi tu tą
linią i wiezie nią dalej") — to kod był węższy niż obietnica. Reguła stawiania
kropki zyskała za to drugą połowę i na zgodę użytkownika (2026-08-31) doszło
do „Gdzie stoi kropka" jedno zdanie: miejsce, przez które wszystko tylko
przejeżdża, też nie jest przesiadką.

---

## Liczba wierszy dymka wraca z pliku środowiska pod zębatkę (2026-08-31)

**Zgłoszenie:** „co to robi w .env? to powinno być w ustawieniach tak jak
wszystko inne".

Racja i to podwójna. Plik środowiska w tym projekcie istnieje dla JEDNEGO
sekretu — klucza do API PKP — i tylko dlatego leży poza repozytorium, na tym
samym wolumenie co baza. Liczba wierszy tablicy pod kropką nie jest sekretem,
tylko pokrętłem wyglądu, a wszystkie inne takie pokrętła (wielkość kropek,
gdzie pokazać rozkład, całe okno czasowe) siedzą pod zębatką. Do tego to samo
ustawienie żyło w trzech miejscach naraz: w pliku środowiska, w funkcji
konfiguracji z własnym sufitem i jako atrybut `data-` na `<body>`, skąd front
je odczytywał — a osiem było jeszcze osobno wpisane w serwerowy limit tablicy.

**Co się zmieniło.** Suwak „Ile odjazdów w tablicy" w sekcji „Kropki
i rozkład", obok wielkości kropek, zapamiętywany w tym samym kluczu
`localStorage` co reszta tej sekcji. Sufit dwudziestu został tam, gdzie był
sensowny — ale teraz obowiązuje przy ODCZYCIE, nie przy zapisie: atrybut
suwaka pilnuje przeciągania, a z pamięci przeglądarki może wrócić wartość
z czasów innego zakresu albo w ogóle nie liczba. Serwer o tej liczbie nie
wie już nic; nie musi, bo i tak pobieramy z zapasem (40) i odsiewamy na
froncie. Zmiana suwaka czyści pamięć podręczną dymków — leży w niej gotowy
HTML, przycięty do STAREJ liczby wierszy — i przeładowuje ten otwarty.

**Domyślną ustawiliśmy na 20**, czyli na sam sufit (decyzja użytkownika,
2026-08-31). Wyszła przy tym druga rzecz: o zapas prosiła dotąd tylko kropka
węzła wachlarza, bo tylko ona coś odsiewa — kropka na WYBRANEJ trasie pytała
bez limitu i dostawała serwerową ósemkę. Przy domyślnej ósemce nie było tego
widać; przy dwudziestce byłby to cichy sufit mocniejszy od suwaka. Teraz
o zapas prosi każda kropka.

**Testy:** 150 (było 149; jeden stary sprawdzian pytał o atrybut z serwera,
więc zniknął razem z nim, doszły dwa). Obie mutacje złapane:

| mutacja | złapana przez |
|---|---|
| odczyt bez sufitu i podłogi | `test_row_count_is_a_panel_setting` |
| tablica przycinana na sztywno do ośmiu | `test_the_slider_actually_trims_the_table` |

---

## Kolej przestaje być doklejką (2026-08-31)

**Zgłoszenie:** „nie znaleziono połączenia DWORZEC GŁÓWNY → Wojszyce po 11:02",
na localu, z danymi kolejowymi. Ta sama relacja bez kolei działała.

**Przyczyna, jedno zdanie.** Skan pytał „czy to już cel" WYŁĄCZNIE przy
wysiadaniu z pojazdu, nigdy po przejściu na sąsiedni słupek. Pociąg dowoził
na stację Wrocław Wojszyce o 11:21:42, trzy minuty pieszo dawały przystanek
Wojszyce o 11:24:42 — czyli cel, i to szybciej niż autobusem 113 o 11:27.
Ta godzina wpisywała się do tabeli najwcześniejszych dojazdów, przez co
odrzucała późniejszy dojazd autobusem (ten BYŁBY zauważony) — i sama nie
była zauważana. Zostawało „nie znaleziono połączenia" na relacji, którą
skan miał policzoną.

Dziura była we wspólnym silniku od zawsze, tylko nieosiągalna: dopóki
wszystkie słupki jednego miejsca obsługiwały te same linie, przejście nigdy
nie było JEDYNYM wejściem w cel. Kolej była pierwszą siecią, która to
potrafi. Naprawa to jedno miejsce, w którym pada pytanie o cel — wołane
i przy wysiadaniu, i po przejściu.

**Przy okazji: audyt „czy kolej to naprawdę trzeci typ".** Silnik tak —
w wyszukiwarce nie ma ani jednej gałęzi „a jeśli pociąg". Poniżej silnika
było osiem miejsc, które wiedzą, że pociąg to pociąg: osobna baza scalana
przy każdym budowaniu dnia, dwa pola dnia tylko dla kolei, rozgałęzienie po
prefiksie w rysowaniu, DRUGI mechanizm przesiadki (własny promień 500 m),
brak przynależności do miejsca, osobne doklejanie podpowiedzi i markerów,
osobny plik współrzędnych, sekundy na osi czasu bez sekund.

**Co z tego zrobiliśmy (decyzja użytkownika).** Wszystko ma być to samo poza
pobieraniem. Trzy rzeczy weszły od razu:

1. **Stacja przechodzi przez to samo sklejanie w miejsce co przystanek.**
   Kolej dokładana do dnia PRZED budowaniem miejsc, nie po. Wcześniej stacja
   nie należała do żadnego miejsca (zmierzone: 0 z 2974) i właśnie dlatego
   musiała mieć drugi mechanizm przesiadki.
2. **Własny promień przesiadkowy usunięty** razem z funkcją, która go liczył.
   Przesiadka bierze się wyłącznie z miejsca: ta sama nazwa = to samo miejsce.
3. **Czas kolejowy ucinany do pełnych minut**, ostrożnie: odjazd w dół,
   przyjazd w górę. Jedna oś czasu nie może mieć dwóch dokładności.

**Pułapka po drodze (postoje).** Ucięcie w dwie strony potrafi na jednej
stacji odwrócić postój krótszy niż minuta (10:14:42 → 10:15 przyjazdu,
10:14:48 → 10:14 odjazdu). Cofnięcie czasu jest niżej brane za przejście
przez północ i dokładało do kursu CAŁĄ DOBĘ. Zmierzone: dotyczy 14,1%
postojów (36 155 z 257 049). Odjazd nie może wypaść przed przyjazdem.

**Druga pułapka (nazwa to za mało).** Reguła „ta sama nazwa" była zaufana bez
sprawdzania odległości, bo wszystkie dane pochodziły z jednego miasta.
Ogólnopolski słownik stacji łamie to założenie. Zmierzone: 11 nazw wspólnych
z miastem, z czego **naprawdę to samo miejsce tylko 2** (Wrocław Szczepin
200 m, Ramiszów 500 m); reszta to wsie 77–354 km stąd. Bez zabezpieczenia
„Mokra" we Wrocławiu skleiłaby się ze stacją 242 km dalej i wyszedłby
trzyminutowy spacer przez pół Polski. Zabezpieczenie to ta sama miara, której
miejsce używa już przy doklejaniu peronów — jedna reguła dla wszystkich
źródeł, bez gałęzi „a jeśli kolej".

Pierwsza wersja tego zabezpieczenia rozwiązywała CAŁĄ rozjechaną grupę i przez
to karała miasto za kolizję kolei: dwa wrocławskie słupki „Wiśniowa" traciły
wspólne miejsce, bo do ich nazwy dopisała się stacja spod Kielc. Poprawione:
odstający wypada sam, spójny rdzeń zostaje.

**Bilans zmierzony.** Miejskich słupków tracących miejsce: 2 (C.H. Korona,
Tarczyński Arena (Aleja Śląska)) — oba stoją naprawdę dalej niż 400 m od
swojego imiennika, więc to nie regresja po kolei. Stacji bez miejsca: 10
z 3023 (dokładnie te kolizje nazw). Stacji dzielących miejsce z przystankiem
miejskim: **1** (Wrocław Szczepin). To znaczy, że dziś obie sieci stykają się
w jednym punkcie — zamierzone: porządne łączenie stacji z przystankami to
osobne zadanie, nie ten kod.

**Testy:** 160 (było 150), dziesięć nowych, pięć mutacji — każda złapana
przez właściwy test:

| mutacja | złapana przez |
|---|---|
| cel sprawdzany tylko przy wysiadaniu z pojazdu | `test_a_target_reached_only_on_foot_is_still_a_connection` + 2 |
| nazwa wystarczy, odległość nieważna | `test_the_same_name_far_away_is_not_the_same_place` + 1 |
| rozjechana grupa rozwiązana w całości | `test_the_far_stop_is_dropped_not_the_whole_place` |
| brak zabezpieczenia postoju | `test_a_dwell_shorter_than_a_minute_does_not_rewind_the_clock` |
| czas kolei bez ucinania | `test_rail_connections_land_on_whole_minutes` + 1 |

**Zostaje do zrobienia.** Cztery szwy z ośmiu wciąż są: osobna baza scalana
przy każdym budowaniu dnia, dwa pola dnia tylko dla kolei, rozgałęzienie po
prefiksie w rysowaniu, osobne doklejanie podpowiedzi i markerów. Wszystkie
znikają jedną zmianą — import kolei do wspólnych tabel rozkładu przy budowie
bazy, dokładnie jak Siechnice, które są scalane właśnie tam i poniżej importu
nikt już o nich nie wie.

---

## Zawsze jakaś trasa — punkt 13 wchodzi w życie (2026-08-31)

**Punkty 12 i 13 wpisane do kontraktu** na wyraźną zgodę użytkownika.
Dwunastka opisuje stan już wprowadzony (patrz notatka wyżej). Trzynastka
wymagała kodu.

**Co było.** Wyszukiwanie przeszukiwało całą dobę rozkładową i poddawało się
dopiero na jej końcu — sprawdzone: pytanie o 03:30 znajdowało trasę o 04:08,
więc „za godzinę" działało już wcześniej. Nie działało przejście przez
północ: pytanie o 23:59 o relację bez kursów nocnych dostawało „nie
znaleziono połączenia tego dnia", choć rano jedzie.

**Co jest.** Gdy w dobie z pytania nic nie jedzie, szukamy w kolejnych —
do siedmiu, bo rozkład jest tygodniowy i relacja bez kursu przez tydzień
naprawdę go nie ma. Odpowiedź niesie godzinę wyjazdu, ile dni do niego
i ile się czeka; front pisze to nad wynikami neutralnym komunikatem (nie
czerwonym — to nie błąd, tylko odpowiedź „za jakiś czas").

**Okno przestało liczyć się od pytania.** Naddatek brał dotąd za podstawę
`przyjazd − godzina pytania`, więc godzina czekania rozdymała wachlarz:
przy pytaniu o 10:00 i autobusie 12:00→12:30 podstawą było 150 minut zamiast
30, a naddatek dobijał do sufitu. Teraz podstawą jest odjazd pierwszego
przejazdu. Dotyczy to każdej trasy, nie tylko tych „na jutro" — czekanie
nigdy nie było częścią podróży.

**Próg widoczności czekania** to 20 minut, ta sama miara, którą mapa uznaje
już za „przesiadka jeszcze łączy odcinki". Nie nowa, dobrana liczba — jedna
z dwóch, które w tym projekcie znaczą „czekanie, które jeszcze uchodzi".

**Pułapka po drodze (oś czasu czekania).** Po zejściu na kolejną dobę
godzina pytania zostaje w poprzedniej, a wyjazd liczy się od zera nowej.
Zwykła różnica pokazywała 3 minuty czekania tam, gdzie w rzeczywistości
było prawie tyle samo — ale w innym dniu, więc przy większej odległości
wyszłaby bzdura. Czekanie liczy się od pytania, przez granicę doby.

**Testy:** 166 (było 160), sześć nowych, cztery mutacje — każda złapana:

| mutacja | złapana przez |
|---|---|
| okno liczone od pytania | `test_the_map_window_itself_starts_at_the_departure` |
| brak zejścia na kolejną dobę | `test_nothing_today_is_answered_with_tomorrow` |
| odjazd trasy = godzina pytania | `test_the_window_is_measured_from_the_departure_not_the_question` + 2 |
| czekanie nigdy nie pokazane | `test_a_route_that_starts_much_later_says_so` |

## Jedna zasada chodzenia — punkt 14 (2026-09-12)

Kontrakt dostał punkt 14, a punkty 4, 10 i 12 zostały pod niego dociągnięte.
Powód był prosty: chodzenie miało trzy różne zasady naraz — 300 m
w przesiadce z czasem marszu, 400 m na krańcach relacji z czasem marszu
i 1000 m wokół punktu klikniętego na mapie **za darmo**. Ta sama czynność
była wyceniana trzy razy inaczej, zależnie od tego, gdzie w podróży wypadła:
900 m na starcie nie kosztowało ani minuty, 350 m w środku trasy pięć.

**Promień jest jeden** (`gtfs.WALK_M`, 600 m). Punkt z mapy wchodzi do dnia
jako zwykły słupek bez połączeń (`gtfs.with_point`) — dzięki temu most
pieszy, skan w przód, skan wstecz, profil celu i całe kotwiczenie nie musiały
się o nim niczego dowiedzieć. Suwak „Zasięg szukania punktu" zniknął razem
z parametrem `range_m`: nie ma już czego regulować.

**Czas marszu liczy się z samej odległości w linii prostej**, 0,7 m/s, w górę
do pełnych minut, nie mniej niż trzy. Wcześniej: 1,3 m/s dzielone przez
współczynnik nadłożenia drogi (efektywnie 0,96 m/s). Skoro i tak znamy tylko
prostą, cały brak wiedzy ma siedzieć w jednej liczbie — za nią stoi ostrożny
pieszy (1,07 m/s, wartość z inżynierii ruchu; przekracza ją 85% ludzi) na
drodze półtora raza dłuższej niż prosta (pomiary „detour factor": 1,4–1,5
w siatce ulic, więcej na osiedlach z zaułkami).

**Przejście musi coś otwierać.** Zgłoszone na żywo ze zrzutem ekranu:
`Wojszyce → DWORZEC GŁÓWNY`, 11.09, 18:08. Autobus 112 staje na Parafialnej
o 18:13 i na Wojszycach o 18:14 — ten sam kurs. Wyszukiwarka kazała iść
cztery minuty WSTECZ, żeby wsiąść przystanek wcześniej, z identyczną godziną
w celu (18:32); mapa rysowała 112 i 113 od Parafialnej, a na Wojszycach nie
było nawet kropki, bo obie linie tylko tamtędy „przejeżdżały". Dwie poprawki,
obie tej samej myśli — po pojazd, który i tak po nas przyjedzie, się nie
chodzi:

1. `_cheaper_boarding` przesuwa wsiadanie na przystanek, na którym już stoimy,
   gdy zapisany punkt wsiadania wymagał marszu (przy tej samej liczbie
   przejazdów). Wcześniej warunek był ostry (`legs[stop] >= board_legs`), więc
   remis rozstrzygała kolejność skanowania.
2. `_select_and_anchor` dostaje `source_stops` i `walk_stops` OSOBNO. Kurs,
   który zatrzymuje się na przystanku startowym, kotwiczy się tam, choćby
   wcześniej mijał słupek osiągalny pieszo.

Po zmianie ta sama relacja: 112 od Wojszyc, kropka na Wojszycach (z flagą
startu), ta sama godzina w celu.

**Pomiar rozdzielający przyczyny** (6 relacji, 12.09): sama reguła wsiadania
nie zmienia map — kawałki, linie, kropki i propozycje wychodzą co do sztuki
tak samo jak przed zmianą. Wszystkie różnice biorą się z wolniejszego marszu
i o to chodziło: przejście z peronu Wrocław Główny na przystanek „DWORZEC
GŁÓWNY" kosztowało 3 minuty, teraz 7, więc autobus, którego się nie łapało,
przestał być proponowany.

**Czego tu NIE ma.** Przejść pieszo mapa nadal nie rysuje — to wybór
(punkt 4), nie brak: kreska przy każdym przejściu zaśmieciłaby rysunek
bardziej, niż tłumaczy. Kropka przesiadkowa nadal nie wie o chodzeniu:
miejsce, z którego wychodzi się PIESZO na inny przystanek, wygląda dla niej
jak „tu się tylko wysiada". To jest otwarta część punktu 11, świadomie
zostawiona do przemyślenia.

**Testy:** 268 (było 264). Cztery nowe w `tests/test_dojscie_piesze.py`:
marsz po własny kurs (zweryfikowany jako czerwony na starej regule), mapa
rysowana od przystanku startowego, czas dojścia z klikniętego punktu i punkt
bez przystanków w zasięgu. Cztery istniejące przestały zakładać starą
prędkość i drugi promień — liczą teraz czas przejścia z `gtfs.walk_seconds`,
więc nie zamrażają żadnej stałej.

### Dopisek tego samego dnia: „mniej marszu", nie tylko „bez marszu"

Pierwsza wersja reguły z punktu 14 porównywała wsiadanie „bez chodzenia"
z wsiadaniem „za marszem" — i przez to nie łapała przypadku, w którym OBA
przystanki są za marszem. Zgłoszone z punktu klikniętego w Radwanicach:
APK1 staje na Mickiewicza o 15:00 i na Skrajnej o 15:01 (ten sam kurs), a ze
wskazanego punktu idzie się odpowiednio 14 i 7 minut. Mapa proponowała dalszy
przystanek, bo kurs mija go wcześniej i to tam zapisywało się wsiadanie.

Miarą jest teraz ILOŚĆ MARSZU, nie jego obecność. Skan niesie `walked`
(sekundy chodzenia w najlepszej drodze do przystanku; jazda nóg nie kosztuje),
`_cheaper_boarding` przy remisie wybiera mniejszy marsz, a `_select_and_anchor`
kotwiczy kurs na tym ze słupków osiągalnych pieszo, do którego jest najbliżej —
nie na tym mijanym najwcześniej. Po zmianie: wsiadanie na Skrajnej, kropka na
Skrajnej, przyjazd 15:21 taki sam jak przedtem.

Otwarte i świadomie nietknięte: mapa pokazuje JEDNO miejsce wsiadania w kurs
(kotwica), więc „wsiądź na Skrajnej ALBO na Mickiewicza" nie ma dziś jak być
pokazane jako dwie opcje — patrz znane ograniczenie o kotwiczeniu segmentów.

**Testy:** 270 (było 268). Dwa nowe: wybór bliższego z dwóch przystanków tego
samego kursu (zweryfikowany jako czerwony na poprzedniej wersji reguły —
wskazywał dalszy) i kotwica mapy dla tego samego układu.

## Auto na wynajem wchodzi na mapę — punkt 15 (2026-09-12)

Do tej pory Traficar żył wyłącznie w liście propozycji: mapa nie wiedziała
o żadnym aucie, a auto nie wiedziało o żadnej mapie. Warstwa z odrzuconego
branchu `archive/traficar-layer` (7fd7b66, 2026-07-22) pokazywała z kolei
wszystkie auta w mieście naraz i nie wiedziała nic o wyszukiwanej relacji —
ładny fiolet, zero odpowiedzi na pytanie „i co mi z tego". Stąd wygląd
został wzięty stamtąd, a reguła powstała od zera.

**Reguła jest jednym zdaniem:** auto jest miejscem, do którego da się dojść.
Nie kursem, nie etapem, nie propozycją. Wszystko inne wynika z tego zdania:

- czym się do niego dochodzi — tym samym dojściem, co wszędzie indziej
  (`gtfs.WALK_M`, `gtfs.walk_time_sec`, punkt 14). Auto nie dostało własnego
  promienia ani własnej prędkości, choć `traficar.py` jeden taki ma —
  `WALK_TO_CAR_M`/`WALK_SPEED_MPS` zostały tam, gdzie były, bo obsługują
  propozycje, a te są osobnym procesem i nie zmieniały się tu ani o minutę;
- skąd się dochodzi — z tego, co mapa NARYSOWAŁA (`planner._drawn_reach`
  czyta godziny z narysowanych kawałków), plus z samego startu. Kuszące było
  wziąć `earliest` ze skanu w przód: jest pod ręką i zna pół miasta. Właśnie
  dlatego nie — „mapa dowozi" ma znaczyć to, co widać na ekranie, inaczej
  auto stałoby przy trasie, której nikt nie narysował. Z tego samego powodu
  w trybie awaryjnym (mapa jest wtedy jedną trasą, nie wachlarzem) aut nie ma
  wcale;
- co auto o sobie mówi — godzinę, o której się przy nim jest, i odległość
  celu w linii prostej. Ani minuty jazdy.

**Dlaczego mapa nie podaje czasu jazdy, skoro propozycje podają.** To nie jest
niekonsekwencja, tylko dwa różne pytania. Propozycja musi ułożyć CAŁĄ trasę
i skończyć się godziną w celu — bez szacunku nie dałoby się jej pokazać
w ogóle, więc szacunek jest, zmierzony i podpisany „ok." wszędzie, gdzie się
pojawia. Mapa odpowiada krócej: „o 15:12 możesz stać przy tym aucie, do celu
stąd 2,7 km w linii prostej". Reszta należy do pasażera — i do przyszłego
odnośnika „prowadź", który to pytanie odda temu, kto naprawdę zna drogę
(patrz „Pomysły na dalej" w PROJECT.md).

**Ile tego jest.** Wolnych aut we Wrocławiu jest ok. 40 naraz (feed, pomiar
2026-09-12), a w zasięgu jednej mapy wychodzi 1–9: `Wojszyce → Dworzec
Główny` 1, `Kozanów → pl. Grunwaldzki` 1, `Katedra → Leśnica` 3, punkt
w Radwanicach → `pl. Grunwaldzki` 4, `Osobowice → Biskupin` 9. Żadnego
odsiewu „czy to auto ma sens" nie ma i celowo: mapa nie zakłada jazdy, więc
nie ma czym mierzyć sensu — a przy takich liczbach nie ma też czego ratować.

**Testy:** 282 (było 270). Jedenaście nowych w
`tests/test_trafikary_na_mapie.py` (osobny plik od `test_traficar.py` —
tamten jest o propozycjach): co w ogóle trafia na mapę, godzina przy aucie
jako dojazd plus marsz tą samą regułą, wybór najwcześniejszego dojścia,
sama odległość w linii prostej bez pól jazdy, brak wpływu na wachlarz
(`segments`/`nodes` identyczne jak bez aut), cisza przy pytaniu o inny dzień,
przy wyłączniku i przy padniętym feedzie. Jeden we froncie, przez emulator:
`test_a_car_is_a_place_on_the_map_not_a_ride` — znacznik powstaje, dymek
mówi godzinę, dojście, skąd i ile do celu, nie mówi nic o jeździe, a liczba
narysowanych kawałków nie drgnęła.

### Dopisek tego samego dnia: „Ogarniam" i ulica, której nikt nie potrzebuje

Dwie poprawki po obejrzeniu warstwy na żywo.

**Ulica postoju wypadła z dymka.** Feed podaje adres („Wrocław, ul.
Łagiewnicka"), ale znacznik i tak stoi dokładnie tam, gdzie auto — nazwa
ulicy nie dodaje do tego nic, czego nie widać. Pole zostaje w odpowiedzi
(propozycje z autem nadal je wypisują), zniknął tylko wiersz na mapie.

**Doszło to, po co naprawdę się na te auta patrzy: program „Ogarniam".**
Traficar płaci zniżką za zatankowanie, posprzątanie albo przestawienie auta,
a `fioletowe.live` to publikuje — pole `discounts`, udokumentowane jako
`CarDiscountV1`, z zamkniętą listą nazw (Tankowanie, Sprzątanie, Relokacja)
i kwotą. Sprawdzone przed napisaniem czegokolwiek: 11 z 44 wolnych aut we
Wrocławiu miało coś do wzięcia (15–30 zł za zadanie), a auto bez nagrody ma
tam `null`, nie pustą listę.

Na mapie: dymek mówi ZA CO i ZA ILE, a gdy nie ma nic — mówi i to. Milczenie
w tym miejscu znaczyłoby naraz „nic tu nie ma" i „nie wiadomo", a to dwie
różne odpowiedzi (ta sama zasada, co przy notce o rowerze). Znacznik auta
z nagrodą dostał złotą obwódkę: przy dziewięciu autach na mapie szukanie tego
jednego płatnego przez najeżdżanie po kolei byłoby pracą, nie informacją.

Kwota idzie z feedu bez przeliczania i bez waluty w źródle — „zł" dokłada
front, bo Traficar jeździ po Polsce. Gdyby feed kiedyś zaczął podawać coś
innego niż złotówki, to jest jedyne miejsce do poprawienia.

Kontrakt (punkt 15) nie był ruszany — mówi o godzinie i odległości, a nie
o tym, co jeszcze auto ma na sobie napisane. Do rozważenia przy najbliższym
przeglądzie punktu, czy nagroda zasługuje na własne zdanie.

**Testy:** 286 (było 282). Trzy nowe w `tests/test_trafikary_na_mapie.py`
(nagroda dociera do mapy, brak nagrody to pusta lista, `null` z feedu zamienia
się w pustą listę) i jeden we froncie: `test_the_car_says_what_is_there_to_earn`
— dymek z kwotami, dymek „nic do wzięcia", złota obwódka tylko przy nagrodzie
i brak ulicy w obu.

## Rower miejski wchodzi na mapę — punkt 16 (2026-09-12)

Punkt 16 kontraktu powstał na końcu, po trzech rundach uwag i dwóch cofniętych
pomysłach — użytkownik wprost poprosił, żeby napisać go dopiero wtedy, gdy
kształt warstwy będzie uzgodniony. Ta sekcja jest zapisem drogi do niego oraz
tego, co w warstwie jest zgadywane.

**Reguła sensu, w jednym zdaniu:** przejazd rowerem zostaje na mapie, jeżeli
po zsiadaniu zdąży się jeszcze WSIĄŚĆ w coś, co mapa RYSUJE — albo dojechać
pod sam cel.

To jest świadome odwrócenie mojej pierwszej propozycji, którą użytkownik
odrzucił. Proponowałem „przejazd ma sens, gdy stawia Cię gdzieś WCZEŚNIEJ, niż
mapa sama Cię tam dowozi" — czyli mierzenie zysku w minutach. Odpowiedź: *„Nic
innego nie musi być szybsze, żeby było uwzględnione. To musi być opcją, która
Ciebie realnie dowiezie w zakres mapy. Może ktoś mieć ochotę na rower zamiast
tramwaju."* I ma rację również formalnie: cała reszta mapy jest odsiewana
oknem (punkt 2), a nie porównaniem z alternatywą. Wyjątek dla roweru byłby
drugą miarą sensowności w jednym rysunku.

Technicznie: `planner.plan_flow` podaje `bikes.map_places` dwie rzeczy i obie
pochodzą z tego, co mapa RYSUJE — `reach` (dokąd dowozi, to samo wejście, co
przy autach) oraz `_drawn_boardings` (najpóźniejsza godzina, o której mapa
pozwala na danym słupku wsiąść w kawałek jadący DALEJ), plus krańce relacji
liczone do deadline'u. Przejazd przeżywa, gdy dla najbliższego słupka przy
stacji docelowej `przyjazd + dojście ≤ board[słupek]`.

**Poprawka, która to naprawiła (ten sam dzień).** Pierwsza wersja brała tu
`latest` ze skanu wstecz i to był błąd dokładnie tej samej klasy, co kiedyś
przy autach: skan zna pół miasta. Użytkownik złapał to na zrzucie — stacja
Piaskowa / św. Ducha, przejazd „otwiera Halę Targową", a z Hali Targowej mapa
nie rysuje ani jednego odjazdu. *„Ale czemu mogę do niej dojechać? I z niej
co?"* Zdążyć na przystanek to nie to samo, co móc z niego pojechać. Skutek
poprawki jest drastyczny: Kozanów → pl. Grunwaldzki o 21:07 miał kilkadziesiąt
kropek, po poprawce ma dwanaście, a wśród otwieranych słupków zostały same
narysowane (PL. GRUNWALDZKI, Katedra, Ogród Botaniczny, Pl. Bema, Reja).

**Dlaczego najbliższy słupek, a nie najlepszy.** Po zsiadaniu idzie się do
najbliższego; dalszy, na który też by się zdążyło, nie jest przez ten przejazd
otwarty *bardziej*. Gdy na najbliższy już się nie zdąży, bierzemy kolejny —
stąd dwa testy obok siebie (`test_otwiera_najblizszy_slupek_na_ktory_sie_zdazy`
i `test_pomija_slupek_na_ktory_sie_nie_zdazy`).

**Tempo — jedna liczba zamiast dwóch.** Warstwa propozycji liczy przejazd jako
14 km/h razy 1,35 krętości miasta. Na mapie to zostało przeliczone na jedną
prędkość po linii prostej: 10 km/h (10,37 zaokrąglone w dół — w tę stronę
zaokrągla się w tym projekcie cały czas nierozkładowy). Powód jest ten sam, co
przy „nie zgadujemy jazdy autem": rozbicie na prędkość × krętość ma sens tylko
tam, gdzie zna się przebieg trasy, a mapa zna wyłącznie odległość w linii
prostej — dwie liczby udawałyby wiedzę, której nie ma. Narzut stały to 2 min
(wypożyczenie w aplikacji, wyjęcie roweru z blokady, a na drugim końcu wpięcie
i potwierdzenie). Pierwotnie wpisałem 5 min, źle odczytawszy wypowiedź
użytkownika; poprawione na jego wskazanie. Skutek był duży — na Kozanowie
liczba kropek wzrosła z 44 do 95, bo w oknie mieści się teraz znacznie więcej
przejazdów. **Obie liczby są od użytkownika i obie są łatwe do zmiany**
(`bikes.MAP_RIDE_MPS`, `bikes.MAP_OVERHEAD_SEC`); model propozycji został
nietknięty, bo to osobna warstwa i osobny priorytet.

Wolno je w ogóle zgadywać z tego samego powodu, z którego wolno zgadywać marsz
(punkt 10): zakaz szacowania dotyczy POJAZDÓW, które mają rozkład. Rower
rozkładu nie ma, więc nie ma czego odczytać.

**Rowery luzem.** Kanał `free_bike_status` istnieje dla `nextbike_pl`
i w chwili pomiaru stało we Wrocławiu 131 rowerów poza stojakami (plus 2629
w stojakach — te trzeba odsiewać po `station_id`, inaczej byłyby policzone
dwa razy). Wchodzą jako POCZĄTEK przejazdu, nigdy jako koniec: za zostawienie
roweru poza stacją operator liczy osobno i dużo. Elektryki rozpoznajemy po
`propulsion_type`, nie po nazwie modelu — nazwy („E-Bike", „e-SMARTbike 2.0
RFID") to marketing operatora, a lista modeli rośnie z każdą dostawą.

**Czego NIE ma, choć wydawało się naturalne.** Nie ma wymogu wolnego stojaka
na stacji docelowej: użytkownik wprost powiedział, że u WRM-u taka sytuacja
po prostu nie występuje. Nie ma progu „przejazd musi coś przyspieszyć" (patrz
wyżej). Zostały dwa progi z warstwy propozycji, bo dotyczą tego, czy to
w ogóle jest przejazd, a nie czy jest opłacalny: dół 500 m (poniżej samo
wypożyczenie trwa dłużej niż marsz) i góra pół godziny pedałowania, czyli
~5 km w linii prostej.

**Ile tego jest (2026-09-12, 17:30).** Kozanów → pl. Grunwaldzki: 95 kropek,
807 przejazdów. Osobowice → Biskupin: 68 i 567. Katedra → Leśnica: 53 i 198.
Wojszyce → Dworzec Główny: zero.

**Sprostowanie do tego zera.** Napisałem najpierw, że „na południu nie ma
stacji WRM". To nieprawda i użytkownik słusznie o to dopytał: poniżej Wojszyc
jest 31 stacji, najdalsza w Siechnicach. Prawdziwy powód jest inny i ciekawszy.
Ta relacja rysuje się jako JEDEN kawałek (autobus 113) o dziesięciu słupkach,
a stacji w promieniu dojścia od nich jest szesnaście — wszystkie dopiero
w centrum, przy samym celu. Do pierwszej z nich dochodzi się o 17:51, a okno
mapy kończy się chwilę później: każdy przejazd wypada za deadline. Zero nie
jest więc faktem o rozmieszczeniu stacji, tylko o tym, że przy celu okno jest
już wyczerpane - i to jest zachowanie poprawne.

Te liczby rozstrzygnęły wygląd: kropki stoją zawsze, kreski przejazdów
pojawiają się **pod kursorem**. Kilkaset kresek naraz zasłania mapę, a dwie
kropki i tak mówią to samo, co kreska między nimi — użytkownik ujął to tak, że
*„jak widzisz kropkę tu i kropkę tam, to nie trzeba rysować kreski, żeby się
domyślić"*. Pod zębatką są dwa przełączniki: „przejazdy widoczne bez
najeżdżania" (do obejrzenia całości) oraz „godziny przejazdu rowerem", która
chowa JEDYNĄ zgadywaną liczbę — godzina „jesteś przy rowerze" pochodzi
z rozkładu i zostaje zawsze.

**Pytanie o inny dzień.** Inaczej niż przy autach: kropki ZOSTAJĄ. Stacja stoi
w tym samym miejscu jutro, więc jej zniknięcie mówiłoby nieprawdę; nieznany
jest sam stan stojaka i dymek pisze to wprost, zamiast podać dzisiejszą liczbę
jako jutrzejszą. Rowery luzem przy takim pytaniu znikają — rower leżący dziś
na chodniku jutro tam nie leży.

**Ślepa uliczka: kropki jako „jeden rodzaj rzeczy".** Po pierwszej rundzie
uwag zrobiłem z każdej stacji równorzędną kropkę, łącznie z tymi, do których
dowozi wyłącznie rower. To było nadinterpretowanie zastrzeżenia użytkownika
i zostało cofnięte: *„Co to w ogóle za stacja, do których żadna komunikacja
nie dowozi i dojeżdżasz do nich rowerem? Nie prosiłem w ogóle o to."* Kropkę
dostaje wyłącznie miejsce, w którym da się WSIĄŚĆ na rower. Prawdziwe
zastrzeżenie („najeżdżam na drugi koniec i chcę rozpatrzyć, że przyjadę tu
komunikacją i stąd pojadę dalej") jest spełnione bez tego: taka stacja i tak
jest na mapie z własnego tytułu, z własnymi przejazdami.

**Bez przełączników.** Trzy pokrętła pod zębatką zostały usunięte i zastąpione
stałymi w `static/app.js`: `BIKE_RIDES_ALWAYS` (kreski tylko pod kursorem)
i `BIKE_RIDE_TIMES` (godzin samego przejazdu nie pokazujemy). Przy drugim
końcu widać SAMĄ odległość — godzina jest policzona, nie odczytana, a
odległość mówi to samo, nie udając rozkładu.

**Do przemyślenia (użytkownik sam to oznaczył).** Co przejazd „otwiera", jest
dziś jednym wierszem: nazwa słupka i godzina. Docelowo ma to wyglądać jak
zawartość kropki przesiadkowej (punkt 11) — z liniami, które stamtąd
odjeżdżają. Osobno: gdy kreski są włączone na stałe, mogłaby być widoczna
tylko najlepsza z każdej stacji, a wszystkie dopiero pod kursorem.

**Testy** (23 nowe w `tests/test_rowery_na_mapie.py`, 2 w
`tests/js/checks.js`): co trafia na mapę i czym jest odsiewane, że przejazd nie
musi być szybszy niż tramwaj, skąd bierze się każda godzina, oba progi
długości przejazdu, rowery luzem jako początek i nie-koniec, pytanie o inny
dzień, nietykalność wachlarza, milczący kanał i wyłącznik, oraz czytanie
kanału (elektryki po rodzaju napędu, rower w stojaku nie jest „luzem").
Doszedł też fixture wyciszający kanał WRM w całym zestawie — warstwa mapy
sięga po niego przy KAŻDYM wyszukaniu, nie tylko przy odhaczonym rowerze.
Razem 311, było 286.
