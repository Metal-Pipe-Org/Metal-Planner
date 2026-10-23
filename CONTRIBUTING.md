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
6. Gotowe zmiany idą zwykłym pull requestem do `main` albo stosem PR-ów (pkt 23).
7. „Co aplikacja ma robić” omawiamy w issue — widzą to wszyscy, którzy je obserwują, i zostaje po PR-ze. „Czy ten kod jest dobry” — w PR-ze, przy liniach kodu.
8. PR wskazuje issue, nad którym pracujesz: w opisie (albo tytule) stoi `Closes #12` lub `Refs #12` (co które znaczy — punkt 9), a Ty jesteś wśród assignees tego issue. Szablon PR-a podsuwa gotowe `Closes #`. PR bez issue to decyzja, nie przeoczenie — zaznaczasz ją labelem `No Issue`, który wyłącza całe sprawdzenie. Gołe `#12` (bez słowa kluczowego) wolno pisać swobodnie, np. o innych issues — nie jest sprawdzane, ale też nie zastępuje `Closes`/`Refs`. PR, który tego nie spełnia (brak `Closes`/`Refs`, numer PR-a albo nieistniejący zamiast issue, issue z `Closes` albo `Refs` bez Ciebie wśród assignees), dostaje label `wrong syntax` i komentarz, która reguła nie przeszła i co możesz zrobić, a gotowy PR wraca do draftu. Po poprawce — dopisanie `Closes`/`Refs`, przypisanie się do issue albo `No Issue` — label schodzi sam, a komentarz się zwija; PR oznaczasz jako gotowy sam, co uruchamia review Claude'a. Dotyczy PR-ów do `main` i PR-ów piętrowych; wydania do `stable` i PR-y botów są pominięte. PR otwarty od razu jako gotowy może jeszcze dostać review Claude'a, choć automat cofa go do draftu — oba ruszają równocześnie.

### Review i mergowanie

9. `Closes #12` (albo `Fixes`, `Resolves`) w opisie PR-a znaczy „ten PR załatwia issue”: po merge'u issue zamyka się samo. `Refs #12` (albo `Ref`, `Related to`, `Part of`, `Dotyczy`) to luźniejszy związek — issue dostaje label, ale zamyka je człowiek. Gołe `#12` niczego nie oznacza, więc można pisać o innych issues bez obaw.
10. Zaraz po otwarciu pod PR-em pojawia się jednolinijkowa zapowiedź, że Claude go czyta, a po kilku minutach review: werdykt, podsumowanie, uwagi do kodu. Zapowiedź zwija się, gdy review już stoi. Dostajesz ping na GitHubie i na Discordzie.
11. PR otwarty w konflikcie od razu wraca do draftu (prośby o review się zdejmują) i nie dostaje review. Rozwiąż konflikt i oznacz PR jako gotowy („Ready for review”) — wtedy przychodzi review Claude'a.
12. Review leci przy otwarciu PR-a i przy każdym wyjściu z draftu. Gdy się nie udało, napisz `@claude` w komentarzu pod PR-em. Gdy przyjdzie nowe review, starsze review i informacje o nieudanych review zwijają się same: widać z nich tylko werdykt, resztę rozwija się kliknięciem „Starsza ocena — rozwiń”. Informacja, że PR zmienia workflow review, zwija się wtedy w całości. Nieudane ponowne review niczego nie zwija.
13. Kolejne review jest przyrostowe: Claude zaczyna od tego, co w zmianach PR-a doszło od jego poprzedniego review — `main` wciągnięty w międzyczasie do brancha się nie liczy — i sam decyduje, czy przejrzeć cały PR od nowa. Zakres („Ocena pełna” albo „Ocena przyrostowa od …”) stoi zaraz pod werdyktem.
14. Gotowy PR nie dostaje pushy: każda zmiana — poprawki po review Claude'a i po review ludzi, rozwiązanie konfliktu — idzie wyłącznie przez draft. Push do gotowego PR-a automat i tak przestawi, zostawiając komentarz i zdejmując prośby o review. Wyjątek to push, który tylko dociąga gałąź bazową („Update branch”, merge bazy bez własnych poprawek, rebase) — zmiany PR-a zostają te same, więc PR zostaje gotowy, z zatwierdzeniami i ze statusem „Ocena Claude'a” (pkt 18). Po poprawkach oznacz PR jako gotowy — to uruchamia ponowne review Claude'a. Przeczytaj je i dopiero wtedy poproś o review w panelu bocznym („Reviewers”). Reviewer dostaje ping na Discordzie.
15. Gdy merge czegoś do `main` — albo push do niższego piętra stosu (pkt 23) — wpędzi Twój PR w konflikt z bazą, dostajesz ping na Discordzie — raz, nie po każdym kolejnym merge'u; draftów też to dotyczy. Gotowy PR wraca przy tym do draftu, a jeśli czekał na review, prośby o review zdejmują się same: rozwiąż konflikt, oznacz PR jako gotowy i po review Claude'a poproś o review ponownie.
16. Zatwierdzenia nie znikają przy pushu. Po każdym wyjściu z draftu Claude ocenia też, czy zmiany od ostatniego zatwierdzenia człowieka potrzebują jeszcze raz ludzkiego oka — samo rozwiązanie konfliktu, literówka czy poprawka wprost z uwag recenzenta zwykle nie. Zatwierdzenie zostaje tylko wtedy, gdy zmiany są małe, werdykt jest 🟢 i nikt nie ma czekającego „Request changes”; dostajesz wtedy osobny komentarz z trzema wyjściami: wróć do draftu (chcesz jeszcze coś zmienić), zmerguj sam albo poproś o ponowne review. W każdym innym razie bot cofa zatwierdzenia, a powód stoi w osi czasu PR-a — przeczytaj review Claude'a i poproś o review ponownie.
17. „Request changes” znosi tylko późniejsze zatwierdzenie tej samej osoby albo cofnięcie (dismiss) tego review — zatwierdzenie kogoś innego go nie znosi. Cudze „Request changes” może cofnąć każdy z prawem zapisu, ale tylko wtedy, gdy recenzent od 24 h nie odpowiada albo sam się na to zgodził — zawsze z powodem w polu cofnięcia.
18. Merge blokuje wymagany status „Ocena Claude'a” (lista checków pod PR-em). Od wyjścia z draftu do końca review — razem z rozstrzygnięciem o zatwierdzeniach — czeka („pending”), a zielony robi się dopiero po nim, więc nikt nie zmerguje PR-a, zanim bot zdąży cofnąć nieaktualne zatwierdzenie. Zielony znaczy „review skończone”, nie „review dobre” — werdykt widać w opisie statusu. Czerwony (review się nie udało albo bot nie rozstrzygnął o zatwierdzeniach) albo brak statusu na najnowszym commicie (np. PR sprzed wprowadzenia statusu) — napisz `@claude` w komentarzu pod PR-em. Status jest przypięty do commita, a nowe zmiany i tak wracają przez draft (pkt 14).
19. Review może zrobić każdy z zespołu, a każdy PR potrzebuje co najmniej jednego zatwierdzenia człowieka. Autor nie zatwierdza własnego PR-a (GitHub na to nie pozwala).
20. Recenzentem koniecznym może się zadeklarować każdy z zespołu: dopisuje swój login PR-em do linijki `*` w `.github/CODEOWNERS`, a wycofuje się PR-em, który go z niej usuwa; liczy się linijka na `main`, więc deklaracja działa od merge'a. To deklaracja, nie hierarchia ani stanowisko — działa w obie strony: każdy PR musi dostać zatwierdzenie od każdego recenzenta koniecznego, a recenzent konieczny robi review każdego PR-a w ciągu 24 h. Własnego PR-a nie zatwierdza — wtedy wystarczą pozostali. Gdy PR jest gotowy, GitHub sam prosi recenzentów koniecznych o review.
21. Merge blokuje też wymagany status „Recenzenci konieczni”: zielony, gdy każdy recenzent konieczny poza autorem ma stojące zatwierdzenie (ostatnie jego review to zatwierdzenie, niecofnięte); do tego czasu opis statusu mówi, na kogo PR czeka. Kto pilnuje których plików, wyznaczają pozostałe linijki `.github/CODEOWNERS` — gdy PR dotyka pliku z ownerem, GitHub dodaje go do reviewerów, ale jego zatwierdzenie nie jest wymagane.
22. PR merguje jego autor. Można używać automerge. Stos PR-ów scala się inaczej — pkt 24.

### Stos PR-ów

23. Zmiany, które budują jedna na drugiej, możesz wystawić stosem: każdy kolejny PR celuje w branch PR-a pod nim. Twórz go jako natywny stos GitHuba (w interfejsie albo `gh stack`). Każde piętro przechodzi cały przepływ jak PR do `main`: review Claude'a, drafty, konflikty z piętrem niżej, wymagane statusy i zatwierdzenia — reguły `main` obowiązują każde piętro stosu. Gotowe piętro, pod albo nad którym coś jeszcze nie jest gotowe, czeka z labelem `waiting for other PR`.
24. Stos scalamy w całości, od dołu: merge albo auto-merge tylko na najwyższym PR-ze, gdy wszystkie piętra są zaakceptowane — GitHub scala wtedy cały stos od dołu i sam przestawia bazy. Auto-merge można włączyć wcześniej: na najwyższym PR-ze to jeden wybór dla całego stosu, który czeka, aż wszystko będzie gotowe. Nie scalaj niższego piętra osobno. Gdy niższe piętro się zmieni, „Rebase stack” przenosi resztę stosu; piętro, którego zmiany zostały te same, zostaje gotowe (pkt 14).

## Release

25. Gdy zmiany na `main` są zebrane i przetestowane na wersji testowej, pull request z `main` do `stable` tworzy nowy release. Taki PR też dostaje review Claude'a.

## Issues

26. `@claude` w treści nowego issue albo w komentarzu pod nim wysyła Claude'a do pracy: czyta wątek, robi poprawkę, uruchamia testy i wypycha ją na branch `claude/issue-<numer>`. Na starcie wstawia jednolinijkową zapowiedź, a na końcu nowy komentarz z odpowiedzią i linkiem, który otwiera PR do `main` z gotowym `Closes` (zapowiedź wtedy się zwija). Kolejne `@claude` w tym samym wątku dokładają się do tego samego brancha i tego samego PR-a.
27. Claude bierze się tylko za zadania jasne i niewielkie. Przy niejasnym issue zamiast poprawki zadaje pytanie. Nie zmienia automatów, sekretów ani uprawnień — to zawsze robi człowiek.
28. Koniec pracy Claude'a ogłasza ping na Discordzie, który prowadzi do komentarza z odpowiedzią. Issue, którym się zajmował, dostaje label `claude` — to sam znacznik, niczego nie uruchamia.
29. Bycie assignee w issue znaczy, że nad nim pracujesz. Gdy issue ma kilku assignees, ustalają między sobą, jak dzielą pracę, żeby nie robić tego samego dwa razy.
30. Gdy przestajesz pracować nad issue, a go nie zamykasz (np. zmiana planów), usuń się z jego assignees.
31. „Zgłoś problem” w aplikacji (klik w ikonę → „O aplikacji”) otwiera nowe issue z wpisaną wersją, urządzeniem i adresem strony. Issues od osób spoza zespołu dostają label `external`. Żeby zgłosić, trzeba mieć konto na GitHubie.

### Labels

32. PR-y i issues mają osobne labele stanu. Każdy otwarty PR nosi dokładnie jeden z trzech, który mówi, czyj jest następny ruch; automat przestawia go po każdej zmianie PR-a, recenzji i review Claude'a, a zamknięty PR traci go całkiem. Ręcznie ich nie zmieniaj — przy następnym przeliczeniu wrócą.
   - `waiting for author` — ruch autora: PR jest draftem albo ma konflikt z bazą; review Claude'a się nie udało albo nie ruszyło (napisz `@claude`); ktoś ma czekające „Request changes”; brakuje zatwierdzeń, a nikt nie jest proszony o review (poproś w „Reviewers”); albo wszystko jest gotowe — zmerguj.
   - `waiting for review` — ruch kogoś innego niż autor: Claude właśnie robi review albo czeka prośba o review człowieka, w tym recenzenta koniecznego.
   - `waiting for other PR` — PR jest gotowy do merge'a, ale należy do stosu (PR, którego baza to branch innego PR-a, i PR-y z nim tak połączone), a inny PR w tym stosie nie jest. Stos scalamy w całości, najwyższym PR-em (pkt 24).
33. Labele stanu issues nadają się same: `PR in progress` od otwarcia PR-a (także wersji roboczej) z `Closes` albo `Refs`, niezależnie od stanu PR-a (zdejmuje się, gdy PR jest zamknięty), `merged - is it ok?` po merge'u PR-a z `Refs`. Ten ostatni zdejmuje ten, kto issue zamyka.
34. Issue bez niczyjego wpisu przez 14 dni dostaje pytanie, czy temat jest aktualny, i label `stale`. Po kolejnych 7 dniach ciszy zamyka się jako „not planned”. Automat zostawia issue otwarte po każdym komentarzu człowieka, ale nie odpisuj samym „tak” — napisz, dlaczego ma zostać, choć nikt go nie ruszał przez 14 dni. Nie dotyczy issues z labelem `external blocked`, zablokowanych przez inne otwarte issue (GitHubowe „Blocked by") ani takich, które mają otwarte sub-issues.
35. Issue, które stoi przez coś poza naszym wpływem, dostaje label `external blocked`, a w issue trzeba napisać, na co czeka. Issue zablokowane przez inne otwarte issue (GitHubowe „Blocked by") jest traktowane tak samo, bez labela i bez opisu — zależność sama mówi, na co czeka; po zamknięciu blokującego issue przestaje blokować. Dając `external blocked`, zdejmij `stale` — te dwa labele nie powinny stać razem, a automat sam tego nie posprząta.
36. `to discuss` znaczy, że issue trzeba omówić z zespołem. Każdy z zespołu jest zachęcany, żeby dorzucić swoje zdanie. Label zdejmujemy, gdy dyskusję uznamy za zakończoną.
37. `to revisit` dajemy przy zamykaniu issues, które teraz wykraczają poza zakres, ale są dobrym pomysłem na przyszłość. Dzięki niemu można je zamknąć, żeby nie poszerzały listy otwartych issues, a wciąż da się je odróżnić od innych zamkniętych issues.

### Views

38. „Open for taking” otwierasz, gdy chcesz wziąć coś nowego.
39. „Todo” to twoja lista. Tego nikt za ciebie nie zrobi.
40. „Open discussions” to lista issues, dla których konwersacja ciągle trwa.

## Discord

41. Kanał repo dostaje issues, PR-y, assigny, review i komentarze ludzi — z prawdziwymi pingami osób, których dotyczą.
42. Co rano osobny kanał dostaje podsumowanie dnia: kartę każdej osoby z jej wczorajszym postępem (zmergowane PR-y, zamknięte i założone issues) oraz kartę „Ogólnie” z tym, co jest do zrobienia, linkami do „Todo”, „Open for taking” i „Open discussions” oraz liczbami issues i PR-ów.
43. Podsumowanie pinguje tylko tam, gdzie na kogoś czeka ruch: reviewerów otwartych PR-ów (albo autora, który jeszcze nie poprosił o review), autora issue z `merged - is it ok?` i assignees issues ze `stale` (bez assignee — autora). Drafty są bez pingu.
44. W komentarzu na GitHubie `@login` pinguje tę osobę także na Discordzie, `**login**` tylko ją wymienia.
45. Nowa osoba w zespole potrzebuje wpisu w mapie kont GitHub → Discord (zmienna repozytorium `DISCORD_IDS`), inaczej nie dostaje pingów.

## Kiedy automat celowo milczy

Zanim uznasz ciszę za błąd, sprawdź tę listę. Każdy punkt to zamierzone zachowanie albo ograniczenie GitHuba, a nie awaria.

46. **Draft PR nie dostaje review.** Review przychodzi po „Ready for review”. Wcześniej tylko na wyraźne `@claude`.
47. **PR w konflikcie z bazą nie dostaje review.** Przy konflikcie GitHub w ogóle nie uruchamia automatów PR-a, więc w Actions nie ma nawet śladu biegu review. PR wraca wtedy do draftu, a review przychodzi po rozwiązaniu konfliktu i „Ready for review”.
48. **PR z forka nie dostaje review.** Kod spoza repo nie trafia do Claude'a razem z sekretami.
49. **PR, który zmienia sam workflow review, nie dostaje review przy otwarciu.** Akcja Claude'a działa tylko z workflow takim jak na `main`. Tak samo PR w stosie, którego baza (niższe piętro) ma inny workflow review niż `main` — zmienia go albo odbiła od starszego `main`. Pod PR-em pojawia się o tym informacja — napisz `@claude`, a review ruszy.
50. **`@claude` działa tylko we własnym tekście i tylko od osoby z prawem zapisu.** Wołanie przepisane w cytacie albo od kogoś bez zapisu jest pomijane bez komentarza.
51. **Zmiana workflow na branchu nie działa dla `@claude` ani ręcznego biegu, aż trafi na `main`.** Te wyzwalacze zawsze czytają wersję z `main`.
52. **To, co robią automaty, nie trafia na Discorda samo.** Wiadomość idzie tylko wtedy, gdy automat wysyła ją sam — dziś robią to review Claude'a (z dopiskiem, czy zatwierdzenia zostały, czy bot je cofnął), koniec pracy Claude'a nad issue, codzienne podsumowanie, przypomnienie, zamknięcie po ciszy, wstrzymanie review przy konflikcie i `wrong syntax` na PR-ze. Przestawienie PR-a na draft przez automat zostawia tylko komentarz pod PR-em; o nowym konflikcie z bazą przychodzi dodatkowo ping.
53. **Ręczny ratunek:** Actions → „Claude - oceń PR” → Run workflow → numer PR-a robi review od nowa w każdym z powyższych przypadków poza forkiem.
54. **Wydanie do `stable` i PR bota nie są sprawdzane, czy wskazują issue.** Nie dostają `wrong syntax` ani komentarza, nawet bez `Closes`/`Refs`.

Aktualizacja: 23-10-2026
