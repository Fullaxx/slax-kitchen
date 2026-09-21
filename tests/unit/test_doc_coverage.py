#!/usr/bin/env python3
"""ci/checks/90-doc-coverage.sh's target-count rule, driven over throwaway repositories.

WHY THIS EXISTS. That gate carries four prose-count rules and, until this file, no test for
any of them -- which is an odd gap for the gate whose entire job is catching numbers that
stopped being true. The target rule is the one worth starting with: on 2026-09-20 the build
matrix count was stated on 45 lines across 33 files with nothing checking one of them, so a
fifth target would have falsified 33 files in a single commit in silence.

AND THE FIRST DRAFT OF THAT RULE COULD NOT FAIL ON THE LINE THAT MATTERS MOST. It asked
"does this line mention the right word anywhere", the way the gate and upstream-issue rules
do, and `\\bfour\\b` matches inside "Twenty-four". docs/50-cookbook/README.md:116 opens
"**Twenty-four of the thirty-five work on all four targets**", so that line was exempt from
the check whatever its target count said. Found by planting a wrong number in that exact
line, not by reading the regex -- which is why the shape is pinned here.

Driven against throwaway repositories rather than this checkout, the way test_ci_lib.py
drives 00-no-binaries: the cases need a fifth fingerprint and deliberately wrong prose, and
neither belongs in this tree.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
LIB = os.path.join(ROOT, "ci", "lib.sh")
GATE = os.path.join(ROOT, "ci", "checks", "90-doc-coverage.sh")

FAILURES = []

FINGERPRINT = "apiVersion: slax-kitchen/v1\nkind: Fingerprint\nmetadata:\n  name: {}\n"


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)


def run_gate(tmp, fingerprints, prose, sole_markdown=False):
    """Build a fixture repo with N fingerprints and one doc, and run the real gate in it.

    sole_markdown puts the prose in the cookbook index instead of a second page, so the file
    list grep is handed exactly one file -- the case where grep drops the filename prefix.
    """
    repo = os.path.join(tmp, "repo")
    for d in ("ci/checks", "compat", "recipes/available", "docs/50-cookbook"):
        os.makedirs(os.path.join(repo, d))
    for a in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
              ["config", "user.name", "t"]):
        git(repo, *a)
    shutil.copy2(LIB, os.path.join(repo, "ci", "lib.sh"))
    shutil.copy2(GATE, os.path.join(repo, "ci", "checks", "90-doc-coverage.sh"))
    for i in range(fingerprints):
        with open(os.path.join(repo, "compat", f"t{i}.yaml"), "w") as fh:
            fh.write(FINGERPRINT.format(f"target-{i}"))
    # The bijection rules above the count rules need these two to exist; with no recipes
    # they loop over nothing, and the recipe count is 0, which numword() declines to word.
    body = prose if prose.endswith("\n") else prose + "\n"
    with open(os.path.join(repo, "docs", "50-cookbook", "README.md"), "w") as fh:
        fh.write("# Cookbook\n" + (body if sole_markdown else ""))
    if not sole_markdown:
        with open(os.path.join(repo, "docs", "page.md"), "w") as fh:
            fh.write(body)
    git(repo, "add", "-A")
    p = subprocess.run(["sh", "ci/checks/90-doc-coverage.sh"], cwd=repo, capture_output=True,
                       text=True, env=dict(os.environ, KITCHEN_SCOPE="tree",
                                           REPO_ROOT=repo, NO_COLOR="1"))
    return p.returncode, p.stdout + p.stderr


def test_the_right_count_passes_and_a_wrong_one_does_not():
    """Both directions. A rule that only ever passes is the defect it exists to catch."""
    tmp = tempfile.mkdtemp(prefix="doccov-")
    try:
        rc, out = run_gate(tmp, 4, "Built on all four targets.")
        check("four fingerprints and 'four targets' is clean", rc, 0)
        shutil.rmtree(os.path.join(tmp, "repo"))

        rc, out = run_gate(tmp, 5, "Built on all four targets.")
        check("a fifth fingerprint makes the same line fail", rc != 0, True)
        check("...saying what it read", "says 'four targets'" in out, True)
        check("...and what it should be", "want 'five'" in out, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_line_naming_the_right_word_elsewhere_is_still_checked():
    """The case that defeated the first draft, pinned so it cannot come back.

    "Twenty-four" contains "four", so a rule asking whether the line mentions the right word
    anywhere exempts this line however wrong its target count is. The number immediately
    before "targets" is the question being asked, so that is what is read.
    """
    tmp = tempfile.mkdtemp(prefix="doccov-")
    try:
        rc, out = run_gate(tmp, 4, "**Twenty-four of the thirty-five work on all five targets.**")
        check("the wrong count is caught despite 'Twenty-four'", rc != 0, True)
        check("...and is read as five, not four", "says 'five targets'" in out, True)
        shutil.rmtree(os.path.join(tmp, "repo"))

        # ...and a hyphenated pair is one token, not its second half.
        rc, out = run_gate(tmp, 4, "Built on all twenty-four targets.")
        check("a hyphenated number is not read as its tail",
              "says 'twenty-four targets'" in out, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_singular_is_a_different_claim_and_is_left_alone():
    """"booted on one target" is the verification ladder, not the matrix count.

    The plural is what separates them, and it is the only anchor this rule has -- so a rule
    that fired on the singular would make the ladder's own phrase unwritable.
    """
    tmp = tempfile.mkdtemp(prefix="doccov-")
    try:
        rc, out = run_gate(tmp, 4, "Class D - one target only, and booted on one target.")
        check("the singular does not fire", rc, 0)
        shutil.rmtree(os.path.join(tmp, "repo"))

        rc, out = run_gate(tmp, 4, "The 4-target matrix, and a 4-targets compound.")
        check("the digit compound passes when right", rc, 0)
        shutil.rmtree(os.path.join(tmp, "repo"))

        rc, out = run_gate(tmp, 5, "The 4-target matrix.")
        check("...and fails when wrong", "says '4 targets'" in out, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_one_markdown_file_is_still_read_correctly():
    """grep drops the filename when it is handed exactly one file, and the parse splits on it.

    Reachable: a tree holding only docs/50-cookbook/README.md lists one file, and xargs may
    end a batch on one. Without -H that line parses as file="12", line="four targets", and
    the rule reports nonsense rather than a finding -- a gate that has stopped working while
    still exiting non-zero. Found by self-review on 2026-09-20, before it could bite.
    """
    tmp = tempfile.mkdtemp(prefix="doccov-")
    try:
        rc, out = run_gate(tmp, 5, "Built on all four targets.", sole_markdown=True)
        check("the finding still appears", rc != 0, True)
        check("...naming the file it is in", "docs/50-cookbook/README.md:" in out, True)
        check("...and not a line number in its place", "FAIL 2:" in out, False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    for fn in [test_the_right_count_passes_and_a_wrong_one_does_not,
               test_a_line_naming_the_right_word_elsewhere_is_still_checked,
               test_the_singular_is_a_different_claim_and_is_left_alone,
               test_one_markdown_file_is_still_read_correctly]:
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
    print("tests/unit/test_doc_coverage.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
