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
  której to się opiera)
- **4** — `test_dead_end_branch_never_appears`
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
