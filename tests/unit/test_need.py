#!/usr/bin/env python3
"""lib/need.py, and the commands that now check their tools before they start.

WHY THIS EXISTS. The commands that only READ an image -- `kitchen test --structure`
(iso_assert.py), `diff`, `sources`, `probe`, `fingerprint` -- ran xorriso, unsquashfs, xz,
cpio and `file` unchecked, and so did two more found later: `pack`'s dpkg-database step
(unsquashfs, mksquashfs) and `upstream-diff` (git). A missing tool surfaced as a Python
traceback from deep inside: a line number, not the package that fixes it. Each now calls
need.require() first and refuses, exit 2, naming the package.

Two copies of the tool-to-package table exist -- kitchen's TOOLS (shell, for doctor and
kitchen test) and need.TOOL_PKG (Python) -- so the test here that they agree is what keeps
a fix in one from quietly disagreeing with the other.

Run directly: python3 tests/unit/test_need.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import need  # noqa: E402
import traceback

FAILURES = []

# Every command that used to run its tools unchecked, with arguments that get it past
# argparse, and every tool it must name when none is installed. Nothing here needs the image
# to exist: the check comes before the image is opened.
#
# The last three were found by the self-review of the change that introduced this file:
# `sources` runs git on every run (provenance.git_digest), not only for --fetch; `pack`
# runs dpkgdb.py, which runs unsquashfs and mksquashfs; `upstream-diff` runs git.
FINGERPRINT = ["xorriso", "unsquashfs", "xz", "cpio", "file"]
COMMANDS = [
    ("kitchen diff", ["lib/diff.py", "a.iso", "b.iso"], ["xorriso"]),
    ("kitchen probe", ["lib/probe.py", "a.iso"], FINGERPRINT),
    ("kitchen fingerprint", ["lib/fingerprint.py", "a.iso"], FINGERPRINT),
    ("iso_assert.py", ["tests/structure/iso_assert.py", "a.iso"], ["xorriso"]),
    ("kitchen sources", ["lib/sources.py", "a.iso"], ["xorriso", "git"]),
    ("kitchen pack", ["lib/dpkgdb.py", "tree"], ["unsquashfs", "mksquashfs"]),
    ("kitchen upstream-diff", ["lib/upstream_diff.py"], ["git"]),
]


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def kitchen_tools() -> dict:
    """kitchen's TOOLS table as {tool: package}, read from the script itself."""
    text = open(os.path.join(ROOT, "kitchen")).read()
    body = text.split("TOOLS='", 1)[1].split("'", 1)[0]
    return {ln.split("|")[0]: ln.split("|")[1] for ln in body.splitlines() if "|" in ln}


def test_the_two_tables_agree():
    shell, py = kitchen_tools(), need.TOOL_PKG
    both = sorted(set(shell) & set(py))
    check("the tables share tools to compare", len(both) >= 10, True)
    for tool in both:
        check(f"{tool}: kitchen's TOOLS and need.TOOL_PKG name one package", shell[tool], py[tool])


def test_missing_names_the_package():
    box = tempfile.mkdtemp(prefix="need-")
    saved = os.environ.get("PATH", "")
    try:
        stub = os.path.join(box, "xorriso")
        with open(stub, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(stub, 0o755)
        os.environ["PATH"] = box
        check("a tool on PATH is not missing", need.missing(["xorriso"]), [])
        check("one that is not is, with its package", need.missing(["unsquashfs"]),
              ["unsquashfs is not installed (apt-get install squashfs-tools)"])
        check("each once, in the order asked", need.missing(["cpio", "xz", "cpio"]),
              ["cpio is not installed (apt-get install cpio)",
               "xz is not installed (apt-get install xz-utils)"])
        check("a tool the table does not know is still named",
              need.missing(["no-such-tool"]), ["no-such-tool is not installed"])
    finally:
        os.environ["PATH"] = saved
        shutil.rmtree(box, ignore_errors=True)


def test_every_command_refuses_instead_of_crashing():
    """With nothing on PATH, each command exits 2 and names every tool it needs with its
    package -- it used to get as far as running the first, and died in a traceback."""
    empty = tempfile.mkdtemp(prefix="need-empty-")
    try:
        for who, args, tools in COMMANDS:
            p = subprocess.run([sys.executable] + [os.path.join(ROOT, args[0])] + args[1:],
                               cwd=empty, env={"PATH": empty, "HOME": empty},
                               capture_output=True, text=True, timeout=60)
            out = p.stdout + p.stderr
            check(f"{who}: exits 2", p.returncode, 2)
            for tool in tools:
                check(f"{who}: names {tool} and its package",
                      f"{who}: {tool} is not installed (apt-get install "
                      f"{need.TOOL_PKG[tool]})" in out, True)
            check(f"{who}: no traceback", "Traceback" in out, False)
    finally:
        shutil.rmtree(empty, ignore_errors=True)


def main():
    # One box for every fixture, removed afterwards: the convention of #25.
    box = tempfile.mkdtemp(prefix="test_need-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_the_two_tables_agree, test_missing_names_the_package,
                   test_every_command_refuses_instead_of_crashing]:
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
        print("tests/unit/test_need.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
