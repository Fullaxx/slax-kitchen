#!/usr/bin/env python3
"""ci/lib.sh's file_size, and the size rule in 00-no-binaries that depends on it.

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

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
LIB = os.path.join(ROOT, "ci", "lib.sh")
GATE = os.path.join(ROOT, "ci", "checks", "00-no-binaries.sh")

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
        git(repo, "add", "-A", check=True)

        p = subprocess.run(["sh", "ci/checks/00-no-binaries.sh"], cwd=repo,
                           capture_output=True, text=True,
                           env=dict(os.environ, KITCHEN_SCOPE="staged"))
        out = p.stdout + p.stderr
        check("the gate refuses", p.returncode != 0, True)
        check("an oversized file is still TOO_BIG", "big.bin" in out, True)
        check("a Windows binary is still refused by extension", "payload.exe" in out, True)
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


def main():
    for fn in [test_a_staged_submodule_measures_zero_not_missing,
               test_the_size_rule_still_has_teeth,
               test_a_stat_without_c_still_refuses_to_run]:
        fn()
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_ci_lib.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
