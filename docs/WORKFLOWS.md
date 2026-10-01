# Workflow GitHuba

Krótka mapa automatów w `.github/workflows/` i zasad, których trzeba pilnować
przy dokładaniu nowych. Szczegóły i uzasadnienia siedzą w komentarzach na
początku każdego pliku.

## Co jest

| Plik | Co robi |
|---|---|
| `claude-review-pr.yml` | Ocena każdego nowego PR-a przez Claude'a; wynik nowym komentarzem i pingiem autora. Kształt oceny: `.claude/commands/review-pr.md`. |
| `claude-issue.yml` | Solver: `@claude` pod zgłoszeniem, praca na gałęzi `claude/issue-<numer>`. |
| `issue-labels.yml` | Etykiety stanu na zgłoszeniach wskazanych przez PR i zapasowe zamykanie po merge'u. |
| `stale-issues.yml` | Przypomnienie po 14 dniach ciszy, zamknięcie po kolejnych 7. |
| `discord.yml` | Kanał repo na Discordzie z prawdziwymi pingami. |
| `cleanup-skipped.yml` | Sprząta z zakładki Actions biegi pominięte przez warunki. |
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
   stale i zapasowe zamykanie.
3. Skrypt pobieramy zawsze z `main` (sparse checkout `.github/scripts` do
   `.discord`), nigdy z drzewa PR-a ani pracy Claude'a: dostaje w env sekret
   webhooka.
4. W treści `@login` znaczy ping (dla osób z mapy), `**login**` — samo
   wymienienie bez pingu.
5. Konfiguracja: sekret `DISCORD_WEBHOOK`, zmienna repo `DISCORD_IDS` (JSON
   login GitHuba → ID z Discorda). Nowa osoba w zespole = nowy wpis w zmiennej.

## Zasady przy pisaniu workflow

6. Komentarze po polsku i tylko o tym, *dlaczego*; logika w bashu z `gh` i `jq`.
7. Wszystko, co piszą ludzie (tytuły, opisy, komentarze), wchodzi do skryptu
   przez `env`, nigdy przez `${{ }}` wklejone w bash.
8. `pull_request_target` tylko bez checkoutu kodu z PR-a.
9. Workflow z wyzwalaczem `issue_comment` (albo innym, który często kończy się
   pominięciem) dopisz do listy w `cleanup-skipped.yml`.
10. `claude-review-pr.yml` ma kopię na `stable`, która wjeżdża tam z każdym
    wydaniem — nie edytuj jej osobno.
