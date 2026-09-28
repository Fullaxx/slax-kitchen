#!/usr/bin/env python3
"""kitchen sources -- what is in a built ISO, and where the source of each part lives.

    kitchen sources <iso> [--provenance FILE] [--json FILE] [--markdown FILE]

What a build changed is in the project's repository and slax-kitchen's, at the commits
the provenance records; for the rest, this says where each upstream publishes its source,
where that is known. It works out where every file in the ISO came from, from evidence
rather than from a list someone keeps:

  slax        sha256 equals the committed stock manifest for the base image
              (docs/30-inventory/manifests/{bootfiles,isofiles,initramfs}-<target>.sha256)
  debian      a bundle a recorded bundle.packages step produced, byte for byte: its packages
  slackware   the same, for Slackware packages
  prebuilt    a recorded download, installed unmodified -- an archive or a payload -- or
              bytes pack copied from the build host (the MBR). A file a bundle.files entry
              downloaded, or copied in naming an upstream_source, is the same thing inside
              a bundle: a prebuilt part of it, as is each file a script reported fetching
  built       compiled or assembled here: GRUB's EFI image, a busybox build, a binary a
              bundle.script declares
  recipe      written, generated or copied in by this build: a recipe's step, named, or
              kitchen pack
  base        made by no step of this build, on a base image this kitchen does not know as
              stock Slax: it came with the image the build started from
  unrecorded  no step of this build recorded it

No class is an error, and nothing here refuses an image (#62). The command exits 0 once the
report is written, and 2 when it cannot read the image, its sidecar or a tool it needs.

The evidence is the image's provenance sidecar (<iso>.provenance.json, written by `kitchen
pack`). A file matches a recorded step only if its sha256 is the one recorded, so a bundle
altered after the build is unrecorded rather than silently attributed. A repacked
initramfs is opened and its members compared with the stock manifest one by one, because
"the parts that match Slax are Slax" is a claim, and a claim has to be counted.

WHAT THE REPORT RESTS ON IS SAID. A download nobody pinned, a pointer nobody gave, a
checkout with uncommitted changes and a sidecar that describes another ISO are stated in
the report; none of them changes the exit status. Offline: nothing is fetched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
import re
import stat
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import need  # noqa: E402

SCHEMA = "slax-kitchen/sources/v2"
CLASSES = ("slax", "debian", "slackware", "prebuilt", "built", "recipe", "base", "unrecorded")
SLAX_SOURCE = "https://github.com/Tomas-M/linux-live"


# ------------------------------------------------------------------ pointers ------

def snapshot_url(source: str, version: str) -> str:
    """Debian's permanent archive page for one source package version. Epochs contain a
    colon, which the path has to carry encoded."""
    return ("https://snapshot.debian.org/package/"
            f"{urllib.parse.quote(source, safe='')}/{urllib.parse.quote(version, safe='')}/")


def launchpad_url(source: str, version: str) -> str:
    return (f"https://launchpad.net/ubuntu/+source/{urllib.parse.quote(source, safe='')}/"
            f"{urllib.parse.quote(version, safe='')}")


def host_source_url(pkg: dict | None, builder: dict | None) -> str | None:
    """Where the source of a BUILD-HOST package lives: Launchpad on Ubuntu, snapshot on
    Debian. None when the host package is unknown."""
    if not pkg or not pkg.get("source"):
        return None
    ver = pkg.get("source_version") or pkg.get("version")
    if (builder or {}).get("id") == "ubuntu":
        return launchpad_url(pkg["source"], ver)
    return snapshot_url(pkg["source"], ver)


# ------------------------------------------------------------------ inputs --------

def read_manifest(path: str) -> dict:
    """`sha256  ./path` lines -> {path: sha256}."""
    out = {}
    if os.path.isfile(path):
        for ln in open(path):
            parts = ln.rstrip("\n").split("  ", 1)
            if len(parts) == 2:
                out[parts[1][2:] if parts[1].startswith("./") else parts[1]] = parts[0]
    return out


def base_target(prov: dict, sources_yaml: dict) -> tuple[str | None, dict]:
    want = (prov.get("base") or {}).get("sha256")
    for name, t in (sources_yaml.get("targets") or {}).items():
        if want and t.get("sha256") == want:
            return name, t
    return None, {}


def stock_index(target: str | None, manifests: str) -> dict:
    """{path in the ISO: sha256} for the stock image, plus its initramfs members."""
    if not target:
        return {"files": {}, "initramfs": {}}
    files = {f"slax/boot/{p}": h for p, h in
             read_manifest(os.path.join(manifests, f"bootfiles-{target}.sha256")).items()}
    files.update(read_manifest(os.path.join(manifests, f"isofiles-{target}.sha256")))
    return {"files": files,
            "initramfs": read_manifest(os.path.join(manifests, f"initramfs-{target}.sha256"))}


def iso_files(iso: str) -> dict:
    """{path: sha256} for every regular file in the ISO, hashed from its extent."""
    import diff
    ents = diff._entries(iso)
    out = {}
    with open(iso, "rb") as fh:
        for path, e in ents.items():
            if e["type"] != "file" or e["lba"] is None:
                continue
            out[path.lstrip("/")] = diff._sha256_extent(fh, e["lba"], e["size"])
            if path == "/slax/boot/isolinux.bin":
                out["slax/boot/isolinux.bin@64"] = diff._sha256_extent(
                    fh, e["lba"], e["size"], diff.BIT_LEN)
    return out


def cpio_members(blob: bytes) -> dict:
    """{path: sha256} for the regular files in a `newc` cpio archive.

    Parsed here rather than shelled out to cpio, because unpacking needs privilege to get
    the answer right: Slax's initramfs holds seven device nodes, and a non-root cpio
    writes them as empty regular files (docs/30-inventory/manifests/README.md), which
    would then read as seven members the stock manifest does not have. Reading the headers
    costs nothing and answers for any user.
    """
    def field(at: int, i: int) -> int:
        """Header field `i`: newc writes each as eight ASCII hex digits, after the magic.

        STRICTLY eight hex digits. int() accepts a sign and surrounding space, so a field
        written `-000006e` parsed as a negative length, the next offset landed back on the
        current one, and the loop never ended -- on an image the operator did not build."""
        raw = blob[at + 6 + i * 8: at + 14 + i * 8]
        if not HEX8.fullmatch(raw):
            raise ValueError(f"cpio: header field {i} at {at} is not eight hex digits")
        return int(raw, 16)

    # Stops at the first TRAILER!!!, so a concatenated initramfs (several archives one
    # after another, which the kernel accepts) reads as its first segment. Slax builds one.
    out: dict[str, str] = {}
    linked: dict[tuple, list[str]] = {}       # inode -> the names sharing it
    content: dict[tuple, str] = {}            # inode -> the hash of the one copy of its data
    off, trailer = 0, False
    while off + 110 <= len(blob):
        if blob[off:off + 6] != b"070701":
            break
        mode, nlink, filesize = field(off, 1), field(off, 4), field(off, 6)
        namesize = field(off, 11)
        name_at = off + 110
        data_at = (name_at + namesize + 3) & ~3
        if namesize < 1 or data_at + filesize > len(blob):
            raise ValueError("cpio: the archive ends in the middle of a member")
        name = blob[name_at: name_at + namesize - 1].decode("utf-8", "replace")
        if name == "TRAILER!!!":
            trailer = True
            break
        if stat.S_ISREG(mode):
            digest = hashlib.sha256(blob[data_at:data_at + filesize]).hexdigest()
            out[name] = digest
            # HARD LINKS. newc stores the data once, with the LAST name; every earlier
            # name gets filesize 0, which would hash as the empty file and read as a
            # member that no longer matches Slax. Names sharing an inode share the hash.
            if nlink > 1:
                ino = (field(off, 0), field(off, 7), field(off, 8))
                linked.setdefault(ino, []).append(name)
                if filesize:
                    content[ino] = digest
        off = (data_at + filesize + 3) & ~3
    if not trailer:
        # Every newc archive ends with one. Running off the end without seeing it means
        # the tail was cut off, and the members read so far are not the whole story.
        raise ValueError("cpio: no TRAILER!!! -- the archive is truncated")
    for ino, names in linked.items():
        if ino in content:
            for n in names:
                out[n] = content[ino]
    return out


# Slax's initramfs unpacks to about 40 MiB. The cap is for an image from somewhere else:
# xz compresses zeroes about 1000:1, so a 20 MiB initrfs.img can ask for 20 GiB, and
# `kitchen sources` would be killed by the OOM killer rather than say what it found.
INITRAMFS_MAX = 512 << 20


def initramfs_from_bytes(blob: bytes) -> dict | None:
    """{member: sha256} for an xz-compressed newc initramfs, or None when the bytes are
    not one. Malformed input is an answer, not a traceback: this runs on images from
    elsewhere, and `kitchen sources` says what it could not read rather than dying."""
    try:
        d = lzma.LZMADecompressor()
        raw = d.decompress(blob, max_length=INITRAMFS_MAX)
        if not d.eof:
            return None                     # more than the cap, or a truncated stream
        return cpio_members(raw) or None
    except (lzma.LZMAError, ValueError, EOFError, MemoryError):
        return None


def initramfs_members(iso: str) -> dict | None:
    """{member path: sha256} for the initramfs inside an ISO, or None if it cannot be read.

    The stock manifests record every regular file in the initramfs
    (docs/30-inventory/manifests/initramfs-<target>.sha256), and the only way to say which
    of an image's members are still Slax's is to look at them.
    """
    import diff
    e = diff._entries(iso).get("/slax/boot/initrfs.img")
    if not e or e.get("lba") is None:
        return None
    with open(iso, "rb") as fh:
        fh.seek(e["lba"] * diff.SECTOR)
        blob = fh.read(e["size"])
    return initramfs_from_bytes(blob)


def initramfs_delta(members: dict | None, stock: dict) -> dict:
    """Which of an initramfs's members are the stock image's, member by member.

    Pure. `members` is None when the initramfs could not be unpacked, and then nothing is
    claimed about it -- the point of this is to stop asserting that the parts that match
    Slax match Slax."""
    # The manifest first: with nothing to compare against, that is the reason whether or
    # not the image's initramfs could be read, and saying "could not be read" about an
    # image that read perfectly sends the reader after the wrong problem.
    if not stock:
        return {"compared": False,
                "why": "this target has no committed initramfs manifest to compare against"}
    if members is None:
        return {"compared": False, "why": "the initramfs in the image could not be read"}
    same = [p for p, h in members.items() if stock.get(p) == h]
    return {"compared": True, "members": len(members), "stock_members": len(same),
            "changed": sorted(p for p, h in members.items() if stock.get(p) != h),
            "removed": sorted(p for p in stock if p not in members)}


def alpine_aports_url(release: str | None, name: str) -> str | None:
    """Alpine's recipe for a package -- APKBUILD, patches, the upstream tarball it names --
    on the stable branch of the build container's release. A branch, not the release tag:
    `apk add` installs the branch's newest build, which can be newer than the tag (musl was
    1.2.4_git20230717-r6 on 3.19-stable while the v3.19.9 tag had -r5). The recorded version
    says which build it was."""
    m = re.match(r"^(\d+\.\d+)\.", release or "")
    return (f"https://gitlab.alpinelinux.org/alpine/aports/-/tree/{m.group(1)}-stable/main/{name}"
            if m else None)


# ------------------------------------------------------------------ classify ------

def _download_parts(step: dict, by: str) -> list[dict]:
    """The files a step downloaded, as prebuilt parts of its bundle.

    A part is what `_pointer_for_prebuilt` says of a whole tarball, for one file inside a
    bundle that also holds other things: where it came from, its sha256, whether the
    recipe pinned it, and where its upstream publishes the source. A bundle.files `url:`
    entry is a file the engine fetched itself; a script's KITCHEN-FETCHED line is what the
    script reported, recorded as it printed it.
    """
    return [{k: v for k, v in (
        ("member", f.get("path")), ("class", "prebuilt"), ("by", by),
        ("source", f.get("url")), ("source_sha256", f.get("sha256")),
        ("upstream_source", f.get("upstream_source") or step.get("upstream_source")),
        ("pinned", f.get("pinned"))) if v is not None}
        for f in step.get("fetched") or []]


def _copied_parts(step: dict, by: str) -> list[dict]:
    """What a bundle.files `src:` entry copied in and named an upstream_source for -- an
    installer or a Flatpak tree the project's build staged (#60) -- as prebuilt parts."""
    return [{k: v for k, v in (
        ("member", c.get("path")), ("class", "prebuilt"), ("by", by),
        ("copied_from", c.get("input")),
        ("upstream_source", c.get("upstream_source"))) if v is not None}
        for c in step.get("copied") or []]


def _pointer_for_prebuilt(step: dict) -> dict:
    """What a downloaded file points at. `pinned` is False when the recipe named no sha256,
    so the file is whatever that server served on build day: recorded, but not reproducible.
    Steps written before the verbs recorded it say nothing, and nothing is claimed for them."""
    return {"source": step.get("source"), "source_sha256": step.get("source_sha256"),
            "member": step.get("member"), "upstream_source": step.get("upstream_source"),
            "pinned": step.get("pinned")}


def _debian_pointer(rec: dict) -> dict:
    """A package record with its source package and snapshot URL filled in. Debian omits
    Source: when the source package has the binary's name and version, so that is the
    fallback; with no version at all there is no pointer, and the caller says so."""
    src = rec.get("source") or rec.get("package")
    ver = rec.get("source_version") or rec.get("version")
    return dict(rec, source=src, source_version=ver,
                source_published_at=snapshot_url(src, ver) if src and ver else None)


def from_debian(origin: str) -> bool:
    """True when an apt index filename names an archive Debian itself runs.

    The origin is a FILENAME: the archive URI with "/" turned into "_", so everything
    before the first "_" is the host and the rest is the path. `debian.org` can sit
    anywhere in that path -- `mirror.example.com_debian.org_pub_dists_...` -- so only the
    host is tested, and a package from someone else's mirror stays undeclared."""
    host = (origin or "").split("_", 1)[0].split(":", 1)[0]
    # Any debian.org host counts, `people.debian.org` included, so a personal repository
    # there is taken for an archive Debian runs. It is still Debian's infrastructure and
    # the alternative is a hand-kept list of archive hostnames; the recipe declaring the
    # repository is what makes the pointer right, and this is the fallback.
    return host == "debian.org" or host.endswith(".debian.org")
APT_QUOTED = set('\\|{}[]<>"^~_=!@#$%^&*')


def apt_list_prefix(uri: str) -> str:
    """The name apt gives an archive's index files (apt-pkg's URItoFileName): the URI with
    no scheme or credentials, the characters apt quotes written as %xx, and "/" made "_".
    http://deb.debian.org/debian/ -> deb.debian.org_debian"""
    u = urllib.parse.urlsplit(uri)
    s = (u.hostname or "") + (f":{u.port}" if u.port else "") + u.path.rstrip("/")
    s = "".join(f"%{ord(c):02x}" if c in APT_QUOTED or ord(c) <= 0x20 or ord(c) >= 0x7f else c
                for c in s)
    return s.replace("/", "_")


def _packages(step: dict, flavour: str) -> tuple[list[dict], list[str]]:
    """Every package a bundle.packages/bundle.script step put in its bundle, where its source
    is published, and a note for each package there is no pointer for.

    Changed or new stanzas from the status diff, plus reinstalled ones known only from their
    .debs. Which archive a package came from is the apt index its .deb's sha256 was found in,
    recorded by the verb: a package from a recipe's own repository points at that
    repository's upstream_source, not at snapshot.debian.org, which never had it.
    """
    script = step.get("verb") == "bundle.script"
    declared = [(apt_list_prefix(src["uri"]) + "_", src)
                for src in step.get("apt_sources") or [] if src.get("uri")]
    debs: dict = {}
    for d in step.get("debs") or []:
        if d.get("package"):
            debs.setdefault((d["package"], d.get("version"), d.get("architecture")), d)
            debs.setdefault((d["package"], d.get("version")), d)
    notes: list[str] = []

    def pointed(rec: dict) -> dict:
        name = rec.get("package")
        deb = debs.get((name, rec.get("version"), rec.get("architecture"))) or \
            debs.get((name, rec.get("version"))) or {}
        origin = rec.get("origin") or deb.get("origin")
        repo = next((src for prefix, src in declared if origin and origin.startswith(prefix)), None)
        if repo is not None or (script and origin and not from_debian(origin)):
            up = (repo or step).get("upstream_source")
            where = f"the `{repo.get('name')}` repository" if repo else f"`{origin}`"
            if not up:
                notes.append(f"{name} came from {where}, and the recipe gives no "
                             f"upstream_source for it")
            return dict(rec, archive=(repo or {}).get("name") or origin, source_published_at=up)
        if origin and not from_debian(origin):
            notes.append(f"{name} came from an archive this step does not declare ({origin}), "
                         f"so there is nothing to point at")
            return dict(rec, archive=origin, source_published_at=None)
        if not origin and declared:
            notes.append(f"{name}: no record of which archive it came from, and this step "
                         "adds repositories, so there is nothing to point at")
            return dict(rec, source_published_at=None)
        out = dict(_debian_pointer(rec), archive="debian")
        if not out["source_published_at"]:
            notes.append(f"no source version was recorded for {name}")
        return out

    seen, out = set(), []
    for i in step.get("installed") or []:
        key = (i.get("package"), i.get("version"))
        if key not in seen:
            seen.add(key)
            out.append(pointed(i))
    for d in step.get("debs") or []:
        key = (d.get("package"), d.get("version"))
        if d.get("package") and key not in seen:
            seen.add(key)
            out.append(pointed({k: d.get(k) for k in (
                "package", "version", "architecture", "source", "source_version", "origin")
                if d.get(k) is not None}))
    if flavour == "slackware":
        # Slackware has no Source field: a release publishes its source beside its packages,
        # and the recipe that installed them names that tree.
        up = step.get("upstream_source")
        names = step.get("slackware_installed") or []
        if names and not up:
            notes.append(f"{len(names)} Slackware package(s) ({', '.join(names[:3])}), and the "
                         "recipe gives no upstream_source for their release's source tree")
        for name in names:
            out.append({"package": name, "archive": "slackware", "source_published_at": up})
    return out, notes


def classify(files: dict, stock: dict, prov: dict, target: str | None, target_info: dict,
             initramfs: dict | None = None) -> dict:
    """Pure: files {path: sha256} + stock index + provenance -> the sources document.

    Every file gets one class, and no class is an error (#62): what no step of this build
    recorded is `unrecorded`, or `base` when the build started from an image this kitchen
    does not know as stock Slax. `notes` says what the report rests on -- a checkout with
    uncommitted changes, a record with no commit -- and main() adds a sidecar that
    describes another ISO.

    `initramfs` is {member: sha256} for the image's own initramfs, or None when it could
    not be unpacked; initramfs_members() reads it from the ISO."""
    components, notes = [], []
    builder = prov.get("builder") or {}

    outputs: dict[str, list] = {}
    artifacts: dict[str, list] = {}
    # Which recipe (by position) last recorded a path's sha256, and which last wrote it at
    # all. A step that records its output also lists it as an artifact, so a tie means the
    # recording step was the last writer and the bytes must match what it recorded.
    last_output: dict[str, int] = {}
    last_artifact: dict[str, int] = {}
    recipes = prov.get("recipes") or []
    for i, r in enumerate(recipes):
        for s in r.get("steps") or []:
            if s.get("output"):
                outputs.setdefault(s["output"], []).append((r.get("recipe"), s))
                last_output[s["output"]] = i
        for a in r.get("artifacts") or []:
            if not a.startswith("-"):
                artifacts.setdefault(a.lstrip("/"), []).append(r.get("recipe"))
                last_artifact[a.lstrip("/")] = i
    generated = {g["path"]: g["sha256"] for g in (prov.get("pack") or {}).get("generated") or []}
    # The stock index the other way round. `bundle.renumber` renames a stock bundle without
    # touching a byte, so a path-keyed lookup misses it and the file falls through to the
    # recipe's artifact list -- Slax's own binary, reported as something this project wrote.
    stock_by_sha: dict = {}
    for sp, sh in stock["files"].items():
        if not sp.endswith("@64"):
            stock_by_sha.setdefault(sh, sp)

    def add(path, sha, cls, by=None, **detail):
        components.append({"path": path, "sha256": sha, "class": cls, "by": by,
                           **{k: v for k, v in detail.items() if v is not None}})

    for path in sorted(files):
        if path.endswith("@64"):
            continue
        sha = files[path]
        if stock["files"].get(path) == sha:
            add(path, sha, "slax")
            continue
        tail = path + "@64"
        if tail in files and stock["files"].get(tail) == files[tail]:
            add(path, sha, "slax", note="from byte 64 on it is the stock file, byte for "
                "byte; the mastering tool rewrites the boot-info table at bytes 8-63 and "
                "the first 64 bytes are skipped whole, so bytes 0-7 are not compared")
            continue
        recorded = [(rn, s) for rn, s in outputs.get(path, []) if s.get("output_sha256") == sha]
        if recorded:
            rn, s = recorded[-1]
            verb = s.get("verb")
            # What the step copied in, by where it sits in the kitchen or project checkout:
            # the report names it, and nothing holds it to a commit (#62).
            inputs = [li.get("path") or li.get("outside")
                      for li in s.get("local_inputs") or []] or None
            if path == "slax/boot/initrfs.img":
                parts = []
                for r2, s2 in outputs.get(path, []):
                    if s2.get("verb") == "initramfs.busybox":
                        claim = s2.get("claim") or {}
                        if not claim.get("verified"):
                            # Listed and said: without a claim that matches the binary there
                            # is nothing to point at, which is a fact about this build and
                            # not a reason to refuse the report.
                            parts.append({"member": "bin/busybox", "class": "built", "by": r2,
                                          "note": "its build claim does not match the binary"
                                          if claim else "no build claim was recorded"})
                            continue
                        c = claim.get("claim") or {}
                        release = (c.get("container") or {}).get("alpine_release")
                        linked = [dict(lib, source_published_at=alpine_aports_url(release, lib["name"]))
                                  for lib in c.get("linked") or []]
                        parts.append({"member": "bin/busybox", "class": "built", "by": r2,
                                      "linked": linked or None,
                                      "sources": c.get("sources"), "config": c.get("config"),
                                      "location": s2.get("source_location"),
                                      "container": (c.get("container") or {}).get("image")})
                    else:
                        parts.append({"class": "recipe", "by": r2, "verb": s2.get("verb")})
                delta = initramfs_delta(initramfs, stock["initramfs"])
                if delta["compared"]:
                    note = (f"initramfs repacked by recipes: {delta['stock_members']} of "
                            f"{delta['members']} members are the stock image's, byte for byte")
                    if delta["changed"]:
                        note += f"; changed or added: {', '.join(delta['changed'][:8])}"
                        if len(delta["changed"]) > 8:
                            note += f", and {len(delta['changed']) - 8} more"
                    if delta["removed"]:
                        note += f"; removed: {len(delta['removed'])}"
                else:
                    note = ("initramfs repacked by recipes; its members were not compared: "
                            + delta["why"])
                add(path, sha, "recipe", by=rn, note=note, parts=parts, stock_members=delta)
                continue
            if verb == "bundle.packages":
                flavour = s.get("flavour") or "debian"
                pk, pnotes = _packages(s, flavour)
                add(path, sha, "slackware" if flavour == "slackware" else "debian", by=rn,
                    packages=pk, reinstall=s.get("reinstall"), apt_sources=s.get("apt_sources"),
                    notes=pnotes or None)
            elif verb in ("bundle.fromTarball", "boot.payload"):
                add(path, sha, "prebuilt", by=rn, **_pointer_for_prebuilt(s))
            elif verb == "boot.uefi":
                tools = s.get("tools") or {}
                grub = tools.get("grub-efi-amd64-bin") or {}
                # host_package() returns just {"package": name} when dpkg-query -W fails, and
                # that dict is truthy: it names a package but no source version, so there is
                # nothing to point at, and the note says so.
                known = bool(grub.get("source") and (grub.get("source_version") or grub.get("version")))
                add(path, sha, "built", by=rn, what="GRUB EFI image (grub-mkstandalone)",
                    bootx64_sha256=s.get("bootx64_sha256"), grub_modules=s.get("grub_modules"),
                    host_packages=tools,
                    source_package={"source": grub.get("source"),
                                    "version": grub.get("source_version"),
                                    "published_at": host_source_url(grub, builder)}
                    if known else None,
                    note=None if known else (
                        f"the build host's {grub.get('package')} recorded no source version"
                        if grub else "the build host recorded no GRUB package"))
            elif verb == "bundle.script":
                declared = {d["path"]: d for d in s.get("declares") or []}
                pk, pnotes = _packages(s, "slackware" if s.get("slackware_installed") else "debian")
                # `network: true` says this bundle's account rests on what the script
                # reported: nothing here can see a download the script made without printing
                # a KITCHEN-FETCHED line for it.
                add(path, sha, "recipe", by=rn, network=s.get("network"), inputs=inputs,
                    note="written by a script; what it fetched and installed is listed "
                    "separately" + (", and it ran with network access, so that list is the "
                                    "script's own account" if s.get("network") else ""),
                    packages=pk or None, notes=pnotes or None,
                    # A declared binary was COMPILED here: a built part. A reported download
                    # is a prebuilt one, pointing at the step's upstream_source.
                    parts=[{"member": d["path"], "class": "built", "by": rn,
                            "sources": [{"url": d["source_url"], "sha256": d["source_sha256"],
                                         "license": d.get("license")}]}
                           for d in declared.values()] + _download_parts(s, rn) or None)
            elif verb == "bundle.files":
                # A RECIPE'S BUNDLE, WITH ITS DOWNLOADS AS PARTS (#59): one rule for the verb,
                # so a bundle of nothing but downloads is not a different class from one with
                # a README too. A `src:` entry that names an upstream_source is a part the
                # same way (#60).
                parts = _download_parts(s, rn) + _copied_parts(s, rn)
                add(path, sha, "recipe", by=rn, verb=verb, inputs=inputs, parts=parts or None)
            else:
                add(path, sha, "recipe", by=rn, verb=verb, inputs=inputs)
            continue
        if generated.get(path) == sha:
            add(path, sha, "recipe", by="kitchen pack", note="generated at pack time")
            continue
        if sha in stock_by_sha:
            add(path, sha, "slax", note=f"byte-identical to `{stock_by_sha[sha]}` in the stock "
                                        "image, under a different name -- bundle.renumber "
                                        "moves a bundle without rebuilding it")
            continue
        if path in last_output and last_artifact.get(path, -1) <= last_output[path]:
            rn, s = outputs[path][-1]
            add(path, sha, "unrecorded", note=f"{rn}: {s.get('verb')} recorded sha256 "
                f"{str(s.get('output_sha256'))[:12]}, the image has {sha[:12]}: changed after "
                "the step ran")
            continue
        if path in artifacts:
            add(path, sha, "recipe", by=artifacts[path][-1], note="written or edited by a recipe")
            continue
        if not target:
            add(path, sha, "base", note="made by no step of this build, so it came with the "
                                        "base image")
        elif path in stock["files"]:
            add(path, sha, "unrecorded", note="differs from the stock image, and no recorded "
                                              "step explains it")
        else:
            add(path, sha, "unrecorded", note="not in the stock image, and no recorded step "
                                              "produced it")

    # A file a recipe copied in from the build host -- locale-timezone-keyboard's tzfile --
    # still has a source to point at: the host package that owns it, the way the isohybrid
    # MBR does.
    host_inputs = []
    for r in recipes:
        inputs = [r["recipe_file"]] if r.get("recipe_file") else []
        inputs += [li for st in r.get("steps") or [] for li in st.get("local_inputs") or []]
        for li in inputs:
            hp = li.get("host_package")
            if li.get("outside") and hp and li.get("kind") == "file":
                host_inputs.append({"recipe": r.get("recipe"), "input": li["outside"],
                                    "host_package": hp,
                                    "published_at": host_source_url(hp, builder)})

    mbr = (prov.get("pack") or {}).get("mbr")
    if mbr:
        add("(system area)", mbr.get("sha256"), "prebuilt", by="kitchen pack",
            what=f"isohybrid MBR, {mbr.get('file')} copied from the build host",
            host_package=mbr.get("package"),
            upstream_source=host_source_url(mbr.get("package"), builder))

    base = prov.get("base") or {}
    if not target:
        notes.append(f"the base image, `{base.get('name')}` (sha256 "
                     f"{str(base.get('sha256'))[:12]}), is not a stock Slax release this "
                     "kitchen knows, so every file no step of this build made is listed as "
                     "`base`")
    kitchen = prov.get("kitchen") or {}
    project = prov.get("project") or {}
    if not kitchen.get("commit"):
        notes.append("the provenance records no kitchen commit")
    for name, state in (("kitchen", kitchen), ("project", project)):
        if state.get("dirty"):
            notes.append(f"the {name} checkout had uncommitted changes when this image was "
                         f"built ({state.get('describe')}), so its commit does not hold all "
                         "of them")

    summary = {c: 0 for c in CLASSES}
    for c in components:
        summary[c["class"]] += 1
    iso = (prov.get("pack") or {}).get("iso") or {}
    return {
        "schema": SCHEMA,
        "image": iso,
        "base": {"target": target, "name": base.get("name"), "sha256": base.get("sha256"),
                 "source_published_at": [SLAX_SOURCE]},
        "kitchen": {k: kitchen.get(k) for k in ("commit", "describe", "dirty")},
        "project": {k: project.get(k) for k in ("commit", "describe", "dirty")} if project else None,
        "builder": builder,
        "summary": summary,
        "components": components,
        "notes": notes,
        "host_inputs": host_inputs,
    }


HEX8 = re.compile(rb"[0-9A-Fa-f]{8}")
# ------------------------------------------------------------------ render --------

def _listed(v) -> list:
    """An upstream_source, which the schema lets be one URL or a list, as a list."""
    return [str(u) for u in (v if isinstance(v, list) else [v] if v else [])]


def _joined(v) -> str:
    return ", ".join(_listed(v))


def markdown(doc: dict) -> str:
    img = doc.get("image") or {}
    base = doc.get("base") or {}
    s = doc["summary"]
    built_from = (f"Built from Slax `{base.get('target')}`" if base.get("target")
                  else "Built on an image this kitchen does not know as stock Slax")
    lines = [
        f"# Sources: `{img.get('name')}`",
        "",
        f"sha256 `{img.get('sha256')}`. {built_from} (`{base.get('name')}`, sha256 "
        f"`{base.get('sha256')}`) with slax-kitchen `{(doc.get('kitchen') or {}).get('describe')}`.",
        "",
    ]
    if doc.get("notes"):
        lines += [f"- {n}" for n in doc["notes"]] + [""]
    lines += ["| | components |", "|---|---|"]
    for c in CLASSES:
        lines.append(f"| {c} | {s.get(c, 0)} |")
    lines += ["", "## Where each upstream publishes its source", "",
              f"- **Slax**, as its author built it: <{SLAX_SOURCE}> and the repositories it names."]
    if s.get("base"):
        lines.append(f"- **The base image**, `{base.get('name')}` (sha256 `{base.get('sha256')}`): "
                     f"{s['base']} file(s) came with it. Its own SOURCES.md, if its publisher "
                     "made one, says where their source is.")
    debs, other = {}, {}
    for comp in doc["components"]:
        for p in comp.get("packages") or []:
            if p.get("archive") in (None, "debian") and p.get("source") and p.get("source_version"):
                debs[(p["source"], p["source_version"])] = p["source_published_at"]
            elif p.get("archive") not in (None, "debian", "slackware"):
                other[(p.get("package"), p.get("version"), p["archive"])] = p.get("source_published_at")
    if debs:
        lines.append(f"- **Debian source packages** ({len(debs)}):")
        for (src, ver), url in sorted(debs.items()):
            lines.append(f"  - `{src}` `{ver}` — <{url}>")
    slack: dict = {}
    for comp in doc["components"]:
        for p in comp.get("packages") or []:
            if p.get("archive") == "slackware":
                ups = p.get("source_published_at")
                slack.setdefault(tuple(ups) if isinstance(ups, list) else (ups,), []).append(p["package"])
    for ups, names in sorted(slack.items(), key=lambda kv: str(kv[0])):
        lines.append(f"- **Slackware packages** ({len(names)}), source "
                     + (", ".join(f"<{u}>" for u in ups if u) or "not named") + ": "
                     + ", ".join(f"`{n}`" for n in sorted(names)))
    for h in doc.get("host_inputs") or []:
        hp = h["host_package"]
        lines.append(f"- `{h['input']}`, copied by `{h['recipe']}` from the build host's "
                     f"`{hp.get('package')}` `{hp.get('version') or hp.get('source_version')}` "
                     f"— <{h['published_at']}>")
    for (name, ver, archive), up in sorted(other.items()):
        ups = up if isinstance(up, list) else [up] if up else []
        lines.append(f"- `{name}` `{ver}`, from `{archive}` — "
                     + (", ".join(f"<{u}>" for u in ups) or "no upstream source named"))
    for comp in doc["components"]:
        for part in comp.get("parts") or []:
            for lib in part.get("linked") or []:
                lines.append(f"- `{lib['name']}` `{lib.get('version')}` ({lib.get('license')}), "
                             f"statically linked into `{part.get('member')}` — "
                             f"<{lib.get('source_published_at')}>")
        if comp["class"] == "prebuilt":
            lines.append(f"- `{comp['path']}` — {comp.get('what') or comp.get('source')}"
                         + (f" — source: {_joined(comp.get('upstream_source'))}"
                            if comp.get("upstream_source") else "")
                         + ("; no sha256 was pinned" if comp.get("pinned") is False else ""))
        pre = [p for p in comp.get("parts") or [] if p.get("class") == "prebuilt"]
        if len(pre) == 1:
            p = pre[0]
            lines.append(f"- `{comp['path']}:{p['member']}` — "
                         + (f"copied in from `{p['copied_from']}`" if p.get("copied_from")
                            else f"<{p.get('source')}>")
                         + f" — source: {_joined(p.get('upstream_source')) or 'not named'}"
                         + ("; no sha256 was pinned" if p.get("pinned") is False else ""))
        elif pre:
            ups = sorted({u for p in pre for u in _listed(p.get("upstream_source"))})
            loose = sum(p.get("pinned") is False for p in pre)
            lines.append(f"- `{comp['path']}` — {len(pre)} files it downloaded or copied in "
                         f"unmodified; source: {', '.join(ups) or 'not named'}"
                         + (f"; {loose} with no sha256 pinned" if loose else ""))
    built = [c for c in doc["components"] if c["class"] == "built"] + [
        dict(p, path=f"{c['path']}:{p['member']}") for c in doc["components"]
        for p in c.get("parts") or [] if p.get("class") == "built"]
    if built:
        lines += ["", "## Built here", ""]
        for b in built:
            what = b.get("what") or f"`{b.get('member')}`"
            if b.get("note"):
                lines.append(f"- `{b['path']}` — {what}: {b['note']}")
            if b.get("source_package"):
                sp = b["source_package"]
                lines.append(f"- `{b['path']}` — {what}, from source package `{sp.get('source')}` "
                             f"`{sp.get('version')}` — <{sp.get('published_at')}>")
            for src in b.get("sources") or []:
                lines.append(f"- `{b['path']}` — built from <{src['url']}> (sha256 `{src['sha256']}`)")
            if (b.get("config") or {}).get("file"):
                lines.append(f"  - build configuration `{b['config']['file']}`")
    made = [c for c in doc["components"] if c["class"] == "recipe"]
    if made:
        kit = doc.get("kitchen") or {}
        proj = doc.get("project") or {}
        at = f"slax-kitchen `{kit.get('describe')}`" + (
            f", the project `{proj.get('describe')}`" if proj.get("describe") else "")
        lines += ["", "## Made by this build", "",
                  "Written, generated or copied in by its recipes and by `kitchen pack`. The "
                  "recipes are in the project's repository and slax-kitchen's, at the commits "
                  f"the provenance records ({at}), with their submodules.", ""]
        for c in made:
            ins = c.get("inputs") or []
            lines.append(f"- `{c['path']}` — {c.get('by')}"
                         + (f"; copied in: {', '.join(f'`{i}`' for i in ins[:5])}"
                            + (f", and {len(ins) - 5} more" if len(ins) > 5 else "")
                            if ins else "")
                         + ("; what it downloaded, or copied in naming a source, is listed above"
                            if any(p.get("class") == "prebuilt" for p in c.get("parts") or [])
                            else ""))
    noted = [(c["path"], n) for c in doc["components"] for n in c.get("notes") or []]
    if noted:
        lines += ["", "## Where no pointer could be given", ""]
        for path, n in noted:
            lines.append(f"- `{path}` — {n}")
    unrec = [c for c in doc["components"] if c["class"] == "unrecorded"]
    if unrec:
        lines += ["", "## Not recorded by any step of this build", ""]
        for c in unrec:
            lines.append(f"- `{c['path']}` — {c.get('note')}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ main ----------

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sidecar_note(prov: dict, iso_sha256: str) -> str | None:
    """A note when this ISO is not the one its provenance describes -- it changed after
    pack, or the sidecar is another build's -- so the report may not describe it. It used to
    make the image unresolved (#62); a sidecar that records no sha256 says nothing either
    way."""
    want = ((prov.get("pack") or {}).get("iso") or {}).get("sha256")
    if want in (None, iso_sha256):
        return None
    return ("this ISO's sha256 is not the one its provenance records -- it changed after "
            "pack, or the sidecar is another build's -- so this report may not describe it")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kitchen sources", description=__doc__.split("\n\n")[0])
    ap.add_argument("iso")
    ap.add_argument("--provenance", help="default: <iso>.provenance.json")
    ap.add_argument("--json", help="write the machine-readable manifest here")
    ap.add_argument("--markdown", help="write the human-readable manifest here")
    a = ap.parse_args(argv[1:])
    # xorriso lists the image, and that is all this needs: nothing is looked up in git any
    # more, and nothing is fetched (#62).
    need.require(["xorriso"], "kitchen sources")

    prov_path = a.provenance or a.iso + ".provenance.json"
    if not os.path.isfile(prov_path):
        print(f"kitchen sources: no provenance at {prov_path}\n"
              f"  `kitchen pack` writes it beside the ISO; rebuild with this kitchen.",
              file=sys.stderr)
        return 2
    prov = json.load(open(prov_path))
    import yaml
    sources_yaml = yaml.safe_load(open(os.path.join(REPO, "compat", "sources.yaml"))) or {}
    target, info = base_target(prov, sources_yaml)
    stock = stock_index(target, os.path.join(REPO, "docs", "30-inventory", "manifests"))
    # A LISTING THAT FAILED IS NOT AN IMAGE WITH NOTHING IN IT. classify() accounts for the
    # files it is given, so an empty listing -- xorriso 1.5.6 exits 0 on a file it cannot
    # read as an ISO -- used to come out "unresolved 0" and pass: a report on an image
    # nobody had examined. diff._entries() now refuses such a listing.
    import diff
    try:
        files = iso_files(a.iso)
        # Unpack the initramfs only when there is something to learn: it is not the stock
        # one (an untouched initrfs.img is answered by its own sha256) and this target has a
        # manifest to compare it against. Otherwise the xz is work whose result is unusable.
        irfs = files.get("slax/boot/initrfs.img")
        members = (initramfs_members(a.iso)
                   if irfs and stock["initramfs"]
                   and stock["files"].get("slax/boot/initrfs.img") != irfs else None)
    except diff.ListingError as e:
        print(f"kitchen sources: {e} -- so nothing in it can be accounted for", file=sys.stderr)
        return 2
    doc = classify(files, stock, prov, target, info, initramfs=members)
    # Hashed only when there is a recorded sha256 to compare it with.
    if ((prov.get("pack") or {}).get("iso") or {}).get("sha256"):
        note = sidecar_note(prov, _sha256(a.iso))
        if note:
            doc["notes"].insert(0, note)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
            f.write("\n")
    if a.markdown:
        open(a.markdown, "w").write(markdown(doc))

    s = doc["summary"]
    base = doc["base"]
    print(f"sources  {doc['image'].get('name')}  "
          + (f"(base {target})" if target else
             f"(base {base.get('name')}, not a stock Slax release)"))
    print("  " + "  ".join(f"{c} {s[c]}" for c in CLASSES))
    for n in doc["notes"]:
        print(f"  note: {n}")
    for c in doc["components"]:
        if c["class"] == "unrecorded":
            print(f"  unrecorded  {c['path']}: {c.get('note')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
