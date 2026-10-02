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
