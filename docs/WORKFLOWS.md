# Workflow GitHuba

Krótka mapa automatów w `.github/workflows/` i zasad, których trzeba pilnować
przy dokładaniu nowych. Szczegóły i uzasadnienia siedzą w komentarzach na
początku każdego pliku. Jak z nich korzystać na co dzień i kiedy celowo
milczą, opisuje [CONTRIBUTING.md](../CONTRIBUTING.md).

## Co jest

| Plik | Co robi |
|---|---|
| `claude-review-pr.yml` | Ocena PR-a przez Claude'a przy otwarciu i przy każdym wyjściu z wersji roboczej; wynik nowym komentarzem i pingiem autora; nowa ocena zwija starsze do samego werdyktu. Ocena jest przyrostowa: zaczyna od tego, co w zmianach PR-a doszło od commitu poprzedniej oceny, a Claude sam decyduje, czy przejrzeć cały PR. Po ocenie workflow rozstrzyga o zatwierdzeniach ludzi — zostają albo są cofane (pkt 13). Kształt oceny: `.claude/commands/review-pr.md`, reguły zatwierdzeń: `.github/scripts/approvals.sh`. |
| `claude-issue.yml` | Solver: `@claude` pod zgłoszeniem, praca na gałęzi `claude/issue-<numer>`. |
| `issue-labels.yml` | Etykiety stanu na zgłoszeniach wskazanych przez PR i zapasowe zamykanie po merge'u. Które zapisy (`Closes`, `Refs`, gałąź `claude/issue-N`…) liczą się jako wskazanie, rozstrzyga `.github/scripts/refs.sh`, wspólny z `pr-syntax.yml`. |
| `pr-syntax.yml` | „PR-y: składnia” (nazwy nie zmieniaj — można jej słuchać przez `workflow_run`). PR do `main` albo piętrowy (wydania do `stable` i PR-y botów pomija) musi wskazywać zgłoszenie tak, jak rozpoznaje to `issue-labels.yml`, a każdy wskazany numer musi być zgłoszeniem z autorem PR-a wśród assignees; etykieta `No Issue` zwalnia ze sprawdzenia. Inaczej: etykieta `wrong syntax`, komentarz z regułą, która nie przeszła, ping autora na Discordzie, a gotowy PR wraca do wersji roboczej. Komentarz tylko przy wejściu w błąd, zmianie powodu albo cofnięciu do wersji roboczej. Po poprawce etykieta schodzi, komentarz się zwija. Słucha też przypisań w zgłoszeniach. Potrzebuje `contents: write` (przestawienie na wersję roboczą, #417) — bezpieczne, bo kodu z PR-a nie pobiera. Znany wyścig: ocena Claude'a przy otwarciu i „Ready for review” rusza równolegle, więc PR cofnięty tu do wersji roboczej może jeszcze dostać ocenę. Logika w `.github/scripts/syntax.sh` (z `DRY_RUN=1`), szablon opisu w `.github/pull_request_template.md`. |
| `issue-triage.yml` | Etykieta `external` dla autorów spoza zespołu. |
| `stale-issues.yml` | Przypomnienie po 14 dniach ciszy, zamknięcie po kolejnych 7. Zablokowanych nie rusza. |
| `discord.yml` | Kanał repo na Discordzie z prawdziwymi pingami. |
| `pr-conflicts.yml` | Po pushu do `main`: autor otwartego PR-a (także wersji roboczej), który ten push wpędził w konflikt z `main`, dostaje ping na Discordzie — raz na konflikt, nie po każdym kolejnym merge'u. Czekające prośby o recenzję takiego PR-a są zdejmowane, a gotowy PR wraca do wersji roboczej. Logika w `.github/scripts/conflicts.sh`. |
| `pr-drafts.yml` | Gotowy PR do `main` nie dostaje zmian: push, który zmienia jego zmiany względem `main`, albo konflikt z `main` przy otwarciu i przy „Ready for review” przestawia go na wersję roboczą, z komentarzem o powodzie i zdjęciem próśb o recenzję. Push, który tylko dociąga `main` (diff PR-a bez zmian), zostawia go gotowym. Wyjście z wersji roboczej uruchamia ponowną ocenę Claude'a. Logika w `.github/scripts/drafts.sh`. |
| `daily-digest.yml` | Co rano na Discordzie: postęp z wczoraj po karcie na osobę, potem karta „Ogólnie” z tym, co do zrobienia, i liczbami. |
| `tests.yml` | Testy, odpalane wyłącznie ręcznie. |

## Discord

1. Kanał zasila `discord.yml`. We wbudowanym webhooku GitHuba (Settings →
   Webhooks) są odznaczone Issues, Issue comments, Pull requests, Pull request
   reviews i Pull request review comments — te zdarzenia wysyłamy sami. Resztę
   (wydania, gwiazdki, forki…) dalej ogłasza wbudowany.
2. **Komentarze i akcje botów nie trafiają na Discorda same.** `discord.yml`
   pomija wpisy botów, a o tym, co workflow zrobi swoim tokenem
   (`GITHUB_TOKEN`), GitHub innym workflow w ogóle nie mówi. Każdy nowy ważny
   komentarz albo akcja bota musi więc sama wysłać wiadomość przez
   `.github/scripts/discord.sh` — tak jak robią to dziś ocena Claude'a, solver,
   stale, zapasowe zamykanie, ping o nowy konflikt z `main` i ping o błędnej
   składni PR-a. Zasługa
   za zgłoszenie zamknięte naszym tokenem nie idzie na bota: od wyłączenia
   automatycznego zamykania w ustawieniach repo (#228) zamyka je zapasowy
   krok, a GitHub nie zapisuje wtedy PR-a jako zamykającego. Wiadomość
   z zapasowego kroku podpisuje ten, kto zmergował PR, a podsumowanie dnia —
   autor zmergowanego PR-a powiązanego ze zgłoszeniem.
3. Skrypt pobieramy zawsze z `main` (sparse checkout `.github/scripts` do
   `.discord`), nigdy z drzewa PR-a ani pracy Claude'a: dostaje w env sekret
   webhooka.
4. Sprawdzanie, kto może zawołać Claude'a (cytat, prawo zapisu), zwijanie zapowiedzi i zwijanie starszych ocen siedzą w jednym miejscu: `.github/scripts/guard.sh`. Workflow pobierają go z `main` tak samo jak skrypt Discorda; nie kopiuj tej logiki do workflow. Starsze oceny nie są zwijane przez GitHuba, tylko edytowane (werdykt na wierzchu, reszta pod „rozwiń”), a informacja o zmienionym workflow oceny i instrukcja „zatwierdzenie nadal obowiązuje” zwijają się w całości jak zapowiedź; edycja nie wysyła powiadomień ani nie trafia na Discorda, bo żaden workflow nie słucha edycji komentarzy. Pod udaną oceną, obok `<!-- claude-review -->`, stoi `<!-- claude-review-sha: … -->` z ocenianym commitem — po nim następna ocena poznaje, od czego liczyć zmiany (`last_review`). Informacja o porażce go nie niesie.
5. W treści `@login` znaczy ping (dla osób z mapy), `**login**` — samo
   wymienienie bez pingu.
6. Konfiguracja: sekret `DISCORD_WEBHOOK`, zmienna repo `DISCORD_IDS` (JSON
   login GitHuba → ID z Discorda). Nowa osoba w zespole = nowy wpis w zmiennej.
   Podsumowanie dnia idzie osobnym sekretem `DISCORD_WEBHOOK_DIGEST` na kanał
   #metal-statystyki, żeby nie przykrywało zdarzeń, na które ktoś czeka.

## Zasady przy pisaniu workflow

7. Komentarze po polsku i tylko o tym, *dlaczego*; logika w bashu z `gh` i `jq`.
8. Wszystko, co piszą ludzie (tytuły, opisy, komentarze), wchodzi do skryptu
   przez `env`, nigdy przez `${{ }}` wklejone w bash.
9. `pull_request_target` tylko bez checkoutu kodu z PR-a.
10. `claude-review-pr.yml` ma kopię na `stable`, która wjeżdża tam z każdym
   wydaniem — nie edytuj jej osobno.
11. Akcja Claude'a uruchamia się tylko z workflow identycznym jak na `main`.
    PR, który zmienia `claude-review-pr.yml`, nie dostaje więc oceny przy
    otwarciu (zamiast niej idzie informacja); ocenia go `@claude`, a samą
    zmianę workflow widać w działaniu dopiero po merge'u.
12. Wersja robocza to zwykły etap PR-a, nie powód do milczenia: gotowy PR wraca do niej przy każdej zmianie (`pr-drafts.yml`, `pr-conflicts.yml`) i przy błędnej składni (`pr-syntax.yml`, który sprawdza też wersje robocze). Nowy workflow, który pomija wersje robocze, musi mieć ku temu powód — dziś pomijają je tylko ocena Claude'a (przychodzi po „Ready for review”), etykiety stanu zgłoszeń i pingi w podsumowaniu dnia. Przestawienie robione naszym tokenem nie wywołuje `converted_to_draft` w innych workflow.
13. Zatwierdzeń ludzi nie zdejmuje GitHub, tylko `claude-review-pr.yml`: w regułach `main` „Dismiss stale pull request approvals when new commits are pushed” jest wyłączone, bo zdejmowało zatwierdzenie także przy samym dociągnięciu bazy albo rozwiązaniu konfliktu. Po każdej ocenie (i po nieudanej ocenie) workflow bierze zatwierdzenia, które stoją — ostatnia recenzja zmieniająca stan danej osoby to zatwierdzenie, boty się nie liczą — i:
    - zostawia je, gdy Claude ocenił zmiany od najnowszego z nich jako małe, werdykt jest 🟢 i nikt nie ma czekającej prośby o zmiany (prośbę znosi tylko późniejsze zatwierdzenie tej samej osoby albo jej cofnięcie); autor dostaje wtedy osobny komentarz z trzema wyjściami: wersja robocza, merge, prośba o ponowne review,
    - w każdym innym razie cofa wszystkie, z powodem w wiadomości cofnięcia. Brak odpowiedzi Claude'a o wielkości zmian, commit zatwierdzenia nie do pobrania i nieudana ocena liczą się jak duże zmiany; zatwierdzenie na ocenianym commicie nie ma zmian do oceny.

    Wynik dopisuje się do pingu z oceną na Discordzie. Cofanie wymaga, żeby `github-actions` mógł cofać recenzje (bez ograniczeń „kto może cofać” w regułach `main`); nieudane cofnięcie czerwieni bieg. Logika i jej opis: `.github/scripts/approvals.sh`, pobierany z `main`.
