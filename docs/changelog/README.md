# Changelog — jeden plik na PR

1. Każdy PR dodaje tu własny plik
   `RRRR-MM-DD-<numer zgłoszenia>-<krótki-opis>.md`, np.
   `2026-10-08-246-changelog-z-plikow.md`. Data to dzień wpisu, opis to
   kilka słów małymi literami, rozdzielonych myślnikami. Bez zgłoszenia
   numer się pomija.
2. W środku jeden wpis w stylu dotychczasowego changelogu
   w [PROJECT.md](../PROJECT.md#changelog): `- **data** — **co się zmieniło**
   (zgłoszenie #N).`, a dalej co było źle, co jest teraz i dlaczego tak.
   Kolejne poprawki w tym samym PR-ze zmieniają ten sam plik.
3. Nazwy zaczynają się od daty, więc lista plików od końca to changelog od
   najnowszych zmian.
4. Historia sprzed 2026-10-08 zostaje w changelogu
   [PROJECT.md](../PROJECT.md#changelog). Jest zamknięta: czyta się ją, ale nie
   dopisuje i nie poprawia.
5. Osobne pliki są po to, żeby równoległe PR-y nie kłóciły się przy merge'u
   (zgłoszenie #246).
