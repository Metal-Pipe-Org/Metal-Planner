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

# set_status SHA NAZWA STAN OPIS [LINK] — stawia status NAZWA na commicie.
# STAN: pending / success / failure / error. GitHub odrzuca opis dłuższy niż
# 140 znaków, więc przycinamy go sami — lepiej urwany opis niż brak statusu,
# który blokuje merge. Opis może nieść tekst od ludzi (werdykt z oceny), więc
# do API idzie wyłącznie przez jq.
set_status() {
  local sha=$1 ctx=$2 state=$3 desc=$4 url=${5:-} body

  body=$(jq -n --arg s "$state" --arg c "$ctx" --arg d "$desc" --arg u "$url" '
    {state: $s, context: $c,
     description: (if ($d | length) > 140 then $d[0:139] + "…" else $d end)}
    + (if $u != "" then {target_url: $u} else {} end)')
  if [ "${DRY_RUN:-}" = 1 ]; then
    echo "DRY_RUN: status na ${sha:0:7}: $(jq -c . <<<"$body")"
    return 0
  fi
  gh api -X POST "repos/$REPO/statuses/$sha" --input - <<<"$body" > /dev/null
}

# get_status SHA NAZWA — najnowszy status NAZWA na commicie jako
# `{state, description, target_url}` albo nic, gdy go nie ma. Zbiorczy status
# commitu podaje już tylko najnowszy status każdej nazwy.
get_status() {
  gh api "repos/$REPO/commits/$1/status?per_page=100" \
    | jq -c --arg c "$2" '.statuses[]? | select(.context == $c) | {state, description, target_url}'
}
