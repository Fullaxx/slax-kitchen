#!/usr/bin/env python3
"""Unit tests for the boot harness's wait logic, and for what one boot's ledger row records.

`_wait` used to be `time.sleep(seconds)`. That made `--seconds` not a timeout but a bill:
CI paid 240 + 120 + 120 = 480 s per run regardless of what the guest did, and the duration
the harness reported back was the flag it had been given rather than anything it measured.

Polling is only an improvement if it keeps the one property that matters -- a boot missing
an expectation must still FAIL, and must not be let off early. These tests exist because
an over-eager poll would be worse than the sleep it replaced: it would turn a red test
green and do it faster.

Seconds of real time, not milliseconds, because the thing under test is a clock.

But the CEILINGS are this file's choice, not the code's. They were 10/4/3/2/2 and cost
13 s of a gate paid at both pre-commit and pre-push; `_wait` polls every 0.5 s, so 4/2/1/1
exercises the same loop and the same predicates. Every "burned the ceiling" assertion is
`>=`, so a loaded machine overshooting only makes them safer, and both upper bounds keep
at least a second of margin. Re-checked by mutation after retiming, not assumed.
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "qemu_boot", os.path.join(_HERE, "..", "boot", "qemu_boot.py"))
qb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qb)

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def _log():
    d = tempfile.mkdtemp(prefix="qbwait-")
    p = os.path.join(d, "serial.log")
    open(p, "w").close()
    return p


def _append_later(path, delay, text):
    def go():
        time.sleep(delay)
        with open(path, "a") as f:
            f.write(text)
    threading.Thread(target=go, daemon=True).start()


def test_returns_as_soon_as_every_expectation_is_present():
    """The point of the change: stop when the guest has said everything we asked for."""
    log = _log()
    _append_later(log, 0.2, "Looking for slax data\n")
    _append_later(log, 0.7, "Live Kit done, starting slax\n")
    took = qb._wait(log, 4, ["Looking for slax data", "Live Kit done"])
    check("returned early", took < 2.5, True)
    check("did not return before the last marker", took >= 0.7, True)


def test_a_missing_expectation_still_burns_the_whole_ceiling():
    """The property that makes polling safe.

    If this ever returns early, a boot that never reached `Live Kit done` would be
    reported faster AND the run would still fail on the missing string -- but only by
    luck of ordering. Burning the ceiling is what makes the failure honest: we waited as
    long as we promised and it never came.

    IT IS ALSO WHAT PINS `all`, NOT `any`. One expectation here is present and the other
    never appears, so a poll breaking on the first match returns immediately and this
    goes red. `Live Kit done` is the LAST marker livekit prints; returning on the first
    would stop before the stages that follow it. A separate test used to make that point
    with a second pair of markers, and was removed 2026-09-20 after mutation-checking
    showed `all` -> `any` in qemu_boot.py is caught here and by the test above -- the
    same single predicate, asserted twice for 3.0 s.
    """
    log = _log()
    with open(log, "w") as f:
        f.write("Looking for slax data\n")
    took = qb._wait(log, 2, ["Looking for slax data", "this-never-appears"])
    check("waited the full ceiling", took >= 1.9, True)


def test_no_expectations_keeps_the_flat_sleep():
    """The screenshot modes have nothing to poll for.

    `--bios` and `--uefi` produce an EMPTY serial log -- no Slax menu entry sets
    console=ttyS0 -- so there is no text that could ever satisfy a poll. They must keep
    sleeping, or they would return instantly and screendump a black framebuffer.
    """
    log = _log()
    took = qb._wait(log, 1, [])
    check("slept", 0.9 <= took <= 2.0, True)


def test_a_missing_serial_file_is_not_a_crash():
    """QEMU creates the log itself; there is a window before it exists."""
    d = tempfile.mkdtemp(prefix="qbwait-")
    took = qb._wait(os.path.join(d, "never-created.log"), 1, ["anything"])
    check("waited rather than raising", took >= 0.9, True)


def test_qemu_version_is_what_the_binary_says():
    """Asked of the qemu that ran, so a ledger row can name it. Found by pattern rather than
    by word position, and "unknown" -- never a guess -- when it cannot be read."""
    d = tempfile.mkdtemp(prefix="qbver-")

    def stub(name, body):
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(p, 0o755)
        return p

    # Captured from: qemu-system-x86_64 8.2.2 (Debian packaging) and 9.1.0 (upstream)
    check("debian's banner", qb.qemu_version(stub(
        "a", 'echo "QEMU emulator version 8.2.2 (Debian 1:8.2.2+ds-0ubuntu1.17)"; '
             'echo "Copyright (c) 2003-2023 Fabrice Bellard"')), "8.2.2")
    check("a bare one", qb.qemu_version(stub("b", 'echo "QEMU emulator version 9.1.0"')), "9.1.0")
    check("no version in it", qb.qemu_version(stub("c", 'echo "not qemu"')), "unknown")
    check("no such binary", qb.qemu_version(os.path.join(d, "absent")), "unknown")


def test_a_record_names_the_qemu_that_booted():
    """The row beside `accel`: both are facts about the machine that ran the boot, and
    ci/tier-c.sh now takes them from here rather than measuring its own."""
    d = tempfile.mkdtemp(prefix="qbrec-")
    iso = os.path.join(d, "slax.iso")
    with open(iso, "w") as f:
        f.write("x")
    rec = os.path.join(d, "runs.jsonl")
    a = types.SimpleNamespace(label=None, seconds=30, expect=["Live Kit done"], run_tag=None)
    r = {"serial_text": "Live Kit done", "mode": "kernel", "iso": iso, "kvm": True,
         "waited": 4.0, "screenshot_bytes": 0, "qemu": "8.2.2"}
    qb.record(rec, r, a, 0, "match")
    with open(rec) as f:
        row = json.loads(f.read())
    check("the row names its qemu", row.get("qemu"), "8.2.2")
    check("...beside its accel", row.get("accel"), "kvm")


def main():
    # EVERY FIXTURE THIS FILE MAKES GOES IN ONE BOX, AND THE BOX GOES AWAY.
    # both of this file's mkdtemp() calls had no cleanup, so running this file by hand left
    # their trees in /tmp and nothing took them away. ci/checks/80-unit.sh does this
    # for a GATE run; the box is the half that survives running the file directly.
    # Issue #25.
    #
    # Set before the first test, because tempfile.tempdir only steers calls made
    # after it -- and cleared afterwards so a caller that imports this file is not
    # left pointing at a directory that no longer exists.
    #
    # BOTH THE GLOBAL AND THE VARIABLE. tempfile.tempdir steers this process; TMPDIR steers
    # the children, and they are not the same thing. ci/release-assets.sh and three siblings
    # do `mktemp -d "${TMPDIR:-/tmp}/..."`, which reads the variable and never the global, so
    # the global on its own leaves a subprocess's fixture outside the box. Nothing here drives
    # one of those today -- but the box claims every fixture this file makes, and those are
    # fixtures this file made. Under the gate it changes nothing, since the ambient TMPDIR is
    # already the gate's own box; it is the by-hand run that this is for.
    import shutil
    box = tempfile.mkdtemp(prefix="test_qemu_boot-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_returns_as_soon_as_every_expectation_is_present,
                   test_a_missing_expectation_still_burns_the_whole_ceiling,
                   test_no_expectations_keeps_the_flat_sleep,
                   test_a_missing_serial_file_is_not_a_crash,
                   test_qemu_version_is_what_the_binary_says,
                   test_a_record_names_the_qemu_that_booted]:
            fn()
        if FAILURES:
            for f in FAILURES:
                print(f"FAIL {f}", file=sys.stderr)
            return 1
        print("tests/unit/test_qemu_boot.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
