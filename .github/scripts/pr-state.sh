# Stan PR-a liczony od zera z API, za każdym razem w całości (#352).
# Wołający robi `source` (razem ze statuses.sh i approvals.sh) i woła
# `pr_state`; resztę bierze z env:
#
#   GH_TOKEN  token ze `statuses: write` i odczytem PR-ów
#   REPO      `właściciel/repozytorium`
#   DRY_RUN   `1` — tylko wypisz, co by się zmieniło; nic nie zapisuj.
#             Do sprawdzenia na prawdziwych PR-ach tokenem tylko do odczytu
#
# Wołający pobiera ten plik z `main`, tak jak skrypt Discorda: od statusu,
# który tu powstaje, zależy, czy PR da się zmergować.
#
# Nic tu nie pamiętamy między biegami i nie liczymy różnic: każdy bieg bierze
# z API recenzje, prośby o recenzję i listę recenzentów koniecznych i stawia
# status od nowa. Dzięki temu nie ma znaczenia, które zdarzenie bieg
# wywołało ani czy jakieś przepadło — następne i tak policzy dobrze.
# Status zapisujemy tylko wtedy, gdy się zmienił: GitHub trzyma najwyżej
# tysiąc statusów jednej nazwy na commicie, a przeliczeń jest dużo.

REVIEWERS_DOC="https://github.com/${REPO:-Metal-Pipe-Org/Metal-Planner}/blob/main/CONTRIBUTING.md#review-i-mergowanie"

# codeowners_required — filtr: z treści CODEOWNERS wypisuje loginy
# recenzentów koniecznych, po jednym w linijce. Recenzent konieczny to login
# z linijki `*` (#352): każdy dopisuje się tam sam, PR-em, i tak samo się
# wypisuje. Liczy się ostatnia linijka `*`, jak w samym CODEOWNERS, gdzie
# późniejsza reguła wygrywa. Zespoły (`@org/zespół`) i adresy e-mail
# pomijamy z ostrzeżeniem: token workflow nie widzi składu zespołów, więc nie
# umiałby rozstrzygnąć, czy zespół już zatwierdził.
codeowners_required() {
  awk '
    { sub(/\r$/, ""); sub(/^#.*/, ""); sub(/[ \t]#.*/, "") }
    $1 == "*" { line = $0 }
    END {
      n = split(line, t, /[ \t]+/)
      for (i = 1; i <= n; i++) {
        if (t[i] == "" || t[i] == "*") continue
        if (t[i] ~ /^@[A-Za-z0-9][A-Za-z0-9-]*$/) print substr(t[i], 2)
        else print "::warning::CODEOWNERS: „" t[i] "” w linijce * to nie login — pomijam." > "/dev/stderr"
      }
    }'
}

# required_reviewers — recenzenci konieczni jako tablica JSON, z CODEOWNERS
# na `main`, nie z PR-a: PR nie może sam sobie zdjąć recenzenta, a deklaracja
# obowiązuje od merge'a PR-a, który ją wnosi. Brak pliku to pusta lista
# (nikt się nie zadeklarował). Każdy inny błąd wywraca bieg: pusta lista
# z błędu przepuściłaby PR bez recenzentów koniecznych.
required_reviewers() {
  local out err rc=0
  err=$(mktemp)
  out=$(gh api -H "Accept: application/vnd.github.raw+json" \
    "repos/$REPO/contents/.github/CODEOWNERS?ref=main" 2> "$err") || rc=$?
  if [ "$rc" -ne 0 ]; then
    if grep -q 'HTTP 404' "$err"; then
      rm -f "$err"
      echo '[]'
      return 0
    fi
    cat "$err" >&2
    rm -f "$err"
    return 1
  fi
  rm -f "$err"
  printf '%s\n' "$out" | codeowners_required | jq -R . | jq -sc 'unique'
}

# reviewers_status RECENZENCI AUTOR — filtr: z tablicy recenzji PR-a liczy
# status „Recenzenci konieczni” jako `{state, description}`. Zatwierdzenie
# stoi tak samo jak w approvals.sh: ostatnia recenzja zmieniająca stan danej
# osoby to zatwierdzenie (boty pomijamy). Autor nie zatwierdzi własnego PR-a
# (GitHub nie pozwala), więc jego samego nie wymagamy. Loginy porównujemy bez
# wielkości liter — w CODEOWNERS ktoś mógł je wpisać inaczej niż na koncie.
reviewers_status() {
  jq -c --argjson req "$1" --arg author "$2" "$(_review_states_jq)"' as $last
    | ($last | map(select(.state == "APPROVED") | .user.login | ascii_downcase)) as $ok
    | [ $req[]
        | select(ascii_downcase != ($author | ascii_downcase))
        | select(ascii_downcase as $r | any($ok[]; . == $r) | not) ] as $missing
    | if ($missing | length) > 0
        then {state: "pending", description: ("Czeka na: " + ($missing | join(", ")))}
      elif ($req | length) == 0
        then {state: "success", description: "Nikt nie jest zadeklarowany jako recenzent konieczny"}
      else {state: "success", description: "Zatwierdzili wszyscy recenzenci konieczni"}
      end'
}

# put_status SHA NAZWA NOWY STATUSY — stawia status NOWY (`{state,
# description}`) z linkiem do CONTRIBUTING.md, chyba że na commicie stoi już
# dokładnie taki (STATUSY z `commit_statuses`).
put_status() {
  local sha=$1 ctx=$2 new=$3 statuses=$4
  if jq -e --arg c "$ctx" --argjson n "$new" \
      'any(.[]; .context == $c and .state == $n.state and .description == $n.description)' \
      <<<"$statuses" > /dev/null; then
    return 0
  fi
  set_status "$sha" "$ctx" "$(jq -r .state <<<"$new")" "$(jq -r .description <<<"$new")" "$REVIEWERS_DOC"
}

# pr_state_one PR REQUIRED — przelicza jeden otwarty PR (PR to obiekt z listy
# PR-ów z API, REQUIRED — tablica recenzentów koniecznych).
pr_state_one() {
  local pr=$1 req=$2 n sha author reviews statuses new

  n=$(jq -r .number <<<"$pr")
  sha=$(jq -r .head.sha <<<"$pr")
  author=$(jq -r .user.login <<<"$pr")
  reviews=$(pr_reviews "$n")
  statuses=$(commit_statuses "$sha")

  new=$(reviewers_status "$req" "$author" <<<"$reviews")
  echo "#$n (${sha:0:7}): $REVIEWERS_CONTEXT → $(jq -r '"\(.state): \(.description)"' <<<"$new")"
  put_status "$sha" "$REVIEWERS_CONTEXT" "$new" "$statuses"
}

# open_prs — wszystkie otwarte PR-y jako jedna tablica JSON. Bez filtra bazy:
# PR-y w stosie celują w gałąź PR-a pod nimi, a też mają mieć stan.
open_prs() {
  gh api --paginate --slurp "repos/$REPO/pulls?state=open&per_page=100" | jq -c 'add // []'
}

# pr_state CEL… — przelicza wskazane PR-y; `all` (albo brak celu) to
# wszystkie otwarte. Zamknięty PR nie dostaje już nic: wymagane statusy
# blokują tylko merge, a tego nie ma.
pr_state() {
  local req prs targets n pr

  req=$(required_reviewers)
  echo "Recenzenci konieczni (CODEOWNERS na main): $(jq -r 'if length == 0 then "nikt" else join(", ") end' <<<"$req")"
  prs=$(open_prs)

  if [ $# -eq 0 ] || [ "$1" = all ]; then
    targets=$(jq -r '.[].number' <<<"$prs")
  else
    targets="$*"
  fi

  for n in $targets; do
    pr=$(jq -c --argjson n "$n" '.[] | select(.number == $n)' <<<"$prs")
    if [ -z "$pr" ]; then
      echo "#$n: nie jest otwarty — pomijam."
      continue
    fi
    pr_state_one "$pr" "$req"
  done
}
