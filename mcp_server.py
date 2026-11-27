"""
Endpoint MCP: Claude (albo inny klient MCP) pyta wyszukiwarkę przez narzędzia.

Transport Streamable HTTP w wersji bezstanowej: każda wiadomość to jeden POST
z JSON-RPC, a odpowiedź to zwykły JSON - bez sesji i bez strumienia zdarzeń,
więc chodzi na tych samych workerach gunicorna co reszta aplikacji. Przy tej
skali (initialize, ping, tools/list, tools/call) protokół jest mówiony tu
wprost, a nie przez pakiet `mcp`, który zakłada serwer ASGI - a Flask nim nie
jest. Ten sam układ ma AI-productivity-app (app/mcp/routes.py).

Endpoint jest publiczny, bez tokena: wszystko, co dają narzędzia, i tak leży
pod /api/* dla każdego, kto otworzy stronę, a żadne narzędzie niczego nie
zapisuje. Co narzędzia pokazują - w mcp_tools.py.

Dodanie do Claude Code:

    claude mcp add --transport http metal-planner https://<host>/mcp

a na claude.ai: Ustawienia → Konektory → Dodaj własny, z tym samym adresem.
"""

import json
import logging
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request

import mcp_tools


mcp_bp = Blueprint("mcp", __name__)
log = logging.getLogger(__name__)

# Od najnowszej; pierwszą dostaje klient, który prosi o coś innego.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def init_mcp(app):
    app.register_blueprint(mcp_bp)


@mcp_bp.route("/mcp", methods=["POST"])
def endpoint():
    if not _origin_allowed():
        return jsonify(_error(None, INVALID_REQUEST, "Origin not allowed.")), 403

    try:
        message = json.loads(request.get_data(as_text=True) or "null")
    except ValueError:
        return jsonify(_error(None, PARSE_ERROR, "Body is not JSON.")), 400

    if isinstance(message, list):
        if not message:
            return jsonify(_error(None, INVALID_REQUEST, "Empty batch.")), 400
        answers = [answer for answer in (_handle(entry) for entry in message) if answer]
        return (jsonify(answers), 200) if answers else ("", 202)

    answer = _handle(message)
    return (jsonify(answer), 200) if answer else ("", 202)


# Strumienia zdarzeń do otwarcia ani sesji do zamknięcia nie ma: transport
# każe wtedy odpowiadać 405, co mówi klientowi, żeby został przy POST-ach.
@mcp_bp.route("/mcp", methods=["GET", "DELETE"])
def no_stream():
    return jsonify({"error": "Ten serwer MCP odpowiada tylko na POST."}), 405


def _origin_allowed():
    """Odmowa dla strony z innej witryny, o co transport prosi serwery.

    Klienci MCP nie wysyłają Origin; przeglądarka wysyła zawsze, a obcy
    Origin to czyjaś strona próbująca szczęścia. Dane są publiczne, ale
    tę furtkę i tak lepiej mieć zamkniętą - tak każe specyfikacja.
    """
    origin = request.headers.get("Origin")
    if not origin:
        return True
    return urlsplit(origin).netloc == request.host


def _handle(message):
    """Jedna wiadomość JSON-RPC na wejściu, jej odpowiedź na wyjściu - albo
    None dla powiadomienia."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "Not a JSON-RPC 2.0 message.")

    method = message.get("method")
    if "id" not in message:
        # Powiadomienie ("initialized", "cancelled") albo odpowiedź klienta;
        # żadne nie czeka na odpowiedź, a ten serwer o nic nie pyta.
        return None
    request_id = message["id"]
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, INVALID_PARAMS, "Params must be an object.")

    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(request_id, {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "metal-planner", "title": "Metal-Planner", "version": "1"},
            "instructions": mcp_tools.INSTRUCTIONS,
        })
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": mcp_tools.definitions()})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _error(request_id, INVALID_PARAMS, "Arguments must be an object.")
        if not mcp_tools.exists(name):
            return _error(request_id, INVALID_PARAMS, f"Unknown tool: {name}")
        try:
            return _result(request_id, mcp_tools.call(name, arguments))
        except Exception:  # noqa: BLE001 - jedno zepsute narzędzie nie kładzie serwera
            log.exception("Narzędzie MCP %s padło", name)
            return _error(request_id, INTERNAL_ERROR, "The tool failed on the server.")
    return _error(request_id, METHOD_NOT_FOUND, f"Unknown method: {method}")


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
