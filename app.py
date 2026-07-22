import os

from flask import Flask
from routes import init_routes

app = Flask(__name__)

init_routes(app)

if __name__ == "__main__":
    # Domyślnie 5001, bo 5000 na macOS zajmuje AirPlay Receiver.
    app.run(debug=True, port=int(os.environ.get("PORT", 5001)))