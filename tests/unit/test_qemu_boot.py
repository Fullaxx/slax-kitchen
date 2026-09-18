#!/usr/bin/env python3
"""Unit tests for the boot harness's wait logic.

`_wait` used to be `time.sleep(seconds)`. That made `--seconds` not a timeout but a bill:
CI paid 240 + 120 + 120 = 480 s per run regardless of what the guest did, and the duration
the harness reported back was the flag it had been given rather than anything it measured.

Polling is only an improvement if it keeps the one property that matters -- a boot missing
an expectation must still FAIL, and must not be let off early. These tests exist because
an over-eager poll would be worse than the sleep it replaced: it would turn a red test
green and do it faster.

Seconds of real time, not milliseconds, because the thing under test is a clock.
"""
import importlib.util
import os
import sys
import tempfile
import threading
import time

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
    _append_later(log, 0.5, "Looking for slax data\n")
    _append_later(log, 1.5, "Live Kit done, starting slax\n")
    took = qb._wait(log, 10, ["Looking for slax data", "Live Kit done"])
    check("returned early", took < 5, True)
    check("did not return before the last marker", took >= 1.5, True)


def test_a_missing_expectation_still_burns_the_whole_ceiling():
    """The property that makes polling safe.

    If this ever returns early, a boot that never reached `Live Kit done` would be
    reported faster AND the run would still fail on the missing string -- but only by
    luck of ordering. Burning the ceiling is what makes the failure honest: we waited as
    long as we promised and it never came.
    """
    log = _log()
    with open(log, "w") as f:
        f.write("Looking for slax data\n")
    took = qb._wait(log, 4, ["Looking for slax data", "this-never-appears"])
    check("waited the full ceiling", took >= 3.9, True)


def test_a_partial_match_does_not_satisfy():
    """`all`, not `any`. `Live Kit done` is the LAST marker livekit prints.

    Returning on the first expectation would stop before the stages that follow it, which
    is precisely the coverage this test exists to protect.
    """
    log = _log()
    with open(log, "w") as f:
        f.write("Live Kit done, starting slax\n")
    took = qb._wait(log, 3, ["Live Kit done", "Mounting bundles"])
    check("did not return on a partial match", took >= 2.9, True)


def test_no_expectations_keeps_the_flat_sleep():
    """The screenshot modes have nothing to poll for.

    `--bios` and `--uefi` produce an EMPTY serial log -- no Slax menu entry sets
    console=ttyS0 -- so there is no text that could ever satisfy a poll. They must keep
    sleeping, or they would return instantly and screendump a black framebuffer.
    """
    log = _log()
    took = qb._wait(log, 2, [])
    check("slept", 1.9 <= took <= 3.0, True)


def test_a_missing_serial_file_is_not_a_crash():
    """QEMU creates the log itself; there is a window before it exists."""
    d = tempfile.mkdtemp(prefix="qbwait-")
    took = qb._wait(os.path.join(d, "never-created.log"), 2, ["anything"])
    check("waited rather than raising", took >= 1.9, True)


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
                   test_a_partial_match_does_not_satisfy,
                   test_no_expectations_keeps_the_flat_sleep,
                   test_a_missing_serial_file_is_not_a_crash]:
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
