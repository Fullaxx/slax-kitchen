#!/bin/sh
# Shared helpers for slax-kitchen checks.
# Sourced by ci/run-checks.sh and by every ci/checks/*.sh script.
#
# Contract for a check script:
#   - declares its stages in a "# stages:" header line (pre-commit, pre-push, ci)
#   - reads $KITCHEN_SCOPE: "staged" (only files staged for commit) or "tree" (whole repo)
#   - uses check_files() to get the file list for the current scope
#   - calls fail() for each problem, then exits with check_result()

: "${KITCHEN_SCOPE:=tree}"
: "${REPO_ROOT:=$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

_FAILED=0

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RED=$(printf "\033[31m")
    C_YEL=$(printf '\033[33m'); C_DIM=$(printf '\033[2m'); C_OFF=$(printf '\033[0m')
else
    C_RED=; C_YEL=; C_DIM=; C_OFF=
fi

fail() { _FAILED=1; printf '%s  FAIL%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; }
warn() { printf '%s  warn%s %s\n' "$C_YEL" "$C_OFF" "$*" >&2; }
note() { printf '%s  %s%s\n' "$C_DIM" "$*" "$C_OFF" >&2; }
check_result() { return $_FAILED; }

# Files in scope, NUL-separated on stdout.
#   staged -> added/copied/modified files staged for commit
#   tree   -> every tracked file, plus untracked-but-not-ignored files
check_files() {
    if [ "$KITCHEN_SCOPE" = "staged" ]; then
        git diff --cached --name-only --diff-filter=ACMR -z
    else
        git ls-files -z
        git ls-files -z --others --exclude-standard
    fi
}

# Same, but newline-separated (convenient; paths with newlines are rejected by 70-whitespace).
check_files_nl() { check_files | tr '\0' '\n' | grep -v '^$' || true; }

# Read a file as it will be committed (staged content), or from disk in tree scope.
file_content() {
    if [ "$KITCHEN_SCOPE" = "staged" ]; then
        git show ":$1" 2>/dev/null
    else
        cat "$REPO_ROOT/$1" 2>/dev/null
    fi
}

# Size in bytes of a file as it will be committed.
file_size() {
    if [ "$KITCHEN_SCOPE" = "staged" ]; then
        git cat-file -s "$(git rev-parse ":$1" 2>/dev/null)" 2>/dev/null || echo 0
    else
        stat -c%s "$REPO_ROOT/$1" 2>/dev/null || echo 0
    fi
}

have() { command -v "$1" >/dev/null 2>&1; }
