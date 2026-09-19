#!/usr/bin/env python3
"""ci/checks/97-tier-c-ledger.sh's rule on strings: the ledger records names, not paths.

WHY THIS EXISTS. The gate used to refuse any string that looked like a path on the machine
that ran the boot, by importing HOSTISH -- a regex of home-directory shapes -- from
lib/provenance.py. #26 retired that regex, because the shapes are also the image's own
paths and it never caught anything real. The ledger's rule became the one it already applied
to `iso_name`, extended to every string: no path separator anywhere.

Nothing tested the gate's content rules before this, so that change would have been a claim.
Each case plants a separator in one field of a copy of the committed ledger and requires the
gate to refuse it BY NAME. The first case is one HOSTISH let through -- a relative path has no
leading slash and no home directory in it -- which is the difference between the two rules.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
GATE = os.path.join(ROOT, "ci", "checks", "97-tier-c-ledger.sh")
LEDGER = os.path.join(ROOT, "tests", "boot", "tier-c.json")

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def program():
    """The Python embedded in the gate, taken from the gate itself so the two cannot differ."""
    text = open(GATE).read()
    body = text.split("<<'PY'", 1)[1].split("\n", 1)[1]
    return body.split("\nPY\n", 1)[0]


def run(doc):
    """Run that program on `doc` exactly as the gate does: the ledger's path is its argument."""
    d = tempfile.mkdtemp(prefix="ledger-")
    try:
        path = os.path.join(d, "tier-c.json")
        with open(path, "w") as f:
            json.dump(doc, f)
        r = subprocess.run([sys.executable, "-", path], input=program(),
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_separator_anywhere_is_refused_by_name():
    if not os.path.isfile(LEDGER):
        # The gate passes an absent ledger -- Tier C has not been run -- so there is nothing
        # here to plant into.
        print("  tests/boot/tier-c.json absent - nothing to test against")
        return
    base = json.load(open(LEDGER))
    rc, out = run(base)
    check("the committed ledger passes", rc, 0)
    check("...and says what it counted", "tier-c ledger:" in out, True)

    i = next(n for n, r in enumerate(base["runs"]) if r.get("markers"))
    cases = [
        # HOSTISH passed this one: relative, so no leading slash, and no home directory.
        (f"runs[{i}].golden", lambda d: d["runs"][i].__setitem__(
            "golden", "tests/boot/golden/boot-matrix-debian-64bit-12.2.0.testkit")),
        (f"runs[{i}].iso_name", lambda d: d["runs"][i].__setitem__(
            "iso_name", "/home/someone/out/slax.iso")),
        (f"runs[{i}].markers[0]", lambda d: d["runs"][i]["markers"].__setitem__(
            0, "file /etc/hostname: slax")),
        ("qemu", lambda d: d.__setitem__("qemu", "C:\\qemu\\8.2.2")),
    ]
    for field, plant in cases:
        doc = copy.deepcopy(base)
        plant(doc)
        rc, out = run(doc)
        check(f"a separator in {field} fails the gate", rc, 1)
        check(f"...and the gate names {field}", f"ledger.{field}:" in out, True)


def test_qemu_must_name_a_version():
    """qemu is a fact about the machine that booted, so it has to be one.

    The top level recorded "unknown" whenever the machine running tier-c.sh had no qemu of
    its own, and this gate let it through. Rows now carry their own `qemu`, from the process
    that ran it: allowed, and held to the same rule.
    """
    if not os.path.isfile(LEDGER):
        print("  tests/boot/tier-c.json absent - nothing to test against")
        return
    base = json.load(open(LEDGER))

    doc = copy.deepcopy(base)
    doc["runs"][0]["qemu"] = "8.2.2"
    rc, out = run(doc)
    check("a run may name its own qemu", rc, 0)

    doc = copy.deepcopy(base)
    doc["qemu"] = "unknown"
    rc, out = run(doc)
    check("an unknown qemu at the top fails", rc, 1)
    check("...by name", "qemu: a ledger that cannot say which qemu booted" in out, True)

    doc = copy.deepcopy(base)
    doc["runs"][0]["qemu"] = "unknown"
    rc, out = run(doc)
    check("an unknown qemu in a run fails", rc, 1)
    check("...by name", "runs[0].qemu:" in out, True)


def main():
    # One box for every fixture, removed afterwards: the convention of #25.
    box = tempfile.mkdtemp(prefix="test_tier_c_ledger-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        test_a_separator_anywhere_is_refused_by_name()
        test_qemu_must_name_a_version()
        if FAILURES:
            for f in FAILURES:
                print(f"FAIL {f}", file=sys.stderr)
            return 1
        print("tests/unit/test_tier_c_ledger.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
