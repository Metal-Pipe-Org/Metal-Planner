# Notatki do kontraktu mapy przepływów

Historia wdrożeń, audytów, napraw i otwarte pytania dla
[FLOW_MAP_CONTRACT.md](FLOW_MAP_CONTRACT.md). **Ten plik ma rosnąć** —
tu lądują dopiski przy każdej zmianie dotykającej kontrakt, żeby sam
kontrakt zostawał krótki. Numeracja punktów odpowiada numeracji w
kontrakcie.

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
