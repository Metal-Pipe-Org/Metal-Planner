# Składnia PR-a: czy PR wskazuje zgłoszenie, nad którym pracuje jego autor
# (#356, #355). PR bez zgłoszenia ma być decyzją, a nie przeoczeniem (#342),
# więc „nic nie wskazuję" trzeba powiedzieć wprost etykietą `No Issue`.
# Wołający robi `source` (razem z refs.sh, drafts.sh, guard.sh i discord.sh)
# i woła `check_pr` albo `check_prs_for_issue`; resztę bierze z env:
#
#   GH_TOKEN  token z `pull-requests: write`, `issues: write`
#             i `contents: write` (to ostatnie dla `to_draft`, patrz
#             pr-syntax.yml)
#   REPO      `właściciel/repozytorium`
#   DRY_RUN   `1` = tylko wypisz, co by się stało (etykieta, komentarz,
#             wersja robocza, Discord); do sprawdzenia na prawdziwych PR-ach
#             tokenem tylko do odczytu
#   DISCORD_WEBHOOK, DISCORD_IDS — jak w discord.sh
#
# Wołający pobiera ten plik z `main`, tak jak skrypt Discorda.
#
# Poprawna składnia to (a) odwołanie do zgłoszenia w tytule, opisie albo
# nazwie gałęzi dokładnie w tej formie, którą rozpoznaje issue-labels.yml
# (wspólny refs.sh), oraz (b) każdy wskazany numer to istniejące
# ZGŁOSZENIE (nie PR), którego assignees obejmują autora PR-a — PR dotyczy
# tylko zgłoszeń swojego autora (#355, CONTRIBUTING „Issues”: assignee to
# ten, kto pracuje). Etykieta `No Issue` zwalnia z obu warunków.
#
# Stan trzymamy w samym PR-ze, nie w pamięci między biegami: etykieta
# `wrong syntax` plus ostatni niezwinięty komentarz bota ze znacznikiem
# `<!-- pr-syntax: KLUCZ -->`. KLUCZ to powód w postaci do porównania
# (`no-ref` albo `bad:12=unassigned,15=pr`). Nowy komentarz idzie tylko przy
# wejściu w błędną składnię, przy zmianie powodu albo gdy gotowy PR wraca do
# wersji roboczej — kolejne edycje opisu z tym samym błędem nie spamują.

LABEL_WRONG=${LABEL_WRONG:-wrong syntax}
LABEL_NO_ISSUE=${LABEL_NO_ISSUE:-No Issue}
SYNTAX_MARK='<!-- pr-syntax:'

# pr_issue_refs TYTUŁ OPIS GAŁĄŹ NUMER_PR — wszystkie numery, które PR
# wskazuje którąkolwiek ścieżką, po jednym w linii. Numer samego PR-a
# odejmujemy jak issue-labels.yml: to nie jest odwołanie do zgłoszenia.
pr_issue_refs() {
  { keyword_refs "$1" "$2" "$3"; mention_refs "$1" "$2"; } \
    | grep -vxF "$4" | sort -un || true
}

# syntax_key PR_JSON — wypisuje KLUCZ błędu albo nic, gdy składnia jest
# poprawna. Kod 2 przy błędzie API innym niż „nie ma takiego numeru": awaria
# GitHuba albo limit zapytań nie może wyglądać jak zły numer w opisie, bo
# autor dostałby fałszywy zarzut i PR wróciłby do wersji roboczej.
syntax_key() {
  local pr=$1 n author json err rc
  local -a probs=()

  if jq -e --arg l "$LABEL_NO_ISSUE" \
      'any(.labels[].name; ascii_downcase == ($l | ascii_downcase))' <<<"$pr" > /dev/null; then
    return 0
  fi

  local refs
  refs=$(pr_issue_refs "$(jq -r .title <<<"$pr")" "$(jq -r '.body // ""' <<<"$pr")" \
    "$(jq -r .head.ref <<<"$pr")" "$(jq -r .number <<<"$pr")")
  if [ -z "$refs" ]; then
    echo no-ref
    return 0
  fi

  author=$(jq -r '.user.login | ascii_downcase' <<<"$pr")
  err=$(mktemp)
  for n in $refs; do
    rc=0
    json=$(gh api "repos/$REPO/issues/$n" 2> "$err") || rc=$?
    if [ "$rc" -ne 0 ]; then
      # 404 — nie ma takiego numeru, 410 — zgłoszenie skasowane.
      if grep -qE 'HTTP (404|410)' "$err"; then
        probs+=("$n=missing")
        continue
      fi
      cat "$err" >&2
      rm -f "$err"
      echo "::error::Nie udało się sprawdzić #$n." >&2
      return 2
    fi
    if jq -e '.pull_request' <<<"$json" > /dev/null; then
      probs+=("$n=pr")
    elif ! jq -e --arg a "$author" 'any(.assignees[].login; ascii_downcase == $a)' \
        <<<"$json" > /dev/null; then
      probs+=("$n=unassigned")
    fi
  done
  rm -f "$err"

  if [ ${#probs[@]} -gt 0 ]; then
    (IFS=,; echo "bad:${probs[*]}")
  fi
}

# syntax_problem_lines KLUCZ AUTOR — linie „co się nie zgadza” do komentarza.
syntax_problem_lines() {
  local key=$1 author=$2 p
  if [ "$key" = no-ref ]; then
    echo "- W tytule, opisie ani nazwie gałęzi nie ma odwołania do zgłoszenia w formie, którą rozpoznaje automat: \`Closes #12\` (także \`Fixes\`, \`Resolves\`), \`Refs #12\` (także \`Ref\`, \`Related to\`, \`Relates to\`, \`Part of\`, \`Dotyczy\`) albo gałąź \`claude/issue-12-…\`. Gołe \`#12\` się nie liczy."
    return 0
  fi
  for p in $(tr , ' ' <<<"${key#bad:}"); do
    case ${p#*=} in
      missing) echo "- #${p%%=*} nie istnieje." ;;
      pr) echo "- #${p%%=*} to PR, a nie zgłoszenie." ;;
      unassigned) echo "- #${p%%=*} nie ma **$author** wśród assignees — PR może dotyczyć tylko zgłoszeń, nad którymi pracuje jego autor." ;;
    esac
  done
}

# syntax_short KLUCZ — jednolinijkowy powód na Discorda i do logu.
syntax_short() {
  local key=$1 p out=()
  if [ "$key" = no-ref ]; then
    echo "Brak \`Closes #…\` / \`Refs #…\` i etykiety \`$LABEL_NO_ISSUE\`"
    return 0
  fi
  for p in $(tr , ' ' <<<"${key#bad:}"); do
    case ${p#*=} in
      missing) out+=("#${p%%=*} nie istnieje") ;;
      pr) out+=("#${p%%=*} to PR, nie zgłoszenie") ;;
      unassigned) out+=("autor nie jest przypisany do #${p%%=*}") ;;
    esac
  done
  jq -rn '$ARGS.positional | join("; ")' --args "${out[@]}"
}

# syntax_comment_body KLUCZ AUTOR WRACA — treść komentarza. WRACA=1, gdy PR
# właśnie wraca do wersji roboczej. Komentarz mówi, która reguła nie
# przeszła i co autor może zrobić od razu, bez czytania całego CONTRIBUTING.
syntax_comment_body() {
  local key=$1 author=$2 drafted=$3 todo p
  if [ "$key" = no-ref ]; then
    todo="- PR załatwia zgłoszenie — dopisz do opisu \`Closes #<numer>\`; tylko go dotyczy — \`Refs #<numer>\`. Zgłoszenie musi mieć Cię wśród assignees."
  else
    todo=""
    for p in $(tr , ' ' <<<"${key#bad:}"); do
      case ${p#*=} in
        missing|pr) todo+="- Popraw w opisie numer #${p%%=*} na numer zgłoszenia, którego PR dotyczy."$'\n' ;;
        unassigned) todo+="- Pracujesz nad #${p%%=*}? Przypisz się do niego (pole „Assignees” w zgłoszeniu; bez prawa zapisu poproś o to kogoś z zespołu). Nie pracujesz — usuń odwołanie z opisu."$'\n' ;;
      esac
    done
    todo=${todo%$'\n'}
  fi

  cat <<EOF
🏷️ @$author, PR ma błędną składnię — dostaje etykietę \`$LABEL_WRONG\`$([ "$drafted" = 1 ] && printf ' i wraca do wersji roboczej').

**Co się nie zgadza**

$(syntax_problem_lines "$key" "$author")

**Co możesz zrobić teraz**

$todo
- PR celowo nie dotyczy żadnego zgłoszenia — nadaj mu etykietę \`$LABEL_NO_ISSUE\`; wtedy automat niczego nie sprawdza.

Gdy składnia będzie poprawna, etykieta zejdzie sama, a ten komentarz się zwinie.$([ "$drafted" = 1 ] && printf ' Potem oznacz PR jako gotowy („Ready for review”) — to uruchomi ocenę Claude'"'"'a.')

Zasady: [CONTRIBUTING.md — Tworzenie](https://github.com/$REPO/blob/main/CONTRIBUTING.md#tworzenie).

$SYNTAX_MARK $key -->
EOF
}

# syntax_comments N — niezwinięte komentarze składni pod PR-em, po jednym
# w linii: `node_id<TAB>KLUCZ`, od najstarszego. GraphQL, bo tylko on mówi,
# czy komentarz jest zwinięty. Bierzemy 100 ostatnich komentarzy: nasz
# ostatni jest wśród nich praktycznie zawsze, a gdyby PR miał setki
# komentarzy po nim, najgorsze, co się stanie, to jeden powtórzony komentarz
# albo starszy niezwinięty. Autora sprawdzamy, żeby znacznik
# przepisany przez człowieka nie udawał stanu; GraphQL podaje bota bez
# `[bot]`, REST z nim — przyjmujemy oba.
syntax_comments() {
  gh api graphql -f owner="${REPO%%/*}" -f name="${REPO#*/}" -F n="$1" -f query='
    query($owner: String!, $name: String!, $n: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $n) {
          comments(last: 100) { nodes { id body isMinimized author { login } } }
        }
      }
    }' --jq '.data.repository.pullRequest.comments.nodes[]
      | select(.isMinimized | not)
      | select(.author.login == "github-actions" or .author.login == "github-actions[bot]")
      | (.body | capture("<!-- pr-syntax: (?<key>[^ ]+) -->")) as $m
      | "\(.id)\t\($m.key)"'
}

# label_uri NAZWA — nazwa etykiety do ścieżki URL-a (spacja w `wrong syntax`).
label_uri() {
  jq -rn --arg l "$1" '$l | @uri'
}

# check_pr N — sprawdza jeden PR i doprowadza go do stanu zgodnego
# z wynikiem. Pomija PR-y zamknięte, wydaniowe (`stable`) i PR-y botów.
# Kod 2 przy błędzie; wołający woła nas też z `||`, gdzie `set -e` nie
# działa, więc każdy ważny krok ma własne sprawdzenie.
check_pr() {
  local n=$1 pr state base author author_type draft key has_label active
  local last_key need_comment rc url title fail=0

  pr=$(gh api "repos/$REPO/pulls/$n") || return 2
  state=$(jq -r .state <<<"$pr")
  base=$(jq -r .base.ref <<<"$pr")
  author=$(jq -r .user.login <<<"$pr")
  author_type=$(jq -r .user.type <<<"$pr")
  draft=$(jq -r .draft <<<"$pr")
  url=$(jq -r .html_url <<<"$pr")
  title=$(jq -r .title <<<"$pr")

  # Wydanie `main` -> `stable` niesie cudze, dawno powiązane zmiany — samo
  # nie ma zgłoszenia i mieć nie musi. Bot (np. Dependabot) nie przypisze
  # się do zgłoszenia, więc reguła byłaby dla niego nie do spełnienia.
  if [ "$state" != open ]; then
    echo "#$n: zamknięty — pomijam."; return 0
  fi
  if [ "$base" = stable ]; then
    echo "#$n: PR wydaniowy do stable — pomijam."; return 0
  fi
  if [ "$author_type" = Bot ]; then
    echo "#$n: autor to bot ($author) — pomijam."; return 0
  fi

  key=$(syntax_key "$pr") || return 2
  has_label=$(jq -r --arg l "$LABEL_WRONG" \
    'any(.labels[].name; ascii_downcase == ($l | ascii_downcase))' <<<"$pr")
  active=$(syntax_comments "$n") || return 2

  if [ -z "$key" ]; then
    if [ "$has_label" != true ] && [ -z "$active" ]; then
      echo "#$n: składnia w porządku."
      return 0
    fi
    if [ "${DRY_RUN:-}" = 1 ]; then
      echo "[na sucho] #$n: składnia poprawiona — zdjąłbym \"$LABEL_WRONG\" i zwinął $(grep -c . <<<"$active") komentarz(y)."
      return 0
    fi
    if [ "$has_label" = true ]; then
      gh api -X DELETE "repos/$REPO/issues/$n/labels/$(label_uri "$LABEL_WRONG")" > /dev/null || fail=1
    fi
    while IFS=$'\t' read -r node _; do
      [ -n "$node" ] || continue
      minimize_comment "$node" || fail=1
    done <<<"$active"
    echo "#$n: składnia poprawiona — etykieta zdjęta, komentarz zwinięty" \
      | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
    [ "$fail" = 0 ] || return 2
    return 0
  fi

  # Błędna składnia. Komentarz idzie, gdy nie ma niezwiniętego z tym samym
  # powodem (wejście w błąd albo zmiana powodu) albo gdy gotowy PR trzeba
  # cofnąć do wersji roboczej — to drugie dzieje się, gdy autor oznaczył PR
  # jako gotowy bez poprawki, i komentarz mówi, czemu PR wrócił.
  last_key=$(tail -n 1 <<<"$active" | cut -f 2)
  need_comment=false
  if [ "$last_key" != "$key" ] || [ "$draft" != true ]; then
    need_comment=true
  fi

  if [ "${DRY_RUN:-}" = 1 ]; then
    echo "[na sucho] #$n: błędna składnia ($key): $(syntax_short "$key")"
    [ "$has_label" = true ] || echo "  nadałbym \"$LABEL_WRONG\""
    if [ "$need_comment" = true ]; then
      [ "$draft" = true ] || echo "  przestawiłbym PR na wersję roboczą"
      echo "  napisałbym:"
      syntax_comment_body "$key" "$author" "$([ "$draft" = true ] || echo 1)" | sed 's/^/    | /'
      [ -z "$active" ] || echo "  zwinąłbym $(grep -c . <<<"$active") starszy(e) komentarz(e)"
      echo "  i wysłał na Discorda ping do @$author"
    else
      echo "  komentarz z tym powodem już stoi — bez nowego"
    fi
    return 0
  fi

  if [ "$has_label" != true ]; then
    # Pierwszy bieg w repo zakłada etykiety; przy kolejnych `gh` zgłasza
    # „already exists" i to jest w porządku. `No Issue` zakładamy razem
    # z nią, bo komentarz każe ją nadać — musi już istnieć.
    gh label create "$LABEL_WRONG" --repo "$REPO" --color B60205 \
      --description "PR nie wskazuje zgłoszenia swojego autora (Closes/Refs) — patrz komentarz bota" 2> /dev/null || true
    gh label create "$LABEL_NO_ISSUE" --repo "$REPO" --color C5DEF5 \
      --description "PR celowo bez zgłoszenia — sprawdzenie składni go pomija" 2> /dev/null || true
    jq -n --arg l "$LABEL_WRONG" '{labels: [$l]}' \
      | gh api -X POST "repos/$REPO/issues/$n/labels" --input - > /dev/null || fail=1
    echo "#$n: etykieta \"$LABEL_WRONG\" ($key)" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
  fi

  if [ "$need_comment" != true ]; then
    echo "#$n: komentarz z tym powodem już stoi — bez nowego."
    [ "$fail" = 0 ] || return 2
    return 0
  fi

  local drafted=0
  if [ "$draft" != true ]; then
    rc=0
    to_draft "$n" "$(syntax_comment_body "$key" "$author" 1)" || rc=$?
    case $rc in
      0) drafted=1 ;;
      # Wersję roboczą zrobił w międzyczasie autor albo pr-drafts.yml —
      # komentarz o składni piszemy sami, chyba że ten powód już stoi.
      1) if [ "$last_key" = "$key" ]; then
           [ "$fail" = 0 ] || return 2
           return 0
         fi ;;
      *) fail=1 ;;
    esac
  fi
  if [ "$drafted" != 1 ]; then
    gh pr comment "$n" --repo "$REPO" --body "$(syntax_comment_body "$key" "$author" 0)" || fail=1
  fi
  echo "#$n: komentarz o błędnej składni ($key)" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"

  # Nowy komentarz zastępuje starsze — zostaje jeden niezwinięty.
  while IFS=$'\t' read -r node _; do
    [ -n "$node" ] || continue
    minimize_comment "$node" || fail=1
  done <<<"$active"

  # Etykieta, komentarz i wersja robocza z naszego tokena nie dochodzą do
  # discord.yml, a autor ma tu coś do zrobienia — często nie z własnej winy
  # (ktoś zdjął mu przypisanie), więc ping wysyłamy sami.
  discord_send warn "" "🏷️ Błędna składnia PR-a: #$n $title" "$url" \
    "$(syntax_short "$key")$([ "$drafted" = 1 ] && printf '. PR wraca do wersji roboczej')" "" "$author"

  [ "$fail" = 0 ] || return 2
}

# check_prs_for_issue NUMER — po zmianie assignees zgłoszenia sprawdza
# otwarte PR-y, które je wskazują: autor często przypisuje się dopiero po
# otwarciu PR-a, a zdjęcie przypisania psuje składnię PR-a, którego nikt
# nie edytował. Błąd przy jednym PR-ze nie zatrzymuje pozostałych.
check_prs_for_issue() {
  local issue=$1 prs line n refs fail=0

  # Lista najpierw do zmiennej: błąd API w `< <(…)` zginąłby po cichu
  # i wyglądał jak „żaden PR nie wskazuje zgłoszenia".
  prs=$(gh api --paginate "repos/$REPO/pulls?state=open&per_page=100" \
    --jq '.[] | {number, title, body, head: .head.ref} | tojson') || return 2

  while IFS= read -r line; do
    [ -n "$line" ] || continue
    n=$(jq -r .number <<<"$line")
    # Numery do zmiennej, a nie prosto w `grep -q`: ten kończy przy pierwszym
    # trafieniu, a przerwany potok pod `pipefail` dałby fałsz mimo trafienia.
    refs=$(pr_issue_refs "$(jq -r .title <<<"$line")" "$(jq -r '.body // ""' <<<"$line")" \
      "$(jq -r .head <<<"$line")" "$n")
    if grep -qxF "$issue" <<<"$refs"; then
      check_pr "$n" || fail=1
    fi
  done <<<"$prs"

  [ "$fail" = 0 ] || return 2
}
