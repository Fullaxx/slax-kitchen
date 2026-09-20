#!/usr/bin/env python3
"""`kitchen test`'s decisions before a boot: which entry, which assertions, or a refusal.

The real `kitchen` and lib/*.sh run in a fixture checkout whose tests/boot/qemu_boot.py is
a stub that only records how it was called -- so these see exactly what kitchen_test asked
the harness to do, with no qemu and no image. xorriso is a stub too, serving files out of a
directory that stands in for the ISO and failing the way the real one does for a file that
is not there (measured on xorriso 1.5.6: exit 5, and a FAILURE line naming the path).

WHY THIS EXISTS. A menu boot -- --bios, --uefi, --usb -- used to treat "xorriso is not
installed", "the menu would not extract" and "the serial entry's label cannot be typed" the
same as "this image has no serial entry": it printed the last, booted with nothing to
assert, and passed on a screenshot. On a machine without xorriso that was the normal
outcome, and it blamed the image.

PATH IS CLOSED. Every tool the scripts run is linked into one directory and PATH is that
directory alone, so "no xorriso" means no xorriso even on a machine that has one.

Run directly: python3 tests/unit/test_kitchen_test.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

FAILURES = []

MARKERS = ["Looking for slax data", "Mounting bundles", "Live Kit done, starting slax"]

# What `kitchen` and lib/*.sh run, linked from wherever this machine keeps them.
TOOLS = ("sh", "python3", "awk", "sed", "grep", "mkdir", "mktemp", "rm", "cp", "cat",
         "dirname", "basename", "head", "tail", "tr", "cut", "du", "wc", "ls", "env", "id")

STUB_HARNESS = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_HARNESS_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
'''

# Stands in for lib/boot_host.py. `active` answers the question kitchen_test asks before
# every boot -- 0 a boot host, 1 boots stay here -- and `test` records the rebuilt flags
# and returns whatever the case wants, which is how exit 2 and 3 get exercised without a
# host to be unreachable.
STUB_BOOT_HOST = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
if argv[:1] == ["active"]:
    if os.environ.get("KITCHEN_BOOT_HOST") == "local":
        raise SystemExit(1)
    print(os.environ.get("STUB_BH_HOST", "kvmbox"))
    raise SystemExit(0)
with open(os.environ["STUB_BH_LOG"], "a") as f:
    f.write(json.dumps(argv) + "\n")
raise SystemExit(int(os.environ.get("STUB_BH_RC", "0")))
'''

# Captured from: xorriso 1.5.6 -- the exit 5 and the FAILURE line below are that version's.
STUB_XORRISO = r'''#!/bin/sh
rc=0
while [ $# -gt 0 ]; do
    case "$1" in
        -extract)
            if [ -f "$STUB_ISO_TREE$2" ]; then cp "$STUB_ISO_TREE$2" "$3"
            else
                echo "xorriso : FAILURE : Cannot determine attributes of (ISO) source file '$2' : No such file or directory" >&2
                rc=5
            fi
            shift 3 ;;
        *) shift ;;
    esac
done
exit $rc
'''

ISOLINUX_SERIAL = """\
LABEL default
  APPEND vga=normal
LABEL serial
  APPEND vga=normal console=tty0 console=ttyS0,115200n8
"""

ISOLINUX_PLAIN = "LABEL default\n  APPEND vga=normal\n"

ISOLINUX_UNTYPEABLE = ISOLINUX_SERIAL.replace("LABEL serial", "LABEL Serial-Con")

GRUB_SERIAL = """\
set timeout=30
menuentry "Slax" {
  linux /slax/boot/vmlinuz vga=normal
}
menuentry "Slax (serial)" {
  linux /slax/boot/vmlinuz console=tty0 console=ttyS0,115200n8
}
"""


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, mode)


class Fixture:
    def __init__(self, tmp):
        self.tmp = tmp
        self.repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(self.repo, "lib"))
        shutil.copy2(os.path.join(ROOT, "kitchen"), os.path.join(self.repo, "kitchen"))
        for f in os.listdir(os.path.join(ROOT, "lib")):
            if f.endswith(".sh"):
                shutil.copy2(os.path.join(ROOT, "lib", f), os.path.join(self.repo, "lib", f))
        write(os.path.join(self.repo, "tests", "boot", "qemu_boot.py"), STUB_HARNESS, 0o755)
        # The structure check too, so a test can see whether --structure ever reached it.
        write(os.path.join(self.repo, "tests", "structure", "iso_assert.py"),
              STUB_HARNESS.replace("sys.argv[1:]", '["iso_assert"] + sys.argv[1:]'), 0o755)
        self.bin = os.path.join(tmp, "bin")
        os.makedirs(self.bin)
        for t in TOOLS:
            real = shutil.which(t)
            if real:
                os.symlink(real, os.path.join(self.bin, t))
        write(os.path.join(self.bin, "qemu-system-x86_64"), "#!/bin/sh\nexit 0\n", 0o755)
        self.tree = os.path.join(tmp, "isotree")
        os.makedirs(self.tree)
        self.iso = os.path.join(tmp, "images", "slax-stub.iso")
        write(self.iso, "stands in for an image; the stub xorriso reads isotree/\n")
        self.box = os.path.join(tmp, "tmpdir")
        os.makedirs(self.box)
        self.log = os.path.join(tmp, "harness.jsonl")
        self.bhlog = os.path.join(tmp, "boot-host.jsonl")
        self.bh_rc = "0"

    def with_boot_host(self):
        """A configured boot host: the file kitchen_test looks for, and the driver.

        BOTH, because the hook checks the file exists before spending a python start --
        and because `python3 <missing file>` also exits 2, which is the code that means
        "the configuration was refused".
        """
        write(os.path.join(self.repo, "boot-host.ini"),
              "[boot-host]\nhost = kvmbox\nscratch = /srv/s\n")
        write(os.path.join(self.repo, "lib", "boot_host.py"), STUB_BOOT_HOST, 0o755)
        # What a remote boot needs HERE, stubbed. They are never invoked -- the driver
        # above is a stub too -- but kitchen_test checks they exist before handing over,
        # and on this closed PATH nothing exists unless it is put here.
        for t in ("ssh", "rsync", "git"):
            write(os.path.join(self.bin, t), "#!/bin/sh\nexit 0\n", 0o755)

    def without_ssh(self):
        os.unlink(os.path.join(self.bin, "ssh"))

    def with_xorriso(self):
        write(os.path.join(self.bin, "xorriso"), STUB_XORRISO, 0o755)

    def without_qemu(self):
        os.unlink(os.path.join(self.bin, "qemu-system-x86_64"))

    def iso_file(self, path, text):
        write(os.path.join(self.tree, path.lstrip("/")), text)

    def run(self, *args):
        open(self.log, "w").close()
        open(self.bhlog, "w").close()
        env = {"PATH": self.bin, "HOME": self.tmp, "TMPDIR": self.box, "NO_COLOR": "1",
               "STUB_HARNESS_LOG": self.log, "STUB_ISO_TREE": self.tree,
               "STUB_BH_LOG": self.bhlog, "STUB_BH_RC": self.bh_rc}
        p = subprocess.run(["sh", os.path.join(self.repo, "kitchen"), "test", self.iso] + list(args),
                           cwd=self.tmp, env=env, capture_output=True, text=True, timeout=60)
        calls = [json.loads(ln) for ln in open(self.log) if ln.strip()]
        self.sent = [json.loads(ln) for ln in open(self.bhlog) if ln.strip()]
        # kitchen_test's own scratch -- the menu and kernel extraction -- goes in TMPDIR,
        # and every exit path has to take it away again, the new early refusals included.
        left = os.listdir(self.box)
        return p.returncode, p.stdout + p.stderr, calls, left


def opt(call, name):
    """Every value given to `name` in one recorded harness call."""
    return [call[i + 1] for i, a in enumerate(call[:-1]) if a == name]


def case(fn):
    def run():
        tmp = tempfile.mkdtemp(prefix="kitchen-test-")
        try:
            fn(Fixture(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    run.__name__ = fn.__name__
    return run


@case
def test_no_xorriso_refuses_a_menu_boot_before_booting(fx):
    rc, out, calls, left = fx.run("--bios")
    check("no xorriso, --bios: fails", rc, 1)
    check("...naming the package", "apt-get install xorriso" in out, True)
    check("...and nothing was booted", calls, [])
    check("...never the old claim about the image", "no serial entry" in out, False)
    check("...and leaves no scratch", left, [])


@case
def test_no_xorriso_refuses_a_kernel_boot_before_booting(fx):
    """NOT a duplicate of the --bios case above, though it reads like one.

    An audit proposed deleting this on the grounds that
    test_every_missing_tool_is_named_at_once covers it. Mutation-checked 2026-09-20:
    dropping `want_kernel` from lib/build.sh's xorriso rule is caught by THIS TEST AND
    NOTHING ELSE. The other case runs `--structure --kernel`, and --structure requires
    xorriso by its own rule, which masks the mutation entirely. Kernel mode alone is the
    only shape that can see it.
    """
    rc, out, calls, left = fx.run("--kernel")
    check("no xorriso, --kernel: fails", rc, 1)
    check("...naming the package", "xorriso is not installed (apt-get install xorriso)" in out,
          True)
    check("...and nothing was booted", calls, [])


@case
def test_structure_without_xorriso_is_refused_before_it_runs(fx):
    """iso_assert.py lists the image with xorriso. Unchecked, --structure ran it anyway and
    it died in a traceback, after half its assertions had printed."""
    rc, out, calls, left = fx.run("--structure")
    check("no xorriso, --structure: fails", rc, 1)
    check("...naming the package", "xorriso is not installed (apt-get install xorriso)" in out,
          True)
    check("...before iso_assert.py ran", calls, [])
    check("...with no traceback", "Traceback" in out, False)


@case
def test_no_qemu_is_a_failure_not_a_skip(fx):
    """The old message said "skipping boot test" and then failed the test."""
    fx.with_xorriso()
    fx.without_qemu()
    rc, out, calls, left = fx.run("--kernel")
    check("no qemu: fails", rc, 1)
    check("...naming the package",
          "qemu-system-x86_64 is not installed (apt-get install qemu-system-x86)" in out, True)
    check("...not calling it a skip", "skipping" in out, False)
    check("...and nothing was booted", calls, [])


@case
def test_every_missing_tool_is_named_at_once(fx):
    """One run names all of it -- not one tool per attempt, each found by trying again."""
    fx.without_qemu()
    rc, out, calls, left = fx.run("--structure", "--kernel")
    check("both missing: fails", rc, 1)
    check("...names xorriso", "xorriso is not installed" in out, True)
    check("...and qemu", "qemu-system-x86_64 is not installed" in out, True)
    check("...each once", out.count("xorriso is not installed"), 1)
    check("...and runs nothing, structure included", calls, [])


@case
def test_no_keys_needs_no_xorriso_and_says_why_it_asserts_nothing(fx):
    rc, out, calls, left = fx.run("--bios", "--no-keys")
    check("--no-keys boots without xorriso", len(calls), 1)
    check("...with nothing to assert", opt(calls[0], "--expect"), [])
    check("...and says it was asked to", "--no-keys: the menu was not touched" in out, True)
    check("...not that the image has no serial entry", "no serial entry" in out, False)
    check("...and passes as it always did", rc, 0)


@case
def test_a_menu_that_will_not_extract_fails_with_xorrisos_reason(fx):
    fx.with_xorriso()
    rc, out, calls, left = fx.run("--bios")
    check("unreadable menu: fails", rc, 1)
    check("...names the file", "/slax/boot/isolinux.cfg is missing or empty" in out, True)
    check("...and passes on xorriso's own reason", "FAILURE : Cannot determine attributes" in out, True)
    check("...and nothing was booted", calls, [])
    check("...and leaves no scratch", left, [])


@case
def test_an_empty_menu_is_not_a_menu_without_the_entry(fx):
    """It extracts cleanly -- no FAILURE line -- and there is still nothing to choose from."""
    fx.with_xorriso()
    fx.iso_file("/slax/boot/isolinux.cfg", "")
    rc, out, calls, left = fx.run("--bios")
    check("empty menu: fails", rc, 1)
    check("...saying so", "/slax/boot/isolinux.cfg is missing or empty" in out, True)
    check("...and nothing was booted", calls, [])
    check("...never that the image has no serial entry", "no serial entry" in out, False)


@case
def test_uefi_without_a_grub_menu_points_at_the_recipe(fx):
    fx.with_xorriso()
    fx.iso_file("/slax/boot/isolinux.cfg", ISOLINUX_SERIAL)
    rc, out, calls, left = fx.run("--uefi")
    check("no grub.cfg under --uefi: fails", rc, 1)
    check("...and names uefi-bootable", "uefi-bootable" in out, True)
    check("...and nothing was booted", calls, [])


@case
def test_a_menu_with_no_serial_entry_still_falls_back_and_says_so(fx):
    fx.with_xorriso()
    fx.iso_file("/slax/boot/isolinux.cfg", ISOLINUX_PLAIN)
    rc, out, calls, left = fx.run("--bios")
    check("no serial entry: one boot", len(calls), 1)
    check("...asserting nothing", opt(calls[0], "--expect"), [])
    check("...reported as exactly that", "screenshot only -- no serial entry in this ISO" in out, True)
    check("...and passes on the evidence it has", rc, 0)
    check("...and leaves no scratch", left, [])


@case
def test_a_serial_entry_is_typed_by_name_and_asserted(fx):
    fx.with_xorriso()
    fx.iso_file("/slax/boot/isolinux.cfg", ISOLINUX_SERIAL)
    rc, out, calls, left = fx.run("--bios")
    check("serial entry: one boot", len(calls), 1)
    check("...typed by name at boot:", opt(calls[0], "--keys"),
          ["1s,esc,0.5s,esc,0.5s,s,e,r,i,a,l,ret"])
    check("...asserting the three livekit markers", opt(calls[0], "--expect"), MARKERS)
    check("...and passes", rc, 0)


@case
def test_a_label_that_cannot_be_typed_is_not_a_missing_entry(fx):
    fx.with_xorriso()
    fx.iso_file("/slax/boot/isolinux.cfg", ISOLINUX_UNTYPEABLE)
    rc, out, calls, left = fx.run("--bios")
    check("untypeable label: fails", rc, 1)
    check("...saying which label", "LABEL Serial-Con" in out, True)
    check("...and nothing was booted", calls, [])
    check("...never that the image has no serial entry", "no serial entry" in out, False)


@case
def test_uefi_counts_menuentries_inside_the_grub_timeout(fx):
    fx.with_xorriso()
    fx.iso_file("/boot/grub/grub.cfg", GRUB_SERIAL)
    rc, out, calls, left = fx.run("--uefi")
    check("grub serial entry: one boot", len(calls), 1)
    check("...a 10 s lead inside a 30 s timeout, then one down", opt(calls[0], "--keys"),
          ["10s,down,ret"])
    check("...asserted", opt(calls[0], "--expect"), MARKERS)


# --------------------------------------------------------------- the boot host ----
# These assert the SEAM, not the ssh: `kitchen test` decides where a boot runs, rebuilds
# the boot half's flags and hands them over. lib/boot_host.py is a stub here, so a wrong
# flag shows up as a wrong argument list rather than as a boot that behaved oddly on
# somebody's machine half an hour later.


@case
def test_a_boot_host_takes_the_boot_and_not_the_structure_check(fx):
    fx.with_boot_host()
    fx.with_xorriso()
    fx.iso_file("/slax/boot/vmlinuz", "k")
    fx.iso_file("/slax/boot/initrfs.img", "i")
    rc, out, calls, left = fx.run("--structure", "--kernel")
    check("it succeeds", rc, 0)
    # --structure reads the image, and the image is already here. Sending it would copy
    # 400+ MiB across a network to answer a question xorriso can answer locally.
    check("the structure check ran here", any(c[:1] == ["iso_assert"] for c in calls), True)
    check("...and nothing was booted here", [c for c in calls if c[:1] != ["iso_assert"]], [])
    check("the boot went to the boot host", len(fx.sent), 1)
    check("...as a `test` call", fx.sent[0][0], "test")
    check("...leaving no scratch", left, [])


@case
def test_the_flags_the_boot_host_gets_are_the_ones_that_were_asked_for(fx):
    fx.with_boot_host()
    fx.with_xorriso()
    rc, out, calls, left = fx.run("--bios", "--uefi", "--persistence", "--seconds", "77",
                                  "--mem", "3072", "--no-keys", "--perch-device", "/dev/sdb",
                                  "--out", "/tmp/ev", "--golden", "/tmp/g", "--record",
                                  "/tmp/r.jsonl", "--expect", "one two", "--expect", "three")
    check("it succeeds", rc, 0)
    check("one handover for the whole set", len(fx.sent), 1)
    sent = fx.sent[0]
    for want in ("--bios", "--uefi", "--persistence"):
        check(f"{want} is carried", want in sent, True)
    check("--kernel was not invented", "--kernel" in sent, False)
    check("--structure never travels", "--structure" in sent, False)
    check("the paths are named options", opt(sent, "--iso"), [fx.iso])
    check("...as is the evidence directory", opt(sent, "--out"), ["/tmp/ev"])
    check("...the golden", opt(sent, "--golden"), ["/tmp/g"])
    check("...and the ledger", opt(sent, "--record"), ["/tmp/r.jsonl"])
    # Resolved values, not the raw command line: kitchen_test's parser has already
    # applied the defaults, and forwarding "$@" would lose them.
    check("--seconds is carried", opt(sent, "--seconds"), ["77"])
    check("--mem is carried", opt(sent, "--mem"), ["3072"])
    check("--perch-device is carried", opt(sent, "--perch-device"), ["/dev/sdb"])
    check("--no-keys is carried", "--no-keys" in sent, True)
    # Repeatable, and each one contains a space -- so a list that is joined and re-split
    # anywhere along the way arrives as five expectations instead of two.
    check("every --expect arrives intact", opt(sent, "--expect"), ["one two", "three"])


@case
def test_a_default_run_carries_the_default_seconds_and_no_key_choice(fx):
    fx.with_boot_host()
    fx.with_xorriso()
    rc, out, calls, left = fx.run("--bios")
    sent = fx.sent[0]
    check("the parser's default ceiling travels", opt(sent, "--seconds"), ["32"])
    # auto_keys means "read the image's own menu", which happens over there against the
    # image that is over there. Sending --keys would freeze a decision this side cannot make.
    check("no key choice is imposed", "--keys" in sent or "--no-keys" in sent, False)
    check("no --mem when none was asked for", "--mem" in sent, False)


@case
def test_a_remote_boot_does_not_need_qemu_here(fx):
    """The whole point. This container has no qemu, and that must stop being fatal.

    NOT a subset of the two boot-host tests either side of it, though it reads like one.
    Mutation-checked 2026-09-20: adding qemu-system-x86_64 back to lib/build.sh's
    boot-host branch is caught by THIS TEST AND NOTHING ELSE. The others keep the
    fixture's stub qemu on PATH, so a needless demand for it costs them nothing;
    without_qemu() is what makes the demand visible.
    """
    fx.with_boot_host()
    fx.with_xorriso()
    fx.without_qemu()
    rc, out, calls, left = fx.run("--kernel")
    check("it runs", rc, 0)
    check("...without asking for qemu", "qemu-system-x86" in out, False)
    check("...and the boot went over", len(fx.sent), 1)


@case
def test_a_remote_boot_checks_what_IT_needs_here(fx):
    """Different modes, different tools -- and the list moved with the boot.

    Without a boot host this asks for qemu. With one it asks for what carries the work
    there instead, and says so with the package, before anything is sent.
    """
    fx.with_boot_host()
    fx.with_xorriso()
    fx.without_ssh()
    rc, out, calls, left = fx.run("--kernel")
    check("no ssh: refused", rc, 1)
    check("...naming the package", "apt-get install openssh-client" in out, True)
    check("...and nothing was sent", fx.sent, [])
    check("...nor booted here", calls, [])
    # The point of the split: it must NOT demand the tools the boot host provides.
    check("...and qemu was not demanded", "qemu-system-x86" in out, False)


@case
def test_local_beats_the_configured_host(fx):
    fx.with_boot_host()
    fx.with_xorriso()
    fx.without_qemu()
    rc, out, calls, left = fx.run("--kernel", "--local")
    check("--local boots here", fx.sent, [])
    check("...and here there is no qemu, so it fails as it always did", rc, 1)
    check("...naming the package", "apt-get install qemu-system-x86" in out, True)


@case
def test_the_boot_hosts_own_failures_are_not_test_results(fx):
    """2 and 3 travel out of `kitchen test` unchanged.

    ci/tier-c.sh reads them to decide whether to write a ledger, and a ledger row is a
    claim about a boot that happened. Flattened to 1 they would be indistinguishable from
    an image that failed to boot, which is the opposite conclusion.
    """
    fx.with_boot_host()
    fx.with_xorriso()
    for code, what in (("2", "a refused configuration"), ("3", "a host that could not run")):
        fx.bh_rc = code
        rc, out, calls, left = fx.run("--kernel")
        check(f"{what} arrives as {code}", rc, int(code))
        check(f"{what}: nothing booted here", calls, [])
        check(f"{what}: no scratch left", left, [])
    fx.bh_rc = "1"
    rc, out, calls, left = fx.run("--kernel")
    check("a failed boot is still just a failure", rc, 1)


def main():
    # One box for every fixture, removed afterwards: the convention of #25.
    box = tempfile.mkdtemp(prefix="test_kitchen_test-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_no_xorriso_refuses_a_menu_boot_before_booting,
                   test_no_xorriso_refuses_a_kernel_boot_before_booting,
                   test_structure_without_xorriso_is_refused_before_it_runs,
                   test_no_qemu_is_a_failure_not_a_skip,
                   test_every_missing_tool_is_named_at_once,
                   test_no_keys_needs_no_xorriso_and_says_why_it_asserts_nothing,
                   test_a_menu_that_will_not_extract_fails_with_xorrisos_reason,
                   test_an_empty_menu_is_not_a_menu_without_the_entry,
                   test_uefi_without_a_grub_menu_points_at_the_recipe,
                   test_a_menu_with_no_serial_entry_still_falls_back_and_says_so,
                   test_a_serial_entry_is_typed_by_name_and_asserted,
                   test_a_label_that_cannot_be_typed_is_not_a_missing_entry,
                   test_uefi_counts_menuentries_inside_the_grub_timeout,
                   test_a_boot_host_takes_the_boot_and_not_the_structure_check,
                   test_the_flags_the_boot_host_gets_are_the_ones_that_were_asked_for,
                   test_a_default_run_carries_the_default_seconds_and_no_key_choice,
                   test_a_remote_boot_does_not_need_qemu_here,
                   test_a_remote_boot_checks_what_IT_needs_here,
                   test_local_beats_the_configured_host,
                   test_the_boot_hosts_own_failures_are_not_test_results]:
            fn()
        if FAILURES:
            for f in FAILURES:
                print(f"FAIL {f}", file=sys.stderr)
            return 1
        print("tests/unit/test_kitchen_test.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
