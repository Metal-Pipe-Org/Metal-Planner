"""Wskaźniki bałaganu (#532): liczby do zapisywania co tydzień, żeby widzieć
trend. Wypisuje czytelne podsumowanie i na końcu jedną linię JSON do dopisania
do historii.

    python wskazniki_balaganu.py              # pełny zestaw testów + reszta
    python wskazniki_balaganu.py --bez-testow # tylko pliki i martwy kod

Testy puszczamy tak jak CI (świeży klon, bez data/gtfs.sqlite), więc liczba
czerwonych to ta, którą widziałby automat.
"""
import ast
import json
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOP_PLIKOW = 10
POMIJANE_KATALOGI = {".git", ".claude", ".tmp-agent", ".prywatne", "node_modules", "venv", ".venv", "__pycache__", "data", "docs"}


def pliki_zrodlowe():
    wynik = []
    for sciezka in ROOT.rglob("*"):
        if not sciezka.is_file() or POMIJANE_KATALOGI & set(sciezka.relative_to(ROOT).parts):
            continue
        # zminifikowane i dołączone biblioteki to nie nasz kod
        if sciezka.suffix in {".py", ".js", ".css", ".html"} and ".min." not in sciezka.name and "vendor" not in sciezka.parts:
            wynik.append(sciezka)
    return wynik


def testy():
    start = time.monotonic()
    proces = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=ROOT, capture_output=True, text=True,
    )
    czas = time.monotonic() - start
    liczby = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for ile, nazwa in re.findall(r"(\d+) (passed|failed|errors?|skipped)", proces.stdout.splitlines()[-1]):
        liczby["error" if nazwa.startswith("error") else nazwa] = int(ile)
    return {"czas_s": round(czas, 1), "zaliczone": liczby["passed"],
            "czerwone": liczby["failed"] + liczby["error"], "pominiete": liczby["skipped"]}


def najwieksze_pliki():
    rozmiary = [(len(p.read_text(errors="ignore").splitlines()), str(p.relative_to(ROOT))) for p in pliki_zrodlowe()]
    return sorted(rozmiary, reverse=True)[:TOP_PLIKOW]


def martwy_kod():
    """HEURYSTYKA, nie dowód: funkcje i klasy najwyższego poziomu (Python,
    poza testami i dekorowane, np. trasy Flaska) oraz `function nazwa` w JS,
    których nazwa występuje w całym kodzie tylko raz, czyli w definicji.
    Pomija użycia po nazwie złożonej z tekstu (getattr, onclick w HTML łapie)."""
    pliki = pliki_zrodlowe()
    tekst = "\n".join(p.read_text(errors="ignore") for p in pliki)
    nazwy = []
    for p in pliki:
        zrodlo = p.read_text(errors="ignore")
        if p.suffix == ".py" and "tests" not in p.parts:
            for wezel in ast.parse(zrodlo).body:
                if isinstance(wezel, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not wezel.decorator_list and not wezel.name.startswith("__"):
                    nazwy.append(wezel.name)
        elif p.suffix == ".js" and "tests" not in p.parts:
            nazwy += re.findall(r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", zrodlo, re.M)
    martwe = sorted(n for n in set(nazwy) if len(re.findall(r"(?<![\w$])" + re.escape(n) + r"(?![\w$])", tekst)) == 1)
    return martwe


def main():
    wynik = {"data": date.today().isoformat()}
    if "--bez-testow" not in sys.argv:
        wynik["testy"] = testy()
    wynik["najwieksze_pliki"] = {sciezka: linie for linie, sciezka in najwieksze_pliki()}
    martwe = martwy_kod()
    wynik["martwy_kod_heurystyka"] = len(martwe)

    if "testy" in wynik:
        t = wynik["testy"]
        print(f"Testy: {t['czas_s']} s, zaliczone {t['zaliczone']}, czerwone {t['czerwone']}, pominięte {t['pominiete']}")
    print("Największe pliki (linie):")
    for sciezka, linie in wynik["najwieksze_pliki"].items():
        print(f"  {linie:6d}  {sciezka}")
    print(f"Martwy kod (HEURYSTYKA: nazwa występuje w kodzie tylko raz): {len(martwe)}")
    for nazwa in martwe[:15]:
        print(f"  {nazwa}")
    if len(martwe) > 15:
        print(f"  ... i {len(martwe) - 15} kolejnych")
    print(json.dumps(wynik, ensure_ascii=False))


if __name__ == "__main__":
    main()
