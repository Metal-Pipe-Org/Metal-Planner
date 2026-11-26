# Zasady dla każdej zmiany

Claude Code czyta ten plik sam przy każdej pracy w tym repo, a ocena PR-a przez Claude'a pyta o te same rzeczy. Obowiązuje każdego, kto pisze kod: ludzi i AI. Skąd się wzięły: #519.

Reszta jest gdzie indziej: sposób pracy na GitHubie w [CONTRIBUTING.md](../../CONTRIBUTING.md), architektura w [docs/PROJECT.md](../../docs/PROJECT.md), zasady nadrzędne w [docs/PRINCIPLES.md](../../docs/PRINCIPLES.md) (między innymi: mechanizmy są tak proste, jak się da).

1. **Najpierw poszukaj, potem pisz.** Zanim dodasz funkcję, ustawienie, styl albo pomocnika w testach, przeszukaj repo, czy podobne już nie istnieje. Jeśli jest, użyj go albo rozbuduj, zamiast stawiać drugie obok.
2. **Usuń to, co Twoja zmiana uśmierciła.** Funkcje, gałęzie kodu, stałe, importy, style i ustawienia, których po zmianie nic już nie używa, znikają w tym samym PR-ze. Ustawienie, które jeszcze działa, ale ma zniknąć, dostaje w `settings.json` pole `obsolete` z datą (opis w [docs/PROJECT.md](../../docs/PROJECT.md), sekcja „Frontend”): miesiąc później znika z menu, a wtedy jego wpis, wiersz w `templates/index.html` i kod idą do usunięcia.
3. **Test sprawdza zachowanie, nie jeden dzień rozkładu.** Opisuje regułę, którą da się powiedzieć jednym zdaniem. Gdy potrzebuje rozkładu, buduje własny przez `make_day` z `tests/gtfs_builder.py`, bez `data/gtfs.sqlite` i bez sieci. Test odtwarzający jeden kurs z jednego dnia prawdziwego rozkładu traci sens przy następnej paczce GTFS.
4. **Usuń albo popraw testy i opisy, które zmiana uczyniła nieaktualnymi.** Test starego zachowania nie zostaje obok nowego, czerwony „na później” ani wyłączony. Opis w `docs/`, który przestał być prawdą, poprawiasz w tym samym PR-ze.
5. **Sprzątasz po swojej zmianie, nie całe repo.** Bałagan, którego Twoja zmiana nie dotyka, zgłoś w issue, zamiast dokładać go do PR-a, bo PR robi się za duży do przejrzenia.
