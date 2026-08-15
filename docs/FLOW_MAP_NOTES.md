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
- **7** — brak automatycznego testu (zachowanie frontendu — najechanie na
  linię, także tam gdzie kilka nakłada się w tym samym miejscu, pokazuje
  ich numery; wymagałoby testu w przeglądarce). Zweryfikowane czytaniem
  kodu.
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

