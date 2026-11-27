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


# Chwila merge'a PR-a CREDIT_PR, którą podstawiony `gh` podaje jako start kolejki.
START = "2026-11-28T09:00:00Z"
DAY = START[:10]

# Uprawnienia w repo, które podaje podstawiony `gh` (`collaborators/…/permission`);
# kto nie jest tu wpisany, ma `write`.
PERMISSIONS = {"obcy": "read", "obca": "none"}

FAKE_GH = r"""#!/usr/bin/env python3
# Podstawiony `gh`: odpowiedzi z responses.json po ścieżce API (klucz z `*` na
# końcu pasuje po początku), GraphQL z page.json — tylko raz na bieg.
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[2:]
with_value = {"-X", "--input", "--jq", "-f", "-H", "-q"}
path, i = None, 0
while i < len(args):
    if args[i] in with_value:
        i += 2
        continue
    if not args[i].startswith("-"):
        path = path or args[i]
    i += 1
if path == "graphql":
    mark = os.path.join(here, "called")
    if os.path.exists(mark):
        sys.exit(99)
    open(mark, "w").close()
    sys.stdout.write(open(os.path.join(here, "page.json")).read())
    sys.exit(0)
responses = json.load(open(os.path.join(here, "responses.json")))
for key, value in responses.items():
    if key == path or (key.endswith("*") and path.startswith(key[:-1])):
        if isinstance(value, str):
            print(value)
        else:
            if "--slurp" in args:
                value = [value]
            print(json.dumps(value))
        sys.exit(0)
if "/collaborators/" in (path or ""):
    login = path.split("/collaborators/")[1].split("/")[0]
    print(json.load(open(os.path.join(here, "permissions.json"))).get(login, "write"))
"""


def _run(tmp_path, script, responses=None, nodes=(), has_next=False, start=START, check=True):
    """Odpala skrypt z `.github/scripts` (po `source` jak w pr-state.yml) na
    podstawionym `gh` i zwraca wynik biegu."""
    page = {"data": {"repository": {"pullRequests": {
        "pageInfo": {"hasNextPage": has_next, "endCursor": "c"}, "nodes": list(nodes)}}}}
    (tmp_path / "page.json").write_text(json.dumps(page))
    (tmp_path / "responses.json").write_text(json.dumps(
        {"repos/o/r/pulls/507": start, **(responses or {})}))
    (tmp_path / "permissions.json").write_text(json.dumps(PERMISSIONS))
    fake = tmp_path / "gh"
    fake.write_text(FAKE_GH)
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "REPO": "o/r", "DRY_RUN": "1",
           "ENSURED": "", "TEAM_CACHE": str(tmp_path / "team-cache")}
    return subprocess.run(["bash", "-c", f'set -euo pipefail; source "{SCRIPTS}/statuses.sh"; '
                           f'source "{SCRIPTS}/approvals.sh"; source "{SCRIPTS}/pr-state.sh"; '
                           f'source "{SCRIPTS}/drafts.sh"; {script}'],
                          check=check, capture_output=True, text=True, cwd=ROOT, env=env)


def _pr(number, merged, labeled=False, reviews=()):
    return {"number": number, "mergedAt": merged, "updatedAt": merged,
            "labels": {"nodes": [{"name": "merged on credit"}] if labeled else []},
            "reviews": {"nodes": [{"databaseId": i, "state": state, "submittedAt": "2026-11-29T10:00:00Z",
                                   "author": {"login": login, "__typename": kind}, "commit": {"oid": "abc"}}
                                  for i, (login, kind, state) in enumerate(reviews, 1)]}}


def _kredyt(tmp_path, nodes, has_next=False, script="credit_facts"):
    return _run(tmp_path, script, nodes=nodes, has_next=has_next).stdout


def test_kolejka_kredytu_to_scalone_od_merge_a_bez_zatwierdzenia_zespolu(tmp_path):
    nodes = [
        _pr(1, f"{DAY}T12:00:00Z"),
        _pr(2, f"{DAY}T13:00:00Z", labeled=True, reviews=[("ala", "User", "APPROVED")]),
        _pr(3, f"{DAY}T14:00:00Z", reviews=[("dependabot[bot]", "Bot", "APPROVED")]),
        _pr(4, f"{DAY}T15:00:00Z", reviews=[("ola", "User", "APPROVED"), ("ola", "User", "DISMISSED")]),
        # Zatwierdzenie konta spoza zespołu nie zwalnia miejsca.
        _pr(6, f"{DAY}T16:00:00Z", reviews=[("obcy", "User", "APPROVED")]),
        # Ten sam dzień, ale przed merge'em CREDIT_PR — poza kolejką.
        _pr(7, f"{DAY}T08:59:59Z"),
        _pr(5, "2000-01-01T00:00:00Z"),
    ]
    facts = json.loads(_kredyt(tmp_path, nodes))
    assert facts == [
        {"number": 1, "labeled": False, "approved": False},
        {"number": 2, "labeled": True, "approved": True},
        {"number": 3, "labeled": False, "approved": False},
        {"number": 4, "labeled": False, "approved": False},
        {"number": 6, "labeled": False, "approved": False},
    ]


def test_start_kolejki_to_merge_pr_a_kredytu_z_api(tmp_path):
    assert _config()["CREDIT_PR"] == "507"
    assert _run(tmp_path, "credit_since").stdout.strip() == START
    # Niescalony PR startu wywraca bieg, zamiast liczyć kolejkę od zera.
    res = _run(tmp_path, "credit_count", start="", check=False)
    assert res.returncode != 0
    assert "nie jest scalony" in res.stderr


def test_kolejka_kredytu_nie_zalezy_od_etykiety(tmp_path):
    nodes = [_pr(1, f"{DAY}T12:00:00Z", labeled=True), _pr(2, f"{DAY}T13:00:00Z")]
    assert _kredyt(tmp_path, nodes, script="credit_count").strip() == "2"


def test_lista_urywa_sie_na_starcie_kolejki(tmp_path):
    # Strona jest cała starsza niż start, więc drugiej nie pobieramy
    # (podstawiony `gh` wywraca bieg przy drugim zapytaniu).
    nodes = [_pr(5, "2000-01-01T00:00:00Z")]
    assert json.loads(_kredyt(tmp_path, nodes, has_next=True)) == []


def test_sweep_zgrywa_etykiete_z_faktami_w_obie_strony(tmp_path):
    nodes = [
        _pr(1, f"{DAY}T12:00:00Z"),
        _pr(2, f"{DAY}T13:00:00Z", labeled=True),
        _pr(3, f"{DAY}T14:00:00Z", labeled=True, reviews=[("ala", "User", "APPROVED")]),
        _pr(4, f"{DAY}T15:00:00Z", reviews=[("ala", "User", "APPROVED")]),
        # Zatwierdzenie spoza zespołu nie zdejmuje etykiety.
        _pr(5, f"{DAY}T16:00:00Z", labeled=True, reviews=[("obca", "User", "APPROVED")]),
    ]
    out = _kredyt(tmp_path, nodes, script="credit_sweep")
    assert "Kolejka kredytu: 3/" in out
    assert "DRY_RUN: #1: dałbym" in out
    assert "DRY_RUN: #3: zdjąłbym" in out
    assert "#2:" not in out and "#4:" not in out and "#5:" not in out


def _review(rid, login, state, commit="head"):
    return {"id": rid, "state": state, "submitted_at": f"2026-11-29T10:00:{rid:02d}Z",
            "commit_id": commit, "user": {"login": login, "type": "User"}}


def test_recenzje_spoza_zespolu_nie_licza_sie_nigdzie(tmp_path):
    reviews = [_review(1, "obcy", "APPROVED"), _review(2, "obca", "CHANGES_REQUESTED"),
               _review(3, "ala", "APPROVED")]
    res = _run(tmp_path, "pr_reviews 9 | review_summary; human_approvals 9",
               responses={"repos/o/r/pulls/9/reviews": reviews})
    summary, approvals = res.stdout.strip().split("\n")
    assert json.loads(summary) == {"approvals": 1, "approved_commits": ["head"], "changes_requested": []}
    assert approvals == "1"
    # O każdą osobę pytamy raz: druga funkcja bierze uprawnienia z pliku.
    cache = (tmp_path / "team-cache").read_text().splitlines()
    assert sorted(cache) == ["ala\twrite", "obca\tnone", "obcy\tread"]


def _credit_status(tmp_path, approved=(), changes=(), review=None, queue=3, team=True):
    summary = {"approvals": len(approved), "approved_commits": list(approved), "changes_requested": list(changes)}
    statuses = [] if review is None else [{"context": "Ocena Claude'a", **review}]
    out = subprocess.run(
        ["bash", "-c", f'source "{SCRIPTS}/statuses.sh"; source "{SCRIPTS}/approvals.sh"; '
         f'source "{SCRIPTS}/credit.sh"; credit_status "$1" "$2" "$3" head "$4"', "bash",
         json.dumps(summary), json.dumps(statuses), str(queue), "true" if team else "false"],
        check=True, capture_output=True, text=True, cwd=ROOT,
        env={**os.environ, "TEAM_CACHE": str(tmp_path / "team-cache")}).stdout
    return json.loads(out)


GREEN = {"state": "success", "description": "Ocena gotowa: ✅ Bez zastrzeżeń", "creator": "github-actions[bot]"}
YELLOW = {**GREEN, "description": "Ocena gotowa: ⚠️ Drobne uwagi"}
RUNNING = {"state": "pending", "description": "Claude ocenia ten PR", "creator": "github-actions[bot]"}


def test_zatwierdzenie_sprzed_pushu_liczy_sie_dopiero_po_ocenie(tmp_path):
    # Zatwierdzenie na czubku liczy się od razu.
    assert _credit_status(tmp_path, approved=["head"])["state"] == "success"
    # Sprzed pushu: nie, dopóki ocena czubka nie rozstrzygnie o zatwierdzeniach…
    stale = _credit_status(tmp_path, approved=["old"], review=RUNNING)
    assert stale["state"] == "pending" and "sprzed ostatnich zmian" in stale["description"]
    assert _credit_status(tmp_path, approved=["old"])["state"] == "pending"
    # …a po skończonej ocenie zostawione zatwierdzenie znów się liczy, także
    # przy werdykcie innym niż 🟢 (takie workflow oceny by cofnął).
    assert _credit_status(tmp_path, approved=["old"], review=YELLOW) == \
        {"state": "success", "description": "Zatwierdzenie człowieka"}


def test_kredyt_tylko_z_prawdziwej_zielonej_oceny_i_dla_zespolu(tmp_path):
    assert _credit_status(tmp_path, review=GREEN) == {"state": "success", "description": "kredyt: 3/15 w kolejce"}
    assert _credit_status(tmp_path, review=GREEN, queue=15)["state"] == "pending"
    assert _credit_status(tmp_path, review=YELLOW)["state"] == "pending"
    outsider = _credit_status(tmp_path, review=GREEN, team=False)
    assert outsider["state"] == "pending" and "prawem zapisu" in outsider["description"]
    forged = _credit_status(tmp_path, review={**GREEN, "creator": "ktos"})
    assert forged["state"] == "pending" and "GitHub Actions" in forged["description"]
    # Podrobiona ocena nie robi też z zatwierdzenia sprzed pushu aktualnego.
    assert _credit_status(tmp_path, approved=["old"], review={**GREEN, "creator": "ktos"})["state"] == "pending"
    blocked = _credit_status(tmp_path, approved=["head"], changes=["ala"], review=GREEN)
    assert blocked["state"] == "pending"


def test_podrobiony_status_jest_nadpisywany_i_nie_przenoszony(tmp_path):
    new = json.dumps({"state": "success", "description": "kredyt: 3/15 w kolejce"})
    ours = json.dumps([{"context": "Recenzja albo kredyt", "state": "success",
                        "description": "kredyt: 3/15 w kolejce", "creator": "github-actions[bot]"}])
    forged = ours.replace("github-actions[bot]", "ktos")
    script = 'put_status abc "Recenzja albo kredyt" "$NEW" "$ST"'
    assert _run(tmp_path, f"NEW='{new}' ST='{ours}'; {script}").stdout == ""
    assert "DRY_RUN: status na abc" in _run(tmp_path, f"NEW='{new}' ST='{forged}'; {script}").stdout

    # Lista statusów idzie od najnowszego: liczy się pierwszy z każdej nazwy.
    listed = [{"context": "Ocena Claude'a", "state": "success", "description": "Ocena gotowa: ✅",
               "target_url": "u", "creator": {"login": "ktos"}},
              {"context": "Ocena Claude'a", "state": "pending", "description": "Claude ocenia",
               "target_url": "u", "creator": {"login": "github-actions[bot]"}}]
    res = _run(tmp_path, "carry_review_status 9 old new",
               responses={"repos/o/r/commits/old/statuses?per_page=100": listed})
    assert "nie przenoszę" in res.stdout + res.stderr
    assert "DRY_RUN" not in res.stdout


def _carry(tmp_path, desc, before="old1234567"):
    """Przenosi zielony status oceny z `before` na `nowy` (samo dociągnięcie
    bazy) i zwraca status, który by poszedł na nowy commit."""
    listed = [{"context": "Ocena Claude'a", "state": "success", "description": desc,
               "target_url": "u", "creator": {"login": "github-actions[bot]"}}]
    out = _run(tmp_path, f"carry_review_status 9 {before} nowy",
               responses={f"repos/o/r/commits/{before}/statuses?per_page=100": listed}).stdout
    body = json.loads(out.split("DRY_RUN: status na nowy: ", 1)[1].splitlines()[0])
    return {"state": body["state"], "description": body["description"], "creator": "github-actions[bot]"}


def test_przeniesienie_po_dociagnieciu_bazy_zachowuje_kredyt(tmp_path):
    fresh = _run(tmp_path, 'review_done_desc "🟢 Bez zastrzeżeń"').stdout
    assert _credit_status(tmp_path, review={**GREEN, "description": fresh})["state"] == "success"
    carried = _carry(tmp_path, fresh)
    assert "old1234" in carried["description"]
    assert _credit_status(tmp_path, review=carried) == {"state": "success", "description": "kredyt: 3/15 w kolejce"}
    # Drugie „Update branch” zastępuje dopisek, a nie dokleja następny.
    again = _carry(tmp_path, carried["description"], before="nowy777777")
    assert again["description"].count("przeniesiona") == 1 and "nowy777" in again["description"]
    assert _credit_status(tmp_path, review=again)["state"] == "success"
    # Przeniesiony werdykt inny niż zielony dalej nie daje kredytu, a podrobiony
    # status nie daje go bez względu na opis.
    assert _credit_status(tmp_path, review=_carry(tmp_path, YELLOW["description"]))["state"] == "pending"
    assert _credit_status(tmp_path, review={**carried, "creator": "ktos"})["state"] == "pending"


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
