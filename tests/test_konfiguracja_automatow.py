"""Wspólna konfiguracja automatów w `.github/scripts/config.sh` (zgłoszenie #392).

Wartości mają stać w jednym miejscu. Wyjątkiem jest wstępne sito w `if:` workflowów,
które liczy się przed checkoutem i pliku nie widzi, więc trzyma kopię literału — test
pilnuje, żeby kopia nie rozjechała się z configiem. Pilnuje też, żeby żaden skrypt ani
workflow nie przypisywał tych zmiennych na nowo po swojemu.
"""

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
