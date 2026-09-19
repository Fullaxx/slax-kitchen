#!/usr/bin/env python3
"""Assert structural properties of a built Slax ISO. No booting, no mounting.

This is the Tier A check: it runs in about a second and catches the failures that are
expensive to find any other way -- a bootloader that will not load, a bundle built with
the wrong compressor, a UEFI recipe that silently did not take effect.

Exit 0 if every assertion holds.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
import need  # noqa: E402
# By these names, not `import diff`: main() has a local `diff` of its own.
from diff import ListingError, _entries as image_entries  # noqa: E402
from isoparse import IsoReader  # noqa: E402

# Every stock bundle on all four reference ISOs has exactly these parameters, and so must
# anything we build -- create_bundle(), dir2sb and savechanges all use the same
# mksquashfs flags. A mismatch means a bundle was built with the wrong command line.
WANT_SQUASHFS = {"version": "4.0", "compression": "xz", "block_size": 1048576,
                 "flags": 0x04E0, "xz_dict_size": 1048576, "xz_bcj": 1}

REQUIRED = ["slax/boot/vmlinuz", "slax/boot/initrfs.img", "slax/boot/isolinux.bin",
            "slax/boot/isolinux.cfg", "slax/boot/syslinux.cfg"]


class Asserter:
    def __init__(self) -> None:
        self.fail: list[str] = []
        self.ok = 0

    def check(self, cond: bool, msg: str, detail: str = "") -> None:
        if cond:
            self.ok += 1
            print(f"  ok   {msg}")
        else:
            self.fail.append(msg + (f"  ({detail})" if detail else ""))
            print(f"  FAIL {msg}" + (f"  ({detail})" if detail else ""))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="structural assertions for a Slax ISO")
    ap.add_argument("iso")
    ap.add_argument("--expect-uefi", action="store_true", help="require an EFI El Torito entry")
    ap.add_argument("--expect-hybrid", action="store_true", help="require an isohybrid MBR")
    ap.add_argument("--expect-gpt", action="store_true")
    ap.add_argument("--volid", default="slax",
                    help="expected volume id (iso.metadata can legitimately change it)")
    ap.add_argument("--max-size-mib", type=int)
    ap.add_argument("--require", action="append", default=[],
                    help="extra path that must exist in the ISO")
    a = ap.parse_args(argv[1:])
    # xorriso lists the image's files. Checked first: without it the listing below died in
    # a traceback, after half the assertions had already printed.
    need.require(["xorriso"], "iso_assert.py")
    if not os.path.isfile(a.iso):
        print(f"no such file: {a.iso}", file=sys.stderr)
        return 2

    t = Asserter()
    print(f"structure: {a.iso}  ({os.path.getsize(a.iso) / 1048576:.0f} MiB)")

    with IsoReader(a.iso) as r:
        info = r.info()
        squashes = r.squashfs_images()

        # --- container -----------------------------------------------------
        t.check(info.volume_id == a.volid, f"volume id is {a.volid!r}",
                f"got {info.volume_id!r}")
        t.check(info.rock_ridge, "Rock Ridge present",
                "without it, lowercase names and the exec bit on bootinst.sh are lost")
        t.check(info.joliet, "Joliet present")

        # --- El Torito -----------------------------------------------------
        bios = [e for e in info.boot_entries
                if e.kind in ("initial", "section") and e.platform == 0x00]
        efi = [e for e in info.boot_entries
               if e.kind in ("initial", "section") and e.platform == 0xEF]
        t.check(len(bios) == 1, "exactly one BIOS El Torito entry", f"found {len(bios)}")
        if bios:
            t.check(bios[0].bootable and bios[0].media == "no-emulation",
                    "BIOS entry is bootable, no-emulation",
                    f"bootable={bios[0].bootable} media={bios[0].media}")
            t.check(bios[0].sector_count == 4, "BIOS boot-load-size is 4 sectors",
                    f"got {bios[0].sector_count}")

        if a.expect_uefi:
            t.check(len(efi) == 1, "EFI El Torito entry present",
                    "the uefi-bootable recipe did not take effect" if not efi else "")
        else:
            # Symmetric on purpose: an ISO that GREW a UEFI entry nobody asked for is
            # as much a surprise as one that lost the entry a recipe added. But the old
            # wording -- "no EFI entry (as expected for a stock build)" -- described the
            # PASS, so when it failed it read like an explanation rather than a problem.
            # Say what went wrong, and name the flag that makes it right.
            t.check(not efi, "no EFI El Torito entry, and none was expected",
                    "this ISO HAS a UEFI entry -- pass --expect-uefi if that is intended "
                    "(kitchen build derives it from the recipe list)")

        bit = info.boot_info_table
        t.check(bool(bit) and bit["self_consistent"],
                "isolinux boot-info-table is self-consistent",
                "file_lba does not match the El Torito entry")

        # --- hybrid --------------------------------------------------------
        if a.expect_hybrid:
            t.check(info.isohybrid_mbr, "isohybrid MBR present",
                    "without it, dd to a USB stick produces a non-bootable stick")
            if a.expect_gpt or a.expect_uefi:
                t.check(info.gpt, "GPT present alongside the MBR")
                t.check(any(p["type"] == 0xEF for p in info.partitions),
                        "an EFI System Partition (type 0xEF) is in the partition table")
        else:
            t.check(not info.isohybrid_mbr, "no isohybrid MBR (as expected)")

        # --- bundles -------------------------------------------------------
        t.check(len(squashes) >= 1, "at least one squashfs bundle", f"found {len(squashes)}")
        bad = []
        for sq in squashes:
            got = {"version": sq.version, "compression": sq.compression,
                   "block_size": sq.block_size, "flags": sq.flags,
                   "xz_dict_size": sq.xz_dict_size, "xz_bcj": sq.xz_bcj}
            diff = {k: (v, got[k]) for k, v in WANT_SQUASHFS.items() if got[k] != v}
            if diff:
                bad.append(f"@0x{sq.offset:x} {diff}")
        t.check(not bad, f"all {len(squashes)} bundles are v4.0/xz/1MiB/flags 0x04e0",
                "; ".join(bad[:2]))

        # --- required files ------------------------------------------------
        # The listing `kitchen diff` and `kitchen sources` use, which refuses one it cannot
        # trust. This had its own `xorriso -lsl` and regex, ignoring the exit status: a
        # listing that parsed to nothing reported the kernel, the initramfs and the
        # bootloader missing from an image that held all three.
        try:
            paths = set(image_entries(a.iso))
        except ListingError as e:
            t.check(False, "the image's files can be listed", str(e))
        else:
            check_files(t, paths, a.require)

    if a.max_size_mib:
        mib = os.path.getsize(a.iso) / 1048576
        t.check(mib <= a.max_size_mib, f"size <= {a.max_size_mib} MiB", f"is {mib:.0f} MiB")

    print(f"\n{t.ok} passed, {len(t.fail)} failed")
    return 1 if t.fail else 0


def check_files(t: Asserter, paths: set, extras: list) -> None:
    """The required files, and any --require, by exact path in the image's listing.

    EXACT PATHS. --require compared only the basename, and against /slax/boot's listing,
    so `--require /EFI/BOOT/isolinux.cfg` passed on an image with no /EFI at all, because
    /slax/boot has an isolinux.cfg.

    ONE FAILURE FOR ONE FACT. Without /slax/boot this is not a Slax image, and checking its
    five files would only restate that five times as five false specifics.
    """
    if "/slax/boot" not in paths:
        t.check(False, "/slax/boot is in the image",
                "it is not there -- this is not a Slax image, so its files were not checked")
        return
    for req in REQUIRED:
        t.check("/" + req in paths, f"{req} present")
    for extra in extras:
        t.check("/" + extra.lstrip("/") in paths, f"{extra} present")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
