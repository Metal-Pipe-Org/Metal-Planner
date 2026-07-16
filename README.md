# Metal-Planner

Webowa wyszukiwarka połączeń komunikacji miejskiej Wrocławia (przystanek A → przystanek B).

## Jak to działa

- **Dane:** oficjalny rozkład GTFS z [Otwartych Danych Wrocławia](https://open-data.cui.wroclaw.pl/hdb/metadane/13/)
  (linie MPK: autobusy i tramwaje). `update_gtfs.py` sam wybiera z portalu
  najnowszą paczkę, która już obowiązuje.
- **Przechowywanie:** SQLite (`data/gtfs.sqlite`, plik ignorowany przez gita).
  Aktualizacja buduje bazę obok i podmienia ją atomowo (`os.replace`),
  więc działająca aplikacja nigdy nie czyta wpół zapisanego pliku.
- **Wyszukiwanie:** algorytm CSA (Connection Scan) w `planner.py` — wszystkie
  połączenia dnia posortowane po odjeździe, jeden liniowy skan, przesiadki
  z buforem 2 min + przejścia między słupkami o tej samej nazwie (3 min).
  Rozkład dnia jest cache'owany w RAM; pierwsze zapytanie trwa ~1 s, kolejne są natychmiastowe.
- **Mapa:** Leaflet + kafelki OpenStreetMap (wymaga internetu). Kliknięcie
  przystanku wybiera start (zielony), drugie kliknięcie cel (czerwony)
  i od razu szuka; trasa rysowana po przystankach (tramwaj czerwony,
  autobus niebieski, przejście przerywane). Panel z polami tekstowymi
  chowa się przyciskiem ☰. API dla frontu: `/api/stops` i `/api/plan`.

## Setup

Wymagany Python ≥ 3.9 (Flask 3.x nie działa na 3.8).

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python update_gtfs.py   # pobiera rozkład (~12 MB) i buduje bazę, ~10 s
.venv/bin/python app.py           # http://localhost:5001
```

Port to domyślnie 5001 (5000 zajmuje AirPlay na macOS); można zmienić zmienną `PORT`.

## Codzienna aktualizacja rozkładu

Cron na serwerze, np. o 3:00:

```
0 3 * * * cd /sciezka/do/Metal-Planner && .venv/bin/python update_gtfs.py >> logs/update.log 2>&1
```

Gdy pobieranie się nie powiedzie, stara baza zostaje nietknięta — aplikacja
działa dalej na wczorajszych danych.

## Znane ograniczenia (MVP)

- Optymalizujemy tylko czas przyjazdu — trasa może mieć „sprytną” dodatkową
  przesiadkę, która oszczędza 2 minuty.
- Wyszukiwanie działa w ramach jednej doby: kursy „po północy” (24:xx z wczorajszego
  rozkładu) są widoczne dla zapytań wieczornych, ale zapytanie o 0:30 nie widzi
  końcówek wczorajszych kursów.
- Brak tras pieszych po mieście — przesiadka możliwa tylko między słupkami
  o identycznej nazwie przystanku.
- Linia trasy na mapie łączy przystanki po prostej (nie po torach/ulicach) —
  dokładna geometria wymagałaby wczytania `shapes.txt`.
