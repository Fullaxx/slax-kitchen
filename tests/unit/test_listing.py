#!/usr/bin/env python3
"""A listing that did not work is not an image without files.

diff._entries() is the one listing of an image's files: `kitchen diff`, `kitchen sources`
and the structure check (tests/structure/iso_assert.py) all read it. It returned whatever
parsed, and xorriso does not always say when it failed -- measured on 1.5.6, listing a file
that is not an ISO at all exits 0, prints no FAILURE line, and lists only `/`. So a listing
that failed read as an image with no files, and each reader took that at its word:

  kitchen sources --strict   accounts for the files it is given: "unresolved 0", exit 0 --
                             a pass for an image nobody had examined
  kitchen diff               every file of the other image reported removed, or added
  --structure                the kernel, initramfs and bootloader reported missing from
                             an image that held all three -- five false FAILs

The structure check also compared --require by basename against /slax/boot's listing, so
`--require /EFI/BOOT/isolinux.cfg` passed on an image with no /EFI at all. Its ESP check is
decided from the same listing, so it is tested here too: an image that carried boot/efi.img
with no EFI El Torito entry had passed.

A stub xorriso serves canned `lsdl` and `report_lba` output, in the format measured on
1.5.6, and the image is a few sectors written here -- enough for lib/isoparse.py. Nothing
needs xorriso or a real ISO, which is what lets this run in CI's gates job.

Run directly: python3 tests/unit/test_listing.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import diff  # noqa: E402
import traceback

_spec = importlib.util.spec_from_file_location(
    "iso_assert", os.path.join(ROOT, "tests", "structure", "iso_assert.py"))
iso_assert = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(iso_assert)

FAILURES = []
SECTOR = 2048
FIVE = ["vmlinuz", "initrfs.img", "isolinux.bin", "isolinux.cfg", "syslinux.cfg"]

# Serves $STUB_DIR/lsdl for `-exec lsdl`, $STUB_DIR/lba for `-exec report_lba`, anything in
# $STUB_DIR/err on stderr, and exits with $STUB_DIR/rc.
# Captured from: xorriso 1.5.6 -- the listing shapes and the FAILURE/SORRY/MISHAP wording
# the cases below feed it are that version's.
STUB_XORRISO = r'''#!/bin/sh
for a in "$@"; do
    case "$a" in
        lsdl) cat "$STUB_DIR/lsdl" 2>/dev/null ;;
        report_lba) cat "$STUB_DIR/lba" 2>/dev/null ;;
    esac
done
[ -f "$STUB_DIR/err" ] && cat "$STUB_DIR/err" >&2
exit "$(cat "$STUB_DIR/rc" 2>/dev/null || echo 0)"
'''


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def lsdl(dirs, files) -> str:
    """Lines as `xorriso -find / -exec lsdl` prints them."""
    out = [f"drwxr-xr-x    1 0        0               0 Sep 19 14:37 '{d}'" for d in dirs]
    out += [f"-rw-r--r--    1 0        0              12 Sep 19 14:37 '{f}'" for f in files]
    return "\n".join(out) + "\n"


def report_lba(files) -> str:
    out = ["Report layout: xt , Startlba ,   Blocks , Filesize , ISO image path"]
    out += [f"File data lba:  0 ,       {33 + i} ,        1 ,       12 , '{f}'"
            for i, f in enumerate(files)]
    return "\n".join(out) + "\n"


def slax_listing():
    files = [f"/slax/boot/{n}" for n in FIVE]
    return lsdl(["/", "/slax", "/slax/boot"], files), report_lba(files)


def tiny_iso(path: str) -> str:
    """A primary volume descriptor and a terminator: enough for IsoReader to open it."""
    img = bytearray(SECTOR * 24)
    pvd = bytearray(SECTOR)
    pvd[0], pvd[1:6], pvd[6] = 1, b"CD001", 1
    struct.pack_into("<I", pvd, 80, 24)
    struct.pack_into("<H", pvd, 128, SECTOR)
    struct.pack_into("<I", pvd, 158, 23)
    struct.pack_into("<I", pvd, 166, SECTOR)
    img[16 * SECTOR:17 * SECTOR] = pvd
    img[17 * SECTOR] = 255
    img[17 * SECTOR + 1:17 * SECTOR + 6] = b"CD001"
    with open(path, "wb") as f:
        f.write(img)
    return path


class Stub:
    """A directory holding the stub xorriso and what it will say."""
    def __init__(self, tmp):
        self.dir = os.path.join(tmp, "stub")
        os.makedirs(self.dir)
        p = os.path.join(self.dir, "xorriso")
        with open(p, "w") as f:
            f.write(STUB_XORRISO)
        os.chmod(p, 0o755)

    def says(self, lsdl_text="", lba_text="", err="", rc=0):
        for name, text in (("lsdl", lsdl_text), ("lba", lba_text), ("err", err),
                           ("rc", str(rc))):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write(text)
        return self

    def env(self):
        return dict(os.environ, PATH=self.dir + os.pathsep + os.environ.get("PATH", ""),
                    STUB_DIR=self.dir)


@contextlib.contextmanager
def environ(env):
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def in_a_box(fn):
    def run():
        tmp = tempfile.mkdtemp(prefix="listing-")
        try:
            fn(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    run.__name__ = fn.__name__
    return run


def entries_or_error(stub, iso):
    with environ(stub.env()):
        try:
            return diff._entries(iso), None
        except diff.ListingError as e:
            return None, str(e)


@in_a_box
def test_entries_refuses_a_listing_it_cannot_trust(tmp):
    iso = tiny_iso(os.path.join(tmp, "slax.iso"))
    stub = Stub(tmp)

    got, err = entries_or_error(stub.says(lsdl(["/"], []), ""), iso)
    check("only / listed, exit 0 -- what a non-ISO gives: refused", got, None)
    check("...saying it listed no files", "listed no files" in (err or ""), True)

    got, err = entries_or_error(stub.says(
        err="xorriso : FAILURE : Cannot read the image\n", rc=5), iso)
    check("a FAILURE and exit 5: refused", got, None)
    check("...carrying xorriso's own line", "FAILURE : Cannot read the image" in (err or ""),
          True)

    lsdl_text, lba_text = slax_listing()
    got, err = entries_or_error(stub.says(lsdl_text, lba_text,
                                          err="xorriso : SORRY : a later read failed\n"), iso)
    check("a full listing with a SORRY beside it: refused", err is not None, True)

    # MISHAP sits between SORRY and FAILURE, below the default abort threshold, so xorriso
    # can report one and exit 0. The first version of the rule matched SORRY and FAILURE by
    # name and let it through.
    got, err = entries_or_error(stub.says(lsdl_text, lba_text,
                                          err="libisofs: MISHAP : a directory was skipped\n"),
                                iso)
    check("a full listing with a MISHAP beside it, exit 0: refused", err is not None, True)

    got, err = entries_or_error(stub.says(lsdl_text, lba_text), iso)
    check("a real listing: no error", err, None)
    check("...every file listed", sorted(p for p, e in (got or {}).items() if e["type"] == "file"),
          sorted(f"/slax/boot/{n}" for n in FIVE))
    check("...each with its extent", all(e["lba"] for e in (got or {}).values()
                                         if e["type"] == "file"), True)


def run_checks(paths, extras=()):
    t = iso_assert.Asserter()
    with contextlib.redirect_stdout(io.StringIO()):
        iso_assert.check_files(t, set(paths), list(extras))
    return t


def test_check_files_with_plain_data():
    full = {"/", "/slax", "/slax/boot"} | {f"/slax/boot/{n}" for n in FIVE}

    t = run_checks(full)
    check("all present: five pass", (t.ok, t.fail), (5, []))

    t = run_checks(full - {"/slax/boot/isolinux.bin"})
    check("one missing: that one fails, by name", t.fail, ["slax/boot/isolinux.bin present"])
    check("...and the other four pass", t.ok, 4)

    t = run_checks({"/", "/EFI", "/EFI/BOOT", "/EFI/BOOT/BOOTX64.EFI"})
    check("no /slax/boot: one failure, not five", len(t.fail), 1)
    check("...saying what it is", t.fail and t.fail[0].startswith("/slax/boot is in the image"),
          True)

    t = run_checks(full, ["/EFI/BOOT/isolinux.cfg"])
    check("--require by exact path: a basename in /slax/boot does not count",
          t.fail, ["/EFI/BOOT/isolinux.cfg present"])
    t = run_checks(full, ["slax/boot/vmlinuz"])
    check("--require of a path that is there, with or without the leading /", t.fail, [])


@in_a_box
def test_the_structure_check_end_to_end(tmp):
    """main() itself, so the wiring is covered and not only the function: the first
    version of this change imported the listing as `diff`, which main()'s own local
    `diff` shadowed -- an UnboundLocalError no plain-data test could have seen."""
    iso = tiny_iso(os.path.join(tmp, "slax.iso"))
    stub = Stub(tmp)

    def structure():
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "structure",
                                                         "iso_assert.py"), iso],
                           env=stub.env(), capture_output=True, text=True, timeout=60)
        return p.stdout + p.stderr

    # The image is a few sectors, so its other structural checks fail; only the file checks
    # are this test's business.
    stub.says(*slax_listing())
    out = structure()
    check("a real listing: the five files pass",
          all(f"ok   slax/boot/{n} present" in out for n in FIVE), True)
    check("...with no traceback", "Traceback" in out, False)
    check("...and no ESP question, with no ESP in the image", "boot/efi.img" in out, False)

    stub.says(lsdl(["/"], []))
    out = structure()
    check("a listing of nothing: one failure saying so", out.count("the image's files can be listed"),
          1)
    check("...and no file reported missing", "slax/boot/vmlinuz present" in out, False)


@in_a_box
def test_an_esp_nothing_points_at_fails(tmp):
    """An image that carries boot/efi.img and has no EFI El Torito entry fails.

    FOUND BUILDING ON A UEFI IMAGE. A consumer built on a UEFI-bootable base, the way
    LAYERING.md described it, shipped the base's ESP without the entry that lets firmware
    find it, and the structure check passed: "no EFI El Torito entry, and none was
    expected". tiny_iso() has no El Torito catalog at all, so here the listing alone
    decides it.
    """
    iso = tiny_iso(os.path.join(tmp, "slax.iso"))
    stub = Stub(tmp)
    esp = [f"/slax/boot/{n}" for n in FIVE] + ["/boot/efi.img"]
    stub.says(lsdl(["/", "/boot", "/slax", "/slax/boot"], esp), report_lba(esp))
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "structure",
                                                     "iso_assert.py"), iso],
                       env=stub.env(), capture_output=True, text=True, timeout=60)
    check("an ESP and no EFI entry: one failure saying so",
          (p.stdout + p.stderr).count(
              "FAIL EFI El Torito entry present, as boot/efi.img is in the image"), 1)


def base_sha256() -> str:
    """A real target's sha256, read from compat/sources.yaml rather than copied here."""
    import yaml
    doc = yaml.safe_load(open(os.path.join(ROOT, "compat", "sources.yaml")))
    return next(iter(doc["targets"].values()))["sha256"]


@in_a_box
def test_sources_refuses_what_it_could_not_list(tmp):
    """The false pass: with a provenance naming a real base and a listing of nothing,
    `kitchen sources --strict` said "unresolved 0" and exited 0."""
    iso = tiny_iso(os.path.join(tmp, "slax.iso"))
    with open(iso + ".provenance.json", "w") as f:
        json.dump({"kitchen": {"commit": "0123abc", "describe": "0123abc", "dirty": False},
                   "base": {"sha256": base_sha256()}}, f)
    stub = Stub(tmp).says(lsdl(["/"], []))
    p = subprocess.run([sys.executable, os.path.join(ROOT, "lib", "sources.py"), iso, "--strict"],
                       env=stub.env(), capture_output=True, text=True, timeout=60)
    check("sources: refused, not passed", p.returncode, 2)
    check("...saying nothing could be accounted for", "nothing in it can be accounted for"
          in p.stderr, True)


@in_a_box
def test_diff_refuses_before_it_reports(tmp):
    a = tiny_iso(os.path.join(tmp, "a.iso"))
    b = tiny_iso(os.path.join(tmp, "b.iso"))
    stub = Stub(tmp).says(lsdl(["/"], []))
    p = subprocess.run([sys.executable, os.path.join(ROOT, "lib", "diff.py"), a, b],
                       env=stub.env(), capture_output=True, text=True, timeout=60)
    check("diff: refused", p.returncode, 2)
    check("...naming the image", "listed no files in a.iso" in p.stderr, True)
    check("...before printing half a report", p.stdout, "")


def main():
    # One box for every fixture, removed afterwards: the convention of #25.
    box = tempfile.mkdtemp(prefix="test_listing-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_entries_refuses_a_listing_it_cannot_trust,
                   test_check_files_with_plain_data,
                   test_the_structure_check_end_to_end,
                   test_an_esp_nothing_points_at_fails,
                   test_sources_refuses_what_it_could_not_list,
                   test_diff_refuses_before_it_reports]:
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
        print("tests/unit/test_listing.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
