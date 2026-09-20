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
import contextlib
import importlib.util
import io
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


# ----- the golden, and the verdict ladder that decides pass or fail -----
#
# main() calls boot() and record() through module globals, so replacing boot with a
# function that returns a dict makes every branch below it reachable with plain data --
# no qemu, no QMP, and no sleeps, since _wait is inside boot(). None of what follows
# adds measurably to this file's time.

def drive(tmp, serial="", mode="kernel", shot=100, expect=(), golden=None,
          record=None, died=False, iso_name="x.iso", make_iso=True):
    """qemu_boot.main() over a fake boot. Returns (rc, everything it printed)."""
    iso = os.path.join(tmp, iso_name)
    if make_iso and not os.path.exists(iso):
        with open(iso, "w") as f:
            f.write("not an image")
    r = {"serial_text": serial, "mode": mode, "iso": iso, "kvm": False, "waited": 1.0,
         "screenshot_bytes": shot, "serial": os.path.join(tmp, "s.log"),
         "screenshot": os.path.join(tmp, "s.png"), "qemu": "8.2.2"}
    if died:
        r["died"], r["died_why"] = True, "qemu exited early"
    argv = ["qemu_boot.py", iso, "--mode", mode]
    for e in expect:
        argv += ["--expect", e]
    if golden:
        argv += ["--golden", golden]
    if record:
        argv += ["--record", record]
    saved, out = qb.boot, io.StringIO()
    try:
        qb.boot = lambda *a, **k: r
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = qb.main(argv)
    finally:
        qb.boot = saved
    return rc, out.getvalue()


def fenced(*lines):
    body = "".join(ln + "\n" for ln in lines)
    return ("boot noise\n" + qb.TESTKIT_BEGIN + "\n" + body +
            qb.TESTKIT_END + "\nLive Kit done, starting slax\n")


def test_the_testkit_block_is_what_lies_between_the_fences():
    """testkit_block decides what a golden IS, and nothing tested it.

    It is the one pure function in the harness and every golden -- written or compared --
    goes through it, so a change here silently rewrites what every recorded run means.
    """
    check("the lines between the fences",
          qb.testkit_block(fenced("union: aufs", "kernel: 6.1.38")),
          ["union: aufs", "kernel: 6.1.38"])
    # A volatile line is excluded from both sides, which is what lets a golden be
    # compared at all: perch-marker flips between the two persistence boots by design.
    check("a volatile prefix is dropped",
          qb.testkit_block(fenced("union: aufs", "perch-marker: absent")), ["union: aufs"])
    # testkit builds `bundles:` with `tr '\n' ' '`, so it ends in a space -- and a
    # committed golden with trailing whitespace is refused by ci/checks/70-whitespace.sh.
    check("every line is rstripped", qb.testkit_block(fenced("bundles: 01 02   ")),
          ["bundles: 01 02"])
    check("no BEGIN fence means no block", qb.testkit_block("just boot noise\n"), [])
    check("an empty block is empty", qb.testkit_block(fenced()), [])
    # A blank line between the fences is kept, because a golden is a record of what the
    # image printed and testkit prints no blank lines of its own.
    check("a blank line inside the fences survives",
          qb.testkit_block(fenced("union: aufs", "")), ["union: aufs", ""])
    # Recorded rather than argued: END breaks unconditionally, so an END before any
    # BEGIN truncates everything after it.
    check("an END before any BEGIN ends the scan",
          qb.testkit_block(qb.TESTKIT_END + "\n" + fenced("union: aufs")), [])
    # MEASURED, not assumed, and not what it looks like: a second BEGIN only re-sets the
    # flag, it does not clear what is already collected, so both blocks are kept and run
    # together. Nothing produces two blocks in one serial log today -- one boot, one
    # testkit run -- so this is recorded rather than changed: making the last one win
    # would quietly redefine what every committed golden means.
    check("a second BEGIN does not discard the first block",
          qb.testkit_block("x\n" + qb.TESTKIT_BEGIN + "\nfirst\n" +
                           qb.TESTKIT_BEGIN + "\nsecond\n" + qb.TESTKIT_END + "\n"),
          ["first", "second"])


def test_a_golden_is_born_and_then_agreed_with():
    """Creating one is a PASS; the run after it must match, or creation means nothing."""
    d = tempfile.mkdtemp(prefix="qbgold-")
    g = os.path.join(d, "t.testkit")
    rc, out = drive(d, serial=fenced("union: aufs", "kernel: 6.1.38"), golden=g)
    check("a golden is born on the first run", rc, 0)
    check("...and says so", "does not exist; writing it" in out, True)
    check("...with exactly the block", open(g).read(), "union: aufs\nkernel: 6.1.38\n")

    rc, out = drive(d, serial=fenced("union: aufs", "kernel: 6.1.38"), golden=g)
    check("the next identical run matches", rc, 0)
    check("...and says how many lines", "2 testkit lines match" in out, True)


def test_a_golden_that_differs_names_both_sides():
    d = tempfile.mkdtemp(prefix="qbgold-")
    g = os.path.join(d, "t.testkit")
    with open(g, "w") as f:
        f.write("union: aufs\nkernel: 6.1.38\n")
    rc, out = drive(d, serial=fenced("union: overlay", "kernel: 6.1.38"), golden=g)
    check("a changed image fails", rc, 1)
    check("...naming what the golden had", "- union: aufs" in out, True)
    check("...and what this run produced", "+ union: overlay" in out, True)


def test_a_reordered_golden_does_not_fail_in_silence():
    """The same lines in a different order: FAIL, and nothing else printed.

    The two loops under the FAIL are membership tests, so when the sets agree they both
    print nothing -- while `got == want` still fails the run. The reader is told the
    output "differs" and shown no difference at all.
    """
    d = tempfile.mkdtemp(prefix="qbgold-")
    g = os.path.join(d, "t.testkit")
    with open(g, "w") as f:
        f.write("union: aufs\nkernel: 6.1.38\n")
    rc, out = drive(d, serial=fenced("kernel: 6.1.38", "union: aufs"), golden=g)
    check("a reordering still fails", rc, 1)
    check("...and says it is a reordering", "different order" in out, True)


def test_a_golden_with_trailing_whitespace_is_not_unmatchable():
    """Both sides must be stripped the same way, and they were not.

    testkit_block rstrips every line and its own comment says "Both the write and the
    comparison go through here, so they cannot disagree" -- but the comparison read the
    file with rstrip("\n"), keeping any trailing space. Such a golden could NEVER match,
    whatever the image did. Committed goldens are protected by 70-whitespace.sh; a
    --golden-dir pointing outside the tree is not.
    """
    d = tempfile.mkdtemp(prefix="qbgold-")
    g = os.path.join(d, "t.testkit")
    with open(g, "w") as f:
        f.write("union: aufs   \nkernel: 6.1.38\n")
    rc, out = drive(d, serial=fenced("union: aufs", "kernel: 6.1.38"), golden=g)
    check("trailing space in the golden is not a difference", rc, 0)


def test_a_golden_needs_a_testkit_block_to_compare():
    d = tempfile.mkdtemp(prefix="qbgold-")
    g = os.path.join(d, "t.testkit")
    rc, out = drive(d, serial="booted fine, no testkit recipe applied\n", golden=g)
    check("no block is a failure, not a birth", rc, 1)
    check("...naming the likely cause", "is the testkit recipe applied?" in out, True)
    check("...and no golden was written", os.path.exists(g), False)


def test_the_verdict_ladder():
    """Five independent ways a boot fails, and one thing that is NOT a failure."""
    d = tempfile.mkdtemp(prefix="qbverdict-")
    rc, out = drive(d, mode="bios", shot=0, serial="anything\n")
    check("a menu mode with no screenshot fails", rc, 1)
    check("...saying qemu never reached a display", "no screenshot captured" in out, True)

    rc, _ = drive(d, mode="kernel", shot=0, serial="output\n")
    check("kernel mode is exempt from the screenshot rule", rc, 0)

    rc, out = drive(d, mode="kernel", serial="   \n")
    check("kernel mode with an empty serial log fails", rc, 1)
    check("...saying the kernel said nothing", "serial log is empty" in out, True)

    # OVMF writes its own banner, so a UEFI run that missed the menu still leaves
    # kilobytes of firmware chatter. Emptiness is the wrong test; a livekit marker is.
    rc, out = drive(d, mode="uefi", serial="UEFI firmware chatter\n" * 20,
                    expect=("Live Kit done",))
    check("a menu mode that reached no livekit marker fails", rc, 1)
    check("...and blames the keystrokes, not the image",
          "did not select an entry" in out, True)

    rc, out = drive(d, mode="kernel", serial="Live Kit done, starting slax\n",
                    expect=("Live Kit done", "never-appears"))
    check("a missing expectation fails", rc, 1)
    check("...naming it", "FAIL serial missing 'never-appears'" in out, True)
    check("...and the one that was there is not reported missing",
          "ok   serial contains 'Live Kit done'" in out, True)

    # A guest that vanished mid-run is a note: whatever serial output survived is still
    # asserted, and it either carries the markers or it does not.
    rc, out = drive(d, mode="kernel", serial="Live Kit done, starting slax\n",
                    expect=("Live Kit done",), died=True)
    check("a guest that died early is not itself a failure", rc, 0)
    check("...but it is said out loud", "qemu exited before the harness did" in out, True)


def test_a_refusal_is_exit_2_and_not_a_traceback():
    """ci/tier-c.sh keys on >= 2 meaning NO BOOT HAPPENED, so it writes no ledger."""
    d = tempfile.mkdtemp(prefix="qbrefuse-")
    rc, out = drive(d, iso_name="absent.iso", make_iso=False)
    check("an image that is not there is exit 2", rc, 2)
    check("...naming it", "no such file" in out, True)

    iso = os.path.join(d, "x.iso")
    with open(iso, "w") as f:
        f.write("x")
    saved, buf = qb.boot, io.StringIO()
    try:
        def refuse(*a, **k):
            raise RuntimeError("no MBR signature: not a hybrid image")
        qb.boot = refuse
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = qb.main(["qemu_boot.py", iso, "--mode", "usb"])
    finally:
        qb.boot = saved
    check("a refusal from boot() is exit 2", rc, 2)
    check("...carrying its reason", "no MBR signature" in buf.getvalue(), True)
    check("...and no traceback", "Traceback" in buf.getvalue(), False)


def test_a_row_records_the_verdict_and_the_golden():
    """The ledger's `result` and `golden` come from this run, and 97-tier-c-ledger
    publishes them. Only `qemu` and `accel` were asserted before."""
    d = tempfile.mkdtemp(prefix="qbrow-")
    g = os.path.join(d, "t.testkit")
    rec = os.path.join(d, "runs.jsonl")
    drive(d, serial=fenced("union: aufs"), golden=g, record=rec,
          expect=("Live Kit done",))
    row = json.loads(open(rec).read().strip())
    check("the first run records a born golden", row["golden"], "created")
    check("...and passes", row["result"], "pass")
    check("...listing the markers it saw", row["markers"], ["Live Kit done"])
    check("...and nothing missing", row["missing"], [])

    open(rec, "w").close()
    drive(d, serial=fenced("union: aufs"), golden=g, record=rec,
          expect=("Live Kit done", "never-appears"))
    row = json.loads(open(rec).read().strip())
    check("a matching golden on a failed boot still says match", row["golden"], "match")
    check("...but the row says fail", row["result"], "fail")
    check("...and names what was missing", row["missing"], ["never-appears"])


def main():
    # EVERY FIXTURE THIS FILE MAKES GOES IN ONE BOX, AND THE BOX GOES AWAY.
    # both of this file's 2 mkdtemp() calls had no cleanup on 2026-09-18, so running it by hand left
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
                   test_a_record_names_the_qemu_that_booted,
                   test_the_testkit_block_is_what_lies_between_the_fences,
                   test_a_golden_is_born_and_then_agreed_with,
                   test_a_golden_that_differs_names_both_sides,
                   test_a_reordered_golden_does_not_fail_in_silence,
                   test_a_golden_with_trailing_whitespace_is_not_unmatchable,
                   test_a_golden_needs_a_testkit_block_to_compare,
                   test_the_verdict_ladder,
                   test_a_refusal_is_exit_2_and_not_a_traceback,
                   test_a_row_records_the_verdict_and_the_golden]:
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
