"""Wspólna konfiguracja automatów w `.github/scripts/config.sh` (zgłoszenie #392).

Wartości mają stać w jednym miejscu. Wyjątkiem jest wstępne sito w `if:` workflowów,
które liczy się przed checkoutem i pliku nie widzi, więc trzyma kopię literału — test
pilnuje, żeby kopia nie rozjechała się z configiem. Pilnuje też, żeby żaden skrypt ani
workflow nie przypisywał tych zmiennych na nowo po swojemu.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".github" / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"
CONFIG = SCRIPTS / "config.sh"


def _bash(script, *args):
    return subprocess.run(["bash", "-c", script, "bash", *args], check=True,
                          capture_output=True, text=True, cwd=ROOT).stdout


def _config():
    names = re.findall(r"^([A-Z_]+)=", CONFIG.read_text(), re.M)
    out = _bash(f'source "{CONFIG}"; ' + "; ".join(f'printf "%s\\0" "${n}"' for n in names))
    return dict(zip(names, out.split("\0")))


def test_sito_wolania_claudea_zgodne_z_configiem():
    mention = _config()["CLAUDE_MENTION"]
    for name in ("claude-issue.yml", "claude-review-pr.yml"):
        literals = re.findall(r"contains\(github\.event\.[^)]*body[^)]*,\s*'([^']*)'\)",
                              (WORKFLOWS / name).read_text())
        assert literals, f"{name}: nie znaleziono sita z wołaniem w if:"
        assert set(literals) == {mention}, f"{name}: {literals} zamiast {mention!r}"


def test_sito_etykiety_stale_zgodne_z_configiem():
    label = _config()["LABEL_STALE"]
    literals = re.findall(r"labels\.\*\.name,\s*'([^']*)'\)",
                          (WORKFLOWS / "stale-unlabel.yml").read_text())
    stale_like = [lit for lit in literals if lit not in ("external blocked", "PR in progress")]
    assert stale_like, "stale-unlabel.yml: nie znaleziono sita z etykietą w if:"
    assert set(stale_like) == {label}


def _pr(number, merged, labeled=False, reviews=()):
    return {"number": number, "mergedAt": merged, "updatedAt": merged,
            "labels": {"nodes": [{"name": "merged on credit"}] if labeled else []},
            "reviews": {"nodes": [{"databaseId": i, "state": state, "submittedAt": "2026-11-25T10:00:00Z",
                                   "author": {"login": login, "__typename": kind}, "commit": {"oid": "abc"}}
                                  for i, (login, kind, state) in enumerate(reviews, 1)]}}


def _kredyt(tmp_path, nodes, has_next=False, script="credit_facts"):
    """Odpala funkcję z credit.sh na podstawionym `gh`, który zwraca jedną stronę
    GraphQL (a przy próbie pobrania drugiej wywraca bieg)."""
    page = {"data": {"repository": {"pullRequests": {
        "pageInfo": {"hasNextPage": has_next, "endCursor": "c"}, "nodes": nodes}}}}
    (tmp_path / "page.json").write_text(json.dumps(page))
    fake = tmp_path / "gh"
    fake.write_text('#!/bin/bash\n'
                    'if [ "$1 $2" = "api graphql" ]; then\n'
                    f'  [ ! -e "{tmp_path}/called" ] || exit 99\n'
                    f'  touch "{tmp_path}/called"; cat "{tmp_path}/page.json"\n'
                    'else exit 0; fi\n')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "REPO": "o/r", "DRY_RUN": "1", "ENSURED": ""}
    out = subprocess.run(["bash", "-c", f'set -euo pipefail; source "{SCRIPTS}/approvals.sh"; '
                          f'source "{SCRIPTS}/pr-state.sh"; {script}'],
                         check=True, capture_output=True, text=True, cwd=ROOT, env=env).stdout
    return out


def test_kolejka_kredytu_to_scalone_od_odciecia_bez_zatwierdzenia(tmp_path):
    cutoff = _config()["CREDIT_SINCE"]
    nodes = [
        _pr(1, f"{cutoff}T12:00:00Z"),
        _pr(2, f"{cutoff}T13:00:00Z", labeled=True, reviews=[("ala", "User", "APPROVED")]),
        _pr(3, f"{cutoff}T14:00:00Z", reviews=[("dependabot[bot]", "Bot", "APPROVED")]),
        _pr(4, f"{cutoff}T15:00:00Z", reviews=[("ola", "User", "APPROVED"), ("ola", "User", "DISMISSED")]),
        _pr(5, "2000-01-01T00:00:00Z"),
    ]
    facts = json.loads(_kredyt(tmp_path, nodes))
    assert facts == [
        {"number": 1, "labeled": False, "approved": False},
        {"number": 2, "labeled": True, "approved": True},
        {"number": 3, "labeled": False, "approved": False},
        {"number": 4, "labeled": False, "approved": False},
    ]


def test_kolejka_kredytu_nie_zalezy_od_etykiety(tmp_path):
    cutoff = _config()["CREDIT_SINCE"]
    nodes = [_pr(1, f"{cutoff}T12:00:00Z", labeled=True), _pr(2, f"{cutoff}T13:00:00Z")]
    assert _kredyt(tmp_path, nodes, script="credit_count").strip() == "2"


def test_lista_urywa_sie_na_dniu_odciecia(tmp_path):
    # Strona jest cała starsza niż odcięcie, więc drugiej nie pobieramy
    # (podstawiony `gh` wywraca bieg przy drugim zapytaniu).
    nodes = [_pr(5, "2000-01-01T00:00:00Z")]
    assert json.loads(_kredyt(tmp_path, nodes, has_next=True)) == []


def test_sweep_zgrywa_etykiete_z_faktami_w_obie_strony(tmp_path):
    cutoff = _config()["CREDIT_SINCE"]
    nodes = [
        _pr(1, f"{cutoff}T12:00:00Z"),
        _pr(2, f"{cutoff}T13:00:00Z", labeled=True),
        _pr(3, f"{cutoff}T14:00:00Z", labeled=True, reviews=[("ala", "User", "APPROVED")]),
        _pr(4, f"{cutoff}T15:00:00Z", reviews=[("ala", "User", "APPROVED")]),
    ]
    out = _kredyt(tmp_path, nodes, script="credit_sweep")
    assert "Kolejka kredytu: 2/" in out
    assert "DRY_RUN: #1: dałbym" in out
    assert "DRY_RUN: #3: zdjąłbym" in out
    assert "#2:" not in out and "#4:" not in out


@pytest.mark.parametrize("path", sorted(
    [p for p in SCRIPTS.glob("*.sh") if p != CONFIG] + list(WORKFLOWS.glob("*.yml")),
    key=str), ids=lambda p: p.name)
def test_nikt_nie_przypisuje_wartosci_z_configu_po_swojemu(path):
    text = path.read_text()
    for name in _config():
        # Przypisanie w bashu (`X=`) albo w `env:` workflowu (`X:`).
        assert not re.search(rf"^\s*(?:local\s+|export\s+)?{name}(=|:\s)", text, re.M), \
            f"{path.name} przypisuje {name} — wartość ma stać tylko w config.sh"


def _refs(title, body, branch):
    out = _bash(f'source "{SCRIPTS}/refs.sh"; '
                'keyword_refs "$1" "$2" "$3" | tr "\\n" " "; echo "|"; '
                'mention_refs "$1" "$2" | tr "\\n" " "', title, body, branch)
    keyword, mention = out.split("|")
    return set(keyword.split()), set(mention.split())


def test_slowa_z_configu_rozpoznawane_jak_przedtem():
    body = ("Closes #1\nfixed #2 and RESOLVES:#4\nprefix#9 xcloses #10\n"
            "Refs #5, related   to #6, Part of #7, dotyczy #8, relates to: #11, ref#12")
    keyword, mention = _refs("Fix: #3 coś", body, "claude/issue-42-run1")
    assert keyword == {"1", "2", "3", "4", "42"}
    assert mention == {"5", "6", "7", "8", "11", "12"}


def test_dopisane_slowo_dziala_bez_zmiany_skryptu():
    out = _bash(f'source "{SCRIPTS}/refs.sh"; REFS_MENTION_WORDS="$REFS_MENTION_WORDS|see also"; '
                "mention_refs '' 'See  also #7'")
    assert out.split() == ["7"]
