import os

from flask import Flask
from routes import init_routes

app = Flask(__name__)

init_routes(app)

if __name__ == "__main__":
    # Domyślnie 5001, bo 5000 na macOS zajmuje AirPlay Receiver. host="0.0.0.0"
    # (zamiast domyślnego 127.0.0.1) - żeby telefon/inne urządzenie w tej samej
    # sieci Wi-Fi mogło się połączyć pod adresem LAN tego Maca, nie tylko
    # localhost. Patrz PROJECT.md, "Znane ograniczenia" - to też otwiera
    # debugger Werkzeuga (debug=True) na całą sieć lokalną, nie tylko localhost.
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))