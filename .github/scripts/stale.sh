LABEL_STALE="stale"
DAYS_QUIET=14
DAYS_GRACE=7
# Niewidoczny znacznik, po którym rozpoznajemy własne przypomnienie wśród
# innych komentarzy bota.
MARKER="<!-- stale-ping -->"

# Decyzja o zgłoszeniu po ciszy, wspólna dla biegu dziennego
# (stale-issues.yml) i natychmiastowego zdejmowania etykiety
# (stale-unlabel.yml). Wołający robi `source`; plik bierze z `main`.
#
# stale_action ZGŁOSZENIE.json OŚ_CZASU.json ZNACZNIK ETYKIETA DNI_CISZY DNI_ŁASKI
#
# Wypisuje jedno z: ping / close / unlabel / none. Aktywność to założenie
# zgłoszenia, wpis człowieka, PR, który na nie wskazuje, PR podpięty
# ręcznie i ponowne otwarcie przez człowieka (uzasadnienie w
# stale-issues.yml).
stale_action() {
  jq -nr \
    --slurpfile i "$1" --slurpfile t "$2" \
    --arg marker "$3" --arg label "$4" \
    --argjson quiet "$5" --argjson grace "$6" '
      def ts: fromdateiso8601;
      $i[0] as $issue | $t[0] as $events |

      ([$issue.created_at | ts]
        + [$events[]
           | select(
               (.event == "commented" and .user.type != "Bot")
               or (.event == "cross-referenced" and .source.issue.pull_request != null)
               or .event == "connected"
               or (.event == "reopened" and .actor.type != "Bot"))
           | .created_at | ts]
        | max) as $active
      | ([$events[]
          | select(.event == "commented" and .user.type == "Bot" and (.body | contains($marker)))
          | .created_at | ts] | max) as $ping
      | (any($issue.labels[]; .name == $label)) as $labeled
      | if $ping != null and $ping > $active then
          (if now - $ping >= $grace * 86400 then "close" else "none" end)
        elif now - $active >= $quiet * 86400 then "ping"
        elif $ping != null and $labeled then "unlabel"
        else "none" end'
}

# remove_stale NUMER — zdejmuje etykietę po świeżym odczycie. Migawka z
# początku biegu bywa już nieaktualna (stale-unlabel.yml mógł ją zdjąć), a
# `gh issue edit --remove-label` na brakującej etykiecie kończy się błędem.
remove_stale() {
  if [ "$(gh issue view "$1" --repo "$REPO" --json labels --jq "any(.labels[]; .name == \"$LABEL_STALE\")")" = true ]; then
    gh issue edit "$1" --repo "$REPO" --remove-label "$LABEL_STALE"
  fi
}
