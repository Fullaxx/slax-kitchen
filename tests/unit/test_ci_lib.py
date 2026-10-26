#!/usr/bin/env python3
"""ci/lib.sh's file_size, the size rule in 00-no-binaries that depends on it, and
20-vendor-pristine, which holds each submodule to its pointer.

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


def test_the_vendor_gate_holds_each_submodule_to_its_pointer():
    """Gate 20 fails a submodule committed as anything but its pointer, and nothing else (#103).

    Until #103 it read the submodule's working tree: checked out at its pin, and (#101)
    nothing edited or untracked inside. Measured 2026-10-26, that failed a commit leaving a
    moved checkout unstaged, which moved nothing, and passed `git add -A` and `git commit -a`,
    which staged the move. A commit records a pointer or files, never a working tree. Every
    downstream copy had to change the path as well: #101's test failed three of six checks
    against slax-wine's gate on 2026-10-25, until slax-wine adapted it.

    So the fixture names a submodule path of its own in its own .gitmodules, and the gate
    must find it there, as a copy must in any project. The path holds a space, which
    `--get-regexp` uses to separate key and value, and a byte above 127, which
    `git ls-files` quotes without -z: the gate's first draft failed `deps/ünï` (2026-10-26).
    - The clean case comes first: a gate that fails a clean tree is the same defect wearing
      the other face. A moved and edited checkout is clean as far as history goes.
    - The hook case takes the pointer out of an index of the hook's own, the kind
      `git commit -a` makes. #101's fix cleared GIT_INDEX_FILE for git inside the
      submodule; done here, that would check .git/index instead of the commit being made.
    - A .gitmodules git cannot read must fail. `git config --blob` exits 1 for one, the
      same as for one that names nothing, and the gate's first draft passed it (measured
      2026-10-26).
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

        def project(name):
            repo = new_repo(tmp, name)
            os.makedirs(os.path.join(repo, "ci", "checks"))
            shutil.copy2(LIB, os.path.join(repo, "ci", "lib.sh"))
            shutil.copy2(VENDOR_GATE, os.path.join(repo, "ci", "checks",
                                                   "20-vendor-pristine.sh"))
            return repo

        def gate(where, **extra):
            # REPO_ROOT set, not inherited. run-checks.sh exports this checkout's, lib.sh
            # keeps an inherited one, and until #102 the unit gate passed it on, so the gate
            # checked THIS repository. The sandbox clears it now; this holds in a shell that
            # exports one. Staged scope, as at pre-commit: the gate reads the index in either.
            p = subprocess.run(["sh", "ci/checks/20-vendor-pristine.sh"], cwd=where,
                               capture_output=True, text=True,
                               env=dict(os.environ, KITCHEN_SCOPE="staged", NO_COLOR="1",
                                        REPO_ROOT=where, **extra))
            return p.returncode, p.stdout + p.stderr

        repo = project("repo")
        path = "deps/the thïng"
        r = git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                upstream, path)
        if r.returncode != 0:
            FAILURES.append(f"could not create a submodule fixture: {r.stderr.strip()}")
            return
        git(repo, "add", "-A", check=True)
        git(repo, "commit", "-qm", "seed", check=True)
        sub = os.path.join(repo, path)

        rc, out = gate(repo)
        check("a clean submodule passes", (rc, "FAIL" in out), (0, False))

        git(sub, "checkout", "-q", "HEAD~1", check=True)
        with open(os.path.join(sub, "README"), "a") as fh:
            fh.write("edited\n")
        rc, out = gate(repo)
        check("...and so does a moved and edited checkout, which no commit records (#103)",
              (rc, "FAIL" in out), (0, False))
        git(sub, "checkout", "-q", "-f", "-", check=True)

        hook_index = os.path.join(repo, ".git", "hook-index")
        shutil.copy2(os.path.join(repo, ".git", "index"), hook_index)
        git(repo, "rm", "-q", "--cached", path, check=True,
            env=dict(os.environ, GIT_INDEX_FILE=hook_index))
        rc, out = gate(repo, GIT_DIR=".git", GIT_INDEX_FILE=hook_index)
        plain_rc, _ = gate(repo, GIT_DIR=".git")
        check("a pointer missing from a hook's own index fails, named, while .git/index passes",
              (rc != 0, f"{path} is a submodule in .gitmodules, but the index records no pointer"
               in out, plain_rc), (True, True, 0))
        os.remove(hook_index)

        # The vendored tree committed as files: the pointer gone, its files in its place.
        git(repo, "rm", "-q", "--cached", path, check=True)
        shutil.rmtree(sub)
        os.makedirs(os.path.join(sub, "sub"))
        for n in ("a", "b", "c", "d", "sub/e"):
            with open(os.path.join(sub, n), "w") as fh:
                fh.write(n + "\n")
        git(repo, "add", "--", path, check=True)
        rc, out = gate(repo)
        check("its tree committed as files instead fails, with the count and the first names",
              (rc != 0, "committed as 5 file(s) instead of a pointer" in out, f"{path}/a" in out),
              (True, True, True))

        with open(os.path.join(repo, ".gitmodules"), "w") as fh:
            fh.write('[submodule "x"\n\tpath = x\n')
        git(repo, "add", ".gitmodules", check=True)
        rc, out = gate(repo)
        check("a .gitmodules git cannot read fails, and says so",
              (rc != 0, "git cannot read .gitmodules" in out), (True, True))

        plain = project("plain")
        git(plain, "add", "-A", check=True)
        rc, out = gate(plain)
        check("a project with no .gitmodules passes, and says so",
              (rc, "no .gitmodules" in out), (0, True))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    for fn in [test_a_staged_submodule_measures_zero_not_missing,
               test_the_size_rule_still_has_teeth,
               test_a_stat_without_c_still_refuses_to_run,
               test_require_python3_refuses_instead_of_standing_down,
               test_the_vendor_gate_holds_each_submodule_to_its_pointer]:
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
