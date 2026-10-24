#!/bin/sh
# stages: pre-commit pre-push ci
# desc: vendor/linux-live must stay byte-identical to its pinned upstream commit.
#
# Upstream Linux Live Kit is GPLv2 inside an MIT repo. We vendor it unmodified as a
# submodule and never relicense or patch it in place. Only the submodule POINTER may
# move, and that needs a note in docs/15-upstream/drift-report.md: a rule for that commit,
# which this gate does not check.
#
# THREE RULES, and what they do not cover:
#   1. no file under vendor/linux-live/ is committed into this repository;
#   2. the submodule is checked out at its pin: `git submodule status` has no leading +;
#   3. nothing inside it is modified, staged or untracked. Ignored files do not count.
# Not covered: whether a newly recorded pin is an upstream commit. A commit made inside
# the submodule, then `git add` of the pointer, passes all three.
#
# RULE 3 IS #101. Until then this read only `git submodule status`, whose + means a moved
# pointer, and its message said it caught "uncommitted local modifications". It did not:
# an edit or an untracked file inside the submodule leaves that line unchanged. slax-rpgs
# measured it on its own copy of this gate on 2026-10-01. On 2026-10-24 a scratch clone
# here passed with a line appended to DOC/bundle.txt, and with an untracked file, while the
# parent's `git status` showed ` M vendor/linux-live`.
#
# NOT CHECKED OUT IS A SKIP, said out loud. A clone without --recurse-submodules leaves
# vendor/linux-live an empty directory with no .git, and `git -C` there answers for the
# PARENT repository: measured, its status listed the parent's own untracked file. So rules
# 2 and 3 run only where the submodule is checked out.
. "$(dirname "$0")/../lib.sh"

[ -e "$REPO_ROOT/vendor/linux-live" ] || { note "vendor/linux-live not present yet - skipping"; exit 0; }

# Rule 1: no file inside the submodule tree may be staged directly.
check_files_nl | grep '^vendor/linux-live/' | while IFS= read -r f; do echo "$f"; done \
    > /tmp/.kitchen-vendor.$$
while IFS= read -r f; do
    [ -n "$f" ] && fail "vendor/ must stay pristine, do not commit into it: $f"
done < /tmp/.kitchen-vendor.$$
rm -f /tmp/.kitchen-vendor.$$

_sub=$(git -C "$REPO_ROOT" submodule status vendor/linux-live 2>/dev/null)
if [ ! -e "$REPO_ROOT/vendor/linux-live/.git" ] || [ "${_sub#-}" != "$_sub" ]; then
    note "vendor/linux-live is not checked out - rules 2 and 3 skipped (git submodule update --init)"
else
    # Rule 2: checked out at its pin.
    case "$_sub" in
        +*) fail "vendor/linux-live is checked out at a commit other than its pin"
            note "if the pointer move is intentional, record it in docs/15-upstream/drift-report.md" ;;
    esac

    # Rule 3: nothing modified, staged or untracked inside it. A git that will not answer
    # fails too, as ci/lib.sh refuses one for the parent: unread is not clean. Under
    # another uid, as in CI's container job, git refuses the submodule for "dubious
    # ownership" even with the parent in safe.directory: reproduced 2026-10-24 with that
    # job's ownership in the reference container.
    #
    # A GIT HOOK EXPORTS THE PARENT'S GIT_DIR AND GIT_INDEX_FILE, and git inside the
    # submodule reads them. Under the pre-commit hook this failed with "fatal: .git/index:
    # index file open failed: Not a directory", 2026-10-24, while every run outside a hook
    # passed. They are cleared here as ci/checks/80-unit.sh clears them for its tests.
    _repo_env=$(git rev-parse --local-env-vars 2>/dev/null)
    # shellcheck disable=SC2086  # one variable name per word, split on purpose
    if ! _dirty=$(unset $_repo_env; git -C "$REPO_ROOT/vendor/linux-live" status \
            --porcelain --untracked-files=all 2>&1); then
        fail "git will not answer for vendor/linux-live, so nothing inside it was checked: $(printf '%s\n' "$_dirty" | head -1)"
        note "if that is dubious ownership: git config --global --add safe.directory $REPO_ROOT/vendor/linux-live"
    elif [ -n "$_dirty" ]; then
        _n=$(printf '%s\n' "$_dirty" | wc -l | tr -d ' ')
        _some=$(printf '%s\n' "$_dirty" | head -3 | cut -c4- | tr '\n' ' ')
        fail "vendor/linux-live has $_n modified or untracked path(s) inside it: $_some"
        note "see: git -C vendor/linux-live status"
    fi
fi
check_result
