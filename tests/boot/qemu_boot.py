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
import socket
import subprocess
import sys
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


def boot(iso: str, mode: str, seconds: int, outdir: str, mem: int = 2048) -> dict:
    os.makedirs(outdir, exist_ok=True)
    tag = f"{os.path.basename(iso).rsplit('.', 1)[0]}-{mode}"
    serial = os.path.join(outdir, tag + ".serial.log")
    shot = os.path.join(outdir, tag + ".png")
    qmp = os.path.join(outdir, tag + ".qmp")
    for p in (serial, shot, qmp):
        if os.path.exists(p):
            os.unlink(p)

    cmd = ["qemu-system-x86_64", "-m", str(mem), "-cdrom", iso,
           "-display", "none", "-serial", f"file:{serial}",
           "-qmp", f"unix:{qmp},server,nowait", "-boot", "d"]
    if os.access("/dev/kvm", os.W_OK):
        cmd.insert(1, "-enable-kvm")
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
        time.sleep(seconds)
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
    ap.add_argument("--mode", choices=["bios", "uefi"], default="bios")
    ap.add_argument("--seconds", type=int, default=25, help="how long to let it run")
    ap.add_argument("--out", default="out/boot-tests")
    ap.add_argument("--expect", action="append", default=[],
                    help="string that must appear in the serial log")
    a = ap.parse_args(argv[1:])
    if not os.path.isfile(a.iso):
        print(f"no such file: {a.iso}", file=sys.stderr)
        return 2

    r = boot(a.iso, a.mode, a.seconds, a.out)
    print(f"boot {a.mode}: {os.path.basename(a.iso)}"
          f"   ({'KVM' if r['kvm'] else 'TCG -- slow'})")
    print(f"  serial log : {r['serial']} ({len(r['serial_text'])} bytes)")
    print(f"  screenshot : {r['screenshot']} ({r['screenshot_bytes']} bytes)")

    rc = 0
    if r["screenshot_bytes"] == 0:
        print("  FAIL: no screenshot captured (qemu never reached a display state)")
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
