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

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
LIB = os.path.join(ROOT, "ci", "lib.sh")
GATE = os.path.join(ROOT, "ci", "checks", "80-unit.sh")

FAILURES = []

# What the probe does is what the four real tests do: build a repository somewhere else and
# commit in it. With the variables inherited, `git -C d add -A` writes the index of the
# commit in progress instead of the fixture's.
PROBE = '''#!/usr/bin/env python3
import os, subprocess, sys, tempfile
d = tempfile.mkdtemp(prefix="probe-")
def g(*a):
    return subprocess.run(["git", "-C", d] + list(a), capture_output=True, text=True)
g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t")
open(os.path.join(d, "fixture-file"), "w").write("x\\n")
g("add", "-A"); g("commit", "-qm", "fixture commit")
with open(os.environ["PROBE_REPORT"], "w") as fh:
    fh.write("\\n".join(sorted(k for k in os.environ if k.startswith("GIT_"))))
sys.exit(%d)
'''


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True,
                          env=clean_env())


def local_env_vars():
    """The names git calls repository-local -- the exact set the gate scrubs.

    Read from git rather than listed here for the same reason the gate reads it: a copy is
    one more thing to keep in step. Note what this is NOT: GIT_EDITOR, GIT_AUTHOR_NAME and
    friends are not repository-local and are not git's to clear, so a test asserting "no
    GIT_* at all survived" asserts something the fix never promised. This file's first run
    did exactly that and failed on an ambient GIT_EDITOR.
    """
    return subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True,
                          text=True).stdout.split()


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


def gate_fixture(tmp, probe_exit=0):
    """A REPO_ROOT for the gate: the real lib.sh and the real gate, plus one probe test."""
    fx = os.path.join(tmp, "fixture")
    os.makedirs(os.path.join(fx, "ci", "checks"))
    os.makedirs(os.path.join(fx, "tests", "unit"))
    shutil.copy2(LIB, os.path.join(fx, "ci", "lib.sh"))
    shutil.copy2(GATE, os.path.join(fx, "ci", "checks", "80-unit.sh"))
    p = os.path.join(fx, "tests", "unit", "test_probe.py")
    with open(p, "w") as fh:
        fh.write(PROBE % probe_exit)
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
        rc, out = run_gate(fx, victim, os.path.join(tmp, "report"))
        check("a failing test fails the gate", rc != 0, True)
        check("...and is named", "test_probe.py" in out, True)
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


TESTS = [test_a_test_cannot_reach_the_commit_in_progress,
         test_it_refuses_to_run_when_git_will_not_name_the_variables,
         test_the_gate_still_has_teeth_through_the_subshell,
         test_every_test_here_is_registered]


def main():
    for fn in TESTS:
        fn()
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_unit_gate.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
