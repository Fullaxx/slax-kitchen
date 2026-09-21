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
import re
import runpy
import sys
import traceback

SKIP_STATUS = 77


def defined_tests(tree: ast.Module) -> list[str]:
    """Module-level `test_*` functions, in source order.

    Module level only, as the gate's own rule was: a nested helper called test_something
    is not a registered test and never was one.
    """
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")]


def disabled_tests(tree: ast.Module) -> dict:
    """What a file declares it has switched off, and the reason each entry gives.

        DISABLED = {"test_the_thing": "#33 - no evidence this ever bit; justify to re-enable"}

    WHY THIS EXISTS AT ALL. A test that fails and has no incident behind it is supposed to
    be disabled and argued back through an issue, rather than adjusted until it passes --
    CONTRIBUTING.md, "What a test here is for", question 4. But the check above fails any
    test that is defined and never runs, so obeying that rule would turn the gate red, and
    the only way to switch a test off would be to delete it. Deleting it loses the one
    thing the rule is for: the trail back to why.

    THE ISSUE NUMBER IS THE POINT, not the entry. A disable nobody has to justify is just a
    quieter way of deleting the test, so an entry with no `#N` in it fails the gate exactly
    as an undeclared one does. `grep -rn DISABLED tests/` is then the list of tests waiting
    on somebody, which is a list that should be short and visible.
    """
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "DISABLED" for t in node.targets):
            continue
        if isinstance(node.value, ast.Dict):
            for key, val in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and isinstance(val, ast.Constant):
                    out[str(key.value)] = str(val.value)
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: unit-run.py <test file> [args...]", file=sys.stderr)
        return 2
    path = os.path.abspath(argv[1])
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), path)
        want = set(defined_tests(tree))
        disabled = disabled_tests(tree)
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
    except Exception:                          # noqa: BLE001
        # THE FILE ITSELF FELL OVER -- a crash outside its own per-test guard, in main()
        # or at import, or a file written without one. Caught rather than allowed to take
        # this runner down with it: an uncaught exception here exits through unit-run.py,
        # whose traceback names unit-run.py, and the count of tests that never ran -- the
        # fact this branch exists to report -- is never reached at all. Not BaseException,
        # so a KeyboardInterrupt still stops everything.
        traceback.print_exc()
        rc = 1
    finally:
        # Off before anything is printed, so the reporting below is not itself profiled.
        sys.setprofile(None)

    if rc == SKIP_STATUS:
        print(f"{os.path.basename(path)}: skipped")
        return 0
    if rc != 0:
        # Only the "never registered" verdict is withheld over a failure: a file that
        # failed has a real failure to report, and a test after the failing one may
        # legitimately not have been reached.
        #
        # BUT SAY HOW MANY, because that is the difference between a list of failures and
        # a list of the failures that fit. Each file guards its own loop so one crash does
        # not stop the rest (CONTRIBUTING, "What a test here is for"), and this is what
        # catches the residual: a crash in main() itself, outside that loop, or a file
        # written without the guard. Information, not a second failure -- the file has
        # already failed.
        missed = len(want - entered)
        if missed:
            print(f"{os.path.basename(path)}: {missed} of {len(want)} tests did not run "
                  f"-- the file stopped early", file=sys.stderr)
        return rc if isinstance(rc, int) else 1

    base = os.path.basename(path)
    problems = 0
    for name in sorted(want - entered):
        why = disabled.get(name)
        if why is None:
            print(f"{base}: {name} is defined but never ran", file=sys.stderr)
            problems += 1
        elif not re.search(r"#\d+", why):
            print(f"{base}: {name} is DISABLED with no issue to argue it back: {why!r}",
                  file=sys.stderr)
            problems += 1
        else:
            # Said out loud on every run. A disabled test that nobody is reminded of is a
            # deleted test with extra steps.
            print(f"{base}: {name} is disabled -- {why}")
    for name in sorted(set(disabled) & entered):
        print(f"{base}: {name} is listed in DISABLED but ran; drop the entry")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
