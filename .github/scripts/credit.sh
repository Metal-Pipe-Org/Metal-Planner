# Merge na kredyt (#506): PR z zielonym werdyktem oceny Claude'a może wejść do
# `main` bez recenzji człowieka, dopóki kolejka takich PR-ów jest krótsza niż
# CREDIT_LIMIT. Kolejka to scalone PR-y z etykietą LABEL_CREDIT; etykietę daje
# po merge'u pr-credit.yml, a zdejmuje ją zatwierdzenie człowieka (też po
# merge'u). Wołający robi `source` (razem z approvals.sh i pr-state.sh —
# pr-state.sh sam wczytuje ten plik) i bierze z env:
#
#   GH_TOKEN  token z odczytem PR-ów i zgłoszeń (etykiety: zapis)
#   REPO      `właściciel/repozytorium`
#   DRY_RUN   `1` — tylko wypisz, co by się zmieniło; nic nie zapisuj
#
# Wołający pobiera ten plik z `main`, tak jak skrypt Discorda: od statusu,
# który stąd wynika, zależy, czy PR da się zmergować.
#
# Nic tu nie pamiętamy: kolejkę liczymy za każdym razem z etykiet w API, więc
# nie ma znaczenia, które zdarzenie przepadło — następny bieg policzy dobrze.
# Chronionych plików (workflow, Docker, `.claude/`) ten mechanizm nie pilnuje:
# robią to code ownerzy z `.github/CODEOWNERS`.

source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

LABEL_COLOR_CREDIT="fef2c0"

# credit_numbers — numery scalonych PR-ów z etykietą kredytu, po jednym
# w linijce. Lista zgłoszeń, nie wyszukiwarka: wyszukiwarka GitHuba spóźnia się
# z indeksem, a po merge'u przeliczamy kolejkę od razu. Zgłoszenia mieszają
# się z PR-ami, stąd `pull_request`; etykieta trafia tylko na scalone PR-y,
# ale `merged_at` sprawdzamy na wszelki wypadek.
credit_numbers() {
  gh api --paginate --slurp \
    "repos/$REPO/issues?state=all&per_page=100&labels=$(jq -rn --arg s "$LABEL_CREDIT" '$s | @uri')" \
    | jq -r 'add // [] | .[] | select(.pull_request != null and .pull_request.merged_at != null) | .number'
}

# credit_count [POMIŃ] — ile PR-ów stoi w kolejce, bez PR-a POMIŃ (numer).
credit_count() {
  credit_numbers | jq -Rs --arg skip "${1:-}" 'split("\n") | map(select(. != "" and . != $skip)) | length'
}

# human_approvals N — ile stojących zatwierdzeń człowieka ma PR (liczy się jak
# w approvals.sh: ostatnia recenzja zmieniająca stan każdej osoby, bez botów).
human_approvals() {
  pr_reviews "$1" | standing_approvals | jq length
}

# credit_status SUMA STATUSY KOLEJKA — status „Recenzja albo kredyt” jako
# `{state, description}`. SUMA to `{approvals, changes_requested}` z
# `review_summary`, STATUSY to `commit_statuses` czubka PR-a, KOLEJKA to
# `credit_count`. Zielony, gdy:
#
#   • PR ma stojące zatwierdzenie człowieka (i nikt nie ma czekającej prośby
#     o zmiany — GitHub przy zerowej liczbie wymaganych zatwierdzeń jej już
#     nie pilnuje, więc pilnujemy sami), albo
#   • (kredyt) ostatnia ocena Claude'a na tym commicie jest skończona
#     z zielonym werdyktem, a w kolejce jest jeszcze miejsce.
#
# Status oceny jest przypięty do commita, więc „dotyczy dokładnie czubka PR-a”
# wynika z tego, że bierzemy go z `STATUSY` czubka. Opis oceny ma werdykt po
# „Ocena gotowa: ”, a set_status zamienia 🟢 na ✅ (GitHub odrzuca znaki
# 4-bajtowe) — rozpoznajemy oba zapisy. Brak zielonego światła to nigdy
# `failure`: to „jeszcze nie”, nie „źle”.
credit_status() {
  jq -nc --argjson sum "$1" --argjson st "$2" --argjson q "$3" \
    --argjson max "$CREDIT_LIMIT" --arg ctx "$REVIEW_CONTEXT" '
    ([$st[] | select(.context == $ctx)] | first // null) as $rv
    | if ($sum.changes_requested | length) > 0 then
        {state: "pending", description: ("Czeka prośba o zmiany od " + ($sum.changes_requested | join(", ")))}
      elif $sum.approvals > 0 then
        {state: "success", description: "Zatwierdzenie człowieka"}
      elif $rv == null or $rv.state != "success" then
        {state: "pending", description: "Czeka na ocenę Claude'\''a albo recenzję człowieka"}
      elif ($rv.description | test("^Ocena gotowa: (✅|🟢)") | not) then
        {state: "pending", description: "Werdykt oceny nie jest zielony — potrzebna recenzja człowieka"}
      elif $q < $max then
        {state: "success", description: "kredyt: \($q)/\($max) w kolejce"}
      else
        {state: "pending", description: "kolejka pełna \($q)/\($max) — potrzebna recenzja"}
      end'
}

# credit_on_merge N — po merge'u: PR bez żadnego zatwierdzenia człowieka dostaje
# etykietę kredytu. Zatwierdzenie, które stało w chwili merge'a, liczy się tak
# jak przy statusie, więc PR, który przeszedł dzięki recenzji, kredytu nie zajmuje.
credit_on_merge() {
  local n=$1
  if [ "$(human_approvals "$n")" -gt 0 ]; then
    echo "#$n: scalony z recenzją człowieka — kredytu nie zajmuje."
    return 0
  fi
  ensure_label "$LABEL_CREDIT" "$LABEL_COLOR_CREDIT" "PR scalony bez recenzji człowieka — zajmuje miejsce w kolejce, zwalnia je zatwierdzenie"
  if [ "${DRY_RUN:-}" = 1 ]; then
    echo "DRY_RUN: #$n: dałbym „$LABEL_CREDIT”"
  else
    jq -n --arg l "$LABEL_CREDIT" '{labels: [$l]}' | gh api -X POST "repos/$REPO/issues/$n/labels" --input - > /dev/null
  fi
  echo "#$n: scalony bez recenzji — kredyt $(credit_count)/$CREDIT_LIMIT." | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
}

# credit_sweep — z każdego scalonego PR-a z etykietą kredytu, który ma już
# stojące zatwierdzenie człowieka, zdejmuje etykietę. To nasza akcja, więc nie
# woła nikogo na Discordzie (pr-credit.yml pinguje tylko ręczne zdjęcie).
# Rozstrzyga stan z API, a nie to, które zdarzenie nas obudziło — recenzja
# scalonego PR-a mogła nie wywołać żadnego biegu.
credit_sweep() {
  local n
  for n in $(credit_numbers); do
    [ "$(human_approvals "$n")" -gt 0 ] || continue
    if [ "${DRY_RUN:-}" = 1 ]; then
      echo "DRY_RUN: #$n: zdjąłbym „$LABEL_CREDIT” — jest zatwierdzenie"
    else
      gh api -X DELETE "repos/$REPO/issues/$n/labels/$(jq -rn --arg s "$LABEL_CREDIT" '$s | @uri')" > /dev/null
    fi
    echo "#$n: zatwierdzony po merge'u — kredyt spłacony, miejsce w kolejce wolne." | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
  done
}

# credit_unlabeled N SENDER TYTUŁ URL — ktoś zdjął etykietę kredytu ręcznie.
# Gdy PR nie ma zatwierdzenia, to obejście kolejki: wiadomość z @everyone na
# Discordzie (discord_send musi być wczytany). Z zatwierdzeniem zdjęcie jest
# tym, co i tak zrobiłby sweep, więc milczymy. Nasze własne zdjęcia nie
# docierają tu wcale: token workflow nie tworzy zdarzeń.
credit_unlabeled() {
  local n=$1 sender=$2 title=$3 url=$4
  if [ "$(human_approvals "$n")" -gt 0 ]; then
    echo "#$n: etykietę zdjął $sender, ale PR ma zatwierdzenie — bez pingu."
    return 0
  fi
  discord_send warn "$sender" "⚠️ Zdjęta etykieta kredytu bez zatwierdzenia: #$n $title" "$url" \
    "Kolejka kredytu po zdjęciu: $(credit_count)/$CREDIT_LIMIT. PR nie ma zatwierdzenia człowieka — ktoś musi go przejrzeć po fakcie." "" "" everyone
}

# credit_merge_note N — dopisek do wiadomości o merge'u (discord.yml): puste,
# gdy PR miał zatwierdzenie; inaczej „scalony bez recenzji — kredyt 7/15”.
# Etykiety jeszcze może nie być (bieg pr-credit.yml idzie równolegle), więc
# kolejkę liczymy bez tego PR-a i dodajemy jego miejsce.
credit_merge_note() {
  local n=$1 q
  [ "$(human_approvals "$n")" -eq 0 ] || return 0
  q=$(credit_count "$n")
  echo "Scalony bez recenzji — kredyt $((q + 1))/$CREDIT_LIMIT"
}
