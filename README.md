# Metal-Planner

Webowa wyszukiwarka połączeń komunikacji miejskiej Wrocławia. Zamiast jednej
wyliczonej trasy pokazuje na mapie **wszystkie sensowne dojazdy naraz** —
główne korytarze jaskrawo, niszowe objazdy ledwo widocznie — a obok, w panelu,
**listę gotowych propozycji** z godzinami, liniami i przesiadkami. Wybór
propozycji podświetla ją na mapie, a kliknięcie linii na mapie otwiera
propozycję, która nią jedzie. Start można ustawić przyciskiem ◎ na aktualną
lokalizację. Na telefonie mapa i lista przełączają się dolnymi zakładkami.

Pełny opis projektu, architektury i algorytmów: **[docs/PROJECT.md](docs/PROJECT.md)**
(szczegóły samego algorytmu mapy przepływów: **[docs/ROUTING_ALGORITHM.md](docs/ROUTING_ALGORITHM.md)**,
gwarancje zachowania mapy, które to sprawdzają: **[docs/FLOW_MAP_CONTRACT.md](docs/FLOW_MAP_CONTRACT.md)**).

## Szybki start

Wymagany Python ≥ 3.9 (Flask 3.x nie działa na 3.8).

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python update_gtfs.py
.venv/bin/python app.py
```

`update_gtfs.py` pobiera rozkład (~12 MB) i buduje z niego bazę — zajmuje to
około 10 sekund. `app.py` wystawia serwer na http://localhost:5001; port to
domyślnie 5001 (5000 zajmuje AirPlay na macOS), można go zmienić zmienną
`PORT`.

## Instalacja jako aplikacja (PWA)

Planer instaluje się jak zwykła aplikacja — na telefonie „Dodaj do ekranu
głównego", na pulpicie ikoną ⤓ w nagłówku albo z paska adresu przeglądarki.
Działa wtedy we własnym oknie, a raz odwiedzona okolica mapy jest dostępna
też bez internetu (samo wyszukiwanie połączeń wymaga sieci).

Instalację przeglądarki proponują wyłącznie po **HTTPS**; wyjątkiem jest
`localhost`, więc lokalnie działa to od ręki.

## Codzienna aktualizacja rozkładu

Serwer robi to sam: raz na dobę o godzinie z `GTFS_AUTO_UPDATE_HOUR`
(w `docker-compose.yml` domyślnie 3:00, wg strefy czasowej kontenera).
Harmonogram żyje w procesie głównym serwera — jeden niezależnie od liczby
workerów — a samą aktualizację odpala jako osobny proces, więc budowa bazy
nie rośnie w pamięci serwera. Pusta wartość wyłącza harmonogram.

To nie jest wygoda, tylko warunek działania: paczka GTFS ma okno ważności
rzędu trzech tygodni, więc kontener stojący dłużej bez restartu dojechałby
do jego końca i przestał znajdować **jakiekolwiek** połączenia.

Gdy pobieranie się nie powiedzie, stara baza zostaje nietknięta — aplikacja
działa dalej na wczorajszych danych i przeładuje nowe sama, bez restartu.

Poza Dockerem to samo załatwia cron, np. o 3:00:

```
0 3 * * * cd /sciezka/do/Metal-Planner && .venv/bin/python update_gtfs.py >> logs/update.log 2>&1
```

### Odświeżanie przy starcie serwera

Cron nie jest potrzebny do jednego przypadku: **każdy start serwera odświeża
rozkład sam** — i w kontenerze (`docker/entrypoint.sh`, przed gunicornem),
i lokalnie (`python app.py`). Zasada jest ta sama: brakującą bazę serwer
pobiera blokująco, przed startem, a istniejącą odświeża w tle — wstaje
natychmiast na dotychczasowych danych i podmienia je w locie.

Różnica jest jedna. Lokalnie obowiązuje **próg świeżości**: baza młodsza niż
`GTFS_MAX_AGE_HOURS` (domyślnie 12 h) nie jest ruszana. Bez tego reloader
Flaska, który restartuje serwer po każdym zapisie pliku, ciągnąłby 12 MB po
każdym Ctrl+S. Kontener restartuje się rzadko, więc tam progu nie ma —
aktualizuje zawsze.

`GTFS_UPDATE_ON_START=off` wyłącza to w obu miejscach (w kontenerze przez
`docker-compose.yml`).

## Testy

Sprawdzają gwarancje z [docs/FLOW_MAP_CONTRACT.md](docs/FLOW_MAP_CONTRACT.md)
na syntetycznych danych (bez SQLite, bez zależności od daty) - patrz ten
plik po opis, co dokładnie który test sprawdza.

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -v
```

## Konwencja - branche
- dla każdej funkcji tworzymy indywidualne branche w konwencji `username/feature`
- tworzymy pull reguesty do testing gdy zmiany są gotowe
- gdy zmiany są zebrane, przetestowane i gotowe to stworzenia następnego relase'a tworzymy pull request z `testing` do `main` 
