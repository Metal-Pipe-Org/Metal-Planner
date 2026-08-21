import mimetypes
import os

from flask import Flask

# Przed resztą importów aplikacji: data/.env ma być wczytany, zanim
# którykolwiek moduł sięgnie po os.environ.
import config  # noqa: F401
import update_gtfs
from routes import init_routes

# Python nie zna tego rozszerzenia, a manifest podany jako octet-stream bywa
# przez przeglądarki ignorowany.
mimetypes.add_type("application/manifest+json", ".webmanifest")

app = Flask(__name__)

init_routes(app)

if __name__ == "__main__":
    # Odświeżenie rozkładu przy starcie - tylko dla uruchomienia lokalnego.
    # W kontenerze ten plik nie jest punktem wejścia (startuje gunicorn),
    # a aktualizację odpala wcześniej docker/entrypoint.sh.
    #
    # Warunek na WERKZEUG_RUN_MAIN: w trybie debug reloader trzyma dwa procesy
    # (nadzorca + właściwy serwer) i oba wykonują ten blok. Bez tego dwie
    # przebudowy pisałyby równolegle do jednego gtfs_new.sqlite.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        update_gtfs.refresh_on_start()
        # Codzienna aktualizacja o GTFS_AUTO_UPDATE_HOUR. Lokalnie ta zmienna
        # zwykle nie jest ustawiona i wtedy to nic nie robi - jest tu po to,
        # żeby zachowanie obu sposobów uruchomienia było jedno, a harmonogram
        # dało się sprawdzić bez kontenera.
        update_gtfs.start_daily_scheduler()

    # Domyślnie 5001, bo 5000 na macOS zajmuje AirPlay Receiver.
    app.run(debug=True, port=int(os.environ.get("PORT", 5001)))
