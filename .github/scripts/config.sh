# Ustawienia automatów repo w jednym miejscu (#392): progi czasowe i słowa,
# które rozpoznają workflowy. Wczytują go same skrypty z tego katalogu
# (`source` na ich początku), więc workflow, który robi `source` na stale.sh,
# refs.sh, guard.sh czy pr-state.sh, dostaje te wartości bez dodatkowego kroku.
# Workflowy pobierają skrypty z `main`, więc zmiana działa po merge'u — PR nie
# zmieni tych wartości dla własnych biegów.
#
# Czego stąd zmienić się nie da:
# - godzin biegów (`cron:` w stale-issues.yml, stale-prs.yml,
#   daily-digest.yml) — GitHub czyta harmonogram wprost z pliku workflowu;
# - wstępnego sita w `if:` (wołanie `@claude`, etykieta `stale`) — wyrażenie
#   liczy się przed checkoutem, więc pliku nie widzi. Tam stoi kopia
#   literału; test tests/test_konfiguracja_automatow.py pilnuje, żeby była
#   taka sama jak tutaj.
#
# Format: zwykłe przypisania basha, bez spacji wokół `=`.

# --- Cisza w zgłoszeniach i PR-ach (stale-issues.yml, stale-prs.yml,
# stale-unlabel.yml, issue-labels.yml) ---
# Po tylu dniach ciszy przypomnienie i etykieta…
DAYS_QUIET=14
# …a po tylu kolejnych bez odpowiedzi zamknięcie (PR czekający na recenzję
# dostaje w tym rytmie kolejne pingi na Discordzie, bez zamykania).
DAYS_GRACE=7
LABEL_STALE="stale"

# --- Etykiety stanu PR-a (pr-state.sh, stale.sh) ---
LABEL_PR_AUTHOR="waiting for author"
LABEL_PR_CLAUDE="waiting for claude review"
LABEL_PR_HUMAN="waiting for human review"
LABEL_PR_OTHER="waiting for other PR"

# --- Wołanie Claude'a (guard.sh, claude-issue.yml, claude-review-pr.yml) ---
CLAUDE_MENTION="@claude"
# Gałąź solvera to `<przedrostek><numer zgłoszenia>`, a jej robocze odnogi
# `<przedrostek><numer>-run<id biegu>`.
CLAUDE_BRANCH_PREFIX="claude/issue-"

# --- Słowa wskazujące zgłoszenie w PR-ze (refs.sh, syntax.sh) ---
# Słowo przed `#numer` w tytule albo opisie, bez względu na wielkość liter.
# Rozdzielone `|`; same litery, a w słowie z kilku wyrazów zwykła spacja
# (przed numerem może stać dowolny odstęp i dwukropek).
# Zamyka zgłoszenie po merge'u (ścieżka A w issue-labels.yml) — te same słowa,
# które rozumie GitHub:
REFS_CLOSE_WORDS="close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved"
# Tylko wskazuje zgłoszenie, bez zamykania (ścieżka B):
REFS_MENTION_WORDS="ref|refs|related to|relates to|part of|dotyczy"
