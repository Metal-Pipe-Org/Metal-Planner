# Rozpoznawanie, które zgłoszenia wskazuje PR. Jedno miejsce dla
# issue-labels.yml (etykiety stanu i zamykanie po merge'u) i pr-syntax.yml
# (sprawdzenie, czy PR w ogóle wskazuje zgłoszenie): gdyby każdy miał własny
# regexp, PR mógłby przejść sprawdzenie zapisem, którego etykiety nie widzą —
# albo odwrotnie. Co znaczy która ścieżka i czemu akurat te słowa, opisuje
# nagłówek issue-labels.yml.
#
# Funkcje są czyste: dostają tekst w argumentach, niczego nie pytają API
# i nie sprawdzają, czy numer to istniejące zgłoszenie — to robi wołający,
# bo każdy potrzebuje innej odpowiedzi (etykiety po cichu pomijają zły numer,
# sprawdzenie składni mówi o nim autorowi). Wołający robi `source`; tekst od
# ludzi podaje z env, nigdy przez `${{ }}` wklejone w bash.
#
# Wołający pobiera ten plik z `main`, tak jak skrypt Discorda. Słowa
# i przedrostek gałęzi solvera są w config.sh.

source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

# _refs_words_re SŁOWA — lista z config.sh (`a|b c`) jako alternatywa dla
# `grep -E`: spacja w słowie łapie dowolny odstęp.
_refs_words_re() {
  printf '%s' "$1" | sed -E 's/[[:space:]]+/[[:space:]]+/g'
}

# refs_words_md SŁOWA — ta sama lista do komentarza: `a`, `b c`.
refs_words_md() {
  printf '%s' "$1" | sed -E 's/^/`/; s/$/`/; s/\|/`, `/g'
}

# keyword_refs TYTUŁ OPIS GAŁĄŹ — ścieżka A: numery po `Closes` / `Fixes` /
# `Resolves` (z odmianami, bez względu na wielkość liter) w tytule i opisie
# oraz numer z nazwy gałęzi solvera `claude/issue-<numer>-…`. Po jednym
# w linii, bez powtórzeń.
keyword_refs() {
  {
    printf '%s\n%s\n' "$1" "$2" \
      | grep -oiE "(^|[^[:alnum:]])($(_refs_words_re "$REFS_CLOSE_WORDS"))[[:space:]:]*#[0-9]+" \
      | grep -oE '[0-9]+' || true
    printf '%s\n' "$3" \
      | grep -oE "^${CLAUDE_BRANCH_PREFIX}[0-9]+" \
      | grep -oE '[0-9]+' || true
  } | sort -u
}

# mention_refs TYTUŁ OPIS — ścieżka B: numery po słowie „dotyczy" (`Refs`,
# `Ref`, `Related to`, `Relates to`, `Part of`, `Dotyczy`) w tytule i opisie.
# Po jednym w linii, bez powtórzeń. Numerów ze ścieżki A ani numeru samego
# PR-a nie odejmujemy — to decyzja wołającego.
mention_refs() {
  printf '%s\n%s\n' "$1" "$2" \
    | grep -oiE "(^|[^[:alnum:]])($(_refs_words_re "$REFS_MENTION_WORDS"))[[:space:]:]*#[0-9]+" \
    | grep -oE '[0-9]+' | sort -u || true
}
