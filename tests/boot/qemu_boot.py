#!/usr/bin/env python3
"""Boot an ISO in QEMU and capture evidence that it got somewhere.

Two kinds of evidence, because they catch different failures:

  serial   the livekit init prints progress to the console; with a serial boot entry
           those lines are machine-readable and tell you how far init got.
  screen   a QMP screendump PNG. This is the only way to see a BOOTLOADER, since GRUB
           and isolinux draw to the video console, not to serial. It is also readable
           by an AI agent directly.

Exit code 0 if every requested assertion held.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time

OVMF_CODE = "/usr/share/OVMF/OVMF_CODE_4M.fd"
OVMF_VARS = "/usr/share/OVMF/OVMF_VARS_4M.fd"


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
    `--seconds` becomes the ceiling it always read like. Without expectations -- the
    screenshot modes, whose serial log stays empty because no Slax menu entry sets
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


def boot(iso: str, mode: str, seconds: int, outdir: str, mem: int = 2048,
         keys: str | None = None, kernel: str | None = None,
         initrd: str | None = None, expect: list | None = None) -> dict:
    os.makedirs(outdir, exist_ok=True)
    tag = f"{os.path.basename(iso).rsplit('.', 1)[0]}-{mode}"
    serial = os.path.join(outdir, tag + ".serial.log")
    shot = os.path.join(outdir, tag + ".png")
    # AF_UNIX paths are capped at ~108 bytes, so the QMP socket cannot live beside the
    # evidence -- a deep -o directory silently broke the harness with "AF_UNIX path too
    # long". Put it in a short temp dir and let the outputs go wherever they like.
    qmp_dir = tempfile.mkdtemp(prefix="qb-")
    qmp = os.path.join(qmp_dir, "q")
    for p in (serial, shot):
        if os.path.exists(p):
            os.unlink(p)

    cmd = ["qemu-system-x86_64", "-m", str(mem), "-cdrom", iso,
           "-display", "none", "-serial", f"file:{serial}",
           "-qmp", f"unix:{qmp},server,nowait", "-boot", "d"]
    if os.access("/dev/kvm", os.W_OK):
        cmd.insert(1, "-enable-kvm")
    if mode == "kernel":
        # Direct kernel boot: skip the bootloader entirely and put the kernel on the
        # serial port by construction. The menu modes cannot do this -- their default
        # entry has no console=ttyS0, so everything after the loader goes to video and
        # the serial log stays empty. This is the per-push signal: it exercises the
        # whole of livekit init with no menu timing to get wrong under TCG.
        if kernel is None or initrd is None:
            raise RuntimeError("kernel mode needs --kernel and --initrd")
        cmd += ["-kernel", kernel, "-initrd", initrd, "-append",
                "vga=normal rw printk.time=0 consoleblank=0 automount "
                "console=ttyS0,115200n8 from=/dev/sr0/slax"]
    if mode == "uefi":
        if not os.path.isfile(OVMF_CODE):
            raise RuntimeError(f"{OVMF_CODE} missing (apt-get install ovmf)")
        vars_copy = os.path.join(outdir, tag + ".vars.fd")
        subprocess.run(["cp", OVMF_VARS, vars_copy], check=True)
        cmd[1:1] = ["-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
                    "-drive", f"if=pflash,format=raw,file={vars_copy}"]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    result = {"iso": iso, "mode": mode, "serial": serial, "screenshot": shot,
              "kvm": "-enable-kvm" in cmd}
    try:
        q = Qmp(qmp)
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
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    result["serial_text"] = open(serial, errors="replace").read() if os.path.exists(serial) else ""
    result["screenshot_bytes"] = os.path.getsize(shot) if os.path.exists(shot) else 0
    return result


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="boot an ISO in QEMU and capture evidence")
    ap.add_argument("iso")
    ap.add_argument("--mode", choices=["bios", "uefi", "kernel"], default="bios")
    ap.add_argument("--kernel", help="vmlinuz for --mode kernel")
    ap.add_argument("--initrd", help="initrfs.img for --mode kernel")
    ap.add_argument("--seconds", type=int, default=25,
                    help="ceiling; with --expect the run ends as soon as every "
                         "expectation appears, so this is a timeout, not a duration")
    ap.add_argument("--out", default="out/boot-tests")
    ap.add_argument("--expect", action="append", default=[],
                    help="string that must appear in the serial log")
    ap.add_argument("--keys", help="menu keystrokes, e.g. '6s,down,down,ret'")
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
    r = boot(a.iso, a.mode, a.seconds, a.out, keys=a.keys,
             kernel=a.kernel, initrd=a.initrd, expect=a.expect)
    for attempt in range(a.retries):
        txt = r["serial_text"]
        reached_us = any(m in txt for m in LIVEKIT_MARKERS)
        panicked = "Kernel panic" in txt or "end trace" in txt
        if reached_us or not panicked:
            break
        print(f"  retry {attempt + 1}/{a.retries}: the guest panicked in early kernel "
              f"init without reaching livekit -- almost certainly TCG flakiness, "
              f"retrying", file=sys.stderr)
        r = boot(a.iso, a.mode, a.seconds, a.out, keys=a.keys,
                 kernel=a.kernel, initrd=a.initrd, expect=a.expect)
    print(f"boot {a.mode}: {os.path.basename(a.iso)}"
          f"   ({'KVM' if r['kvm'] else 'TCG -- slow'})")
    waited = r.get("waited", 0)
    if a.expect:
        print(f"  waited     : {waited:.0f}s of a {a.seconds}s ceiling"
              f"{' (all expectations seen)' if waited < a.seconds - 1 else ' -- HIT THE CEILING'}")
    print(f"  serial log : {r['serial']} ({len(r['serial_text'])} bytes)")
    print(f"  screenshot : {r['screenshot']} ({r['screenshot_bytes']} bytes)")

    rc = 0
    if r["screenshot_bytes"] == 0 and a.mode != "kernel":
        print("  FAIL: no screenshot captured (qemu never reached a display state)")
        rc = 1
    if a.mode == "kernel" and not r["serial_text"].strip():
        print("  FAIL: serial log is empty -- the kernel produced no output at all")
        rc = 1
    for want in a.expect:
        if want in r["serial_text"]:
            print(f"  ok   serial contains {want!r}")
        else:
            print(f"  FAIL serial missing {want!r}")
            rc = 1
    if r["serial_text"].strip():
        tail = [ln for ln in r["serial_text"].splitlines() if ln.strip()][-6:]
        print("  last serial lines:")
        for ln in tail:
            print(f"    | {ln[:110]}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
