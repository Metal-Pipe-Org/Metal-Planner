# Cotygodniowe sprzątanie jednego obszaru (#531): wybór obszaru i opiekuna,
# kolizje z otwartymi PR-ami, bramka po pracy Claude'a i porównanie testów.
# Wołający (`sprzatanie.yml`) robi `source`; funkcje liczą na lokalnym gicie
# i na tym, co dostaną, a po API sięga sam workflow — dzięki temu całość da
# się sprawdzić lokalnie (tests/test_konfiguracja_automatow.py).
#
# Bramka to cała gwarancja, że sprzątanie samo nie zrobi bałaganu: Claude może
# się pomylić, a gałąź wychodzi do ludzi dopiero, gdy wszystkie sprawdzenia
# przeszły. Progi stoją w config.sh.

source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

# sprzatanie_wybor OBSZARY_JSON TYDZIEN ZAJETE_JSON [WYMUSZONY] — obszar
# (obiekt JSON) na ten tydzień albo nic, gdy wszystkie są wstrzymane lub
# zajęte. Kolejka po numerze tygodnia: pozycja TYDZIEN mod liczba obszarów,
# a gdy ta jest zablokowana, kolejne po niej. Bez zapisanego stanu: tydzień,
# w którym nic nie wyszło, po prostu mija. ZAJETE_JSON to wynik
# `sprzatanie_zajete`. WYMUSZONY (id obszaru z ręcznego biegu) pomija
# kolejkę, ale nie blokady — sprzątanie pliku, który właśnie przepisuje inny
# PR, i tak nie przeszłoby bramki.
sprzatanie_wybor() {
  jq -c --argjson week "$2" --argjson busy "$3" --arg forced "${4:-}" '
    length as $n
    | [range(0; $n) as $i | .[($week + $i) % $n]]
    | map(select($forced == "" or .id == $forced))
    | map(select((.wstrzymany // "") == "" and all(.kod[]; $busy[.] == null)))
    | first // empty' "$1"
}

# sprzatanie_blokady OBSZARY_JSON ZAJETE_JSON — dla każdego zablokowanego
# obszaru linia „nazwa: powód” do podsumowania biegu.
sprzatanie_blokady() {
  jq -r --argjson busy "$2" '.[]
    | if (.wstrzymany // "") != "" then "\(.nazwa): wstrzymany — \(.wstrzymany)"
      else ([.kod[] | select($busy[.] != null) as $f | "\($f) (PR \($busy[$f] | map("#\(.)") | join(", ")))"]) as $z
        | if ($z | length) > 0 then "\(.nazwa): zajęte \($z | join(", "))" else empty end
      end' "$1"
}

# sprzatanie_zajete — plik → numery PR-ów, z którymi jego sprzątanie
# oczywiście by się gryzło: usuwają go, przenoszą albo zmieniają co najmniej
# SPRZATANIE_ZAJETY_PROCENT jego linii. Wejście (stdin): lista
# [{number, files: [{filename, previous_filename?, status, additions,
# deletions}]}] z API; długości plików z bieżącego drzewa (czysty `main`).
# Drobne zmiany w tym samym pliku obszaru nie blokują: realną kolizję łapie
# dopiero `sprzatanie_kolizja` na gotowej zmianie.
sprzatanie_zajete() {
  local prs files lens=""
  prs=$(cat)
  files=$(jq -r '[.[].files[] | .previous_filename // .filename] | unique[]' <<<"$prs")
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ -f "$f" ]; then
      lens="$lens$(jq -n --arg f "$f" --argjson n "$(wc -l <"$f" | tr -d ' ')" '{($f): $n}')"
    fi
  done <<<"$files"
  jq -c --argjson lens "$(printf '%s' "$lens" | jq -s 'add // {}')" \
      --argjson pct "$SPRZATANIE_ZAJETY_PROCENT" '
    [.[] | .number as $n | .files[]
     | (.previous_filename // .filename) as $f
     | select(.status == "removed" or .status == "renamed"
              or (($lens[$f] // 0) > 0 and (.additions + .deletions) * 100 >= $pct * $lens[$f]))
     | {f: $f, n: $n}]
    | group_by(.f) | map({(.[0].f): (map(.n) | unique)}) | add // {}' <<<"$prs"
}

# sprzatanie_opiekun TYGODNIE PRZYPISANI... — opiekun tego tygodnia: osoby
# przypisane do zgłoszenia sprzątania po kolei, co tydzień następna, od
# SPRZATANIE_PIERWSZY_OPIEKUN. TYGODNIE to pełne tygodnie od
# SPRZATANIE_START (przed nim 0). Opiekun musi być przypisany do zgłoszenia,
# bo PR wskazuje je przez `Refs` (pr-syntax.yml), stąd lista z przypisanych.
sprzatanie_opiekun() {
  local weeks=$1
  shift
  if [ $# -eq 0 ]; then
    echo "$SPRZATANIE_PIERWSZY_OPIEKUN"
    return 0
  fi
  printf '%s\n' "$@" | jq -Rrs --argjson w "$weeks" --arg first "$SPRZATANIE_PIERWSZY_OPIEKUN" '
    split("\n") | map(select(. != "")) | sort_by(ascii_downcase) as $l
    | ([$l | to_entries[] | select(.value | ascii_downcase == ($first | ascii_downcase)) | .key] | first // 0) as $s
    | $l[($s + ([$w, 0] | max)) % ($l | length)]'
}

# sprzatanie_bramka BAZA CZUBEK OBSZAR_JSON — zakres i wielkość zmiany.
# Wypisuje każde naruszenie w osobnej linii i kończy się kodem 1, gdy jest
# choć jedno. Wolno zmieniać tylko pliki obszaru i jego testy, nigdy ścieżek
# z SPRZATANIE_ZAKAZANE; razem najwyżej SPRZATANIE_MAX_LINII zmienionych linii,
# a dodanych najwyżej 1/SPRZATANIE_USUNIETE_NA_DODANE usuniętych — to
# mechanicznie wymusza usuwanie i scalanie zamiast przepisywania.
sprzatanie_bramka() {
  local base=$1 head=$2 area=$3 bad="" add=0 del=0 a d f p
  while IFS=$'\t' read -r a d f; do
    [ -n "$f" ] || continue
    for p in $SPRZATANIE_ZAKAZANE; do
      case "$f" in "$p"*) bad="$bad"$'\n'"$f: tej ścieżki sprzątanie nie rusza" ;; esac
    done
    if ! jq -e --arg f "$f" '(.kod + .testy) | index($f)' <<<"$area" >/dev/null; then
      bad="$bad"$'\n'"$f: poza obszarem"
    fi
    if [ "$a" = - ]; then
      bad="$bad"$'\n'"$f: plik binarny"
      continue
    fi
    add=$((add + a))
    del=$((del + d))
  done < <(git diff --numstat --no-renames "$base" "$head")
  if [ $((add + del)) -gt "$SPRZATANIE_MAX_LINII" ]; then
    bad="$bad"$'\n'"zmienionych linii $((add + del)), a wolno najwyżej $SPRZATANIE_MAX_LINII"
  fi
  if [ $((add * SPRZATANIE_USUNIETE_NA_DODANE)) -gt "$del" ] || [ "$del" -eq 0 ]; then
    bad="$bad"$'\n'"dodanych $add, usuniętych $del — sprzątanie ma usuwać (dodanych najwyżej 1/$SPRZATANIE_USUNIETE_NA_DODANE usuniętych)"
  fi
  if [ -n "$bad" ]; then
    printf '%s\n' "${bad#$'\n'}"
    return 1
  fi
}

# sprzatanie_wyniki JUNIT_XML — JSON {test: "zielony"|"czerwony"|"pominiety"}
# z raportu pytest. Klucz to plik testu i nazwa, np.
# "tests/test_x.py::test_y[a]"; czerwony to też błąd ustawienia.
sprzatanie_wyniki() {
  python3 -I - "$1" <<'PY'
import json, sys, xml.etree.ElementTree as ET
wynik = {}
for t in ET.parse(sys.argv[1]).getroot().iter("testcase"):
    czesci = (t.get("classname") or "").split(".")
    plik = "/".join(czesci[:2]) + ".py"
    nazwa = "::".join([plik, *czesci[2:], t.get("name") or ""])
    if t.find("failure") is not None or t.find("error") is not None:
        wynik[nazwa] = "czerwony"
    elif t.find("skipped") is not None:
        wynik[nazwa] = "pominiety"
    else:
        wynik[nazwa] = "zielony"
print(json.dumps(wynik, ensure_ascii=False))
PY
}

# sprzatanie_testy PRZED_JSON PO_JSON ZMIENIONE_PLIKI — porównanie wyników
# (`sprzatanie_wyniki`) na czystym `main` i po sprzątaniu:
# {zielone, zepsute, usuniete, usuniete_czerwone}. Każdy test zielony przed
# ma być zielony po. Wyjątek: test, którego już nie ma, w pliku testów
# zmienionym przez sprzątanie — to usunięty test i idzie z nazwy do opisu
# PR-a. Czerwonych sprzątanie nie usuwa (to robi #533). ZMIENIONE_PLIKI to
# lista ścieżek, po jednej w linii.
sprzatanie_testy() {
  jq -n --argjson przed "$1" --argjson po "$2" --arg pliki "$3" '
    ($pliki | split("\n") | map(select(. != ""))) as $zm
    | [$przed | keys[] | select($po[.] == null and (split("::")[0] as $f | $zm | index($f)))] as $usuniete
    | {zielone: [$przed | to_entries[] | select(.value == "zielony")] | length,
       zepsute: [$przed | to_entries[] | select(.value == "zielony" and $po[.key] != "zielony")
                 | .key | . as $k | select($usuniete | index($k) | not)],
       usuniete: [$usuniete[] | select($przed[.] != "czerwony")],
       usuniete_czerwone: [$usuniete[] | select($przed[.] == "czerwony")]}'
}

# sprzatanie_zniknione BAZA CZUBEK — nazwy (identyfikatory, klasy CSS, id)
# z usuniętych linii, których po sprzątaniu nie ma już nigdzie w repo, po
# jednej w linii.
sprzatanie_zniknione() {
  local tokens kept
  tokens=$(git diff --no-renames -U0 "$1" "$2" | grep '^-' | grep -v '^---' \
    | grep -oE '[A-Za-z_$][A-Za-z0-9_$-]{3,}' | LC_ALL=C sort -u || true)
  [ -n "$tokens" ] || return 0
  kept=$(git grep -h -o -w -F -f <(printf '%s\n' "$tokens") "$2" -- . 2>/dev/null | LC_ALL=C sort -u || true)
  LC_ALL=C comm -23 <(printf '%s\n' "$tokens") <(printf '%s\n' "$kept" | sed '/^$/d')
}

# sprzatanie_kolizja BAZA CZUBEK PR_CZUBEK ZNIKNIONE — czy sprzątanie
# (CZUBEK) gryzie się z otwartym PR-em: (a) `merge-tree` daje konflikt,
# którego między BAZĄ a tym PR-em nie było; (b) PR w dodanych liniach używa
# nazwy, którą sprzątanie usunęło z repo — tekstowo zmerguje się czysto,
# a potem PR wywoła coś, czego już nie ma. Wypisuje powód, kod 1 przy
# kolizji. ZNIKNIONE z `sprzatanie_zniknione`.
sprzatanie_kolizja() {
  local base=$1 head=$2 pr=$3 used
  if ! git merge-tree --write-tree "$head" "$pr" >/dev/null 2>&1 \
     && git merge-tree --write-tree "$base" "$pr" >/dev/null 2>&1; then
    echo "konflikt w plikach: $(git merge-tree --write-tree --name-only "$head" "$pr" 2>/dev/null \
      | sed -n '2,/^$/p' | sed '/^$/d' | tr '\n' ' ')"
    return 1
  fi
  [ -n "$4" ] || return 0
  used=$(git diff --no-renames -U0 "$(git merge-base "$base" "$pr")" "$pr" | grep '^+' | grep -v '^+++' \
    | grep -oE '[A-Za-z_$][A-Za-z0-9_$-]{3,}' | LC_ALL=C sort -u \
    | LC_ALL=C comm -12 - <(printf '%s\n' "$4" | LC_ALL=C sort -u) | tr '\n' ' ')
  if [ -n "$used" ]; then
    echo "używa usuniętych nazw: $used"
    return 1
  fi
}

# sprzatanie_link REPO GAŁĄŹ TYTUŁ OPIS — link, który otwiera gotowy PR do
# `main` z tytułem i opisem. PR zakłada człowiek (opiekun), nie bot: PR
# założony tokenem workflow nie budzi oceny Claude'a, stanu ani Discorda
# (docs/WORKFLOWS.md, zasada 2), a „PR merguje autor” nie miałoby kogo
# wskazać.
sprzatanie_link() {
  jq -rn --arg repo "$1" --arg b "$2" --arg t "$3" --arg body "$4" \
    '"https://github.com/\($repo)/compare/main...\($b)?quick_pull=1&title=\($t | @uri)&body=\($body | @uri)"'
}
