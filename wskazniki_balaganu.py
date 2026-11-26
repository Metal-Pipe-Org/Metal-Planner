"""Wskaźniki bałaganu (#532): liczby do zapisywania co tydzień, żeby widzieć
trend. Wypisuje czytelne podsumowanie i na końcu jedną linię JSON do dopisania
do historii.

    python wskazniki_balaganu.py              # pełny zestaw testów + reszta
    python wskazniki_balaganu.py --bez-testow # tylko pliki i martwy kod

Wynik testów zależy od środowiska, więc skrypt podaje, czy była baza rozkładu
(bez niej część testów pada, a przeglądarkowe są pomijane). Testy przeglądarkowe
(playwright) wymagają uruchomienia poza sandboxem Claude'a, a emulator frontu
wymaga macOS (osascript). Pełny zestaw z bazą trwa kilkanaście minut.
"""
import ast
import json
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
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
    with tempfile.TemporaryDirectory() as katalog:
        raport = Path(katalog) / "junit.xml"
        start = time.monotonic()
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests", f"--junitxml={raport}"],
            cwd=ROOT, capture_output=True, text=True,
        )
        czas = time.monotonic() - start
        przypadki = list(ET.parse(raport).getroot().iter("testcase"))
    wynik = {"czas_s": round(czas, 1), "zaliczone": 0, "czerwone": 0, "bledy_ustawienia": 0, "pominiete": 0}
    pominiecia = Counter()
    for przypadek in przypadki:
        blad, usterka, pominiety = przypadek.find("failure"), przypadek.find("error"), przypadek.find("skipped")
        if blad is not None:
            wynik["czerwone"] += 1
        elif usterka is not None:
            wynik["bledy_ustawienia"] += 1
        elif pominiety is not None:
            wynik["pominiete"] += 1
            pominiecia[(pominiety.get("message") or "")[:70]] += 1
        else:
            wynik["zaliczone"] += 1
    wynik["baza_rozkladu"] = (ROOT / "data" / "gtfs.sqlite").exists()
    wynik["pominiecia"] = dict(pominiecia.most_common(5))
    return wynik


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
        print(f"Testy: {t['czas_s']} s, zaliczone {t['zaliczone']}, czerwone {t['czerwone']}, błędy ustawienia {t['bledy_ustawienia']}, pominięte {t['pominiete']}")
        print(f"  baza rozkładu data/gtfs.sqlite: {'jest' if t['baza_rozkladu'] else 'BRAK (jak na świeżym klonie; część testów pada lub jest pominięta)'}")
        for powod, ile in t["pominiecia"].items():
            print(f"  pominięte {ile}: {powod}")
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
