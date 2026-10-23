# Stan PR-a liczony od zera z API, za każdym razem w całości: wymagany
# status „Recenzenci konieczni” (#352) i etykieta mówiąca, na czyj ruch PR
# czeka (#353). Wołający robi `source` (razem ze statuses.sh i approvals.sh)
# i woła `pr_state`; resztę bierze z env:
#
#   GH_TOKEN  token ze `statuses: write`, zapisem etykiet i odczytem PR-ów
#   REPO      `właściciel/repozytorium`
#   STARTING  numer PR-a, którego ocena Claude'a właśnie rusza (otwarcie,
#             ponowne otwarcie, „Ready for review”); puste, gdy żaden
#   DRY_RUN   `1` — tylko wypisz, co by się zmieniło; nic nie zapisuj.
#             Do sprawdzenia na prawdziwych PR-ach tokenem tylko do odczytu
#
# Wołający pobiera ten plik z `main`, tak jak skrypt Discorda: od statusu,
# który tu powstaje, zależy, czy PR da się zmergować.
#
# Nic tu nie pamiętamy między biegami i nie liczymy różnic: każdy bieg bierze
# z API recenzje, prośby o recenzję, statusy i listę recenzentów koniecznych
# i ustawia wszystko od nowa. Dzięki temu nie ma znaczenia, które zdarzenie
# bieg wywołało ani czy jakieś przepadło — następne i tak policzy dobrze.
# Zapisujemy tylko to, co się zmieniło: GitHub trzyma najwyżej tysiąc
# statusów jednej nazwy na commicie, a przeliczeń jest dużo.

REVIEWERS_DOC="https://github.com/${REPO:-Metal-Pipe-Org/Metal-Planner}/blob/main/CONTRIBUTING.md#review-i-mergowanie"

# Etykiety stanu PR-a (#353): dokładnie jedna na otwartym PR-ze, żadna na
# zamkniętym. Nazwy po angielsku, jak reszta etykiet w repo. Etykiety
# zgłoszeń (`PR in progress`, `merged - is it ok?`) to osobny zestaw
# z issue-labels.yml — tych tu nie ruszamy.
LABEL_AUTHOR="waiting for author"
LABEL_REVIEW="waiting for review"
LABEL_OTHER="waiting for other PR"
LABEL_COLOR_AUTHOR="d93f0b"
LABEL_COLOR_REVIEW="fbca04"
LABEL_COLOR_OTHER="c5def5"

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

# review_summary — filtr: z tablicy recenzji wypisuje `{approvals,
# changes_requested}`: ile zatwierdzeń ludzi stoi i kto ma czekające „Request
# changes” — według tej samej reguły co wyżej i w approvals.sh.
review_summary() {
  jq -c "$(_review_states_jq)"' as $last
    | {approvals: ($last | map(select(.state == "APPROVED")) | length),
       changes_requested: ($last | map(select(.state == "CHANGES_REQUESTED") | .user.login))}'
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

# pr_facts PR REQUIRED — dla jednego otwartego PR-a (obiekt z listy PR-ów
# z API) stawia status „Recenzenci konieczni” i wypisuje jedną linijkę JSON
# ze wszystkim, czego potrzebuje `labels_plan`. Dwa wywołania API na PR.
pr_facts() {
  local pr=$1 req=$2 n sha author reviews statuses reviewers

  n=$(jq -r .number <<<"$pr")
  sha=$(jq -r .head.sha <<<"$pr")
  author=$(jq -r .user.login <<<"$pr")
  reviews=$(pr_reviews "$n")
  statuses=$(commit_statuses "$sha")

  reviewers=$(reviewers_status "$req" "$author" <<<"$reviews")
  echo "#$n (${sha:0:7}): $REVIEWERS_CONTEXT → $(jq -r '"\(.state): \(.description)"' <<<"$reviewers")" >&2
  put_status "$sha" "$REVIEWERS_CONTEXT" "$reviewers" "$statuses" >&2

  jq -nc --argjson pr "$pr" --argjson st "$statuses" --argjson rv "$reviewers" \
    --argjson sum "$(review_summary <<<"$reviews")" --arg ctx "$REVIEW_CONTEXT" --arg repo "$REPO" '
    $pr | {number, draft, base: .base.ref, head: .head.ref,
           same_repo: ((.head.repo.full_name // "") == $repo),
           labels: [.labels[]?.name],
           requested: ((.requested_reviewers | length) + (.requested_teams | length)),
           review: ([$st[] | select(.context == $ctx)] | first // null),
           reviewers_ok: ($rv.state == "success")} + $sum'
}

# STACK_JQ — wspólne definicje jq dla stosów. Węzeł to `{number, base, head,
# same_repo}` (fakty z `pr_facts` albo `node` z obiektu PR-a z API). Dwa PR-y
# są w jednym stosie, gdy baza jednego to gałąź drugiego — tylko gałąź z tego
# repo, bo baza zawsze jest stąd. Gałęzie `main` i `stable` nie łączą:
# inaczej każdy PR do `main` byłby w jednym stosie z PR-em wydania
# `main` -> `stable`. `stack` rozrasta zbiór numerów, aż przestanie rosnąć —
# w obie strony i przez dowolnie wiele pięter.
STACK_JQ='
  def node($repo):
    {number, base: .base.ref, head: .head.ref,
     same_repo: ((.head.repo.full_name // "") == $repo)};
  def adj($a; $b):
    ($a.number != $b.number)
    and (($b.same_repo and $a.base == $b.head and ($b.head | IN("main", "stable") | not))
         or ($a.same_repo and $b.base == $a.head and ($a.head | IN("main", "stable") | not)));
  def stack($nodes; $start):
    def grow: . as $s
      | ($s + [ $nodes[] | . as $q
                | select(any($nodes[]; . as $p | any($s[]; . == $p.number) and adj($p; $q)))
                | .number ])
      | unique;
    $start | unique | until(. == grow; grow);
'

# labels_plan STARTING — filtr: z tablicy faktów (`pr_facts`) liczy, jaką
# etykietę ma dostać każdy PR. STARTING to tablica numerów PR-ów, których
# ocena Claude'a właśnie rusza. Na wyjściu tablica `{number, want, why,
# labels}`, gdzie `want` to `author` / `review` / `other`. Reguły (#353) —
# „autor” to wszystko, co musi zrobić autor, „recenzja” — wszystko, co musi
# zrobić ktoś inny (Claude też):
#
#   1. wersja robocza → autor (poprawia i oznacza jako gotowy),
#   2. ocena Claude'a (tylko PR-y z tego repo do `main` i `stable` — innych
#      Claude nie ocenia): właśnie rusza albo status czeka → recenzja; status
#      z „@claude” w opisie (porażka, PR zmienia workflow oceny), inna
#      porażka albo brak statusu, gdy ocena nie rusza → autor (`@claude`),
#   3. czekające „Request changes” → autor,
#   4. brak pełnego zatwierdzenia (co najmniej jedno stojące zatwierdzenie
#      człowieka i zielony „Recenzenci konieczni”): czeka prośba o recenzję
#      (osoba albo zespół) → recenzja, nie czeka żadna → autor (prosi),
#   5. wszystko gotowe → autor (merguje), chyba że PR jest w stosie, a inny
#      PR tego stosu nie jest gotowy → inny PR. Stos scalamy raz, od
#      najwyższego (#400), więc gotowy kawałek stosu czeka na resztę — także
#      na PR-y pod nim, nie tylko nad nim.
#
# Przy „ocena rusza” patrzymy na zdarzenie, nie na status: bieg oceny stawia
# `pending` chwilę później, a commit może jeszcze mieć zielony status
# z poprzedniej oceny (wyjście z wersji roboczej bez pushu).
labels_plan() {
  jq -c --argjson starting "$1" "$STACK_JQ"'
    def own:
      ((.base | IN("main", "stable")) and .same_repo) as $claude
      | if .draft then {s: "author", why: "wersja robocza"}
        elif $claude and (.number as $n | any($starting[]; . == $n)) then {s: "review", why: "ocena Claude'\''a rusza"}
        elif $claude and .review == null then {s: "author", why: "brak statusu oceny Claude'\''a — trzeba napisać @claude"}
        elif $claude and ((.review.description // "") | test("@claude")) then {s: "author", why: "ocena Claude'\''a czeka na @claude"}
        elif $claude and .review.state == "pending" then {s: "review", why: "Claude ocenia"}
        elif $claude and .review.state != "success" then {s: "author", why: "ocena Claude'\''a się nie udała"}
        elif (.changes_requested | length) > 0 then {s: "author", why: ("prośba o zmiany od " + (.changes_requested | join(", ")))}
        elif .approvals == 0 or (.reviewers_ok | not) then
          (if .requested > 0 then {s: "review", why: "czeka na recenzję"}
           else {s: "author", why: "brak pełnego zatwierdzenia i żadnej prośby o recenzję"} end)
        else {s: "merge", why: "gotowy do merge'\''a"}
        end;
    [ .[] | . + {own: own} ] as $prs
    | [ $prs[] | . as $p
        | stack($prs; [$p.number]) as $stack
        | [ $prs[] | select(.number as $n | $n != $p.number and any($stack[]; . == $n))
            | select(.own.s != "merge") | .number ] as $blocking
        | {number, labels,
           want: (if $p.own.s == "review" then "review"
                  elif $p.own.s == "author" then "author"
                  elif ($blocking | length) > 0 then "other"
                  else "author" end),
           why: (if $p.own.s == "merge" and ($blocking | length) > 0
                   then "gotowy, ale w stosie czeka " + ($blocking | map("#\(.)") | join(", "))
                 else $p.own.why end)} ]'
}

# ensure_label NAZWA KOLOR OPIS — zakłada etykietę w repo, gdy jej nie ma.
# Na żądanie, a nie raz ręcznie: etykieta skasowana przez kogoś wraca przy
# następnym przeliczeniu, zamiast wywracać bieg. Raz sprawdzoną zapamiętujemy
# w ENSURED, żeby nie pytać API przy każdym PR-ze.
ENSURED=""
ensure_label() {
  local name=$1 color=$2 desc=$3 err
  case "|$ENSURED|" in *"|$name|"*) return 0 ;; esac
  if gh api "repos/$REPO/labels/$(jq -rn --arg s "$name" '$s | @uri')" > /dev/null 2>&1; then
    ENSURED="$ENSURED|$name"
    return 0
  fi
  if [ "${DRY_RUN:-}" = 1 ]; then
    echo "DRY_RUN: założyłbym etykietę „$name”"
  elif ! err=$(jq -n --arg n "$name" --arg c "$color" --arg d "$desc" '{name: $n, color: $c, description: $d}' \
      | gh api -X POST "repos/$REPO/labels" --input - 2>&1 > /dev/null); then
    # Równoległy bieg mógł ją założyć przed chwilą — wtedy 422 i tyle.
    if ! grep -q 'already_exists\|HTTP 422' <<<"$err"; then
      echo "$err" >&2
      return 1
    fi
  fi
  ENSURED="$ENSURED|$name"
}

# label_name KLUCZ — nazwa etykiety dla `author` / `review` / `other`.
label_name() {
  case "$1" in
    author) echo "$LABEL_AUTHOR" ;;
    review) echo "$LABEL_REVIEW" ;;
    other) echo "$LABEL_OTHER" ;;
  esac
}

# set_pr_label N CHCIANA OBECNE — zostawia na PR-ze dokładnie etykietę
# CHCIANA spośród naszych trzech (pusta — żadnej). OBECNE to tablica JSON
# etykiet PR-a. Cudzych etykiet nie ruszamy. Etykiety PR-a idą przez API
# zgłoszeń — dla GitHuba PR to też zgłoszenie.
set_pr_label() {
  local n=$1 want=$2 have=$3 name
  for name in "$LABEL_AUTHOR" "$LABEL_REVIEW" "$LABEL_OTHER"; do
    [ "$name" != "$want" ] || continue
    jq -e --arg l "$name" 'index($l) != null' <<<"$have" > /dev/null || continue
    if [ "${DRY_RUN:-}" = 1 ]; then
      echo "DRY_RUN: #$n: zdjąłbym „$name”"
    else
      gh api -X DELETE "repos/$REPO/issues/$n/labels/$(jq -rn --arg s "$name" '$s | @uri')" > /dev/null
    fi
  done
  [ -n "$want" ] || return 0
  ! jq -e --arg l "$want" 'index($l) != null' <<<"$have" > /dev/null || return 0
  case "$want" in
    "$LABEL_AUTHOR") ensure_label "$want" "$LABEL_COLOR_AUTHOR" "PR: ruch autora — poprawki, @claude, prośba o recenzję albo merge" ;;
    "$LABEL_REVIEW") ensure_label "$want" "$LABEL_COLOR_REVIEW" "PR: ruch kogoś innego niż autor — recenzja człowieka albo ocena Claude'a" ;;
    "$LABEL_OTHER") ensure_label "$want" "$LABEL_COLOR_OTHER" "PR: gotowy, ale stos scalamy raz — czeka na inny PR ze stosu" ;;
  esac
  if [ "${DRY_RUN:-}" = 1 ]; then
    echo "DRY_RUN: #$n: dałbym „$want”"
  else
    jq -n --arg l "$want" '{labels: [$l]}' | gh api -X POST "repos/$REPO/issues/$n/labels" --input - > /dev/null
  fi
}

# open_prs — wszystkie otwarte PR-y jako jedna tablica JSON. Bez filtra bazy:
# PR-y w stosie celują w gałąź PR-a pod nimi, a też mają mieć stan.
open_prs() {
  gh api --paginate --slurp "repos/$REPO/pulls?state=open&per_page=100" | jq -c 'add // []'
}

# stack_seeds PR OTWARTE — numery otwartych PR-ów, z którymi PR (już
# zamknięty) dzielił gałąź: po merge'u albo zamknięciu kawałka stosu reszta
# stosu ma się przeliczyć od razu, a nie przy swoim następnym zdarzeniu.
stack_seeds() {
  jq -r --argjson p "$1" --arg repo "$REPO" "$STACK_JQ"'
    ($p | node($repo)) as $c | .[] | node($repo) | select(adj(.; $c)) | .number' <<<"$2"
}

# pr_state CEL… — przelicza wskazane PR-y razem z ich stosami; `all` (albo
# brak celu) to wszystkie otwarte. Zamknięty PR traci nasze etykiety, a jego
# dawny stos przelicza się od nowa; statusów już nie dostaje — wymagane
# statusy blokują tylko merge, a tego już nie będzie.
pr_state() {
  local req prs targets seeds="" n pr facts plan line want

  req=$(required_reviewers)
  echo "Recenzenci konieczni (CODEOWNERS na main): $(jq -r 'if length == 0 then "nikt" else join(", ") end' <<<"$req")"
  prs=$(open_prs)

  if [ $# -eq 0 ] || [ "$1" = all ]; then
    targets=$(jq -r '.[].number' <<<"$prs")
  else
    targets="$*"
  fi

  for n in $targets; do
    if jq -e --argjson n "$n" 'any(.[]; .number == $n)' <<<"$prs" > /dev/null; then
      seeds="$seeds $n"
      continue
    fi
    pr=$(gh api "repos/$REPO/pulls/$n")
    if [ "$(jq -r .state <<<"$pr")" = open ]; then
      # Otwarty, a nie było go na liście — lista sprzed chwili. Dokładamy.
      prs=$(jq -c --argjson p "$pr" '. + [$p]' <<<"$prs")
      seeds="$seeds $n"
      continue
    fi
    echo "#$n: zamknięty — zdejmuję etykiety stanu."
    set_pr_label "$n" "" "$(jq -c '[.labels[]?.name]' <<<"$pr")"
    seeds="$seeds $(stack_seeds "$pr" "$prs" | tr '\n' ' ')"
  done

  # Wskazane PR-y razem z ich stosami: gotowość jednego PR-a zmienia etykietę
  # reszty stosu. Fakty to dwa wywołania API na PR, więc liczymy je tylko dla
  # tej grupy, nie dla wszystkich otwartych.
  seeds=$(printf '%s\n' $seeds | jq -R 'select(. != "") | tonumber' | jq -sc .)
  seeds=$(jq -c --argjson start "$seeds" --arg repo "$REPO" "$STACK_JQ"'
    [.[] | node($repo)] as $nodes | stack($nodes; $start)' <<<"$prs")
  [ "$seeds" != "[]" ] || return 0

  facts=$(for n in $(jq -r '.[]' <<<"$seeds"); do
    pr_facts "$(jq -c --argjson n "$n" '.[] | select(.number == $n)' <<<"$prs")" "$req"
  done | jq -sc .)

  plan=$(labels_plan "$(jq -nc --arg s "${STARTING:-}" '[$s | select(. != "") | tonumber]')" <<<"$facts")
  while IFS= read -r line; do
    n=$(jq -r .number <<<"$line")
    want=$(label_name "$(jq -r .want <<<"$line")")
    echo "#$n: „$want” — $(jq -r .why <<<"$line")" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
    set_pr_label "$n" "$want" "$(jq -c .labels <<<"$line")"
  done < <(jq -c '.[]' <<<"$plan")
}
