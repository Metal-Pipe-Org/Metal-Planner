# Wysyłka na kanał Discorda, wspólna dla wszystkich workflow. Wołający robi
# `source` i używa `discord_send`; webhook i mapę kont bierze ze swojego env:
#
#   DISCORD_WEBHOOK  sekret z URL-em webhooka; bez niego nic nie wychodzi
#   DISCORD_IDS      zmienna repo: JSON z loginów GitHuba na ID z Discorda
#
# Wiadomość to karta w stylu wbudowanej integracji GitHuba: kolorowy pasek,
# kto to zrobił (z awatarem), tytuł-link i opis. Na pierwszy rzut oka widać,
# że to automat, a kolor mówi, co się stało, zanim ktoś przeczyta tekst.
#
# Każde `@login` z mapy — w opisie od nas i w cytowanym fragmencie od ludzi —
# zamienia się w prawdziwy ping. Dlatego wołający pisze `@login`, gdy chce
# kogoś zawołać, a `**login**`, gdy tylko go wymienia. Loginy spoza mapy
# zostają zwykłym tekstem.
#
# Wołający zawsze pobiera ten plik z `main`, nigdy z drzewa PR-a ani pracy
# Claude'a: skrypt dostaje w env sekret webhooka.

# discord_send KOLOR AKTOR TYTUŁ LINK [OPIS] [FRAGMENT] [PINGI]
#   KOLOR     open / merged / closed / grey / info / comment / warn
#   AKTOR     login GitHuba, który to zrobił — wiadomość idzie pod jego nazwą
#             i awatarem; pusty, gdy zrobił to automat (wtedy nazwa webhooka)
#   TYTUŁ     np. „Nowe zgłoszenie #12 Tytuł” — link do LINK
#   OPIS      linijka od nas z tym, czego nie ma w tytule; tu padają pingi
#   FRAGMENT  tekst od ludzi (opis, komentarz) — wstawiany pod opisem w całości
#             albo wcale
#   PINGI     loginy po spacji, które wołamy nad kartą bez wymieniania ich
#             w treści — gdy z tytułu i tak wiadomo, czemu ktoś dostał ping
discord_send() {
  if [ -z "${DISCORD_WEBHOOK:-}" ]; then
    echo "Brak sekretu DISCORD_WEBHOOK — pomijam Discorda."
    return 0
  fi

  local payload
  # Komentarze HTML (znaczniki botów) wycinamy, bo na Discordzie wyszłyby
  # jako surowy tekst. Obrazki też by tak wyszły, ale zostawiają ślad
  # „[Image]”, żeby było wiadomo, że jest co obejrzeć na GitHubie; kilka pod
  # rząd składa się w jedno „[Image] ×N”, żeby galeria zrzutów nie zajęła
  # całej karty.
  #
  # Za długi fragment wypada w całości: urwany w połowie i tak nic nie mówi,
  # a tytuł z linkiem prowadzi do pełnej treści. Pingi z niego zostają, bo
  # ktoś, kogo zawołano w długim komentarzu, dalej ma się o tym dowiedzieć.
  #
  # Wiadomość idzie „jako” aktor: jego awatar i login stoją wtedy obok
  # wiadomości, a niebieski tytuł w karcie zaraz pod nimi, więc nawet krótkie
  # zdarzenie zajmuje jedną linijkę karty. Kilka zdarzeń jednej osoby pod
  # rząd Discord skleja w jeden blok. Linijka autora w karcie byłaby tylko
  # powtórzeniem.
  #
  # Wzmianka w karcie wyświetla się jak wzmianka, ale nikogo nie budzi —
  # powiadamia tylko zwykła treść wiadomości. Dlatego pingi idą osobno nad
  # kartą, a w opisie zostają jako podświetlone imiona.
  #
  # `parse: []` wyłącza wszystkie wzmianki z treści, także @everyone i role
  # wpisane przez kogokolwiek w komentarzu; `users` wpuszcza z powrotem tylko
  # osoby z mapy, które naprawdę padły w wiadomości.
  #
  # Tytuł niesie tytuły od ludzi, a karta z tytułem ponad 256 znaków jest
  # przez Discorda odrzucana w całości — stąd przycięcie.
  #
  # Zepsuty JSON w DISCORD_IDS wywraca samo `jq`; pod `set -e` u wołającego
  # przerwałoby to cały krok, np. pętlę zamykającą zgłoszenia, stąd `if !`.
  if ! payload=$(jq -n --arg color "$1" --arg actor "$2" --arg title "$3" --arg url "$4" \
      --arg text "${5:-}" --arg excerpt "${6:-}" --arg pings "${7:-}" \
      --argjson ids "${DISCORD_IDS:-"{}"}" --argjson max 500 '
    {open: 2991182, merged: 8540383, closed: 13574702, grey: 7239553,
     info: 616922, comment: 14774052, warn: 12551936} as $colors
    | ($excerpt
      | gsub("<!--[\\s\\S]*?-->"; "")
      | gsub("<img\\b[^>]*>"; "\u0001"; "i")
      | gsub("!\\[[^\\]]*\\]\\([^)]*\\)"; "\u0001")
      | gsub("(?<run>\u0001(?:\\s*\u0001)*)";
          (.run | [scan("\u0001")] | length) as $n
          | if $n > 1 then "[Image] ×\($n)" else "[Image]" end)
      | gsub("\r"; "")
      | gsub("[ \t]+\n"; "\n")
      | gsub("\n{3,}"; "\n\n")
      | gsub("^\\s+|\\s+$"; "")) as $e
    | (if ($e | length) > $max then "_(długi tekst — całość na GitHubie)_" else $e end) as $shown
    | ([$text, $shown] | map(select(. != "")) | join("\n")) as $desc
    | (if ($title | length) > 250 then $title[:250] + "…" else $title end) as $title
    | ([$desc, $e, ($pings | split(" ") | map("@" + .) | join(" "))] | join("\n")) as $scan
    | reduce ($ids | to_entries[]) as $p ({desc: $desc, users: []};
        ("(?<![A-Za-z0-9-])@" + $p.key + "(?![A-Za-z0-9-])") as $re
        | if ($scan | test($re; "i"))
          then .desc |= gsub($re; "<@" + $p.value + ">"; "i") | .users += [$p.value]
          else . end)
    | {content: (.users | map("<@" + . + ">") | join(" ")),
       allowed_mentions: {parse: [], users: .users},
       embeds: [{color: ($colors[$color] // $colors.grey), title: $title, url: $url}
         + (if .desc != "" then {description: .desc} else {} end)]}
    + (if $actor != "" then {username: $actor,
        avatar_url: ("https://github.com/" + $actor + ".png")} else {} end)'); then
    echo "::warning::Nie udało się złożyć wiadomości na Discorda (zły DISCORD_IDS?): $3"
    return 0
  fi

  # Discord to dodatek: jego awaria ma być widać w logu, ale nie może
  # przerwać tego, co workflow robi naprawdę (zamykanie, ocena, przypomnienia).
  if printf '%s' "$payload" \
      | curl -fsS -H 'Content-Type: application/json' --data-binary @- "$DISCORD_WEBHOOK"; then
    echo "Discord: $3"
  else
    echo "::warning::Nie udało się wysłać na Discorda: $3"
  fi
}

# discord_link TEKST URL — link w markdownie do OPISU. Tekst bywa od ludzi
# (tytuł PR-a), a nawias kwadratowy w nim zamknąłby link w pół i Discord
# pokazałby resztę surowo. Backslash escapujemy pierwszy, bo inaczej
# zjadłby znak po sobie.
discord_link() {
  local text=${1//\\/\\\\}
  text=${text//\[/\\[}
  text=${text//\]/\\]}
  printf '[%s](%s)' "$text" "$2"
}
