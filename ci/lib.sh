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

# `stat -c%s` IS GNU, and a missing -c used to measure every file as zero. Same shape as
# the git guard above, same argument, forty lines apart: 00-no-binaries calls itself the
# most important gate in this repo and its size rule is `[ $(file_size f) -gt 2097152 ]`,
# so on BSD stat, or a busybox stat without -c, every file measured 0, nothing was ever too
# big, and the gate reported ok. A check which cannot fail is worse than no check -- so when
# stat will not answer, refuse to run rather than measure zero. Issue #19.
#
# Probed ONCE, against this library itself, which always exists: a failure then means "stat
# cannot do this" and never "that path is missing". Per-file probing would print a thousand
# FATALs where one is readable.
_sz=$(stat -c%s "$REPO_ROOT/ci/lib.sh" 2>/dev/null) || _sz=""
case "$_sz" in
    ''|*[!0-9]*)
        printf '%s  FATAL%s ci/lib.sh: stat -c%%s does not work here\n' "$C_RED" "$C_OFF" >&2
        printf '         Refusing to run: file_size would answer for no file, and the size\n' >&2
        printf '         rule in 00-no-binaries would then pass a binary of any size.\n' >&2
        printf '         GNU coreutils stat is required; BSD stat spells it -f%%z.\n' >&2
        exit 1 ;;
esac
unset _sz

# Size in bytes of a file as it will be committed, or the string MISSING.
#
# NOT `|| echo 0` on either branch, which is what let a file with no measurable size sail
# under a size limit. MISSING is deliberately not a number: a caller comparing it with
# `-gt` gets a shell error rather than a silent false, so a new caller cannot repeat the
# mistake by accident. Callers that legitimately tolerate an absent file test for it.
file_size() {
    if [ "$KITCHEN_SCOPE" = "staged" ]; then
        _oid=$(git rev-parse ":$1" 2>/dev/null) || { echo MISSING; return 0; }
        git cat-file -s "$_oid" 2>/dev/null || echo MISSING
    else
        stat -c%s "$REPO_ROOT/$1" 2>/dev/null || echo MISSING
    fi
}

have() { command -v "$1" >/dev/null 2>&1; }
