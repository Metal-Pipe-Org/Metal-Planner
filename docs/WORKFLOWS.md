# Workflow GitHuba

Krótka mapa automatów w `.github/workflows/` i zasad, których trzeba pilnować
przy dokładaniu nowych. Szczegóły i uzasadnienia siedzą w komentarzach na
początku każdego pliku. Jak z nich korzystać na co dzień i kiedy celowo
milczą, opisuje [CONTRIBUTING.md](../CONTRIBUTING.md).

## Co jest

| Plik | Co robi |
|---|---|
| `claude-review-pr.yml` | Ocena PR-a przez Claude'a przy otwarciu i przy każdym wyjściu z wersji roboczej; wynik nowym komentarzem i pingiem autora; nowa ocena zwija starsze do samego werdyktu. Kształt oceny: `.claude/commands/review-pr.md`. |
| `claude-issue.yml` | Solver: `@claude` pod zgłoszeniem, praca na gałęzi `claude/issue-<numer>`. |
| `issue-labels.yml` | Etykiety stanu na zgłoszeniach wskazanych przez PR i zapasowe zamykanie po merge'u. |
| `issue-triage.yml` | Etykieta `external` dla autorów spoza zespołu. |
| `stale-issues.yml` | Przypomnienie po 14 dniach ciszy, zamknięcie po kolejnych 7. Zablokowanych nie rusza. |
| `discord.yml` | Kanał repo na Discordzie z prawdziwymi pingami. |
| `pr-conflicts.yml` | Po pushu do `main`: autor otwartego PR-a (także wersji roboczej), który ten push wpędził w konflikt z `main`, dostaje ping na Discordzie — raz na konflikt, nie po każdym kolejnym merge'u. Czekające prośby o recenzję takiego PR-a są zdejmowane, a gotowy PR wraca do wersji roboczej. Logika w `.github/scripts/conflicts.sh`. Token z `contents: write` — kodu z PR-a nie uruchamia (zasada 9). |
| `pr-drafts.yml` | Gotowy PR do `main` nie dostaje zmian: push, który zmienia jego zmiany względem `main`, albo konflikt z `main` przy otwarciu i przy „Ready for review” przestawia go na wersję roboczą, z komentarzem o powodzie i zdjęciem próśb o recenzję. Push, który tylko dociąga `main` (diff PR-a bez zmian), zostawia go gotowym. Wyjście z wersji roboczej uruchamia ponowną ocenę Claude'a. Logika w `.github/scripts/drafts.sh`. Token z `contents: write` — kodu z PR-a nie uruchamia (zasada 9). |
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
   stale, zapasowe zamykanie i ping o nowy konflikt z `main`. Zasługa
   za zgłoszenie zamknięte naszym tokenem nie idzie na bota: od wyłączenia
   automatycznego zamykania w ustawieniach repo (#228) zamyka je zapasowy
   krok, a GitHub nie zapisuje wtedy PR-a jako zamykającego. Wiadomość
   z zapasowego kroku podpisuje ten, kto zmergował PR, a podsumowanie dnia —
   autor zmergowanego PR-a powiązanego ze zgłoszeniem.
3. Skrypt pobieramy zawsze z `main` (sparse checkout `.github/scripts` do
   `.discord`), nigdy z drzewa PR-a ani pracy Claude'a: dostaje w env sekret
   webhooka.
4. Sprawdzanie, kto może zawołać Claude'a (cytat, prawo zapisu), zwijanie zapowiedzi i zwijanie starszych ocen siedzą w jednym miejscu: `.github/scripts/guard.sh`. Workflow pobierają go z `main` tak samo jak skrypt Discorda; nie kopiuj tej logiki do workflow. Starsze oceny nie są zwijane przez GitHuba, tylko edytowane (werdykt na wierzchu, reszta pod „rozwiń”), a informacja o zmienionym workflow oceny zwija się w całości jak zapowiedź; edycja nie wysyła powiadomień ani nie trafia na Discorda, bo żaden workflow nie słucha edycji komentarzy.
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
9. `pull_request_target` tylko bez checkoutu kodu z PR-a. Kod z PR-a nigdy
   się nie wykonuje: skrypty idą z `main` (sparse checkout,
   `persist-credentials: false`), a czubek PR-a co najwyżej pobieramy jako
   obiekty gita do odczytu (`merge-tree`, `diff`, `patch-id`). Szczególnie
   pilnuj tego w `pr-drafts.yml` i `pr-conflicts.yml`: mają `contents: write`,
   bo bez niego GraphQL `convertPullRequestToDraft` odmawia przestawienia
   PR-a na wersję roboczą (#417), a z takim tokenem kod z PR-a mógłby
   pushować do gałęzi PR-ów (`main` chroni ruleset).
10. `claude-review-pr.yml` ma kopię na `stable`, która wjeżdża tam z każdym
   wydaniem — nie edytuj jej osobno.
11. Akcja Claude'a uruchamia się tylko z workflow identycznym jak na `main`.
    PR, który zmienia `claude-review-pr.yml`, nie dostaje więc oceny przy
    otwarciu (zamiast niej idzie informacja); ocenia go `@claude`, a samą
    zmianę workflow widać w działaniu dopiero po merge'u.
12. Wersja robocza to zwykły etap PR-a, nie powód do milczenia: gotowy PR wraca do niej przy każdej zmianie (`pr-drafts.yml`, `pr-conflicts.yml`). Nowy workflow, który pomija wersje robocze, musi mieć ku temu powód — dziś pomijają je tylko ocena Claude'a (przychodzi po „Ready for review”), etykiety stanu zgłoszeń i pingi w podsumowaniu dnia. Przestawienie robione naszym tokenem nie wywołuje `converted_to_draft` w innych workflow.
