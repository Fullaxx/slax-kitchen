#!/usr/bin/env python3
"""Boot an ISO in QEMU and capture evidence that it got somewhere.

Two kinds of evidence, because they catch different failures:

  serial   the livekit init prints progress to the console; with a serial boot entry
           those lines are machine-readable and tell you how far init got.
  screen   a QMP screendump PNG. This is the only way to see a BOOTLOADER, since GRUB
           and isolinux draw to the video console, not to serial. It is also readable
           by an AI agent directly.

Exit code 0 if every requested assertion held.

FOUR MODES, and they prove different things:

  kernel   -kernel/-initrd, bypassing the bootloader entirely. The only mode that can
           put the kernel on ttyS0 without help, and therefore the per-push signal.
  bios     through isolinux. Proves the BIOS boot path, which `kernel` deliberately skips.
  uefi     through GRUB under OVMF. Proves the UEFI path.
  usb      the ISO attached as a usb-storage device rather than a CD. Proves the image is
           what a dd'd stick would be -- which needs the isohybrid recipe.

`bios`, `uefi` and `usb` reach the bootloader's DEFAULT entry, which carries no
console=ttyS0 and so leaves the serial log empty. Give them --keys to select the entry
that `serial-console` adds and they become assertable like `kernel` is. Without --keys
they are screenshot evidence and nothing more, which is how a boot test that asserted
nothing passed for four CI runs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

# Searched in order, and overridable, because these were two hardcoded absolute paths
# that died with a traceback on any distribution that lays firmware out differently.
# tools/qemu/boot.py searches the same list in the same order, so the harness and the
# interactive launcher find the same firmware -- tests/unit/test_tools_qemu.py fails if
# the two drift apart.
OVMF_DIRS = ("/usr/share/OVMF", "/usr/share/ovmf", "/usr/share/edk2/ovmf", "/usr/share/qemu")
OVMF_CODE_NAMES = ("OVMF_CODE_4M.fd", "OVMF_CODE.fd", "OVMF.fd")
OVMF_VARS_NAMES = ("OVMF_VARS_4M.fd", "OVMF_VARS.fd")

# Lines excluded from a --golden comparison because they are SUPPOSED to differ between
# runs. The perch marker says "absent" on the first boot of a persistence pair and
# "present" on the second -- that difference IS the test, and it is asserted with
# --expect. Golden-diffing it would make the two boots contradict each other.
GOLDEN_VOLATILE = ("perch-marker:",)

TESTKIT_BEGIN = "### TESTKIT BEGIN"
TESTKIT_END = "### TESTKIT END"


def qemu_version(binary: str) -> str:
    """What `binary --version` says it is -- "8.2.2" -- or "unknown".

    Asked here, by the process that runs qemu, so a ledger row names the qemu that booted
    it. ci/tier-c.sh used to ask instead, of whatever qemu-system-x86_64 the machine
    running tier-c.sh had: right only while the two are one machine, and "unknown" when
    that machine has none -- which the ledger gate let through.
    """
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    m = re.search(r"\bversion (\d+(?:\.\d+)+)", out)
    return m.group(1) if m else "unknown"


def find_ovmf() -> tuple[str, str]:
    code = os.environ.get("OVMF_CODE", "")
    varsf = os.environ.get("OVMF_VARS", "")
    for d in OVMF_DIRS:
        if not code:
            for n in OVMF_CODE_NAMES:
                if os.path.isfile(os.path.join(d, n)):
                    code = os.path.join(d, n)
                    break
        if not varsf:
            for n in OVMF_VARS_NAMES:
                if os.path.isfile(os.path.join(d, n)):
                    varsf = os.path.join(d, n)
                    break
    if not code:
        raise RuntimeError("no OVMF firmware found (apt-get install ovmf), or set $OVMF_CODE")
    if not os.path.isfile(code):
        raise RuntimeError(f"$OVMF_CODE is not a file: {code}")
    if not varsf:
        raise RuntimeError(f"found {code} but no matching OVMF_VARS (set $OVMF_VARS)")
    if not os.path.isfile(varsf):
        raise RuntimeError(f"$OVMF_VARS is not a file: {varsf}")
    return code, varsf


class Qmp:
    def __init__(self, path: str, timeout: float = 60):
        end = time.time() + timeout
        while True:
            try:
                self.s = socket.socket(socket.AF_UNIX)
                self.s.connect(path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.time() > end:
                    raise
                time.sleep(0.2)
        self.f = self.s.makefile("rw")
        self.f.readline()                     # greeting
        self.cmd("qmp_capabilities")

    def cmd(self, name: str, **args) -> dict:
        self.f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
        self.f.flush()
        while True:
            line = self.f.readline()
            if not line:
                raise RuntimeError("qmp closed")
            msg = json.loads(line)
            if "return" in msg or "error" in msg:
                return msg

    def close(self) -> None:
        try:
            self.cmd("quit")
        except Exception:                      # noqa: BLE001
            pass
        self.s.close()


def send_keys(q: "Qmp", spec: str) -> None:
    """Drive a boot menu. spec is comma-separated qcodes, optionally with a delay:
    'down,down,ret' or '2s,down,ret' -- an Ns token waits before the next key.

    Needed because a bootloader menu is the one thing serial cannot reach: isolinux and
    GRUB draw to the video console, so selecting a non-default entry means synthesising
    real keystrokes.
    """
    for tok in [t.strip() for t in spec.split(",") if t.strip()]:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)s", tok)
        if m:
            time.sleep(float(m.group(1)))
            continue
        q.cmd("send-key", keys=[{"type": "qcode", "data": tok}])
        time.sleep(0.25)


# Markers that prove the guest reached OUR code rather than dying in the kernel.
LIVEKIT_MARKERS = ("Looking for", "Mounting bundles", "Live Kit done", "Setting up")


def _wait(serial: str, seconds: int, expect: list) -> float:
    """Wait for the guest, and return how long that actually took.

    WHY THIS IS NOT time.sleep(seconds). It used to be, and `--seconds` was therefore not
    a timeout but a bill: CI paid 240 + 120 + 120 = 480 s of sleeping per run whatever the
    guest did, and the number the harness reported back was the flag it had been given
    rather than anything it measured.

    With expectations, poll the serial log and return as soon as EVERY one is present.
    `--seconds` becomes the ceiling it always read like. Without expectations -- a menu
    mode given no --keys, whose serial log stays empty because the default entry sets no
    console=ttyS0 -- there is nothing to poll for, so the flat sleep stands.

    Polling only on ALL expectations matters: returning on the first would stop before the
    later stages ran, and this test's whole value is that `Live Kit done` comes last.
    """
    start = time.time()
    if not expect:
        time.sleep(seconds)
        return time.time() - start
    while time.time() - start < seconds:
        time.sleep(0.5)
        try:
            with open(serial, errors="replace") as f:
                txt = f.read()
        except OSError:
            continue
        if all(w in txt for w in expect):
            break
    return time.time() - start


def make_perch_disk(path: str, size: str) -> None:
    """Create a writable device for a persistence run: sparse file + ext4, no root.

    ext4 rather than FAT because livekitlib picks its storage strategy with a live POSIX
    test, not by filesystem name: a filesystem that passes gets a plain `mount --bind`
    with NO size limit, while FAT32 and NTFS fall back to DynFileFS + a 16000 MB XFS
    container. The bind path is the one worth testing and the one that needs no room, so
    the disk is small on purpose.

    `mkfs.ext4` on a plain file needs neither root nor a loop device, which is what keeps
    this runnable by an ordinary user on any host.
    """
    if os.path.exists(path):
        return
    # /usr/sbin is not always on PATH -- a non-login ssh session is the usual way to
    # discover that, and it hides mkfs.ext4 rather than reporting it missing. Look there
    # explicitly before believing it is absent.
    mkfs = shutil.which("mkfs.ext4") or shutil.which(
        "mkfs.ext4", path="/usr/sbin:/sbin:/usr/local/sbin")
    if mkfs is None:
        raise RuntimeError("mkfs.ext4 not found, including in /usr/sbin and /sbin "
                           "(apt-get install e2fsprogs)")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    subprocess.run(["truncate", "-s", size, path], check=True)
    subprocess.run([mkfs, "-F", "-q", "-L", "slaxperch", path],
                   check=True, stdout=subprocess.DEVNULL)


def testkit_block(text: str) -> list[str]:
    """The lines between the testkit fences, minus the ones meant to vary."""
    out, inside = [], False
    for ln in text.splitlines():
        # rstrip everything, not just \r. A golden is a normalised record, not a
        # transcript: `bundles:` ends in a space because testkit builds it with
        # `tr '\n' ' '`, and a committed file with trailing whitespace is rejected by
        # ci/checks/70-whitespace.sh -- correctly. Both the write and the comparison go
        # through here, so they cannot disagree.
        ln = ln.rstrip()
        if TESTKIT_BEGIN in ln:
            inside = True
            continue
        if TESTKIT_END in ln:
            break
        if inside and not ln.startswith(GOLDEN_VOLATILE):
            out.append(ln)
    return out


def boot(iso: str, mode: str, seconds: int, outdir: str, mem: int = 2048,
         keys: str | None = None, kernel: str | None = None,
         initrd: str | None = None, expect: list | None = None,
         disk: str | None = None, disk_size: str = "256M",
         append: str | None = None, run_tag: str | None = None,
         pidfile: str | None = None) -> dict:
    # BEFORE anything is created, so a refusal leaks nothing. A stock Slax ISO has bytes
    # 0..512 all zero -- no signature, no partition table -- so attaching it as a stick
    # gives a guest that finds nothing bootable and an empty serial log, for a reason
    # having nothing to do with what was being tested.
    if mode == "usb":
        with open(iso, "rb") as f:
            if f.read(512)[510:512] != b"\x55\xaa":
                raise RuntimeError(
                    f"{os.path.basename(iso)} has no MBR signature, so it is not a "
                    f"bootable USB image.\n"
                    f"  A stock Slax ISO has none -- that is upstream issue 2. Apply the "
                    f"isohybrid recipe:\n"
                    f"      kitchen apply isohybrid && kitchen pack --hybrid")
    os.makedirs(outdir, exist_ok=True)
    # run_tag keeps a two-boot persistence pair from erasing its own first half: the tag
    # is otherwise just (iso, mode), and both artifacts are unlinked on entry below.
    tag = f"{os.path.basename(iso).rsplit('.', 1)[0]}-{mode}"
    if run_tag:
        tag += f"-{run_tag}"
    serial = os.path.join(outdir, tag + ".serial.log")
    shot = os.path.join(outdir, tag + ".png")
    # AF_UNIX paths are capped at ~108 bytes, so the QMP socket cannot live beside the
    # evidence -- a deep -o directory silently broke the harness with "AF_UNIX path too
    # long". Put it in a short temp dir and let the outputs go wherever they like.
    qmp_dir = tempfile.mkdtemp(prefix="qb-")
    qmp = os.path.join(qmp_dir, "q")
    vars_copy = None
    for p in (serial, shot):
        if os.path.exists(p):
            os.unlink(p)

    # -no-reboot so a guest that triple-faults or reaches `reboot` stops instead of
    # looping; the harness would otherwise keep sampling a machine that already died.
    cmd = ["qemu-system-x86_64", "-m", str(mem),
           "-display", "none", "-serial", f"file:{serial}",
           "-qmp", f"unix:{qmp},server,nowait", "-no-reboot"]
    if pidfile:
        cmd += ["-pidfile", pidfile]
    if os.access("/dev/kvm", os.W_OK):
        cmd.insert(1, "-enable-kvm")

    if mode == "usb":
        # An isohybrid ISO IS the USB image, byte for byte. Copying it to a usb.img
        # first -- which is what the handoff doc used to say -- is 440 MiB of litter per
        # run for no difference in what gets booted.
        #
        # TWO THINGS THE DOCUMENTED COMMAND LINE GETS WRONG, both measured 2026-09-16:
        #
        #   * `-device usb-storage` alone dies with "No 'usb-bus' bus found for device
        #     'usb-storage'". A machine type with no USB controller has nothing to plug
        #     a stick into, so one has to be added. xhci rather than the older `-usb`
        #     UHCI because USB 2.0+ is what any stick made this century negotiates.
        #   * `-drive` opens READ-WRITE by default, so a boot test would have the guest
        #     holding a writable handle on the artifact under test. readonly=on: a dd'd
        #     stick carries ISO9660 and is read-only to the guest anyway, and a test
        #     that can alter its own input is not a test.
        cmd += ["-device", "qemu-xhci,id=xhci",
                "-drive", f"if=none,format=raw,readonly=on,id=usbstick,file={iso}",
                "-device", "usb-storage,bus=xhci.0,drive=usbstick"]
    else:
        cmd += ["-cdrom", iso, "-boot", "d"]

    if disk:
        make_perch_disk(disk, disk_size)
        # No if=, so it lands on the default bus as an IDE disk -- /dev/sda in the guest,
        # with the CD at /dev/sr0. The caller names the device in its perchdir= rather
        # than the harness guessing, because guessing is what a test should never do.
        cmd += ["-drive", f"file={disk},format=raw"]

    if mode == "kernel":
        # Direct kernel boot: skip the bootloader entirely and put the kernel on the
        # serial port by construction. The menu modes cannot do this by themselves --
        # their default entry has no console=ttyS0, so everything after the loader goes
        # to video and the serial log stays empty unless --keys picks the serial entry.
        # This is the per-push signal: it exercises the whole of livekit init with no
        # menu timing to get wrong under TCG.
        if kernel is None or initrd is None:
            raise RuntimeError("kernel mode needs --kernel and --initrd")
        cmdline = ("vga=normal rw printk.time=0 consoleblank=0 automount "
                   "console=ttyS0,115200n8 from=/dev/sr0/slax")
        if append:
            cmdline += " " + append
        cmd += ["-kernel", kernel, "-initrd", initrd, "-append", cmdline]
    if mode == "uefi":
        ovmf_code, ovmf_vars = find_ovmf()
        vars_copy = os.path.join(outdir, tag + ".vars.fd")
        shutil.copyfile(ovmf_vars, vars_copy)
        cmd[1:1] = ["-drive", f"if=pflash,format=raw,readonly=on,file={ovmf_code}",
                    "-drive", f"if=pflash,format=raw,file={vars_copy}"]

    # Asked before the guest starts, so the question never overlaps the boot being timed.
    version = qemu_version(cmd[0])
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    result = {"iso": iso, "mode": mode, "serial": serial, "screenshot": shot,
              "kvm": "-enable-kvm" in cmd, "qemu": version, "died": False}
    try:
        try:
            q = Qmp(qmp)
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            # QEMU never came up. Its own message says why and ours does not, and the
            # difference is a stack trace versus "No 'usb-bus' bus found" -- which is
            # the actual bug this caught. stderr was already being captured and simply
            # never read.
            proc.poll()
            err = b""
            if proc.stderr is not None:
                try:
                    err = proc.stderr.read() or b""
                except Exception:                  # noqa: BLE001
                    pass
            msg = err.decode(errors="replace").strip().splitlines()
            msg = [m for m in msg if m and "warning: host doesn't support" not in m]
            raise RuntimeError("qemu exited before the monitor came up:\n  "
                               + ("\n  ".join(msg) if msg else "(no stderr output)"))
        if keys:
            send_keys(q, keys)
        result["waited"] = _wait(serial, seconds, expect or [])
        # QEMU writes PPM unless told otherwise -- the filename extension is NOT
        # enough, and a .png that is really a PPM silently breaks every image reader
        # downstream. The format argument exists since QEMU 7.1; older builds get a
        # PPM written under a .ppm name so the caller can see what happened.
        if "error" in q.cmd("screendump", filename=shot, format="png"):
            shot = shot[:-4] + ".ppm"
            q.cmd("screendump", filename=shot)
        for _ in range(40):                    # screendump is asynchronous
            if os.path.exists(shot) and os.path.getsize(shot) > 0:
                break
            time.sleep(0.25)
        result["screenshot"] = shot
        q.close()
    except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
        # QEMU went away mid-run -- killed from outside, out of memory, or a QMP
        # protocol error. Reporting that as a traceback buries the one fact the caller
        # needs. Whatever serial output exists is still on disk and still gets asserted,
        # so a guest that died after printing everything we asked for is not a failure.
        result["died"] = True
        result["died_why"] = f"{type(e).__name__}: {e}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        # Both of these leaked. 27 stale /tmp/qb-* directories had accumulated in the
        # dev container and 7 on the build host before anyone counted, and every UEFI
        # run left a 540 KB firmware-variables copy sitting in the evidence directory.
        # Neither is evidence: the socket is dead and the vars file is scratch.
        shutil.rmtree(qmp_dir, ignore_errors=True)
        if vars_copy and os.path.exists(vars_copy):
            os.unlink(vars_copy)
        if pidfile and os.path.exists(pidfile):
            os.unlink(pidfile)
    result["serial_text"] = open(serial, errors="replace").read() if os.path.exists(serial) else ""
    result["screenshot_bytes"] = os.path.getsize(shot) if os.path.exists(shot) else 0
    return result


def record(path: str, r: dict, a, rc: int, golden: str) -> None:
    """Append one JSON object per run, for ci/tier-c.sh to assemble into the ledger.

    Deliberately a SUBSET of the result dict. `iso`, `serial` and `screenshot` are
    absolute paths on whatever machine ran this, and the ledger is committed -- so the
    ISO is recorded by basename and size, which are facts about the artifact, and the
    paths are dropped. Append-only, so two runs cannot race a read-modify-write.
    """
    text = r["serial_text"]
    row = {
        "path": a.label or r["mode"],
        "iso_name": os.path.basename(r["iso"]),
        "iso_bytes": os.path.getsize(r["iso"]) if os.path.exists(r["iso"]) else 0,
        "accel": "kvm" if r["kvm"] else "tcg",
        # Beside accel, and for the same reason: both are facts about the machine that ran
        # this boot, and this is the only process that knows them first-hand.
        "qemu": r.get("qemu", "unknown"),
        "waited_s": round(r.get("waited", 0), 1),
        "seconds_ceiling": a.seconds,
        "markers": [w for w in a.expect if w in text],
        "missing": [w for w in a.expect if w not in text],
        "screenshot_bytes": r["screenshot_bytes"],
        "golden": golden,
        "result": "pass" if rc == 0 else "fail",
    }
    if a.run_tag:
        row["run_tag"] = a.run_tag
    with open(path, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="boot an ISO in QEMU and capture evidence")
    ap.add_argument("iso")
    ap.add_argument("--mode", choices=["bios", "uefi", "kernel", "usb"], default="bios")
    ap.add_argument("--kernel", help="vmlinuz for --mode kernel")
    ap.add_argument("--initrd", help="initrfs.img for --mode kernel")
    ap.add_argument("--seconds", type=int, default=25,
                    help="ceiling; with --expect the run ends as soon as every "
                         "expectation appears, so this is a timeout, not a duration")
    ap.add_argument("--out", default="out/boot-tests")
    ap.add_argument("--expect", action="append", default=[],
                    help="string that must appear in the serial log")
    ap.add_argument("--keys", help="menu keystrokes, e.g. '6s,down,down,ret'")
    ap.add_argument("--mem", type=int, default=2048,
                    help="guest memory in MiB; a toram run needs at least the image size")
    ap.add_argument("--append", help="extra kernel cmdline for --mode kernel, "
                                     "e.g. 'perchdir=/dev/sda/slax/changes'")
    ap.add_argument("--disk", help="attach a writable raw disk, creating it with an "
                                   "ext4 filesystem if absent. NEVER deleted here: a "
                                   "two-boot persistence test needs it to survive, so "
                                   "the caller owns its lifetime")
    ap.add_argument("--disk-size", default="256M")
    ap.add_argument("--run-tag", help="suffix for the evidence filenames, so a second "
                                      "boot does not erase the first one's log")
    ap.add_argument("--label", help="what to call this boot in the ledger. The two "
                                    "persistence boots run in kernel mode but are "
                                    "testing persistence, and the record should say so")
    ap.add_argument("--golden", help="file of expected testkit lines to diff against")
    ap.add_argument("--record", help="append one JSON line describing this run")
    ap.add_argument("--pidfile", help="where qemu writes its pid, so a driver's trap "
                                      "can kill this run and nothing else")
    ap.add_argument("--retries", type=int, default=1,
                    help="retry this many times if the guest panics before reaching "
                         "livekit at all (TCG flakiness); a boot that reached livekit "
                         "and then failed is never retried")
    a = ap.parse_args(argv[1:])
    if not os.path.isfile(a.iso):
        print(f"no such file: {a.iso}", file=sys.stderr)
        return 2

    # TCG is not reliably deterministic. A run of this harness produced a kernel panic
    # in mask_ioapic_irq during x86_late_time_init -- a crash in early kernel init,
    # before the initramfs is even unpacked -- and the identical ISO booted fine on the
    # next attempt. That is infrastructure noise, not a product failure, and letting it
    # fail CI would teach people to ignore a red boot test.
    #
    # Retry ONLY when the guest never reached our code: no livekit marker at all AND a
    # panic in the log. A boot that got into livekit and then failed is a real failure
    # and is never retried, so this cannot mask a product bug.
    kw = dict(keys=a.keys, kernel=a.kernel, initrd=a.initrd, expect=a.expect,
              mem=a.mem, append=a.append, disk=a.disk, disk_size=a.disk_size,
              run_tag=a.run_tag, pidfile=a.pidfile)
    try:
        r = boot(a.iso, a.mode, a.seconds, a.out, **kw)
    except RuntimeError as e:
        # A refusal is an answer, not a crash. These messages name the recipe or package
        # that fixes them, and a traceback buries that under a stack.
        print(f"{a.mode}: {e}", file=sys.stderr)
        return 2
    for attempt in range(a.retries):
        txt = r["serial_text"]
        reached_us = any(m in txt for m in LIVEKIT_MARKERS)
        panicked = "Kernel panic" in txt or "end trace" in txt
        if reached_us or not panicked:
            break
        print(f"  retry {attempt + 1}/{a.retries}: the guest panicked in early kernel "
              f"init without reaching livekit -- almost certainly TCG flakiness, "
              f"retrying", file=sys.stderr)
        r = boot(a.iso, a.mode, a.seconds, a.out, **kw)
    print(f"boot {a.mode}: {os.path.basename(a.iso)}"
          f"   ({'KVM' if r['kvm'] else 'TCG -- slow'})")
    waited = r.get("waited", 0)
    if a.expect:
        print(f"  waited     : {waited:.0f}s of a {a.seconds}s ceiling"
              f"{' (all expectations seen)' if waited < a.seconds - 1 else ' -- HIT THE CEILING'}")
    if r.get("died"):
        print(f"  note       : qemu exited before the harness did ({r['died_why']}); "
              f"asserting on whatever serial output it left")
    print(f"  serial log : {r['serial']} ({len(r['serial_text'])} bytes)")
    print(f"  screenshot : {r['screenshot']} ({r['screenshot_bytes']} bytes)")

    rc = 0
    # A screenshot is the only evidence a menu mode produces when it was given no --keys.
    # Kernel mode is exempt because it has no display state worth capturing.
    if r["screenshot_bytes"] == 0 and a.mode != "kernel":
        print("  FAIL: no screenshot captured (qemu never reached a display state)")
        rc = 1
    if a.mode == "kernel" and not r["serial_text"].strip():
        print("  FAIL: serial log is empty -- the kernel produced no output at all")
        rc = 1
    # A menu mode WITH expectations has been pointed at a serial entry. Reaching NO
    # livekit marker means the keystrokes missed it and something else booted -- which is
    # how this test catches its own misconfiguration rather than reporting a boot it never
    # selected as a product failure.
    #
    # Emptiness is the wrong test, and it was the first one here: OVMF writes its own
    # banner to the serial port, so a UEFI run that missed the menu entirely still leaves
    # ~4 KB of firmware chatter and this never fired. Found by booting a 32-bit image
    # whose GRUB timeout was shorter than the keystroke lead: three FAILs about missing
    # markers, and a screenshot of a perfectly good Fluxbox desktop.
    if (a.mode != "kernel" and a.expect
            and not any(m in r["serial_text"] for m in LIVEKIT_MARKERS)):
        print(f"  FAIL: no livekit marker reached the serial log, so the --keys sequence "
              f"({a.keys!r}) did not select an entry with console=ttyS0.")
        print(f"        The guest may well have booted fine on a DIFFERENT entry -- check "
              f"the screenshot before blaming the image.")
        rc = 1
    for want in a.expect:
        if want in r["serial_text"]:
            print(f"  ok   serial contains {want!r}")
        else:
            print(f"  FAIL serial missing {want!r}")
            rc = 1

    golden_state = "none"
    if a.golden:
        got = testkit_block(r["serial_text"])
        if not got:
            print(f"  FAIL golden: no {TESTKIT_BEGIN} block in the serial log "
                  f"(is the testkit recipe applied?)")
            golden_state, rc = "absent", 1
        elif not os.path.isfile(a.golden):
            # Not a failure: this is how a golden is born. Writing it silently would
            # make the first run of a broken boot the thing every later run agrees with.
            print(f"  note golden: {a.golden} does not exist; writing it from this run")
            with open(a.golden, "w") as f:
                f.write("\n".join(got) + "\n")
            golden_state = "created"
        else:
            # rstrip(), not rstrip("\n"), so BOTH sides are stripped the same way.
            # testkit_block's comment says "Both the write and the comparison go through
            # here, so they cannot disagree" -- and the comparison did not: it kept any
            # trailing whitespace the file carried, so such a golden could never match,
            # whatever the image did, and the diff below could not say why. A committed
            # golden is protected by ci/checks/70-whitespace.sh; a --golden-dir pointing
            # outside the tree, which every scratch run uses, is not.
            want = [ln.rstrip() for ln in open(a.golden)]
            if got == want:
                print(f"  ok   golden: {len(got)} testkit lines match {a.golden}")
                golden_state = "match"
            else:
                print(f"  FAIL golden: testkit output differs from {a.golden}")
                missing = [ln for ln in want if ln not in got]
                extra = [ln for ln in got if ln not in want]
                for ln in missing:
                    print(f"    - {ln}")
                for ln in extra:
                    print(f"    + {ln}")
                # BOTH OF THOSE ARE MEMBERSHIP TESTS, so when the two sides hold the same
                # lines in a different order they print nothing at all -- while `got ==
                # want` above still fails the run. "differs from" followed by silence is
                # the one diff a reader cannot act on.
                if not missing and not extra:
                    at = next((i for i, (g, w) in enumerate(zip(got, want)) if g != w),
                              None)
                    if at is None:
                        print(f"    the same lines, but {len(got)} of them here and "
                              f"{len(want)} in the golden")
                    else:
                        print(f"    the same {len(got)} lines in a different order: "
                              f"line {at + 1} is {got[at]!r}, expected {want[at]!r}")
                golden_state, rc = "differ", 1

    if r["serial_text"].strip():
        tail = [ln for ln in r["serial_text"].splitlines() if ln.strip()][-6:]
        print("  last serial lines:")
        for ln in tail:
            print(f"    | {ln[:110]}")
    if a.record:
        record(a.record, r, a, rc, golden_state)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
