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


def test_akcja_wskaznikow_przypieta_do_commitu():
    # Akcja dostaje token z prawem zapisu, a tag da się przesunąć (#532).
    uses = re.findall(r"uses:\s*(benchmark-action/github-action-benchmark@\S+)",
                      (WORKFLOWS / "wskazniki-balaganu.yml").read_text())
    assert uses and all(re.fullmatch(r".+@[0-9a-f]{40}", u) for u in uses), uses


def _data_js(tmp_path, *pomiary):
    import json
    entries = [{"commit": {"id": str(i)}, "benches": [
        {"name": n, "unit": "s" if n == "Testy: czas" else "testy", "value": v, "extra": "baza rozkładu: brak"}
        for n, v in pomiar.items()]} for i, pomiar in enumerate(pomiary)]
    path = tmp_path / "data.js"
    path.write_text("window.BENCHMARK_DATA = " + json.dumps({"entries": {"Wskaźniki bałaganu": entries}}))
    return path


def _karta(path):
    return _bash(f'source "{SCRIPTS}/wskazniki.sh"; DRY_RUN=1 wskazniki_karta "$1" "Wskaźniki bałaganu" 150% Metal-Pipe-Org/Metal-Planner',
                 str(path))


def test_karta_wskaznikow_strzalki_i_alarm(tmp_path):
    out = _karta(_data_js(tmp_path,
                          {"Testy: czerwone": 33, "Testy: błędy ustawienia": 0, "Testy: czas": 412.34, "Linie: static/app.js": 9200},
                          {"Testy: czerwone": 28, "Testy: błędy ustawienia": 2, "Testy: czas": 412.34, "Linie: static/app.js": 9219,
                           "Linie: planner.py": 4978}))
    assert "[warn]" in out
    assert "https://metal-pipe-org.github.io/Metal-Planner/wskazniki/" in out
    assert "czerwone 33 → 28 ▼\n" in out
    assert "błędy ustawienia 0 → 2 ▲ ⚠️" in out
    assert "czas testów 412.3 s → 412.3 s =" in out
    # Na karcie tylko największy plik; reszta jest na wykresach.
    assert "największy plik (static/app.js) 9200 → 9219 ▲" in out and "planner.py" not in out
    assert "bez bazy rozkładu" in out


def test_karta_wskaznikow_pierwszy_pomiar_bez_strzalek(tmp_path):
    out = _karta(_data_js(tmp_path, {"Testy: czerwone": 28}))
    assert "[info]" in out and "czerwone 28\n" in out and "→" not in out


def test_wpisy_benchmarku_w_formacie_akcji():
    import sys
    sys.path.insert(0, str(ROOT))
    from wskazniki_balaganu import wpisy_benchmarku
    wpisy = wpisy_benchmarku({
        "testy": {"czas_s": 12.5, "zaliczone": 9, "czerwone": 1, "bledy_ustawienia": 0, "pominiete": 2,
                  "baza_rozkladu": False, "pominiecia": {}},
        "najwieksze_pliki": {"static/app.js": 9219, "planner.py": 4978},
        "martwy_kod_heurystyka": 1,
    })
    # customSmallerIsBetter: lista {name, unit, value[, extra]}, nazwy są kluczami wykresów.
    assert all(set(w) <= {"name", "unit", "value", "extra"} and {"name", "unit", "value"} <= set(w) for w in wpisy)
    assert len({w["name"] for w in wpisy}) == len(wpisy)
    nazwy = {w["name"]: w["value"] for w in wpisy}
    assert nazwy["Testy: czerwone"] == 1 and nazwy["Testy: czas"] == 12.5 and nazwy["Linie: static/app.js"] == 9219
    assert "baza rozkładu: brak" in wpisy[0]["extra"]
