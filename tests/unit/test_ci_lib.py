#!/usr/bin/env python3
"""ci/lib.sh's file_size, the size rule in 00-no-binaries that depends on it, and
20-vendor-pristine, which keeps a submodule pristine.

WHY THIS EXISTS. file_size feeds the size limit in 00-no-binaries, which calls itself the
most important gate in this repo, and nothing tested it: `grep -rl file_size tests/`
returned nothing. Two bugs lived there in succession, each the consequence of fixing the
one before.

  #19  `|| echo 0` on both branches. A stat without -c measured every file as zero, so the
       size rule passed anything while reporting ok. Fixed by answering MISSING and
       refusing it.
  #22  MISSING was then the wrong answer for a SUBMODULE. `git rev-parse` succeeds on a
       gitlink -- it returns the submodule's own commit -- and `git cat-file` fails,
       because that object is in the submodule's store. So every commit staging a
       submodule pointer failed its own pre-commit hook. This repository has
       vendor/linux-live; the next pin bump would have hit it.

Driven against throwaway repositories rather than this checkout, the way
tests/unit/test_tier_c_guard.py and test_build_busybox.py drive shell: the cases need a
staged submodule bump and an oversized file, and neither belongs in this tree.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
LIB = os.path.join(ROOT, "ci", "lib.sh")
GATE = os.path.join(ROOT, "ci", "checks", "00-no-binaries.sh")
VENDOR_GATE = os.path.join(ROOT, "ci", "checks", "20-vendor-pristine.sh")

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def git(repo, *args, **kw):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                          text=True, **kw)


def new_repo(tmp, name):
    repo = os.path.join(tmp, name)
    os.makedirs(repo)
    for a in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
              ["config", "user.name", "t"]):
        git(repo, *a, check=True)
    return repo


def sh(repo, script):
    """Run a snippet with ci/lib.sh sourced, inside `repo`."""
    p = subprocess.run(["sh", "-c", f'. "{LIB}"\n{script}'], cwd=repo, capture_output=True,
                       text=True, env=dict(os.environ, KITCHEN_SCOPE="staged"))
    return (p.stdout + p.stderr).strip(), p.returncode


def test_a_staged_submodule_measures_zero_not_missing():
    """The #22 case, built with a real `git submodule add`.

    0 is honest: a gitlink is a pointer in a tree object and contributes no file content to
    the superproject, so there is nothing for a size limit to be about. MISSING would be
    refused by the UNMEASURED branch, which is what broke every pin bump.
    """
    tmp = tempfile.mkdtemp(prefix="cilib-")
    try:
        inner = new_repo(tmp, "inner")
        with open(os.path.join(inner, "a.txt"), "w") as fh:
            fh.write("x\n")
        git(inner, "add", "-A", check=True)
        git(inner, "commit", "-qm", "one", check=True)

        outer = new_repo(tmp, "outer")
        with open(os.path.join(outer, "seed"), "w") as fh:
            fh.write("seed\n")
        git(outer, "add", "-A", check=True)
        git(outer, "commit", "-qm", "seed", check=True)

        r = git(outer, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                inner, "vendor/inner")
        if r.returncode != 0:
            FAILURES.append(f"could not create a submodule fixture: {r.stderr.strip()}")
            return

        mode = git(outer, "ls-files", "-s", "vendor/inner").stdout.split()[:1]
        check("the fixture really is a gitlink", mode, ["160000"])

        out, _rc = sh(outer, 'REPO_ROOT=. file_size vendor/inner')
        check("a staged gitlink measures 0", out.splitlines()[-1], "0")

        # And the ordinary case is untouched by the new branch.
        out, _rc = sh(outer, 'REPO_ROOT=. file_size seed')
        check("a staged regular file still measures its true size",
              out.splitlines()[-1], "5")

        # A path that is genuinely not in the index still answers MISSING, so #19 stands:
        # the branch is narrow, and only a gitlink takes the new route.
        out, _rc = sh(outer, 'REPO_ROOT=. file_size no/such/file')
        check("an unstaged path still answers MISSING", out.splitlines()[-1], "MISSING")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_size_rule_still_has_teeth():
    """00-no-binaries must still refuse what it always refused.

    The point of the #22 fix is to stop one specific false positive, not to soften the
    gate: an oversized file and a forbidden extension are both still refused, and an
    unmeasurable size is still reported rather than swallowed.

    And an extension is an extension whatever its case (#47): SETUP.EXE holds the same
    bytes as payload.exe, and 8.3-era Windows payloads are uppercase more often than not.
    The file is small on purpose, so only the extension rule can be what names it.
    """
    tmp = tempfile.mkdtemp(prefix="cigate-")
    try:
        repo = new_repo(tmp, "repo")
        os.makedirs(os.path.join(repo, "ci", "checks"))
        shutil.copy2(LIB, os.path.join(repo, "ci", "lib.sh"))
        shutil.copy2(GATE, os.path.join(repo, "ci", "checks", "00-no-binaries.sh"))
        with open(os.path.join(repo, "big.bin"), "wb") as fh:
            fh.write(b"\0" * (3 * 1024 * 1024))
        with open(os.path.join(repo, "payload.exe"), "wb") as fh:
            fh.write(b"MZ" + b"\0" * 512)
        with open(os.path.join(repo, "SETUP.EXE"), "wb") as fh:
            fh.write(b"MZ" + b"\0" * 512)
        git(repo, "add", "-A", check=True)

        p = subprocess.run(["sh", "ci/checks/00-no-binaries.sh"], cwd=repo,
                           capture_output=True, text=True,
                           env=dict(os.environ, KITCHEN_SCOPE="staged"))
        out = p.stdout + p.stderr
        check("the gate refuses", p.returncode != 0, True)
        check("an oversized file is still TOO_BIG", "big.bin" in out, True)
        check("a Windows binary is still refused by extension", "payload.exe" in out, True)
        check("...and in capitals too (#47)",
              "binary artifact must not be committed: SETUP.EXE" in out, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_stat_without_c_still_refuses_to_run():
    """#19 stays fixed: when stat will not answer, refuse rather than measure zero."""
    tmp = tempfile.mkdtemp(prefix="cistat-")
    try:
        binp = os.path.join(tmp, "bin")
        os.makedirs(binp)
        stub = os.path.join(binp, "stat")
        with open(stub, "w") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        os.chmod(stub, 0o755)
        p = subprocess.run(["sh", "-c", f'. "{LIB}"; echo reached'], cwd=ROOT,
                           capture_output=True, text=True,
                           env=dict(os.environ, PATH=binp + ":" + os.environ["PATH"]))
        out = p.stdout + p.stderr
        check("sourcing refuses", p.returncode != 0, True)
        check("...and says why", "stat -c%s does not work here" in out, True)
        check("...and never reached the caller", "reached" in p.stdout, False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_require_python3_refuses_instead_of_standing_down():
    """A gate that needs the interpreter must not report ok without it.

    45-doc-yaml stood down green on a machine with no python3 at all, while 40-schema next
    door failed on the same machine -- so "python3 missing" meant two different things
    depending on which gate you asked. `exit 0` makes run-checks.sh print `ok` and count the
    gate in "N checks passed", which is the same shape as the git and stat guards above:
    success reported over nothing examined. Measured 2026-09-20.
    """
    tmp = tempfile.mkdtemp(prefix="cipy-")
    try:
        binp = os.path.join(tmp, "bin")
        os.makedirs(binp)
        # Everything ci/lib.sh needs to source, and deliberately not python3.
        for t in ("git", "stat", "sed", "sh", "cat", "grep", "tr"):
            found = shutil.which(t)
            if found:
                os.symlink(os.path.realpath(found), os.path.join(binp, t))
        check("the fixture really has no python3", shutil.which("python3", path=binp), None)

        script = f'. "{LIB}"; require_python3 "NOTHING AT ALL is checked"; echo reached'
        p = subprocess.run(["sh", "-c", script], cwd=ROOT, capture_output=True, text=True,
                           env=dict(os.environ, PATH=binp, NO_COLOR="1"))
        out = p.stdout + p.stderr
        check("the gate refuses", p.returncode != 0, True)
        check("...saying python3 is the reason", "python3 is not installed" in out, True)
        check("...and what was lost, in the caller's words",
              "NOTHING AT ALL is checked" in out, True)
        check("...and never reached the rest of the gate", "reached" in p.stdout, False)

        # And it does not fire when the interpreter is there: a guard that always refuses
        # would be the same defect wearing the other face.
        p = subprocess.run(["sh", "-c", script], cwd=ROOT, capture_output=True, text=True,
                           env=dict(os.environ, NO_COLOR="1"))
        check("with python3 present it returns", p.returncode, 0)
        check("...and the gate carries on", "reached" in p.stdout, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_vendor_gate_sees_inside_the_submodule():
    """Gate 20 fails on an edited or untracked file inside vendor/linux-live (#101).

    It read only `git submodule status`, whose + means a moved pointer, while its message
    claimed to catch "uncommitted local modifications". An edit inside the submodule leaves
    that line unchanged. slax-rpgs measured it on its own copy on 2026-10-01, and on
    2026-10-24 a scratch clone here passed with a line appended to DOC/bundle.txt, and with
    an untracked file.

    Driven against a throwaway repository with a real submodule at vendor/linux-live, the
    way test_the_size_rule_still_has_teeth drives 00-no-binaries.
    - The clean case comes first: a gate that fails a clean tree is the same defect wearing
      the other face.
    - A clone without --recurse-submodules must skip, and not report the parent's files as
      the submodule's. `git -C` in a directory with no .git answers for the parent, measured.
    """
    tmp = tempfile.mkdtemp(prefix="civendor-")
    try:
        upstream = new_repo(tmp, "upstream")
        with open(os.path.join(upstream, "README"), "w") as fh:
            fh.write("one\n")
        git(upstream, "add", "-A", check=True)
        git(upstream, "commit", "-qm", "one", check=True)
        with open(os.path.join(upstream, "README"), "w") as fh:
            fh.write("two\n")
        git(upstream, "commit", "-qam", "two", check=True)

        repo = new_repo(tmp, "repo")
        os.makedirs(os.path.join(repo, "ci", "checks"))
        shutil.copy2(LIB, os.path.join(repo, "ci", "lib.sh"))
        shutil.copy2(VENDOR_GATE, os.path.join(repo, "ci", "checks", "20-vendor-pristine.sh"))
        r = git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                upstream, "vendor/linux-live")
        if r.returncode != 0:
            FAILURES.append(f"could not create a submodule fixture: {r.stderr.strip()}")
            return
        git(repo, "add", "-A", check=True)
        git(repo, "commit", "-qm", "seed", check=True)
        sub = os.path.join(repo, "vendor", "linux-live")

        def gate(where, **extra):
            # REPO_ROOT set, not inherited: run-checks.sh exports this checkout's, and
            # lib.sh keeps an inherited one, so under the unit gate the gate checked THIS
            # repository's clean submodule and passed every case.
            p = subprocess.run(["sh", "ci/checks/20-vendor-pristine.sh"], cwd=where,
                               capture_output=True, text=True,
                               env=dict(os.environ, KITCHEN_SCOPE="tree", NO_COLOR="1",
                                        REPO_ROOT=where, **extra))
            return p.returncode, p.stdout + p.stderr

        rc, out = gate(repo)
        check("a clean submodule passes", (rc, "FAIL" in out), (0, False))
        # ...under a git hook too, which exports the parent's GIT_DIR and GIT_INDEX_FILE.
        # git inside the submodule read them, and the pre-commit hook refused #101's own
        # commit: "fatal: .git/index: index file open failed: Not a directory".
        rc, out = gate(repo, GIT_DIR=".git", GIT_INDEX_FILE=".git/index")
        check("...and under a git hook's GIT_DIR and GIT_INDEX_FILE", (rc, "FAIL" in out),
              (0, False))

        # One run for both halves of rule 3: the message must name each path, so a gate that
        # saw only edits, or only untracked files, still fails this.
        with open(os.path.join(sub, "README"), "a") as fh:
            fh.write("edited\n")
        with open(os.path.join(sub, "NEW"), "w") as fh:
            fh.write("x\n")
        rc, out = gate(repo)
        check("an edited and an untracked file inside fail, each named (#101)",
              (rc != 0, "2 modified or untracked path(s)" in out, "README" in out, "NEW" in out),
              (True, True, True, True))
        git(sub, "checkout", "-q", "--", "README", check=True)
        os.remove(os.path.join(sub, "NEW"))

        git(sub, "checkout", "-q", "HEAD~1", check=True)
        rc, out = gate(repo)
        check("a checkout at another commit still fails",
              (rc != 0, "other than its pin" in out), (True, True))
        git(sub, "checkout", "-q", "-", check=True)

        bare = os.path.join(tmp, "bare")
        git(tmp, "clone", "-q", repo, bare, check=True)
        with open(os.path.join(bare, "stray"), "w") as fh:
            fh.write("y\n")
        rc, out = gate(bare)
        check("a submodule not checked out is skipped, and says so",
              (rc, "not checked out" in out), (0, True))
        check("...without reporting the parent's own file as the submodule's",
              "stray" in out, False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    for fn in [test_a_staged_submodule_measures_zero_not_missing,
               test_the_size_rule_still_has_teeth,
               test_a_stat_without_c_still_refuses_to_run,
               test_require_python3_refuses_instead_of_standing_down,
               test_the_vendor_gate_sees_inside_the_submodule]:
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
    print("tests/unit/test_ci_lib.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
