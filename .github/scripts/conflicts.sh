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

# Ile razy i co ile sekund pytamy o `mergeable`. Po zmianie bazy GitHub liczy
# je w tle i do tego czasu zwraca null.
MERGEABLE_TRIES=${MERGEABLE_TRIES:-10}
MERGEABLE_WAIT=${MERGEABLE_WAIT:-6}

# pr_mergeable N — wypisuje true / false, albo nic, gdy GitHub nie zdążył.
pr_mergeable() {
  local i m
  for ((i = 1; i <= MERGEABLE_TRIES; i++)); do
    m=$(gh api "repos/$REPO/pulls/$1" --jq '.mergeable')
    if [ "$m" != null ]; then
      printf '%s' "$m"
      return 0
    fi
    sleep "$MERGEABLE_WAIT"
  done
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
  local n title url author users teams m who

  # Pętla czyta z fd 3, żeby żadne `gh` w środku nie zjadło jej wejścia.
  while IFS=$'\t' read -r n title url author users teams <&3; do
    m=$(pr_mergeable "$n")
    if [ -z "$m" ]; then
      echo "::warning::#$n: GitHub nie policzył, czy da się zmergować — pomijam."
      continue
    fi
    [ "$m" = false ] || continue

    who=$(jq -rn --arg u "$users" --arg t "$teams" \
      '[($u | split(",") | map(select(. != "") | "**" + . + "**"))[],
        ($t | split(",") | map(select(. != "") | "zespół **" + . + "**"))[]] | join(", ")')

    if [ "${DRY_RUN:-}" = 1 ]; then
      echo "[na sucho] #$n: zdjąłbym prośby o recenzję ($who) i wysłał:"
      echo "  Konflikt z main: #$n $title"
      echo "  @$author, ten PR ma konflikt z main. Prośby o recenzję zdjęte: $who."
      echo "  Rozwiąż konflikt i poproś o recenzję ponownie."
      continue
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
  done 3< <(gh api --paginate "repos/$REPO/pulls?state=open&base=main&per_page=100" --jq '.[]
    | select((.requested_reviewers | length) + (.requested_teams | length) > 0)
    | [.number, .title, .html_url, .user.login,
       (.requested_reviewers | map(.login) | join(",")),
       (.requested_teams | map(.slug) | join(","))]
    | @tsv')
}
