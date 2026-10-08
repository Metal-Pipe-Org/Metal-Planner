# Metal-Planner

Webowa wyszukiwarka połączeń komunikacji miejskiej Wrocławia. Zamiast jednej
wyliczonej trasy pokazuje na mapie **wszystkie sensowne dojazdy naraz**,
a obok, w panelu, listę gotowych propozycji z godzinami, liniami
i przesiadkami.

Jak pracujemy w zespole — gałęzie, pull requesty, ocena Claude'a, zgłoszenia
i Discord: [CONTRIBUTING.md](CONTRIBUTING.md).

| Gałąź | Adres |
|---|---|
| `main` | https://metal-testing.sze.one/ |
| `stable` | https://metal.sze.one/ |

## Co potrafi

1. **Mapa przepływów.** Każdy dojazd to cała podróż od startu do celu,
   opisana dwiema liczbami: o której jest się w celu i o której trzeba wyjść.
   Na mapie jest to, co w którejś z nich jest najlepsze, plus podróże
   najwyżej o kilka minut dłuższe. Ile tych minut, mapa dobiera sama tak,
   żeby pozostała czytelna; przycisk **Pokaż więcej** (do trzech kliknięć)
   zagęszcza ją, zawsze o coś nowego. Wszystkie narysowane linie wyglądają
   tak samo — o tym, czy coś jest na mapie, rozstrzyga sam próg.
2. **Zawsze wiadomo, co jedzie.** Linie jednego korytarza leżą na sobie,
   a numery stoją przy nich w zwartych grupkach. Najechanie kursorem
   podświetla jedną linię na całej długości i podaje godzinę w tym miejscu
   oraz o której będzie się w celu. Pasek nad mapą mówi, o której wyjść.
3. **Kropki przesiadek.** Przy miejscu przesiadki tablica: wiersz na linię
   z jej godzinami i znakiem, czy się tu wsiada, jedzie dalej czy wysiada.
4. **Zawsze jakaś trasa.** Jeśli o podanej godzinie nic nie jedzie, mapa
   pokazuje najbliższy dojazd, choćby rano następnego dnia, i mówi to wprost.
5. **Jestem w pojeździe.** Zamiast „skąd" podaje się linię, kierunek
   i najbliższy przystanek; każda propozycja mówi, na którym przystanku i za
   ile przystanków wysiąść.
6. **Rozkłady jazdy** (przycisk ◷, na telefonie zakładka „Rozkład"). Jedno
   pole na linię i przystanek. Linia: warianty trasy i lista przystanków na
   mapie. Przystanek: tablica odjazdów z odhaczanymi liniami, podział na
   słupki i pełny rozkład ze słupka, wiersz na godzinę. Przyciski „odjazdy"
   i „trasa" przeskakują między jednym a drugim.
7. **Traficar.** Przycisk 🚗 pokazuje wolne auta, do których mapa dowozi:
   każde, którego nie bije inne naraz w godzinie dojścia, zniżce z programu
   „Ogarniam" i szacowanym przyjeździe do celu. Najechanie na auto pokazuje
   strefę, w której da się je oddać. Na liście propozycji może też stanąć
   trasa kończąca się autem.
8. **Rower miejski (WRM).** Przyciski 🚲 (zwykłe) i ⚡ (elektryczne) dokładają
   na mapę stacje i rowery luzem, z których przejazd prowadzi do czegoś, co
   mapa rysuje; przejazdy widać pod kursorem. Te same przyciski dokładają
   propozycje z rowerem do listy.
9. **Pojazdy na żywo.** Przycisk ◉ pokazuje autobusy i tramwaje w ruchu —
   przy narysowanej mapie tylko linie z tej mapy.
10. **Kolej.** Z kluczem `PKP_API_KEY` w wynikach pojawiają się pociągi PKP,
    w tej samej wyszukiwarce co tramwaje i autobusy.
11. **Aplikacja (PWA).** Na telefonie „Dodaj do ekranu głównego", na pulpicie
    ikona ⤓ w nagłówku. Raz obejrzana okolica mapy działa bez internetu;
    samo wyszukiwanie wymaga sieci. Instalację przeglądarki proponują tylko
    po HTTPS albo na `localhost`.

Ustawienia dla zespołu są pod zębatką ⚙: gęstość mapy i założenia czasowe
(tempo marszu, prędkość roweru i auta).

## Uruchomienie lokalne

Python 3.11 — na nim chodzą testy i lokalne środowisko (obraz Dockera używa
3.12). Flask 3.x nie działa na 3.8.

12. Środowisko i zależności:

    ```bash
    python3.11 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    ```

13. Rozkład: `.venv/bin/python update_gtfs.py` pobiera paczkę GTFS (~12 MB)
    i buduje z niej bazę w około 10 sekund.
14. Serwer: `.venv/bin/python app.py` wystawia aplikację na
    http://localhost:5001 (5000 zajmuje AirPlay na macOS; inny port zmienną
    `PORT`).
15. Sekrety, jeśli są potrzebne: `cp data/.env.example data/.env` i wpisać
    w nim `PKP_API_KEY`. Plik wczytuje się sam przy starcie.

## Jak to jest zbudowane

Flask bez frameworka na froncie i bez kroku budowania. `update_gtfs.py`
pobiera rozkład i buduje z niego SQLite; `planner.py` liczy mapę i trasy na
tablicy połączeń dnia trzymanej w pamięci; `routes.py` wystawia API;
`static/app.js` to cały front mapy. Pełny opis — w
[docs/PROJECT.md](docs/PROJECT.md).

## Wdrożenie

Cały deployment opisuje `docker-compose.yml` (komentarze w nim mówią, co
i dlaczego). Pierwsza instalacja i każda aktualizacja, na serwerze:

```bash
curl -fsSL https://raw.githubusercontent.com/Metal-Pipe-Org/Metal-Planner/main/docker/deploy.sh | bash
```

Produkcja: `BRANCH=stable` przed `bash`.

## Konfiguracja

Zmienne środowiskowe — w kontenerze z `docker-compose.yml`, lokalnie z powłoki
albo z `data/.env` (zmienne ze środowiska przebijają plik). Najważniejsze:

| Zmienna | Domyślnie | Co robi |
|---|---|---|
| `PORT` | 5001 lokalnie | port serwera |
| `PKP_API_KEY` | brak | klucz PKP PLK OpenData; bez niego nie ma pociągów |
| `GTFS_AUTO_UPDATE_HOUR` | w compose 3 | godzina codziennej aktualizacji rozkładu; pusta wyłącza |
| `GTFS_UPDATE_ON_START` | `on` | odświeżanie rozkładu przy starcie serwera |
| `SIECHNICE_ENABLED` | `off` | autobusy gminy Siechnice (niżej) |
| `WRM_ENABLED` | `on` | rower miejski |
| `TRAFICAR` | `1` | auta Traficara; `0` wyłącza |
| `DEV_MODE` | wyłączony | `1` pokazuje sekcje DEV w ⚙ i dokłada w „O aplikacji” linki zespołu; lokalnie i na serwerze testowym |

Pozostałe (rozkład PKP, procesy i wątki serwera, strefa czasowa) opisują
komentarze w `docker-compose.yml`.

## Skąd dane

16. **Rozkład MPK** — paczka GTFS z
    [Otwartych Danych Wrocławia](https://open-data.cui.wroclaw.pl/hdb/metadane/13/).
    Jest ważna około trzech tygodni, więc serwer odświeża ją sam: codziennie
    i przy każdym starcie, w tle, bez przerwy w działaniu. Nieudane pobranie
    zostawia poprzednią bazę.
17. **Kolej** — PKP PLK OpenData, tylko z `PKP_API_KEY`.
18. **Autobusy gminy Siechnice** — nie ma ich w żadnych otwartych danych;
    umiemy je złożyć z API kiedyPrzyjedzie, ale to jest **domyślnie
    wyłączone**, bo serwis nie zgadza się na ponowne użycie danych. Szczegóły
    i wzór pisma do gminy: [docs/SIECHNICE_DANE.md](docs/SIECHNICE_DANE.md).
19. **Rower miejski** — kanał GBFS operatora (nextbike, licencja CC0-1.0).
20. **Traficar** — [fioletowe.live](https://fioletowe.live/), strona trzecia
    republikująca API Traficara; może zniknąć bez ostrzeżenia.
21. **Pojazdy na żywo** — pozycje z `mpk.wroc.pl`.

Źródła na żywo (19–21) liczą się tylko przy pytaniu o dziś, a awaria
któregokolwiek zabiera tylko jego warstwę — reszta działa bez zmian.

## Testy

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -v
```

22. Większość testów buduje syntetyczny rozkład (`tests/gtfs_builder.py`),
    ale część sięga po prawdziwą bazę `data/gtfs.sqlite` — przed pierwszym
    uruchomieniem trzeba zrobić `update_gtfs.py`.
23. Testy frontu (`tests/js/`) uruchamiają prawdziwy `static/app.js`
    w JavaScriptCore przez `osascript`, więc działają tylko na macOS;
    gdzie indziej są pomijane.
24. Workflow `tests.yml` w GitHub Actions odpala się wyłącznie ręcznie, bo
    runner nie ma bazy rozkładu.

Który test pilnuje którego punktu kontraktu mapy, opisuje
[docs/FLOW_MAP_NOTES.md](docs/FLOW_MAP_NOTES.md#testy).

## Gałęzie i pull requesty

Cały sposób pracy opisuje [CONTRIBUTING.md](CONTRIBUTING.md).

## Dokumentacja

25. [docs/PROJECT.md](docs/PROJECT.md) — architektura, algorytmy, API,
    struktura plików i changelog do 2026-10-08 (zamknięty).
    Nowsze wpisy: po pliku na PR w [docs/changelog/](docs/changelog/).
26. [docs/FLOW_MAP_CONTRACT.md](docs/FLOW_MAP_CONTRACT.md) — gwarancje
    zachowania mapy przepływów; zmienia się wyłącznie na wyraźne polecenie.
27. [docs/PRINCIPLES.md](docs/PRINCIPLES.md) — zasady nadrzędne nad
    pojedynczymi funkcjami; też tylko na wyraźne polecenie.
28. [docs/FLOW_MAP_NOTES.md](docs/FLOW_MAP_NOTES.md) — testy kontraktu,
    otwarte pytania i dziennik zmian mapy z pomiarami i decyzjami.
29. [docs/ROUTING_ALGORITHM.md](docs/ROUTING_ALGORITHM.md) — jak mapa jest
    liczona, krok po kroku na przykładzie (po angielsku).
30. [docs/SIECHNICE_DANE.md](docs/SIECHNICE_DANE.md) — skąd wziąć rozkład
    gminy Siechnice.
31. [docs/WORKFLOWS.md](docs/WORKFLOWS.md) — automaty GitHuba i Discord.
