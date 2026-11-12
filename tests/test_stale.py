"""Decyzje o ciszy w `.github/scripts/stale.sh` (#427, #473).

Wołamy funkcje basha na zmyślonych odpowiedziach API; daty liczymy względem `now`,
bo jq liczy ciszę od chwili wywołania.
"""

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".github" / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"


def _iso(days_ago):
    t = datetime.now(timezone.utc) - timedelta(days=days_ago, minutes=5)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(tmp_path, issue, events, call):
    (tmp_path / "i.json").write_text(json.dumps(issue))
    (tmp_path / "t.json").write_text(json.dumps(events))
    out = subprocess.run(
        ["bash", "-c", f'source "{SCRIPTS}/stale.sh"; {call}', "bash",
         str(tmp_path / "i.json"), str(tmp_path / "t.json")],
        check=True, capture_output=True, text=True, cwd=ROOT).stdout
    return out.strip()


def _action(tmp_path, issue, events):
    return _run(tmp_path, issue, events,
                'stale_action "$1" "$2" "$MARKER" "$LABEL_STALE" "$DAYS_QUIET" "$DAYS_GRACE"')


def _issue(created=40, labels=()):
    return {"created_at": _iso(created), "labels": [{"name": n} for n in labels]}


def _comment(days_ago, body, bot=False):
    return {"event": "commented", "created_at": _iso(days_ago), "body": body,
            "user": {"type": "Bot" if bot else "User"}}


def _labeled(days_ago, bot):
    return {"event": "labeled", "created_at": _iso(days_ago), "label": {"name": "stale"},
            "actor": {"type": "Bot" if bot else "User"}}


def _marker(name):
    return re.search(rf'^{name}="(.*)"$', (SCRIPTS / "stale.sh").read_text(), re.M).group(1)


def test_znaczniki_sa_rozne():
    assert _marker("MARKER") not in _marker("MARKER_REVIEW")
    assert _marker("MARKER_REVIEW") not in _marker("MARKER")


def test_przypiecie_do_sprintu_nie_jest_aktywnoscia(tmp_path):
    events = [_comment(10, "x"), {"event": "milestoned", "created_at": _iso(0),
                                   "actor": {"type": "User"}, "milestone": {"title": "Sprint"}}]
    assert _run(tmp_path, _issue(), events, 'quiet_days "$1" "$2"') == "10"


def test_etykieta_ze_spoznionej_recenzji_znika_gdy_pr_wraca_do_autora(tmp_path):
    events = [_comment(5, "x"), _labeled(3, bot=True),
              _comment(3, _marker("MARKER_REVIEW") + "\n@a recenzja czeka", bot=True)]
    assert _action(tmp_path, _issue(labels=["stale"]), events) == "unlabel"


def test_etykieta_ze_spoznionej_recenzji_bez_komentarza_tez_znika(tmp_path):
    events = [_comment(5, "x"), _labeled(3, bot=True)]
    assert _action(tmp_path, _issue(labels=["stale"]), events) == "unlabel"


def test_komentarz_do_recenzentow_nie_odlicza_zamkniecia_autora(tmp_path):
    # Dawny komentarz recenzyjny przy 30 dniach ciszy: autor dostaje pierwsze
    # przypomnienie (ping), a nie zamknięcie.
    events = [_comment(30, "x"), _labeled(25, bot=True),
              _comment(25, _marker("MARKER_REVIEW") + "\n@a", bot=True)]
    assert _action(tmp_path, _issue(labels=["stale"]), events) == "ping"


def test_etykieta_nadana_recznie_zostaje(tmp_path):
    events = [_comment(5, "x"), _labeled(3, bot=False)]
    assert _action(tmp_path, _issue(labels=["stale"]), events) == "none"


def test_zamkniecie_autora_dalej_liczy_sie_od_znacznika_autora(tmp_path):
    events = [_comment(30, "x"), _labeled(10, bot=True),
              _comment(10, _marker("MARKER") + "\n@a", bot=True)]
    assert _action(tmp_path, _issue(labels=["stale"]), events) == "close"


@pytest.mark.parametrize("name", ["stale-issues.yml", "stale-prs.yml", "stale-unlabel.yml"])
def test_workflow_zna_otwarty_sprint(name):
    assert '.milestone.state == "open"' in (WORKFLOWS / name).read_text()


def test_unlabel_reaguje_na_przypiecie_do_sprintu():
    text = (WORKFLOWS / "stale-unlabel.yml").read_text()
    assert len(re.findall(r"types: \[[^\]]*milestoned", text)) == 2
