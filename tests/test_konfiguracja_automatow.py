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


SPRZATANIE = SCRIPTS / "sprzatanie.sh"
OBSZARY = ROOT / ".github" / "sprzatanie-obszary.json"


def _sprzatanie(script, *args, cwd=ROOT, check=True):
    return subprocess.run(["bash", "-c", f'source "{SPRZATANIE}"; {script}', "bash", *args],
                          check=check, capture_output=True, text=True, cwd=cwd)


def test_lista_obszarow_sprzatania():
    import json
    obszary = json.loads(OBSZARY.read_text())
    zakazane = _config()["SPRZATANIE_ZAKAZANE"].split()
    assert len({o["id"] for o in obszary}) == len(obszary)
    for o in obszary:
        assert re.fullmatch(r"[a-z0-9-]+", o["id"]), o["id"]
        assert o["nazwa"] and o["kod"], o
        for f in o["kod"] + o["testy"]:
            assert (ROOT / f).is_file(), f"{o['id']}: nie ma {f}"
            assert not any(f.startswith(z) for z in zakazane), f"{o['id']}: {f} jest zakazane"


def _wybor(tmp_path, week, zajete="{}", wymuszony=""):
    import json
    path = tmp_path / "obszary.json"
    path.write_text(json.dumps([
        {"id": "a", "nazwa": "A", "kod": ["a.py"], "testy": []},
        {"id": "b", "nazwa": "B", "kod": ["b.py"], "testy": [], "wstrzymany": "czeka na #1"},
        {"id": "c", "nazwa": "C", "kod": ["c.py", "c2.py"], "testy": []},
    ]))
    out = _sprzatanie('sprzatanie_wybor "$@"', str(path), str(week), zajete, wymuszony).stdout
    return json.loads(out)["id"] if out.strip() else None


def test_wybor_obszaru_po_numerze_tygodnia(tmp_path):
    assert _wybor(tmp_path, 48) == "a"  # 48 mod 3 = 0
    # Wstrzymany i zajęty odpadają, bierze następny wolny.
    assert _wybor(tmp_path, 49) == "c"
    assert _wybor(tmp_path, 50) == "c"
    assert _wybor(tmp_path, 48, '{"a.py": [7]}') == "c"
    assert _wybor(tmp_path, 48, '{"a.py": [7], "c2.py": [8]}') is None
    # Ręczny bieg wymusza obszar, ale nie zdejmuje blokady.
    assert _wybor(tmp_path, 48, "{}", "c") == "c"
    assert _wybor(tmp_path, 48, "{}", "b") is None


def test_plik_zajety_przez_otwarty_pr(tmp_path):
    import json
    (tmp_path / "duzy.py").write_text("x\n" * 100)
    (tmp_path / "maly.py").write_text("x\n" * 100)
    prs = [{"number": 7, "files": [{"filename": "duzy.py", "status": "modified", "additions": 20, "deletions": 5},
                                   {"filename": "maly.py", "status": "modified", "additions": 3, "deletions": 1}]},
           {"number": 8, "files": [{"filename": "nowy.py", "previous_filename": "stary.py", "status": "renamed",
                                    "additions": 0, "deletions": 0}]}]
    out = subprocess.run(["bash", "-c", f'source "{SPRZATANIE}"; sprzatanie_zajete'], input=json.dumps(prs),
                         check=True, capture_output=True, text=True, cwd=tmp_path).stdout
    assert json.loads(out) == {"duzy.py": [7], "stary.py": [8]}


def test_opiekun_co_tydzien_nastepny_z_kolejki():
    config = _config()
    assert config["SPRZATANIE_OPIEKUNOWIE"].split() == ["EgorexW", "SzymonEls"]
    assert config["SPRZATANIE_START"] == "2026-11-30"

    def opiekun(data):
        return _sprzatanie('sprzatanie_opiekun "$1"', data).stdout.strip()
    # Tydzień od poniedziałku 30.11.2026 (ISO 49) to EgorexW, przed nim też.
    assert [opiekun(d) for d in ("2026-11-20", "2026-11-30", "2026-12-06")] == ["EgorexW"] * 3
    assert [opiekun(d) for d in ("2026-12-07", "2026-12-13", "2026-12-14")] == ["SzymonEls", "SzymonEls", "EgorexW"]
    # 2026 ma 53 tygodnie ISO: kolejka nie powtarza opiekuna na przełomie lat.
    assert [opiekun(d) for d in ("2026-12-28", "2027-01-04")] == ["EgorexW", "SzymonEls"]


def _repo(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    return git


def _commit(git, tmp_path, files, msg):
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    git("add", "-A")
    git("commit", "-q", "-m", msg)
    return git("rev-parse", "HEAD")


def test_bramka_sprzatania(tmp_path):
    git = _repo(tmp_path)
    base = _commit(git, tmp_path, {"a.py": "".join(f"def f{i}():\n    pass\n" for i in range(150)),
                                   "b.py": "x = 1\n", "docs/x.md": "a\n"}, "start")
    area = '{"kod": ["a.py"], "testy": ["tests/test_a.py"]}'

    def bramka(files):
        git("checkout", "-q", base)
        head = _commit(git, tmp_path, files, "zmiana")
        r = _sprzatanie('sprzatanie_bramka "$@"', base, head, area, cwd=tmp_path, check=False)
        return r.returncode, r.stdout

    ok = "".join(f"def f{i}():\n    pass\n" for i in range(130)) + "def g():\n    pass\n"
    assert bramka({"a.py": ok}) == (0, "")
    code, out = bramka({"a.py": ok, "b.py": "", "docs/x.md": ""})
    assert code == 1 and "b.py: poza obszarem" in out and "docs/x.md: tej ścieżki sprzątanie nie rusza" in out
    code, out = bramka({"a.py": ""})
    assert code == 1 and "zmienionych linii 300" in out
    code, out = bramka({"a.py": ok.replace("pass", "return None")})
    assert code == 1 and "sprzątanie ma usuwać" in out


def _junit(path, wyniki):
    cases = []
    for nazwa, stan in wyniki.items():
        plik, test = nazwa.split("::")
        cls = plik.removesuffix(".py").replace("/", ".")
        inner = {"zielony": "", "czerwony": "<failure message='x'/>", "pominiety": "<skipped message='x'/>"}[stan]
        cases.append(f'<testcase classname="{cls}" name="{test}">{inner}</testcase>')
    path.write_text(f'<testsuites><testsuite>{"".join(cases)}</testsuite></testsuites>')
    return str(path)


def test_porownanie_testow_przed_i_po(tmp_path):
    import json
    przed = _junit(tmp_path / "przed.xml", {
        "tests/test_a.py::test_1": "zielony", "tests/test_a.py::test_stary": "zielony",
        "tests/test_a.py::test_czerwony": "czerwony", "tests/test_b.py::test_2": "zielony",
        "tests/test_b.py::test_3": "zielony", "tests/test_c.py::test_4": "zielony"})
    po = _junit(tmp_path / "po.xml", {
        "tests/test_a.py::test_1": "zielony", "tests/test_b.py::test_2": "czerwony"})
    out = _sprzatanie('sprzatanie_testy "$(sprzatanie_wyniki "$1")" "$(sprzatanie_wyniki "$2")" "$3"',
                      przed, po, "a.py\ntests/test_a.py").stdout
    assert json.loads(out) == {
        "zielone": 5,
        # test_3 i test_4 zniknęły z plików, których sprzątanie nie ruszało — to awaria, nie usunięcie.
        "zepsute": ["tests/test_b.py::test_2", "tests/test_b.py::test_3", "tests/test_c.py::test_4"],
        "usuniete": ["tests/test_a.py::test_stary"],
        "usuniete_czerwone": ["tests/test_a.py::test_czerwony"]}


def test_kolizja_z_otwartym_pr(tmp_path):
    git = _repo(tmp_path)
    base = _commit(git, tmp_path, {"a.py": "def martwa_funkcja():\n    pass\n\n\ndef zywa():\n    pass\n",
                                   "b.py": "x = 1\n"}, "start")
    git("branch", "pr-uzywa")
    git("branch", "pr-konflikt")
    git("branch", "pr-inny")
    sprzatanie = _commit(git, tmp_path, {"a.py": "def zywa():\n    pass\n"}, "sprzątanie")
    git("checkout", "-q", "pr-uzywa")
    _commit(git, tmp_path, {"b.py": "x = 1\nmartwa_funkcja()\n"}, "używa usuniętej")
    git("checkout", "-q", "pr-konflikt")
    _commit(git, tmp_path, {"a.py": "def martwa_funkcja():\n    return 2\n\n\ndef zywa():\n    pass\n"}, "zmienia usuwane")
    git("checkout", "-q", "pr-inny")
    _commit(git, tmp_path, {"b.py": "x = 2\n"}, "nie gryzie się")

    zniknione = _sprzatanie('sprzatanie_zniknione "$@"', base, sprzatanie, cwd=tmp_path).stdout
    assert zniknione.split() == ["martwa_funkcja"]

    def kolizja(pr):
        r = _sprzatanie('sprzatanie_kolizja "$@"', base, sprzatanie, pr, zniknione, cwd=tmp_path, check=False)
        return r.returncode, r.stdout
    assert kolizja("pr-inny") == (0, "")
    code, out = kolizja("pr-uzywa")
    assert code == 1 and "martwa_funkcja" in out
    code, out = kolizja("pr-konflikt")
    assert code == 1 and "konflikt w plikach: a.py" in out


def test_link_do_pr_sprzatania():
    from urllib.parse import parse_qs, urlsplit
    url = _sprzatanie('sprzatanie_link "$@"', "o/r", "sprzatanie/pkp-2026-11-30", "Sprzątanie: PKP",
                      "Closes #600\n\nDla pasażera & reszta").stdout.strip()
    parts = urlsplit(url)
    assert parts.path == "/o/r/compare/main...sprzatanie/pkp-2026-11-30"
    q = parse_qs(parts.query)
    assert q == {"quick_pull": ["1"], "title": ["Sprzątanie: PKP"], "body": ["Closes #600\n\nDla pasażera & reszta"]}


def _zglos(wynik, tydzien, nazwa, dane, galaz=""):
    """Zgłoszenie tygodnia w DRY_RUN: tytuł, wywołania API i adres."""
    import json
    import os
    dane = {"opiekun": "SzymonEls", "bieg": "https://github.com/o/r/actions/runs/1", "nazwa": nazwa, **dane}
    r = subprocess.run(["bash", "-c", f'source "{SPRZATANIE}"; t=$(sprzatanie_tytul "$1" "$2" "$3"); '
                        'echo "$t"; sprzatanie_zglos "$1" "$t" "$4" "$5"', "bash",
                        wynik, tydzien, nazwa, json.dumps(dane), galaz],
                       check=True, capture_output=True, text=True, cwd=ROOT,
                       env={**os.environ, "DRY_RUN": "1", "REPO": "o/r", "GH_TOKEN": ""})
    tytul, url = r.stdout.strip().split("\n")
    calls = []
    for line in r.stderr.splitlines():
        assert line.startswith("DRY_RUN: "), line
        method, path, payload = line.removeprefix("DRY_RUN: ").split(" ", 2)
        calls.append((method, path, json.loads(payload)))
    assert url == "https://github.com/o/r/issues/0"
    return tytul, calls


def _zgloszenie_tygodnia(calls, tytul, opiekun):
    """Wspólne dla każdego wyniku: jedno nowe, otwarte zgłoszenie z opiekunem, typem i etykietą."""
    config = _config()
    nowe = [c for c in calls if c[:2] == ("POST", "repos/o/r/issues")]
    assert len(nowe) == 1
    assert nowe[0][2]["title"] == tytul
    assert nowe[0][2]["assignees"] == [opiekun]
    # Otwarte: nic nie zmienia stanu ani go nie zamyka.
    assert not any("state" in c[2] for c in calls)
    assert ("PATCH", "repos/o/r/issues/0", {"type": "Task"}) in calls and config["SPRZATANIE_TYP"] == "Task"
    assert ("POST", "repos/o/r/issues/0/labels", {"labels": ["claude"]}) in calls
    assert config["SPRZATANIE_ETYKIETA"] == "claude"
    bodies = [c[2]["body"] for c in calls if "body" in c[2]]
    body = bodies[-1]
    assert f"z #{config['SPRZATANIE_ZGLOSZENIE']}." in body and f"@{opiekun}" in body
    return body


def test_zgloszenie_tygodnia_gotowa_zmiana():
    from urllib.parse import parse_qs, urlsplit
    tytul, calls = _zglos("pr", "49", "PKP", {
        "opis": "Dla pasażera nic się nie zmienia.\n- `foo()` — nieużywana",
        "testy": "Testy: 5 zielonych na `main` przed zmianą — wszystkie dalej zielone."},
        "sprzatanie/pkp-2026-11-30")
    assert tytul == "Sprzątanie: PKP (tydzień 49)"
    body = _zgloszenie_tygodnia(calls, tytul, "SzymonEls")
    assert "Link do założenia PR-a pojawi się tu za chwilę." in calls[0][2]["body"]
    for part in ("- `foo()` — nieużywana", "Testy: 5 zielonych"):
        assert part in body, part
    link = re.search(r"\[Otwórz PR do `main`\]\(([^)]+)\)", body).group(1)
    parts = urlsplit(link)
    assert parts.path == "/o/r/compare/main...sprzatanie/pkp-2026-11-30"
    pr_body = parse_qs(parts.query)["body"][0]
    assert pr_body.startswith("Closes #0\n\nDla pasażera nic się nie zmienia.")

    # Opis PR-a przechodzi pr-syntax.yml: wskazuje tylko zgłoszenie tygodnia (opiekun jest do niego
    # przypisany), nie #531, do którego przypisany być nie musi.
    refs = _bash(f'source "{SCRIPTS}/refs.sh"; keyword_refs "$1" "$2" "$3"; echo "|"; mention_refs "$1" "$2"',
                 "Sprzątanie: PKP", pr_body, "sprzatanie/pkp-2026-11-30")
    assert refs.split() == ["0", "|"]


def test_zgloszenie_tygodnia_odrzucone_przez_bramke():
    tytul, calls = _zglos("odrzucone", "50", "PKP", {
        "powod": "zakres albo wielkość zmiany poza progami",
        "lista": "b.py: poza obszarem\nzmienionych linii 300, a wolno najwyżej 200",
        "zmiany": " pkp.py | 300 ---\n 1 file changed, 300 deletions(-)",
        "opis": "Dla pasażera nic się nie zmienia."})
    assert tytul == "Sprzątanie: PKP (tydzień 50) — odrzucone przez bramkę"
    body = _zgloszenie_tygodnia(calls, tytul, "SzymonEls")
    for part in ("zakres albo wielkość zmiany poza progami", "- b.py: poza obszarem",
                 "- zmienionych linii 300, a wolno najwyżej 200", "1 file changed, 300 deletions(-)",
                 "przez 7 dni", "](https://github.com/o/r/actions/runs/1)"):
        assert part in body, part
    assert "Otwórz PR" not in body


def test_zgloszenie_tygodnia_nic_do_sprzatania():
    tytul, calls = _zglos("nic", "51", "Polinka", {"powod": "Każda funkcja ma wywołanie w app.py."})
    assert tytul == "Sprzątanie: Polinka (tydzień 51) — nic do sprzątania"
    body = _zgloszenie_tygodnia(calls, tytul, "SzymonEls")
    assert "„Polinka”" in body and "> Każda funkcja ma wywołanie w app.py." in body


def test_zgloszenie_tygodnia_brak_wolnego_obszaru():
    tytul, calls = _zglos("zablokowane", "52", "", {
        "opiekun": "EgorexW", "lista": "PKP: zajęte pkp.py (PR #7)\nPolinka: wstrzymany — czeka na #1"})
    assert tytul == "Sprzątanie (tydzień 52) — brak wolnego obszaru"
    body = _zgloszenie_tygodnia(calls, tytul, "EgorexW")
    assert "- PKP: zajęte pkp.py (PR #7)" in body and "- Polinka: wstrzymany — czeka na #1" in body


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
