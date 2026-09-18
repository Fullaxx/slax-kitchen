#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Unit tests for the recipe engine's pure logic (no ISO needed, milliseconds).
. "$(dirname "$0")/../lib.sh"

# GIT'S REPOSITORY VARIABLES DO NOT BELONG IN A TEST'S ENVIRONMENT.
#
# githooks(5) says it outright: git exports GIT_DIR, GIT_INDEX_FILE and the rest so a hook
# can find the repository, and a hook that runs git "in a foreign repository or in a
# different working tree" must clear them first. Four tests here do exactly that --
# test_ci_lib, test_release, test_sources and test_tier_c_guard build throwaway
# repositories and run init/add/commit inside them -- and this loop handed them the hook's
# environment whole. Issue #23.
#
# Measured on git 2.43.0, three commit modes, in a throwaway repository whose pre-commit
# hook was this gate:
#
#   git commit          GIT_INDEX_FILE=.git/index -- RELATIVE, so it resolved inside
#                       whichever fixture directory the test was working in and nothing
#                       happened. Safe by accident, not by design.
#   git commit -a       GIT_INDEX_FILE is the absolute path of the commit's own lock file
#   git commit -- path  (.git/index.lock, or .git/next-index-<pid>.lock for a pathspec).
#                       A fixture's `git add -A` rewrote the index that was about to be
#                       committed: "error: invalid object ... for 'fixture-file'", "error:
#                       Error building trees", commit refused -- and this gate printed ok.
#   commit in a linked  GIT_DIR names the worktree's gitdir, absolute. Worst of the three:
#   worktree            the commit SUCCEEDED, reporting ok, and the tree it recorded was
#                       the fixture's -- one throwaway file, every real path deleted.
#
# CLEARED HERE, NOT IN run-checks.sh: every other gate must KEEP these. During a partial
# commit GIT_INDEX_FILE names the temporary index that is actually being committed, which
# is exactly what check_files() has to read -- scrubbing globally would trade a latent bug
# for a live one.
#
# CLEARED HERE, NOT IN EACH TEST: this covers the tests not written yet, and one rule in
# one place cannot drift from itself. The gap that leaves, stated rather than discovered
# later: a test run BY HAND straight from a hook is still unscrubbed.
#
# The names come from git rather than a list copied into this file, which would be one more
# pair of things to keep in step.
_repo_env=$(git rev-parse --local-env-vars 2>/dev/null)
if [ -z "$_repo_env" ]; then
    # Fail closed. An empty list here means running every test with the commit's own index
    # within reach, which is the bug -- and a check that cannot fail is worse than no check.
    fail "git rev-parse --local-env-vars answered nothing"
    printf '      Refusing to run: the tests build their own repositories, and without\n' >&2
    printf '      that list they would do it with this commit%s index in reach.\n' "'" >&2
    check_result
    exit
fi

# AND A TMPDIR OF ITS OWN, FOR THE SAME REASON, IN THE SAME PLACE.
#
# Five of the fifteen tests here build fixtures with tempfile.mkdtemp() and never remove
# them: 46 directories per run of this gate, measured with TMPDIR pointed somewhere empty.
# This gate runs at pre-commit AND pre-push, so a machine with the hooks installed collects
# them at every commit and every push -- 238 MB of them since 2026-09-13 on the machine
# where it was found. CI runners are thrown away, which is why nothing noticed. Issue #24.
#
# Here rather than in the five tests for the reason above, and for one the #23 scrub does
# not have: test_unit_gate.py's fixture is made by a probe THIS GATE SPAWNS, a grandchild
# the test cannot see, so its own `finally: rmtree` could never have reached it. A test
# cannot always clean up after itself. The gate can.
#
# KEPT WHEN THE TEST FAILS, and the path printed: a failure is exactly when the fixtures
# are worth having, and a red gate blocks the commit, so they cannot pile up. The leak then
# only happens when someone is already looking for it.
for t in "$REPO_ROOT"/tests/unit/test_*.py; do
    [ -f "$t" ] || continue
    _tmp=$(mktemp -d) || { fail "$(basename "$t"): cannot create its TMPDIR"; continue; }
    ( unset $_repo_env; TMPDIR=$_tmp; export TMPDIR; exec python3 "$t" ) \
        >/dev/null 2>/tmp/.kitchen-unit.$$ || {
        fail "$(basename "$t")"
        sed 's/^/      /' /tmp/.kitchen-unit.$$ >&2
        note "fixtures kept for debugging: $_tmp"
        _tmp=
    }
    if [ -n "$_tmp" ]; then
        # AND THE BOX IS A DETECTOR, NOT ONLY A MOP. Whatever is still in here after a test
        # PASSED was left behind rather than cleaned up, and removing it quietly is how the
        # four tests in issue #25 went on littering every by-hand run with nothing to say
        # so. Free, because the directory is already in hand: a behavioural census would
        # re-run the whole suite, and at 16 s dominated by test_qemu_boot's deliberate
        # sleeps that would double a gate which runs at pre-commit AND pre-push.
        #
        # Only after a PASS. A failing test keeps everything by the branch above, and
        # complaining about its fixtures there would be noise on top of a real failure.
        _left=$(ls -A "$_tmp" 2>/dev/null | wc -l)
        if [ "$_left" -gt 0 ]; then
            fail "$(basename "$t"): left $_left fixture(s) in its TMPDIR"
            ls -A "$_tmp" | sed 's/^/      /' >&2
        fi
        rm -rf "$_tmp"
    fi
    rm -f /tmp/.kitchen-unit.$$
done
check_result
