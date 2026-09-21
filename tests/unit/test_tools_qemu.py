#!/usr/bin/env python3
"""Unit tests for tools/qemu/boot.py, the interactive QEMU launcher.

Nothing in CI runs the launcher -- it exists so a person can look at an image -- and no gate
compiles Python under tools/, so without this file a launcher that no longer parses would
pass every commit gate. These drive its --print mode, which builds the same argv a launch
runs, against ISOs a few sectors long written here. No qemu, OVMF, xorriso or real ISO is
needed, which is the point: the gates job has none of them.

Run directly: python3 tests/unit/test_tools_qemu.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.join(_HERE, "..", "..")


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, relpath))
    mod = importlib.util.module_from_spec(spec)
    # Registered before it runs: dataclasses resolve a class's module through sys.modules,
    # and boot.py has dataclasses.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


boot = _load("tools_qemu_boot", "tools/qemu/boot.py")
qemu_boot = _load("qemu_boot", "tests/boot/qemu_boot.py")

FAILURES = []
SECTOR = 2048
# Whatever OVMF variables the developer has exported must not decide a test.
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("OVMF")}


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def fat_image(entry: bytes, at: int = 1024, attr: int = 0x20) -> bytes:
    """A 2048-byte FAT boot sector with one directory entry: enough for esp_loaders."""
    img = bytearray(SECTOR)
    struct.pack_into("<H", img, 11, 512)        # bytes per sector
    struct.pack_into("<H", img, 19, 4)          # total sectors: 4 x 512 = one ISO sector
    img[510:512] = b"\x55\xaa"
    if entry:
        img[at:at + 11] = entry
        img[at + 11] = attr
    return bytes(img)


def make_iso(path: str, *, efi: bool = True, loader: bytes = b"BOOTX64 EFI",
             mbr: bool = False, esp_partition_loader: bytes | None = None,
             loader_at: int = 1024, loader_attr: int = 0x20) -> str:
    """Just enough ISO for lib/isoparse.py: PVD at 16, boot record at 17, terminator at 18,
    an El Torito catalog at 19 (a BIOS entry, and an EFI section pointing at 20), a FAT
    image at 20, and optionally a hybrid MBR whose 0xEF partition points at a second FAT
    image at 21 -- different bytes, so a test can tell which one was read."""
    img = bytearray(SECTOR * 24)

    def vd(kind: int) -> bytearray:
        d = bytearray(SECTOR)
        d[0], d[1:6], d[6] = kind, b"CD001", 1
        return d

    pvd = vd(1)
    struct.pack_into("<I", pvd, 80, 24)
    struct.pack_into("<H", pvd, 128, SECTOR)
    struct.pack_into("<I", pvd, 158, 23)
    struct.pack_into("<I", pvd, 166, SECTOR)
    img[16 * SECTOR:17 * SECTOR] = pvd
    record = vd(0)
    record[7:30] = b"EL TORITO SPECIFICATION"
    struct.pack_into("<I", record, 71, 19)
    img[17 * SECTOR:18 * SECTOR] = record
    img[18 * SECTOR:19 * SECTOR] = vd(255)

    cat = bytearray(SECTOR)
    cat[0], cat[30], cat[31] = 0x01, 0x55, 0xAA                     # validation entry
    cat[32] = 0x88                                                   # BIOS, bootable
    struct.pack_into("<HBxHI", cat, 34, 0, 0, 4, 22)
    if efi:
        cat[64], cat[65] = 0x91, 0xEF                                # last section: EFI
        struct.pack_into("<H", cat, 66, 1)
        cat[96] = 0x88
        struct.pack_into("<HBxHI", cat, 98, 0, 0, 4, 20)
    img[19 * SECTOR:20 * SECTOR] = cat
    img[20 * SECTOR:21 * SECTOR] = fat_image(loader, loader_at, loader_attr)

    if mbr:
        img[510:512] = b"\x55\xaa"
        img[446] = 0x80
        struct.pack_into("<II", img, 446 + 8, 0, 96)
        if esp_partition_loader is not None:
            img[21 * SECTOR:22 * SECTOR] = fat_image(esp_partition_loader)
            img[446 + 16 + 4] = 0xEF
            struct.pack_into("<II", img, 446 + 16 + 8, 21 * SECTOR // 512, 4)
    with open(path, "wb") as f:
        f.write(img)
    return path


@contextlib.contextmanager
def patched(obj, **attrs):
    saved = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(obj, k, v)


def run_main(args: list[str], env: dict | None = None, cwd: str | None = None):
    out, err = io.StringIO(), io.StringIO()
    saved_env, saved_cwd = dict(os.environ), os.getcwd()
    os.environ.clear()
    os.environ.update(CLEAN_ENV if env is None else env)
    if cwd:
        os.chdir(cwd)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = boot.main(["boot.py"] + args)
            except SystemExit as e:           # argparse's own usage errors
                rc = e.code
            except Exception as e:            # noqa: BLE001
                # A crash is a failed check, not the end of the run: a traceback here would
                # stop every later test, and read as fewer failures than there are.
                rc = f"crash: {type(e).__name__}: {e}"
    finally:
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)
    return rc, out.getvalue(), err.getvalue()


def printed_argv(script: str) -> list[str]:
    """The qemu command out of a --print script, as sh would split it."""
    at = script.index("qemu-system-")
    return shlex.split(script[at:].replace("\\\n", ""))


def printed(args: list[str], **kw) -> tuple[int, list[str], str]:
    rc, out, err = run_main(args + ["--print"], **kw)
    return rc, (printed_argv(out) if rc == 0 else []), err


def read_bytes(path: str) -> bytes | None:
    """Contents, or None when the file is gone -- so a check reports it rather than crashing."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def after(argv: list[str], flag: str, n: int = 1) -> list[str]:
    """The n tokens after the first `flag`, or [] -- never a ValueError mid-run."""
    return argv[argv.index(flag):argv.index(flag) + 1 + n] if flag in argv else []


def device_of(argv: list[str], drive: str) -> str:
    return next((v for v in argv if f"drive={drive}" in v and not v.startswith("if=")), "")


def fake_firmware(d: str, code: str = "OVMF_CODE_4M.fd") -> list[str]:
    for name in (code, "OVMF_VARS_4M.fd"):
        with open(os.path.join(d, name), "wb") as f:
            f.write(b"\0" * 64)
    return ["--ovmf-code", os.path.join(d, code), "--ovmf-vars", os.path.join(d, "OVMF_VARS_4M.fd")]


# ---------------------------------------------------------------------- tests --

def test_firmware_must_be_chosen():
    """BIOS and UEFI boot different loaders from different parts of the ISO, so which one a
    person meant to look at is never guessed: there is no default firmware."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        rc, _out, err = run_main([iso, "--print"])
        check("no firmware exits 2", rc, 2)
        check("no firmware names the choice", "--bios --uefi" in err, True)
        rc, _out, _err = run_main([iso, "--bios", "--uefi", "--print"])
        check("both firmwares exit 2", rc, 2)


def test_arch_selects_the_binary():
    """--arch x86 must be a 32-bit-only machine, not a 64-bit one running a 32-bit ISO."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        check("default binary", printed([iso, "--bios"])[1][:1], ["qemu-system-x86_64"])
        check("x86 binary", printed([iso, "--bios", "--arch", "x86"])[1][:1], ["qemu-system-i386"])


def test_uefi_refuses_an_iso_with_no_efi_entry():
    """Stock Slax has no EFI El Torito entry, so OVMF sits in an EFI shell that looks like a
    broken image. The refusal is the feature, and --force must still get past it."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "bios-only.iso"), efi=False)
        rc, _argv, err = printed([iso, "--uefi"])
        check("no EFI entry exits 2", rc, 2)
        check("names the recipe", "uefi-bootable" in err, True)
        rc, _argv, err = printed([iso, "--uefi", "--force"])
        check("--force boots anyway", rc, 0)
        check("--force says what it overrode", "--force given" in err, True)
        check("BIOS is not refused", printed([iso, "--bios"])[0], 0)


def test_the_loader_must_match_the_arch():
    """uefi-bootable ships BOOTX64.EFI only, so 32-bit UEFI firmware finds nothing to load.
    The name only counts as a directory entry: 32-byte aligned, not a directory, not a
    long-name fragment -- file contents can say anything."""
    with tempfile.TemporaryDirectory() as t:
        x64 = make_iso(os.path.join(t, "x64.iso"))
        fw = fake_firmware(t)
        check("x64 loader, x64 firmware", printed([x64, "--uefi"] + fw)[0], 0)
        rc, _argv, err = printed([x64, "--uefi", "--arch", "x86"] + fw)
        check("x64 loader, x86 firmware exits 2", rc, 2)
        check("names the missing loader", "BOOTIA32.EFI" in err, True)
        ia32 = make_iso(os.path.join(t, "ia32.iso"), loader=b"BOOTIA32EFI")
        check("ia32 loader, x86 firmware", printed([ia32, "--uefi", "--arch", "x86"] + fw)[0], 0)
        check("ia32 loader, x64 firmware exits 2", printed([ia32, "--uefi"] + fw)[0], 2)
        for label, kw in (("misaligned", {"loader_at": 1025}),
                          ("a directory", {"loader_attr": 0x10}),
                          ("a long-name fragment", {"loader_attr": 0x0F})):
            iso = make_iso(os.path.join(t, "odd.iso"), loader=b"BOOTIA32EFI", **kw)
            check(f"loader name as {label} is not a loader",
                  printed([iso, "--uefi", "--arch", "x86"] + fw)[0], 2)
        unreadable = make_iso(os.path.join(t, "nofat.iso"))
        with open(unreadable, "r+b") as f:
            f.seek(20 * SECTOR + 510)
            f.write(b"\0\0")
        rc, _argv, err = printed([unreadable, "--uefi", "--arch", "x86"] + fw)
        check("an unreadable ESP is a note, not a refusal", rc, 0)
        check("and the note says it was not checked", "was not checked" in err, True)


def test_usb_needs_an_mbr_and_is_attached_read_only():
    """A stock ISO attached as a stick finds nothing bootable, and -drive opens READ-WRITE by
    default: the guest would hold a writable handle on the image under test. No controller
    at all is "No 'usb-bus' bus found"."""
    with tempfile.TemporaryDirectory() as t:
        plain = make_iso(os.path.join(t, "plain.iso"))
        rc, _argv, err = printed([plain, "--bios", "--iso-bus", "usb"])
        check("no MBR exits 2", rc, 2)
        check("names isohybrid", "isohybrid" in err, True)
        hybrid = make_iso(os.path.join(t, "hybrid.iso"), mbr=True)
        # --no-tablet: the tablet needs xhci too, and would hide a stick that forgot it.
        rc, argv, _err = printed([hybrid, "--bios", "--iso-bus", "usb", "--no-tablet"])
        check("hybrid as a stick", rc, 0)
        check("has an xhci controller", "qemu-xhci,id=xhci" in argv, True)
        drive = next((v for v in argv if v.startswith("if=none,id=iso,")), "")
        check("stick is read-only", "readonly=on" in drive.split(","), True)
        check("stick is not a CD", "media=cdrom" in drive, False)
        check("stick device", device_of(argv, "iso"), "usb-storage,bus=xhci.0,drive=iso,bootindex=0")


def test_usb_under_uefi_reads_the_partition_table():
    """A stick is a disk: firmware finds its ESP through the partition table, not El Torito.
    The two images differ here, so reading the wrong one fails."""
    with tempfile.TemporaryDirectory() as t:
        fw = fake_firmware(t)
        no_esp = make_iso(os.path.join(t, "noesp.iso"), mbr=True)
        rc, _argv, err = printed([no_esp, "--uefi", "--iso-bus", "usb"] + fw)
        check("MBR without an EFI partition exits 2", rc, 2)
        check("says there is no EFI system partition", "no EFI system partition" in err, True)
        both = make_iso(os.path.join(t, "both.iso"), mbr=True, esp_partition_loader=b"BOOTIA32EFI")
        check("stick: the partition's IA32 loader is found",
              printed([both, "--uefi", "--arch", "x86", "--iso-bus", "usb"] + fw)[0], 0)
        check("CD: El Torito's image has no IA32 loader",
              printed([both, "--uefi", "--arch", "x86"] + fw)[0], 2)


def test_print_writes_nothing():
    """--print exists to run somewhere else. A dry run that copies firmware, extracts a kernel
    or creates a disk here has written to the wrong machine -- the same bug `kitchen apply
    --dry-run` once had."""
    def boom(*_a, **_k):
        raise AssertionError("--print reached the launch path")

    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        fw = fake_firmware(t)
        disk = os.path.join(t, "disk.img")
        before = sorted(os.listdir(t))
        with patched(boot, launch=boom, perform=boom, run_qemu=boom, require_tools=boom,
                     probe_display=boom):
            rc, _argv, err = printed([iso, "--uefi", "--kernel-boot", "--disk", disk,
                                      "--disk-create", "ext4"] + fw)
        check("print succeeds", (rc, err if rc else ""), (0, ""))
        check("print left the directory as it was", sorted(os.listdir(t)), before)


def test_the_printed_script_runs_the_planned_argv():
    """What a pasted script hands qemu must be exactly the planned argv, as /bin/sh splits it
    -- spaces, a comma (doubled inside option strings) and a colon (read as a protocol
    unless prefixed ./) included."""
    with tempfile.TemporaryDirectory() as t:
        odd = os.path.join(t, "my isos")
        os.makedirs(odd)
        iso = make_iso(os.path.join(odd, "slax,uefi.iso"))
        fw = fake_firmware(t)
        stubs = os.path.join(t, "bin")
        os.makedirs(stubs)
        for name in ("qemu-system-x86_64", "qemu-img", "mkfs.ext4", "xorriso"):
            with open(os.path.join(stubs, name), "w") as f:
                f.write("#!/bin/sh\n{ printf '%s\\0' \"${0##*/}\" \"$@\"; printf '\\001\\0'; } "
                        ">> \"$STUB_LOG\"\n")
            os.chmod(os.path.join(stubs, name), 0o755)
        args = [iso, "--uefi", "--kernel-boot", "--append", "perchdir=/dev/sda/slax/changes",
                "--disk", "odd:name.img", "--disk-create", "ext4", "--disk-size", "16M",
                "--serial", "file:serial log.txt"] + fw
        rc, out, err = run_main(args + ["--print"], cwd=t)
        check("print succeeds", (rc, err if rc else ""), (0, ""))
        a = boot.parse_args(args)
        cfg = boot.resolve(a, CLEAN_ENV, True, [])
        base = boot.scratch_base(iso)
        want = boot.build_plan(a, cfg, base + ".ovmf-vars.fd", base + ".kernel-boot").argv
        log = os.path.join(t, "stub.log")
        script = os.path.join(t, "run.sh")
        with open(script, "w") as f:
            f.write(out)
        env = dict(CLEAN_ENV, PATH=stubs + os.pathsep + os.environ.get("PATH", ""), STUB_LOG=log)
        r = subprocess.run(["/bin/sh", script], cwd=t, env=env, capture_output=True, text=True)
        check("script exits 0", (r.returncode, r.stderr), (0, ""))
        calls = [c.split(b"\0")[:-1] for c in (read_bytes(log) or b"").split(b"\x01\0") if c]
        calls = [[tok.decode() for tok in c] for c in calls]
        ran = next((c[1:] for c in calls if c[0] == "qemu-system-x86_64"), None)
        check("qemu got the planned argv", ran, want[1:])
        check("the comma is doubled for qemu",
              any("file=" + base.replace(",", ",,") + ".ovmf-vars.fd" in v for v in want), True)
        check("the colon path is prefixed", any(v.endswith("file=./odd:name.img") for v in want), True)
        check("the vars copy was made where it was printed", os.path.isfile(base + ".ovmf-vars.fd"), True)
        made = os.path.join(t, "odd:name.img")
        check("the disk was made", os.path.getsize(made) if os.path.exists(made) else None, 16 << 20)
        check("mkfs.ext4 labelled it",
              any(c[0] == "mkfs.ext4" and "slaxperch" in c for c in calls), True)


def test_printed_lines_never_recreate_an_existing_disk():
    """The disk that holds a saved session must survive the second paste of the same script."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        disk = os.path.join(t, "perch.img")
        rc, out, _err = run_main([iso, "--bios", "--disk", disk, "--disk-create", "ext4", "--print"])
        check("print succeeds", rc, 0)
        with open(disk, "wb") as f:
            f.write(b"precious session")
        prereqs = out.split("qemu-system-", 1)[0] if rc == 0 else "exit 1"
        r = subprocess.run(["/bin/sh", "-c", prereqs], capture_output=True, text=True,
                           env=dict(CLEAN_ENV, PATH="/nonexistent"))
        check("prerequisites exit 0 without touching it", r.returncode, 0)
        check("existing disk untouched", read_bytes(disk), b"precious session")


def test_the_vars_drive_is_always_a_copy():
    """UEFI writes its variables. Pointed at the packaged template it either fails or leaks
    state between boots, and the code image must be read-only."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        fw = fake_firmware(t)
        rc, argv, _err = printed([iso, "--uefi"] + fw)
        pflash = [v for v in argv if v.startswith("if=pflash")]
        check("two pflash drives", len(pflash), 2)
        check("code is read-only", pflash and "readonly=on" in pflash[0], True)
        template = os.path.join(t, "OVMF_VARS_4M.fd")
        check("vars is not the template", any(template in v for v in pflash[1:]), False)
        check("vars is the copy beside the ISO",
              pflash[1:] == [f"if=pflash,format=raw,file={os.path.join(t, 'a.ovmf-vars.fd')}"], True)


def test_smm_firmware_forces_q35():
    """secboot builds abort without SMM, which qemu provides only on q35 -- and .ms.fd and
    .snakeoil.fd are symlinks to secboot, so the name alone misses them."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        fw = fake_firmware(t, code="OVMF_CODE_4M.secboot.fd")
        rc, argv, _err = printed([iso, "--uefi"] + fw)
        check("secboot selects q35 with SMM", after(argv, "-machine"), ["-machine", "q35,smm=on"])
        check("and the secure flash", "driver=cfi.pflash01,property=secure,value=on" in argv, True)
        link = os.path.join(t, "firmware.fd")
        os.symlink(os.path.join(t, "OVMF_CODE_4M.secboot.fd"), link)
        rc, argv, _err = printed([iso, "--uefi", "--ovmf-code", link, "--ovmf-vars", fw[3]])
        check("a symlink to secboot is still SMM", "q35,smm=on" in argv, True)
        rc, _argv, err = printed([iso, "--uefi", "--machine", "pc"] + fw)
        check("explicit pc is refused", rc, 2)
        check("and says why", "needs SMM" in err, True)
        plain = fake_firmware(t)
        check("plain OVMF stays on pc", "pc" in printed([iso, "--uefi"] + plain)[1], True)


def test_print_firmware_is_portable_and_scratch_is_beside_the_iso():
    """--print runs somewhere else, so it must not search this machine for firmware, and its
    scratch files must not land in whatever directory the user is standing in."""
    with tempfile.TemporaryDirectory() as t, tempfile.TemporaryDirectory() as found:
        iso = make_iso(os.path.join(t, "slax.iso"))
        for name in ("OVMF_CODE.fd", "OVMF_VARS.fd"):
            open(os.path.join(found, name), "wb").close()
        search = {"x64": ((found,), ("OVMF_CODE.fd",), ("OVMF_VARS.fd",)),
                  "x86": ((found,), ("OVMF_CODE.fd",), ("OVMF_VARS.fd",))}
        with patched(boot, OVMF_SEARCH=search):
            rc, out, _err = run_main([iso, "--uefi", "--kernel-boot", "--print"], cwd=found)
        argv = printed_argv(out) if rc == 0 else []
        check("x64 print uses the portable code path",
              "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd" in argv, True)
        check("vars copy beside the ISO", f"cp /usr/share/OVMF/OVMF_VARS_4M.fd {t}/slax.ovmf-vars.fd" in out, True)
        check("kernel beside the ISO", os.path.join(t, "slax.kernel-boot", "vmlinuz") in argv, True)
        check("nothing in the current directory", sorted(os.listdir(found)), ["OVMF_CODE.fd", "OVMF_VARS.fd"])
        rc, argv, _err = printed([iso, "--uefi", "--arch", "x86", "--force"])
        check("x86 print uses the secboot build both distros ship",
              "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF32_CODE_4M.secboot.fd" in argv, True)
        check("which means q35 with SMM", "q35,smm=on" in argv, True)


def test_ide_on_q35_is_refused_and_slots_are_counted():
    """q35's ide.N are AHCI ports, so "ide" there would test SATA under the wrong name; its
    ports take no unit= (qemu: "bus supports only 1 units"); pc has four IDE positions and
    the CD holds one of them."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        rc, _argv, err = printed([iso, "--bios", "--machine", "q35", "--iso-bus", "ide"])
        check("q35 ide exits 2", rc, 2)
        check("says it is AHCI", "AHCI" in err, True)
        check("q35 --disk-bus ide exits 2",
              printed([iso, "--bios", "--machine", "q35", "--disk", os.path.join(t, "x.img"), "--disk-bus", "ide"])[0], 2)
        disks = sum((["--disk", os.path.join(t, f"d{i}.img")] for i in range(3)), [])
        rc, argv, _err = printed([iso, "--bios", "--machine", "q35"] + disks)
        check("q35 defaults to sata", device_of(argv, "iso"), "ide-cd,bus=ide.0,drive=iso,bootindex=0")
        check("q35 sata never says unit=", any("unit=" in v for v in argv), False)
        check("q35 disks take the next ports", [device_of(argv, f"disk{i}") for i in range(3)],
              [f"ide-hd,bus=ide.{i + 1},drive=disk{i},bootindex={i + 1}" for i in range(3)])
        four = sum((["--disk", os.path.join(t, f"d{i}.img")] for i in range(4)), [])
        rc, _argv, err = printed([iso, "--bios"] + four)
        check("four IDE disks beside an IDE CD exits 2", rc, 2)
        rc, argv, _err = printed([iso, "--bios", "--iso-bus", "scsi"] + four)
        check("four IDE disks without the CD fit", rc, 0)
        check("the fourth takes the CD's place", device_of(argv, "disk3"),
              "ide-hd,bus=ide.1,unit=0,drive=disk3,bootindex=4")
        rc, argv, _err = printed([iso, "--bios", "--iso-bus", "sata", "--disk", os.path.join(t, "x.img"), "--disk-bus", "sata"])
        check("pc sata adds a controller", "ahci,id=sata0" in argv, True)
        check("pc sata ports", (device_of(argv, "iso"), device_of(argv, "disk0")),
              ("ide-cd,bus=sata0.0,drive=iso,bootindex=0", "ide-hd,bus=sata0.1,drive=disk0,bootindex=1"))


def test_boot_disk_swaps_the_order():
    """--boot-disk boots what was installed, with the ISO still attached as the fallback."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        check("--boot-disk needs --disk", printed([iso, "--bios", "--boot-disk"])[0], 2)
        rc, argv, _err = printed([iso, "--bios", "--disk", os.path.join(t, "hd.qcow2"), "--boot-disk"])
        check("disk first", device_of(argv, "disk0"), "ide-hd,bus=ide.0,unit=0,drive=disk0,bootindex=0")
        check("ISO second", device_of(argv, "iso"), "ide-cd,bus=ide.1,unit=0,drive=iso,bootindex=1")
        check("never -boot", "-boot" in argv, False)


def test_kernel_boot_sets_no_bootindex():
    """qemu gives the kernel loader bootindex 0 itself, and a device claiming 0 as well is a
    hard error: "The bootindex 0 has already been used"."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"), efi=False)
        rc, argv, _err = printed([iso, "--uefi", "--kernel-boot", "--disk", os.path.join(t, "p.img")] + fake_firmware(t))
        check("kernel boot skips the ISO checks", rc, 0)
        check("no bootindex anywhere", any("bootindex=" in v for v in argv), False)
        check("--boot-disk contradicts it",
              printed([iso, "--bios", "--kernel-boot", "--disk", os.path.join(t, "p.img"), "--boot-disk"])[0], 2)
        check("--append needs it", printed([iso, "--bios", "--append", "toram"])[0], 2)


def test_uefi_kernel_boot_keeps_off_ide():
    """After OVMF's direct kernel boot the kernel finds nothing on PIIX IDE: the CD is "Could
    not locate slax data" and an IDE disk is absent. Measured -- so that combination defaults
    to SATA and refuses ide, while --bios --kernel-boot, where IDE works, keeps it."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        fw = fake_firmware(t)
        rc, argv, err = printed([iso, "--uefi", "--kernel-boot", "--disk", os.path.join(t, "p.img")] + fw)
        check("uefi kernel boot defaults the ISO to sata", device_of(argv, "iso"), "ide-cd,bus=sata0.0,drive=iso")
        check("and its disks", device_of(argv, "disk0"), "ide-hd,bus=sata0.1,drive=disk0")
        # The summary says "SATA" whatever happens, so look for the note itself.
        check("and says why", "attaches the ISO and the disks on SATA" in err, True)
        rc, argv, err = printed([iso, "--uefi", "--kernel-boot", "--iso-bus", "scsi", "--disk", os.path.join(t, "p.img")] + fw)
        check("the note names only what it moved", "attaches the disks on SATA" in err, True)
        check("explicit ide ISO exits 2", printed([iso, "--uefi", "--kernel-boot", "--iso-bus", "ide"] + fw)[0], 2)
        check("explicit ide disk exits 2",
              printed([iso, "--uefi", "--kernel-boot", "--disk", os.path.join(t, "p.img"), "--disk-bus", "ide"] + fw)[0], 2)
        rc, argv, _err = printed([iso, "--bios", "--kernel-boot"])
        check("bios kernel boot stays on ide", device_of(argv, "iso"), "ide-cd,bus=ide.1,unit=0,drive=iso")
        rc, argv, _err = printed([iso, "--uefi"] + fw)
        check("uefi through GRUB stays on ide", device_of(argv, "iso"), "ide-cd,bus=ide.1,unit=0,drive=iso,bootindex=0")


def test_a_disk_that_fails_to_be_made_is_not_left_behind():
    """A half-made disk left behind would be taken for a real one by the next run and opened
    unformatted. And a file that exists is never touched, whatever the plan thought."""
    false = shutil.which("false") or "/bin/false"
    with tempfile.TemporaryDirectory() as t:
        disk = os.path.join(t, "perch.img")
        try:
            boot.perform(boot.Step("disk", dest=disk, fmt="ext4", size="16M"), false)
            check("a failing mkfs is refused", "no refusal", "Refusal")
        except boot.Refusal:
            pass
        check("the half-made disk is gone", os.path.exists(disk), False)
        with open(disk, "wb") as f:
            f.write(b"precious session")
        for fmt in ("raw", "ext4", "qcow2"):
            try:
                boot.perform(boot.Step("disk", dest=disk, fmt=fmt, size="16M"), false)
                check(f"{fmt} over an existing file is refused", "no refusal", "Refusal")
            except boot.Refusal:
                pass
            check(f"{fmt}: the existing file is untouched", read_bytes(disk), b"precious session")


def test_kernel_command_line():
    """console=tty0 before ttyS0, because /dev/console is the LAST console= and init must
    print on serial; from= only where the ISO really is /dev/sr0; the user's from= wins."""
    def cmdline(args):
        with tempfile.TemporaryDirectory() as t:
            iso = make_iso(os.path.join(t, "a.iso"), mbr=True)
            argv = printed([iso, "--bios", "--kernel-boot"] + args)[1]
            return (after(argv, "-append") + ["", ""])[1]

    line = cmdline([])
    check("tty0 before ttyS0", 0 <= line.find("console=tty0") < line.find("console=ttyS0"), True)
    for bus in ("ide", "sata", "scsi"):
        check(f"from= on a {bus} CD", "from=/dev/sr0/slax" in cmdline(["--iso-bus", bus]), True)
    check("no from= on a stick", "from=" in cmdline(["--iso-bus", "usb"]), False)
    mine = cmdline(["--append", "from=/dev/sda/slax toram"])
    check("the user's from= is the only one", (mine.count("from="), mine.endswith("toram")), (1, True))


def test_serial_and_monitor_never_both_claim_stdio():
    """qemu refuses -serial mon:stdio with -monitor stdio ("cannot use stdio by multiple
    character devices"), and curses already owns the terminal."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        argv = printed([iso, "--bios"])[1]
        check("default: serial and monitor share stdio", ("mon:stdio" in argv, "-monitor" in argv), (True, False))
        argv = printed([iso, "--bios", "--serial", "file:serial.log"])[1]
        check("serial to a file puts the monitor on stdio",
              after(argv, "-serial", 3), ["-serial", "file:serial.log", "-monitor", "stdio"])
        argv = printed([iso, "--bios", "--display", "curses"])[1]
        check("curses: serial on vc, no stdio monitor", ("vc" in argv, "-monitor" in argv), (True, False))
        check("curses with serial on stdio exits 2",
              printed([iso, "--bios", "--display", "curses", "--serial", "stdio"])[0], 2)


def test_accelerators():
    """A list of accelerators is qemu's own fallback, decided on the machine that runs the
    command -- -enable-kvm decided here would make a printed command wrong somewhere else."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        argv = printed([iso, "--bios"])[1]
        check("auto is kvm then tcg", after(argv, "-accel", 3), ["-accel", "kvm", "-accel", "tcg"])
        check("never -enable-kvm", "-enable-kvm" in argv, False)
        argv = printed([iso, "--bios", "--accel", "tcg"])[1]
        check("tcg alone", argv.count("-accel"), 1)
        check("--cpu host with tcg exits 2", printed([iso, "--bios", "--accel", "tcg", "--cpu", "host"])[0], 2)


def test_ovmf_search_matches_the_harness():
    """tests/boot/qemu_boot.py and this launcher must find the same x64 firmware; a variable
    beats the search, and x86 does not read the x64 variable."""
    check("dirs agree", boot.OVMF_DIRS, qemu_boot.OVMF_DIRS)
    check("code names agree", boot.OVMF_CODE_NAMES, qemu_boot.OVMF_CODE_NAMES)
    check("vars names agree", boot.OVMF_VARS_NAMES, qemu_boot.OVMF_VARS_NAMES)
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        for d, code in ((first, "OVMF_CODE.fd"), (second, "OVMF_CODE_4M.fd")):
            for name in (code, "OVMF_VARS.fd"):
                open(os.path.join(d, name), "wb").close()
        search = dict(boot.OVMF_SEARCH, x64=((first, second), boot.OVMF_CODE_NAMES, boot.OVMF_VARS_NAMES),
                      x86=((first, second), ("OVMF_CODE_4M.fd",), ("OVMF_VARS.fd",)))
        with patched(boot, OVMF_SEARCH=search):
            a = boot.parse_args(["x.iso", "--uefi"])
            check("directory order beats name order", boot.firmware(a, {}, False, [])[0],
                  os.path.join(first, "OVMF_CODE.fd"))
            env = {"OVMF_CODE": os.path.join(second, "OVMF_CODE_4M.fd")}
            check("$OVMF_CODE beats the search", boot.firmware(a, env, False, [])[0], env["OVMF_CODE"])
            a86 = boot.parse_args(["x.iso", "--uefi", "--arch", "x86"])
            wrong = {"OVMF_CODE": os.path.join(first, "OVMF_CODE.fd")}
            check("x86 ignores $OVMF_CODE", boot.firmware(a86, wrong, False, [])[0],
                  os.path.join(second, "OVMF_CODE_4M.fd"))


def test_existing_disk_format_is_read_not_guessed():
    """An existing disk is opened as what it is, and qemu is always told explicitly."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "a.iso"))
        q = os.path.join(t, "q.img")
        with open(q, "wb") as f:
            f.write(b"QFI\xfb" + b"\0" * 60)
        r = os.path.join(t, "r.qcow2")
        with open(r, "wb") as f:
            f.write(b"\0" * 64)
        argv = printed([iso, "--bios", "--disk", q, "--disk", r])[1]
        check("qcow2 by magic", f"if=none,id=disk0,format=qcow2,file={q}" in argv, True)
        check("raw despite the name", f"if=none,id=disk1,format=raw,file={r}" in argv, True)


# ------------------------------------------------------------------ the VNC seat --
# Where the framebuffer is offered, and to whom. This server has no password, so the
# address it binds is a security decision and not a preference -- and the port it binds
# has to be the one asked for, because an ssh tunnel is built for one port before qemu
# starts.


def test_the_vnc_port_is_the_one_asked_for():
    """No walk-up. `:0,to=99` let a busy 5900 become 5901, which is a fine answer for
    someone reading the terminal and a wrong one for a tunnel built before qemu ran: the
    viewer then reaches nothing, or -- on a second launch -- the PREVIOUS boot."""
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "s.iso"))
        rc, argv, err = printed([iso, "--bios"])
        check("default binds display 0", after(argv, "-vnc"), ["-vnc", "127.0.0.1:0"])
        check("and does not walk up", "to=" in " ".join(argv), False)
        # qemu's VNC server takes a DISPLAY NUMBER and listens on 5900+d, so a port has
        # to be converted here -- `-vnc 127.0.0.1:5903` would bind 11803.
        rc, argv, err = printed([iso, "--bios", "--vnc-addr", "127.0.0.1:5903"])
        check("5903 is display 3", after(argv, "-vnc"), ["-vnc", "127.0.0.1:3"])
        rc, argv, err = printed([iso, "--bios", "--vnc-addr", "[fd00::1]:5902"])
        check("a bracketed v6 address is accepted", rc, 0)
        # ...and a v6 literal is bracketed, or the colons of the address run into the
        # colon before the display number and qemu cannot parse either.
        check("v6 is bracketed", after(argv, "-vnc"), ["-vnc", "[fd00::1]:2"])


def test_a_public_vnc_address_is_refused_until_confirmed():
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "s.iso"))
        for addr in ("0.0.0.0", "8.8.8.8", "[::]", "100.64.0.1"):
            rc, out, err = run_main([iso, "--bios", "--print", "--vnc-addr", addr])
            check(f"{addr}: refused", rc, 2)
            check(f"{addr}: says it is not private", "NOT a private address" in err, True)
            check(f"{addr}: names the confirmation", "--vnc-public" in err, True)
            check(f"{addr}: no traceback", "Traceback" in err, False)
            rc, out, err = run_main([iso, "--bios", "--print", "--vnc-addr", addr,
                                     "--vnc-public"])
            check(f"{addr}: accepted when confirmed", rc, 0)
            # ...and every launch says so, which is the other half of the bargain.
            # The banner is on stderr; stdout carries the script itself.
            check(f"{addr}: and is called public", "PUBLIC ADDRESS" in err, True)
        for addr in ("127.0.0.1", "10.1.2.3", "172.17.0.1", "192.168.1.5", "[fd00::1]"):
            rc, out, err = run_main([iso, "--bios", "--print", "--vnc-addr", addr])
            check(f"{addr}: private, so no confirmation needed", rc, 0)
            check(f"{addr}: and is not called public", "PUBLIC ADDRESS" in err, False)
        # --display none binds nothing, so there is no address to be exposed and a
        # refusal would be a refusal about nothing.
        rc, out, err = run_main([iso, "--bios", "--print", "--display", "none",
                                 "--vnc-addr", "0.0.0.0"])
        check("no vnc server, no refusal", rc, 0)


def test_the_viewer_is_told_the_route_that_works():
    with tempfile.TemporaryDirectory() as t:
        iso = make_iso(os.path.join(t, "s.iso"))
        # Loopback is reachable only from the machine qemu runs on: tunnel.
        rc, out, err = run_main([iso, "--bios", "--print", "--vnc-addr", "127.0.0.1:5905"])
        check("loopback gets a tunnel", "ssh -L 5905:127.0.0.1:5905" in err, True)
        check("...and a local viewer", "vncviewer localhost:5905" in err, True)
        # Anything else already answers where it is. Telling someone to build a tunnel to
        # an address they can already reach is telling them to do nothing -- and the rule
        # that tested `not 127.*` handed exactly that advice to a --vnc-public address.
        rc, out, err = run_main([iso, "--bios", "--print", "--vnc-addr", "172.17.0.1"])
        check("a routable address is direct", "vncviewer 172.17.0.1:5900" in err, True)
        check("...with no tunnel to build", "ssh -L" in err, False)
        rc, out, err = run_main([iso, "--bios", "--print", "--vnc-addr", "8.8.8.8",
                                 "--vnc-public"])
        check("a public address is direct too", "vncviewer 8.8.8.8:5900" in err, True)
        check("...and not tunnelled", "ssh -L" in err, False)


def test_the_command_line_sent_to_a_boot_host():
    """What boot.py becomes on the other machine.

    FORWARDED, not rebuilt: the same parser runs there, so every option means the same
    thing. Only what is true HERE and not THERE is rewritten -- the image's path, and the
    choice of where to run.
    """
    got = boot.remote_argv(
        ["out/x.iso", "--bios", "--local", "--mem", "4G", "--vnc-addr", "127.0.0.1:5999"],
        "out/x.iso", "/srv/s/boot-host/runs/r1/x.iso", "127.0.0.1", 5900)
    check("the image is the one over there", got[0], "/srv/s/boot-host/runs/r1/x.iso")
    check("ordinary options are carried", after(got, "--mem"), ["--mem", "4G"])
    check("the firmware choice is carried", "--bios" in got, True)
    # Resolved once, here, and stated exactly: the tunnel is built for this port.
    check("the address is the resolved one", after(got, "--vnc-addr"),
          ["--vnc-addr", "127.0.0.1:5900"])
    check("only one of them", got.count("--vnc-addr"), 1)
    # ...and the far side must not go looking for a boot host of its own.
    check("it is told to boot where it is", got[-1], "--local")
    check("...once", got.count("--local"), 1)
    # A confirmed public address has to travel, or the same rule refuses it over there.
    pub = boot.remote_argv(["out/x.iso", "--bios"], "out/x.iso", "/r/x.iso",
                           "8.8.8.8", 5900)
    check("a public address carries its confirmation", "--vnc-public" in pub, True)
    priv = boot.remote_argv(["out/x.iso", "--bios"], "out/x.iso", "/r/x.iso",
                            "10.0.0.5", 5900)
    check("a private one does not", "--vnc-public" in priv, False)


def test_a_missing_image_is_refused_before_the_boot_host_is_touched():
    """A typo in the path is answered here, not after the tree has been sent.

    The check for the image used to sit BELOW the boot-host dispatch, so a configured host
    never reached it: run() handed straight to boot_host.launch(), which connected, ran the
    pre-run check, swept, opened a run directory, rsync'd the whole tree, and only then
    hashed the image -- dying with a FileNotFoundError traceback out of
    lib/boot_host.py's sha256_file(). 1.6 s of work and somebody else's machine involved,
    to answer a question this side could answer immediately; with --local the same typo
    was refused in a millisecond.

    boot_host.load and .launch are BOTH patched. load, because a tester with a real
    boot-host.ini would otherwise take the remote branch for real -- the test must not
    depend on an untracked file. launch, because reaching it at all is the failure.
    """
    def boom(*_a, **_k):
        raise AssertionError("the boot host was reached for an image that is not here")

    cfg = boot.boot_host.Config("kvmbox", "/srv/s", "127.0.0.1", 5900, False, "x")
    with tempfile.TemporaryDirectory() as t:
        gone = os.path.join(t, "not-here.iso")
        with patched(boot.boot_host, load=lambda *a, **k: cfg, launch=boom):
            rc, _out, err = run_main([gone, "--bios", "--display", "none"])
        check("refused", rc, 2)
        check("...naming the file", f"no such file: {gone}" in err, True)
        # The same answer with no host configured: moving the check must not have changed
        # what the local path says.
        with patched(boot.boot_host, load=lambda *a, **k: None, launch=boom):
            rc, _out, err = run_main([gone, "--bios", "--display", "none"])
        check("the local path answers the same", (rc, f"no such file: {gone}" in err),
              (2, True))
        # ...and --print is still the exception. It writes a script for another machine,
        # so the image is allowed to be absent there.
        with patched(boot.boot_host, load=lambda *a, **k: cfg, launch=boom):
            rc, _out, err = run_main([gone, "--bios", "--print"])
        check("--print still tolerates an absent image", rc, 0)
        check("...and says the boot records went unchecked",
              "boot records were not checked" in err, True)


def main():
    for fn in [test_firmware_must_be_chosen,
               test_arch_selects_the_binary,
               test_uefi_refuses_an_iso_with_no_efi_entry,
               test_the_loader_must_match_the_arch,
               test_usb_needs_an_mbr_and_is_attached_read_only,
               test_usb_under_uefi_reads_the_partition_table,
               test_print_writes_nothing,
               test_the_printed_script_runs_the_planned_argv,
               test_printed_lines_never_recreate_an_existing_disk,
               test_the_vars_drive_is_always_a_copy,
               test_smm_firmware_forces_q35,
               test_print_firmware_is_portable_and_scratch_is_beside_the_iso,
               test_ide_on_q35_is_refused_and_slots_are_counted,
               test_boot_disk_swaps_the_order,
               test_kernel_boot_sets_no_bootindex,
               test_uefi_kernel_boot_keeps_off_ide,
               test_a_disk_that_fails_to_be_made_is_not_left_behind,
               test_kernel_command_line,
               test_serial_and_monitor_never_both_claim_stdio,
               test_accelerators,
               test_ovmf_search_matches_the_harness,
               test_existing_disk_format_is_read_not_guessed,
               test_the_vnc_port_is_the_one_asked_for,
               test_a_public_vnc_address_is_refused_until_confirmed,
               test_the_viewer_is_told_the_route_that_works,
               test_the_command_line_sent_to_a_boot_host,
               test_a_missing_image_is_refused_before_the_boot_host_is_touched]:
        # One test crashing must not stop the rest: the count of failures is only honest if
        # every test ran.
        try:
            fn()
        except Exception as e:                # noqa: BLE001
            traceback.print_exc()
            FAILURES.append(f"{fn.__name__} crashed: {type(e).__name__}: {e}")
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_tools_qemu.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
