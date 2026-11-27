# Merge na kredyt (#506): PR z zielonym werdyktem oceny Claude'a może wejść do
# `main` bez recenzji człowieka, dopóki kolejka takich PR-ów jest krótsza niż
# CREDIT_LIMIT. Kolejka to fakty z API: PR-y scalone do `main` od merge'a
# CREDIT_PR (config.sh), które nie mają stojącego zatwierdzenia osoby
# z zespołu (liczonego jak w approvals.sh). Etykieta LABEL_CREDIT jest tylko widokiem tych
# faktów, żeby kolejkę widać było na liście PR-ów: credit_sweep dokłada ją na
# scalony PR bez zatwierdzenia i zdejmuje z zatwierdzonego, więc ręczne zdjęcie
# albo dołożenie etykiety niczego nie zmienia i przy następnym biegu wraca jak
# było. Wołający robi `source` (razem z approvals.sh i pr-state.sh —
# pr-state.sh sam wczytuje ten plik) i bierze z env:
#
#   GH_TOKEN  token z odczytem PR-ów i zgłoszeń (etykiety: zapis)
#   REPO      `właściciel/repozytorium`
#   DRY_RUN   `1` — tylko wypisz, co by się zmieniło; nic nie zapisuj
#
# Wołający pobiera ten plik z `main`, tak jak skrypt Discorda: od statusu,
# który stąd wynika, zależy, czy PR da się zmergować.
#
# Nic tu nie pamiętamy: kolejkę liczymy za każdym razem od zera, więc nie ma
# znaczenia, które zdarzenie przepadło — następny bieg policzy dobrze.
# Chronionych plików (workflow, Docker, `.claude/`) ten mechanizm nie pilnuje:
# robią to code ownerzy z `.github/CODEOWNERS`.

source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

LABEL_COLOR_CREDIT="fef2c0"

# credit_since — chwila startu kolejki: merge CREDIT_PR, z API. Liczona
# z faktów, żeby nikt nie musiał przy merge'u wpisywać daty. PR jeszcze
# niescalony (albo błąd API) wywraca bieg, zamiast liczyć kolejkę od zera.
credit_since() {
  local at
  at=$(gh api "repos/$REPO/pulls/$CREDIT_PR" --jq '.merged_at // empty') || return 1
  if [ -z "$at" ]; then
    echo "::error::#$CREDIT_PR (start kredytu) nie jest scalony — nie ma od czego liczyć kolejki." >&2
    return 1
  fi
  echo "$at"
}

# credit_facts — scalone do `main` PR-y od merge'a CREDIT_PR jako tablica JSON
# `{number, labeled, approved}`: czy PR ma etykietę kredytu i czy ma stojące
# zatwierdzenie osoby z zespołu. Jedno zapytanie GraphQL na 50 PR-ów razem
# z ich recenzjami i etykietami, a nie recenzje po jednym. Lista idzie od
# ostatnio zmienionych i urywa się, gdy cała strona jest starsza niż start:
# PR scalony od tej chwili ma `updatedAt` nie wcześniejszy niż merge, więc
# dalej nic już nie ma. Nie wyszukiwarka: ta spóźnia się z indeksem, a po
# merge'u przeliczamy kolejkę od razu.
credit_facts() {
  local cursor="" page rows="" args pr since
  since=$(credit_since) || return 1
  while :; do
    args=(-f owner="${REPO%/*}" -f name="${REPO#*/}")
    [ -z "$cursor" ] || args+=(-f after="$cursor")
    page=$(gh api graphql "${args[@]}" -f query='
      query($owner: String!, $name: String!, $after: String) {
        repository(owner: $owner, name: $name) {
          pullRequests(states: MERGED, baseRefName: "main", first: 50, after: $after,
                       orderBy: {field: UPDATED_AT, direction: DESC}) {
            pageInfo { hasNextPage endCursor }
            nodes {
              number mergedAt updatedAt
              labels(first: 30) { nodes { name } }
              reviews(first: 100) {
                nodes { databaseId state submittedAt author { login __typename } commit { oid } }
              }
            }
          }
        }
      }')
    # Recenzje w kształcie REST, żeby team_reviews i standing_approvals
    # liczyły je tak samo jak wszędzie (konto skasowane po fakcie to
    # `user: null`). Zatwierdzenie konta spoza zespołu nie zwalnia miejsca.
    rows+=$(jq -c --arg since "$since" --arg label "$LABEL_CREDIT" '
      .data.repository.pullRequests.nodes[]
      | select(.mergedAt >= $since)
      | {number, labeled: any(.labels.nodes[]; .name == $label),
         reviews: [.reviews.nodes[] | {id: .databaseId, state, submitted_at: .submittedAt,
           commit_id: .commit.oid,
           user: (if .author == null then null else {login: .author.login, type: .author.__typename} end)}]}' <<<"$page")$'\n'
    jq -e --arg since "$since" '.data.repository.pullRequests
        | .pageInfo.hasNextPage and ((.nodes | last.updatedAt) >= $since)' <<<"$page" > /dev/null || break
    cursor=$(jq -r '.data.repository.pullRequests.pageInfo.endCursor' <<<"$page")
  done
  while IFS= read -r pr; do
    [ -n "$pr" ] || continue
    jq -c --argjson a "$(jq -c .reviews <<<"$pr" | team_reviews | standing_approvals | jq length)" \
      '{number, labeled, approved: ($a > 0)}' <<<"$pr"
  done <<<"$rows" | jq -s .
}

# credit_numbers — numery PR-ów w kolejce kredytu, po jednym w linijce.
credit_numbers() {
  credit_facts | jq -r '.[] | select(.approved | not) | .number'
}

# credit_count [POMIŃ] — ile PR-ów stoi w kolejce, bez PR-a POMIŃ (numer).
credit_count() {
  credit_numbers | jq -Rs --arg skip "${1:-}" 'split("\n") | map(select(. != "" and . != $skip)) | length'
}

# human_approvals N — ile stojących zatwierdzeń człowieka ma PR (liczy się jak
# w approvals.sh: ostatnia recenzja zmieniająca stan każdej osoby z zespołu).
human_approvals() {
  pr_reviews "$1" | standing_approvals | jq length
}

# credit_status SUMA STATUSY KOLEJKA CZUBEK ZESPÓŁ — status „Recenzja albo
# kredyt” jako `{state, description}`. SUMA to `{approved_commits,
# changes_requested}` z `review_summary` (recenzje już tylko od zespołu),
# STATUSY to `commit_statuses` czubka PR-a, KOLEJKA to `credit_count`,
# CZUBEK to SHA czubka PR-a, ZESPÓŁ to `true`, gdy autor PR-a ma prawo zapisu.
# Zielony, gdy:
#
#   • PR ma stojące zatwierdzenie osoby z zespołu, które dotyczy czubka (i nikt
#     nie ma czekającej prośby o zmiany — GitHub przy zerowej liczbie
#     wymaganych zatwierdzeń jej już nie pilnuje, więc pilnujemy sami), albo
#   • (kredyt) autor jest z zespołu, ostatnia ocena Claude'a na tym commicie
#     jest skończona z zielonym werdyktem, a w kolejce jest jeszcze miejsce.
#
# Zatwierdzenie dotyczy czubka, gdy na nim powstało albo gdy ocena Claude'a
# na czubku jest skończona: ona kończy się dopiero po rozstrzygnięciu
# o zatwierdzeniach sprzed zmian (approvals.sh) — zatwierdzenie, które dalej
# stoi, Claude uznał za aktualne, a resztę workflow już cofnął. Do tego czasu
# zatwierdzenie sprzed pushu nie liczy się, tak jak przy wbudowanym
# „Dismiss stale pull request approvals”, tylko bez wołania recenzenta od
# nowa po samym „Update branch”.
#
# Status oceny jest przypięty do commita, więc „dotyczy dokładnie czubka PR-a”
# wynika z tego, że bierzemy go z `STATUSY` czubka. Liczy się tylko status
# postawiony przez nasze workflow (STATUS_CREATOR) — taki sam od kogoś innego
# to podróbka. Opis oceny ma werdykt po „Ocena gotowa: ”, a set_status
# zamienia 🟢 na ✅ (GitHub odrzuca znaki 4-bajtowe) — rozpoznajemy oba
# zapisy. Kredyt tylko dla autorów z zespołu: oceny Claude'a dostają i tak
# tylko PR-y z gałęzi tego repo, ale prawo zapisu mogło zostać odebrane.
# Brak zielonego światła to nigdy `failure`: to „jeszcze nie”, nie „źle”.
credit_status() {
  jq -nc --argjson sum "$1" --argjson st "$2" --argjson q "$3" --arg head "$4" --argjson team "$5" \
    --argjson max "$CREDIT_LIMIT" --arg ctx "$REVIEW_CONTEXT" --arg creator "$STATUS_CREATOR" '
    ([$st[] | select(.context == $ctx)] | first // null) as $raw
    | (if $raw != null and $raw.creator == $creator then $raw else null end) as $rv
    | ($rv != null and $rv.state == "success") as $judged
    | ([$sum.approved_commits[] | select($judged or . == $head)] | length) as $fresh
    | if ($sum.changes_requested | length) > 0 then
        {state: "pending", description: ("Czeka prośba o zmiany od " + ($sum.changes_requested | join(", ")))}
      elif $fresh > 0 then
        {state: "success", description: "Zatwierdzenie człowieka"}
      elif $raw != null and $rv == null then
        {state: "pending", description: "Status oceny Claude'\''a nie pochodzi z GitHub Actions — pomijam go; potrzebna recenzja człowieka"}
      elif ($sum.approved_commits | length) > 0 then
        {state: "pending", description: "Zatwierdzenie sprzed ostatnich zmian — liczy się po ocenie Claude'\''a albo nowym zatwierdzeniu"}
      elif $rv == null or $rv.state != "success" then
        {state: "pending", description: "Czeka na ocenę Claude'\''a albo recenzję człowieka"}
      elif ($rv.description | test("^Ocena gotowa: (✅|🟢)") | not) then
        {state: "pending", description: "Werdykt oceny nie jest zielony — potrzebna recenzja człowieka"}
      elif ($team | not) then
        {state: "pending", description: "Kredyt tylko dla autorów z prawem zapisu — potrzebna recenzja człowieka"}
      elif $q < $max then
        {state: "success", description: "kredyt: \($q)/\($max) w kolejce"}
      else
        {state: "pending", description: "kolejka pełna \($q)/\($max) — potrzebna recenzja"}
      end'
}

# credit_sweep — zgrywa etykietę kredytu z faktami (credit_facts): dokłada ją
# na scalony PR bez zatwierdzenia, zdejmuje ze scalonego PR-a z zatwierdzeniem.
# To nasza akcja, więc nikogo nie woła. Ręczne zdjęcie etykiety też nikogo nie
# woła, bo nic nie zmienia: kolejka nie liczy etykiet, a ten bieg ją przywróci.
# Rozstrzyga stan z API, a nie to, które zdarzenie nas obudziło — recenzja
# scalonego PR-a mogła nie wywołać żadnego biegu.
credit_sweep() {
  local facts n labeled approved ensured=""
  facts=$(credit_facts)
  echo "Kolejka kredytu: $(jq '[.[] | select(.approved | not)] | length' <<<"$facts")/$CREDIT_LIMIT" \
    | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
  while IFS=$'\t' read -r n labeled approved; do
    [ -n "$n" ] || continue
    if [ "$approved" = false ] && [ "$labeled" = false ]; then
      if [ -z "$ensured" ]; then
        ensure_label "$LABEL_CREDIT" "$LABEL_COLOR_CREDIT" "PR scalony bez recenzji człowieka — zajmuje miejsce w kolejce, zwalnia je zatwierdzenie (etykieta tylko pokazuje kolejkę)"
        ensured=1
      fi
      if [ "${DRY_RUN:-}" = 1 ]; then
        echo "DRY_RUN: #$n: dałbym „${LABEL_CREDIT}” — scalony bez zatwierdzenia"
      else
        jq -n --arg l "$LABEL_CREDIT" '{labels: [$l]}' | gh api -X POST "repos/$REPO/issues/$n/labels" --input - > /dev/null
      fi
      echo "#$n: scalony bez recenzji — zajmuje miejsce w kolejce kredytu." | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
    elif [ "$approved" = true ] && [ "$labeled" = true ]; then
      if [ "${DRY_RUN:-}" = 1 ]; then
        echo "DRY_RUN: #$n: zdjąłbym „${LABEL_CREDIT}” — jest zatwierdzenie"
      else
        gh api -X DELETE "repos/$REPO/issues/$n/labels/$(jq -rn --arg s "$LABEL_CREDIT" '$s | @uri')" > /dev/null
      fi
      echo "#$n: zatwierdzony po merge'u — kredyt spłacony, miejsce w kolejce wolne." | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
    fi
  done < <(jq -r '.[] | [.number, .labeled, .approved] | @tsv' <<<"$facts")
}

# credit_merge_note N — dopisek do wiadomości o merge'u (discord.yml): puste,
# gdy PR miał zatwierdzenie; inaczej „scalony bez recenzji — kredyt 7/15”.
# Lista scalonych PR-ów mogła jeszcze nie dogonić merge'a, więc kolejkę liczymy
# bez tego PR-a i dodajemy jego miejsce.
credit_merge_note() {
  local n=$1 q
  [ "$(human_approvals "$n")" -eq 0 ] || return 0
  q=$(credit_count "$n")
  echo "Scalony bez recenzji — kredyt $((q + 1))/$CREDIT_LIMIT"
}
