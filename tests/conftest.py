import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import gtfs


@pytest.fixture
def install_day(monkeypatch):
    """install_day(day) podmienia gtfs.load_day tak, żeby plan_flow/plan_route
    dostawały syntetyczny DayData zamiast czytać SQLite."""
    def _install(day):
        monkeypatch.setattr(gtfs, "load_day", lambda d: day)
    return _install
