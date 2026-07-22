# Metal-Planner

Webowa wyszukiwarka połączeń komunikacji miejskiej Wrocławia. Zamiast jednej
wyliczonej trasy pokazuje na mapie **wszystkie sensowne dojazdy naraz** —
główne korytarze jaskrawo, niszowe objazdy ledwo widocznie — a użytkownik
sam wybiera.

Pełny opis projektu, architektury i algorytmów: **[PROJECT.md](PROJECT.md)**.

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

Serwer nasłuchuje na `0.0.0.0`, więc jest też dostępny z telefonu/innego
urządzenia w TEJ SAMEJ sieci Wi-Fi co komputer, na którym działa - pod adresem
LAN tego komputera zamiast `localhost` (np. `http://192.168.1.23:5001`; adres
sprawdzisz przez System Settings → Wi-Fi → Details, albo `ipconfig getifaddr
en0` w terminalu na macOS). To też oznacza, że interaktywny debugger Flaska
(`debug=True`) jest wystawiony na całą sieć lokalną, nie tylko na ten
komputer - akceptowalne w domowej sieci, ale nie wystawiać tak na internet.

## Codzienna aktualizacja rozkładu

Cron na serwerze, np. o 3:00:

```
0 3 * * * cd /sciezka/do/Metal-Planner && .venv/bin/python update_gtfs.py >> logs/update.log 2>&1
```

Gdy pobieranie się nie powiedzie, stara baza zostaje nietknięta — aplikacja
działa dalej na wczorajszych danych i przeładuje nowe sama, bez restartu.
