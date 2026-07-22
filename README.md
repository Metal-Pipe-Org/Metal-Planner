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

## Deployment na serwerze (Docker)

Na serwerze potrzebny jest **wyłącznie plik `docker-compose.yml`** — obraz
buduje się prosto z GitHuba, repo nie musi być sklonowane:

```bash
curl -O https://raw.githubusercontent.com/Metal-Pipe-Org/Metal-Planner/testing/docker-compose.yml
docker compose up -d --build
```

Aplikacja stoi na `http://serwer:5001`. Pierwszy start pobiera rozkład GTFS
i buduje bazę (~1 min) — postęp widać w `docker compose logs -f`.

### Folder z danymi

Przy pierwszym starcie obok `docker-compose.yml` powstaje zwykły folder `data/`
— to całe trwałe państwo aplikacji:

```
docker-compose.yml
data/
├── gtfs.sqlite       # baza rozkładów, ~80 MB
└── dev_token.txt     # token menu deweloperskiego
```

Kopia zapasowa to skopiowanie tego folderu, a przeniesienie aplikacji na inny
serwer — przeniesienie go razem z `docker-compose.yml`. Przebudowy obrazu
(`up -d --build`) i restarty go nie ruszają; usunięcie `data/` powoduje tylko
ponowne pobranie rozkładu i wylosowanie nowego tokenu.

Folder należy do użytkownika o UID 10001 (kontener nie działa jako root),
więc do podejrzenia go z hosta może być potrzebne `sudo`.

### Zmienne środowiskowe

Wszystkie mają sensowne wartości w [docker-compose.yml](docker-compose.yml) —
nic nie musisz ustawiać.

| Zmienna | Domyślnie | Znaczenie |
| --- | --- | --- |
| `GTFS_AUTO_UPDATE_HOUR` | `3` | Godzina codziennej aktualizacji rozkładu; puste = tylko ręczna. |
| `TZ` | `Europe/Warsaw` | Strefa czasowa (rozkład jest liczony wg zegara kontenera). |
| `WEB_CONCURRENCY` | `1` | Liczba workerów. Każdy trzyma własną kopię rozkładu w RAM — patrz [gunicorn.conf.py](gunicorn.conf.py). |
| `DEV_TOKEN` | *losowany* | Narzuca własny token menu zamiast losowanego. |
| `DEV_MENU` | `on` | `off` wyłącza menu deweloperskie i jego endpointy. |

Aktualizacja aplikacji do najnowszego commita — `docker compose up -d --build`.

## Menu deweloperskie

Przycisk ⚙ w lewym dolnym rogu aplikacji otwiera panel z trzema rzeczami:

- **stan danych** — kiedy zbudowano bazę, jej rozmiar, liczba przystanków,
  linii i kursów, zakres dat rozkładu, a do tego ostrzeżenie, gdy żaden
  kalendarz nie obejmuje dzisiejszej daty (czyli rozkład się przeterminował);
- **aktualizacja rozkładu** — jeden przycisk, log leci na żywo w panelu;
- **zrzut cache** — zwalnia rozkład i geometrię z pamięci bez restartu.

Nie ma domyślnego hasła: token losuje się przy pierwszym starcie i ląduje
w pliku obok bazy.

```bash
cat data/dev_token.txt
```

Wypisuje się też w logu pierwszego uruchomienia (`docker compose logs`).
Żeby narzucić własny, odkomentuj `DEV_TOKEN` w compose; `DEV_MENU: "off"`
wyłącza menu razem z jego endpointami.

## Aktualizacja rozkładu

Trzy drogi, wszystkie uruchamiają ten sam `update_gtfs.py`:

- **menu deweloperskie** — przycisk opisany wyżej;
- **automatycznie w kontenerze** — codziennie o `GTFS_AUTO_UPDATE_HOUR`;
- **z crona na hoście** (instalacja bez Dockera):

  ```
  0 3 * * * cd /sciezka/do/Metal-Planner && .venv/bin/python update_gtfs.py >> logs/update.log 2>&1
  ```

Gdy pobieranie się nie powiedzie, stara baza zostaje nietknięta — aplikacja
działa dalej na wczorajszych danych i przeładuje nowe sama, bez restartu.

## Automatyczne wdrożenia (GitHub Actions) — opcjonalnie

**To dodatek, nie wymóg.** Opisany wyżej `docker compose up -d --build`
w zupełności wystarcza do postawienia i aktualizowania aplikacji ręcznie.
Ta sekcja przydaje się tylko wtedy, gdy chcesz, żeby wdrożenie działo się samo.

Sercem jest [docker/deploy.sh](docker/deploy.sh) — skrypt na serwerze. Rola
GitHuba sprowadza się do zalogowania po SSH i uruchomienia go: żadnego
`checkout`, żadnego budowania obrazu na runnerze.

### Kiedy się uruchamia

Gałęzią wdrożeniową jest **`testing`** — to do niej idą pull requesty,
a wdrożenie startuje po ich zmergowaniu.

Trigger to `push` na tę gałąź i to jest świadomy wybór: push powstaje dopiero
wtedy, gdy merge naprawdę się zakończył, a PR-a z konfliktem po prostu nie da
się zmergować — więc nie ma czego wdrażać. Gdyby użyć triggera
`pull_request`, workflow odpalałby się na *podglądowym* commicie merge'a,
który bywa pokonfliktowany; dlatego go tu nie ma.

Gałąź nie jest nigdzie zaszyta w krokach workflow — wdrażana jest ta, która
wywołała przebieg (`github.ref_name`). Zmiana gałęzi wdrożeniowej wymaga więc
poprawienia trzech miejsc, które muszą się zgadzać:

| Plik | Co zmienić |
| --- | --- |
| [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) | `on.push.branches` |
| [`docker-compose.yml`](docker-compose.yml) | gałąź po `#` w `build.context` |
| [`docker/deploy.sh`](docker/deploy.sh) | domyślna wartość `BRANCH` |

Nie trzeba tego pilnować z pamięci: `deploy.sh` porównuje gałąź, którą wdraża,
z gałęzią zaszytą w pobranym `docker-compose.yml` i przerywa z błędem, gdy się
rozjadą. Bez tego wdrożenie po cichu postawiłoby inny kod niż ten, który
wywołał przebieg.

### Konfiguracja, raz

Na serwerze:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"      # wyloguj się i zaloguj ponownie
ssh-keygen -t ed25519 -f ~/.ssh/deploy -N ''
cat ~/.ssh/deploy.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/deploy                    # klucz PRYWATNY -> sekret SSH_KEY
```

W repozytorium (*Settings → Secrets and variables → Actions*):

| Sekret | Zawartość |
| --- | --- |
| `SSH_HOST` | adres serwera |
| `SSH_USER` | użytkownik SSH (w grupie `docker`) |
| `SSH_KEY` | klucz prywatny z `~/.ssh/deploy`, w całości |
| `SSH_KNOWN_HOSTS` | wynik `ssh-keyscan <adres-serwera>` |

`SSH_KNOWN_HOSTS` jest po to, żeby runner weryfikował tożsamość serwera
zamiast ufać pierwszemu napotkanemu kluczowi.

### Co robi docker/deploy.sh

Jest idempotentny — ta sama komenda instaluje od zera i aktualizuje, więc
można go puścić także ręcznie:

```bash
curl -fsSL https://raw.githubusercontent.com/Metal-Pipe-Org/Metal-Planner/testing/docker/deploy.sh | bash
```

Kolejno: pobiera nowy `docker-compose.yml` i **sprawdza jego poprawność**,
dopiero potem zatrzymuje starą wersję, przebudowuje obraz, sprząta osierocone
obrazy i czeka, aż `/healthz` odpowie. Kolejność jest celowa — gdy pobranie
albo walidacja zawiedzie, nic nie zostaje zatrzymane i stara wersja działa
dalej.

Aplikacja ląduje w `~/metal-planner`. Zmienne `APP_DIR`, `BRANCH`,
`HEALTH_URL` i `HEALTH_TIMEOUT` pozwalają to zmienić, ale mają sensowne
wartości domyślne.

### Dwa warianty: normalny i eco

Różnica polega wyłącznie na tym, **czy runner czeka na wynik**:

| | [`deploy.yml`](.github/workflows/deploy.yml) — aktywny | [`deploy-eco.yml.disabled`](.github/workflows/deploy-eco.yml.disabled) |
| --- | --- | --- |
| Czas Actions na wdrożenie | kilka minut | kilka sekund |
| Log budowy | w konsoli Actions | na serwerze (`deploy.log`) |
| Czerwony przebieg znaczy | wdrożenie się nie udało | tylko: nie udało się wystartować |

W wariancie eco zielony przebieg oznacza „wdrożenie **wystartowało**", a nie
„udało się" — wynik trzeba sprawdzić na serwerze:

```bash
cat  ~/metal-planner/deploy.status    # OK / BŁĄD + znacznik czasu
tail ~/metal-planner/deploy.log       # pełny przebieg
```

Pobranie skryptu zostaje synchroniczne także w eco, więc problemy z SSH
i z samym GitHubem nadal zapalają czerwone światło.

### Przełączenie na eco

GitHub uruchamia wyłącznie pliki `.yml` i `.yaml` z katalogu
`.github/workflows/` — dlatego wariant eco ma końcówkę `.disabled` i po prostu
tam leży. Przełączenie to zamiana końcówek miejscami:

```bash
cd .github/workflows
git mv deploy.yml deploy.yml.disabled
git mv deploy-eco.yml.disabled deploy-eco.yml
```

Powrót — odwrotnie. **Aktywny ma być dokładnie jeden**: gdyby oba miały
końcówkę `.yml`, każdy push wywołałby dwa równoległe wdrożenia na ten sam
serwer (`concurrency` tego nie uratuje — grupy działają w obrębie jednego
workflow, nie między nimi).

## For devs
- venv `python -m venv myenv`
- installing python requirements `pip install -r requirements.txt`
- run `python app.py`
