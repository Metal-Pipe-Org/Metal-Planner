#!/usr/bin/env bash
# Instalacja i aktualizacja Metal-Plannera na serwerze.
#
# Ten sam skrypt robi pierwszą instalację i każdą kolejną aktualizację -
# jest idempotentny, można go puszczać dowolnie często.
#
# Wołany przez GitHub Actions po SSH (.github/workflows/deploy.yml), ale
# działa też ręcznie:
#     curl -fsSL https://raw.githubusercontent.com/Metal-Pipe-Org/Metal-Planner/main/docker/deploy.sh | bash
#
# Cała ciężka robota - klonowanie repo i budowa obrazu - dzieje się tutaj,
# po stronie serwera. Runner GitHuba tylko nawiązuje SSH i czeka, więc zużywa
# kilkanaście sekund zamiast minut na budowanie.

set -euo pipefail

REPO="${REPO:-Metal-Pipe-Org/Metal-Planner}"
# testing jest gałęzią wdrożeniową - to do niej idą pull requesty, a wdrożenie
# rusza dopiero po zmergowaniu. Workflow podaje tu gałąź, która go wywołała.
BRANCH="${BRANCH:-testing}"
APP_DIR="${APP_DIR:-$HOME/metal-planner}"
COMPOSE_URL="https://raw.githubusercontent.com/$REPO/$BRANCH/docker-compose.yml"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:5001/healthz}"
# Pierwsza instalacja buduje obraz i bazę rozkładów - na słabym vCPU
# potrafi to zająć kilka minut, stąd hojny limit.
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-420}"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die()  { printf '\n\033[31mBŁĄD: %s\033[0m\n' "$*" >&2; exit 1; }

# --- warunki wstępne --------------------------------------------------------

command -v curl >/dev/null || die "Brak curla."
command -v docker >/dev/null \
    || die "Brak dockera. Zainstaluj: curl -fsSL https://get.docker.com | sh"
docker compose version >/dev/null 2>&1 \
    || die "Brak wtyczki 'docker compose' (v2). Stare 'docker-compose' nie wystarczy."
docker info >/dev/null 2>&1 \
    || die "Brak dostępu do demona Dockera. Dodaj użytkownika do grupy: sudo usermod -aG docker $USER"

mkdir -p "$APP_DIR"
cd "$APP_DIR"

# Wynik zapisujemy do pliku, bo przy wdrożeniu z GitHub Actions ten skrypt
# leci w tle i nie ma komu odebrać jego kodu wyjścia - zielony przebieg
# w Actions znaczy tylko tyle, że wdrożenie wystartowało.
finish() {
    local code=$?
    if [ "$code" -eq 0 ]; then
        printf 'OK  %s\n' "$(date -Is)" > deploy.status
    else
        printf 'BŁĄD (kod %s)  %s\n' "$code" "$(date -Is)" > deploy.status
    fi
    exit "$code"
}
trap finish EXIT

# --- 1. nowy compose, zanim cokolwiek zatrzymamy ----------------------------
# Kolejność jest celowa: gdyby pobieranie padło PO zatrzymaniu kontenera,
# zostawilibyśmy serwer wyłączony bez pliku, którym da się go wskrzesić.

log "Pobieram docker-compose.yml z gałęzi $BRANCH"
curl -fsSL "$COMPOSE_URL" -o docker-compose.yml.new \
    || die "Nie udało się pobrać $COMPOSE_URL"
[ -s docker-compose.yml.new ] || die "Pobrany docker-compose.yml jest pusty."
docker compose -f docker-compose.yml.new config -q \
    || die "Pobrany docker-compose.yml jest niepoprawny - zostawiam starą wersję."

# Compose ma w sobie gałąź, z której Docker sklonuje kod (build.context).
# Gdyby rozjechała się z gałęzią, którą wdrażamy, wdrożenie po cichu
# postawiłoby inny kod niż ten, który wywołał przebieg - stąd twardy stop.
context_branch="$(grep -oE '\.git#[A-Za-z0-9._/-]+' docker-compose.yml.new \
                  | head -1 | cut -d'#' -f2 || true)"
if [ -n "$context_branch" ] && [ "$context_branch" != "$BRANCH" ]; then
    die "Niezgodność gałęzi: wdrażam '$BRANCH', a docker-compose.yml buduje '$context_branch'."
fi

# --- 2. zatrzymanie działającej instancji -----------------------------------

if [ -f docker-compose.yml ]; then
    log "Zatrzymuję poprzednią wersję"
    # Niepowodzenie nie blokuje wdrożenia: stary plik mógł zostać po nieudanej
    # próbie i nie ma powodu, żeby uniemożliwiał postawienie nowej wersji.
    docker compose down --remove-orphans || true
fi

mv docker-compose.yml.new docker-compose.yml

# --- 3. budowa i start ------------------------------------------------------
# --build klonuje repo z GitHuba i buduje obraz od nowa; folder ./data
# z bazą rozkładów i tokenem zostaje nietknięty.

log "Buduję obraz i uruchamiam (to może potrwać kilka minut)"
docker compose up -d --build

# --- 4. sprzątanie ----------------------------------------------------------
# Każda przebudowa zostawia poprzedni obraz jako <none>. Na 50 GB dysku
# uzbierałoby się to w kilka miesięcy.

log "Usuwam osierocone obrazy"
docker image prune -f >/dev/null

# --- 5. czy naprawdę wstało -------------------------------------------------

log "Czekam, aż aplikacja odpowie na $HEALTH_URL"
deadline=$(( SECONDS + HEALTH_TIMEOUT ))
until curl -fsS "$HEALTH_URL" >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        printf '\n--- ostatnie 60 linii logu kontenera ---\n' >&2
        docker compose logs --tail 60 >&2 || true
        die "Aplikacja nie odpowiedziała w ciągu ${HEALTH_TIMEOUT}s."
    fi
    sleep 3
done

log "Gotowe - aplikacja działa"
docker compose ps

# Token celowo NIE trafia tutaj do wypisania: log tego skryptu ląduje
# w GitHub Actions, a tam sekretów się nie zostawia.
if [ -f data/dev_token.txt ]; then
    echo "Token menu deweloperskiego: $APP_DIR/data/dev_token.txt (odczyt: sudo cat ...)"
fi
