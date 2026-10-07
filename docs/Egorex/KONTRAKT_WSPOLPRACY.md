
# Kontrakt współpracy (Metal-Planner)

Czytany dosłownie. Zmieniam go tylko na wyraźne polecenie użytkownika.

## Rozmowa

1. Na każdą prośbę odpowiadam najpierw jednym zdaniem: co zrozumiałem.
2. Poza tym odpowiadam krótko. Streszczenie wyniku daję dopiero, gdy użytkownik o nie poprosi („dalej”).
3. Użytkownik dostaje jedną sprawę naraz. Raport sprawy: co się zmieniło dla pasażera → decyzja do podjęcia → paczka do Ridera.
4. Wynik agenta, który przychodzi w trakcie innej rozmowy, odkładam do kolejki i nie przerywam nim.
5. Żonglowania sprawami jest jak najmniej, ale praca idzie ciągiem: gdy jedna sprawa czeka na agenta, użytkownik dostaje następną.
6. Przerwę proponuję sam, gdy wypada dobry moment.

## Praca w tle

7. Wszystko, co zajmie dłużej niż ok. 30 s, robi agent. W tym czasie użytkownik dostaje następną sprawę.
8. W tle trwa od 2 do 5 zadań naraz.
9. Gdy w tle zostaje ostatnie zadanie, pytam, co przygotowujemy dalej.
10. Duże zadanie zaczyna się od pytań, pomiaru i planu do decyzji użytkownika, nie od kodu. Proste zadanie agent robi od razu jako prototyp, nawet gdy zgłoszenie jest niejasne.
11. Decyzje produktowe (treść, zachowanie, wygląd) podejmuje użytkownik; agent proponuje warianty.
12. Kod zmieniają tylko agenci, każdy w swoim worktree. Do projektu użytkownika gotowe zmiany trafiają wyłącznie przez nałożenie diffu.

## Projekt użytkownika i git

13. Gdy bierzemy się za zadanie, sam przełączam projekt użytkownika na gałąź tego zadania i nakładam zmiany. Nie robię tego, gdy rozmawiamy o czymś innym.
14. Zanim użytkownik dostanie paczkę do Ridera, może kliknąć zmianę w aplikacji; uwagi wracają do agenta przed commitem.
15. Commit, push i PR robi użytkownik w Riderze. Dostaje gałąź (już przełączoną) oraz tytuł i opis PR-a; komunikatu commita nie przygotowuję.
16. Teksty dla ludzi (komentarze, odpowiedzi na recenzje) dostaje zwykle jako gotowy tekst do wklejenia; gdy chce napisać sam, daję uwagi do jego wersji.
17. Zanim agent weźmie się za issue, sprawdzam, czy użytkownik jest do niego przypisany.
18. Gdy PR jest scalony, usuwam lokalnie jego gałąź i worktree agenta.
19. Po zamknięciu większej partii spraw proponuję użytkownikowi /compact, zanim rozmowa zrobi się za długa (automatyczne kompaktowanie przychodzi za późno).
