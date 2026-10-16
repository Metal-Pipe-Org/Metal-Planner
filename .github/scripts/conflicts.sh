# Ping autorów PR-ów, które push do `main` właśnie wpędził w konflikt.
# Wołający robi `source` (razem z discord.sh) i woła `ping_new_conflicts`;
# resztę bierze z env:
#
#   GH_TOKEN  token z `pull-requests: write` — zdejmuje prośby o recenzję
#   REPO      `właściciel/repozytorium`
#   BEFORE    `main` przed pushem (`github.event.before`)
#   AFTER     `main` po pushu (`github.sha`)
#   GIT_REPO  klon repo z historią, w którym liczymy konflikty; domyślnie `.`
#   DRY_RUN   `1` = tylko wypisz, co by poszło na Discorda i co by zdjęto;
#             do sprawdzenia na prawdziwych PR-ach tokenem tylko do odczytu
#
# Wołający pobiera ten plik z `main`, tak jak skrypt Discorda.

# conflicts_with BAZA PR — kod 0, gdy PR ma konflikt z BAZĄ, 1, gdy nie ma.
# Liczymy to samo, co GitHub przy merge'u, ale lokalnie i dla dowolnej bazy:
# `mergeable` z API mówi tylko o obecnym `main`, a my porównujemy dwa stany.
# Inny błąd niż konflikt (np. brak commita) wywraca bieg pod `set -e`, zamiast
# udawać, że konfliktu nie ma.
conflicts_with() {
  local rc=0
  git -C "${GIT_REPO:-.}" merge-tree --write-tree "$1" "$2" > /dev/null || rc=$?
  case $rc in
    0) return 1 ;;
    1) return 0 ;;
    *) echo "::error::merge-tree $1 $2 zakończył się kodem $rc." >&2; exit "$rc" ;;
  esac
}

# ping_conflict N TYTUŁ LINK AUTOR UŻYTKOWNICY ZESPOŁY — pinguje autora PR-a
# w konflikcie i zdejmuje prośby o recenzję, jeśli jakieś czekają.
ping_conflict() {
  local n=$1 title=$2 url=$3 author=$4 users=${5:-} teams=${6:-} who desc=""

  who=$(jq -rn --arg u "$users" --arg t "$teams" \
    '[($u | split(",") | map(select(. != "") | "**" + . + "**"))[],
      ($t | split(",") | map(select(. != "") | "zespół **" + . + "**"))[]] | join(", ")')
  if [ -n "$who" ]; then
    desc="Prośby o recenzję zdjęte ($who)"
  fi

  if [ "${DRY_RUN:-}" = 1 ]; then
    echo "[na sucho] #$n: nowy konflikt; wysłałbym:"
    echo "  @$author"
    echo "  Konflikt z main: #$n $title"
    [ -z "$desc" ] || echo "  $desc"
    return 0
  fi

  # Zdjęcie od zespołu może się nie udać, gdy token nie widzi zespołów
  # organizacji. Wiadomość i tak wychodzi: autor ma się dowiedzieć
  # o konflikcie, a niezdjęta prośba nikogo drugi raz nie zawoła, bo ping
  # zależy od przejścia w konflikt, nie od prośby.
  if [ -n "$who" ] && ! jq -n --arg u "$users" --arg t "$teams" \
      '{reviewers: ($u | split(",") | map(select(. != ""))),
        team_reviewers: ($t | split(",") | map(select(. != "")))}' \
      | gh api -X DELETE "repos/$REPO/pulls/$n/requested_reviewers" --input - > /dev/null; then
    echo "::warning::#$n: nie udało się zdjąć próśb o recenzję ($users $teams)."
  fi

  echo "#$n: nowy konflikt z main${who:+ — zdjęte prośby o recenzję: $users $teams}" \
    | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
  discord_send warn "" "⚔️ Konflikt z main: #$n $title" "$url" "$desc" "" "$author"
}

# ping_new_conflicts
#
# Bierzemy wszystkie otwarte PR-y do `main` poza szkicami: zatwierdzony PR
# albo PR bez recenzentów też ktoś chce zmergować, a w konflikcie GitHub nie
# puszcza jego automatów (#245), więc bez pingu nikt się nie dowie. Szkic
# autor i tak jeszcze przerabia.
#
# Ping idzie tylko przy przejściu: PR nie miał konfliktu ze starym `main`,
# a z nowym ma. Jeden konflikt to jeden ping, a nie ping po każdym kolejnym
# merge'u, i nie musimy niczego zapamiętywać między biegami. Oba stany liczy
# `merge-tree` tak samo, więc przejście nie wynika z różnicy dwóch metod,
# a bieg nie czeka, aż GitHub policzy `mergeable`. Za nowy `main` bierzemy
# commit z pushu, nie obecny czubek: inaczej dwa szybkie merge'e dałyby ten
# sam konflikt w obu biegach.
#
# Zdjęcie próśb o recenzję zostaje: recenzent nie traci czasu na PR, który
# i tak się jeszcze zmieni, a autor po rozwiązaniu konfliktu prosi od nowa.
#
# Którym merge'em konflikt powstał, nie piszemy: autorowi do rozwiązania
# konfliktu niepotrzebne.
ping_new_conflicts() {
  local prs=() refs=() line n f git=(git -C "${GIT_REPO:-.}")

  while IFS= read -r line; do
    prs+=("$line")
  done < <(gh api --paginate "repos/$REPO/pulls?state=open&base=main&per_page=100" --jq '.[]
    | select(.draft | not)
    | [(.number | tostring), .title, .html_url, .user.login,
       (.requested_reviewers | map(.login) | join(",")),
       (.requested_teams | map(.slug) | join(","))]
    | join("\u001f")')

  [ ${#prs[@]} -gt 0 ] || return 0

  # Pola dzieli znak \x1f, nie tabulator: `read` skleja kolejne tabulatory,
  # więc pusta lista osób przesunęłaby zespoły na jej miejsce.
  for line in "${prs[@]}"; do
    n=${line%%$'\x1f'*}
    refs+=("+refs/pull/$n/head:refs/remotes/pr/$n")
  done
  "${git[@]}" fetch -q --no-tags origin "$AFTER" "${refs[@]}"

  # Bez starego `main` nie da się powiedzieć, co jest nowym konfliktem: przy
  # pierwszym pushu `before` to same zera, a po nadpisaniu historii stary
  # commit bywa już nieosiągalny. Wtedy milczymy, zamiast pingować o każdy
  # konflikt od nowa — przegapiony ping to mniejsze zło niż powtórka dla
  # wszystkich, a GitHub i tak pokazuje konflikt na stronie PR-a.
  if [ -z "${BEFORE//0/}" ] \
      || ! "${git[@]}" fetch -q --no-tags origin "$BEFORE" 2> /dev/null \
      || ! "${git[@]}" cat-file -e "$BEFORE^{commit}" 2> /dev/null; then
    echo "::warning::Brak poprzedniego main ($BEFORE) — nie da się ustalić nowych konfliktów, pomijam."
    return 0
  fi

  for line in "${prs[@]}"; do
    n=${line%%$'\x1f'*}
    if conflicts_with "$AFTER" "refs/remotes/pr/$n" \
        && ! conflicts_with "$BEFORE" "refs/remotes/pr/$n"; then
      IFS=$'\x1f' read -r -a f <<<"$line"
      ping_conflict "${f[@]}"
    fi
  done
}
