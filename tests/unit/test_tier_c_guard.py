#!/usr/bin/env python3
"""ci/tier-c.sh's dirty-tree guard, driven with a stub PATH -- no qemu, no KVM, no boot.

WHY THIS EXISTS. The guard refuses to record Tier C evidence from a modified working tree,
because `git describe --always --dirty` is read when the LEDGER IS WRITTEN and a tracked
file edited mid-sweep taints every target that finishes afterwards. Nothing downstream
catches that: ci/checks/97-tier-c-ledger.sh accepts a "-dirty" stamp, so the evidence stops
being reconstructible without anything turning red.

`--allow-dirty` is the exploratory escape hatch, and it has to move BOTH destinations --
the ledger and the golden directory -- because a MISSING golden is created rather than
failed (tests/boot/qemu_boot.py: "this is how a golden is born"). Redirect only the ledger
and a dirty run can still write a golden into tests/boot/golden/.

That is issue #18, and it shipped: one `COMMITTED_DEST` flag cleared by either option is an
OR, while the refusal names both options and says "as well", and the doc says "and". The
guard and its own error message disagreed, and nothing executed either of them.

The guard sits after the tool check and before any work, so a stub PATH is enough to reach
it: the script only needs the four tools to be FOUND, never run. No boot, no root, no
/dev/kvm -- which is why this can be a unit test at all.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
TIER_C = os.path.join(ROOT, "ci", "tier-c.sh")

FAILURES = []

# The four the script checks for before it reaches the guard. Stubbed, never invoked.
STUB_TOOLS = ("qemu-system-x86_64", "xorriso", "mkfs.ext4")


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def stub_path(tmp):
    """A PATH carrying the tools tier-c.sh looks for, plus the real python3 and git.

    python3 and git are NOT stubbed: the script runs `git describe` to decide whether the
    tree is dirty, which is the thing under test.
    """
    d = os.path.join(tmp, "bin")
    os.makedirs(d, exist_ok=True)
    for t in STUB_TOOLS:
        p = os.path.join(d, t)
        with open(p, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(p, 0o755)
    for real in ("python3", "git", "sh", "sed", "grep", "cat", "stat", "date", "mkdir",
                 "rm", "ls", "wc", "tr", "cut", "basename", "dirname", "du", "kill"):
        src = shutil.which(real)
        if src and not os.path.exists(os.path.join(d, real)):
            os.symlink(src, os.path.join(d, real))
    return d


def run_guard(repo, extra_args, tmp):
    """Run tier-c.sh in `repo` and return (rc, combined output).

    --paths '' so that even if the guard let us through, no boot would be attempted.
    """
    env = dict(os.environ, PATH=stub_path(tmp) + ":" + os.environ.get("PATH", ""))
    p = subprocess.run(["sh", os.path.join(repo, "ci", "tier-c.sh")] + extra_args,
                       cwd=repo, env=env, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def fixture_repo(tmp, dirty):
    """A git repo holding a copy of tier-c.sh, clean or dirty as asked.

    Its own repo rather than this checkout: the guard reads `git describe --always --dirty`
    of the tree it runs in, and a test must not depend on whether the developer happens to
    have uncommitted work.
    """
    repo = os.path.join(tmp, "repo-dirty" if dirty else "repo-clean")
    os.makedirs(os.path.join(repo, "ci"), exist_ok=True)
    shutil.copy2(TIER_C, os.path.join(repo, "ci", "tier-c.sh"))
    q = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=repo)
    subprocess.run(["git", "init", "-q"], check=True, **q)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], check=True, **q)
    subprocess.run(["git", "config", "user.name", "t"], check=True, **q)
    subprocess.run(["git", "add", "-A"], check=True, **q)
    subprocess.run(["git", "commit", "-qm", "fixture"], check=True, **q)
    if dirty:
        with open(os.path.join(repo, "ci", "tier-c.sh"), "a") as fh:
            fh.write("\n# a tracked file, modified\n")
    return repo


def test_allow_dirty_needs_both_destinations():
    """The four shapes, of which the third is issue #18.

    Asserted on the REFUSAL TEXT, not just the exit code: tier-c.sh exits 2 for every
    `die`, including "no such image", so a test that only read the status would pass while
    the guard did nothing at all.
    """
    tmp = tempfile.mkdtemp(prefix="tierc-guard-")
    try:
        dirty = fixture_repo(tmp, dirty=True)
        led, gold = os.path.join(tmp, "s.json"), os.path.join(tmp, "sgold")

        rc, out = run_guard(dirty, [], tmp)
        check("dirty tree, no flag: refused", rc != 0, True)
        check("...and says why", "working tree is modified" in out, True)

        rc, out = run_guard(dirty, ["--allow-dirty"], tmp)
        check("dirty + --allow-dirty alone: refused", rc != 0, True)
        check("...naming the committed evidence",
              "would write into the committed evidence" in out, True)

        # ISSUE #18. This passed the guard before the fix, leaving GOLDEN_DIR at
        # tests/boot/golden -- and a missing golden is created, not failed.
        rc, out = run_guard(dirty, ["--allow-dirty", "--ledger", led], tmp)
        check("dirty + --allow-dirty + --ledger ONLY: refused", rc != 0, True)
        check("...and it is the evidence refusal, not some later error",
              "would write into the committed evidence" in out, True)

        # The mirror image: redirecting only the goldens must fail the same way.
        rc, out = run_guard(dirty, ["--allow-dirty", "--golden-dir", gold], tmp)
        check("dirty + --allow-dirty + --golden-dir ONLY: refused", rc != 0, True)
        check("...same refusal", "would write into the committed evidence" in out, True)

        # Both: the guard is satisfied and the script proceeds to its real work, which
        # without an ISO is "no such image". Reaching THAT is the pass condition.
        rc, out = run_guard(
            dirty, ["--allow-dirty", "--ledger", led, "--golden-dir", gold], tmp)
        check("dirty + --allow-dirty + BOTH: guard satisfied",
              "would write into the committed evidence" not in out, True)
        check("...and got past the dirty refusal too",
              "working tree is modified" not in out, True)

        # A clean tree is unaffected: no dirty refusal, with or without redirection.
        clean = fixture_repo(tmp, dirty=False)
        rc, out = run_guard(clean, [], tmp)
        check("clean tree: no dirty refusal", "working tree is modified" not in out, True)
        check("clean tree: no evidence refusal",
              "would write into the committed evidence" not in out, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    for fn in [test_allow_dirty_needs_both_destinations]:
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
    print("tests/unit/test_tier_c_guard.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
