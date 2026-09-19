#!/usr/bin/env python3
"""ci/tier-c.sh run end to end against a stub `kitchen` -- no qemu, no KVM, no image.

tests/unit/test_tier_c_guard.py stops at the dirty-tree guard. This goes the whole way: the
tier-c.sh under test runs its boot loop, its ledger step and its leak checks, in a throwaway
repository whose `kitchen` is a stub that behaves like `kitchen test` from the outside --
it appends a ledger row to --record, and makes whatever mess a case asks it to.

WHY THE QMP LEAK CHECK IS HERE. tier-c.sh counted qmp socket directories in /tmp, but
qemu_boot.py makes them with tempfile.mkdtemp(prefix="qb-"), which honours $TMPDIR. With
TMPDIR set anywhere else the check compared two counts of nothing and printed "clean" -- a
check that could not fail. The stub leaks its directory the way the harness would, through
tempfile itself, so this fails for exactly that reason when the check looks in the wrong
place.

WHY THE LEDGER'S HOST FACTS ARE HERE. Its `accel` and `qemu` were measured on the machine
running tier-c.sh -- right only while that is the machine that booted. They now come from
the rows, which qemu_boot.py writes beside the qemu it ran; the stub's rows and the stub qemu
on PATH disagree on purpose, so a ledger copying the wrong one says so.

Run directly: python3 tests/unit/test_tier_c_run.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
TIER_C = os.path.join(ROOT, "ci", "tier-c.sh")

FAILURES = []

# The stub `kitchen`. `test` behaves like kitchen test seen from tier-c.sh: one JSON row per
# boot appended to --record, carrying accel and qemu as qemu_boot.py writes them.
#   $STUB_LEAK_QB     leave a qmp directory behind the way qemu_boot.py would, by asking
#                     tempfile for one and never removing it
#   $STUB_QEMU        the qemu version rows name (default 8.2.2); "-" writes no qemu at
#                     all, as a qemu_boot.py from before rows carried one
#   $STUB_TCG_PATH    the one path whose row says tcg
STUB_KITCHEN = r'''#!/bin/sh
case "$1" in
    version) echo "kitchen 0.1.0-dev (stub)"; exit 0 ;;
    test) ;;
    *) echo "stub kitchen: unexpected $*" >&2; exit 2 ;;
esac
shift
iso="" path="" record=""
while [ $# -gt 0 ]; do
    case "$1" in
        --record) record=$2; shift 2 ;;
        --out|--seconds|--golden) shift 2 ;;
        --kernel|--bios|--uefi|--usb|--persistence) path=${1#--}; shift ;;
        *) iso=$1; shift ;;
    esac
done
[ -n "${STUB_LEAK_QB:-}" ] && python3 -c 'import tempfile; tempfile.mkdtemp(prefix="qb-")'
accel=kvm
[ "$path" = "${STUB_TCG_PATH:-}" ] && accel=tcg
qemu=${STUB_QEMU:-8.2.2}
if [ "$qemu" = - ]; then qemu_field=""; else qemu_field=", \"qemu\": \"$qemu\""; fi
printf '{"path": "%s", "iso_name": "%s", "iso_bytes": 1, "accel": "%s"%s, "markers": [], "missing": [], "result": "pass"}\n' \
    "$path" "$(basename "$iso")" "$accel" "$qemu_field" >> "$record"
'''

# Found by tier-c.sh's tool check. The qemu answers 9.9.9 when asked its version, which no
# row ever says -- so a ledger naming 9.9.9 measured this machine instead of the one that
# booted.
STUB_TOOLS = {
    "qemu-system-x86_64": '#!/bin/sh\necho "QEMU emulator version 9.9.9 (stub)"\n',
    "xorriso": "#!/bin/sh\nexit 0\n",
    "mkfs.ext4": "#!/bin/sh\nexit 0\n",
}


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, mode)


def fixture(tmp):
    """A committed, clean repository holding tier-c.sh and the stub kitchen, an image to
    name, and a bin/ of stub tools. Its own repository, so the dirty-tree guard reads a tree
    this test controls rather than whatever the developer has uncommitted."""
    repo = os.path.join(tmp, "repo")
    write(os.path.join(repo, "ci", "tier-c.sh"), open(TIER_C).read(), 0o755)
    write(os.path.join(repo, "kitchen"), STUB_KITCHEN, 0o755)
    # Everything tier-c.sh writes is sent outside the repository, so nothing here can make
    # the tree look modified.
    write(os.path.join(repo, ".gitignore"), "out/\n")
    q = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=repo, check=True)
    subprocess.run(["git", "init", "-q"], **q)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], **q)
    subprocess.run(["git", "config", "user.name", "t"], **q)
    subprocess.run(["git", "add", "-A"], **q)
    subprocess.run(["git", "commit", "-qm", "fixture"], **q)
    iso = os.path.join(tmp, "slax-stub.iso")
    write(iso, "not an image\n")
    bindir = os.path.join(tmp, "bin")
    for name, body in STUB_TOOLS.items():
        write(os.path.join(bindir, name), body, 0o755)
    return repo, iso, bindir


def run_tier_c(tmp, env_extra, paths="kernel"):
    repo, iso, bindir = fixture(tmp)
    out = os.path.join(tmp, "evidence")
    env = dict(os.environ, PATH=bindir + ":" + os.environ.get("PATH", ""), **env_extra)
    p = subprocess.run(
        ["sh", os.path.join(repo, "ci", "tier-c.sh"), "--iso", iso, "--target", "stub-target",
         "--paths", paths, "--out", out, "--ledger", os.path.join(tmp, "ledger.json"),
         "--golden-dir", os.path.join(tmp, "golden")],
        cwd=repo, env=env, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def ledger_of(tmp):
    path = os.path.join(tmp, "ledger.json")
    return json.load(open(path)) if os.path.exists(path) else None


def in_a_box(fn):
    """A fresh fixture directory, with a TMPDIR inside it, for one case; removed after."""
    def run():
        tmp = tempfile.mkdtemp(prefix="tierc-run-")
        try:
            box = os.path.join(tmp, "tmpdir")
            os.makedirs(box)
            fn(tmp, box)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    run.__name__ = fn.__name__
    return run


@in_a_box
def test_the_ledger_names_the_machine_that_booted(tmp, box):
    """accel and qemu come from the rows, which qemu_boot.py wrote beside the qemu it ran.

    They used to be measured here: `-w /dev/kvm` on the machine running tier-c.sh, and its
    own qemu-system-x86_64 --version. The stub qemu on PATH answers 9.9.9 and this machine
    may well have no KVM; the rows say 8.2.2 and kvm, and the ledger must say what they say.
    """
    rc, out = run_tier_c(tmp, {"TMPDIR": box})
    doc = ledger_of(tmp)
    check("the run passes", rc, 0)
    check("qemu is the one that booted", doc and doc["qemu"], "8.2.2")
    check("accel is the one that booted", doc and doc["accel"], "kvm")


@in_a_box
def test_one_tcg_boot_makes_a_tcg_run(tmp, box):
    rc, out = run_tier_c(tmp, {"TMPDIR": box, "STUB_TCG_PATH": "bios"}, paths="kernel bios")
    doc = ledger_of(tmp)
    check("two rows written", doc and len(doc["runs"]), 2)
    check("kvm only if every row is", doc and doc["accel"], "tcg")


def test_a_run_that_cannot_name_its_qemu_writes_no_ledger():
    """"unknown", or no qemu at all -- a row from a harness older than the rule. Either way
    the ledger would say nothing about which qemu booted, and it is not written."""
    for qemu in ("unknown", "-"):
        tmp = tempfile.mkdtemp(prefix="tierc-run-")
        try:
            box = os.path.join(tmp, "tmpdir")
            os.makedirs(box)
            rc, out = run_tier_c(tmp, {"TMPDIR": box, "STUB_QEMU": qemu})
            check(f"qemu {qemu!r}: the run fails", rc != 0, True)
            check(f"qemu {qemu!r}: ...saying why", "refusing to write the ledger" in out, True)
            check(f"qemu {qemu!r}: ...and writes nothing", ledger_of(tmp), None)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def test_the_qmp_leak_check_looks_where_the_harness_writes():
    """TMPDIR pointed away from /tmp, as a caller is entitled to do.

    A leak there must be caught by name. Before the fix it was invisible: the check counted
    /tmp/qb-*, found the same number before and after, and said "clean".
    """
    tmp = tempfile.mkdtemp(prefix="tierc-run-")
    try:
        box = os.path.join(tmp, "tmpdir")
        os.makedirs(box)
        rc, out = run_tier_c(tmp, {"TMPDIR": box, "STUB_LEAK_QB": "1"})
        check("a leaked qmp directory fails the run", rc != 0, True)
        check("...and is reported as a leak", "LEAK" in out, True)
        check("...not waved through as clean",
              "no qmp socket directories leaked" in out, False)
        check("...in the directory the harness really used", box in out, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_run_that_leaks_nothing_is_clean():
    """The other half: with nothing left behind the same check must pass, or the first
    test would pass for a run that reports LEAK about everything."""
    tmp = tempfile.mkdtemp(prefix="tierc-run-")
    try:
        box = os.path.join(tmp, "tmpdir")
        os.makedirs(box)
        rc, out = run_tier_c(tmp, {"TMPDIR": box})
        check("a clean run passes", rc, 0)
        check("...says so", "no qmp socket directories leaked" in out, True)
        ledger = json.load(open(os.path.join(tmp, "ledger.json")))
        check("...and wrote its one row", len(ledger["runs"]), 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    # One box for every fixture, removed afterwards: the convention of #25.
    box = tempfile.mkdtemp(prefix="test_tier_c_run-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_the_qmp_leak_check_looks_where_the_harness_writes,
                   test_a_run_that_leaks_nothing_is_clean,
                   test_the_ledger_names_the_machine_that_booted,
                   test_one_tcg_boot_makes_a_tcg_run,
                   test_a_run_that_cannot_name_its_qemu_writes_no_ledger]:
            fn()
        if FAILURES:
            for f in FAILURES:
                print(f"FAIL {f}", file=sys.stderr)
            return 1
        print("tests/unit/test_tier_c_run.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
