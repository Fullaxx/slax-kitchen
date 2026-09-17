#!/usr/bin/env python3
"""The Redistribution section of release notes for a release that attaches an image.

    ci/redistribution-claim.py <assets-dir>

Reads what ci/release-assets.sh wrote -- release-index.json and the sources manifest --
and prints Markdown that says what IS attached, by name, and where the rest is published.
`RELEASE_ASSETS=<dir> ci/release-notes.sh <tag>` puts this in place of the "No image is
attached" section, which stays the default because a release of slax-kitchen itself
attaches nothing.

Every sentence is read off the directory rather than written ahead of time, so the notes
cannot claim an asset the release does not carry. Run ci/release-verify.py first: this
describes a directory, it does not check one.
"""
from __future__ import annotations

import json
import os
import sys


def claim(outdir: str) -> str:
    index = json.load(open(os.path.join(outdir, "release-index.json")))
    assets = index.get("assets") or []
    by_role: dict[str, list] = {}
    for a in assets:
        by_role.setdefault(a.get("role"), []).append(a)
    src_name = next(a["name"] for a in by_role.get("sources", []) if a["name"].endswith(".sources.json"))
    src = json.load(open(os.path.join(outdir, src_name)))
    md_name = next((a["name"] for a in by_role.get("sources", []) if a["name"].endswith(".SOURCES.md")),
                   None)
    image = index.get("image") or {}
    lines = ["## Redistribution", ""]
    if image.get("attached"):
        lines.append(f"This release attaches `{image.get('name')}`, sha256 `{image.get('sha256')}`. "
                     "`SHA256SUMS` checks every download; it does not promise that a rebuild "
                     "matches, because images are not byte-reproducible.")
    else:
        lines.append("No image is attached to this release. It carries the records and source "
                     f"for `{image.get('name')}`, sha256 `{image.get('sha256')}`.")
    lines += ["", "Attached with it:", ""]
    for a in by_role.get("provenance", []):
        lines.append(f"- `{a['name']}` — what the build fetched and built, with sha256s")
    if md_name:
        lines.append(f"- `{md_name}` and `{src_name}` — every file in the image, and where the "
                     "source of each part is published")
    for a in by_role.get("source", []):
        what = "; ".join(a.get("what") or []) or "source"
        lines.append(f"- `{a['name']}` — {what}")

    fw = src.get("firmware") or {}
    if fw.get("stock_bundle") or fw.get("license_texts") or fw.get("fetched_firmware"):
        lines += ["", "Using an image that contains firmware implies acceptance of each firmware's "
                      "license terms."]
        if fw.get("license_texts"):
            lines[-1] += (" Debian's copyright files for its firmware packages are inside the image"
                          + (", and a license file beside each file taken from linux-firmware."
                             if fw.get("fetched_firmware") else "."))
        elif fw.get("stock_bundle"):
            lines[-1] += (" Slax's own build removed the license texts of its firmware bundle; "
                          "`usr/lib/firmware/ipw2x00.LICENSE` is the one that remains.")
        if fw.get("stock_bundle"):
            lines[-1] += (" Slax's firmware bundle also holds the Broadcom b43 firmware its build "
                          "extracted from Broadcom's driver, and no license text came with those files.")
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
