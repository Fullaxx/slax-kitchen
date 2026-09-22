#!/usr/bin/env python3
"""Which firmware can this image's kernel ask for, and where does each file come from?

    tools/firmware-coverage.py <work>/iso [--suite bookworm] [--tag 20260916]
                               [--mirror URL] [--cache DIR] [--recipe]

A MAINTAINER TOOL. No build runs it. It produces the two lists recipes/available/
firmware-refresh.yaml carries -- the Debian packages to install and the linux-firmware
files to add -- and the coverage table its cookbook page quotes. Run it against a FRESHLY
UNPACKED STOCK image (`kitchen unpack`), because it treats every bundle in the tree as
stock: a tree that already has firmware-refresh applied would count that recipe's own
output as "already there".

The measurement, in four sources:

  requested   every `firmware=` a kernel module declares (MODULE_FIRMWARE), read from
              the .modinfo section of each .ko in the image's own module tree. This is a
              floor, not a ceiling: some drivers build names at runtime (SOF, iwlwifi's
              API range, brcmfmac board files), which is why a few packages are added as
              known runtime families rather than by name.
  stock       every file under usr/lib/firmware in the image's bundles.
  debian      the suite's Contents indexes: which package ships which firmware file.
  linux-fw    linux-firmware's WHENCE at a tag: which files exist there, and which
              license file each entry names.

The rules the recipe follows, and this tool applies:

  * Debian first, accepted as Debian ships it. A package is selected when it ships at
    least one requested file the image does not already have, or belongs to a runtime
    family, and is not excluded for a stated reason (EXCLUDE below).
  * Stock firmware packages are reinstalled, not re-added: their files are already in
    01-firmware.sb, but upstream's cleanup deleted their /usr/share/doc. A reinstall
    brings the copyright file back and ships nothing else (measured: 64 KiB for ten).
  * Only what NO Debian package ships is taken from linux-firmware -- never a
    replacement for a Debian file -- and only when its WHENCE entry names a license
    file, which travels with the copy.
"""
import argparse
import collections
import gzip
import hashlib
import lzma
import os
import re
import struct
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import dpkgdb  # noqa: E402  (sortmod: the union's own load order)

# linux-firmware's own home is GitLab; git.kernel.org carries the same tags. Mirrors in
# order, sha256 as the authority -- the lib/fetch.py rule. git.kernel.org answered 503 to
# a burst of ~70 sequential requests from this tool (2026-09-17), so neither is trusted to
# be up on its own.
LF_MIRRORS = (
    "https://gitlab.com/kernel-firmware/linux-firmware/-/raw/{tag}/{path}",
    "https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/{path}?h={tag}",
)
UA = "slax-kitchen firmware-coverage (+https://github.com/Fullaxx/slax-kitchen)"

# Present in the suite, and deliberately not installed. Every entry says why, because an
# exclusion nobody can explain is indistinguishable from an omission.
EXCLUDE = {
    "raspi-firmware": "Raspberry Pi boot firmware for /boot/firmware; nothing an x86 image loads",
    "firmware-qcom-soc": "Qualcomm ARM SoC firmware; no driver loads it on x86",
    "firmware-qcom-media": "Qualcomm ARM SoC firmware; no driver loads it on x86",
    "firmware-nvidia-gsp": "GSP firmware for NVIDIA's proprietary driver, which Slax does not ship",
    "firmware-nvidia-tesla-gsp": "GSP firmware for NVIDIA's proprietary driver, which Slax does not ship",
    "firmware-nvidia-tesla-535-gsp": "GSP firmware for NVIDIA's proprietary driver, which Slax does not ship",
    "amd64-microcode": "CPU microcode must load early, from the initramfs; a bundle arrives too late",
    "intel-microcode": "CPU microcode must load early, from the initramfs; a bundle arrives too late",
    "dahdi-firmware-nonfree": "for DAHDI telephony drivers, which are not in this kernel",
    "firmware-ivtv": "prompts for license acceptance through debconf at install (PVR capture cards)",
    "firmware-ipw2x00": "prompts for license acceptance through debconf at install; stock already ships "
                        "it, with /usr/lib/firmware/ipw2x00.LICENSE beside it",
    "firmware-b43-installer": "a downloader: its postinst fetches from www.lwfinger.com, which no "
                              "longer resolves; stock already carries the extracted firmware",
    "firmware-b43legacy-installer": "a downloader for the same dead host",
    "firmware-microbit-micropython": "flashed onto a BBC micro:bit from the host; no driver loads it",
    "firmware-microbit-micropython-doc": "documentation for the above",
    "firmware-tomu": "flashed onto a Tomu board from the host; no driver loads it",
}

# Asked for by name at runtime rather than declared with MODULE_FIRMWARE.
RUNTIME_FAMILIES = {
    "firmware-sof-signed": "Sound Open Firmware: DSP audio on most laptops since ~2019",
    "firmware-intel-sound": "Intel audio DSP firmware requested by the SST/SOF drivers",
    "wireless-regdb": "regulatory.db, which cfg80211 loads for every Wi-Fi device",
}


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, **kw)


def fetch(urls, cache, key=None):
    """GET the first URL that answers, with a disk cache and backoff between attempts.

    Sequential and identified: snapshot.debian.org and git.kernel.org both rate-limit
    anonymous bulk clients, and this tool has no reason to be one.
    """
    import time
    if isinstance(urls, str):
        urls = [urls]
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, re.sub(r"[^A-Za-z0-9._-]+", "_", key or urls[0]))
    if os.path.isfile(path):
        return path
    last = None
    for delay in (0, 3, 10, 30):
        time.sleep(delay)
        for url in urls:
            parts = urllib.parse.urlsplit(url)
            url = urllib.parse.urlunsplit(parts._replace(path=urllib.parse.quote(parts.path, safe="/%")))
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=120) as r, \
                        open(path + ".part", "wb") as fh:
                    fh.write(r.read())
                os.replace(path + ".part", path)
                return path
            except OSError as e:
                last = e
    raise SystemExit(f"could not fetch {urls[0]}: {last}")


def lf_fetch(path, tag, cache):
    return fetch([m.format(tag=tag, path=path) for m in LF_MIRRORS], cache,
                 key=f"linux-firmware-{tag}-{path}")


def bundles(tree):
    mods = os.path.join(tree, "slax", "modules")
    return [os.path.join(mods, n) for n in dpkgdb.sortmod(
        [n for n in os.listdir(mods) if n.endswith(".sb")])]


def squash_entries(sb):
    """(path, kind, link_target) for every entry, from `unsquashfs -lls`."""
    out = run(["unsquashfs", "-lls", sb], text=True).stdout
    for ln in out.splitlines():
        parts = ln.split(None, 5)
        if len(parts) < 6 or not parts[5].startswith("squashfs-root/"):
            continue
        name = parts[5][len("squashfs-root/"):]
        target = None
        if " -> " in name:
            name, target = name.split(" -> ", 1)
        yield name, parts[0][0], target


def modinfo_firmware(data):
    """The firmware= entries of a .ko's .modinfo section, parsed from the ELF directly."""
    if data[:4] != b"\x7fELF":
        return []
    e = "<" if data[5] == 1 else ">"
    if data[4] == 2:
        shoff = struct.unpack_from(e + "Q", data, 0x28)[0]
        shentsize, shnum, shstrndx = struct.unpack_from(e + "HHH", data, 0x3A)
        fmt = e + "IIQQQQ"
    else:
        shoff = struct.unpack_from(e + "I", data, 0x20)[0]
        shentsize, shnum, shstrndx = struct.unpack_from(e + "HHH", data, 0x2E)
        fmt = e + "IIIIII"

    def section(i):
        name, _t, _f, _a, off, size = struct.unpack_from(fmt, data, shoff + i * shentsize)
        return name, off, size

    _, stroff, strsize = section(shstrndx)
    strtab = data[stroff:stroff + strsize]
    for i in range(shnum):
        n, off, size = section(i)
        if strtab[n:strtab.index(b"\0", n)] == b".modinfo":
            return [x[9:].decode("utf-8", "replace")
                    for x in data[off:off + size].split(b"\0") if x.startswith(b"firmware=")]
    return []


def requested_firmware(tree, cache):
    """(names, patterns, kernel release) declared by the image's kernel modules."""
    core = next((b for b in bundles(tree)
                 if any(p.startswith("usr/lib/modules/") for p, _k, _t in squash_entries(b))), None)
    if not core:
        raise SystemExit("no bundle carries usr/lib/modules")
    dest = os.path.join(cache, "modules-" + os.path.basename(core))
    os.makedirs(cache, exist_ok=True)       # unsquashfs -d makes the leaf, not its parents
    if not os.path.isdir(dest):
        r = run(["unsquashfs", "-q", "-n", "-d", dest, core, "usr/lib/modules"])
        if r.returncode != 0:
            raise SystemExit(f"unsquashfs {core}: {r.stderr.decode()[:300]}")
    names, releases = set(), set()
    for root, _dirs, files in os.walk(os.path.join(dest, "usr", "lib", "modules")):
        rel = os.path.relpath(root, os.path.join(dest, "usr", "lib", "modules")).split(os.sep)[0]
        for f in files:
            p = os.path.join(root, f)
            if f.endswith(".ko"):
                data = open(p, "rb").read()
            elif f.endswith(".ko.xz"):
                data = lzma.open(p).read()
            else:
                continue
            releases.add(rel)
            names.update(modinfo_firmware(data))
    patterns = {n for n in names if any(c in n for c in "*?[")}
    return names - patterns, patterns, sorted(releases)


def norm(p):
    """Contents says lib/firmware, bundles say usr/lib/firmware; a kernel asks for neither."""
    return re.sub(r"^(usr/)?lib/firmware/", "", p)


def stock_state(tree):
    files, installed = set(), set()
    status_from = None
    for b in bundles(tree):
        for p, kind, _t in squash_entries(b):
            if re.match(r"^(usr/)?lib/firmware/.", p) and kind in "-l":
                files.add(norm(p))
            if p == "var/lib/dpkg/status":
                status_from = b
    if status_from:
        text = run(["unsquashfs", "-cat", status_from, "var/lib/dpkg/status"],
                   text=True).stdout
        for stanza in text.split("\n\n"):
            m = re.search(r"^Package: (\S+)", stanza, re.M)
            if m and re.search(r"^Status: .* installed$", stanza, re.M):
                installed.add(m.group(1))
    return files, installed


def debian_indexes(mirror, suite, cache):
    ships = collections.defaultdict(set)      # firmware name -> packages
    meta = {}                                  # package -> (version, deb bytes, component)
    for comp in ("main", "contrib", "non-free-firmware"):
        for arch in ("all", "amd64"):
            with gzip.open(fetch(f"{mirror}/dists/{suite}/{comp}/Contents-{arch}.gz", cache),
                           "rt", errors="replace") as fh:
                for ln in fh:
                    if not re.match(r"^(usr/)?lib/firmware/", ln):
                        continue
                    path, pkgs = ln.rstrip("\n").rsplit(None, 1)
                    for pk in pkgs.split(","):
                        ships[norm(path)].add(pk.split("/")[-1])
            text = lzma.open(fetch(f"{mirror}/dists/{suite}/{comp}/binary-{arch}/Packages.xz",
                                   cache)).read().decode("utf-8", "replace")
            for stanza in text.split("\n\n"):
                m = re.search(r"^Package: (\S+)", stanza, re.M)
                if m:
                    v = re.search(r"^Version: (\S+)", stanza, re.M)
                    s = re.search(r"^Size: (\d+)", stanza, re.M)
                    meta[m.group(1)] = (v.group(1) if v else "?", int(s.group(1)) if s else 0, comp)
    return ships, meta


def licence_names(tag, cache):
    """The license files linux-firmware carries at a tag, as {name: repo path}.

    Read from the tree rather than guessed from WHENCE's prose. The layout moved: by
    20260916 the texts live under LICENSES/ while WHENCE still names them bare ("See
    LICENCE.mediatek for details"), some carry a .txt suffix, and some names are prefixes
    of others (LICENCE.cw1200 / LICENCE.cw1200-sdd) -- all three defeat a regex.
    """
    import json
    proj = urllib.parse.quote("kernel-firmware/linux-firmware", safe="")
    found = {}
    for sub in ("LICENSES", ""):
        page = 1
        while True:
            url = (f"https://gitlab.com/api/v4/projects/{proj}/repository/tree"
                   f"?ref={tag}&path={sub}&per_page=100&page={page}")
            items = json.load(open(fetch(url, cache, key=f"lf-tree-{tag}-{sub or 'root'}-{page}")))
            for it in items:
                if it.get("type") == "blob" and re.match(r"^(LICEN[CS]E|GPL-|Apache-)", it["name"]):
                    found.setdefault(it["name"], it["path"])
            if len(items) < 100:
                break
            page += 1
    if not found:
        raise SystemExit(f"no license files found in linux-firmware at {tag}")
    return found


def whence(tag, cache):
    """name -> (kind, link target or None, license file repo paths, license statement)."""
    text = open(lf_fetch("WHENCE", tag, cache), encoding="utf-8", errors="replace").read()
    lics = licence_names(tag, cache)
    out = {}
    for block in re.split(r"\n-{10,}\n", text):
        named = sorted(path for name, path in lics.items()
                       if re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-]|\.\w)", block))
        stmt = (re.findall(r"^Licen[cs]e:\s*(.+)$", block, re.M) or [""])[0].strip()
        for f in re.findall(r"^(?:File|RawFile):\s*\"?([^\"\n]+?)\"?\s*$", block, re.M):
            out[f] = ("file", None, named, stmt)
        for ln in re.findall(r"^Link:\s*(.+)$", block, re.M):
            name, _, target = ln.partition(" -> ")
            out[name.strip()] = ("link", target.strip(), named, stmt)
    return out


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tree", help="an unpacked STOCK image: <work>/iso")
    ap.add_argument("--suite", default="bookworm")
    ap.add_argument("--mirror", default="http://deb.debian.org/debian")
    ap.add_argument("--tag", default="20260916", help="linux-firmware tag")
    ap.add_argument("--cache", default=os.path.join(HERE, "..", "build", "firmware-coverage"))
    ap.add_argument("--recipe", action="store_true",
                    help="print the package list and file manifest in recipe form")
    a = ap.parse_args()

    requested, patterns, releases = requested_firmware(a.tree, a.cache)
    stock_files, installed = stock_state(a.tree)
    ships, meta = debian_indexes(a.mirror, a.suite, a.cache)
    lf = whence(a.tag, a.cache)

    in_stock = requested & stock_files
    by_debian = {n for n in requested if n in ships}
    beyond = by_debian - stock_files
    nowhere_deb = requested - set(ships)

    # --- Debian selection -------------------------------------------------------------
    reasons = collections.defaultdict(list)
    for n in beyond:
        for pk in ships[n]:
            reasons[pk].append(n)
    select, excluded = {}, {}
    for pk, names in reasons.items():
        if pk in EXCLUDE:
            excluded[pk] = EXCLUDE[pk]
        elif pk not in installed:
            select[pk] = f"{len(names)} requested file(s) the image lacks"
    for pk, why in RUNTIME_FAMILIES.items():
        if pk in meta and pk not in installed and pk not in select:
            select[pk] = why
    fw_like = re.compile(r"^(firmware-|.*-firmware$|wireless-regdb$)")
    reinstall = {pk: "stock copy has lost its /usr/share/doc; reinstall restores it"
                 for pk in installed if fw_like.match(pk) and pk not in EXCLUDE}
    for pk in installed:
        if fw_like.match(pk) and pk in EXCLUDE:
            excluded[pk] = EXCLUDE[pk]

    # --- linux-firmware ----------------------------------------------------------------
    take, no_license, missing = [], [], []
    for n in sorted(nowhere_deb):
        if n not in lf:
            missing.append(n)
            continue
        kind, target, licfiles, stmt = lf[n]
        (take if licfiles else no_license).append((n, kind, target, licfiles, stmt))
    manifest, licence_files, listed = [], set(), set()

    def add_file(path):
        if path not in listed:
            listed.add(path)
            manifest.append(("file", path, sha256_of(lf_fetch(path, a.tag, a.cache))))

    for n, kind, target, licfiles, _stmt in take:
        if kind == "link":
            # WHENCE writes a link's target relative to the link's own directory, exactly
            # as the symlink will hold it; the file to fetch is that path resolved.
            resolved = os.path.normpath(os.path.join(os.path.dirname(n), target))
            if resolved not in stock_files and resolved not in ships:
                add_file(resolved)
            manifest.append(("link", n, target))
        else:
            add_file(n)
        licence_files.update(licfiles)
    for lic in sorted(licence_files) + ["WHENCE"]:
        if lic in stock_files or lic in ships:
            print(f"warning: {lic} already exists in stock or Debian; not listed", file=sys.stderr)
            continue
        add_file(lic)

    if a.recipe:
        print("    packages:")
        for pk in sorted(select):
            print(f"      - {pk:30s} # {select[pk]}")
        for pk in sorted(reinstall):
            print(f"      - {pk:30s} # stock; reinstalled for its copyright file")
        print("\n      # linux-firmware " + a.tag)
        for entry in manifest:
            print("      " + " ".join(entry))
        return 0

    print(f"kernel release(s): {', '.join(releases)}")
    print(f"requested by modules            {len(requested):5d} names "
          f"(+{len(patterns)} wildcard patterns, not expanded)")
    print(f"  already in the stock image    {len(in_stock):5d}")
    print(f"  shipped by a {a.suite} package {len(by_debian):5d}  ({len(beyond)} not in stock)")
    print(f"  shipped by no Debian package  {len(nowhere_deb):5d}")
    print(f"    in linux-firmware {a.tag}, with a license file   {len(take):4d}")
    print(f"    in linux-firmware {a.tag}, no license file named {len(no_license):4d}")
    print(f"    in neither                                        {len(missing):4d}")
    print("\nDebian packages to install:")
    total = 0
    for pk in sorted(select):
        v, size, comp = meta.get(pk, ("?", 0, "?"))
        total += size
        print(f"  {pk:32s} {v:28s} {size / 2**20:6.1f} MiB  {select[pk]}")
    print(f"  {'':32s} {'':28s} {total / 2**20:6.1f} MiB of .debs")
    print("\nstock packages to reinstall (docs only):")
    for pk in sorted(reinstall):
        print(f"  {pk}")
    print("\nexcluded:")
    for pk in sorted(excluded):
        print(f"  {pk:32s} {excluded[pk]}")
    print(f"\nfrom linux-firmware {a.tag}: {sum(1 for m in manifest if m[0] == 'file')} files "
          f"({len(licence_files)} license files + WHENCE), "
          f"{sum(1 for m in manifest if m[0] == 'link')} links")
    stmts = collections.Counter(s[:70] for _n, _k, _t, _l, s in no_license)
    print("left out, no license file named:", ", ".join(f"{k!r} x{v}" for k, v in stmts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
