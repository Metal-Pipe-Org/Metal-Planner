# Sprawdzenia wspólne dla workflow, które reagują na @claude albo na autora
# spoza zespołu. Wołający robi `source`; wszystko, co potrzebne, bierze z env:
#
#   GH_TOKEN  token do `gh api`
#   REPO      `właściciel/repozytorium`
#
# Wołający pobiera ten plik z `main`, nigdy z drzewa PR-a ani pracy Claude'a:
# decyduje on, kto może odpalić runnera z sekretami.

# caller_permission LOGIN — wypisuje uprawnienie osoby w repo (admin /
# maintain / write / triage / read / none). Nie używamy `author_association`:
# przy prywatnym członkostwie w organizacji GitHub zwraca tam CONTRIBUTOR albo
# NONE nawet dla osoby z prawem zapisu. Błąd API ma wywrócić wołającego na
# czerwono, a nie udawać „brak uprawnień" — inaczej zepsute sprawdzanie po
# cichu wyłączyłoby wołanie @claude dla wszystkich. Endpointowi wystarcza
# `metadata: read`, które token ma zawsze.
caller_permission() {
  gh api "repos/$REPO/collaborators/$1/permission" --jq .permission
}

# is_team_permission UPRAWNIENIE — czy to prawo zapisu albo wyższe.
is_team_permission() {
  case "$1" in
    admin|maintain|write) return 0 ;;
    *) return 1 ;;
  esac
}

# gate_claude_call TREŚĆ LOGIN — rozstrzyga, czy wołanie @claude ma ruszyć
# pracę, i zapisuje `go=true|false` do wyjścia kroku. Liczy się tylko @claude
# poza liniami cytatu: odpowiedź cytująca poprzedni wpis Claude'a przepisuje
# razem z nim każde @claude, które w nim padło. Pracować dla kogoś może tylko
# osoba z prawem zapisu, bo robota idzie na sekretach.
gate_claude_call() {
  local body=$1 who=$2 perm

  if ! printf '%s\n' "$body" | grep -v '^[[:space:]]*>' | grep -q '@claude'; then
    echo "go=false" >> "$GITHUB_OUTPUT"
    echo "Wszystkie @claude w tym wpisie są w cytacie — pomijam." \
      | tee -a "$GITHUB_STEP_SUMMARY"
    return 0
  fi

  perm=$(caller_permission "$who")
  if is_team_permission "$perm"; then
    echo "go=true" >> "$GITHUB_OUTPUT"
  else
    echo "go=false" >> "$GITHUB_OUTPUT"
    echo "@$who nie ma prawa zapisu (uprawnienia: $perm) — pomijam." \
      | tee -a "$GITHUB_STEP_SUMMARY"
  fi
}

# minimize_comment NODE_ID — zwija komentarz (odwracalnie, nic nie kasuje).
minimize_comment() {
  gh api graphql -f id="$1" -f query='
    mutation($id: ID!) {
      minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) {
        minimizedComment { isMinimized }
      }
    }' >/dev/null
}

# Ocena Claude'a i informacja o nieudanej ocenie niosą na końcu ten znacznik.
# Starsze, sprzed znacznika, rozpoznaje stopka pod oceną albo nagłówek
# informacji o porażce.
REVIEW_MARK='<!-- claude-review -->'
FOLDED_MARK='<!-- claude-review-folded -->'

# fold_review_comments KEEP_ID — filtr: na wejściu tablica komentarzy z API,
# na wyjściu po jednej linii `{id, body}` dla każdej starszej oceny do zwinięcia,
# z gotową nową treścią. Pierwsza linijka (werdykt) zostaje na wierzchu, reszta
# idzie pod „rozwiń”. Zwinięte niosą drugi znacznik i drugi raz się ich nie owija.
fold_review_comments() {
  jq -c --argjson keep "$1" --arg mark "$REVIEW_MARK" --arg folded "$FOLDED_MARK" '
    .[]
    | select(.user.login == "github-actions[bot]" and .id != $keep)
    | select(.body | contains($folded) | not)
    | select(.body | contains($mark)
        or contains("— ocena gotowa. Gdy uznasz")
        or test("^(⚪|⏳) \\*\\*Ocena się nie udała"))
    | (.body | split("\n")) as $lines
    | {id, body: ($lines[0] + "\n\n<details><summary>Starsza ocena — rozwiń</summary>\n\n"
        + ($lines[1:] | join("\n")) + "\n\n</details>\n\n" + $folded)}'
}

# fold_old_reviews PR NOWY_ID — zwija pod PR-em starsze oceny Claude'a
# i informacje o nieudanych ocenach, gdy stoi już nowa ocena. Edytujemy treść
# zamiast `minimize_comment`, bo zwinięcie przez GitHuba chowa także werdykt,
# a po starych werdyktach widać, jak PR szedł. Edycja nikogo nie powiadamia
# (wzmianka w treści była już wcześniej) i nie odpala żadnego workflow: żaden
# nie słucha `edited`, a zdarzeń z `GITHUB_TOKEN` GitHub innym workflow nie daje.
#
# Informację „Ten PR zmienia workflow oceny” zwijamy za to w całości przez
# `minimize_comment`: to jedna linijka bez werdyktu, więc nie ma czego zostawić
# na wierzchu, a ocena, na którą czekała, właśnie przyszła. Zwinięty komentarz
# API dalej zwraca, więc dalej działa jako znak „ocena już była” dla pushy.
# Ponowne zwinięcie zwiniętego niczego nie zmienia.
fold_old_reviews() {
  local comments line node
  comments=$(gh api --paginate --slurp "repos/$REPO/issues/$1/comments" | jq 'add // []')
  fold_review_comments "$2" <<<"$comments" \
    | while IFS= read -r line; do
        jq -c '{body}' <<<"$line" \
          | gh api -X PATCH "repos/$REPO/issues/comments/$(jq -r .id <<<"$line")" \
              --input - >/dev/null
      done
  workflow_notice_nodes <<<"$comments" \
    | while IFS= read -r node; do
        minimize_comment "$node"
      done
}

# workflow_notice_nodes — filtr: z tablicy komentarzy wypisuje node_id
# informacji botów o tym, że PR zmienia workflow oceny.
workflow_notice_nodes() {
  jq -r '.[]
    | select(.user.login == "github-actions[bot]")
    | select(.body | startswith("⚪ Ten PR zmienia workflow oceny"))
    | .node_id'
}
