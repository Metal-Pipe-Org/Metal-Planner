# Ping autorów PR-ów, które push do `main` właśnie wpędził w konflikt.
# Wołający robi `source` (razem z discord.sh i drafts.sh) i woła
# `ping_new_conflicts`; resztę bierze z env:
#
#   GH_TOKEN  token z `pull-requests: write` — zdejmuje prośby o recenzję
#             i przestawia PR na wersję roboczą
#   REPO      `właściciel/repozytorium`
#   BEFORE    `main` przed pushem (`github.event.before`)
#   AFTER     `main` po pushu (`github.sha`)
#   GIT_REPO  klon repo z historią, w którym liczymy konflikty; domyślnie `.`
#   DRY_RUN   `1` = tylko wypisz, co by poszło na Discorda, co by zdjęto
#             i co by wróciło do wersji roboczej;
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

# ping_conflict N TYTUŁ LINK AUTOR SZKIC UŻYTKOWNICY ZESPOŁY — pinguje autora
# PR-a w konflikcie, zdejmuje prośby o recenzję, jeśli jakieś czekają, a gotowy
# PR przestawia na wersję roboczą.
ping_conflict() {
  local n=$1 title=$2 url=$3 author=$4 draft=$5 users=${6:-} teams=${7:-} who desc=""

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
    [ "$draft" = true ] || echo "  i przestawiłbym PR na wersję roboczą"
    return 0
  fi

  # Niezdjęta prośba nikogo drugi raz nie zawoła, bo ping zależy od przejścia
  # w konflikt, nie od prośby — więc wiadomość wychodzi i bez zdjęcia.
  drop_review_requests "$n" "$users" "$teams"
  if [ "$draft" != true ] && to_draft "$n" "$DRAFT_ON_CONFLICT"; then
    desc="PR wraca do wersji roboczej${desc:+. $desc}"
  fi

  echo "#$n: nowy konflikt z main${who:+ — zdjęte prośby o recenzję: $users $teams}" \
    | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
  discord_send warn "" "⚔️ Konflikt z main: #$n $title" "$url" "$desc" "" "$author"
}

# ping_new_conflicts
#
# Bierzemy wszystkie otwarte PR-y do `main`: zatwierdzony PR albo PR bez
# recenzentów też ktoś chce zmergować, a w konflikcie GitHub nie puszcza jego
# automatów (#245), więc bez pingu nikt się nie dowie. Szkice też, bo wersja
# robocza to zwykły etap PR-a (#349): poprawki po ocenie czy recenzji, które
# przed powrotem do gotowych i tak muszą się zmieścić na `main`.
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
    | [(.number | tostring), .title, .html_url, .user.login, (.draft | tostring),
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
