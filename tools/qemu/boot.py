#!/usr/bin/env python3
"""Boot a Slax ISO under QEMU so a person can look at it -- or print the commands instead.

  tools/qemu/boot.py ISO (--bios | --uefi) [options]
  tools/qemu/boot.py ISO --uefi --print > run.sh     # for a machine you are not on

Full documentation: docs/60-testing/qemu.md. `--help` lists every option.

WHY THIS IS NOT UNDER tests/. Everything in tests/ is the automated tier: headless,
assertion-driven, run by CI. tests/boot/qemu_boot.py is -display none, -serial to a file,
one QMP screendump, assert, exit. That proves an image did not regress. It cannot tell you
the desktop came up, the browser launches, or the thing is usable -- those need a human
and a window, which is what this is for. Nothing here asserts anything.

WHY VNC IS THE DEFAULT, and it is not a preference. Measured on both the dev container and
the build host on 2026-09-16: `qemu-system-x86_64 -display help` lists exactly `none` and
`curses`, and `ldd` finds zero GUI libraries. Debian's qemu-system-x86 is built without GTK
and SDL. VNC is compiled in separately and works, so on these machines it is the ONLY way
to see a framebuffer at all. It also tunnels over ssh, which is what you want when the ISO
lives on a server.

WHY PYTHON. This replaced boot-bios.sh, boot-uefi.sh and the common.sh they sourced. Most
of the reason is reach -- bus permutations, --print and --kernel-boot are a lot of argv to
build in POSIX sh -- but the shell shape also hid a bug: boot-uefi.sh set
`trap 'rm -f "$VARS"' EXIT` and common.sh then `exec`ed qemu. A trap never runs after exec
(checked in dash and bash), so every UEFI boot left a 540 KB copy of the OVMF variable
store in /tmp, and one was still there when this was written. Here qemu is a child
process, and its scratch directory goes when it exits.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
from dataclasses import dataclass, field

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "lib"))
from isoparse import SECTOR, IsoInfo, IsoReader  # noqa: E402
# The VNC address rules live with the boot-host configuration that also carries one, so
# there is a single answer to "is this address safe to bind an unauthenticated VNC server
# to" rather than two that can disagree.
import boot_host  # noqa: E402

PROG = "boot.py"

BINARY = {"x64": "qemu-system-x86_64", "x86": "qemu-system-i386"}

# Searched in order, and overridable. The x64 lists are tests/boot/qemu_boot.py's, verbatim,
# and tests/unit/test_tools_qemu.py fails if the two drift apart: firmware the harness finds
# and this does not is a UEFI boot that passes in the harness and fails by hand, with nothing
# to say why.
OVMF_DIRS = ("/usr/share/OVMF", "/usr/share/ovmf", "/usr/share/edk2/ovmf", "/usr/share/qemu")
OVMF_CODE_NAMES = ("OVMF_CODE_4M.fd", "OVMF_CODE.fd", "OVMF.fd")
OVMF_VARS_NAMES = ("OVMF_VARS_4M.fd", "OVMF_VARS.fd")
# 32-bit firmware is a separate package, ovmf-ia32, and no list in containers/packages/
# installs it. Ubuntu 24.04 ships OVMF32_CODE_4M.fd and a .secboot.fd beside it; Debian 12
# ships ONLY the .secboot.fd, which cannot run without SMM (see needs_smm).
OVMF32_DIRS = ("/usr/share/OVMF", "/usr/share/ovmf")
OVMF32_CODE_NAMES = ("OVMF32_CODE_4M.fd", "OVMF32_CODE_4M.secboot.fd")
OVMF32_VARS_NAMES = ("OVMF32_VARS_4M.fd",)
OVMF_SEARCH = {"x64": (OVMF_DIRS, OVMF_CODE_NAMES, OVMF_VARS_NAMES),
               "x86": (OVMF32_DIRS, OVMF32_CODE_NAMES, OVMF32_VARS_NAMES)}
# One pair of variables per arch, so an $OVMF_CODE exported for an x64 experiment cannot
# quietly hand 64-bit firmware to qemu-system-i386.
OVMF_ENV = {"x64": ("OVMF_CODE", "OVMF_VARS"), "x86": ("OVMF32_CODE", "OVMF32_VARS")}
OVMF_PACKAGE = {"x64": "ovmf", "x86": "ovmf-ia32"}
# --print CANNOT SEARCH, because the machine it would search is not the one that runs the
# command. A container with Ubuntu's ovmf-ia32 finds OVMF32_CODE_4M.fd and prints a path a
# Debian 12 host does not have. These four exist on both.
PORTABLE_OVMF = {
    "x64": ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
    "x86": ("/usr/share/OVMF/OVMF32_CODE_4M.secboot.fd", "/usr/share/OVMF/OVMF32_VARS_4M.fd"),
}
# Builds that abort without SMM. README.Debian says so of .secboot.fd, and the distribution's
# own descriptors (/usr/share/qemu/firmware/*.json) mark it `requires-smm` with machines
# pc-q35-* only. OVMF_CODE_4M.ms.fd and .snakeoil.fd are symlinks to it, so a name check has
# to look at what a symlink resolves to as well as at the name it was given.
SMM_HINTS = ("secboot", ".ms.", "snakeoil")

# The removable-media loader UEFI firmware looks for, and the FAT 8.3 directory entry that
# holds it: 8 name bytes, space padded, then 3 extension bytes, no dot.
EFI_LOADER = {"x64": ("BOOTX64.EFI", b"BOOTX64 EFI"), "x86": ("BOOTIA32.EFI", b"BOOTIA32EFI")}
ESP_SCAN_CAP = 64 << 20

NATIVE_BUS = {"pc": "ide", "q35": "sata"}
ISO_BUSES = ("ide", "sata", "scsi", "usb")
DISK_BUSES = ("ide", "sata", "scsi", "virtio", "nvme", "usb")
CD_BUSES = ("ide", "sata", "scsi")
SATA_PORTS = 6
NICS = {"e1000": "e1000", "e1000e": "e1000e", "rtl8139": "rtl8139", "pcnet": "pcnet",
        "virtio": "virtio-net-pci"}
ACCEL = {"auto": ("kvm", "tcg"), "kvm": ("kvm",), "tcg": ("tcg",)}

# The direct-kernel command line. It is tests/boot/qemu_boot.py's, which boots all four
# targets, less its from= (kernel_cmdline adds that only where it is true) and plus
# console=tty0 ahead of ttyS0. The kernel writes its messages to every console= it is given,
# but /dev/console -- where livekit's init prints -- is the LAST one, so this order keeps
# init on the serial line and still shows the kernel on the screen.
# recipes/available/serial-console.yaml measured the other order sending init to video.
KERNEL_CMDLINE = ("vga=normal rw printk.time=0 consoleblank=0 automount "
                  "console=tty0 console=ttyS0,115200n8")

ISO_MEDIA = {"ide": "CD-ROM on IDE", "sata": "CD-ROM on SATA (AHCI)",
             "scsi": "CD-ROM on virtio-scsi", "usb": "USB stick on xhci, read-only"}
DISK_MEDIA = {"ide": "IDE", "sata": "SATA", "scsi": "virtio-scsi", "virtio": "virtio-blk",
              "nvme": "NVMe", "usb": "USB"}

UPSTREAM_BUGS = "docs/30-inventory/known-upstream-bugs.md"

NO_UEFI_ENTRY = """\
{name} has no UEFI boot entry.

  El Torito lists BIOS only, so OVMF has nothing to load and you will land in an
  EFI shell. Stock Slax cannot boot on UEFI at all -- upstream bug 1 in
  {bugs}.

  Fix it by applying the uefi-bootable recipe, or by building through a profile
  that already includes it:

      ./kitchen apply uefi-bootable -w work && ./kitchen pack -s work/iso
      ./kitchen build browsers

  Boot it anyway with --force, or use --bios instead."""

NO_MBR = """\
{name} has no MBR signature, so it is not a bootable USB image.

  A stock Slax ISO has none -- upstream bug 2 in {bugs}.
  Apply the isohybrid recipe, or attach it as a CD instead of --iso-bus usb:

      ./kitchen apply isohybrid -w work && ./kitchen pack -s work/iso

  Boot it anyway with --force."""

NO_ESP = """\
{name} has an MBR but no EFI system partition.

  As a USB stick it is a disk, and UEFI firmware looks for its loader in the partition
  table. isohybrid alone makes it dd-able for BIOS; the EFI partition comes from
  applying uefi-bootable as well, as profiles/boot-matrix.yaml does.

  Boot it anyway with --force, or use --bios."""

NO_LOADER = """\
{name} has no {loader} in its EFI system partition.

  {bits} UEFI firmware loads EFI/BOOT/{loader} from removable media, so it has
  nothing to load and you will land in an EFI shell.{why}

  Boot it anyway with --force, or use --bios."""

NO_IA32_LOADER = """
  The uefi-bootable recipe installs BOOTX64.EFI only: nothing in this repository builds
  a 32-bit UEFI loader, so --arch x86 --uefi cannot boot any image it makes. A 32-bit
  ISO boots on 64-bit UEFI firmware with --arch x64."""

Q35_IDE = """\
q35 has no IDE controller: its ide.N buses are the ports of the chipset's AHCI (SATA)
  controller, so --iso-bus/--disk-bus ide there would test SATA while saying IDE.
  Use sata on q35, or --machine pc for real IDE."""

UEFI_KERNEL_IDE = """\
--uefi --kernel-boot cannot use IDE: after OVMF's direct kernel boot, Slax's kernel finds
  nothing on the PIIX IDE controller -- the CD gives "Could not locate slax data" and an IDE
  disk is simply absent. Leave --iso-bus/--disk-bus off (they default to sata here), pick
  sata, scsi or usb, or use --bios, where IDE works."""


class Refusal(RuntimeError):
    """A refusal is an answer, not a crash: main() prints it without a traceback."""


@dataclass
class Disk:
    path: str
    fmt: str            # what -drive is told: qcow2 | raw
    create: str = ""    # qcow2 | raw | ext4 -- how to make it where it does not exist
    size: str = ""


@dataclass
class Config:
    binary: str
    machine: str
    smm: bool
    ovmf_code: str
    ovmf_vars: str
    iso_bus: str
    disk_bus: str
    serial: str
    monitor_stdio: bool
    tablet: bool
    disks: list[Disk] = field(default_factory=list)


@dataclass
class Step:
    kind: str           # vars | kernel | disk
    dest: str
    src: str = ""
    fmt: str = ""
    size: str = ""


@dataclass
class Plan:
    steps: list[Step]
    groups: list[list[str]]

    @property
    def argv(self) -> list[str]:
        return [tok for group in self.groups for tok in group]


# ------------------------------------------------------------------ arguments --

def mem_mib(text: str) -> int:
    m = re.fullmatch(r"(\d+)([MG]?)", text.strip(), re.I)
    if not m or int(m.group(1)) == 0:
        raise argparse.ArgumentTypeError(f"wants MiB, or a number with an M or G suffix: {text!r}")
    return int(m.group(1)) * (1024 if m.group(2).upper() == "G" else 1)


def size_arg(text: str) -> str:
    # A suffix is required: a bare number is bytes to both qemu-img and truncate, and an
    # 8-byte disk is never what anyone meant.
    m = re.fullmatch(r"(\d+)([KMGT])", text.strip(), re.I)
    if not m or int(m.group(1)) == 0:
        raise argparse.ArgumentTypeError(f"wants a size with a unit, like 256M or 8G: {text!r}")
    return m.group(1) + m.group(2).upper()


def size_bytes(size: str) -> int:
    return int(size[:-1]) << {"K": 10, "M": 20, "G": 30, "T": 40}[size[-1]]


def count_arg(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"wants a whole number of at least 1: {text!r}")
    return int(text)


def port_arg(text: str) -> int:
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise argparse.ArgumentTypeError(f"wants a TCP port, 1-65535: {text!r}")
    return int(text)


def vnc_arg(text: str) -> tuple:
    """IP[:PORT], or [v6]:PORT -- the address qemu binds, where qemu runs."""
    try:
        return boot_host.parse_vnc(text)
    except boot_host.ConfigError as e:
        raise argparse.ArgumentTypeError(str(e).replace("vnc: ", "", 1))


def serial_arg(text: str) -> str:
    if text in ("stdio", "vc", "pty", "none"):
        return text
    if text.startswith("file:") and len(text) > 5:
        if "," in text:
            raise argparse.ArgumentTypeError("qemu cannot take a comma in a serial log path")
        return text
    raise argparse.ArgumentTypeError(f"wants stdio, vc, pty, none or file:PATH: {text!r}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog=PROG, formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Boot a Slax ISO under QEMU so you can look at it, or --print the "
                    "commands\nfor another machine. Asserts nothing: that is tests/.\n\n"
                    "  tools/qemu/boot.py out/slax.iso --bios\n"
                    "  tools/qemu/boot.py out/slax.iso --uefi --iso-bus usb\n"
                    "  tools/qemu/boot.py out/slax.iso --bios --kernel-boot "
                    "--disk /var/tmp/perch.img \\\n"
                    "      --disk-create ext4 --append perchdir=/dev/sda/slax/changes\n"
                    "  tools/qemu/boot.py out/slax.iso --uefi --print > run.sh\n\n"
                    "Documentation: docs/60-testing/qemu.md")
    ap.add_argument("iso", help="the ISO to boot")

    fw = ap.add_argument_group("firmware (pick one)")
    pick = fw.add_mutually_exclusive_group(required=True)
    pick.add_argument("--bios", dest="firmware", action="store_const", const="bios",
                      help="SeaBIOS, through isolinux; boots any Slax ISO")
    pick.add_argument("--uefi", dest="firmware", action="store_const", const="uefi",
                      help="OVMF, through GRUB; needs the uefi-bootable recipe")
    fw.add_argument("--ovmf-code", metavar="FILE",
                    help="OVMF code image (else $OVMF_CODE, $OVMF32_CODE for x86, else searched; "
                         "--print uses fixed Debian/Ubuntu paths instead of searching)")
    fw.add_argument("--ovmf-vars", metavar="FILE",
                    help="OVMF variable store TEMPLATE; qemu always gets a private copy")

    m = ap.add_argument_group("machine")
    m.add_argument("--arch", choices=("x64", "x86"), default="x64",
                   help="x64: qemu-system-x86_64 (default); x86: qemu-system-i386, a "
                        "32-bit-only machine")
    m.add_argument("--machine", choices=("pc", "q35"),
                   help="pc: i440FX + PIIX IDE (default); q35: ICH9 + AHCI. SMM firmware "
                        "forces q35")
    m.add_argument("-m", "--mem", type=mem_mib, default=2048, metavar="SIZE",
                   help="guest RAM in MiB, or with an M/G suffix (default 2048)")
    m.add_argument("--smp", type=count_arg, default=2, metavar="N", help="vCPUs (default 2)")
    m.add_argument("--cpu", metavar="MODEL", help="qemu CPU model; default is qemu's own")
    m.add_argument("--accel", choices=tuple(ACCEL), default="auto",
                   help="auto: kvm, falling back to tcg (default)")

    b = ap.add_argument_group("boot media")
    b.add_argument("--iso-bus", choices=ISO_BUSES,
                   help="ide|sata|scsi attach a CD-ROM; usb a read-only stick (needs "
                        "isohybrid). Default: the machine's own, ide on pc and sata on q35; "
                        "sata with --uefi --kernel-boot")
    b.add_argument("--kernel-boot", action="store_true",
                   help="skip the bootloader: load the ISO's vmlinuz and initrfs.img "
                        "directly, console on ttyS0")
    b.add_argument("--append", metavar="PARAMS",
                   help="extra kernel parameters, with --kernel-boot")
    b.add_argument("--force", action="store_true",
                   help="boot an ISO the checks say cannot boot this way")

    d = ap.add_argument_group("disks")
    d.add_argument("--disk", action="append", default=[], metavar="FILE",
                   help="attach a disk, creating it if absent; repeatable; never deleted")
    d.add_argument("--disk-bus", choices=DISK_BUSES,
                   help="bus for every --disk. Default: chosen by the same rule as --iso-bus, "
                        "not copied from it")
    d.add_argument("--disk-size", type=size_arg, default="8G", metavar="SIZE",
                   help="size of a disk being created (default 8G)")
    d.add_argument("--disk-create", choices=("qcow2", "raw", "ext4"), default="qcow2",
                   help="how to create a missing disk (default qcow2); ext4 is a raw file "
                        "formatted for perchdir=")
    d.add_argument("--boot-disk", action="store_true",
                   help="boot the first disk instead of the ISO")

    io = ap.add_argument_group("display and I/O")
    io.add_argument("--display", choices=("vnc", "curses", "none", "gtk", "sdl"), default="vnc",
                    help="default vnc; see the docs for why")
    io.add_argument("--vnc-addr", type=vnc_arg, default=None, metavar="IP[:PORT]",
                    help="where the VNC server listens, on the machine running qemu "
                         "(default 127.0.0.1:5900, or boot-host.ini's vnc) -- tunnel in, "
                         "do not expose")
    io.add_argument("--vnc-public", action="store_true",
                    help="confirm a --vnc-addr outside the private ranges. This server "
                         "has no password, so it is refused without this")
    io.add_argument("--vga", choices=("std", "virtio", "cirrus", "vmware", "none"), default="std",
                    help="video card (default std)")
    io.add_argument("--serial", type=serial_arg, metavar="MODE",
                    help="stdio (default: shared with the monitor), vc, pty, none or "
                         "file:PATH; vc under curses")
    io.add_argument("--tablet", action=argparse.BooleanOptionalAction, default=None,
                    help="USB tablet, so a VNC pointer tracks; default on for vnc, gtk, sdl")

    n = ap.add_argument_group("network")
    n.add_argument("--nic", choices=tuple(NICS) + ("none",), default="e1000",
                   help="network card on user-mode networking (default e1000)")
    n.add_argument("--ssh-port", type=port_arg, metavar="PORT",
                   help="forward 127.0.0.1:PORT to the guest's port 22")

    o = ap.add_argument_group("output")
    o.add_argument("--local", action="store_true",
                   help="boot here, even if boot-host.ini names a machine with KVM")
    o.add_argument("--print", dest="print_only", action="store_true",
                   help="print the commands for this boot instead of running them")
    a = ap.parse_args(argv)
    # RESOLVED HERE, so a parsed Namespace is COMPLETE. These began life as attributes
    # that run() attached on the way past, which broke every caller that builds a plan
    # from parse_args() alone -- tests/unit/test_tools_qemu.py does exactly that, and said
    # so. A boot host overrides them afterwards, because only then is there another
    # machine whose address it could be.
    a.vnc_ip, a.vnc_port, a.vnc_from = (
        (a.vnc_addr[0], a.vnc_addr[1], "--vnc-addr") if a.vnc_addr
        else ("127.0.0.1", boot_host.VNC_BASE, "default"))
    return a


# ------------------------------------------------------------------- firmware --

def _first_file(d: str, names: tuple[str, ...]) -> str:
    return next((os.path.join(d, nm) for nm in names if os.path.isfile(os.path.join(d, nm))), "")


def firmware(a: argparse.Namespace, env, printing: bool, notes: list[str]) -> tuple[str, str]:
    code_var, vars_var = OVMF_ENV[a.arch]
    code = a.ovmf_code or env.get(code_var, "")
    varsf = a.ovmf_vars or env.get(vars_var, "")
    pkg = OVMF_PACKAGE[a.arch]
    if printing:
        if not (code and varsf):
            notes.append(f"firmware paths are the {pkg} ones Debian 12 and Ubuntu 24.04 both "
                         f"ship; --ovmf-code/--ovmf-vars point elsewhere")
        portable_code, portable_vars = PORTABLE_OVMF[a.arch]
        return code or portable_code, varsf or portable_vars
    dirs, code_names, vars_names = OVMF_SEARCH[a.arch]
    for d in dirs:
        code = code or _first_file(d, code_names)
        varsf = varsf or _first_file(d, vars_names)
    if not code:
        raise Refusal(f"no {a.arch} OVMF firmware found (apt-get install {pkg}), "
                      f"or pass --ovmf-code / set ${code_var}")
    if not os.path.isfile(code):
        raise Refusal(f"the OVMF code image is not a file: {code}")
    if not varsf:
        raise Refusal(f"found {code} but no variable store to go with it "
                      f"(pass --ovmf-vars or set ${vars_var})")
    if not os.path.isfile(varsf):
        raise Refusal(f"the OVMF variable store is not a file: {varsf}")
    return code, varsf


def needs_smm(code: str) -> bool:
    names = (os.path.basename(code), os.path.basename(os.path.realpath(code)))
    return any(hint in nm for nm in names for hint in SMM_HINTS)


# ---------------------------------------------------------------- resolution --

def qemu_file(path: str) -> str:
    """A path as it has to appear inside a qemu option string.

    Two things in a filename change what qemu reads. A comma ends the option, so qemu wants
    it doubled. And a colon before the first slash reads as a protocol: `nosuch:file.iso` is
    refused with "Unknown protocol 'nosuch'" while `./nosuch:file.iso` opens.
    """
    if ":" in path.split("/", 1)[0]:
        path = "./" + path
    return path.replace(",", ",,")


def image_format(path: str) -> str:
    """qcow2 or raw, read from the header here rather than left to qemu's probe.

    Left to guess, qemu warns on every boot, and the format appears nowhere in the command --
    which matters for --print, whose output has to say what it will open. Reading the magic
    ourselves does not remove the probe's known hazard: a guest that writes a qcow2 header
    onto sector 0 of a raw disk turns that disk into a qcow2 image, one that may name a
    backing file, at the next boot. For a disk attached to your own test image that risk is
    accepted, and this comment is here so it is accepted knowingly.
    """
    try:
        with open(path, "rb") as f:
            return "qcow2" if f.read(4) == b"QFI\xfb" else "raw"
    except OSError:
        return "raw"


def plan_disks(a: argparse.Namespace, printing: bool, notes: list[str]) -> list[Disk]:
    disks = []
    for path in a.disk:
        # exists, not isfile: /dev/sdb is a perfectly good disk to attach.
        if os.path.exists(path):
            fmt = image_format(path)
            create = ""
            if printing:
                # The printed line only creates what is missing on the machine that runs
                # it, and must create the format -drive will be told.
                create = "qcow2" if fmt == "qcow2" else ("ext4" if a.disk_create == "ext4" else "raw")
                notes.append(f"{path} exists here and is opened as {fmt}; the printed "
                             f"commands create one only where it is missing")
            disks.append(Disk(path, fmt, create, a.disk_size))
        else:
            fmt = "qcow2" if a.disk_create == "qcow2" else "raw"
            disks.append(Disk(path, fmt, a.disk_create, a.disk_size))
    return disks


def resolve(a: argparse.Namespace, env, printing: bool, notes: list[str]) -> Config:
    """Everything decided before anything is touched, including every usage refusal."""
    if a.append is not None and not a.kernel_boot:
        raise Refusal("--append needs --kernel-boot: a boot through the menu takes its kernel "
                      "parameters from the ISO, not from qemu")
    if a.boot_disk and not a.disk:
        raise Refusal("--boot-disk needs --disk")
    if a.boot_disk and a.kernel_boot:
        raise Refusal("--boot-disk and --kernel-boot contradict each other: with --kernel-boot "
                      "qemu loads the kernel itself and boots no disk")
    if a.ssh_port and a.nic == "none":
        raise Refusal("--ssh-port needs a network card: drop --nic none")
    if a.cpu == "host":
        if a.accel == "tcg":
            raise Refusal("--cpu host needs KVM, which --accel tcg rules out: try --cpu max")
        if a.accel == "auto":
            if printing:
                notes.append("--cpu host works only where KVM does; the tcg fallback refuses it")
            elif not os.access("/dev/kvm", os.W_OK):
                raise Refusal("--cpu host needs KVM, and with no writable /dev/kvm here qemu "
                              "would fall back to TCG and refuse it: try --cpu max")

    code = varsf = ""
    smm = False
    if a.firmware == "uefi":
        code, varsf = firmware(a, env, printing, notes)
        smm = needs_smm(code)
    if smm and a.machine == "pc":
        raise Refusal(f"{os.path.basename(code)} needs SMM, and OVMF's SMM builds run only on "
                      f"q35: drop --machine pc, or point --ovmf-code at a build without secboot")
    machine = a.machine or ("q35" if smm else "pc")
    if machine == "q35" and "ide" in (a.iso_bus, a.disk_bus):
        raise Refusal(Q35_IDE)
    # OVMF'S DIRECT KERNEL BOOT LEAVES PIIX IDE DARK. Measured 2026-09-17 on pc: with --uefi
    # --kernel-boot, ata_piix registers both channels and finds no device on either, so an
    # IDE CD ends in "Could not locate slax data" and an IDE disk is silently absent -- while
    # the same boot finds its data on sata, scsi and usb, SeaBIOS finds the IDE CD, and OVMF
    # booting through GRUB finds it too. So there the default is sata, and ide is refused.
    native = NATIVE_BUS[machine]
    if a.firmware == "uefi" and a.kernel_boot:
        if "ide" in (a.iso_bus, a.disk_bus):
            raise Refusal(UEFI_KERNEL_IDE)
        moved = (["the ISO"] if a.iso_bus is None else []) + (
            ["the disks"] if a.disk and a.disk_bus is None else [])
        if native == "ide" and moved:
            notes.append(f"--uefi --kernel-boot attaches {' and '.join(moved)} on SATA: after "
                         f"OVMF's direct kernel boot the kernel finds nothing on IDE")
        native = "sata"
    iso_bus = a.iso_bus or native
    disk_bus = a.disk_bus or native

    serial = a.serial or ("vc" if a.display == "curses" else "stdio")
    if a.display == "curses":
        if serial == "stdio":
            raise Refusal("--display curses draws in this terminal, so the serial line cannot "
                          "have it too: pick --serial vc, pty, none or file:PATH")
        if not printing and not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise Refusal("--display curses needs a terminal to draw in, and this is not one")
    tablet = a.tablet if a.tablet is not None else a.display in ("vnc", "gtk", "sdl")

    disks = plan_disks(a, printing, notes)
    if disks and disk_bus == "ide":
        free = 3 if iso_bus == "ide" else 4
        if len(disks) > free:
            raise Refusal(f"pc has {free} free IDE positions and {len(disks)} disks were given: "
                          f"put them on --disk-bus sata, scsi, virtio or nvme")
    if disks and disk_bus == "sata":
        free = SATA_PORTS - (1 if iso_bus == "sata" else 0)
        if len(disks) > free:
            raise Refusal(f"the SATA controller has {free} free ports and {len(disks)} disks "
                          f"were given: put some on --disk-bus scsi, virtio or nvme")

    return Config(binary=BINARY[a.arch], machine=machine, smm=smm, ovmf_code=code,
                  ovmf_vars=varsf, iso_bus=iso_bus, disk_bus=disk_bus, serial=serial,
                  monitor_stdio=serial != "stdio" and a.display != "curses", tablet=tablet,
                  disks=disks)


# ---------------------------------------------------------------- ISO checks --

def eltorito_efi_offset(info: IsoInfo) -> int | None:
    entry = next((e for e in info.boot_entries
                  if e.kind in ("initial", "section") and e.platform == 0xEF), None)
    return entry.lba * SECTOR if entry else None


def esp_loaders(path: str, offset: int) -> set[str] | None:
    """The removable-media loaders in the FAT image at `offset`, or None if it will not parse.

    A byte search, not a FAT driver: the loader's 8.3 name has to sit at a 32-byte boundary
    (every directory entry does, counted from the start of the image) with an attribute byte
    that is neither a long-name fragment nor a directory. Validated against the ESP in a
    boot-matrix ISO, where BOOTX64 EFI sits at offset 32832 and BOOTIA32EFI nowhere.
    """
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            boot = f.read(512)
            if len(boot) < 512 or boot[510:512] != b"\x55\xaa":
                return None
            bps = struct.unpack_from("<H", boot, 11)[0]
            total = struct.unpack_from("<H", boot, 19)[0] or struct.unpack_from("<I", boot, 32)[0]
            if bps not in (512, 1024, 2048, 4096) or not total:
                return None
            f.seek(offset)
            blob = f.read(min(bps * total, ESP_SCAN_CAP))
    except OSError:
        return None
    found = set()
    for name, entry in EFI_LOADER.values():
        at = blob.find(entry)
        while at >= 0:
            if at % 32 == 0 and at + 12 <= len(blob):
                attr = blob[at + 11]
                if attr != 0x0F and not attr & 0x10:
                    found.add(name)
                    break
            at = blob.find(entry, at + 1)
    return found


def check_iso(a: argparse.Namespace, cfg: Config, notes: list[str]) -> None:
    """Refuse an ISO that cannot boot the way it was asked to, before qemu starts.

    Refusing is the point. Stock Slax has no EFI boot entry and no MBR, so a UEFI run sits in
    an EFI shell and a USB run finds no bootable disk -- both of which look like a broken
    image rather than a missing recipe. Saying so costs a second; finding out costs a boot.
    """
    if a.kernel_boot or not os.path.isfile(a.iso):
        return      # qemu loads the kernel itself; or --print for a path not on this machine
    try:
        with IsoReader(a.iso) as r:
            info = r.info()
    except (OSError, struct.error, ValueError, IndexError) as e:
        notes.append(f"could not read {a.iso}'s boot records ({e}), so nothing was checked")
        return
    name = os.path.basename(a.iso)
    usb = cfg.iso_bus == "usb"
    problems = []
    if usb and not info.isohybrid_mbr:
        problems.append(NO_MBR.format(name=name, bugs=UPSTREAM_BUGS))
    elif a.firmware == "uefi":
        esp = None
        if usb:
            # A stick is a disk: firmware finds its ESP in the partition table, not in El
            # Torito. xorriso's hybrid layout points both at the same bytes.
            esp = next((p["start_lba"] * 512 for p in info.partitions if p["type"] == 0xEF), None)
            if esp is None and not info.gpt:
                problems.append(NO_ESP.format(name=name))
            esp = esp if esp is not None else eltorito_efi_offset(info)
        else:
            esp = eltorito_efi_offset(info)
            if esp is None:
                problems.append(NO_UEFI_ENTRY.format(name=name, bugs=UPSTREAM_BUGS))
        loader = EFI_LOADER[a.arch][0]
        if not problems and esp is not None:
            loaders = esp_loaders(a.iso, esp)
            if loaders is None:
                # "I could not check" and "I checked and it is fine" are different claims.
                notes.append(f"could not read the EFI system partition, so {loader} was not checked")
            elif loader not in loaders:
                problems.append(NO_LOADER.format(
                    name=name, loader=loader, bits="32-bit" if a.arch == "x86" else "64-bit",
                    why=NO_IA32_LOADER if a.arch == "x86" else ""))
    if problems:
        if not a.force:
            raise Refusal("\n\n".join(problems))
        for p in problems:
            notes.append(f"--force given, booting anyway: {p.splitlines()[0]}")


# ------------------------------------------------------------------ the plan --

def kernel_cmdline(iso_bus: str, append: str | None) -> str:
    """KERNEL_CMDLINE, plus from= when the ISO is a CD, plus --append.

    from=/dev/sr0/slax only fits a CD: a USB stick is some /dev/sdX, so there livekit is left
    to search every device, as it does on real hardware. And a from= in --append wins --
    two would leave the answer to whichever livekit happens to read.
    """
    parts = [KERNEL_CMDLINE]
    if iso_bus in CD_BUSES and not re.search(r"(^|\s)from=", append or ""):
        parts.append("from=/dev/sr0/slax")
    if append and append.strip():
        parts.append(append.strip())
    return " ".join(parts)


def build_plan(a: argparse.Namespace, cfg: Config, vars_copy: str, kernel_dir: str) -> Plan:
    """The steps to run first and the qemu argv, in printed lines. Touches nothing."""
    steps: list[Step] = []
    groups: list[list[str]] = [[cfg.binary]]

    machine = ["-machine", cfg.machine + (",smm=on" if cfg.smm else "")]
    if cfg.smm:
        machine += ["-global", "driver=cfi.pflash01,property=secure,value=on"]
    groups.append(machine)
    # Never -enable-kvm: a list of accelerators is qemu's own fallback, decided on the
    # machine that runs the command, which is what makes a printed command portable.
    accel = [tok for acc in ACCEL[a.accel] for tok in ("-accel", acc)]
    groups.append(accel + (["-cpu", a.cpu] if a.cpu else []))
    groups.append(["-m", str(a.mem), "-smp", str(a.smp)])

    if a.firmware == "uefi":
        # OVMF_VARS IS COPIED PER RUN. The packaged one is read-only and shared; UEFI writes
        # its variables, so pointing qemu at the original either fails or leaks state
        # between runs.
        steps.append(Step("vars", dest=vars_copy, src=cfg.ovmf_vars))
        groups.append(["-drive", f"if=pflash,format=raw,readonly=on,file={qemu_file(cfg.ovmf_code)}"])
        groups.append(["-drive", f"if=pflash,format=raw,file={qemu_file(vars_copy)}"])

    if a.kernel_boot:
        steps.append(Step("kernel", dest=kernel_dir, src=a.iso))
        groups.append(["-kernel", os.path.join(kernel_dir, "vmlinuz"),
                       "-initrd", os.path.join(kernel_dir, "initrfs.img")])
        groups.append(["-append", kernel_cmdline(cfg.iso_bus, a.append)])

    buses = {cfg.iso_bus} | ({cfg.disk_bus} if cfg.disks else set())
    if "sata" in buses and cfg.machine == "pc":
        groups.append(["-device", "ahci,id=sata0"])
    if "scsi" in buses:
        groups.append(["-device", "virtio-scsi-pci,id=scsi0"])
    if "usb" in buses or cfg.tablet:
        # `-device usb-storage` with no controller dies with "No 'usb-bus' bus found": a
        # machine type with no USB controller has nothing to plug a stick into. xhci rather
        # than -usb's UHCI, because USB 2.0+ is what any stick made this century speaks.
        groups.append(["-device", "qemu-xhci,id=xhci"])

    def sata_bus(port: int) -> str:
        # q35's own AHCI ports are named ide.0-ide.5 and take no unit=; on pc they belong
        # to the ahci controller added above.
        return f"ide.{port}" if cfg.machine == "q35" else f"sata0.{port}"

    # BOOTINDEX, and none at all with --kernel-boot: qemu gives the kernel loader index 0
    # itself, and a second device claiming 0 is a hard error ("The bootindex 0 has already
    # been used"). Measured 2026-09-17, pc and q35.
    def boot_order(n: int) -> str:
        return "" if a.kernel_boot else f",bootindex={n}"

    # A CD is read-only by nature; the stick is not, and -drive opens READ-WRITE by default.
    # readonly=on either way: a boot that can alter the image under test is not looking at it.
    iso_media = "" if cfg.iso_bus == "usb" else "media=cdrom,"
    groups.append(["-drive", f"if=none,id=iso,{iso_media}readonly=on,format=raw,"
                             f"file={qemu_file(a.iso)}"])
    iso_device = {
        "ide": "ide-cd,bus=ide.1,unit=0",          # where -cdrom puts it: /dev/sr0, hdc
        "sata": f"ide-cd,bus={sata_bus(0)}",
        "scsi": "scsi-cd,bus=scsi0.0",
        "usb": "usb-storage,bus=xhci.0",
    }[cfg.iso_bus]
    groups.append(["-device", f"{iso_device},drive=iso{boot_order(1 if a.boot_disk else 0)}"])

    # IDE positions around the CD. The first disk gets ide.0 unit 0, which is /dev/sda in
    # the guest -- what Slax's bootinst.sh expects to find and write an MBR to.
    ide_slots = [(0, 0), (0, 1), (1, 1)] + ([] if cfg.iso_bus == "ide" else [(1, 0)])
    sata_port = 1 if cfg.iso_bus == "sata" else 0
    for i, disk in enumerate(cfg.disks):
        if disk.create:
            steps.append(Step("disk", dest=disk.path, fmt=disk.create, size=disk.size))
        groups.append(["-drive", f"if=none,id=disk{i},format={disk.fmt},file={qemu_file(disk.path)}"])
        if cfg.disk_bus == "ide":
            bus, unit = ide_slots[i]
            device = f"ide-hd,bus=ide.{bus},unit={unit}"
        elif cfg.disk_bus == "sata":
            device = f"ide-hd,bus={sata_bus(sata_port + i)}"
        elif cfg.disk_bus == "scsi":
            device = "scsi-hd,bus=scsi0.0"
        elif cfg.disk_bus == "virtio":
            device = "virtio-blk-pci"
        elif cfg.disk_bus == "nvme":
            device = f"nvme,serial=slaxdisk{i}"
        else:
            device = "usb-storage,bus=xhci.0"
        order = 0 if (a.boot_disk and i == 0) else i + 1
        groups.append(["-device", f"{device},drive=disk{i}{boot_order(order)}"])

    if a.nic == "none":
        groups.append(["-nic", "none"])
    else:
        nic = f"user,model={NICS[a.nic]}"
        if a.ssh_port:
            nic += f",hostfwd=tcp:127.0.0.1:{a.ssh_port}-:22"
        groups.append(["-nic", nic])

    display = ["-vga", a.vga]
    # AN EXACT PORT, AND NO WALK-UP. This was `:0,to=99`, so a busy 5900 became 5901 and
    # qemu printed the port it got. That is a fine answer for someone reading the terminal
    # and a wrong one for everything else: the ssh tunnel is built for one port before
    # qemu starts, so a walk-up leaves the viewer connected to a tunnel that reaches
    # nothing -- or, on a second launch, reaches the PREVIOUS boot, which looks like it
    # worked. Binding exactly what was asked for means a busy port fails and says so.
    #
    # `:d`, not `:port`: qemu's VNC server takes a DISPLAY NUMBER and listens on 5900+d,
    # so `-vnc 127.0.0.1:5900` would bind 11800. The port is turned into a display here,
    # which is the one place that conversion belongs.
    display += (["-vnc", f"{boot_host.bracket(a.vnc_ip)}:"
                 f"{a.vnc_port - boot_host.VNC_BASE}"]
                if a.display == "vnc" else ["-display", a.display])
    if cfg.tablet:
        display += ["-device", "usb-tablet,bus=xhci.0"]
    groups.append(display)

    # mon:stdio multiplexes the monitor onto the serial line, and qemu refuses a second
    # stdio device -- so -monitor stdio only when the serial line went somewhere else.
    serial = ["-serial", "mon:stdio" if cfg.serial == "stdio" else cfg.serial]
    groups.append(serial + (["-monitor", "stdio"] if cfg.monitor_stdio else []))
    return Plan(steps, groups)


def step_line(s: Step) -> str:
    q = shlex.quote
    if s.kind == "vars":
        return f"cp {q(s.src)} {q(s.dest)}"
    if s.kind == "kernel":
        kernel, initrd = os.path.join(s.dest, "vmlinuz"), os.path.join(s.dest, "initrfs.img")
        return (f"mkdir -p {q(s.dest)} && rm -f {q(kernel)} {q(initrd)} && "
                f"xorriso -osirrox on -indev {q(s.src)} -extract /slax/boot/vmlinuz {q(kernel)} "
                f"-extract /slax/boot/initrfs.img {q(initrd)} --")
    if s.fmt == "qcow2":
        make = f"qemu-img create -f qcow2 {q(s.dest)} {q(s.size)}"
    elif s.fmt == "raw":
        make = f"truncate -s {q(s.size)} {q(s.dest)}"
    else:
        # /usr/sbin is not always on PATH -- a non-login ssh session is how this repo found
        # out, and it hides mkfs.ext4 rather than reporting it missing.
        make = (f"{{ truncate -s {q(s.size)} {q(s.dest)} && PATH=\"$PATH:/usr/sbin:/sbin\" "
                f"mkfs.ext4 -F -q -L slaxperch {q(s.dest)}; }}")
    # Only where it is missing: a printed command must never reformat the disk that holds
    # the session you are trying to resume.
    return f"[ -e {q(s.dest)} ] || {make}"


def render_script(plan: Plan) -> str:
    """POSIX sh, and nothing else, on stdout. No comment lines: an interactive zsh treats
    `#` as a command unless INTERACTIVE_COMMENTS is set, and this is meant for pasting."""
    lines = [step_line(s) for s in plan.steps]
    lines.append(" \\\n    ".join(" ".join(shlex.quote(t) for t in g) for g in plan.groups))
    return "\n".join(lines) + "\n"


def scratch_base(iso: str) -> str:
    """Where --print puts the vars copy and the extracted kernel: beside the ISO, as typed.

    Not the current directory, which is usually the repository root -- and .gitignore has
    no rule for *.fd. Beside the ISO is normally out/, which it does, and it is where
    `kitchen test` already puts its boot-tests/.
    """
    stem = os.path.basename(iso)
    if stem.lower().endswith(".iso"):
        stem = stem[:-4]
    return os.path.join(os.path.dirname(iso), stem)


# -------------------------------------------------------------------- output --

def summary(a: argparse.Namespace, cfg: Config, printing: bool, created: list[str]) -> list[str]:
    out = [""]
    name = os.path.basename(a.iso)
    size = f" ({os.path.getsize(a.iso) // 1048576} MiB)" if os.path.isfile(a.iso) else ""
    out.append(f"  iso       {name}{size}, {ISO_MEDIA[cfg.iso_bus]}")
    out.append(f"  machine   {cfg.machine}{' with SMM' if cfg.smm else ''}, {cfg.binary}, "
               f"{a.smp} cpu{'s' if a.smp != 1 else ''}{', cpu ' + a.cpu if a.cpu else ''}")
    out.append(f"  memory    {a.mem} MiB")
    if a.firmware == "bios":
        out.append("  firmware  SeaBIOS (legacy)")
    else:
        out.append(f"  firmware  OVMF (UEFI) {cfg.ovmf_code}")
    if printing:
        accel = {"auto": "kvm where the running machine has it, else tcg",
                 "kvm": "KVM (forced)", "tcg": "TCG (forced)"}[a.accel]
    elif a.accel == "auto":
        accel = ("KVM" if os.access("/dev/kvm", os.W_OK)
                 else "TCG (no writable /dev/kvm -- 10-20x slower)")
    else:
        accel = {"kvm": "KVM (forced)", "tcg": "TCG (forced)"}[a.accel]
    out.append(f"  accel     {accel}")
    if a.kernel_boot:
        out.append("  kernel    vmlinuz + initrfs.img from the ISO, loaded by qemu; no bootloader")
    for i, disk in enumerate(cfg.disks):
        how = f"{disk.fmt} on {DISK_MEDIA[cfg.disk_bus]}"
        made = f"{disk.size}" + (" ext4" if disk.create == "ext4" else "")
        if printing and disk.create:
            how += f", made {made} where missing"
        elif disk.path in created:
            how += f", created {made}"
        boot = "  (booting from it)" if a.boot_disk and i == 0 else ""
        out.append(f"  disk      {disk.path} ({how}){boot}")
    if a.nic == "none":
        out.append("  network   none")
    else:
        ssh = f", ssh -p {a.ssh_port} root@127.0.0.1" if a.ssh_port else ""
        out.append(f"  network   {a.nic}, user-mode{ssh}")
    video = "" if a.vga == "std" else f", {a.vga} video"
    if a.display == "vnc":
        exposed = "" if boot_host.vnc_is_private(a.vnc_ip) else "  PUBLIC ADDRESS"
        out.append(f"  display   vnc on {boot_host.bracket(a.vnc_ip)}:{a.vnc_port}"
                   f"{video}  ({a.vnc_from}){exposed}")
        host = "<kvm-host>" if printing else socket.gethostname()
        if os.environ.get("KITCHEN_BOOT_HOST_AGENT"):
            # Nested inside a remote launch: the driver on the other end built the tunnel
            # and has already said how to reach it. Repeating the instructions here would
            # tell the reader to build the one they are already using.
            pass
        elif boot_host.vnc_is_loopback(a.vnc_ip):
            out += ["", "  from your workstation:",
                    f"    ssh -L {a.vnc_port}:{boot_host.bracket(a.vnc_ip)}"
                    f":{a.vnc_port} {host}",
                    f"    vncviewer localhost:{a.vnc_port}"]
        else:
            # Already reachable from wherever this is: a tunnel would be a second route
            # to the same socket, and telling someone to build one is telling them to do
            # nothing. That is as true of a public address as of a LAN one -- the earlier
            # rule tested `not 127.*`, so a confirmed --vnc-public address was handed
            # tunnel instructions that would not have worked.
            out += ["", "  from your workstation:",
                    f"    vncviewer {boot_host.bracket(a.vnc_ip)}:{a.vnc_port}"]
    else:
        out.append(f"  display   {a.display}{video}")

    out.append("")
    if cfg.serial == "stdio":
        out.append("  the terminal is the qemu monitor and the guest's serial line: Ctrl-a c "
                   "switches,")
        out.append("  Ctrl-a x quits, and Ctrl-C goes to the guest, not to qemu.")
    elif cfg.monitor_stdio:
        out.append(f"  the terminal is the qemu monitor (`quit` exits); serial: {cfg.serial}")
    if a.kernel_boot:
        out.append("  the kernel and livekit print on the serial line: console=ttyS0 is the last "
                   "console.")
    elif cfg.serial == "stdio":
        out.append("  the serial line stays silent after the bootloader: no Slax menu entry "
                   "sets console=ttyS0")
        out.append("  unless the serial-console recipe added one.")
    return out


def emit(lines: list[str]) -> None:
    for ln in lines:
        print(ln, file=sys.stderr)
    sys.stderr.flush()


# -------------------------------------------------------------------- launch --

def find_mkfs() -> str | None:
    return shutil.which("mkfs.ext4") or shutil.which(
        "mkfs.ext4", path="/usr/sbin:/sbin:/usr/local/sbin")


def require_tools(a: argparse.Namespace, cfg: Config) -> None:
    missing = []
    if not shutil.which(cfg.binary):
        missing.append(f"{cfg.binary} (apt-get install qemu-system-x86)")
    creates = {disk.create for disk in cfg.disks if disk.create}
    if "qcow2" in creates and not shutil.which("qemu-img"):
        missing.append("qemu-img (apt-get install qemu-utils)")
    if "ext4" in creates and not find_mkfs():
        missing.append("mkfs.ext4, also looked for in /usr/sbin and /sbin "
                       "(apt-get install e2fsprogs)")
    if a.kernel_boot and not shutil.which("xorriso"):
        missing.append("xorriso, to take the kernel out of the ISO (apt-get install xorriso)")
    if missing:
        raise Refusal("not installed: " + "; ".join(missing))
    for disk in cfg.disks:
        parent = os.path.dirname(os.path.abspath(disk.path))
        if disk.create and not os.path.isdir(parent):
            raise Refusal(f"cannot create {disk.path}: {parent} does not exist")


def probe_display(binary: str, display: str) -> str | None:
    """Warn rather than refuse: this reads a qemu build's capabilities, and being wrong about
    them should cost a confusing qemu error, not a refusal to try.

    HOW TO PROBE, because the obvious way is wrong. `-vnc help` prints the option list and
    then exits NON-ZERO, so testing its exit status reports a false negative -- it briefly
    had the author of the sh version recording that the build host had no VNC when it has.
    Count what the help prints; never trust its exit status.
    """
    try:
        if display == "vnc":
            r = subprocess.run([binary, "-vnc", "help"], capture_output=True, text=True, timeout=30)
            return None if "=<" in r.stdout + r.stderr else (
                "this qemu lists no -vnc options; trying anyway, use --display to pick another")
        r = subprocess.run([binary, "-display", "help"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    have = [ln.strip() for ln in (r.stdout + r.stderr).splitlines()[1:] if ln.strip()]
    if display in have:
        return None
    return (f"this qemu does not list '{display}' as a display backend; it has "
            f"{' '.join(have)} plus vnc. Trying anyway; use --display to pick another")


def perform(s: Step, mkfs: str | None) -> None:
    made = False
    try:
        if s.kind == "vars":
            shutil.copyfile(s.src, s.dest)
        elif s.kind == "kernel":
            kernel, initrd = os.path.join(s.dest, "vmlinuz"), os.path.join(s.dest, "initrfs.img")
            r = subprocess.run(["xorriso", "-osirrox", "on", "-indev", s.src,
                                "-extract", "/slax/boot/vmlinuz", kernel,
                                "-extract", "/slax/boot/initrfs.img", initrd, "--"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if not all(os.path.isfile(p) and os.path.getsize(p) for p in (kernel, initrd)):
                tail = [ln for ln in r.stderr.splitlines() if ln.strip()][-3:]
                raise Refusal(f"could not take /slax/boot/vmlinuz and initrfs.img out of {s.src}"
                              + "".join(f"\n  {ln}" for ln in tail))
        elif s.fmt == "qcow2":
            # qemu-img create overwrites without asking, so look first: the plan said missing.
            if os.path.lexists(s.dest):
                raise FileExistsError(f"{s.dest} appeared after it was found missing")
            made = True
            subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", s.dest, s.size], check=True)
        else:
            # "x": never truncate a file that appeared since the plan was made.
            with open(s.dest, "xb") as f:
                made = True
                f.truncate(size_bytes(s.size))
            if s.fmt == "ext4":
                # ext4 because livekit picks its storage strategy with a live POSIX test: a
                # filesystem that passes gets a plain bind mount with no size limit, while FAT
                # gets DynFileFS and a 16 GB XFS container. mkfs on a plain file needs neither
                # root nor a loop device.
                subprocess.run([mkfs or "mkfs.ext4", "-F", "-q", "-L", "slaxperch", s.dest],
                               check=True, stdout=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as e:
        # A disk this call created and could not finish is removed: left behind, the next run
        # would find it, take it for a real disk and open an unformatted file as one. Nothing
        # that existed before is ever touched.
        if made:
            try:
                os.unlink(s.dest)
            except OSError:
                pass
        raise Refusal(f"could not prepare {s.dest}: {e}") from e


def run_qemu(argv: list[str]) -> int:
    """Run qemu in the foreground and give back its exit status, 128+N for signal N.

    Not exec, because something has to remove the scratch directory afterwards. That means
    two processes share the terminal, so:
      * qemu stays in this process group, so it keeps the terminal;
      * the parent ignores SIGINT. Under mon:stdio Ctrl-C belongs to the guest, and when
        the terminal does raise SIGINT qemu makes its own decision;
      * SIGTERM and SIGHUP are passed on, and the parent waits for qemu to act on them;
      * the terminal settings are put back, since a qemu killed with -9 leaves raw mode.
    """
    saved = None
    if sys.stdin.isatty():
        try:
            saved = termios.tcgetattr(sys.stdin.fileno())
        except termios.error:
            saved = None
    proc = subprocess.Popen(argv)

    def forward(signum, _frame):
        try:
            proc.send_signal(signum)
        except ProcessLookupError:
            pass

    previous = {signal.SIGINT: signal.signal(signal.SIGINT, signal.SIG_IGN)}
    for signum in (signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, forward)
    try:
        rc = proc.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if saved is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
            except termios.error:
                pass
    return 128 - rc if rc < 0 else rc


def launch(a: argparse.Namespace, cfg: Config, notes: list[str]) -> int:
    require_tools(a, cfg)
    warning = probe_display(cfg.binary, a.display)
    if warning:
        notes.append(warning)
    scratch = tempfile.mkdtemp(prefix="slax-qemu-")
    try:
        plan = build_plan(a, cfg, os.path.join(scratch, "ovmf-vars.fd"), scratch)
        mkfs = find_mkfs()
        created = []
        for s in plan.steps:
            perform(s, mkfs)
            if s.kind == "disk":
                created.append(s.dest)
        emit([f"  note: {n}" for n in notes] + summary(a, cfg, False, created))
        print(file=sys.stderr)
        return run_qemu(plan.argv)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def refuse_public_vnc(a: argparse.Namespace) -> None:
    """A VNC server with no password does not go on an address strangers can reach.

    THE ADDRESS IS ON THE MACHINE THAT RUNS QEMU, which is not always this one. A boot
    host's boot-host.ini carries the answer for that machine; --vnc-addr overrides it for
    one launch; otherwise it is loopback, which is reachable only through a tunnel and is
    the right default for a server nobody has to authenticate to.
    """
    ip = a.vnc_ip
    # Only when something is actually listening. --display none binds nothing, and
    # refusing an address nobody will use would be a refusal about nothing.
    if a.display == "vnc" and not boot_host.vnc_is_private(ip) and not a.vnc_public:
        raise Refusal(
            f"--vnc-addr {ip} is NOT a private address.\n"
            f"  A VNC server there is reachable from outside your network, and this one "
            f"has no password: anyone who can reach it has the keyboard and mouse of the "
            f"machine under test.\n"
            f"  Private means 127.0.0.0/8, ::1, 10/8, 172.16/12, 192.168/16 or fc00::/7.\n"
            f"  If you really mean it, pass --vnc-public as well -- and every launch will "
            f"say so.")


def remote_argv(argv: list[str], iso_local: str, iso_remote: str,
                ip: str, port: int) -> list[str]:
    """This command line, as it must read on the boot host.

    FORWARDED, not rebuilt -- unlike `kitchen test`'s hook, which has to keep half its
    flags for itself. The same parser runs at the other end, so every option means the
    same thing there; only the things that are true HERE and not THERE are rewritten: the
    image's path, and the choice of where to run.
    """
    out: list[str] = []
    i, swapped = 0, False
    while i < len(argv):
        t = argv[i]
        if t in ("--local", "--vnc-public"):
            i += 1
        elif t == "--vnc-addr":
            i += 2                                  # re-added below, resolved
        elif t.startswith("--vnc-addr="):
            i += 1
        elif t == iso_local and not swapped:
            # The first bare occurrence is the positional. An option VALUE that happened
            # to equal the image path would be rewritten too, which is why only the first
            # one is taken -- argparse has already told us there is exactly one image.
            out.append(iso_remote)
            swapped = True
            i += 1
        else:
            out.append(t)
            i += 1
    if not swapped:
        out.append(iso_remote)
    out += ["--vnc-addr", f"{boot_host.bracket(ip)}:{port}"]
    if not boot_host.vnc_is_private(ip):
        out.append("--vnc-public")                  # already confirmed here
    return out + ["--local"]


def run(a: argparse.Namespace, env, argv_in: list | None = None) -> int:
    notes: list[str] = []
    argv_in = sys.argv[1:] if argv_in is None else argv_in
    # WHERE QEMU WILL RUN, decided before anything is checked, because almost every check
    # below is a question about that machine. --print writes a script for somebody else's
    # machine and --local says to stay here; both skip the question.
    host = None
    if not a.print_only and not a.local:
        try:
            host = boot_host.load()
        except boot_host.ConfigError as e:
            raise Refusal(str(e))
    if host is not None and a.vnc_addr is None:
        a.vnc_ip, a.vnc_port, a.vnc_from = (host.vnc_ip, host.vnc_port,
                                            boot_host.CONFIG_NAME)
    refuse_public_vnc(a)
    if host is not None:
        return boot_host.launch(
            host, a,
            lambda iso_remote, ip, port: remote_argv(argv_in, a.iso, iso_remote, ip, port))

    if not a.print_only and not os.path.isfile(a.iso):
        raise Refusal(f"no such file: {a.iso}")
    cfg = resolve(a, env, a.print_only, notes)
    if a.print_only and not os.path.isfile(a.iso):
        notes.append(f"{a.iso} is not on this machine, so its boot records were not checked")
    check_iso(a, cfg, notes)
    if not a.print_only:
        return launch(a, cfg, notes)

    base = scratch_base(a.iso)
    plan = build_plan(a, cfg, base + ".ovmf-vars.fd", base + ".kernel-boot")
    needs = sorted({"qemu-system-x86"}
                   | ({OVMF_PACKAGE[a.arch]} if a.firmware == "uefi" else set())
                   | ({"xorriso"} if a.kernel_boot else set())
                   | ({"qemu-utils"} if any(s.fmt == "qcow2" for s in plan.steps) else set())
                   | ({"e2fsprogs"} if any(s.fmt == "ext4" for s in plan.steps) else set()))
    emit([f"  note: {n}" for n in notes] + summary(a, cfg, True, [])
         + ["", f"  the machine that runs this needs: {' '.join(needs)}", ""])
    sys.stdout.write(render_script(plan))
    sys.stdout.flush()
    return 0


def main(argv: list[str]) -> int:
    a = parse_args(argv[1:])
    try:
        return run(a, os.environ)
    except Refusal as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 2
    except boot_host.Unavailable as e:
        # The boot host could not be used. Caught HERE because this file calls
        # boot_host.launch() directly, past the handler boot_host's own CLI has -- so
        # without this a busy VNC port, an unreachable machine or a failed pre-run check
        # arrived as a traceback, which is the shape of failure this toolkit spent four
        # commits removing.
        print(f"\n{PROG}: boot host unavailable: {e}", file=sys.stderr)
        print(f"  nothing was run. To boot here instead: {PROG} ... --local",
              file=sys.stderr)
        return boot_host.EXIT_UNAVAILABLE
    except KeyboardInterrupt:
        print(f"\n{PROG}: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
