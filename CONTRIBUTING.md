# Jak pracujemy

Wspólny sposób pracy na GitHubie: gałęzie, pull requesty, ocena Claude'a,
zgłoszenia i Discord. Jak automaty są zbudowane i czego pilnować przy ich
zmienianiu, opisuje [docs/WORKFLOWS.md](docs/WORKFLOWS.md).

## Gałęzie

1. `main` to wersja testowa (https://metal-testing.sze.one/), `stable` —
   produkcja (https://metal.sze.one/).
2. Każda funkcja dostaje własną gałąź w konwencji `login/opis`, np.
   `Egorex/DiscordKarty` albo `se/keep-typed-destination`.
3. Zanim otworzysz PR, zmerguj aktualny `main` do swojej gałęzi albo zrób
   rebase gałęzi na `main`. PR otwarty w konflikcie dostaje ocenę Claude'a
   dopiero przy pierwszym pushu, który konflikt usuwa.

## Pull requesty

4. Gotowe zmiany idą pull requestem do `main`.
5. `Closes #12` (albo `Fixes`, `Resolves`) w opisie PR-a znaczy „ten PR
   załatwia zgłoszenie": po merge'u zgłoszenie zamyka się samo.
   `Refs #12` (albo `Ref`, `Related to`, `Part of`, `Dotyczy`) to
   luźniejszy związek — zgłoszenie dostaje etykietę, ale zamyka je człowiek.
   Gołe `#12` niczego nie oznacza, więc można pisać o innych zgłoszeniach
   bez obaw.
6. PR, który nie jest jeszcze gotowy, otwieraj jako wersję roboczą. Ocena
   przychodzi dopiero po „Ready for review".
7. Zaraz po otwarciu pod PR-em pojawia się jednolinijkowa zapowiedź, że Claude
   go czyta, a po kilku minutach ocena: werdykt, podsumowanie, uwagi do kodu.
   Zapowiedź zwija się, gdy ocena już stoi. Dostajesz ping na GitHubie
   i na Discordzie.
8. Ocena leci raz na PR. Po większych poprawkach albo gdy się nie udała,
   napisz `@claude` w komentarzu pod PR-em.
9. Przeczytaj ocenę, popraw, co trzeba, i dopiero wtedy poproś o recenzję
   w panelu bocznym („Reviewers"). Recenzent dostaje ping na Discordzie.
10. PR merguje jego autor.

## Wydanie

11. Gdy zmiany na `main` są zebrane i przetestowane na wersji testowej,
    pull request z `main` do `stable` tworzy nowe wydanie. Taki PR też
    dostaje ocenę Claude'a.

## Zgłoszenia

12. `@claude` w treści nowego zgłoszenia albo w komentarzu pod nim wysyła
    Claude'a do pracy: czyta wątek, robi poprawkę, uruchamia testy i wypycha
    ją na gałąź `claude/issue-<numer>`. Na starcie wstawia jednolinijkową
    zapowiedź, a na końcu nowy komentarz z odpowiedzią i linkiem, który
    otwiera PR do `main` z gotowym `Closes` (zapowiedź wtedy się zwija). Kolejne `@claude` w tym samym
    wątku dokładają się do tej samej gałęzi i tego samego PR-a.
13. Claude bierze się tylko za zadania jasne i niewielkie. Przy niejasnym
    zgłoszeniu zamiast poprawki zadaje pytanie. Nie zmienia automatów,
    sekretów ani uprawnień — to zawsze robi człowiek.
14. Koniec pracy Claude'a ogłasza ping na Discordzie, który prowadzi do komentarza z odpowiedzią. Zgłoszenie, którym się
    zajmował, dostaje etykietę `claude` — to sam znacznik, niczego nie
    uruchamia.
15. Etykiety stanu nadają się same: `waiting for PR review` przy PR-ze
    z `Closes`, `PR created` przy `Refs`, `merged - is it ok?` po
    merge'u takiego PR-a. Tę ostatnią zdejmuje ten, kto zgłoszenie zamyka.
16. „Zgłoś problem” w aplikacji (klik w ikonę → „O aplikacji”) otwiera nowe
    zgłoszenie z wpisaną wersją, urządzeniem i adresem strony. Zgłoszenia od osób spoza zespołu dostają etykietę
    `external`. Żeby zgłosić, trzeba mieć konto na GitHubie.
17. Zgłoszenie bez niczyjego wpisu przez 14 dni dostaje pytanie, czy temat
    jest aktualny, i etykietę `stale`. Po kolejnych 7 dniach ciszy zamyka się
    jako „not planned". Żeby zgłoszenie zostało otwarte, nie wystarczy
    odpisać — napisz, dlaczego ma zostać, choć nikt go nie ruszał przez
    14 dni. Nie dotyczy zgłoszeń z etykietą `blocked` ani takich, które mają
    otwarte pod-zgłoszenia.
18. Zgłoszenie, które stoi przez coś poza naszym wpływem, dostaje etykietę
    `blocked`, a w zgłoszeniu trzeba napisać, na co czeka. Dając `blocked`,
    zdejmij `stale` — te dwie etykiety nie powinny stać razem, a automat sam
    tego nie posprząta.
19. `to discuss` znaczy, że autor nie może albo nie chce rozstrzygnąć sprawy
    sam i chce ją omówić z zespołem. Każdy z zespołu jest zachęcany, żeby
    dorzucić swoje zdanie. Etykietę zdejmujemy, gdy dyskusję uznamy
    za zakończoną.
20. `to revisit` dajemy przy zamykaniu zgłoszeń, które teraz wykraczają poza
    zakres, ale są dobrym pomysłem na przyszłość. Dzięki niej można je
    zamknąć, żeby nie zawyżały listy otwartej pracy, a wciąż da się je
    odróżnić od innych zamkniętych zgłoszeń.
21. Przypisanie do zgłoszenia znaczy, że nad nim pracujesz. Gdy przypisanych jest
    kilka osób, ustalają między sobą, jak dzielą pracę, żeby nie robić
    tego samego dwa razy.
22. Gdy przestajesz pracować nad zgłoszeniem, a go nie zamykasz (np. zmiana
    planów), wypisz się z niego.
23. „Open for taking” otwierasz, gdy chcesz wziąć coś nowego.
24. „Todo” to twoja lista. Tego nikt za ciebie nie zrobi.

## Discord

25. Kanał repo dostaje zgłoszenia, PR-y, przypisania, recenzje i komentarze
    ludzi — z prawdziwymi pingami osób, których dotyczą.
26. Co rano osobny kanał dostaje podsumowanie dnia: kartę każdej osoby
    z jej wczorajszym postępem (zmergowane PR-y, zamknięte i założone
    zgłoszenia) oraz kartę „Ogólnie” z tym, co jest do zrobienia, linkami
    do „Todo” i „Open for taking” i liczbami zgłoszeń i PR-ów.
27. Podsumowanie pinguje tylko tam, gdzie na kogoś czeka ruch: recenzentów
    otwartych PR-ów (albo autora, który jeszcze nie poprosił o recenzję),
    autora zgłoszenia z `merged - is it ok?` i przypisanych do zgłoszeń
    `stale` (bez przypisania — autora). Wersje robocze są bez pingu.
28. W komentarzu na GitHubie `@login` pinguje tę osobę także na Discordzie,
    `**login**` tylko ją wymienia.
29. Nowa osoba w zespole potrzebuje wpisu w mapie kont GitHub → Discord
    (zmienna repozytorium `DISCORD_IDS`), inaczej nie dostaje pingów.

## Kiedy automat celowo milczy

Zanim uznasz ciszę za błąd, sprawdź tę listę. Każdy punkt to zamierzone
zachowanie albo ograniczenie GitHuba, a nie awaria.

30. **PR w wersji roboczej nie dostaje oceny.** Ocena przychodzi po „Ready for
    review”. Wcześniej tylko na wyraźne `@claude`.
31. **PR do gałęzi innej niż `main` i `stable` nie dostaje oceny.** Dostanie ją
    tylko po `@claude` albo po ręcznym biegu.
32. **PR otwarty w konflikcie z bazą dostaje ocenę dopiero przy pierwszym
    pushu, który ten konflikt usuwa.** Przy konflikcie GitHub w ogóle nie
    uruchamia automatów PR-a, więc w Actions nie ma nawet śladu biegu.
33. **PR z forka nie dostaje oceny.** Kod spoza repo nie trafia do Claude'a
    razem z sekretami.
34. **PR, który zmienia sam workflow oceny, nie dostaje oceny przy otwarciu.**
    Akcja Claude'a działa tylko z workflow takim jak na `main`. Pod PR-em
    pojawia się o tym informacja — napisz `@claude`, a ocena ruszy.
35. **`@claude` działa tylko we własnym tekście i tylko od osoby z prawem
    zapisu.** Wołanie przepisane w cytacie albo od kogoś bez zapisu jest
    pomijane bez komentarza.
36. **Zmiana workflow na gałęzi nie działa dla `@claude` ani ręcznego biegu, aż
    trafi na `main`.** Te wyzwalacze zawsze czytają wersję z `main`.
37. **To, co robią automaty, nie trafia na Discorda samo.** Wiadomość idzie
    tylko wtedy, gdy automat wysyła ją sam — dziś robią to ocena Claude'a,
    koniec pracy Claude'a nad zgłoszeniem, codzienne podsumowanie,
    przypomnienie i zamknięcie po ciszy.
38. **Ręczny ratunek:** Actions → „Claude - oceń PR” → Run workflow → numer
    PR-a ocenia go od nowa w każdym z powyższych przypadków poza forkiem.
