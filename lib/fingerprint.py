#!/usr/bin/env python3
"""Build a compatibility fingerprint for a Slax ISO.

A fingerprint is the structural identity of a release: what the ISO container looks
like, which kernel and initramfs it carries, and what the bundles are.  `kitchen probe`
matches an arbitrary ISO against the fingerprints in compat/ so we can say
"this is Slax 12.2.0 64-bit" -- or "this is close but N things differ" -- rather than
guessing from a filename.

Everything is read without mounting anything:
  * ISO structure            lib/isoparse.py (pure python)
  * files out of /slax/boot  xorriso -osirrox  (Rock Ridge safe, no privileges)
  * files out of a bundle    unsquashfs -o <offset> reading the .sb IN PLACE inside the
                             ISO -- no need to extract a 122 MB bundle to read one file
  * the initramfs            xz -d | cpio -i into a temp dir
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isoparse import IsoReader  # noqa: E402

BOOT_DIR = "/slax/boot"
MODULES_DIR = "/slax/modules"
# The four files that define livekit behaviour. If any of these move, recipes that patch
# the initramfs need re-checking -- which is exactly what a fingerprint mismatch is for.
INITRAMFS_SCRIPTS = ["init", "lib/livekitlib", "lib/config", "shutdown"]
INITRAMFS_HELPERS = ["blkid", "eject", "ncurses-menu", "xfs_growfs",
                     "mkfs.xfs.custom", "@mount.dynfilefs", "@mount.httpfs2"]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def iso_extract(iso: str, what: str, dest: str) -> None:
    r = run(["xorriso", "-osirrox", "on", "-indev", iso, "-extract", what, dest])
    if "FAILURE" in r.stderr:
        raise RuntimeError(f"xorriso extract {what}: {r.stderr.strip().splitlines()[-1]}")


def iso_listdir(iso: str, path: str) -> list[tuple[str, int]]:
    r = run(["xorriso", "-indev", iso, "-lsl", path + "/", "--"])
    out = []
    for line in r.stdout.splitlines():
        m = re.match(r"^-\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s+.*'(.+)'$", line.strip())
        if m:
            out.append((m.group(2), int(m.group(1))))
    return sorted(out)


def squash_extract(iso: str, offset: int, dest: str, members: list[str]) -> bool:
    """Pull specific paths out of a squashfs bundle sitting inside the ISO."""
    r = run(["unsquashfs", "-o", str(offset), "-d", dest, "-n", "-q", iso, *members])
    return r.returncode == 0


def kernel_banner(vmlinuz: str) -> tuple[str, str]:
    """Recover 'Linux version ...' from a bzImage.

    The payload is compressed (LZMA-alone on these builds), so try the raw image first,
    then each known decompressor over the tail.
    """
    data = open(vmlinuz, "rb").read()
    # A kernel image carries the banner TWICE: linux_proc_banner is truncated just after
    # "PREEMPT_DYNAMIC", while linux_banner also carries the build date. Take the longest
    # match, not the first, or the build date silently goes missing from the fingerprint.
    pat = re.compile(rb"Linux version ([0-9][^\s]*) (\([^)]*\)[^\n\x00]*)")

    def best(buf: bytes):
        ms = list(pat.finditer(buf))
        return max(ms, key=lambda x: len(x.group(0))) if ms else None

    m = best(data)
    if m:
        return m.group(1).decode(), m.group(0).decode(errors="replace").strip()
    for magic, tool in ((b"\x5d\x00\x00", ["xz", "-dc", "--format=lzma"]),
                        (b"\xfd7zXZ", ["xz", "-dc"]),
                        (b"\x1f\x8b", ["gzip", "-dc"]),
                        (b"\x04\x22\x4d\x18", ["lz4", "-dc"]),
                        (b"\x28\xb5\x2f\xfd", ["zstd", "-dc"])):
        i = data.find(magic, 0x200)
        while i > 0:
            p = subprocess.run(tool, input=data[i:], capture_output=True)
            if p.stdout:
                m = best(p.stdout)
                if m:
                    return m.group(1).decode(), m.group(0).decode(errors="replace").strip()
            i = data.find(magic, i + 1)
            if i > len(data) - 1024:
                break
    return "", ""


def fingerprint(iso: str, name: str | None = None) -> dict:
    fp: dict = {}
    tmp = tempfile.mkdtemp(prefix="kitchen-fp-")
    try:
        with IsoReader(iso) as r:
            info = r.info()
            squashes = r.squashfs_images()

        fp["iso"] = {
            "size": info.size,
            "sha256": sha256(iso),
            "volume_id": info.volume_id,
            "system_id": info.system_id,
            "application_id": info.application_id,
            "volume_space": info.volume_space,
            "rock_ridge": info.rock_ridge,
            "joliet": info.joliet,
            "isohybrid_mbr": info.isohybrid_mbr,
            "gpt": info.gpt,
        }
        fp["eltorito"] = {
            "entries": [
                {"platform": e.platform_name, "media": e.media,
                 "bootable": e.bootable, "sectors": e.sector_count}
                for e in info.boot_entries if e.kind in ("initial", "section")
            ],
            "uefi_bootable": info.uefi_bootable,
        }
        if info.boot_info_table:
            b = info.boot_info_table
            fp["eltorito"]["boot_info_table"] = {
                "file_len": b["file_len"],
                "checksum": f"0x{b['checksum']:08x}",
                "self_consistent": b["self_consistent"],
            }

        # ---- /slax/boot ----------------------------------------------------
        boot = os.path.join(tmp, "boot")
        iso_extract(iso, BOOT_DIR, boot)
        files = {}
        for root, _d, names in os.walk(boot):
            for n in sorted(names):
                p = os.path.join(root, n)
                rel = os.path.relpath(p, boot)
                files[rel] = {"size": os.path.getsize(p), "sha256": sha256(p)}
        fp["boot_files"] = dict(sorted(files.items()))

        # ---- kernel --------------------------------------------------------
        vmlinuz = os.path.join(boot, "vmlinuz")
        rel, banner = kernel_banner(vmlinuz) if os.path.exists(vmlinuz) else ("", "")
        fp["kernel"] = {"release": rel, "banner": banner,
                        "sha256": files.get("vmlinuz", {}).get("sha256", "")}

        # ---- initramfs -----------------------------------------------------
        img = os.path.join(boot, "initrfs.img")
        if os.path.exists(img):
            irfs = os.path.join(tmp, "irfs")
            os.makedirs(irfs, exist_ok=True)
            with open(img, "rb") as fh:
                dec = subprocess.run(["xz", "-dc"], stdin=fh, capture_output=True)
            subprocess.run(["cpio", "-idm", "--quiet"], input=dec.stdout,
                           cwd=irfs, capture_output=True)
            ir: dict = {
                "sha256": files.get("initrfs.img", {}).get("sha256", ""),
                "compressed_size": os.path.getsize(img),
                "uncompressed_size": len(dec.stdout),
            }
            ir["scripts"] = {s: sha256(os.path.join(irfs, s))
                             for s in INITRAMFS_SCRIPTS
                             if os.path.isfile(os.path.join(irfs, s))}
            bb = os.path.join(irfs, "bin", "busybox")
            if os.path.isfile(bb):
                ver = run(["sh", "-c", f"strings -a {bb!r} | grep -m1 -E 'BusyBox v[0-9]'"])
                ir["busybox"] = {
                    "sha256": sha256(bb),
                    "size": os.path.getsize(bb),
                    "version": ver.stdout.strip(),
                    "file": run(["file", "-b", bb]).stdout.strip().split(",")[0:3],
                }
            ir["helpers"] = {h: sha256(os.path.join(irfs, "bin", h))
                             for h in INITRAMFS_HELPERS
                             if os.path.isfile(os.path.join(irfs, "bin", h))}
            bindir = os.path.join(irfs, "bin")
            if os.path.isdir(bindir):
                ir["applet_symlinks"] = sum(
                    1 for n in os.listdir(bindir)
                    if os.path.islink(os.path.join(bindir, n)))
            moddir = os.path.join(irfs, "lib", "modules")
            if os.path.isdir(moddir):
                dirs = sorted(os.listdir(moddir))
                ir["module_dir"] = dirs[0] if dirs else ""
                ir["ko_count"] = sum(len([f for f in fs if f.endswith(".ko")])
                                     for _r, _d, fs in os.walk(moddir))
            fp["initramfs"] = ir

        # ---- bundles -------------------------------------------------------
        listing = iso_listdir(iso, MODULES_DIR)
        by_size = {sq.bytes_used: sq for sq in squashes}
        bundles = {}
        for bname, bsize in listing:
            sq = next((s for s in squashes
                       if abs(s.bytes_used - bsize) < 4096), None)
            entry = {"size": bsize}
            if sq:
                entry.update({
                    "offset": sq.offset, "inodes": sq.inodes,
                    "block_size": sq.block_size, "compression": sq.compression,
                    "flags": f"0x{sq.flags:04x}", "flag_names": sq.flag_names,
                    "version": sq.version, "xz_dict_size": sq.xz_dict_size,
                    "xz_bcj": {1: "x86"}.get(sq.xz_bcj, sq.xz_bcj),
                })
            bundles[bname] = entry
        fp["bundles"] = bundles

        # ---- identity ------------------------------------------------------
        core = next((b for b in bundles if b.startswith("01-core")), None)
        ident: dict = {}
        if core and "offset" in bundles[core]:
            cx = os.path.join(tmp, "core")
            squash_extract(iso, bundles[core]["offset"], cx,
                           ["etc/slax-version", "etc/os-release", "etc/debian_version",
                            "etc/slackware-version", "usr/lib/os-release",
                            "usr/bin/ls", "bin/ls", "usr/bin/bash", "bin/bash"])
            for key, rel_ in (("slax_version_file", "etc/slax-version"),
                              ("debian_version", "etc/debian_version"),
                              ("slackware_version", "etc/slackware-version")):
                p = os.path.join(cx, rel_)
                if os.path.isfile(p):
                    ident[key] = open(p, errors="replace").read().strip()
            for osr in ("etc/os-release", "usr/lib/os-release"):
                p = os.path.join(cx, osr)
                if os.path.isfile(p):
                    for line in open(p, errors="replace"):
                        if line.startswith("ID="):
                            ident["os_release_id"] = line.split("=", 1)[1].strip().strip('"')
                        if line.startswith("PRETTY_NAME="):
                            ident["os_release_pretty"] = line.split("=", 1)[1].strip().strip('"')
                    break
            # Authoritative arch: probe a real ELF. /etc/slax-version LIES on the
            # 32-bit Debian ISO -- it says "64bit" while the userland is i386.
            # Note busybox is NOT usable here: it is i386 on all four ISOs by design.
            # Use file -L: on Slackware /usr/bin/ls is a symlink to ../../bin/ls, and an
            # unresolved "symbolic link to ..." string contains no bitness at all.
            for cand in ("usr/bin/ls", "bin/ls", "usr/bin/bash", "bin/bash"):
                p = os.path.join(cx, cand)
                if not os.path.exists(p):
                    continue
                out = run(["file", "-bL", p]).stdout
                if "ELF" not in out:
                    continue
                ident["arch_probe_file"] = cand
                ident["arch_probe_result"] = out.strip().split(",")[0]
                ident["arch"] = "32bit" if "32-bit" in out else "64bit"
                break
        if "arch" not in ident and fp.get("kernel", {}).get("release", "").endswith("-smp"):
            ident["arch"] = "32bit"       # 32-bit Slax kernels carry LOCALVERSION=-smp
        claimed = ident.get("slax_version_file", "")
        if claimed and ident.get("arch"):
            says = "32bit" if "32bit" in claimed else "64bit" if "64bit" in claimed else ""
            if says and says != ident["arch"]:
                ident["arch_mismatch"] = (
                    f"/etc/slax-version claims {says} but the ELF userland is "
                    f"{ident['arch']} -- upstream mislabelled this build")
        fp["identity"] = ident

        flav = "slackware" if "slackware" in str(ident).lower() else "debian"
        m = re.search(r"(\d+\.\d+\.\d+)", claimed)
        fp["metadata"] = {
            "name": name or f"{flav}-{ident.get('arch','unknown')}-{m.group(1) if m else 'unknown'}",
            "flavour": flav,
            "arch": ident.get("arch", "unknown"),
            "version": m.group(1) if m else "unknown",
        }
        fp["apiVersion"] = "slax-kitchen/v1"
        fp["kind"] = "Fingerprint"
        return fp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="build a compat fingerprint for a Slax ISO")
    ap.add_argument("iso")
    ap.add_argument("-o", "--output", help="write YAML here (default: stdout)")
    ap.add_argument("-n", "--name", help="override the fingerprint name")
    a = ap.parse_args(argv[1:])
    if not os.path.isfile(a.iso):
        print(f"no such file: {a.iso}", file=sys.stderr)
        return 2
    try:
        import yaml
    except ImportError:
        print("PyYAML required (pip install PyYAML)", file=sys.stderr)
        return 2
    fp = fingerprint(a.iso, a.name)
    order = ["apiVersion", "kind", "metadata", "identity", "iso", "eltorito",
             "kernel", "initramfs", "bundles", "boot_files"]
    ordered = {k: fp[k] for k in order if k in fp}
    text = yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False, width=100)
    header = ("# Generated by lib/fingerprint.py -- do not hand-edit.\n"
              "# Regenerate:  ./lib/fingerprint.py <iso> -o <this file>\n")
    if a.output:
        with open(a.output, "w") as f:
            f.write(header + text)
        print(f"wrote {a.output}")
    else:
        sys.stdout.write(header + text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
