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

_FAILED=0

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RED=$(printf "\033[31m")
    C_YEL=$(printf '\033[33m'); C_DIM=$(printf '\033[2m'); C_OFF=$(printf '\033[0m')
else
    C_RED=; C_YEL=; C_DIM=; C_OFF=
fi

# git has to actually answer, and this is checked before anything else runs.
#
# Every file-scoped gate funnels through check_files(), and check_files() funnels through
# git. When git refuses to speak -- dubious ownership, a corrupt index, not a repository --
# the file list is not an error, it is the EMPTY SET, and `|| true` in check_files_nl turns
# the refusal into silence. The gate then loops over nothing, never calls fail(), and exits
# 0. It reports success having examined no files at all.
#
# Measured 2026-09-16, not assumed: `docs/README.md` was given trailing whitespace on
# purpose. As the repo's owner, 70-whitespace printed `FAIL trailing whitespace:
# docs/README.md`. As a uid git would not answer for, the same gate on the same dirty tree
# printed NOTHING and exited 0. Five gates were in that state simultaneously --
# 00-no-binaries, 10-no-dnc, 50-secrets, 60-links, 70-whitespace.
#
# `.github/workflows/ci.yml`'s container job already had to add `safe.directory` to get
# past this, and the comment there said the gates "die with git's dubious-ownership
# message instead of its own". That was wrong in the way that matters: they did not die.
# The workaround was load-bearing and nobody knew what it was bearing.
#
# This project's rule is that a check which cannot fail is worse than no check. So when git
# will not answer, refuse to run rather than pass. Every gate is invoked as `sh "$chk"`, so
# exiting here exits that gate non-zero and run-checks.sh reports it.
if ! _top=$(git rev-parse --show-toplevel 2>&1); then
    printf '%s  FATAL%s ci/lib.sh: git will not answer for %s\n' "$C_RED" "$C_OFF" "$(pwd)" >&2
    printf '%s\n' "$_top" | sed 's/^/         /' >&2
    printf '         Refusing to run: every file-scoped gate would examine ZERO files\n' >&2
    printf '         and report success. Fix git access, then re-run.\n' >&2
    exit 1
fi
: "${REPO_ROOT:=$_top}"

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
