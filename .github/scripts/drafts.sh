# Przestawianie gotowych PR-ów z powrotem na wersję roboczą (#349). Gotowy PR
# nie dostaje zmian: poprawki po ocenie i recenzji oraz rozwiązanie konfliktu
# idą przez wersję roboczą, a wyjście z niej („Ready for review") uruchamia
# ponowną ocenę Claude'a. Wołający robi `source`; resztę bierze z env:
#
#   GH_TOKEN  token z `pull-requests: write`
#   REPO      `właściciel/repozytorium`
#   GIT_REPO  klon repo z historią, w którym porównujemy zmiany; domyślnie `.`
#
# Wołający pobiera ten plik z `main`, tak jak skrypt Discorda.
#
# Przestawienie robimy naszym tokenem, a takich zdarzeń GitHub nie przekazuje
# innym workflow — `converted_to_draft` nie dostaną ani discord.yml, ani
# issue-labels.yml. Dlatego pod PR-em zostaje komentarz z powodem.

DRAFT_ON_PUSH="Push do gotowego PR-a — PR wraca do wersji roboczej. Zmiany idą WYŁĄCZNIE przez wersję roboczą, a oznaczenie jako gotowy („Ready for review”) uruchamia ponowną ocenę Claude'a."
DRAFT_ON_CONFLICT="Konflikt z gałęzią bazową — PR wraca do wersji roboczej. Zmiany idą WYŁĄCZNIE przez wersję roboczą, a oznaczenie jako gotowy („Ready for review”) po rozwiązaniu konfliktu uruchamia ponowną ocenę Claude'a."

# drop_review_requests N UŻYTKOWNICY ZESPOŁY — zdejmuje czekające prośby
# o recenzję (listy po przecinku). Zdjęcie od zespołu może się nie udać, gdy
# token nie widzi zespołów organizacji; wtedy tylko ostrzegamy, bo reszta
# (ping, przestawienie) ma pójść i tak.
drop_review_requests() {
  local n=$1 users=$2 teams=$3

  [ -n "$users$teams" ] || return 0
  if ! jq -n --arg u "$users" --arg t "$teams" \
      '{reviewers: ($u | split(",") | map(select(. != ""))),
        team_reviewers: ($t | split(",") | map(select(. != "")))}' \
      | gh api -X DELETE "repos/$REPO/pulls/$n/requested_reviewers" --input - > /dev/null; then
    echo "::warning::#$n: nie udało się zdjąć próśb o recenzję ($users $teams)."
  fi
}

# to_draft N KOMENTARZ — przestawia PR na wersję roboczą, zdejmuje czekające
# prośby o recenzję (w gotowym PR-ze nie może ich być przed oceną Claude'a)
# i zostawia pod nim KOMENTARZ. Kod 1, gdy PR już jest wersją roboczą:
# przestawił go autor albo równoległy bieg, więc drugi komentarz byłby
# szumem. Kod 2, gdy przestawienie się nie udało — wołający woła nas w `if`
# albo z `||`, gdzie `set -e` nie działa, więc błąd musi wyjść kodem, a nie
# zginąć po cichu.
to_draft() {
  local n=$1 body=$2 json

  json=$(gh api "repos/$REPO/pulls/$n")
  if [ "$(printf '%s' "$json" | jq -r .draft)" = true ]; then
    return 1
  fi

  if ! gh pr ready "$n" --repo "$REPO" --undo; then
    echo "::error::#$n: nie udało się przestawić PR-a na wersję roboczą."
    return 2
  fi
  drop_review_requests "$n" \
    "$(printf '%s' "$json" | jq -r '.requested_reviewers | map(.login) | join(",")')" \
    "$(printf '%s' "$json" | jq -r '.requested_teams | map(.slug) | join(",")')"
  gh pr comment "$n" --repo "$REPO" --body "$body"
  echo "#$n: przestawiony na wersję roboczą" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
}

# same_changes BAZA PRZED PO — kod 0, gdy PR po pushu wnosi do BAZY te same
# zmiany co przed nim. Tak wygląda samo dociągnięcie BAZY (przycisk „Update
# branch", merge BAZY bez poprawek, rebase): diff PR-a się nie zmienia, więc
# ocena nie ma czego oglądać. Każdy diff liczymy od wspólnego przodka z BAZĄ,
# bez kontekstu (`-U0`), żeby zmiany w BAZIE tuż obok nie liczyły się jako
# zmiana PR-a, i porównujemy `patch-id`, które pomija numery linii.
same_changes() {
  local base=$1 before=$2 after=$3 git=(git -C "${GIT_REPO:-.}") a b

  a=$("${git[@]}" diff -U0 "$("${git[@]}" merge-base "$base" "$before")" "$before" \
    | "${git[@]}" patch-id --stable)
  b=$("${git[@]}" diff -U0 "$("${git[@]}" merge-base "$base" "$after")" "$after" \
    | "${git[@]}" patch-id --stable)
  [ "$a" = "$b" ]
}

# carry_review_status N PRZED PO — przenosi zielony status „Ocena Claude'a”
# (REVIEW_CONTEXT ze statuses.sh, które wołający też musi zrobić `source`)
# ze starego czubka PR-a na nowy; token potrzebuje do tego jeszcze
# `statuses: write`. Woła się to tylko po pushu, który według
# `same_changes` jedynie dociągnął bazę: ocena nie miałaby czego oglądać,
# a bez statusu na nowym commicie wymagany status blokowałby merge — po
# „Update branch” PR wisiałby do następnego `@claude`.
#
# Przenosimy tylko `success`. Status w toku (`pending`) zostaje na starym
# commicie: bieg oceny skończy go tam i nie wie o nowym czubku, więc
# przeniesiony wisiałby w nieskończoność. Porażka (`failure`/`error`) też
# zostaje — nowy commit bez statusu blokuje merge tak samo, a ratunek ten sam:
# `@claude`. Kod różny od zera, gdy przeniesienie się nie udało: PR stoi
# wtedy zablokowany bez powodu, więc ma to być widać na czerwono.
carry_review_status() {
  local n=$1 before=$2 after=$3 st desc

  st=$(get_status "$before" "$REVIEW_CONTEXT")
  if [ -z "$st" ] || [ "$(jq -r .state <<<"$st")" != success ]; then
    echo "#$n: na ${before:0:7} nie ma zielonego statusu „$REVIEW_CONTEXT” — nie przenoszę."
    return 0
  fi
  desc="Z ${before:0:7} (samo dociągnięcie bazy) — $(jq -r '.description // ""' <<<"$st")"
  set_status "$after" "$REVIEW_CONTEXT" success "$desc" "$(jq -r '.target_url // ""' <<<"$st")" || return 1
  echo "#$n: status „$REVIEW_CONTEXT” przeniesiony z ${before:0:7} na ${after:0:7}." \
    | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
}
