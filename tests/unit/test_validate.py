#!/usr/bin/env python3
"""Unit tests for lib/validate.py's cross-checks -- the rules the JSON Schema cannot state.

The rule under test: a recipe that removes or renumbers a bundle may contain nothing else.
A recipe that does both hides a deletion inside an addition, which is how `all-browsers`
came to force its own position in a profile: it removed 05-chromium as its first step, so
check_plan_order refused any plan that listed it after another bundle had been built. With
removal in its own recipe, additive recipes compose in any order.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import validate  # noqa: E402

FAILURES = []

HEAD = """apiVersion: slax-kitchen/v1
kind: Recipe
metadata:
  name: {name}
  summary: A recipe written for this test, long enough to look real
compat:
  flavours: [debian]
  arch: [64bit]
  privilege: none
steps:
"""

REMOVE = '  - verb: bundle.remove\n    match: "^05-chromium\\\\.sb$"\n'
RENUMBER = '  - verb: bundle.renumber\n    match: "^05-chromium\\\\.sb$"\n    to: "95"\n'
PACKAGES = "  - verb: bundle.packages\n    bundle: 12-tools\n    packages: [tmux]\n"
SCRIPT = '  - verb: bundle.script\n    bundle: 12-tools\n    script: |\n      #!/bin/sh\n      true\n'
CMDLINE = "  - verb: boot.cmdline\n    append: [quiet]\n"


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def problems(steps, name="scratch"):
    """validate_file() on a recipe built from these step blocks."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, f"{name}.yaml")
    with open(path, "w") as f:
        f.write(HEAD.format(name=name) + "".join(steps))
    try:
        return validate.validate_file(path)
    finally:
        os.unlink(path)
        os.rmdir(d)


def test_a_recipe_that_removes_may_do_nothing_else():
    check("removal alone is fine", problems([REMOVE]), [])
    check("renumber alone is fine", problems([RENUMBER]), [])
    check("adding alone is fine", problems([PACKAGES]), [])

    for label, steps in (("remove then build", [REMOVE, PACKAGES]),
                         ("build then remove", [PACKAGES, REMOVE]),
                         ("remove then script", [REMOVE, SCRIPT]),
                         ("renumber then build", [RENUMBER, PACKAGES]),
                         # "nothing else" means nothing else, not "no bundle verbs".
                         ("remove plus a boot edit", [REMOVE, CMDLINE])):
        got = problems(steps)
        check(f"{label} is refused", len(got), 1)
        said = " ".join(got)
        check(f"{label} says what to do", "remove-bundle" in said and "nothing else" in said, True)


def test_every_shipped_recipe_passes():
    """The rule is only worth having if the tree obeys it."""
    import glob
    for f in sorted(glob.glob(os.path.join(ROOT, "recipes", "available", "*.yaml"))):
        got = validate.validate_file(f)
        check(f"shipped recipe {os.path.basename(f)}", got, [])


def main():
    for fn in [test_a_recipe_that_removes_may_do_nothing_else,
               test_every_shipped_recipe_passes]:
        fn()
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_validate.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
