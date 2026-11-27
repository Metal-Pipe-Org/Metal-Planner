# Karta tygodniowych wskaźników bałaganu (#532) na Discorda. Wołający
# (`wskazniki-balaganu.yml`) robi `source` i używa funkcji niżej; historię
# trzyma github-action-benchmark w `data.js` na gałęzi `gh-pages`, więc
# porównanie z poprzednim pomiarem czytamy stamtąd, a nie liczymy drugi raz.
#
# Na karcie są tylko liczby, które da się ogarnąć wzrokiem: testy, martwy
# kod i największy plik. Wszystkie wykresy (także każdego z dziesięciu
# największych plików) są na stronie, do której prowadzi tytuł.
#
# Lokalnie: DRY_RUN=1 nic nie wysyła, tylko wypisuje kartę.
#   DRY_RUN=1 bash -c 'source .github/scripts/wskazniki.sh; wskazniki_karta data.js "Wskaźniki bałaganu" 150% o/r'

source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

# wskazniki_strona REPO — adres strony z wykresami (GitHub Pages z gałęzi
# `gh-pages`; katalog jak `benchmark-data-dir-path` w workflow).
wskazniki_strona() {
  echo "https://$(tr '[:upper:]' '[:lower:]' <<<"${1%%/*}").github.io/${1#*/}/${WSKAZNIKI_KATALOG}/"
}

# wskazniki_porownanie DATA_JS NAZWA PROG — JSON {alarm, linie, bez_bazy}:
# ostatni pomiar względem poprzedniego, linia na wskaźnik („czerwone 33 → 28
# ▼”). Alarm to ten sam próg co `alert-threshold` akcji: wartość większa niż
# PROG razy poprzednia (każdy wskaźnik jest „im mniej, tym lepiej”), a wzrost
# od zera zawsze.
wskazniki_porownanie() {
  sed '1s/^window\.BENCHMARK_DATA = //' "$1" | jq --arg name "$2" \
      --argjson prog "$(awk -v p="${3%\%}" 'BEGIN { print p / 100 }')" '
    def liczba: if . == (. | floor) then tostring else (. * 10 | round / 10 | tostring) end;
    def etykieta: if . == "Testy: czas" then "czas testów"
               elif . == "Martwy kod (heurystyka)" then "martwy kod"
               elif startswith("Linie: ") then "największy plik (\(.[7:]))"
               else sub("^Testy: "; "") end;
    def jednostka: if . == "s" then " s" else "" end;
    .entries[$name] as $s
    | $s[-1] as $cur
    | (if ($s | length) > 1 then $s[-2] else null end) as $prev
    | ([$cur.benches[] | select(.name | startswith("Testy:") or startswith("Martwy"))]
       + [first($cur.benches[] | select(.name | startswith("Linie:")))]) as $shown
    | [$shown[]
       | . as $b
       | ($prev // {benches: []} | .benches | map(select(.name == $b.name)) | first) as $p
       | {name, label: (.name | etykieta), now: .value, unit: (.unit | jednostka), was: ($p.value // null)}
       | .alarm = (.was != null and ((.was == 0 and .now > 0) or (.was > 0 and .now / .was > $prog)))]
    | {alarm: any(.[]; .alarm),
       bez_bazy: any($cur.benches[]; (.extra // "") | test("baza rozkładu: brak")),
       linie: (map(if .was == null then "\(.label) \(.now | liczba)\(.unit)"
                   else "\(.label) \(.was | liczba)\(.unit) → \(.now | liczba)\(.unit) "
                        + (if .now > .was then "▲" elif .now < .was then "▼" else "=" end)
                        + (if .alarm then " ⚠️" else "" end) end)
               | join("\n"))}'
}

# wskazniki_karta DATA_JS NAZWA PROG REPO — wysyła kartę przez discord.sh.
# Bez pingów: nikt nie ma tu ruchu, a alarm widać po kolorze i ⚠️.
wskazniki_karta() {
  local cmp text color=info
  cmp=$(wskazniki_porownanie "$1" "$2" "$3")
  text=$(jq -r '.linie' <<<"$cmp")
  if [ "$(jq '.alarm' <<<"$cmp")" = true ]; then
    color=warn
    text="⚠️ Wyraźnie gorzej niż w poprzednim pomiarze (ponad $3 poprzedniej wartości)."$'\n'"$text"
  fi
  if [ "$(jq '.bez_bazy' <<<"$cmp")" = true ]; then
    text="$text"$'\n'"_Testy bez bazy rozkładu, jak na świeżym klonie: część pada albo jest pominięta._"
  fi
  local title="📈 Wskaźniki bałaganu, tydzień $(TZ=Europe/Warsaw date +%V)"
  if [ -n "${DRY_RUN:-}" ]; then
    printf '%s [%s]\n%s\n%s\n' "$title" "$color" "$(wskazniki_strona "$4")" "$text"
    return 0
  fi
  source "$(dirname "${BASH_SOURCE[0]}")/discord.sh"
  discord_send "$color" "" "$title" "$(wskazniki_strona "$4")" "$text"
}
