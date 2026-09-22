#!/usr/bin/env python3
"""ci/checks/80-unit.sh must not hand git's repository variables to the tests it runs.

WHY THIS EXISTS. githooks(5) says a hook that runs git "in a foreign repository or in a
different working tree" must clear GIT_DIR, GIT_INDEX_FILE and the rest first. Four tests
here build throwaway repositories and run init/add/commit inside them -- test_ci_lib,
test_release, test_sources, test_tier_c_guard -- and the gate ran them with the pre-commit
hook's environment inherited whole. Issue #23.

Measured on git 2.43.0 in a throwaway repository whose pre-commit hook was this gate. What
git exports depends on how the commit was invoked, so the damage does too:

  git commit           GIT_INDEX_FILE=.git/index, RELATIVE. It resolved inside the fixture
                       and nothing happened -- safe by accident.
  git commit -a        an absolute .git/index.lock; a fixture's `git add -A` rewrote the
  git commit -- path   index being committed. "error: invalid object ... for
                       'fixture-file'", "error: Error building trees", commit refused,
                       GATE PRINTED OK.
  linked worktree      an absolute GIT_DIR. The commit SUCCEEDED, gate green, and the tree
                       it recorded was `fixture-file` alone -- every real path deleted.

THE FIX IS INVISIBLE TO AN ORDINARY RUN, which is the whole reason this file exists.
Outside a hook none of those variables is set, so `unset` is a no-op and `run-checks.sh ci`
says nothing about whether the scrub works. A fix that only its own absence can detect is
what this project refuses to ship: a check which cannot fail is worse than no check.

So the gate is driven against a throwaway repository with a VALID poison pointing at a
second one -- the same shape as test_ci_lib.py and test_tier_c_guard.py, which drive shell
against fixtures rather than this checkout. Valid matters: a bogus GIT_DIR trips ci/lib.sh's
own FATAL before the loop is reached, and this would then pass for the wrong reason.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
LIB = os.path.join(ROOT, "ci", "lib.sh")
RUNNER = os.path.join(ROOT, "ci", "unit-run.py")
GATE = os.path.join(ROOT, "ci", "checks", "80-unit.sh")

FAILURES = []

# What the probe does is what the four real tests do: build a repository somewhere else and
# commit in it. With the variables inherited, `git -C d add -A` writes the index of the
# commit in progress instead of the fixture's.
PROBE = '''#!/usr/bin/env python3
import os, shutil, subprocess, sys, tempfile
open(os.environ["PROBE_REPORT"] + ".tmpdir", "w").write(tempfile.gettempdir())
d = tempfile.mkdtemp(prefix="probe-")
def g(*a):
    return subprocess.run(["git", "-C", d] + list(a), capture_output=True, text=True)
g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t")
open(os.path.join(d, "fixture-file"), "w").write("x\\n")
g("add", "-A"); g("commit", "-qm", "fixture commit")
with open(os.environ["PROBE_REPORT"], "w") as fh:
    fh.write("\\n".join(sorted(k for k in os.environ if k.startswith("GIT_"))))
LITTER or shutil.rmtree(d, ignore_errors=True)
sys.exit(EXIT)
'''

# A test nobody runs, in the shape that actually happens: the name is still mentioned in
# code -- here in a list the file keeps and never iterates -- so every version of this
# check that read the source rather than the run called it registered.
STRAY = '''

def test_never_called():
    raise AssertionError("a test nobody ran cannot fail, which is the bug")


_LEFTOVER = [test_never_called]
'''


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True,
                          env=clean_env())


_LOCAL_ENV_VARS = None


def local_env_vars():
    """The names git calls repository-local -- the exact set the gate scrubs.

    Read from git rather than listed here for the same reason the gate reads it: a copy is
    one more thing to keep in step. Note what this is NOT: GIT_EDITOR, GIT_AUTHOR_NAME and
    friends are not repository-local and are not git's to clear, so a test asserting "no
    GIT_* at all survived" asserts something the fix never promised. This file's first run
    did exactly that and failed on an ambient GIT_EDITOR.

    Asked once. clean_env() calls this and git() calls clean_env(), so the answer was
    re-derived by a subprocess on every git invocation in this file -- measured
    2026-09-20 at about 70 processes per run, to recompute a list that cannot change
    while the interpreter is alive.
    """
    global _LOCAL_ENV_VARS
    if _LOCAL_ENV_VARS is None:
        _LOCAL_ENV_VARS = subprocess.run(
            ["git", "rev-parse", "--local-env-vars"],
            capture_output=True, text=True).stdout.split()
    return _LOCAL_ENV_VARS


def clean_env():
    """os.environ with git's repository variables removed.

    The outer test measures the victim, and it must do that with an environment the poison
    cannot reach -- otherwise `git -C victim log` would answer for whatever GIT_DIR names.
    """
    names = local_env_vars()
    return {k: v for k, v in os.environ.items() if k not in names}


def victim_repo(tmp):
    """The repository standing in for the one being committed. One commit, one file."""
    repo = os.path.join(tmp, "victim")
    os.makedirs(repo)
    for a in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
              ["config", "user.name", "t"]):
        git(repo, *a)
    with open(os.path.join(repo, "seed"), "w") as fh:
        fh.write("seed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "seed")
    return repo


def gate_fixture(tmp, probe_exit=0, litter=False, stray=False):
    """A REPO_ROOT for the gate: the real lib.sh, the real runner and the real gate, plus
    one probe test.

    litter=True leaves the probe's fixture behind, which is what a badly-behaved test does
    and what the gate's detector exists to name.

    THE REAL RUNNER, not a stand-in. The gate runs every test through ci/unit-run.py, so a
    fixture without it is a fixture where nothing runs at all -- which shows up here as a
    probe that wrote no report, several assertions away from the cause.
    """
    fx = os.path.join(tmp, "fixture")
    os.makedirs(os.path.join(fx, "ci", "checks"))
    os.makedirs(os.path.join(fx, "tests", "unit"))
    shutil.copy2(LIB, os.path.join(fx, "ci", "lib.sh"))
    shutil.copy2(RUNNER, os.path.join(fx, "ci", "unit-run.py"))
    shutil.copy2(GATE, os.path.join(fx, "ci", "checks", "80-unit.sh"))
    p = os.path.join(fx, "tests", "unit", "test_probe.py")
    body = PROBE.replace("LITTER", str(bool(litter))).replace("EXIT", str(probe_exit))
    if stray:
        # A test the file defines and never calls -- and one that WOULD fail, so the run
        # reporting success is the whole defect in one file. It is appended after the
        # sys.exit, which is exactly how the real thing looks: the function is there, and
        # nothing reaches it.
        body += STRAY
    with open(p, "w") as fh:
        fh.write(body)
    os.chmod(p, 0o755)
    return fx


# Exactly what git 2.43.0 exports to a pre-commit hook for a commit in a linked worktree,
# measured rather than guessed -- an absolute GIT_DIR and an absolute GIT_INDEX_FILE, and
# NOT GIT_WORK_TREE. The difference is not cosmetic: adding GIT_WORK_TREE makes the probe's
# `git -C <fixture> add -A` refuse as outside the work tree, so the poison stops being
# poisonous and the behavioural assertion below passes for the wrong reason. It did, on the
# first run of this file.
POISON = {"GIT_DIR": ".git", "GIT_INDEX_FILE": os.path.join(".git", "index")}


def run_gate(fx, victim, report, extra_path=None):
    """The gate, as a hook would run it: cwd in the victim, its variables in the env."""
    env = dict(os.environ, REPO_ROOT=fx, KITCHEN_SCOPE="staged", PROBE_REPORT=report,
               **{k: os.path.join(victim, v) for k, v in POISON.items()})
    if extra_path:
        env["PATH"] = extra_path + os.pathsep + os.environ.get("PATH", "")
    p = subprocess.run(["sh", os.path.join(fx, "ci", "checks", "80-unit.sh")],
                       cwd=victim, env=env, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def test_a_test_cannot_reach_the_commit_in_progress():
    """The one that counts, asserted on the victim rather than on the environment.

    Without the scrub this is the linked-worktree outcome: the probe's `add -A` replaces the
    index, and what the repository records is the fixture's file instead of its own.
    """
    tmp = tempfile.mkdtemp(prefix="unitgate-")
    try:
        victim = victim_repo(tmp)
        fx = gate_fixture(tmp)
        report = os.path.join(tmp, "report")

        before = git(victim, "ls-files", "-s").stdout
        rc, out = run_gate(fx, victim, report)

        check("the gate passes", rc, 0)
        check("the victim still has one commit",
              git(victim, "rev-list", "--count", "HEAD").stdout.strip(), "1")
        check("...recording its own file and nothing else",
              git(victim, "ls-tree", "-r", "--name-only", "HEAD").stdout.split(), ["seed"])
        check("...and its index is untouched", git(victim, "ls-files", "-s").stdout, before)
        if "fixture-file" in git(victim, "ls-files").stdout:
            FAILURES.append(f"the probe's file reached the victim's index; gate said: {out}")

        # Belt and braces, and it names the leak when the assertion above fires. Scoped to
        # the repository-local set: those are the ones the gate clears and the only ones
        # githooks(5) is about.
        with open(report) as fh:
            seen = {ln for ln in fh.read().split("\n") if ln}
        check("the probe saw no repository variables",
              sorted(seen & set(local_env_vars())), [])
        check("...and the poison really was a set git calls repository-local",
              sorted(set(POISON) - set(local_env_vars())), [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_test_that_litters_is_named():
    """The box is a detector, not only a mop. Issue #25.

    Removing a test's leftovers quietly is how four tests here went on littering every
    by-hand run with nothing to say so: the gate cleaned up after them, so a gate run was
    always green and the mess only appeared somewhere nobody was looking. The gate now says
    which test left what -- and still removes it, because the point is not to start
    accumulating again in order to prove a point.
    """
    tmp = tempfile.mkdtemp(prefix="unitgate-litter-")
    try:
        victim = victim_repo(tmp)
        fx = gate_fixture(tmp, litter=True)
        report = os.path.join(tmp, "report")
        rc, out = run_gate(fx, victim, report)
        check("the gate goes red", rc != 0, True)
        check("...naming the test", "test_probe.py" in out, True)
        check("...and what it left", "left 1 fixture(s) in its TMPDIR" in out, True)
        check("...listing it by name", "probe-" in out, True)

        # Still removed. A detector that stopped mopping would trade this issue for #24.
        # Guarded like the case above, and for the reason that case gives: an uncaught
        # FileNotFoundError here aborts the suite and hides every check after it. Written
        # unguarded first, and the mutation that empties the sidecar proved it by wiping out
        # the whole run's output.
        sidecar = report + ".tmpdir"
        if not os.path.exists(sidecar):
            FAILURES.append("the probe recorded no TMPDIR, so the mop is unchecked here")
            return
        with open(sidecar) as fh:
            box = fh.read().strip()
        check("...and the box is removed anyway", os.path.exists(box), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_it_refuses_to_run_when_git_will_not_name_the_variables():
    """Fails closed. An empty list means running the tests with the index in reach.

    Same shape and same argument as ci/lib.sh's two FATALs: when the thing a guard is built
    on will not answer, refuse rather than proceed unguarded.
    """
    tmp = tempfile.mkdtemp(prefix="unitgate-fc-")
    try:
        victim = victim_repo(tmp)
        fx = gate_fixture(tmp)
        binp = os.path.join(tmp, "bin")
        os.makedirs(binp)
        real = shutil.which("git")
        stub = os.path.join(binp, "git")
        # Answers --show-toplevel so ci/lib.sh proceeds, and nothing for --local-env-vars.
        # Everything else goes to the real git, so the failure under test is the only one.
        with open(stub, "w") as fh:
            fh.write('#!/bin/sh\n'
                     'case "$*" in\n'
                     '  *--local-env-vars*) exit 0 ;;\n'
                     f'esac\nexec {real} "$@"\n')
        os.chmod(stub, 0o755)

        rc, out = run_gate(fx, victim, os.path.join(tmp, "report"), extra_path=binp)
        check("the gate refuses", rc != 0, True)
        check("...and says what would not answer", "--local-env-vars" in out, True)
        check("...and what it was protecting", "index in reach" in out, True)
        check("...and ran no test at all", os.path.exists(os.path.join(tmp, "report")), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_gate_still_has_teeth_through_the_subshell():
    """`( unset ...; exec python3 ... ) || fail` -- exec must keep the status flowing.

    A scrub that swallowed a test's exit code would be the same class of bug as the one it
    was written to fix: the gate reporting ok while something underneath it failed.
    """
    tmp = tempfile.mkdtemp(prefix="unitgate-teeth-")
    try:
        victim = victim_repo(tmp)
        fx = gate_fixture(tmp, probe_exit=1)
        report = os.path.join(tmp, "report")
        rc, out = run_gate(fx, victim, report)
        check("a failing test fails the gate", rc != 0, True)
        check("...and is named", "test_probe.py" in out, True)

        # And the other half of issue #24: a FAILING test keeps its fixtures, because that
        # is when they are worth having. Pinned here rather than in its own case because
        # this is already the only place that drives the gate to red.
        check("...and its fixtures are kept", "fixtures kept" in out, True)
        kept = out.split("fixtures kept for debugging:", 1)[-1].split("\n")[0].strip() \
            if "fixtures kept" in out else ""
        check("...at a path that still exists", bool(kept) and os.path.isdir(kept), True)
        if kept:
            # It lives outside `tmp` by construction -- the gate made it, not this test --
            # so the finally below cannot reach it. Removing it here is the difference
            # between fixing a leak and trading one for another.
            shutil.rmtree(kept, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_test_leaves_nothing_behind():
    """The gate gives each test a TMPDIR of its own and removes it. Issues #24 and #25.

    Five of the fifteen tests here on 2026-09-18 never removed their fixtures -- 46
    directories per run of this gate, 238 MB on the machine where it was found, because the
    gate runs at both pre-commit and pre-push. Dated: it was true then, not a claim about now. Nothing would have noticed the fix going again: every test
    passes with or without it.

    Asserted on the TMPDIR the gate handed out rather than on a fixture inside it: that
    directory is made by `mktemp -d` in the gate itself, so it is the gate's to remove and
    nothing else's. A probe that tidied up after itself would otherwise leave an empty box
    that could sit there forever without any assertion noticing.
    """
    tmp = tempfile.mkdtemp(prefix="unitgate-tmp-")
    try:
        victim = victim_repo(tmp)
        fx = gate_fixture(tmp)
        report = os.path.join(tmp, "report")
        rc, _out = run_gate(fx, victim, report)
        check("the gate passes", rc, 0)

        # Caught rather than left to raise: a missing sidecar means the probe never ran, and
        # an uncaught FileNotFoundError here would abort the suite and hide every later
        # check. Without this the case passes vacuously -- "the directory is gone" is
        # trivially true of a directory that was never made.
        sidecar = report + ".tmpdir"
        if not os.path.exists(sidecar):
            FAILURES.append("the probe recorded no TMPDIR, so this case proves nothing")
            return
        with open(sidecar) as fh:
            box = fh.read().strip()
        check("the probe was given a TMPDIR of its own", bool(box), True)
        check("...not the one this test is using", box != tempfile.gettempdir(), True)
        check("...and it is gone once the gate returns", os.path.exists(box), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_it_refuses_to_run_when_it_cannot_get_a_private_tmpdir():
    """The other fail-closed branch, and it needs its own case or it is decorative.

    Dropping the `mktemp -d ||` guard changes nothing while mktemp works, so no other test
    here would notice it going. Without it a machine that cannot make a temp directory runs
    every test against the shared /tmp instead -- quietly, which is the state this gate is
    supposed to be getting us out of.
    """
    tmp = tempfile.mkdtemp(prefix="unitgate-nomk-")
    try:
        victim = victim_repo(tmp)
        fx = gate_fixture(tmp)
        binp = os.path.join(tmp, "bin")
        os.makedirs(binp)
        stub = os.path.join(binp, "mktemp")
        with open(stub, "w") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        os.chmod(stub, 0o755)

        report = os.path.join(tmp, "report")
        rc, out = run_gate(fx, victim, report, extra_path=binp)
        check("the gate refuses", rc != 0, True)
        check("...and says which test and why", "cannot create its TMPDIR" in out, True)
        check("...and ran it anyway: no", os.path.exists(report), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_every_test_here_is_registered():
    """A test that exists and never runs is the shape this repository keeps shipping.

    6ecf019 and test_status_removals both did it: the function was written, main() did not
    list it, and breaking the code under test produced zero failures.
    """
    defined = {k for k, v in globals().items() if k.startswith("test_") and callable(v)}
    check("main() runs every test in this file", sorted(defined - {f.__name__ for f in TESTS}),
          [])


def test_a_test_that_never_runs_is_named():
    """The gate's oldest silent failure, and the one it kept half-catching.

    A function written, reviewed and committed while main()'s hand-maintained list never
    gained it: the suite prints "all checks passed", because the check that would have
    disagreed was never called. This probe's stray test raises on sight, so if anything
    ran it the run would be red -- it is green, and the gate has to say why.

    The name IS mentioned in code, in a list the probe keeps and never iterates. That is
    what a half-finished edit to a TESTS list looks like, and it is what the two earlier
    versions of this check accepted: the grep counted a mention anywhere, the ast pass
    counted any reference in code. Neither could have failed here.
    """
    tmp = tempfile.mkdtemp(prefix="unitgate-")
    try:
        victim = victim_repo(tmp)
        fx = gate_fixture(tmp, stray=True)
        rc, out = run_gate(fx, victim, os.path.join(tmp, "report"))
        check("the gate fails", rc, 1)
        check("...naming the test that did not run",
              "test_never_called is defined but never ran" in out, True)
        check("...and saying which file it is in", "test_probe.py" in out, True)
        # The gate keeps a failing test's box on purpose, and it made that box outside
        # `tmp`, so the finally below cannot reach it. Same removal as the teeth case
        # above, for the same reason: this test drives the gate to red, and a test that
        # leaks while proving a leak detector works has traded issue #25 for issue #24.
        kept = out.split("fixtures kept for debugging:", 1)[-1].split("\n")[0].strip() \
            if "fixtures kept" in out else ""
        if kept:
            shutil.rmtree(kept, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


TESTS = [test_a_test_cannot_reach_the_commit_in_progress,
         test_a_test_that_never_runs_is_named,
         test_a_test_leaves_nothing_behind,
         test_a_test_that_litters_is_named,
         test_it_refuses_to_run_when_git_will_not_name_the_variables,
         test_it_refuses_to_run_when_it_cannot_get_a_private_tmpdir,
         test_the_gate_still_has_teeth_through_the_subshell,
         test_every_test_here_is_registered]


def main():
    for fn in TESTS:
        # One test crashing must not stop the rest: the count of failures is only honest
        # if every test ran. The traceback still goes to stderr, because a crash's location
        # is the useful half and a one-line summary loses it.
        try:
            fn()
        except Exception as e:                 # noqa: BLE001
            traceback.print_exc()
            FAILURES.append(f"{fn.__name__} crashed: {type(e).__name__}: {e}")
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_unit_gate.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
