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
import need  # noqa: E402
from isoparse import IsoReader  # noqa: E402

# Everything fingerprint() runs, checked before it runs any of it. `kitchen probe` calls
# fingerprint() too, and checks the same list.
NEEDS = ("xorriso", "unsquashfs", "xz", "cpio", "file")

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


# The four places a real ELF lives in 01-core, in the order they are tried. busybox is
# deliberately NOT one of them: it is i386 on all four ISOs by design, so it would answer
# 32bit for every base.
ARCH_CANDIDATES = ("usr/bin/ls", "bin/ls", "usr/bin/bash", "bin/bash")

# The version file each flavour's 01-core carries, in the order they are tried. The two
# are mutually exclusive on all four stock bases -- etc/debian_version on the Debian pair,
# etc/slackware-version on the Slackware pair -- so which one is PRESENT is the answer.
# The order is fixed rather than incidental: a rebuilt core carrying both gets one answer
# every time, not whichever the filesystem happened to hand back first.
FLAVOUR_CANDIDATES = (("slackware", "etc/slackware-version"),
                      ("debian", "etc/debian_version"))

# The bundle both probes read. A NAME, not a prefix -- see pick_core().
CORE_BUNDLE = "01-core.sb"


def pick_core(names) -> str | None:
    """Which of these bundles is 01-core, or None. THE EXACT NAME WINS.

    Both probes above answer about whichever bundle this returns, so picking the wrong one
    is picking the wrong answer -- and a prefix scan taking its first hit picks by sort
    order. `01-core-patches.sb` is a plausible name for an overlay onto the core, the
    recipe schema allows it, and `01` is not one of RESERVED_PREFIXES; it sorts BEFORE
    `01-core.sb` because "-" is 0x2d and "." is 0x2e. So lib/apply.py and this module both
    read the overlay while ci/gen-manifests.sh, which matches the exact name, hashed the
    real core -- one tree, two answers (#40).

    That failure is already named in this tree. _bundle_stack's docstring: "A prefix
    resolves to EVERY match, so `from: [01]` means 01-core AND 01-firmware rather than
    silently just the first one." Found and fixed there, and left standing here.

    The prefix scan survives as the fallback, sorted, because a fork may legitimately ship
    `01-core-15.0.4.sb` and no exact name to match -- and sorting is what makes the two
    callers agree, since one lists a directory and the other takes xorriso's listing order.
    """
    names = list(names)
    if CORE_BUNDLE in names:
        return CORE_BUNDLE
    return next((n for n in sorted(names) if n.startswith("01-core")), None)


def resolve_within(root: str, rel: str, hops: int = 10) -> str | None:
    """Resolve `rel` under `root`, following symlinks but NEVER leaving `root`.

    An extracted bundle is an image's filesystem sitting in a temporary directory, and its
    symlinks were written for the image's root, not ours. Slackware's 01-core has both
    kinds: `usr/bin/ls -> ../../bin/ls`, relative, which lands where you expect -- and
    `usr/bin/bash -> /bin/bash`, ABSOLUTE, which the host's resolver follows straight out
    of the extract. Measured on the 32-BIT Slackware image, 2026-09-20: `file -bL` on that
    path answered "ELF 64-bit LSB pie executable, x86-64", which is this machine's
    /bin/bash and not the image's -- in the probe this project calls authoritative about
    arch, in a tree that spent four commits (#20, #26) getting host paths out of
    provenance.

    So an absolute target is re-rooted at `root`, a relative one is joined, and anything
    still climbing out is refused rather than followed. None for a dangling link, an
    escape, a loop, or a path that is not there.
    """
    cur = rel.lstrip("/")
    for _ in range(hops):
        if cur == ".." or cur.startswith("../"):
            return None
        p = os.path.join(root, cur)
        if not os.path.islink(p):
            return p if os.path.exists(p) else None
        target = os.readlink(p)
        cur = (target.lstrip("/") if os.path.isabs(target)
               else os.path.normpath(os.path.join(os.path.dirname(cur), target)))
    return None


def arch_from_extract(cx: str) -> tuple[str, str] | None:
    """(arch, which candidate answered), read from an ELF header in an extracted 01-core.

    THE ONE AUTHORITY ON ARCH. `kitchen probe` and `kitchen apply`'s `when: arch==` both
    come here, so a fingerprint and a recipe guard cannot disagree about the same tree.

    Why a binary rather than a label: every cheaper source lies or is missing on at least
    one of the four bases. /etc/slax-version says "Slax 12.2.0 64bit" on the genuine
    32-BIT Debian ISO -- upstream mislabelled it. dpkg's status file is clean on Debian
    (247 i386 against 247 amd64) and does not exist on Slackware at all. And the ISO's own
    filename is whatever somebody saved it as, which was issue #27.

    Nothing is executed. Byte 4 of an ELF header is EI_CLASS, the field whose whole job is
    to declare the binary's width.
    """
    for cand in ARCH_CANDIDATES:
        p = resolve_within(cx, cand)
        if not p or not os.path.isfile(p):
            continue
        with open(p, "rb") as fh:
            head = fh.read(5)
        if head[:4] != b"\x7fELF" or head[4:5] not in (b"\x01", b"\x02"):
            continue
        return ("32bit" if head[4:5] == b"\x01" else "64bit"), cand
    return None


def flavour_from_extract(cx: str) -> str:
    """"debian", "slackware", or "unknown", read from an extracted 01-core's version file.

    THE ONE AUTHORITY ON FLAVOUR, the way arch_from_extract() above is the one authority on
    arch. `kitchen probe` and `kitchen apply`'s `when: flavour==` both come here, so a
    fingerprint and a recipe guard cannot disagree about the same tree.

    They did. This module decided flavour with `"slackware" if "slackware" in
    str(ident).lower() else "debian"` -- a substring match over a STRINGIFIED DICT, which
    matches a key name as readily as a value, and which had no third answer, so an ISO
    nothing had been read from was reported as Debian. 012e720 took that same default out
    of lib/apply.py's _detect_flavour and left this copy standing: one 01-core carrying
    neither version file was `unknown` to the recipe guard and `debian` to the fingerprint
    (#35).

    A file that is there rather than a string that matched, because the presence of one
    name IS the distinction -- and unlike arch, nothing has to be opened to see it.

    NARROWER than what it replaces, deliberately: the old match reached etc/os-release
    through ident's os_release_id, so a rebuilt core that drops slackware-version but
    keeps os-release read "slackware" and now reads "unknown". That is the answer arch
    gives for a tree it cannot read, and --facts is how to say otherwise. lib/apply.py's
    _detect_flavour never consulted os-release either, so this is the two of them meeting
    on the signal the recipe guard already used, not a new one.
    """
    for flav, rel in FLAVOUR_CANDIDATES:
        p = resolve_within(cx, rel)
        if p and os.path.isfile(p):
            return flav
    return "unknown"


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
            # Blank on every stock image. Recorded so that `iso.metadata` setting them
            # shows up as a difference rather than as nothing at all.
            "publisher_id": info.publisher_id,
            "preparer_id": info.preparer_id,
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
                        "sha256": (files.get("vmlinuz") or {}).get("sha256", "")}

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
        core = pick_core(bundles)
        ident: dict = {}
        # "unknown" unless a 01-core is actually read below -- the answer `version` and
        # `arch` already give for an image nothing could be read from (#35).
        flav = "unknown"
        if core and "offset" in bundles[core]:
            cx = os.path.join(tmp, "core")
            # The flavour paths are spread from FLAVOUR_CANDIDATES rather than spelled
            # again, the way ARCH_CANDIDATES already is: flavour_from_extract() looks for
            # exactly these, so a list that named them separately could stop extracting
            # one and turn every ISO's flavour "unknown" without a word.
            squash_extract(iso, bundles[core]["offset"], cx,
                           ["etc/slax-version", "etc/os-release", "usr/lib/os-release",
                            *(rel for _flav, rel in FLAVOUR_CANDIDATES),
                            *ARCH_CANDIDATES])
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
            # Authoritative arch, decided by arch_from_extract() -- the same probe
            # `kitchen apply` reads `when: arch==` from, so a fingerprint and a recipe
            # guard cannot disagree about one tree. `file` is still asked, but ONLY for
            # the human-readable line: the answer is EI_CLASS, in one place.
            hit = arch_from_extract(cx)
            if hit:
                ident["arch_probe_file"] = hit[1]
                real = resolve_within(cx, hit[1])
                ident["arch_probe_result"] = run(
                    ["file", "-b", real]).stdout.strip().split(",")[0]
                ident["arch"] = hit[0]
            # From the same extract: its member list above already pulls both version
            # files, so this costs no unsquashfs of its own.
            flav = flavour_from_extract(cx)
        if "arch" not in ident and (fp.get("kernel") or {}).get("release", "").endswith("-smp"):
            ident["arch"] = "32bit"       # 32-bit Slax kernels carry LOCALVERSION=-smp
        claimed = ident.get("slax_version_file", "")
        if claimed and ident.get("arch"):
            says = "32bit" if "32bit" in claimed else "64bit" if "64bit" in claimed else ""
            if says and says != ident["arch"]:
                ident["arch_mismatch"] = (
                    f"/etc/slax-version claims {says} but the ELF userland is "
                    f"{ident['arch']} -- upstream mislabelled this build")
        fp["identity"] = ident

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
    ap = argparse.ArgumentParser(prog="kitchen fingerprint", description="build a compat fingerprint for a Slax ISO")
    ap.add_argument("iso")
    ap.add_argument("-o", "--output", help="write YAML here (default: stdout)")
    ap.add_argument("-n", "--name", help="override the fingerprint name")
    a = ap.parse_args(argv[1:])
    need.require(NEEDS, "kitchen fingerprint")
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
