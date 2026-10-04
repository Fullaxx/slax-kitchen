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
import traceback

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


_KNOWN = None


def known_targets():
    """The closed set the gate is handed, obtained the way the gate obtains it.

    Not a literal list here: a fifth hardcoded copy of the four target names is the thing
    #36 was about, and tests/unit/test_release.py already exists to catch the fourth.

    Asked once. run() is called eight times here and the answer cannot change between
    them; a subprocess per call measured 0.2 s of the unit gate, which is paid at
    pre-commit AND pre-push -- CONTRIBUTING's "Cost is part of the argument".
    """
    global _KNOWN
    if _KNOWN is None:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "lib", "target.py"),
                            "--list"], capture_output=True, text=True)
        _KNOWN = " ".join(r.stdout.split())
    return _KNOWN


def run(doc):
    """Run that program on `doc` exactly as the gate does: the ledger's path, then the
    targets that exist. The second argument arrived with #36, when `target` gained the
    closed set that `path`, `result` and `accel` already had."""
    d = tempfile.mkdtemp(prefix="ledger-")
    try:
        path = os.path.join(d, "tier-c.json")
        with open(path, "w") as f:
            json.dump(doc, f)
        r = subprocess.run([sys.executable, "-", path, known_targets()],
                           input=program(), capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_separator_anywhere_is_refused_by_name():
    if not os.path.isfile(LEDGER):
        # NOT A SKIP. The gate is allowed to pass an absent ledger -- Tier C may not have
        # been run -- but THIS file's whole subject is the committed ledger, so without it
        # both tests here reported success having asserted nothing at all. A check with no
        # input cannot fail. Found 2026-09-20.
        FAILURES.append("tests/boot/tier-c.json is absent, so the ledger rules are "
                        "asserted against nothing")
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


def test_a_row_may_say_when_each_marker_appeared():
    """qemu_boot.py records when each expectation first appeared, `seen_s`, since #87. The
    gate's key set is closed, so without the key the next recorded ledger would have been
    refused as an unknown key: found in #87's self-review. Allowed, and held to what it
    claims: each key is a marker the row says it saw, and each value is a time."""
    if not os.path.isfile(LEDGER):
        FAILURES.append("tests/boot/tier-c.json is absent, so the ledger rules are "
                        "asserted against nothing")
        return
    base = json.load(open(LEDGER))
    i = next(n for n, r in enumerate(base["runs"]) if r.get("markers"))
    markers = base["runs"][i]["markers"]

    doc = copy.deepcopy(base)
    doc["runs"][i]["seen_s"] = {m: 1.0 + n for n, m in enumerate(markers)}
    rc, _out = run(doc)
    check("a row may carry seen_s", rc, 0)

    for label, seen, says in (
            ("a marker the row did not see", {"never printed": 2.0}, "is not a marker this run saw"),
            ("a time that is not a number", {markers[0]: "2s"}, "should be seconds"),
            ("a negative time", {markers[0]: -1.0}, "should be seconds")):
        doc = copy.deepcopy(base)
        doc["runs"][i]["seen_s"] = seen
        rc, out = run(doc)
        check(f"seen_s with {label} fails the gate", rc, 1)
        check(f"...and the gate says why ({label})", says in out, True)


def test_qemu_must_name_a_version():
    """qemu is a fact about the machine that booted, so it has to be one.

    The top level recorded "unknown" whenever the machine running tier-c.sh had no qemu of
    its own, and this gate let it through. Rows now carry their own `qemu`, from the process
    that ran it: allowed, and held to the same rule.
    """
    if not os.path.isfile(LEDGER):
        FAILURES.append("tests/boot/tier-c.json is absent, so the ledger rules are "
                        "asserted against nothing")
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
        for fn in [test_a_separator_anywhere_is_refused_by_name,
                   test_qemu_must_name_a_version,
                   test_a_row_may_say_when_each_marker_appeared]:
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
