#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Unit tests for the recipe engine's pure logic (no ISO needed, seconds).
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
# Five of the fifteen tests here ON 2026-09-18 built fixtures with tempfile.mkdtemp() and
# never removed them: 46 directories per run of this gate, measured with TMPDIR pointed
# somewhere empty. The count is dated because it was true then and is not a claim about now.
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

# AND THE BOOT HOST IS OFF, FOR THE SAME REASON, IN THE SAME PLACE.
#
# A boot-host.ini is one developer's machine, it is untracked, and it changes what the code
# under test DOES: `kitchen test` hands its boot modes to ssh, and ci/tier-c.sh then checks
# for ssh and rsync rather than qemu and mkfs.ext4. A gate whose answer depends on an
# untracked file in the tester's working tree is not a gate.
#
# HONEST ABOUT ITS OWN WEIGHT: every test here builds its own fixture checkout, which has
# no boot-host.ini, so all of them pass without this line today -- measured, not assumed.
# It is here for the test not written yet, the one that drives the real `kitchen` from the
# real root, and because the alternative is remembering.
#
# It is not free, and that is the point: test_tier_c_run's remote-path case has to clear
# it again, in its own body, saying why. A test that means to exercise the boot host has
# to say so where someone reading that test can see it.
KITCHEN_BOOT_HOST=local
export KITCHEN_BOOT_HOST

# A TEST NOBODY REGISTERED IS A TEST THAT REPORTS SUCCESS WITHOUT RUNNING.
#
# Most files here keep an explicit list of their test functions in main() and call it.
# That list is hand-maintained, so a function can be written, reviewed and committed
# while never being called once -- and the file still prints "all checks passed",
# because nothing ran to disagree. The failure is silent at exactly the moment someone
# believes they have added coverage.
#
# ASKED OF THE RUN, NOT OF THE SOURCE, and that is the third answer here. Grepping for
# the name counted a mention in a comment. Parsing for an ast.Name counted any reference
# in code, so a name left behind in a list nothing iterates -- what a half-finished edit
# to that list looks like -- still read as registered. Each version made the inference
# sharper without making it true, because "did this function run" is not a question about
# the source text. ci/unit-run.py measures it: every unit test file below is run through
# it, sys.setprofile records what was entered, and what is left over is named.
#
# That is also why there is no globals() exemption any more. A file that discovers its
# tests that way runs them, and the measurement sees it.

for t in "$REPO_ROOT"/tests/unit/test_*.py; do
    [ -f "$t" ] || continue
    _tmp=$(mktemp -d) || { fail "$(basename "$t"): cannot create its TMPDIR"; continue; }
    ( unset $_repo_env; TMPDIR=$_tmp; export TMPDIR; \
      exec python3 "$REPO_ROOT/ci/unit-run.py" "$t" ) \
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
        # re-run the whole suite, and doubling a gate that runs at pre-commit AND
        # pre-push is not free at all.
        #
        # NO NUMBER HERE ON PURPOSE. This used to say what the suite cost, and so did
        # CONTRIBUTING.md; they were measured at different times and drifted to 16 s and
        # 18 s without either being wrong when it was written. CONTRIBUTING.md's
        # "Seconds, not milliseconds" paragraph is the one copy, and it says how to
        # re-measure. A number kept in two places is a number that disagrees with itself.
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
