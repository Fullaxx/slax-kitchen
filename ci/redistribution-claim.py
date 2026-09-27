#!/usr/bin/env python3
"""The Redistribution section of release notes for a release that carries images.

    ci/redistribution-claim.py <assets-dir>

Reads what ci/release-assets.sh wrote -- release-index.json and each image's sources
manifest -- and prints Markdown that says what IS attached, by name, every image with its
sha256, and where the rest is published. `RELEASE_ASSETS=<dir> ci/release-notes.sh <tag>`
puts this in place of the "No image is attached" section, which stays the default because a
release of slax-kitchen itself attaches nothing.

Every sentence is read off the directory rather than written ahead of time, so the notes
cannot claim an asset the release does not carry. Run ci/release-verify.py first: this
describes a directory, it does not check one.
"""
from __future__ import annotations

import json
import os
import sys


def firmware_sentence(fw: dict) -> str | None:
    """What an image's firmware facts allow the notes to say, or None when it has none."""
    if not (fw.get("stock_bundle") or fw.get("license_texts") or fw.get("fetched_firmware")):
        return None
    line = "Using an image that contains firmware implies acceptance of each firmware's license terms."
    d = fw.get("firmware_dir") or "usr/lib/firmware/"
    # Each fact stated when it holds, rather than one branch excluding the next: an image
    # whose firmware was fetched but whose stock bundle was dropped said only the first
    # sentence, and never said where the license files beside it are.
    if fw.get("license_texts"):
        line += " Debian's copyright files for its firmware packages are inside the image."
    if fw.get("fetched_firmware"):
        line += (f" A license file sits beside each of the {fw['fetched_firmware']} files "
                 "taken from linux-firmware.")
    if fw.get("stock_bundle") and not fw.get("license_texts"):
        line += (" Slax's own build removed the license texts of its firmware bundle; "
                 f"`{d}ipw2x00.LICENSE` is the one that remains.")
    if fw.get("stock_bundle"):
        line += (" Slax's firmware bundle also holds the Broadcom b43 firmware its build "
                 "extracted from Broadcom's driver, and no license text came with those files.")
    return line


def claim(outdir: str) -> str:
    index = json.load(open(os.path.join(outdir, "release-index.json")))
    images = index["images"]
    by_role: dict[str, list] = {}
    for a in index.get("assets") or []:
        by_role.setdefault(a.get("role"), []).append(a)
    attached = [im for im in images if im.get("attached")]
    lines = ["## Redistribution", ""]
    # One image reads as it always has; several are listed, each with its hash.
    if len(images) == 1:
        im = images[0]
        if attached:
            lines.append(f"This release attaches `{im.get('name')}`, sha256 `{im.get('sha256')}`. "
                         "`SHA256SUMS` checks every download; it does not promise that a rebuild "
                         "matches, because images are not byte-reproducible.")
        else:
            lines.append("No image is attached to this release. It carries the records and source "
                         f"for `{im.get('name')}`, sha256 `{im.get('sha256')}`.")
    else:
        if attached:
            lines.append(f"This release attaches {len(attached)} images, built from the same "
                         "commits. `SHA256SUMS` checks every download; it does not promise that a "
                         "rebuild matches, because images are not byte-reproducible.")
        else:
            lines.append("No image is attached to this release. It carries the records and source "
                         f"for {len(images)} images, built from the same commits:")
        lines.append("")
        for im in images:
            lines.append(f"- `{im.get('name')}`, sha256 `{im.get('sha256')}`")
    lines += ["", "Attached with it:" if attached else "Attached:", ""]
    for a in by_role.get("provenance", []):
        lines.append(f"- `{a['name']}` — what the build fetched and built, with sha256s")
    for im in images:
        lines.append(f"- `{im.get('sources_md')}` and `{im.get('sources')}` — every file in "
                     + ("the image" if len(images) == 1 else f"`{im.get('name')}`")
                     + ", and where the source of each part is published")
    for a in by_role.get("source", []):
        # `what` is a list in release-index.json, but one string reads as a list of
        # characters to join(), which would describe an asset as "G; R; U; B".
        w = a.get("what")
        what = "; ".join([w] if isinstance(w, str) else (w or [])) or "source"
        lines.append(f"- `{a['name']}` — {what}")

    # The firmware sentence is each image's own, from its own sources manifest; images
    # that say the same thing share it.
    said: dict[str, list[str]] = {}
    for im in images:
        src = json.load(open(os.path.join(outdir, im["sources"])))
        text = firmware_sentence(src.get("firmware") or {})
        if text:
            said.setdefault(text, []).append(im.get("name"))
    for text, names in said.items():
        if len(said) > 1 or len(names) < len(images):
            text = "For " + ", ".join(f"`{n}`" for n in names) + ": " + text[0].lower() + text[1:]
        lines += ["", text]
    lines += ["", "Slax and Linux Live Kit are the work of **Tomáš Matějíček** — "
                  "<https://www.slax.org>."]
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: ci/redistribution-claim.py <assets-dir>", file=sys.stderr)
        return 2
    try:
        sys.stdout.write(claim(sys.argv[1]))
    except (OSError, ValueError, StopIteration, KeyError) as e:
        print(f"redistribution-claim: {sys.argv[1]} is not an assets directory ({e!r})",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
