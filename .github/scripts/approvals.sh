# Zatwierdzenia ludzi po ocenie Claude'a (#350). W ustawieniach `main`
# wyłączone jest „Dismiss stale pull request approvals when new commits are
# pushed”: GitHub zdejmowałby zatwierdzenie przy każdym pushu, także przy
# samym „Update branch” albo rozwiązaniu konfliktu, i za każdym razem trzeba
# by wołać recenzenta od nowa. Zamiast tego po każdej ocenie Claude'a
# workflow sam rozstrzyga, czy zatwierdzenie człowieka dalej obowiązuje.
# Wołający robi `source`; resztę bierze z env:
#
#   GH_TOKEN  token z `pull-requests: write`
#   REPO      `właściciel/repozytorium`
#   DRY_RUN   `1` — tylko wypisz, co by poszło; nic nie cofaj i nie komentuj
#
# Wołający pobiera ten plik z `main`, tak jak skrypt Discorda: decyduje on
# o cofaniu cudzych zatwierdzeń.
#
# Liczymy tak jak GitHub przy blokowaniu merge'a: dla każdej osoby (bez botów)
# ważna jest jej ostatnia recenzja, która zmienia stan — zatwierdzenie, prośba
# o zmiany albo recenzja już cofnięta. Sam komentarz (COMMENTED) po
# zatwierdzeniu go nie znosi, a prośbę o zmiany znosi tylko późniejsze
# zatwierdzenie tej samej osoby albo cofnięcie — zatwierdzenie kogoś innego
# jej nie znosi.
#
# Zanim ustawienie zostanie wyłączone, GitHub sam zdejmuje zatwierdzenia przy
# pushu — wtedy po ocenie nie stoi żadne i nic tu się nie dzieje.

APPROVALS_DOC="https://github.com/${REPO:-Metal-Pipe-Org/Metal-Planner}/blob/main/CONTRIBUTING.md#review-i-mergowanie"
APPROVAL_NOTE_MARK='<!-- claude-approval-note -->'

# pr_reviews N — wszystkie recenzje PR-a jako jedna tablica JSON.
pr_reviews() {
  gh api --paginate --slurp "repos/$REPO/pulls/$1/reviews" | jq 'add // []'
}

# standing_approvals — filtr: z tablicy recenzji wypisuje tablicę zatwierdzeń
# ludzi, które dalej obowiązują, od najstarszego: {id, login, commit_id}.
standing_approvals() {
  jq -c "$(_review_states_jq)"' | map(select(.state == "APPROVED"))
    | map({id, login: .user.login, commit_id})'
}

# _review_states_jq — wspólny początek filtrów: ostatnia recenzja zmieniająca
# stan każdej osoby, od najstarszej.
_review_states_jq() {
  cat <<'JQ'
    [ .[]
      # Recenzja konta skasowanego po fakcie ma `user: null` — bez tego
      # `endswith` wywraca cały filtr, a z nim ocenę zatwierdzeń.
      | select(.user != null)
      | select((.user.type // "User") != "Bot" and (.user.login | endswith("[bot]") | not))
      | select(.state == "APPROVED" or .state == "CHANGES_REQUESTED" or .state == "DISMISSED") ]
    | group_by(.user.login)
    | map(sort_by(.submitted_at // "", .id) | last)
    | sort_by(.submitted_at // "", .id)
JQ
}

# approval_size — filtr: z pliku oceny wyciąga ocenę zmian od zatwierdzenia,
# którą Claude zostawia ukrytą linijką `<!-- zmiany-od-zatwierdzenia: … -->`.
# Wypisuje `male`, `duze` albo nic. Liczy się ostatnia taka linijka i tylko
# w całości — ten sam napis w środku zdania (np. przy ocenie PR-a, który
# zmienia ten mechanizm) to cytat, nie ocena.
approval_size() {
  grep -E '^[[:space:]]*<!-- zmiany-od-zatwierdzenia: *(male|małe|duze|duże) *-->[[:space:]]*$' \
    | tail -n1 | sed -E 's/.*: *([^ ]+) *-->.*/\1/; s/ł/l/; s/ż/z/' || true
}

# approvals_plan KIND HEAD JUDGED SIZE VERDICT — filtr: z tablicy recenzji
# wylicza, co zrobić z zatwierdzeniami. Na wyjściu
# `{action: none|keep|dismiss, approvals: [...], reasons: [...]}`.
#
#   KIND     `review` (jest nowa ocena) albo cokolwiek innego (ocena się nie
#            udała, PR zmienia workflow oceny) — wtedy nie ma werdyktu
#   HEAD     commit, który Claude właśnie oceniał
#   JUDGED   commit zatwierdzenia, o który Claude był pytany (APPROVED_SHA);
#            pusty, gdy nie pytaliśmy
#   SIZE     odpowiedź Claude'a: `male` / `duze` / pusta
#   VERDICT  werdykt nowej oceny (pierwszy nagłówek `## `)
#
# Zmiany oceniamy od najnowszego zatwierdzenia: to ono widziało PR najpóźniej.
# Zatwierdzenie na ocenianym commicie nie ma zmian do oceny. Każdy inny commit
# niż ten, o który pytaliśmy, i brak czytelnej odpowiedzi to „duże” — przy
# niepewności zatwierdzenie raczej cofnąć, niż puścić niewidziany kod.
#
# Zatwierdzenie zostaje tylko przy drobnych zmianach, zielonym werdykcie
# i bez czekającej prośby o zmiany. W każdym innym razie cofamy wszystkie
# stojące zatwierdzenia, bo nowa ocena dotyczy całego PR-a.
approvals_plan() {
  jq -c --arg kind "$1" --arg head "$2" --arg judged "$3" --arg size "$4" --arg verdict "$5" \
    "$(_review_states_jq)"' as $last
    | ($last | map(select(.state == "APPROVED"))
        | map({id, login: .user.login, commit_id})) as $ok
    | ($last | map(select(.state == "CHANGES_REQUESTED") | .user.login)) as $neg
    | if ($ok | length) == 0 then {action: "none", approvals: [], reasons: []}
      else
        ($ok | last) as $newest
        | (if $newest.commit_id == $head then "brak"
           elif $judged != "" and $newest.commit_id == $judged
             then (if $size == "male" then "male" elif $size == "duze" then "duze" else "nieznane" end)
           else "nieznane" end) as $changes
        | [ (if $changes == "duze" then "Claude uznał zmiany od zatwierdzenia za duże" else empty end),
            (if $changes == "nieznane" then
               (if $kind == "review"
                  then "Claude nie ocenił zmian od zatwierdzenia, więc liczą się jako duże"
                  else "od zatwierdzenia PR się zmienił, a nowej oceny nie ma" end)
             else empty end),
            (if $kind == "review" and ($verdict | startswith("🟢") | not)
               then "werdykt nowej oceny to „\($verdict)”, a nie 🟢" else empty end),
            (if ($neg | length) > 0
               then "czeka prośba o zmiany od " + ($neg | map("**\(.)**") | join(", "))
               else empty end) ] as $reasons
        | {action: (if ($reasons | length) > 0 then "dismiss" else "keep" end),
           approvals: $ok, reasons: $reasons}
      end'
}

# apply_approvals N KIND URL AUTHOR PLAN — wykonuje plan z `approvals_plan`.
# URL to nowa ocena (albo informacja o porażce), do której odsyłamy. Przy
# cofaniu powód idzie w wiadomości cofnięcia — widzi ją recenzent i każdy
# w osi czasu PR-a. Gdy zatwierdzenie zostaje po nowej ocenie, autor dostaje
# osobny komentarz z tym, co może zrobić dalej: sama ocena kończy się
# zachętą do prośby o recenzję, a tu recenzja już jest.
#
# Ustawia APPROVALS_NOTE — jedną linijkę dla Discorda: cofnięcia i zostawienia
# zatwierdzenia robione naszym tokenem nie trafiają tam same. Kod różny od
# zera, gdy któregoś cofnięcia nie udało się zrobić — zatwierdzenie, które
# miało zniknąć, a stoi, pozwala zmergować niewidziany kod, więc to ma być
# widać na czerwono.
apply_approvals() {
  local n=$1 kind=$2 url=$3 author=$4 plan=$5 action who msg id rc=0

  action=$(jq -r .action <<<"$plan")
  who=$(jq -r '.approvals | map("**\(.login)**") | join(", ")' <<<"$plan")
  APPROVALS_NOTE=""

  case "$action" in
    none)
      echo "#$n: żadne zatwierdzenie człowieka nie stoi — nic do zrobienia."
      ;;
    dismiss)
      # Wiadomość cofnięcia to jedyne, co recenzent i autor zobaczą w osi
      # czasu PR-a, więc mówi też, co dalej (#393).
      msg="Zatwierdzenie cofnięte: $(jq -r '.reasons | join("; ")' <<<"$plan") (szczegóły: $url). Potrzebne ponowne review: przeczytaj ocenę, popraw, co trzeba (przez wersję roboczą), i poproś o review w panelu „Reviewers”. Jak to działa: $APPROVALS_DOC"
      for id in $(jq -r '.approvals[].id' <<<"$plan"); do
        if [ "${DRY_RUN:-}" = 1 ]; then
          echo "DRY_RUN: cofnąłbym recenzję $id: $msg"
        elif ! jq -n --arg m "$msg" '{message: $m, event: "DISMISS"}' \
            | gh api -X PUT "repos/$REPO/pulls/$n/reviews/$id/dismissals" --input - > /dev/null; then
          echo "::error::#$n: nie udało się cofnąć recenzji $id."
          rc=1
        fi
      done
      echo "#$n: cofnięte zatwierdzenia ($who): $(jq -r '.reasons | join("; ")' <<<"$plan")" \
        | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
      # Instrukcja „zatwierdzenie nadal obowiązuje” spod poprzedniej oceny
      # właśnie przestała być prawdą. Po udanej ocenie zwinął ją już krok
      # zwijania, ale po porażce i przy PR-ze, który zmienia workflow oceny,
      # tamten krok nie biegnie — a autor nie może jej posłuchać i mergować.
      # Porażka zwijania nie zmienia rozstrzygnięcia, więc tylko ostrzegamy.
      fold_approval_notes "$n" || echo "::warning::#$n: nie udało się zwinąć starej instrukcji o zatwierdzeniu."
      APPROVALS_NOTE="Zatwierdzenie cofnięte ($who) — potrzebne ponowne review."
      ;;
    keep)
      echo "#$n: zatwierdzenia zostają ($who)." | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
      # Bez nowej oceny nie ma czego doradzać — zatwierdzenie na ocenianym
      # commicie po prostu stoi dalej.
      [ "$kind" = review ] || return 0
      approval_note "$author" "$who" "$url" > "${RUNNER_TEMP:-/tmp}/approval-note.md"
      if [ "${DRY_RUN:-}" = 1 ]; then
        echo "DRY_RUN: komentarz pod #$n:"
        cat "${RUNNER_TEMP:-/tmp}/approval-note.md"
      elif ! gh pr comment "$n" --repo "$REPO" --body-file "${RUNNER_TEMP:-/tmp}/approval-note.md"; then
        # Zatwierdzenie i tak stoi słusznie, brakuje tylko podpowiedzi —
        # ostrzeżenie wystarczy.
        echo "::warning::#$n: nie udało się wstawić komentarza o zatwierdzeniu."
      fi
      APPROVALS_NOTE="Zatwierdzenie ($who) zostaje — zmiany od niego drobne, można mergować."
      ;;
  esac
  return "$rc"
}

# approval_note_nodes — filtr: z tablicy komentarzy wypisuje node_id
# instrukcji „zatwierdzenie nadal obowiązuje” (znacznik APPROVAL_NOTE_MARK).
approval_note_nodes() {
  jq -r --arg mark "$APPROVAL_NOTE_MARK" '.[]
    | select(.user.login == "github-actions[bot]" and (.body | contains($mark)))
    | .node_id'
}

# fold_approval_notes N — zwija wszystkie instrukcje o zatwierdzeniu pod PR-em
# (jak `minimize_comment` z guard.sh; ponowne zwinięcie niczego nie zmienia).
fold_approval_notes() {
  local node
  if [ "${DRY_RUN:-}" = 1 ]; then
    echo "DRY_RUN: zwinąłbym instrukcje o zatwierdzeniu pod #$1"
    return 0
  fi
  gh api --paginate --slurp "repos/$REPO/issues/$1/comments" | jq 'add // []' \
    | approval_note_nodes \
    | while IFS= read -r node; do
        gh api graphql -f id="$node" -f query='
          mutation($id: ID!) {
            minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) {
              minimizedComment { isMinimized }
            }
          }' > /dev/null
      done
}

# approval_note AUTOR KTO URL — treść komentarza, gdy zatwierdzenie zostaje.
# Cały proces opisuje CONTRIBUTING.md; tu tylko trzy wyjścia z tej sytuacji.
# Znacznik na końcu pozwala zwinąć tę instrukcję, gdy przyjdzie nowsza ocena.
approval_note() {
  cat <<MD
@$1 — zatwierdzenie od $2 nadal obowiązuje: [nowa ocena]($3) jest 🟢, a zmiany od zatwierdzenia Claude ocenił jako drobne. Co dalej — jedno z trzech:

1. **Chcesz jeszcze coś zmienić** — wróć do wersji roboczej („Convert to draft”) i dopiero tam wypchnij poprawki.
2. **Zmerguj sam** — zatwierdzenie człowieka nadal obowiązuje, bo Claude ocenił zmiany od niego jako drobne. Gdy merge jest jeszcze zablokowany, opis statusu „Recenzenci konieczni” mówi, czyjego zatwierdzenia brakuje.
3. **Wolisz, żeby ktoś jeszcze spojrzał** — poproś o ponowne review w panelu „Reviewers”.

Jak to działa: [CONTRIBUTING.md — Review i mergowanie]($APPROVALS_DOC).

$APPROVAL_NOTE_MARK
MD
}
