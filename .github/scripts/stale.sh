# Progi ciszy (DAYS_QUIET, DAYS_GRACE), etykieta LABEL_STALE i etykiety stanu
# PR-a (LABEL_PR_AUTHOR, LABEL_PR_HUMAN) są w config.sh.
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"
# Niewidoczny znacznik, po którym rozpoznajemy własne przypomnienie wśród
# innych komentarzy bota.
MARKER="<!-- stale-ping -->"

# Stan PR-a z etykiety stanu (pr-state.yml): stale z komentarzem, etykietą
# i zamknięciem dotyczy tylko PR-ów, w których ruch ma autor. PR czekający na
# recenzję człowieka dostaje same przypomnienia na Discordzie — to recenzent
# zwleka, więc nie ma powodu straszyć autora zamknięciem. Uzasadnienie
# w stale-prs.yml. Etykiety: LABEL_PR_AUTHOR i LABEL_PR_HUMAN z config.sh.

# Ostatnia aktywność (sekundy od epoki) — wspólna definicja dla
# `stale_action` i `quiet_days`. Aktywność to założenie, wpis człowieka, PR,
# który wskazuje, PR podpięty ręcznie i ponowne otwarcie przez człowieka
# (uzasadnienie w stale-issues.yml). W PR-ze dochodzi to, co znaczy, że PR
# żyje: recenzja i komentarz w kodzie człowieka, commit (data commitera — oś
# czasu nie zapisuje samego pushu), force push, „Ready for review” i prośba
# o recenzję od człowieka. W zgłoszeniach tych zdarzeń nie ma, więc dla nich
# nic się nie zmienia.
STALE_JQ='
  def ts: fromdateiso8601;
  def last_active($issue; $events):
    [$issue.created_at | ts]
    + [$events[]
       | select(
           (.event == "commented" and .user.type != "Bot")
           or (.event == "cross-referenced" and .source.issue.pull_request != null)
           or .event == "connected"
           or (.event == "reopened" and .actor.type != "Bot")
           or (.event == "reviewed" and .user.type != "Bot")
           or .event == "committed"
           or ((.event == "head_ref_force_pushed"
                or .event == "ready_for_review"
                or .event == "review_requested") and .actor.type != "Bot"))
       | (.created_at // .submitted_at // .committer.date)
       # Niewysłana recenzja nie ma daty — i nie jest aktywnością.
       | select(. != null) | ts]
    + [$events[]
       | select(.event == "line-commented") | .comments[]?
       | select(.user.type != "Bot") | .created_at | ts]
    | max;
'

# Decyzja o zgłoszeniu albo PR-ze po ciszy, wspólna dla biegów dziennych
# (stale-issues.yml, stale-prs.yml) i natychmiastowego zdejmowania etykiety
# (stale-unlabel.yml). Wołający robi `source`; plik bierze z `main`.
#
# stale_action ZGŁOSZENIE.json OŚ_CZASU.json ZNACZNIK ETYKIETA DNI_CISZY DNI_ŁASKI
#
# ZGŁOSZENIE.json to odpowiedź API zgłoszeń albo PR-ów — potrzebne są tylko
# `created_at` i `labels`, a oba API je mają. Oś czasu jest wspólna
# (`issues/N/timeline`). Wypisuje jedno z: ping / close / unlabel / none.
stale_action() {
  jq -nr \
    --slurpfile i "$1" --slurpfile t "$2" \
    --arg marker "$3" --arg label "$4" \
    --argjson quiet "$5" --argjson grace "$6" "$STALE_JQ"'
      $i[0] as $issue | $t[0] as $events |
      last_active($issue; $events) as $active
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

# quiet_days ZGŁOSZENIE.json OŚ_CZASU.json — pełne dni od ostatniej
# aktywności. Na nim stale-prs.yml odmierza przypomnienia dla recenzentów.
quiet_days() {
  jq -nr --slurpfile i "$1" --slurpfile t "$2" "$STALE_JQ"'
    ((now - last_active($i[0]; $t[0])) / 86400 | floor)'
}

# remove_stale NUMER — zdejmuje etykietę po świeżym odczycie. Migawka z
# początku biegu bywa już nieaktualna (stale-unlabel.yml mógł ją zdjąć), a
# zdjęcie brakującej etykiety kończy się błędem. Idzie przez API zgłoszeń,
# bo to ono trzyma etykiety i zgłoszeń, i PR-ów — jedna droga dla obu.
remove_stale() {
  if [ "$(gh api "repos/$REPO/issues/$1/labels" --jq "any(.[]; .name == \"$LABEL_STALE\")")" = true ]; then
    gh api -X DELETE "repos/$REPO/issues/$1/labels/$LABEL_STALE" --silent
  fi
}
