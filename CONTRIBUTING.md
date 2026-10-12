# Jak pracujemy

Wspólny sposób pracy na GitHubie: branche, pull requesty, review Claude'a, issues i Discord. Jak automaty są zbudowane i czego pilnować przy ich zmienianiu, opisuje [docs/WORKFLOWS.md](docs/WORKFLOWS.md).

## Branche

1. `main` to wersja testowa (https://metal-testing.sze.one/), `stable` — produkcja (https://metal.sze.one/).
2. Każda funkcja dostaje własny branch w konwencji `login/opis`, np. `Egorex/DiscordKarty` albo `se/keep-typed-destination`.

## Pull requesty

### Tworzenie

3. Zanim otworzysz PR, zmerguj aktualny `main` do swojego brancha albo zrób rebase brancha na `main`.
4. Wciąż ustalacie, co zrobić albo jak to ma wyglądać — wypchnij sam branch, bez PR-a, i rozmawiajcie w issue. Kod jest tylko szkicem.
5. Potrzebujesz oczu na kodzie — otwórz PR jako draft.
6. Gotowe zmiany idą zwykłym pull requestem do `main`.
7. „Co aplikacja ma robić” omawiamy w issue — widzą to wszyscy, którzy je obserwują, i zostaje po PR-ze. „Czy ten kod jest dobry” — w PR-ze, przy liniach kodu.

### Review i mergowanie

8. `Closes #12` (albo `Fixes`, `Resolves`) w opisie PR-a znaczy „ten PR załatwia issue”: po merge'u issue zamyka się samo. `Refs #12` (albo `Ref`, `Related to`, `Part of`, `Dotyczy`) to luźniejszy związek — issue dostaje label, ale zamyka je człowiek. Gołe `#12` niczego nie oznacza, więc można pisać o innych issues bez obaw.
9. Zaraz po otwarciu pod PR-em pojawia się jednolinijkowa zapowiedź, że Claude go czyta, a po kilku minutach review: werdykt, podsumowanie, uwagi do kodu. Zapowiedź zwija się, gdy review już stoi. Dostajesz ping na GitHubie i na Discordzie.
10. PR otwarty w konflikcie dostaje review Claude'a dopiero przy pierwszym pushu, który konflikt usuwa.
11. Review leci raz na PR. Po większych poprawkach albo gdy się nie udało, napisz `@claude` w komentarzu pod PR-em.
12. Przeczytaj review Claude'a, popraw, co trzeba, i dopiero wtedy poproś o review w panelu bocznym („Reviewers”). Reviewer dostaje ping na Discordzie.
13. Kto pilnuje których plików, wyznacza `.github/CODEOWNERS` — to jedyne źródło prawdy. Gdy PR dotyka pliku z ownerem, GitHub sam dodaje go do reviewerów.
14. PR merguje jego autor. Można używać automerge.

## Release

15. Gdy zmiany na `main` są zebrane i przetestowane na wersji testowej, pull request z `main` do `stable` tworzy nowy release. Taki PR też dostaje review Claude'a.

## Issues

16. `@claude` w treści nowego issue albo w komentarzu pod nim wysyła Claude'a do pracy: czyta wątek, robi poprawkę, uruchamia testy i wypycha ją na branch `claude/issue-<numer>`. Na starcie wstawia jednolinijkową zapowiedź, a na końcu nowy komentarz z odpowiedzią i linkiem, który otwiera PR do `main` z gotowym `Closes` (zapowiedź wtedy się zwija). Kolejne `@claude` w tym samym wątku dokładają się do tego samego brancha i tego samego PR-a.
17. Claude bierze się tylko za zadania jasne i niewielkie. Przy niejasnym issue zamiast poprawki zadaje pytanie. Nie zmienia automatów, sekretów ani uprawnień — to zawsze robi człowiek.
18. Koniec pracy Claude'a ogłasza ping na Discordzie, który prowadzi do komentarza z odpowiedzią. Issue, którym się zajmował, dostaje label `claude` — to sam znacznik, niczego nie uruchamia.
19. Bycie assignee w issue znaczy, że nad nim pracujesz. Gdy issue ma kilku assignees, ustalają między sobą, jak dzielą pracę, żeby nie robić tego samego dwa razy.
20. Gdy przestajesz pracować nad issue, a go nie zamykasz (np. zmiana planów), usuń się z jego assignees.
21. „Zgłoś problem” w aplikacji (klik w ikonę → „O aplikacji”) otwiera nowe issue z wpisaną wersją, urządzeniem i adresem strony. Issues od osób spoza zespołu dostają label `external`. Żeby zgłosić, trzeba mieć konto na GitHubie.

### Labels

22. Labele stanu nadają się same: `waiting for PR review` przy PR-ze z `Closes`, `PR created` przy `Refs`, `merged - is it ok?` po merge'u takiego PR-a. Ten ostatni zdejmuje ten, kto issue zamyka.
23. Issue bez niczyjego wpisu przez 14 dni dostaje pytanie, czy temat jest aktualny, i label `stale`. Po kolejnych 7 dniach ciszy zamyka się jako „not planned”. Żeby issue zostało otwarte, nie wystarczy odpisać — napisz, dlaczego ma zostać, choć nikt go nie ruszał przez 14 dni. Nie dotyczy issues z labelem `blocked` ani takich, które mają otwarte sub-issues.
24. Issue, które stoi przez coś poza naszym wpływem, dostaje label `blocked`, a w issue trzeba napisać, na co czeka. Dając `blocked`, zdejmij `stale` — te dwa labele nie powinny stać razem, a automat sam tego nie posprząta.
25. `to discuss` znaczy, że autor nie może albo nie chce rozstrzygnąć sprawy sam i chce ją omówić z zespołem. Każdy z zespołu jest zachęcany, żeby dorzucić swoje zdanie. Label zdejmujemy, gdy dyskusję uznamy za zakończoną.
26. `to revisit` dajemy przy zamykaniu issues, które teraz wykraczają poza zakres, ale są dobrym pomysłem na przyszłość. Dzięki niemu można je zamknąć, żeby nie poszerzały listy otwartych issues, a wciąż da się je odróżnić od innych zamkniętych issues.

### Views

27. „Open for taking” otwierasz, gdy chcesz wziąć coś nowego.
28. „Todo” to twoja lista. Tego nikt za ciebie nie zrobi.
29. „Open discussions” to lista issues, dla których konwersacja ciągle trwa.

## Discord

30. Kanał repo dostaje issues, PR-y, assigny, review i komentarze ludzi — z prawdziwymi pingami osób, których dotyczą.
31. Co rano osobny kanał dostaje podsumowanie dnia: kartę każdej osoby z jej wczorajszym postępem (zmergowane PR-y, zamknięte i założone issues) oraz kartę „Ogólnie” z tym, co jest do zrobienia, linkami do „Todo”, „Open for taking” i „Open discussions” oraz liczbami issues i PR-ów.
32. Podsumowanie pinguje tylko tam, gdzie na kogoś czeka ruch: reviewerów otwartych PR-ów (albo autora, który jeszcze nie poprosił o review), autora issue z `merged - is it ok?` i assignees issues ze `stale` (bez assignee — autora). Drafty są bez pingu.
33. W komentarzu na GitHubie `@login` pinguje tę osobę także na Discordzie, `**login**` tylko ją wymienia.
34. Nowa osoba w zespole potrzebuje wpisu w mapie kont GitHub → Discord (zmienna repozytorium `DISCORD_IDS`), inaczej nie dostaje pingów.

## Kiedy automat celowo milczy

Zanim uznasz ciszę za błąd, sprawdź tę listę. Każdy punkt to zamierzone zachowanie albo ograniczenie GitHuba, a nie awaria.

35. **Draft PR nie dostaje review.** Review przychodzi po „Ready for review”. Wcześniej tylko na wyraźne `@claude`.
36. **PR do brancha innego niż `main` i `stable` nie dostaje review.** Dostanie je tylko po `@claude` albo po ręcznym biegu.
37. **PR otwarty w konflikcie z bazą dostaje review dopiero przy pierwszym pushu, który ten konflikt usuwa.** Przy konflikcie GitHub w ogóle nie uruchamia automatów PR-a, więc w Actions nie ma nawet śladu biegu.
38. **PR z forka nie dostaje review.** Kod spoza repo nie trafia do Claude'a razem z sekretami.
39. **PR, który zmienia sam workflow review, nie dostaje review przy otwarciu.** Akcja Claude'a działa tylko z workflow takim jak na `main`. Pod PR-em pojawia się o tym informacja — napisz `@claude`, a review ruszy.
40. **`@claude` działa tylko we własnym tekście i tylko od osoby z prawem zapisu.** Wołanie przepisane w cytacie albo od kogoś bez zapisu jest pomijane bez komentarza.
41. **Zmiana workflow na branchu nie działa dla `@claude` ani ręcznego biegu, aż trafi na `main`.** Te wyzwalacze zawsze czytają wersję z `main`.
42. **To, co robią automaty, nie trafia na Discorda samo.** Wiadomość idzie tylko wtedy, gdy automat wysyła ją sam — dziś robią to review Claude'a, koniec pracy Claude'a nad issue, codzienne podsumowanie, przypomnienie i zamknięcie po ciszy.
43. **Ręczny ratunek:** Actions → „Claude - oceń PR” → Run workflow → numer PR-a robi review od nowa w każdym z powyższych przypadków poza forkiem.

Aktualizacja: 12-10-2026
