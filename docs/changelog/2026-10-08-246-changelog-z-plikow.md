- **2026-10-08** — **changelog z plików, po jednym na PR** (zgłoszenie
  #246). Każdy PR dopisywał wpis na górę wspólnego changelogu
  w `docs/PROJECT.md`, więc dwa równoległe PR-y prawie zawsze kłóciły się
  przy merge'u. Na dzień zmiany wszystkie pięć otwartych PR-ów miało konflikt
  z `main` i za każdym razem był to wyłącznie changelog. Teraz każdy PR
  dodaje własny plik w `docs/changelog/`, a osobne pliki nie mają o co się
  kłócić. Dotychczasowa historia zostaje w `docs/PROJECT.md` bez zmian,
  zamknięta. Automatyczne sklejanie obu stron przy merge'u odpadło: przycisk
  GitHuba go nie stosuje, a na starej gałęzi po cichu zdublowało wiersze
  tabeli.
