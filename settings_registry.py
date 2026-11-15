"""Rejestr ustawień z `settings.json` (zgłoszenie #478).

Nazwy, opisy i tagi ustawień żyją w jednym pliku, a szablon tylko ustawia
kontrolki. Tu plik jest czytany i przerabiany na to, czego potrzebuje strona:
tagi trafiają do `data-labels`, tag `dev` zamienia się w klasę `dev-only`,
a ustawienie `obsolete` traci inne tagi, dostaje odnośnik do zgłoszenia
i znika z menu po miesiącu od wpisanej daty.
"""

import calendar
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

REGISTRY_FILE = Path(__file__).resolve().parent / "settings.json"
SCHEMA_FILE = REGISTRY_FILE.with_name("settings.schema.json")
ISSUE_URL = "https://github.com/Metal-Pipe-Org/Metal-Planner/issues/new"


def load():
    """Wpisy rejestru w kolejności z pliku (pole `$schema` służy tylko edytorowi)."""
    return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))["settings"]


def one_month_later(day):
    month = day.month % 12 + 1
    year = day.year + (day.month == 12)
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def issue_link(entry):
    title = f"Do dyskusji: ustawienie {entry['name']} oznaczone jako obsolete"
    body = f"{title}\n\nId ustawienia: {entry['id']}\nObsolete od: {entry['obsolete']}\n"
    return f"{ISSUE_URL}?{urlencode({'title': title, 'body': body})}"


def view(today=None):
    """id -> to, co szablon wstawia do wiersza ustawienia."""
    today = today or date.today()
    result = {}
    for entry in load():
        obsolete = "obsolete" in entry
        expired = obsolete and today >= one_month_later(date.fromisoformat(entry["obsolete"]))
        tags = ["obsolete"] if obsolete else entry.get("tags", [])
        result[entry["id"]] = {
            "name": entry["name"],
            "description": entry.get("description"),
            "labels": ", ".join(tag for tag in tags if tag != "dev"),
            "dev": "dev" in tags,
            "obsolete": entry.get("obsolete"),
            "expired": expired,
            "issue_url": issue_link(entry) if obsolete else None,
        }
    return result
