# Metal-Planner

Webowa wyszukiwarka połączeń komunikacji miejskiej Wrocławia (przystanek A → przystanek B).

## Architektura

Trzy warstwy:

### 1. Pipeline danych — `update_gtfs.py`

Uruchamiany ręcznie albo z crona (nie przez Flaska). Kolejno:

1. Odpytuje [portal Otwartych Danych Wrocławia](https://open-data.cui.wroclaw.pl/hdb/metadane/13/)
   o listę paczek GTFS i wybiera najnowszą, która **już obowiązuje**
   (portal wystawia też paczki z przyszłą datą startu — te pomijamy).
2. Pobiera zip (~12 MB), parsuje pliki CSV (`stops`, `routes`, `trips`,
   `stop_times`, `calendar`…) i buduje `data/gtfs_new.sqlite`.
3. Atomowo podmienia bazę (`os.replace`) na `data/gtfs.sqlite` — działająca
   aplikacja nigdy nie widzi wpół zapisanego pliku, a gdy pobieranie padnie,
   wczorajsza baza zostaje nietknięta.

### 2. Backend — Flask

- **`gtfs.py`** — dostęp do SQLite. Przy pierwszym zapytaniu danego dnia
  wyznacza kursujące tego dnia kursy (logika `calendar.txt`), buduje w RAM
  tablicę ~1 mln „połączeń" (pojedynczych przejazdów między sąsiednimi
  przystankami, posortowanych po odjeździe) i cache'uje ją. Klucz cache
  zawiera mtime pliku bazy, więc po nocnej podmianie dane przeładują się
  same — bez restartu Flaska.
- **`planner.py`** — algorytm CSA (Connection Scan): jeden liniowy skan
  posortowanej tablicy, śledzący najwcześniejszy przyjazd na każdy przystanek.
  Obsługuje przesiadki (bufor 2 min), przejścia między słupkami o tej samej
  nazwie (3 min) i kursy po północy (24:xx). Na końcu odtwarza trasę
  w etapy z godzinami i współrzędnymi. Pierwsze zapytanie dnia ~1 s
  (ładowanie), kolejne natychmiastowe.
- **`routes.py`** — trzy endpointy: `/` (strona), `/api/stops` (wszystkie
  słupki ze współrzędnymi, pod markery) i `/api/plan?start=&end=&time=`
  (JSON z trasą i geometrią etapów).

### 3. Frontend — `templates/index.html`

Jedna strona: pełnoekranowa mapa Leaflet (kafelki OpenStreetMap — wymaga
internetu), wszystkie słupki jako markery na canvasie, chowany panel boczny
(przycisk ☰). Klik 1 = start (zielony), klik 2 = cel (czerwony) i wyszukiwanie
odpala się samo; etapy trasy rysowane jako polilinie (tramwaj czerwony,
autobus niebieski, przejście przerywane). Czysty JS bez frameworka,
gada tylko z dwoma endpointami API.

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
