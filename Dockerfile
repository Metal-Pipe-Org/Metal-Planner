FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# gosu pozwala zejść z roota na użytkownika aplikacji dopiero PO naprawieniu
# praw do podmontowanego ./data - patrz docker/entrypoint.sh.
# Przy okazji dociągamy poprawki bezpieczeństwa bazowego obrazu (upgrade),
# bo tag :slim bywa o kilka tygodni w tyle za łatkami Debiana.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends gosu \
 && rm -rf /var/lib/apt/lists/*

# Zależności osobno od kodu - zmiana w kodzie nie unieważnia warstwy z pipem.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/data \
 && chmod +x /app/docker/entrypoint.sh \
 && chown -R app:app /app

# Bez USER: entrypoint startuje jako root, żeby przejąć na własność
# podmontowany ./data, i sam schodzi na użytkownika app (gosu). Serwer
# aplikacyjny nigdy nie działa jako root.

EXPOSE 8000

# Sonda celowo trafia w /healthz, a nie w "/" - kontener jest zdrowy także
# wtedy, gdy rozkładu jeszcze nie ma i trzeba go dociągnąć z menu dev.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz').read()"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
