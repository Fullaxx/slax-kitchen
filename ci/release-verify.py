#!/usr/bin/env python3
"""Refuse a release directory that does not carry what it says it carries.

    ci/release-verify.py <outdir> [--assert-no-images]

<outdir> is what ci/release-assets.sh wrote. Every problem is printed, then the exit status
is 1; a directory that passes exits 0. Nothing is fetched and nothing is uploaded.

What it refuses:

  - a SHA256SUMS that does not list exactly the files present, or a hash that is wrong
  - a release-index.json that disagrees with the directory
  - a provenance record, sources manifest or image whose sha256 do not agree
  - anything `kitchen sources` left unresolved, and any recipe marked not redistributable
  - something built here whose source is not among the assets
  - a project archive whose submodules are missing, or differ from the recorded pins
  - an asset of 2 GiB or more, more than 1000 assets (GitHub's limits), or a name GitHub
    would rename
  - a path on the build machine in any of the three records
  - with an image attached: Slax's firmware bundle without the copyright files of its Debian
    firmware packages, which firmware-refresh reinstalls (the b43 files in that bundle never
    had a license text, and are kept)
  - with --assert-no-images: an image attached, or any asset whose CONTENT is an ISO 9660
    image, a squashfs, an ELF or PE executable, or a FAT filesystem -- checked by magic
    bytes, not by name

WHY --assert-no-images EXISTS. The weekly CI job builds the `tor` profile and assembles its
assets as proof that the pipeline works, and it must never publish that image: the Tor
Project's trademark policy does not allow "Tor" in the name of another product without
written permission. Checking names would pass a renamed ISO. Checking content does not.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))

from provenance import hostish_values  # noqa: E402

MAX_ASSET = 2 * 1024 ** 3
MAX_ASSETS = 1000
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def content_kind(path: str) -> str | None:
    """What an asset IS, from its bytes: an image or executable format, or None."""
    with open(path, "rb") as f:
        head = f.read(0x8800)
    if head[:4] == b"\x7fELF":
        return "an ELF executable"
    if head[:4] in (b"hsqs", b"sqsh"):
        return "a squashfs filesystem"
    if head[0x8001:0x8006] == b"CD001":
        return "an ISO 9660 image"
    if head[:2] == b"MZ" and len(head) >= 0x40:
        off = int.from_bytes(head[0x3C:0x40], "little")
        if head[off:off + 4] == b"PE\0\0":
            return "a PE executable"
    if len(head) >= 512 and head[510:512] == b"\x55\xaa" and \
            (head[54:59] in (b"FAT12", b"FAT16") or head[82:87] == b"FAT32"):
        return "a FAT filesystem"
    return None


def read_sums(path: str) -> dict[str, str]:
    out = {}
    for ln in open(path):
        m = re.match(r"^([0-9a-f]{64}) [ *](.+)$", ln.rstrip("\n"))
        if m:
            out[m.group(2)] = m.group(1)
    return out


def check_tree_archive(path: str, name: str, state: dict | None, bad) -> None:
    """A project archive holds its submodules, and they are the ones the build recorded."""
    try:
        with tarfile.open(path) as t:
            names = t.getnames()
            top = names[0].split("/")[0] if names else ""
            member = t.extractfile(f"{top}/SUBMODULES.txt") if f"{top}/SUBMODULES.txt" in names else None
            listed = {}
            if member is not None:
                for ln in member.read().decode().splitlines():
                    sha, _, sub = ln.partition(" ")
                    listed[sub] = sha
    except (tarfile.TarError, OSError) as e:
        bad(f"{name}: not a readable archive ({e})")
        return
    if member is None:
        bad(f"{name}: no SUBMODULES.txt, so nothing says which submodules it should hold")
        return
    pins = (state or {}).get("submodules") or {}
    if listed != pins:
        bad(f"{name}: SUBMODULES.txt does not match the submodule pins the build recorded")
    for sub in sorted(pins):
        if not any(n.startswith(f"{top}/{sub}/") for n in names):
            bad(f"{name}: submodule {sub} is empty in the archive")


def verify(outdir: str, assert_no_images: bool = False) -> list[str]:
    problems: list[str] = []
    bad = problems.append

    if not os.path.isdir(outdir):
        return [f"{outdir}: not a directory"]
    present = sorted(n for n in os.listdir(outdir) if os.path.isfile(os.path.join(outdir, n)))
    for n in ("SHA256SUMS", "release-index.json"):
        if n not in present:
            bad(f"no {n}")
    if problems:
        return problems

    # --- the directory, SHA256SUMS and the index agree ------------------------------
    sums = read_sums(os.path.join(outdir, "SHA256SUMS"))
    files = set(present) - {"SHA256SUMS"}
    for n in sorted(files - set(sums)):
        bad(f"SHA256SUMS does not list {n}")
    for n in sorted(set(sums) - files):
        bad(f"SHA256SUMS lists {n}, which is not here")
    for n in sorted(files & set(sums)):
        if sha256(os.path.join(outdir, n)) != sums[n]:
            bad(f"{n}: sha256 does not match SHA256SUMS")
    for n in sorted(files):
        if not SAFE.match(n):
            bad(f"{n}: GitHub would rename this asset")
        if os.path.getsize(os.path.join(outdir, n)) >= MAX_ASSET:
            bad(f"{n}: 2 GiB or more, which GitHub does not accept as a release asset")
    if len(present) > MAX_ASSETS:
        bad(f"{len(present)} assets; a GitHub release holds at most {MAX_ASSETS}")

    try:
        index = json.load(open(os.path.join(outdir, "release-index.json")))
    except ValueError as e:
        return problems + [f"release-index.json: not JSON ({e})"]
    assets = {a.get("name"): a for a in index.get("assets") or []}
    for n in sorted(set(assets) - files):
        bad(f"release-index.json names {n}, which is not here")
    for n in sorted(files - set(assets) - {"release-index.json"}):
        bad(f"{n} is here but release-index.json does not describe it")
    for n, a in sorted(assets.items()):
        if n in files and a.get("sha256") != sums.get(n):
            bad(f"{n}: release-index.json and SHA256SUMS disagree on its sha256")

    def one(role: str, suffix: str) -> tuple[str | None, dict | None]:
        found = [n for n, a in assets.items() if a.get("role") == role and n.endswith(suffix)]
        if len(found) != 1:
            bad(f"expected one {role} asset ending {suffix}, found {len(found)}")
            return None, None
        try:
            return found[0], json.load(open(os.path.join(outdir, found[0])))
        except (OSError, ValueError) as e:
            bad(f"{found[0]}: not readable JSON ({e})")
            return found[0], None

    _pn, prov = one("provenance", ".provenance.json")
    _sn, src = one("sources", ".sources.json")
    if prov is None or src is None:
        return problems

    # --- one image, the same everywhere ---------------------------------------------
    image = index.get("image") or {}
    want = image.get("sha256")
    if not want:
        bad("release-index.json names no image sha256")
    if ((prov.get("pack") or {}).get("iso") or {}).get("sha256") != want:
        bad("the provenance record is for a different image than release-index.json names")
    if (src.get("image") or {}).get("sha256") != want:
        bad("the sources manifest is for a different image than release-index.json names")
    attached = [n for n, a in assets.items() if a.get("role") == "image"]
    if image.get("attached"):
        if len(attached) != 1:
            bad(f"release-index.json says the image is attached; {len(attached)} image assets are")
        elif attached[0] in files and sums.get(attached[0]) != want:
            bad(f"{attached[0]}: not the image the provenance describes")
    elif attached:
        bad("release-index.json says no image is attached, but one is")

    # --- what the manifest says -----------------------------------------------------
    for u in src.get("unresolved") or []:
        bad(f"unresolved in the image: {u.get('path')}: {u.get('reason')}")
    for r in src.get("not_redistributable") or []:
        bad(f"{r.get('recipe')} must not be in a published image: {r.get('why')}")

    covered = {c for a in assets.values() for c in a.get("covers") or []}
    for comp in src.get("components") or []:
        if comp.get("class") == "built" and comp.get("path") not in covered:
            bad(f"{comp.get('path')} was built here and its source is not attached")
        for part in comp.get("parts") or []:
            key = f"{comp.get('path')}:{part.get('member')}"
            if part.get("class") == "built" and key not in covered:
                bad(f"{key} was built here and its source is not attached")

    trees = {"(kitchen)": prov.get("kitchen"), "(project)": prov.get("project")}
    for label, state in trees.items():
        holders = [n for n, a in assets.items() if label in (a.get("covers") or [])]
        if state and state.get("commit") and not holders:
            bad(f"no archive of the {label.strip('()')} tree at {state['commit'][:12]}")
        for n in holders:
            if n in files:
                check_tree_archive(os.path.join(outdir, n), n, state, bad)

    for label, doc in (("provenance", prov), ("sources manifest", src), ("release-index.json", index)):
        for hit in hostish_values(doc):
            bad(f"{label} names a path on the build machine: {hit}")

    fw = src.get("firmware") or {}
    if image.get("attached") and fw.get("stock_bundle") and not fw.get("license_texts"):
        bad("the image carries Slax's firmware bundle without the copyright files of its Debian "
            "firmware packages; add firmware-refresh, which reinstalls them, or leave the firmware "
            "out with remove-bundle")

    # --- nothing that is an image, when none may be ---------------------------------
    if assert_no_images:
        if image.get("attached") or attached:
            bad("--assert-no-images, and an image is attached")
        for n in sorted(files):
            kind = content_kind(os.path.join(outdir, n))
            if kind:
                bad(f"--assert-no-images, and {n} is {kind}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir")
    ap.add_argument("--assert-no-images", action="store_true",
                    help="fail if any asset is an image or executable, whatever its name")
    a = ap.parse_args()
    problems = verify(a.outdir, a.assert_no_images)
    for p in problems:
        print(f"  FAIL {p}")
    if problems:
        print(f"release-verify: {len(problems)} problem(s) in {a.outdir}")
        return 1
    print(f"release-verify: {a.outdir} carries what it says")
    return 0


if __name__ == "__main__":
    sys.exit(main())
