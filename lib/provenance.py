#!/usr/bin/env python3
"""Provenance: what a build fetched, built and embedded, recorded as it happens.

    python3 lib/provenance.py finalize --work WORK --iso OUT.iso --backend NAME [--mbr FILE]

WHY THIS EXISTS. The journal (<work>/.kitchen/journal.yaml) records WHAT was applied --
recipes, verbs, artifacts. It never recorded where anything came from: which URL a
payload was fetched from and its sha256, which package versions apt resolved, which
GRUB the build host embedded in the ESP. And `kitchen build` deletes the work tree when
it finishes, so even what was on disk went with it. Publishing an image means saying what
is in it, and "whatever the build machine had that day" is not an answer.

So verbs call ctx.prov(...) while they run, apply_recipe appends each recipe's records to
<work>/.kitchen/provenance.json, and `kitchen pack` finalizes that into
<iso>.provenance.json NEXT TO THE ISO, where it survives the work tree.

NOTHING ABOUT THE HOST. The same rule as the Tier C ledger, enforced the same way: files
are named by basename or by their path inside the image (no leading slash), and any
string that looks like a place on the build machine is refused. URLs are not host paths
and are allowed. HOSTISH lives here and ci/checks/97-tier-c-ledger.sh imports it, so the
two cannot drift.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

SCHEMA = "slax-kitchen/provenance/v1"
WORK_FILE = "provenance.json"

# A value that names a place on somebody's disk.
HOSTISH = re.compile(r"(^/|/home/|/root/|/Users/|~/|\\\\)")
URLISH = re.compile(r"^[a-z][a-z0-9+.-]*://")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def in_image(path: str) -> str:
    """A path inside the image, written without a leading slash so it is not host-ish."""
    return (path or "").lstrip("/")


def hostish_values(obj, where: str = "") -> list[str]:
    """Every string in obj that names a place on a disk. URLs are allowed."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += hostish_values(v, f"{where}.{k}" if where else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += hostish_values(v, f"{where}[{i}]")
    elif isinstance(obj, str) and not URLISH.match(obj) and HOSTISH.search(obj):
        out.append(f"{where}: {obj!r}")
    return out


def _run(argv: list[str]) -> str | None:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def host_package(path: str | None) -> dict | None:
    """The build host's package that owns `path`, with version and source package.

    None when the host has no dpkg (an Arch or Fedora build machine), when nothing owns
    the path, or when the path is absent. None is a finding, not an error: `kitchen
    sources` reports what it cannot attribute rather than pretending.
    """
    if not path or not shutil.which("dpkg-query"):
        return None
    real = os.path.realpath(path)
    for candidate in dict.fromkeys([path, real]):
        out = _run(["dpkg-query", "-S", candidate])
        if not out:
            continue
        # "pkg: /path" or "pkg1, pkg2: /path"; skip diversion notes.
        line = next((ln for ln in out.splitlines() if not ln.startswith("diversion ")), "")
        owner = line.split(":", 1)[0].split(",")[0].strip()
        if not owner:
            continue
        fmt = "${Package}\t${Version}\t${source:Package}\t${source:Version}\t${Architecture}"
        info = _run(["dpkg-query", "-W", f"-f={fmt}", owner])
        if not info:
            return {"package": owner}
        pkg, ver, src, srcver, arch = (info.split("\t") + [""] * 5)[:5]
        return {"package": pkg, "version": ver, "architecture": arch,
                "source": src or pkg, "source_version": srcver or ver}
    return None


def git_state(root: str | None) -> dict | None:
    """Commit, dirty flag and submodule pins of a checkout, or None outside one."""
    if not root or not os.path.isdir(os.path.join(root, ".git")) and \
            not os.path.isfile(os.path.join(root, ".git")):
        return None
    commit = _run(["git", "-C", root, "rev-parse", "HEAD"])
    if not commit:
        return None
    describe = (_run(["git", "-C", root, "describe", "--always", "--dirty"]) or "").strip()
    subs = {}
    for ln in (_run(["git", "-C", root, "submodule", "status", "--recursive"]) or "").splitlines():
        parts = ln[1:].split()
        if len(parts) >= 2:
            subs[parts[1]] = parts[0]
    return {"commit": commit.strip(), "describe": describe,
            "dirty": describe.endswith("-dirty"), "submodules": subs}


def load_claim(binary: str) -> dict | None:
    """A build claim written beside a binary by the tool that built it, e.g.
    tools/build-busybox.sh writes busybox-1.37.0-i386-static.provenance.json.

    The claim is only as good as its match: `verified` says whether the artifact sha256
    in the claim is the sha256 of the binary actually used.
    """
    path = binary + ".provenance.json"
    if not os.path.isfile(path):
        return None
    try:
        claim = json.load(open(path))
    except (OSError, ValueError):
        return {"file": os.path.basename(path), "verified": False, "error": "unreadable"}
    want = (claim.get("artifact") or {}).get("sha256")
    return {"file": os.path.basename(path), "sha256": sha256(path),
            "verified": bool(want) and want == sha256(binary), "claim": claim}


def _write_atomic(path: str, doc: dict) -> None:
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def load_work(meta: str) -> dict:
    path = os.path.join(meta, WORK_FILE)
    if os.path.isfile(path):
        try:
            return json.load(open(path))
        except ValueError:
            pass
    return {"schema": SCHEMA, "recipes": []}


def append_recipe(meta: str, entry: dict) -> None:
    """Add one applied recipe's records to <work>/.kitchen/provenance.json."""
    bad = hostish_values(entry)
    if bad:
        raise RuntimeError("provenance would record a path on the build machine:\n  "
                           + "\n  ".join(bad))
    doc = load_work(meta)
    doc.setdefault("recipes", []).append(entry)
    _write_atomic(os.path.join(meta, WORK_FILE), doc)


def _tool_version(tool: str) -> str | None:
    flag = {"genisoimage": "--version", "xorriso": "-version"}.get(tool, "--version")
    out = _run([tool, flag])
    return out.splitlines()[0].strip() if out else None


def finalize(work: str, iso: str, backend: str, mbr: str | None) -> str:
    """Write <iso>.provenance.json: the work tree's records plus what pack itself used."""
    import yaml
    meta = os.path.join(work, ".kitchen")
    doc = load_work(meta)
    origin = {}
    opath = os.path.join(meta, "origin.yaml")
    if os.path.isfile(opath):
        origin = yaml.safe_load(open(opath)) or {}
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = {
        "schema": SCHEMA,
        "kitchen": git_state(repo),
        "base": {
            "name": os.path.basename(str(origin.get("source_iso", ""))) or None,
            "sha256": origin.get("source_sha256"),
            "size": origin.get("source_size"),
        },
        "recipes": doc.get("recipes", []),
        "pack": {
            "backend": backend,
            "tool": _tool_version(backend),
            "tool_package": host_package(shutil.which(backend)),
            "iso": {"name": os.path.basename(iso), "sha256": sha256(iso),
                    "size": os.path.getsize(iso)},
        },
    }
    project = os.environ.get("PROJECT_ROOT")
    if project and os.path.abspath(project) != repo:
        out["project"] = git_state(project)
    if mbr:
        out["pack"]["mbr"] = {"file": os.path.basename(mbr), "sha256": sha256(mbr),
                              "package": host_package(mbr)}
    bad = hostish_values(out)
    if bad:
        raise RuntimeError("provenance would record a path on the build machine:\n  "
                           + "\n  ".join(bad))
    dest = iso + ".provenance.json"
    _write_atomic(dest, out)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="provenance for a built ISO")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("finalize", help="write <iso>.provenance.json")
    f.add_argument("--work", required=True)
    f.add_argument("--iso", required=True)
    f.add_argument("--backend", required=True)
    f.add_argument("--mbr")
    a = ap.parse_args()
    try:
        dest = finalize(a.work, a.iso, a.backend, a.mbr)
    except RuntimeError as e:
        print(f"provenance: {e}", file=sys.stderr)
        return 1
    print(dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
