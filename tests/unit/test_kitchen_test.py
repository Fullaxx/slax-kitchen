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

    def with_xorriso(self):
        write(os.path.join(self.bin, "xorriso"), STUB_XORRISO, 0o755)

    def without_qemu(self):
        os.unlink(os.path.join(self.bin, "qemu-system-x86_64"))

    def iso_file(self, path, text):
        write(os.path.join(self.tree, path.lstrip("/")), text)

    def run(self, *args):
        open(self.log, "w").close()
        env = {"PATH": self.bin, "HOME": self.tmp, "TMPDIR": self.box, "NO_COLOR": "1",
               "STUB_HARNESS_LOG": self.log, "STUB_ISO_TREE": self.tree}
        p = subprocess.run(["sh", os.path.join(self.repo, "kitchen"), "test", self.iso] + list(args),
                           cwd=self.tmp, env=env, capture_output=True, text=True, timeout=60)
        calls = [json.loads(ln) for ln in open(self.log) if ln.strip()]
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
                   test_uefi_counts_menuentries_inside_the_grub_timeout]:
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
