#!/usr/bin/env python3
"""Run one unit test file, and report any test in it that did not run.

    python3 ci/unit-run.py tests/unit/test_apply.py

WHY. Most files here keep a hand-maintained list of their test functions in `main()`.
That list can lose an entry while the function stays in the file, and nothing disagrees:
the suite still prints "all checks passed", because the check that would have failed was
never called. The failure is silent at exactly the moment somebody believes they have
added coverage.

WHY NOT READ THE FILE. ci/checks/80-unit.sh asked this question statically, twice, and
had a hole both times. Grepping for the name counted a mention in a comment or a
docstring (2026-09-20). Parsing for an `ast.Name` counted any reference in code -- so a
name left behind in a list nothing iterates, which is what a half-finished edit to that
list looks like, still read as registered:

    _LEFTOVER = [test_the_two_tables_agree]     # never called, and never reported

Each fix made the inference sharper without making it true. What is actually being asked
is "did this function run", which is not a question about the source text at all. So it
is measured: sys.setprofile fires on every call, the run's own entries are recorded, and
what is left over is named. The same move as asking Python for its temporary directory
rather than assuming /tmp (ci/tier-c.sh, 9595de4).

A file that discovers its tests from globals() needs no exemption here. It never could
have this bug, and now it does not need to be recognised either -- it simply runs them
all, and the measurement sees that.

DELIBERATE SKIPS say so, with exit 77 (the autotools convention, and not a status any
test here returns by accident). tests/unit/test_build_busybox.py skips its two tests when
there is no tar. Before this, the gate could not tell that from a pass, because a skipped
file exited 0 like everything else.
"""
from __future__ import annotations

import ast
import os
import runpy
import sys

SKIP_STATUS = 77


def defined_tests(path: str) -> list[str]:
    """Module-level `test_*` functions, in source order.

    Module level only, as the gate's own rule was: a nested helper called test_something
    is not a registered test and never was one.
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: unit-run.py <test file> [args...]", file=sys.stderr)
        return 2
    path = os.path.abspath(argv[1])
    try:
        want = set(defined_tests(path))
    except (OSError, SyntaxError) as e:
        # Fail closed: a file whose tests cannot be accounted for is not a file that
        # passed. ci/lib.sh states the same rule for a git that will not answer.
        print(f"{os.path.basename(path)}: cannot be parsed, so its tests cannot be "
              f"accounted for: {e}", file=sys.stderr)
        return 2

    entered: set = set()

    def hook(frame, event, _arg):
        # co_filename, not the function name alone: a test in one file calling a
        # same-named helper imported from another must not mark this file's test as run.
        if event == "call" and frame.f_code.co_filename == path:
            entered.add(frame.f_code.co_name)
            if want <= entered:
                # Nothing left to see. The hook is called for every call in every module,
                # which is what the measurement costs, so it stops as soon as the answer
                # can no longer change.
                sys.setprofile(None)

    sys.argv = [path] + list(argv[2:])
    rc = 0
    sys.setprofile(hook)
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit as e:
        rc = 0 if e.code is None else e.code
    finally:
        # Off before anything is printed, so the reporting below is not itself profiled.
        sys.setprofile(None)

    if rc == SKIP_STATUS:
        print(f"{os.path.basename(path)}: skipped")
        return 0
    missed = sorted(want - entered)
    if missed and rc == 0:
        # Only over a pass. A file that failed has a real failure to report, and a test
        # after the failing one may legitimately not have been reached.
        for name in missed:
            print(f"{os.path.basename(path)}: {name} is defined but never ran",
                  file=sys.stderr)
        return 1
    return rc if isinstance(rc, int) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
