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
import signal
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
TIER_C = os.path.join(ROOT, "ci", "tier-c.sh")
RUN_BOOT = os.path.join(ROOT, "ci", "run-boot.py")


def _load_run_boot():
    """ci/run-boot.py by path -- a hyphen is not importable, and it has no package."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ci_run_boot", RUN_BOOT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ci_run_boot"] = mod
    spec.loader.exec_module(mod)
    return mod


run_boot = _load_run_boot()

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
iso="" path="" record="" out=""
while [ $# -gt 0 ]; do
    case "$1" in
        --record) record=$2; shift 2 ;;
        --out) out=$2; shift 2 ;;
        --seconds|--golden) shift 2 ;;
        --kernel|--bios|--uefi|--usb|--persistence) path=${1#--}; shift ;;
        *) iso=$1; shift ;;
    esac
done
[ -n "${STUB_RUNLOG:-}" ] && echo "$path" >> "$STUB_RUNLOG"
# $STUB_ORPHAN: still be running a guest when the boot returns, the way a boot that
# lost its own child does.
#
# NOT setsid, AND THAT IS THE POINT. The old fixture detached the orphan into a session
# of its own, because the sweep it was written against could only find a guest by reading
# a pid file and matching /proc. No real guest does that: nothing in the chain
# tier-c.sh -> `sh kitchen` -> qemu_boot.py -> qemu calls setsid or start_new_session, so
# a qemu orphaned by its launcher being killed is reparented to init and STAYS IN THE
# PROCESS GROUP it was born in. ci/run-boot.py gives each boot a group and ends it, so
# this fixture now models the real thing and the old one modelled something unreachable.
#
# Every fd off the harness's pipes, which is load-bearing and was measured: a process
# still holding them keeps subprocess.run(capture_output=True) waiting, so the test would
# hang rather than assert.
#
# The pid is written by the guest itself after exec, for the TEST to read back -- exec
# preserves the pid, so it is the one the guest really has. That is instrumentation now,
# not mechanism: nothing in tier-c.sh reads a pid from anywhere any more.
if [ -n "${STUB_ORPHAN:-}" ] && [ -n "$out" ]; then
    mkdir -p "$out"
    rm -f "$out/orphan-$path.pid"
    # One per path: two boots leave two guests, and a test that can only read the last
    # pid cannot say whether the first was dealt with.
    sh -c 'echo $$ > "$1/orphan-$2.pid"; exec sleep 60' _ "$out" "$path" \
        >/dev/null 2>&1 </dev/null &
    _n=0
    while [ ! -s "$out/orphan-$path.pid" ] && [ "$_n" -lt 200 ]; do
        _n=$((_n + 1)); sleep 0.05
    done
    _op=$(cat "$out/orphan-$path.pid" 2>/dev/null)
    # A fixture that did not take must say so, rather than leave the assertions to fail
    # for a reason none of them names.
    if [ -z "$_op" ] || [ ! -d "/proc/$_op" ]; then
        echo "stub kitchen: the orphan never started (pid '$_op')" >&2
        exit 9
    fi
fi
# $STUB_BLOCK: say we started, then block on a fifo until the test signals us. A real
# boot takes seconds; this takes as long as the test needs and not a millisecond more.
if [ -n "${STUB_BLOCK:-}" ]; then
    : > "$STUB_BLOCK.ready"
    read _x < "$STUB_BLOCK"
fi
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
import traceback
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
    # THE REAL RUNNER, not a stand-in. tier-c.sh starts every boot through it, so a
    # fixture without it is a fixture where no boot runs at all -- which surfaces as a
    # missing ledger, several assertions away from the cause.
    write(os.path.join(repo, "ci", "run-boot.py"), open(RUN_BOOT).read(), 0o755)
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


def alive(pid):
    """Running, not merely present in the process table.

    os.kill(pid, 0) SUCCEEDS ON A ZOMBIE, so this used to answer True for a guest that had
    already stopped -- which is the exact opposite of what every caller asks it. The two
    checks that read "it is not still running" both failed on an unreaped corpse, and the
    one that reads "the process is STILL RUNNING" passed on one -- both for the same wrong
    reason. Found 2026-09-20 when ci/run-checks.sh went red on a runner and
    green on the machine it was written on: after sweep_pids' own `kill -9` the orphan is a
    zombie until something reaps it, which under systemd takes microseconds and under a
    pid 1 that reaps nothing takes forever.

    The state field follows the LAST ')' in /proc/<pid>/stat, because a comm may itself
    contain spaces and parentheses.
    """
    try:
        with open("/proc/%d/stat" % pid) as fh:
            state = fh.read().rsplit(") ", 1)[1].split()[0]
    except (OSError, IndexError, ValueError):
        return False
    return state != "Z"


def reap(pid):
    """Leave no stray process behind, whatever the assertions did."""
    if pid and alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def orphan_pid(out, path="bios"):
    try:
        return int(open(os.path.join(out, f"orphan-{path}.pid")).read().strip())
    except (OSError, ValueError):
        return 0


def pid_in(out, name):
    try:
        return int(open(os.path.join(out, name)).read().strip())
    except (OSError, ValueError):
        return 0


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


# ----- the pid trap: what a leftover guest means, and whose pid it is -----

@in_a_box
def test_an_orphaned_guest_fails_the_sweep_and_is_named(tmp, _box):
    """A boot that returns while its guest is still running is a failed boot.

    THIS USED TO PASS SILENTLY. cleanup() ran from the EXIT trap -- after `exit $rc` had
    already fixed the status -- so an orphan was killed with nothing said and the sweep
    reported success. lib/boot_host.py's agent gets the remote case right and its own
    docstring said tier-c's equivalent "could only ever be a count".

    THE QUESTION IS NOW ASKED OF THE BOOT'S OWN PROCESS GROUP, not of a pid file, so what
    the fixture poses is what a real guest poses: it stays in the group it was born in,
    because nothing in the chain detaches. The three tests this replaces -- a foreign pid
    left alone, a zombie, and one path's pid file reaching the next -- were all about
    recognising a process the sweep had gone looking for. Nothing goes looking any more.
    """
    out = os.path.join(tmp, "evidence")
    rc, o = run_tier_c(tmp, {"STUB_ORPHAN": "1"}, paths="bios")
    pid = orphan_pid(out)
    try:
        check("the orphan was actually created", pid != 0, True)
        check("an orphaned guest fails the sweep", rc != 0, True)
        check("...and is named, not counted", "a guest was left running" in o, True)
        check("...naming the path it belongs to", "bios:" in o, True)
        check("...and it is not still running", alive(pid), False)
    finally:
        reap(pid)


@in_a_box
def test_each_path_answers_for_its_own_guest(tmp, _box):
    """Two boots, two groups, two answers, and neither inherits the other's.

    $OUT/pids was one directory shared by every path with nothing emptying it, so a file
    left by the first was still sitting there when the last finished -- and whatever it
    named was killed at exit under the wrong path's name, if it was noticed at all. A
    group per boot makes that structurally impossible rather than merely fixed: the
    second path cannot see the first's group, and the first is answered for before the
    second starts.

    SO ITS INCIDENT CAN NO LONGER BE BUILT, and the next reader should not have to work
    that out for themselves. 622891b was a real bite, but in a mechanism that has since
    been deleted: there is no shared directory left for one path to leak into the next.
    What this still covers is the loop -- that run-boot.py is invoked per path rather than
    once for all of them -- and that is completeness, not an incident. If it ever fails,
    that is the first question to ask; see CONTRIBUTING.md, "What a test here is for".
    """
    out = os.path.join(tmp, "evidence")
    rc, o = run_tier_c(tmp, {"STUB_ORPHAN": "1"}, paths="bios uefi")
    first, second = orphan_pid(out, "bios"), orphan_pid(out, "uefi")
    try:
        check("both orphans were actually created", (first != 0, second != 0), (True, True))
        check("the sweep fails", rc != 0, True)
        check("the first path is named", "bios:" in o, True)
        check("the second path is named", "uefi:" in o, True)
        check("each is reported once", o.count("a guest was left running"), 2)
        check("the first path's guest was stopped", alive(first), False)
        check("...and so was the second's", alive(second), False)
    finally:
        reap(first)
        reap(second)


def test_a_zombie_in_the_group_is_not_a_running_guest():
    """The defect that reddened the gates job twice, asked of the new mechanism.

    `killpg(pgid, 0)` succeeds while a group has ANY member, and an unreaped zombie is a
    member -- so "is this boot's group still alive" answers yes over a guest that has
    already stopped. That is the same mistake `kill -0` made in the old sweep (09801bc),
    arriving by a different door, and it was found here by probing ci/run-boot.py rather
    than by waiting for CI to go red a third time.

    A CORPSE THAT STAYS A CORPSE, deterministically and on any init. A zombie whose
    parent has exited is reparented to init and reaped at once wherever init reaps, so
    this keeps the parent alive and OUTSIDE the group: it forks a child, the child makes
    itself a group of its own and exits, and the parent never waits for it. The group
    then has exactly one member and that member is dead.
    """
    src = (
        "import os, sys, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.setpgid(0, 0)\n"          # a process group of its own
        "    os._exit(0)\n"
        "time.sleep(0.3)\n"               # ...and it is never waited for
        "sys.stdout.write(str(pid) + chr(10)); sys.stdout.flush()\n"
        "time.sleep(30)\n")
    holder = subprocess.Popen([sys.executable, "-c", src], stdout=subprocess.PIPE, text=True)
    try:
        pgid = int(holder.stdout.readline().strip())
        state, pgrp = "", 0
        for _ in range(100):
            try:
                with open(f"/proc/{pgid}/stat") as fh:
                    fields = fh.read().rsplit(") ", 1)[1].split()
                state, pgrp = fields[0], int(fields[2])
            except (OSError, IndexError, ValueError):
                break
            if state == "Z" and pgrp == pgid:
                break
            time.sleep(0.02)
        # A fixture that did not take must say so rather than let the assertions pass for
        # the wrong reason: a live process would make both answers below "running".
        check("the fixture planted a zombie", state, "Z")
        check("...alone in a group of its own", pgrp, pgid)
        check("the group answers a signal at all", run_boot._group_alive(pgid), True)
        check("...but nothing in it is running", run_boot._group_running(pgid), False)
    finally:
        holder.kill()
        holder.wait()


def test_an_interrupt_stops_the_sweep():
    """Ctrl-C must not start the next boot path, and must not leave the guest running.

    WHY IT EXISTS HAS CHANGED, so the reason is restated rather than left to rot. It was
    written as a regression test for a bug that did not exist: an audit expected cleanup()
    to carry on into the next path and the trap to omit HUP, and neither reproduced when it
    was measured. On its own that is the weakest justification this repo takes --
    completeness.

    It has a real one now. ci/run-boot.py starts each boot in a session of its own, which
    REMOVED THE FREE DELIVERY OF Ctrl-C: the terminal's signal used to reach qemu because
    everything shared one process group, and now it reaches run-boot.py, which forwards it
    by hand. That forwarding is days old, it is the only thing between an interrupted sweep
    and a VM left running, and nothing else exercises it.

    Honest about what it has caught: this test hung once while that forwarding was being
    written and passed after two separate changes. Which one fixed it was never isolated,
    so it is not credited with the catch.

    What this pins is the outcome a reader actually cares about, in a shape that CAN go
    red: after an interrupt, the second path has not started and no guest is left behind.
    The guest has its fds detached, which is what lets it survive this harness long
    enough to be asked about. It is no longer setsid: it sits in the boot's own process
    group, where a real orphaned qemu sits, and the interrupt has to reach it there.

    No sleeps: the stub says it has started and blocks on a fifo, so the signal goes at
    exactly the right moment and the test takes as long as that takes and no longer.
    """
    tmp = tempfile.mkdtemp(prefix="tierc-int-")
    proc = None
    try:
        repo, iso, bindir = fixture(tmp)
        out = os.path.join(tmp, "evidence")
        fifo = os.path.join(tmp, "block")
        runlog = os.path.join(tmp, "runs")
        os.mkfifo(fifo)
        env = dict(os.environ, PATH=bindir + ":" + os.environ.get("PATH", ""),
                   STUB_BLOCK=fifo, STUB_RUNLOG=runlog, STUB_ORPHAN="ours")
        proc = subprocess.Popen(
            ["sh", os.path.join(repo, "ci", "tier-c.sh"), "--iso", iso,
             "--target", "stub-target", "--paths", "bios uefi", "--out", out,
             "--ledger", os.path.join(tmp, "ledger.json"),
             "--golden-dir", os.path.join(tmp, "golden")],
            cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, start_new_session=True)

        # Wait for the first path to say it is in the boot, then interrupt the whole
        # process group -- which is what a terminal does on Ctrl-C, and the only thing
        # that reaches a shell blocked on a foreground child.
        deadline = time.time() + 30
        while not os.path.exists(fifo + ".ready") and time.time() < deadline:
            time.sleep(0.02)
        check("the first path started", os.path.exists(fifo + ".ready"), True)
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        o = proc.communicate(timeout=60)[0]

        ran = [ln for ln in open(runlog).read().split() if ln] if os.path.exists(runlog) else []
        check("only the interrupted path ran", ran, ["bios"])
        check("...and no ledger claims otherwise", ledger_of(tmp), None)
        # THE POINT OF FORWARDING THE SIGNAL. The boot runs in a session of its own, so
        # the terminal's Ctrl-C does not reach it for free any more -- ci/run-boot.py
        # passes it on deliberately. If it did not, an interrupted sweep would leave a VM
        # running on the machine.
        pid = orphan_pid(out)
        check("the interrupted sweep left a guest to find", pid != 0, True)
        check("...and did not leave it running", alive(pid), False)
    finally:
        reap(orphan_pid(os.path.join(tmp, "evidence")))
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                pass
            proc.wait(timeout=10)
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
                   test_a_run_that_cannot_name_its_qemu_writes_no_ledger,
                   test_a_boot_host_that_could_not_run_writes_no_ledger,
                   test_an_ordinary_failure_still_records,
                   test_with_a_boot_host_the_local_tool_check_asks_for_the_transport,
                   test_an_orphaned_guest_fails_the_sweep_and_is_named,
                   test_each_path_answers_for_its_own_guest,
                   test_a_zombie_in_the_group_is_not_a_running_guest,
                   test_an_interrupt_stops_the_sweep]:
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
