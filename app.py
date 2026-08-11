import mimetypes
import os

from flask import Flask
from routes import init_routes

# Python nie zna tego rozszerzenia, a manifest podany jako octet-stream bywa
# przez przeglądarki ignorowany.
mimetypes.add_type("application/manifest+json", ".webmanifest")

app = Flask(__name__)

init_routes(app)

if __name__ == "__main__":
    # Domyślnie 5001, bo 5000 na macOS zajmuje AirPlay Receiver.
    app.run(debug=True, port=int(os.environ.get("PORT", 5001)))