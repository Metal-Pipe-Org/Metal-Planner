# Wysyłka na kanał Discorda, wspólna dla wszystkich workflow. Wołający robi
# `source` i używa `discord_send`; webhook i mapę kont bierze ze swojego env:
#
#   DISCORD_WEBHOOK  sekret z URL-em webhooka; bez niego nic nie wychodzi
#   DISCORD_IDS      zmienna repo: JSON z loginów GitHuba na ID z Discorda
#
# Każde `@login` z mapy — w treści od nas i w cytowanym fragmencie od ludzi —
# zamienia się w prawdziwy ping. Dlatego wołający pisze `@login`, gdy chce
# kogoś zawołać, a `**login**`, gdy tylko go wymienia. Loginy spoza mapy
# zostają zwykłym tekstem.
#
# Wołający zawsze pobiera ten plik z `main`, nigdy z drzewa PR-a ani pracy
# Claude'a: skrypt dostaje w env sekret webhooka.

# discord_send TREŚĆ [FRAGMENT] [LINK]
#   TREŚĆ     jedna linijka od nas: co się stało i kogo to dotyczy
#   FRAGMENT  tekst od ludzi (opis, komentarz) — skracany i wstawiany jako cytat
#   LINK      w <>, żeby Discord nie rozwijał podglądu i wiadomość była krótka
discord_send() {
  if [ -z "${DISCORD_WEBHOOK:-}" ]; then
    echo "Brak sekretu DISCORD_WEBHOOK — pomijam Discorda."
    return 0
  fi

  local payload
  # Fragment skracamy przed zamianą wzmianek, żeby cięcie nie przecięło
  # `<@ID>` w pół. Komentarze HTML (znaczniki botów) wycinamy, bo na Discordzie
  # wyszłyby jako tekst.
  #
  # `parse: []` wyłącza wszystkie wzmianki z treści, także @everyone i role
  # wpisane przez kogokolwiek w komentarzu; `users` wpuszcza z powrotem tylko
  # osoby z mapy, które naprawdę padły w wiadomości.
  #
  # Zepsuty JSON w DISCORD_IDS wywraca samo `jq`; pod `set -e` u wołającego
  # przerwałoby to cały krok, np. pętlę zamykającą zgłoszenia, stąd `if !`.
  if ! payload=$(jq -n --arg text "$1" --arg excerpt "${2:-}" --arg url "${3:-}" \
      --argjson ids "${DISCORD_IDS:-"{}"}" --argjson max 300 '
    ($excerpt
      | gsub("<!--[\\s\\S]*?-->"; "")
      | gsub("\r"; "")
      | gsub("\n{3,}"; "\n\n")
      | gsub("^\\s+|\\s+$"; "")) as $e
    | (if ($e | length) > $max then $e[:$max] + "…" else $e end) as $e
    | ([$text]
        + (if $e != "" then [$e | split("\n") | map("> " + .) | join("\n")] else [] end)
        + (if $url != "" then ["<" + $url + ">"] else [] end)
      | join("\n")) as $msg
    | reduce ($ids | to_entries[]) as $p ({content: $msg, users: []};
        ("(?<![A-Za-z0-9-])@" + $p.key + "(?![A-Za-z0-9-])") as $re
        | if (.content | test($re; "i"))
          then .content |= gsub($re; "<@" + $p.value + ">"; "i") | .users += [$p.value]
          else . end)
    | {content, allowed_mentions: {parse: [], users}}'); then
    echo "::warning::Nie udało się złożyć wiadomości na Discorda (zły DISCORD_IDS?): $1"
    return 0
  fi

  # Discord to dodatek: jego awaria ma być widać w logu, ale nie może
  # przerwać tego, co workflow robi naprawdę (zamykanie, ocena, przypomnienia).
  if printf '%s' "$payload" \
      | curl -fsS -H 'Content-Type: application/json' --data-binary @- "$DISCORD_WEBHOOK"; then
    echo "Discord: $(jq -r .content <<<"$payload")"
  else
    echo "::warning::Nie udało się wysłać na Discorda: $1"
  fi
}
