# Wstrzymanie recenzji PR-ów, które po zmianie na `main` mają z nim konflikt.
# Wołający robi `source` (razem z discord.sh) i woła `hold_conflicting_reviews`;
# resztę bierze z env:
#
#   GH_TOKEN  token z `pull-requests: write` — zdejmuje prośby o recenzję
#   REPO      `właściciel/repozytorium`
#   DRY_RUN   `1` = tylko wypisz, co by poszło na Discorda i co by zdjęto;
#             do sprawdzenia na prawdziwych PR-ach tokenem tylko do odczytu
#
# Wołający pobiera ten plik z `main`, tak jak skrypt Discorda.

# Ile sekund w sumie, na cały bieg, czekamy na `mergeable` i co ile pytamy
# ponownie. Po zmianie bazy GitHub liczy je w tle i do tego czasu zwraca null.
# Limit jest wspólny, a nie na PR: kilka niepoliczonych PR-ów po kolei
# dobiłoby do `timeout-minutes` i przerwało listę w połowie.
MERGEABLE_DEADLINE=${MERGEABLE_DEADLINE:-60}
MERGEABLE_WAIT=${MERGEABLE_WAIT:-6}

# hold_reviews N TYTUŁ LINK AUTOR UŻYTKOWNICY ZESPOŁY — zdejmuje prośby
# o recenzję PR-a w konflikcie i pinguje autora.
hold_reviews() {
  local n=$1 title=$2 url=$3 author=$4 users=$5 teams=${6:-} who

  who=$(jq -rn --arg u "$users" --arg t "$teams" \
    '[($u | split(",") | map(select(. != "") | "**" + . + "**"))[],
      ($t | split(",") | map(select(. != "") | "zespół **" + . + "**"))[]] | join(", ")')

  if [ "${DRY_RUN:-}" = 1 ]; then
    echo "[na sucho] #$n: zdjąłbym prośby o recenzję ($who) i wysłał:"
    echo "  Konflikt z main: #$n $title"
    echo "  @$author, ten PR ma konflikt z main. Prośby o recenzję zdjęte: $who."
    echo "  Rozwiąż konflikt i poproś o recenzję ponownie."
    return 0
  fi

  # Zdjęcie od zespołu może się nie udać, gdy token nie widzi zespołów
  # organizacji. Wiadomość i tak wychodzi: autor ma się dowiedzieć
  # o konflikcie, a niezdjęta prośba najwyżej da ping przy kolejnym pushu.
  if ! jq -n --arg u "$users" --arg t "$teams" \
      '{reviewers: ($u | split(",") | map(select(. != ""))),
        team_reviewers: ($t | split(",") | map(select(. != "")))}' \
      | gh api -X DELETE "repos/$REPO/pulls/$n/requested_reviewers" --input - > /dev/null; then
    echo "::warning::#$n: nie udało się zdjąć próśb o recenzję ($users $teams)."
  fi

  echo "#$n: konflikt z main — zdjęte prośby o recenzję: $users $teams" \
    | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
  discord_send warn "" "Konflikt z main: #$n $title" "$url" \
    "@$author, ten PR ma konflikt z main. Prośby o recenzję zdjęte: $who.
Rozwiąż konflikt i poproś o recenzję ponownie."
}

# hold_conflicting_reviews
#
# Którym merge'em konflikt powstał, nie piszemy: tego nie da się ustalić
# pewnie, a autorowi do rozwiązania konfliktu i tak niepotrzebne.
#
# Bierzemy tylko PR-y z prośbą o recenzję: bez niej nikt na nic nie czeka,
# a autor konflikt i tak zobaczy przy następnym pushu. Zdjęcie próśb samo
# blokuje ponowny ping przy kolejnych pushach do `main`, więc niczego nie
# zapamiętujemy. Recenzentów po rozwiązaniu konfliktu autor wybiera od nowa.
hold_conflicting_reviews() {
  local pending=() left line n m f end=$((SECONDS + MERGEABLE_DEADLINE))

  while IFS= read -r line; do
    pending+=("$line")
  done < <(gh api --paginate "repos/$REPO/pulls?state=open&base=main&per_page=100" --jq '.[]
    | select((.requested_reviewers | length) + (.requested_teams | length) > 0)
    | [(.number | tostring), .title, .html_url, .user.login,
       (.requested_reviewers | map(.login) | join(",")),
       (.requested_teams | map(.slug) | join(","))]
    | join("\u001f")')

  # Pola dzieli znak \x1f, nie tabulator: `read` skleja kolejne tabulatory,
  # więc pusta lista osób przesunęłaby zespoły na jej miejsce.
  #
  # Rundy po wszystkich jeszcze niepoliczonych PR-ach. Po ostatniej rundzie
  # nie czekamy.
  while [ ${#pending[@]} -gt 0 ]; do
    left=()
    for line in "${pending[@]}"; do
      n=${line%%$'\x1f'*}
      m=$(gh api "repos/$REPO/pulls/$n" --jq '.mergeable')
      case "$m" in
        null)
          left+=("$line")
          ;;
        false)
          IFS=$'\x1f' read -r -a f <<<"$line"
          hold_reviews "${f[@]}"
          ;;
      esac
    done
    pending=(${left[@]+"${left[@]}"})

    [ ${#pending[@]} -gt 0 ] || break
    if [ "$SECONDS" -ge "$end" ]; then
      for line in "${pending[@]}"; do
        echo "::warning::#${line%%$'\x1f'*}: GitHub nie policzył, czy da się zmergować — pomijam."
      done
      break
    fi
    sleep "$MERGEABLE_WAIT"
  done
}
