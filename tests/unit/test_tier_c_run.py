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
# $STUB_TEST_RC: what `kitchen test` returns, and the two cases are NOT the same shape.
#   2 or 3  a boot host that could not be used. No boot happened, so this exits BEFORE
#           writing to --record, exactly as a run that never took place would.
#   1       a boot that RAN and FAILED. The row is still written, saying "fail", because
#           a failed boot is evidence and tier-c.sh must still record it.
case "${STUB_TEST_RC:-0}" in
    0|1) ;;
    *) echo "stub kitchen: exiting ${STUB_TEST_RC}" >&2; exit "${STUB_TEST_RC}" ;;
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
result=pass
[ "${STUB_TEST_RC:-0}" = 1 ] && result=fail
printf '{"path": "%s", "iso_name": "%s", "iso_bytes": 1, "accel": "%s"%s, "markers": [], "missing": [], "result": "%s"}\n' \
    "$path" "$(basename "$iso")" "$accel" "$qemu_field" "$result" >> "$record"
exit "${STUB_TEST_RC:-0}"
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


# Stands in for lib/boot_host.py, which tier-c.sh asks where the boots will happen.
STUB_BOOT_HOST = """#!/usr/bin/env python3
import os, sys
if sys.argv[1:2] == ["active"]:
    if os.environ.get("KITCHEN_BOOT_HOST") == "local":
        raise SystemExit(1)
    print("kvmbox")
    raise SystemExit(0)
raise SystemExit(0)
"""


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, mode)


def fixture(tmp, boot_host=False):
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
    if boot_host:
        # WHAT tier-c.sh LEGITIMATELY NEEDS HERE, and nothing else. Linked from wherever
        # this machine keeps them, so the closed PATH in run_tier_c() can be real: qemu,
        # xorriso and mkfs.ext4 are absent by construction rather than by hoping the
        # machine running the suite happens not to have them.
        os.makedirs(bindir, exist_ok=True)
        for t in ("sh", "python3", "git", "ssh", "rsync", "awk", "sed", "grep", "mkdir",
                  "mktemp", "rm", "cp", "cat", "dirname", "basename", "head", "tail",
                  "tr", "cut", "du", "wc", "ls", "env", "id", "stat", "find", "sort",
                  "date", "expr", "touch", "chmod", "printf", "uname", "sleep"):
            real = shutil.which(t)
            if real:
                os.symlink(real, os.path.join(bindir, t))
    for name, body in STUB_TOOLS.items():
        # WITH A BOOT HOST, THE BOOT TOOLS ARE NOT HERE. Writing them anyway would make
        # the tool-check test unfailable: tier-c.sh could go on demanding qemu locally
        # and still pass, because the fixture had quietly provided it. This container is
        # the real case -- no qemu at all -- and the fixture now matches it.
        if boot_host:
            continue
        write(os.path.join(bindir, name), body, 0o755)
    if boot_host:
        # Committed with the rest, so the tree stays clean and the dirty-tree guard --
        # which runs before any of this -- is not what fails the case.
        write(os.path.join(repo, "boot-host.ini"),
              "[boot-host]\nhost = kvmbox\nscratch = /srv/s\n")
        write(os.path.join(repo, "lib", "boot_host.py"), STUB_BOOT_HOST, 0o755)
        subprocess.run(["git", "add", "-A"], **q)
        subprocess.run(["git", "commit", "-qm", "boot host"], **q)
    return repo, iso, bindir


def run_tier_c(tmp, env_extra, paths="kernel", boot_host=False):
    repo, iso, bindir = fixture(tmp, boot_host)
    out = os.path.join(tmp, "evidence")
    # THE BOOT-HOST CASE GETS A CLOSED PATH, and that is the whole point of it.
    #
    # This used to append the ambient PATH in every case. With a boot host the fixture
    # writes no stub tools at all, so "no xorriso anywhere on this PATH" was a comment
    # rather than a fact: /usr/bin/xorriso was right there, and the assertion that
    # tier-c.sh does not demand xorriso could not fail on any machine that has it.
    # Found 2026-09-20. The ordinary case keeps the ambient PATH -- it plants its own
    # stub tools and is not making a claim about absence.
    if boot_host:
        env = dict(os.environ, PATH=bindir, **env_extra)
    else:
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


@in_a_box
def test_a_boot_host_that_could_not_run_writes_no_ledger(tmp, _box):
    """Exit 2 and 3 from `kitchen test` mean no boot happened.

    A ledger row is a claim about a boot. Treating "the host was unreachable" as a failed
    boot would record four rows saying an image did not boot, when nothing ever tried --
    and ci/release-notes.sh reads those rows to make a public claim about Tier C.
    """
    for code in ("2", "3"):
        # A fixture per case: fixture() git-inits and commits, so a second one in the
        # same directory has nothing to commit and dies rather than testing anything.
        one = os.path.join(tmp, "case" + code)
        os.makedirs(one)
        rc, out = run_tier_c(one, {"STUB_TEST_RC": code}, paths="bios uefi")
        check(f"exit {code} travels out of the sweep", rc, int(code))
        check(f"exit {code}: no ledger is written", ledger_of(one), None)
        check(f"exit {code}: it says why", "No ledger was written" in out, True)
        # The second path must not be attempted: it would ask the same unusable machine
        # the same question and produce a second identical failure.
        check(f"exit {code}: it stopped at the first path", out.count("exiting"), 1)


@in_a_box
def test_an_ordinary_failure_still_records(tmp, _box):
    """...and 1 is unchanged: a boot that happened and failed IS evidence.

    THE BODY DID NOT DO THIS. It ran a PASSING sweep -- `run_tier_c(tmp, {})` -- which
    two tests above already assert, so the case this test is named for was covered
    nowhere. Exit 1 has to behave the OPPOSITE way to 2 and 3: those mean no boot
    happened and must write no ledger, this means a boot happened and must still be
    recorded. Found 2026-09-20.
    """
    rc, out = run_tier_c(tmp, {"STUB_TEST_RC": "1"}, paths="bios")
    led = ledger_of(tmp)
    check("a boot that ran and failed still writes a ledger", led is not None, True)
    check("...with the row it produced", len(led["runs"]) if led else 0, 1)
    check("...saying the boot failed", led["runs"][0]["result"] if led else None, "fail")
    check("...and the sweep reports failure", rc != 0, True)
    check("...without claiming nothing ran", "No ledger was written" in out, False)


@in_a_box
def test_with_a_boot_host_the_local_tool_check_asks_for_the_transport(tmp, _box):
    """qemu is not needed here when the boots are not here.

    Demanding it would refuse every sweep on the machine this feature exists for. The
    modes' own tools are checked on the boot host, by the boot host, before it starts.
    """
    # No qemu, no xorriso, no mkfs.ext4 anywhere on this PATH.
    #
    # KITCHEN_BOOT_HOST is cleared here ON PURPOSE. ci/checks/80-unit.sh sets it to
    # `local` for every test, so that a boot-host.ini in the developer's own tree cannot
    # change what the gate measures; this is the one case that means to exercise the
    # remote path, so it says so, here, where it is visible.
    rc, out = run_tier_c(tmp, {"KITCHEN_BOOT_HOST": ""}, paths="bios", boot_host=True)
    check("the sweep runs", rc, 0)
    check("...on a machine with no qemu at all",
          "qemu-system-x86_64 not installed" in out, False)
    check("...nor xorriso", "xorriso not installed" in out, False)
    # THIS machine's /dev/kvm says nothing about boots that happen elsewhere, so the
    # banner must not report it -- the container this runs in has none, and the line
    # would announce TCG over a sweep running entirely under KVM somewhere else.
    check("...and does not claim TCG", "runs under TCG" in out, False)
    check("...saying where instead", "on kvmbox" in out, True)
    # The local qmp count would be 0 before and 0 after however badly the boots behaved.
    check("the leak check moved with the boots",
          "checked its own temporary directory on kvmbox" in out, True)


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
                   test_a_run_that_cannot_name_its_qemu_writes_no_ledger,
                   test_a_boot_host_that_could_not_run_writes_no_ledger,
                   test_an_ordinary_failure_still_records,
                   test_with_a_boot_host_the_local_tool_check_asks_for_the_transport]:
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
