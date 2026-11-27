# Statusy commitów, które stawiają nasze workflow i których wymagają reguły
# `main` do merge'a. Wołający robi `source`; resztę bierze z env:
#
#   GH_TOKEN  token ze `statuses: write` (do samego odczytu wystarcza
#             `contents: read`)
#   REPO      `właściciel/repozytorium`
#   DRY_RUN   `1` — tylko wypisz, jaki status by poszedł
#
# Wołający pobiera ten plik z `main`, tak jak skrypt Discorda: od tych
# statusów zależy, czy PR da się zmergować.
#
# Status, a nie check run: status ustawia się jednym wywołaniem API na
# dowolnym commicie, także z workflow, który biegnie na zupełnie innym
# commicie (`pull_request_target`, `workflow_run`), i da się go przenieść
# z commitu na commit (drafts.sh). Check run przypina GitHub do biegu, który
# go stworzył.
#
# Nazwy statusów są częścią reguł `main` (wymagane statusy) — zmiana nazwy tu
# bez zmiany w regułach zablokuje merge wszystkich PR-ów.
REVIEW_CONTEXT="Ocena Claude'a"
# Zatwierdzenie od każdego recenzenta koniecznego (#352) — stawia pr-state.sh.
REVIEWERS_CONTEXT="Recenzenci konieczni"
# Zatwierdzenie człowieka albo kredyt (#506) — stawia pr-state.sh; zastępuje
# wbudowany wymóg „1 zatwierdzenie” w regułach `main`.
CREDIT_CONTEXT="Recenzja albo kredyt"

# Opis zielonego statusu „Ocena Claude'a” to umowa między tym, kto go pisze
# (claude-review-pr.yml), kto go przenosi (drafts.sh) i kto z niego czyta
# werdykt (credit.sh) — dlatego tylko trzy funkcje niżej znają jego kształt.
# Werdykt stoi zawsze na samym początku, po REVIEW_DONE_PREFIX: tak przetrwa
# przycięcie opisu do 140 znaków, a przeniesienie dopisuje swoje na końcu.
REVIEW_DONE_PREFIX="Ocena gotowa: "
REVIEW_CARRIED_MARK=" · przeniesiona z "

# review_done_desc WERDYKT — opis statusu po skończonej ocenie.
review_done_desc() {
  printf '%s%s' "$REVIEW_DONE_PREFIX" "${1:-bez werdyktu}"
}

# review_carried_desc OPIS SHA — opis statusu przeniesionego z commitu SHA po
# samym dociągnięciu bazy: werdykt zostaje na początku, a skąd przyszedł,
# mówi dopisek na końcu. Dopisek z wcześniejszego przeniesienia zastępujemy,
# żeby kolejne „Update branch” nie doklejały następnych.
review_carried_desc() {
  jq -rn --arg d "$1" --arg m "$REVIEW_CARRIED_MARK" --arg s "${2:0:7}" \
    '($d | split($m) | first) + $m + $s + " (samo dociągnięcie bazy)"'
}

# review_green_jq — definicja jq `review_green`: czy status (obiekt z
# `description`) niesie zielony werdykt. set_status zamienia 🟢 na ✅
# (GitHub odrzuca znaki 4-bajtowe) — rozpoznajemy oba zapisy. Patrzymy
# tylko na początek opisu: dalej może stać tekst werdyktu od Claude'a.
review_green_jq() {
  cat <<JQ
    def review_green:
      (.description // "") as \$d
      | any("${REVIEW_DONE_PREFIX}✅", "${REVIEW_DONE_PREFIX}🟢"; . as \$p | \$d | startswith(\$p));
JQ
}

# set_status SHA NAZWA STAN OPIS [LINK] — stawia status NAZWA na commicie.
# STAN: pending / success / failure / error. GitHub odrzuca opis dłuższy niż
# 140 znaków, więc przycinamy go sami — lepiej urwany opis niż brak statusu,
# który blokuje merge. Odrzuca też znaki zajmujące w UTF-8 4 bajty, a takie
# są kropki werdyktu (🟢🟡🟠🔴) — podmieniamy je na symbole 3-bajtowe, a resztę
# takich znaków wyrzucamy. Opis może nieść tekst od ludzi (werdykt z oceny),
# więc do API idzie wyłącznie przez jq.
set_status() {
  local sha=$1 ctx=$2 state=$3 desc=$4 url=${5:-} body

  body=$(jq -n --arg s "$state" --arg c "$ctx" --arg d "$desc" --arg u "$url" '
    ($d | gsub("🟢"; "✅") | gsub("🟡"; "⚠️") | gsub("🟠"; "❗") | gsub("🔴"; "❌")
        | explode | map(select(. < 65536)) | implode
        | gsub("  +"; " ") | ltrimstr(" ") | rtrimstr(" ")) as $d
    | {state: $s, context: $c,
       description: (if ($d | length) > 140 then $d[0:139] + "…" else $d end)}
    + (if $u != "" then {target_url: $u} else {} end)')
  if [ "${DRY_RUN:-}" = 1 ]; then
    echo "DRY_RUN: status na ${sha:0:7}: $(jq -c . <<<"$body")"
    return 0
  fi
  gh api -X POST "repos/$REPO/statuses/$sha" --input - <<<"$body" > /dev/null
}

# Konto, jako które piszą statusy nasze workflow (token `github.token`).
# Status tej samej nazwy od kogokolwiek innego — ktoś z prawem zapisu przez
# API albo inna aplikacja — jest podróbką: nie czytamy go jako oceny ani
# zatwierdzenia, a nasze nazwy nadpisujemy. Reguły `main` mają ponadto każdy
# wymagany status przypięty do źródła „GitHub Actions” (WORKFLOWS.md, zasada
# 14), więc taki status i tak nie odblokuje merge'a.
STATUS_CREATOR="github-actions[bot]"

# get_status SHA NAZWA — najnowszy status NAZWA na commicie jako
# `{state, description, target_url, creator}` albo nic, gdy go nie ma.
get_status() {
  commit_statuses "$1" | jq -c --arg c "$2" '.[] | select(.context == $c) | {state, description, target_url, creator}'
}

# commit_statuses SHA — najnowszy status każdej nazwy na commicie, jako
# tablica `{context, state, description, target_url, creator}`. Jedno
# wywołanie API dla wszystkich nazw naraz — pr-state.sh potrzebuje kilku.
# Lista statusów, a nie zbiorczy status commitu: tylko ona mówi, kto status
# postawił. Idzie od najnowszego, więc pierwszy z każdej nazwy jest aktualny;
# sto ostatnich wystarcza, bo statusy piszemy tylko przy zmianie.
commit_statuses() {
  gh api "repos/$REPO/commits/$1/statuses?per_page=100" \
    | jq -c '[group_by(.context)[] | first
              | {context, state, description, target_url, creator: (.creator.login // null)}]'
}
