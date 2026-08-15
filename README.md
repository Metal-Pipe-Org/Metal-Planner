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
.venv/bin/python update_gtfs.py   # pobiera rozkład (~12 MB) i buduje bazę, ~10 s
.venv/bin/python app.py           # http://localhost:5001
```

Port to domyślnie 5001 (5000 zajmuje AirPlay na macOS); można zmienić
zmienną `PORT`.

## Instalacja jako aplikacja (PWA)

Planer instaluje się jak zwykła aplikacja — na telefonie „Dodaj do ekranu
głównego", na pulpicie ikoną ⤓ w nagłówku albo z paska adresu przeglądarki.
Działa wtedy we własnym oknie, a raz odwiedzona okolica mapy jest dostępna
też bez internetu (samo wyszukiwanie połączeń wymaga sieci).

Instalację przeglądarki proponują wyłącznie po **HTTPS**; wyjątkiem jest
`localhost`, więc lokalnie działa to od ręki.

## Codzienna aktualizacja rozkładu

Cron na serwerze, np. o 3:00:

```
0 3 * * * cd /sciezka/do/Metal-Planner && .venv/bin/python update_gtfs.py >> logs/update.log 2>&1
```

Gdy pobieranie się nie powiedzie, stara baza zostaje nietknięta — aplikacja
działa dalej na wczorajszych danych i przeładuje nowe sama, bez restartu.

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
